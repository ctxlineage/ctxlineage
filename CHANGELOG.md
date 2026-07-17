# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning:
[SemVer](https://semver.org/spec/v2.0.0.html) (pre-1.0: breaking changes may
land in minor versions).

## [Unreleased]

### Added
- **Import coding-agent sessions** (#57): `ctxlineage import --from claude-code`
  turns a Claude Code / `claude -p` session transcript into ordinary
  `events.jsonl`, so the four views, `ctxlineage test` and the MCP server all
  work on it unchanged. Those agents are separate, non-Python processes that
  `init()` cannot patch, but they already write a transcript to disk — so this
  reads that local artifact. Nothing is proxied, injected, or transmitted.
  - Takes the newest session for the current directory, `--session <id>`, or an
    explicit path; `--dry-run` reports without writing; re-importing a session
    already in the log is refused rather than silently double-counting it.
  - **Honest about reconstruction:** token counts are the API's own `usage`,
    but segment sizes are estimated, and the system prompt, tool definitions and
    reasoning text are *not preserved* by the transcript at all — they were sent
    and cost tokens that the file simply does not record. The import reports the
    coverage it achieved rather than presenting a partial prompt as complete.
- **Context contract testing, first slice** (#14): `ctxlineage test` reads a
  `ctxlineage.toml` of deterministic assertions over recorded events and exits
  non-zero on a hard-gate failure, so context becomes a CI gate. No LLM judge,
  no eval dataset — the rules only read what the report pipeline already
  produces.
  - `window_budget` — assert a call, or one segment kind within it, stays under
    a share of the model's context window. Needs no tagging, so it gates any
    captured run; catches silent context bloat.
  - `grounded` — assert tagged context actually landed in the window
    (`presence`), and optionally flag context no downstream call consumed
    (`warn_dead`).
  - **Tier rule:** a rule gates only where its evidence is exact. `grounded`
    hard-gates tagged content, degrades to a warning on inferred lineage, and
    dead-context is always advisory. Unevaluated assertions are reported as
    skipped/warned, never as a pass.

### Changed
- `tomli` is now a runtime dependency on Python 3.10 only (`python_version <
  "3.11"`), to read `ctxlineage.toml`; 3.11+ uses stdlib `tomllib` and installs
  nothing new.

## [0.1.0] - 2026-07-17

First public release.

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
- Anthropic Messages rendered honestly in the report: top-level `system`
  prompt, `tool_use` / `tool_result` blocks, thinking-block markers, streamed
  assembly, and cache-token folding into the window figures.
- Runnable examples (`examples/rag_app.py`, `examples/agent_app.py`,
  `examples/anthropic_app.py`, all with a keyless `--mock` mode) and a
  `ctxlineage-instrument` agent skill.
