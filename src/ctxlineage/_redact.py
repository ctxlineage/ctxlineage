"""Capture-side field masking (PLAN.md §6): configured via init(redact_fields=...).

Masked values never reach events.jsonl. Path semantics: dots traverse dicts;
a list mid-path applies the remainder to every item; missing keys are a
silent no-op. The whole resolved value is replaced with MASK.
"""

from __future__ import annotations

import copy

MASK = "[redacted]"


def mask_payload(payload: dict, fields: list[str]) -> dict:
    """Return a copy of payload with each dotted-path field replaced by MASK."""
    masked = copy.deepcopy(payload)
    for field in fields:
        _apply(masked, field.split("."))
    return masked


def _apply(node, parts: list[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _apply(item, parts)
        return
    if not isinstance(node, dict) or parts[0] not in node:
        return
    key, rest = parts[0], parts[1:]
    if rest:
        _apply(node[key], rest)
    else:
        node[key] = MASK
