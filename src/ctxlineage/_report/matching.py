"""Tag → segment matching (PLAN.md §4a): exact → partial → honest untagged.

Pure functions over normalized role segments and tag payloads. Matching is
plain substring search by design — v1 degrades gracefully: whatever does not
match stays visible as untagged instead of breaking.
"""

from __future__ import annotations

import json
import unicodedata

_MIN_UNIT_LEN = 4  # substring units shorter than this match everywhere; ignore


def _usable(unit: str) -> bool:
    # 4+ chars, or 3+ when the unit contains non-ASCII (a 3-kanji chunk is
    # already specific; 3 ASCII chars match everywhere)
    return len(unit) >= _MIN_UNIT_LEN or (len(unit) >= 3 and any(ord(ch) > 127 for ch in unit))


def _units(tag: dict) -> list[str]:
    """Matchable strings for a tag: the content, plus element strings when it
    is a JSON array (chunk lists are matched element-wise because apps join
    them into one message). Dict elements contribute their text/content/
    page_content field, so LangChain-style document dumps work too."""
    content = unicodedata.normalize("NFC", tag.get("content") or "")
    units = [content]
    if content.lstrip().startswith("["):
        try:
            parsed = json.loads(content)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            for element in parsed:
                if isinstance(element, str):
                    units.append(element)
                elif isinstance(element, dict):
                    for key in ("text", "content", "page_content"):
                        value = element.get(key)
                        if isinstance(value, str):
                            units.append(value)
                            break
    # normalize each unit: json.loads may decode escapes into non-NFC forms
    return [unicodedata.normalize("NFC", u) for u in units if _usable(u)]


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
        # NFC on both sides: composed/decomposed accent forms must still match
        content = unicodedata.normalize("NFC", segment["content"])
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
