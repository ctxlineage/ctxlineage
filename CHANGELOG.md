# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning:
[SemVer](https://semver.org/spec/v2.0.0.html) (pre-1.0: breaking changes may
land in minor versions).

## [Unreleased]

### Added
- Capture core: `ctxlineage.init()` auto-instruments the **openai** (Chat
  Completions + Responses API) and **anthropic** (Messages) SDKs — sync,
  async, and streaming — into append-only `.ctxlineage/events.jsonl`
  (versioned JSON Schema).
- Explicit **span/tag API**: `with ctxlineage.span(...) as sp: sp.tag(name,
  content, source=..., transform=...)` for context provenance.
- **Report** (`ctxlineage report`): one self-contained HTML file with four
  views — Overview, Calls (context-window anatomy), Chain (session flow),
  Graph (sources → elements → calls lineage with impact tracing) — plus
  client-side search, light/dark themes, i18n-safe rendering.
- Segment matching (exact → partial → honest untagged) with a visible tag
  match rate; lineage edge inference (output→input, same-span) computed in
  the report backend.
- Coexistence test matrix with the Langfuse OpenAI drop-in.
- **Redaction**: `ctxlineage report --redact PATTERN` (repeatable regex;
  applied after matching so stats stay honest, disclosed in the report) and
  capture-side `ctxlineage.init(redact_fields=[...])` field masking (secrets
  never reach `events.jsonl`).
- Read-only **MCP server** (`ctxlineage[mcp]` extra, `ctxlineage-mcp` entry
  point): list_sessions / get_call / get_lineage / generate_report over the
  same JSONL.
- Runnable examples (`examples/rag_app.py`, `examples/agent_app.py`, both
  with a keyless `--mock` mode) and a `ctxlineage-instrument` agent skill.
