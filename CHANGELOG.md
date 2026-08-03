# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning:
[SemVer](https://semver.org/spec/v2.0.0.html) (pre-1.0: breaking changes may
land in minor versions).

## [Unreleased]

### Added
- **`requires_segment`** — a new `ctxlineage test` rule asserting every call
  (optionally scoped to models matching `when_model`, a glob) carries a
  segment of a given kind. The structural counterpart to `window_budget`:
  not *how much* is in the window, but *whether the right thing is there at
  all*. Deterministic from capture alone, no tagging needed, hard-gates like
  `window_budget` — but unlike it, absence is never demoted to a warning,
  since here absence *is* the failure. An imported call whose segments are
  declared partial skips rather than fails (the same #63 reasoning
  `window_budget`'s segment form already uses — absence there is ambiguous
  between "never sent" and "not preserved by the transcript") (#94).
- **`segment_diff`** — a new `ctxlineage test` rule comparing this run's
  segment token counts against a previously-recorded baseline run, call for
  call, and failing when a segment grows past a token-delta budget. This is
  the "regression/differential" assertion class the contract-testing vision
  doc named as its own natural first deliverable, now shipped. Pairing is
  positional (no cross-run call identity exists otherwise): sessions by
  position, calls within a session by span-name occurrence order. A call
  with no counterpart on the other side warns rather than fails — a pairing
  gap is not itself a content regression. `baseline = "..."` resolves
  relative to the `ctxlineage.toml` file's own directory, not the process's
  working directory (#94).
- **`metamorphic`** — a new `ctxlineage test` rule asserting how the
  *assembled context* was allowed to respond to a perturbed input: record
  the run twice (once normally, once with one input changed) and declare
  the relation. `invariant` (shuffling retrieval order must not change what
  the context contains — catches order-sensitive dedup and truncation) or
  `changed` (dropping a chunk must actually reach the prompt — catches a
  silently-ignored parameter). This is the vision doc's third assertion
  class, CheckList's INV/DIR, shipped at the **context** level: judging that
  two different *answers* mean the same thing is a semantic call reserved
  for the judge tier, and would be vacuous on the mocked runs the README
  recommends gating anyway. Like `grounded`, it needs `tag()` to gate —
  untagged, chunks are one joined string and "reordered" is
  indistinguishable from "rewritten", so it warns rather than failing or
  passing. Output-level metamorphic, with the statistical gating a live run
  would need, is deferred to its own design discussion (#14, #105).
- The Calls view renders a JSON segment or output body as a collapsed,
  expandable tree — top-level keys visible at a glance, nested branches
  opened on click — instead of an undifferentiated wall of quotes and
  braces. Non-JSON content is unaffected (#92).
- Chain's lineage edges now say **what** flowed, not just that something did:
  each arrow carries a token-count label and a hover tooltip with a snippet
  of the matched text, and terminates on the specific segment the match
  landed in — previously it always pointed at the aggregated assistant
  ("fed") chip, even when the match was actually in a `tool`-kind segment
  (#93).

- A runnable on-ramp for the two rules that compare two recorded runs:
  `examples/rag_app.py --shuffle-chunks` records the same scenario with the
  retrieval order reversed and nothing else changed, and
  `examples/ctxlineage-metamorphic.toml` asserts `invariant` over the pair.
  CI runs the documented commands and also asserts the gate can *fail* by
  flipping the relation to `changed`, so the workflow the README prints
  cannot drift from the code — the same guarantee `examples/ctxlineage.toml`
  already gives the single-run rules (#109).

### Fixed
- The `mcp` extra is capped at `mcp>=1.2,<2`. `mcp` 2.0 removed
  `mcp.server.fastmcp`, which `ctxlineage_mcp.server` imports, so the
  previously unbounded range meant a fresh `pip install 'ctxlineage[mcp]'`
  installed a server that raised on import. The weekly upgrade job caught it.
  The cap comes off once the server is ported to 2.x's `MCPServer` (#114).
- The MCP server's import guard no longer answers every failure with "install
  the `mcp` extra". When `mcp` is installed but too new to provide `FastMCP`,
  that instruction fixes nothing; the error now names the version actually
  found and asks for the pin instead (#114).
- Imported agent-loop sessions no longer show every call in one episode with
  the identical label (the human turn's own sentence) across Overview, Chain,
  the Calls sidebar and the fn card — in a real trial, 38 consecutive calls
  read as one repeated title. Calls now get a per-call label distinct from
  the span/episode label: preferably the tool whose result fed this call's
  own input, else the tool this call's own output invoked, else the existing
  span-based fallback. Native capture is unaffected — its own per-call
  `call_stack` already served this purpose and is still checked first (#88).
- The INSTRUCTIONS panel and the output body now show a visible `▸`/`▾`
  toggle affordance. The panel already expanded on click; nothing on the
  page said so. The output body gained an expand toggle it previously
  lacked entirely, removing its height cap on click instead of capping long
  output at a fixed, non-obvious 300px. Reopening a panel after scrolling it
  now returns to the top instead of silently resuming mid-content (#92).
- The Graph view no longer renders a mostly-blank three-column layout on a
  session with no tagged elements — a **default experience for every v0.2.x
  user who hasn't adopted `span()`/`tag()`**, not an import-only artifact.
  The SOURCES/CONTEXT ELEMENTS columns collapse and the call column uses the
  full width; the banner is reworded for the general untagged case, with an
  honest, non-actionable variant for imported sessions (tagging is
  structurally impossible there) (#89).
- Segment shares on an imported call — where recovery can be well under 1%
  of the real prompt — now show a second, explicitly-labelled basis (`X tok
  · Y% of prompt · Z% of recovered`) alongside the existing real-prompt
  share, so a segment that rounds to "0% of prompt" isn't the only number on
  the page. Shown only when the two bases differ; live capture is unchanged
  (#90).
- A `[thinking: 0 chars not shown]` placeholder — printed once per stripped
  reasoning block, even when the transcript kept none of its text — no
  longer repeats itself across every output on an imported call; the
  stripped-block count is already shown once, in the provenance panel. A
  block with real, policy-hidden content (native capture) keeps its marker
  (#90).

## [0.2.1] - 2026-07-20

Maintenance: honesty and robustness fixes found putting v0.2.0 through a real
run, plus a few more model context windows. No API changes.

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
- `pytest --ctxlineage` now reports how many tests produced a gateable LLM call
  (e.g. `note: 0 of 34 test(s) produced a gateable LLM call (provider mocked?)`).
  A suite that mocks its provider records no events, so the run is green because
  nothing was gated — the note keeps that from reading as "context under budget"
  (#82).
- A `window_budget` with `segment=` that skips an imported call now names the
  remedy in its skip message — native `ctxlineage.init()` capture, which an
  import cannot reconstruct — instead of only stating the problem. The README
  now frames it directly: use import to explore, native capture to gate segments
  (#83).

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
- `ctxlineage.__version__` now reflects the installed package version instead of
  a stale hardcoded `0.0.1.dev0`. It is derived from the package metadata, so it
  can no longer drift from what `pip install` gives. (`ctxlineage --version` was
  always correct; only the attribute lagged.)

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
