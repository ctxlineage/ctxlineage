"""Report-side pattern redaction (PLAN.md §6): `ctxlineage report --redact`.

Runs after build_report_data, so segment matching, token estimates, and
lineage edges are computed on the real text and stay honest. Only free-text
carriers are walked; structural fields (ids, models, kinds, timestamps) are
never touched. The replacement count is disclosed in data["redaction"] —
the patterns themselves are not (they may contain the secret).
"""

from __future__ import annotations

import re

MASK = "[redacted]"

_SEGMENT_KEYS = ("content", "source", "transform")
_META_KEYS = ("source", "transform")
_META_LIST_KEYS = ("sources", "transforms")  # #44 aggregated per-occurrence provenance


def apply(data: dict, patterns: list[str]) -> int:
    """Mask every regex match in the report data's text fields, in place.

    Returns the number of replacements made. Raises re.error on an invalid
    pattern (before anything is modified).
    """
    compiled = [re.compile(p) for p in patterns]
    count = 0

    def sub(text: str) -> str:
        nonlocal count
        for regex in compiled:
            text, n = regex.subn(MASK, text)
            count += n
        return text

    def sub_keys(item: dict, keys: tuple[str, ...]) -> None:
        for key in keys:
            if isinstance(item.get(key), str):
                item[key] = sub(item[key])

    def sub_list_keys(item: dict, keys: tuple[str, ...]) -> None:
        for key in keys:
            values = item.get(key)
            if isinstance(values, list):
                item[key] = [sub(v) if isinstance(v, str) else v for v in values]

    def mask(text: str) -> str:
        """Mask without counting.

        `structured` is a second representation of text the flattened `content`
        already carries, so counting both reports one secret as two. The
        disclosed number is meant to say how much was found, not how many
        copies of the report data happen to hold it.
        """
        for regex in compiled:
            text = regex.sub(MASK, text)
        return text

    def mask_deep(value):
        """Every string inside a declared-structure payload (#103), keys included.

        `structured` carries a tool call's own arguments — a file path, a query,
        an API key someone passed as a parameter. It is the same text as the
        flattened `content`, so leaving it unwalked would hand back verbatim
        what the mask just removed one field over.

        Keys matter as much as values: a tool argument is routinely a map keyed
        by an address, an id or a path, and a key reaches the rendered tree the
        same way a value does. Before `structured` existed, keys could only get
        to the DOM by parsing an already-masked `content` string, so they were
        covered for free; the new field is a second path that has to mask them
        itself.
        """
        if isinstance(value, str):
            return mask(value)
        if isinstance(value, list):
            return [mask_deep(v) for v in value]
        if isinstance(value, dict):
            return mask_dict(value)
        return value

    def mask_dict(value: dict) -> dict:
        """Mask keys without letting two of them collapse into one entry.

        Masking is many-to-one: `a@x.com` and `b@x.com` both become the mask,
        and a plain dict comprehension would silently drop one key *and its
        value*, then report `object · 1 key` for what were two. Distinct keys
        stay distinct by numbering the repeats, so no value is lost and the
        arity the UI prints stays true. Keys the mask did not touch are
        reserved first, so a real key is never renamed to make room.
        """
        renamed = {k: mask(k) if isinstance(k, str) else k for k in value}
        taken = {new for old, new in renamed.items() if new == old}
        out = {}
        for old, val in value.items():
            new = renamed[old]
            if new != old:
                base, n = new, 2
                while new in taken:
                    new = f"{base} {n}"
                    n += 1
            taken.add(new)
            out[new] = mask_deep(val)
        return out

    def sub_structured(item: dict) -> None:
        parts = item.get("structured")
        if isinstance(parts, list):
            item["structured"] = [
                {
                    **p,
                    "name": mask(p["name"]) if isinstance(p.get("name"), str) else p.get("name"),
                    "value": mask_deep(p.get("value")),
                }
                if isinstance(p, dict)
                else p
                for p in parts
            ]

    for session in data.get("sessions", []):
        for call in session.get("calls", []):
            for segment in call.get("segments", []):
                sub_keys(segment, _SEGMENT_KEYS)
                sub_structured(segment)
            output = call.get("output")
            if isinstance(output, dict):
                sub_keys(output, ("content",))
                sub_structured(output)
            error = call.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                error["message"] = sub(error["message"])
        for element in session.get("elements", []):
            sub_keys(element, _META_KEYS)
            sub_list_keys(element, _META_LIST_KEYS)

    data["redaction"] = {"patterns": len(compiled), "matches": count}
    return count
