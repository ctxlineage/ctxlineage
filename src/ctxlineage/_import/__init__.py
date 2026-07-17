"""Importers: local artifacts other agents already wrote -> the event schema.

An importer never runs alongside the tool it reads and never touches its
traffic; it reads a file that already exists. That keeps the Non-Goals intact
(no server, no proxying, no injection) and is what makes coding agents — which
run as separate, non-Python processes — reachable at all.

The schema is language-agnostic by design, so every adapter lands on the same
events and the report/normalize/MCP pipeline renders them unchanged. Adapters
are siblings over that one schema: `claude-code` today, with #26 (OTel GenAI
semconv, for tools that emit spans but no local transcript) and langfuse
(PLAN §4, v1.5) slotting in beside it.
"""

from __future__ import annotations

from ctxlineage._import import claude_code

#: adapter name (the `--from` value) -> module exposing
#: import_transcript(path) -> list[event]
ADAPTERS = {claude_code.SOURCE: claude_code}

__all__ = ["ADAPTERS", "claude_code"]
