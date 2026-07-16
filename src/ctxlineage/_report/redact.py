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

    for session in data.get("sessions", []):
        for call in session.get("calls", []):
            for segment in call.get("segments", []):
                sub_keys(segment, _SEGMENT_KEYS)
            output = call.get("output")
            if isinstance(output, dict):
                sub_keys(output, ("content",))
            error = call.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                error["message"] = sub(error["message"])
        for element in session.get("elements", []):
            sub_keys(element, _META_KEYS)

    data["redaction"] = {"patterns": len(compiled), "matches": count}
    return count
