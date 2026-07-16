"""Tag → segment matching (PLAN.md §4a): exact → partial → honest untagged.

Pure functions over normalized role segments and tag payloads. Matching is
plain substring search by design — v1 degrades gracefully: whatever does not
match stays visible as untagged instead of breaking.
"""

from __future__ import annotations

import json

_MIN_UNIT_LEN = 4  # substring units shorter than this match everywhere; ignore


def _units(tag: dict) -> list[str]:
    """Matchable strings for a tag: the content, or its elements when it is a
    JSON array of strings (chunk lists are matched element-wise because apps
    join them into one message)."""
    content = tag.get("content") or ""
    units = [content]
    if content.lstrip().startswith("["):
        try:
            parsed = json.loads(content)
        except ValueError:
            parsed = None
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            units.extend(parsed)
    return [u for u in units if len(u) >= _MIN_UNIT_LEN]


def _find_spans(content: str, tags: list[dict]) -> list[tuple[int, int, dict]]:
    """Non-overlapping (start, end, tag) intervals; longer units win, then leftmost."""
    candidates: list[tuple[int, int, dict]] = []
    for tag in tags:
        for unit in _units(tag):
            start = 0
            while True:
                index = content.find(unit, start)
                if index < 0:
                    break
                candidates.append((index, index + len(unit), tag))
                start = index + len(unit)
    # longer first so a longer overlapping unit beats its own substrings
    candidates.sort(key=lambda c: (-(c[1] - c[0]), c[0]))
    chosen: list[tuple[int, int, dict]] = []
    for start, end, tag in candidates:
        if all(end <= s or start >= e for s, e, _ in chosen):
            chosen.append((start, end, tag))
    chosen.sort(key=lambda c: c[0])
    return chosen


def apply_tags(segments: list[dict], tags: list[dict]) -> tuple[list[dict], set[str]]:
    """Split role segments along tag matches.

    Returns (new_segments, matched_tag_names). Matched parts carry the tag name
    as `kind` (+ source/transform, `match`: exact|partial); unmatched parts keep
    their role kind. Every part gets `tagged: bool`.
    """
    matched: set[str] = set()
    out: list[dict] = []
    for segment in segments:
        content = segment["content"]
        spans = _find_spans(content, tags) if content else []
        if not spans:
            out.append({**segment, "tagged": False})
            continue
        cursor = 0
        for start, end, tag in spans:
            if start > cursor:
                out.append({**segment, "content": content[cursor:start], "tagged": False})
            part = {
                **segment,
                "content": content[start:end],
                "kind": tag["name"],
                "tagged": True,
                "match": "exact" if end - start == len(content) else "partial",
            }
            if tag.get("source"):
                part["source"] = tag["source"]
            if tag.get("transform"):
                part["transform"] = tag["transform"]
            out.append(part)
            matched.add(tag["name"])
            cursor = end
        if cursor < len(content):
            out.append({**segment, "content": content[cursor:], "tagged": False})
    return out, matched
