# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning:
[SemVer](https://semver.org/spec/v2.0.0.html) (pre-1.0: breaking changes may
land in minor versions).

## [Unreleased]

### Added
- Context windows for more well-known OpenAI models: `gpt-4-turbo` (128k),
  `gpt-4-32k` (32k) and the `o1` family (200k, with `o1-mini` / `o1-preview` at
  128k). These previously resolved to "unknown", which made `window_budget`
  silently **skip** every call on those models — a CI budget gate that quietly
  did nothing. Only *homogeneous* prefix families are added (every model ID
  under the prefix shares one window); bare `gpt-4` / `gpt-3.5-turbo` are left
  unlisted on purpose, because those prefixes straddle mixed-window families
  (`gpt-4-0613` 8k vs `gpt-4-1106-preview` 128k; `gpt-3.5-turbo-instruct` 4k),
  where an honest skip beats a confidently wrong number.

### Changed
- The read-side commands (`report`, `test`, `import`) and the MCP server now
  honour the `CTXLINEAGE_DIR` environment variable, the same way `init()`
  already does. A directory set once for capture no longer has to be repeated
  with `--dir` on every read (an explicit `--dir` still wins).

### Fixed
- A response that is already a plain `dict` — a raw-response wrapper, a mocked
  client, or a non-pydantic SDK shape — is no longer stringified on capture. It
  had no `model_dump`, so it fell through to `str(obj)` and was stored as a
  Python repr, which also dropped `usage` (read only off a dict). Such calls now
  keep their structured body and token counts.
- `ctxlineage import` no longer prints a coverage over 100%. On a short prompt
  the token estimate can exceed the provider's own reported count; the summary
  clamps the displayed coverage at 100% and drops the "the rest is the system
  prompt…" clause when there is no remainder to describe.
- A corrupt or truncated byte in `events.jsonl` (or an imported transcript) — a
  process killed mid-write leaves one at EOF — no longer aborts every command
  with a raw `UnicodeDecodeError`. The broken line is skipped and counted like
  any other malformed line, and the rest of the log still renders.
- `report --out path/into/a/missing/dir.html` now creates the parent directory
  instead of crashing with `FileNotFoundError` (same for the MCP
  `generate_report` tool).
- `report` over a log with no recorded calls now prints why it is empty
  (capture not wired up, or the wrong `--dir`) instead of silently writing a
  blank report.

## [0.2.0] - 2026-07-18

Context you can **test**, and coding-agent sessions you can **import** — the two
v0.2 tracks, plus the honesty fixes found building them.

### Added
- **pytest plugin** (#72): `pytest --ctxlineage` evaluates the same
  `ctxlineage.toml` contracts *inside* the suite that produces the events, and
  fails **the individual test** whose call breached a hard gate — the per-test
  attribution `ctxlineage test` structurally cannot give (it gates the whole
  run). Same runner, same tier rule: a warning never gates, and a call that
  could not be evaluated is reported as skipped, never a green test.
  - **Inert until opted in** with `--ctxlineage` (or `ctxlineage = true` in the
    pytest ini) — installing ctxlineage never changes a shared suite's
    behaviour. Owns `ctxlineage.init()` into a throwaway temp dir unless the app
    already called `init()`, in which case it uses the app's own directory.
  - Attribution rides on the append-only log: each test is scored over exactly
    the bytes written during its own phase, and events belonging to no test
    (import time, session fixtures, teardown) are swept and evaluated rather
    than left silently unchecked.
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

### Fixed
- **Streams the host abandons are no longer lost** (#34): a `create(stream=True)`
  whose return value was never iterated, closed, or exited had no emit path, so
  a call whose request really did reach the provider left no trace at all. It is
  now recorded when the object is collected, flagged `abandoned: true` — meaning
  "recorded from the finalizer; the host never finished, closed or exited this
  stream". This also covers a `next()`-then-drop, which never reached the
  iterator's cleanup either.
- **The recorded request no longer follows the host's later edits** (#34): the
  request was shallow-copied, so an app mutating its own `messages` list before
  a stream finished rewrote the record of what had already been sent. It is now
  snapshotted per key, falling back to the live reference for anything that
  cannot be copied.
- **A budget no longer passes over a prompt it cannot see** (#63, #71):
  `window_budget` with `segment=` scored only the reconstructed part of an
  imported call — a few tokens of a 33k prompt — and reported a confident green.
  It now skips such a call and says what is missing. The whole-prompt form
  stays exact when the provider reported `usage` (it reads that number, not the
  segments), and skips too when an import carried no usage at all — otherwise it
  would fall back to an estimate over the same partial segments and pass for the
  same wrong reason.
- **Imported calls disclose what the transcript could not preserve** (#64): the
  call anatomy proportioned segments against their own sum, so a handful of
  reconstructed tokens filled the whole bar and a 4-token segment read as "50%
  of input" — the breakdown appeared to account for a prompt it had mostly never
  seen. It now proportions against the real prompt, renders the unaccounted
  remainder explicitly, and names the source and the missing fields. The MCP
  server tells agents to check `segments_complete` for the same reason.
- A `ctxlineage test` run whose findings are all skips no longer summarises as
  "All N assertions passed" — skips do not gate, but calling an unevaluated run
  "passed" is the same thing the skip severity exists to prevent. It also no
  longer claims a segment "never appeared" when every call was skipped and its
  absence was never established.

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
