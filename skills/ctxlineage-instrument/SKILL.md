---
name: ctxlineage-instrument
description: >
  Instrument a Python project with ctxlineage so every LLM call's context becomes
  visible, lineage-traceable, and testable in CI. Use when asked to "add ctxlineage
  to this project", to instrument/trace/record LLM calls, to visualize or debug what
  an LLM prompt is made of, to find token waste in a RAG or agent app, to generate or
  read a ctxlineage report, to assert context contracts / gate context in CI
  (`ctxlineage test`, `ctxlineage.toml`, window budgets, grounding checks), or to
  import a Claude Code / `claude -p` session into a report.
---

# Instrument a project with ctxlineage

ctxlineage records every OpenAI / Anthropic SDK call to a local, append-only
`.ctxlineage/events.jsonl` and renders a single static HTML report showing what
each call's context window was made of (Call Anatomy) and how data flowed
between calls (Lineage Graph). Everything stays on the user's machine: no
server, no database, no network egress — capture is read-only and never
rewrites or proxies requests, so instrumenting an app must not change its
behavior.

Follow the steps in order. Steps 1–2 alone already produce a useful report
(role-based segmentation); steps 3+ add named segments and provenance.

## Step 1 — Install and wire `init()`

```bash
pip install ctxlineage        # or: uv add ctxlineage
```

Add two lines at the process entry point:

```python
import ctxlineage
ctxlineage.init()   # auto-instruments the openai / anthropic SDKs from here on
```

Placement decision, in order of preference:

1. **The `main()` / CLI entry function** of the app, before any LLM client is used.
2. **The app factory / startup hook** for servers (FastAPI lifespan, Django
   `AppConfig.ready`, worker boot function) — anywhere that runs exactly once
   per process, before the first LLM call.
3. If LLM calls happen at import time (rare, discourage it), put `init()` at
   the top of the module that triggers them.

Rules:

- `init()` is idempotent — the **first call wins** (one session per process),
  so calling it defensively in more than one entry point is safe.
- It must run **before the first LLM call**; calls made earlier are not recorded.
- `span()` / `tag()` are silent no-ops before `init()` — safe in library code.
- Output directory: `init("path")` argument > `CTXLINEAGE_DIR` env var >
  `./.ctxlineage` default.
- Add `.ctxlineage/` to `.gitignore` (event logs contain full prompts).

## Step 2 — Find the context-assembly sites

Locate every LLM call site:

```bash
grep -rn "chat.completions.create\|responses.create\|messages.create" --include="*.py" .
```

For each hit, trace **upstream** to the function where the `messages` array /
prompt is composed — where the system prompt, retrieved chunks, history slice,
tool results, few-shot examples are concatenated. That composing function
(a request handler, an `answer()` function, an agent step loop) is where the
span goes. Typical markers: f-strings building a "Context:" block, `history[-n:]`
slicing, retriever/vector-store calls, `json.dumps` of tool output.

## Step 3 — Add spans and tags

Wrap each logical unit of work (one user turn, one agent step, one pipeline
stage) in a span, and tag every context element **before** the LLM call:

```python
with ctxlineage.span("answer_query") as span:
    span.tag("system", SYSTEM_PROMPT, source="prompts/answer.txt")
    span.tag("rag_chunks", chunks, source="qdrant:products_v2", transform="top_k(4)")
    span.tag("history", messages[-10:], transform="last_10_messages")
    resp = client.chat.completions.create(model=..., messages=...)
```

**The one rule that makes matching work: tag the exact object you interpolate
into `messages`.** The report finds segments by substring match against the
tagged content. Tag the joined string, or the list itself — JSON-array tags are
matched element-wise, and lists of dicts contribute their `text` / `content` /
`page_content` field (LangChain-style documents work as-is). Tagging a
paraphrase, a pre-truncation version, or an object whose `str()` differs from
what was sent will show up as an unmatched tag.

Conventional tag names (any name works; these render intuitively):

| name          | element                                     |
| ------------- | ------------------------------------------- |
| `system`      | system prompt                               |
| `rag_chunks`  | retrieved documents                         |
| `history`     | prior conversation slice                    |
| `tool_result` | output of a tool fed back into the context  |
| `memory`      | long-term memory / user profile snippets    |
| `few_shot`    | in-context examples                         |

Provenance conventions:

- `source=` — where the content came from: `"<store>:<collection>"`
  (`"qdrant:products_v2"`), a file path (`"prompts/answer.txt"`), or
  `"tool:<name>"` (`"tool:search_notes"`).
- `transform=` — how it was derived: `"top_k(4)"`, `"last_10_messages"`,
  `"truncate(2000)"`, `"summarize"`.

Constraints:

- Re-tagging the same name in one span (e.g. `tool_result` per tool call in a
  loop) aggregates into one element: the report shows the occurrence count
  (`tool_result ×3`) and every distinct source/transform, so no provenance is
  lost. Number the tags (`tool_result_1`, ...) only if you want each occurrence
  as its own separate node.
- Spans propagate to async tasks but **not to new threads**; when fanning out
  to threads, carry the context with `contextvars.copy_context()`.
- Tags describe context; they never modify it. Do not restructure the user's
  prompt-building code to "make tagging easier".

Working exemplars to imitate: `examples/rag_app.py` (RAG, all three tag kinds,
`source=`/`transform=`), `examples/agent_app.py` (multi-turn tool loop,
per-turn spans, `tool_result` tagging), and `examples/anthropic_app.py`
(anthropic Messages: top-level `system` kwarg, a tool_use/tool_result round
trip, streamed final call).

## Step 4 — Run the app and build the report

```bash
python <their_app_entrypoint> ...        # any normal run of the instrumented app
ctxlineage report --open                 # .ctxlineage/events.jsonl → ctxlineage-report.html
```

`ctxl` is an alias for `ctxlineage`. Useful flags: `--dir/-d` (events location),
`--out/-o` (HTML path), `--json` (print report data instead of HTML).

Confirm capture worked: `.ctxlineage/events.jsonl` exists and grew, and the
report banner counts the calls you expected.

## Step 5 — Read the report (HTML or `--json`)

`ctxlineage report --json` emits the exact data behind the HTML:

- `stats.tags.match_rate` — fraction of tags whose content was found in the
  prompts. **This is the instrumentation quality score**; below 1.0 means some
  tag content differs from what was actually sent (see Step 3's rule).
- `sessions[].calls[]` — one per LLM call: `model`, `usage` (real token counts
  when the API returned them), `input_tokens_est`, `context_window`,
  `call_stack` (who made the call), `step` (span name), `error`.
- `sessions[].calls[].segments[]` — the context anatomy: `kind` (tag name, or
  role for untagged parts), `content`, `tokens_est`, `tagged`,
  `match` (`exact`/`partial`), `source`, `transform`. Tool/function definitions
  appear as a `tool_defs` segment — they consume window tokens too.
- `sessions[].elements[]` — one per tagged element: `name`, `source`,
  `transform`, `matched`, `calls` (which calls consumed it).
- `sessions[].edges[]` — lineage: `{from, to, kind}` where kind is
  `output_text` (call A's output found in call B's input) or `same_span`
  (consecutive calls of one span).

That answers the standard questions directly: *which call consumes the most
tokens* (max `input_tokens_est` / `usage.prompt_tokens`), *what share is RAG
chunks* (sum that segment's `tokens_est` over the call's total), *does call A
feed call B* (an `output_text` edge from A to B).

## Step 6 — Iterate on the untagged remainder

Large `tagged: false` segments in important calls are the TODO list: trace
where that text is assembled, add a tag there, re-run, and re-check
`match_rate`. Stop when the heavy calls are explained — 100% coverage of
trivial calls is not the goal.

## Step 7 — Turn the findings into a gate (`ctxlineage test`)

A report tells the user what happened once. A contract keeps it from regressing.
Once the heavy calls are explained, offer this — it is the payoff for the tags
added in step 3.

Write `ctxlineage.toml` next to the app:

```toml
[[assert.window_budget]]
max_pct = 80             # no call may exceed 80% of the model's window

[[assert.window_budget]]
segment = "history"      # a segment kind, or a tag name from step 3
max_pct = 40

[[assert.grounded]]
tag = "rag_chunks"       # what you retrieved must actually reach the window
warn_dead = true         # advisory: flag chunks nothing downstream consumed
```

```bash
ctxlineage test          # exit 1 on a hard-gate breach → wire into CI
```

**Set the numbers from what the run actually shows, not from a round number the
user will have to fight.** Read the current share first (step 5), then set the
budget above it with headroom.

Rules only gate where their evidence is exact, and this is not negotiable:

- `window_budget` gates any call — token counts are deterministic from capture.
- `grounded` presence gates **only tagged** content: the `tag()` is the user's
  declaration, so "it never arrived" is exact. Untagged → it warns instead.
- dead-context is **always advisory** — "nothing used it" is read off inferred
  lineage edges, and gating on inference produces a flaky build.

Anything unevaluated (unknown model window, incomplete segments) is reported as
skipped — **never** as a pass. If the user asks to make a skip fail, the answer
is to supply the missing evidence (tag the content), not to force the gate.

**CI needs a recorded run**, and it need not be a live one: prompt assembly is
usually deterministic even when replies are not, so the user's existing test
suite with mocked responses is the place to start. A live run needs a
statistical gate (N runs, a pass rate), which does not exist yet — so keep hard
gates on recorded runs.

## Importing coding-agent sessions

If the user wants to inspect a **Claude Code / `claude -p`** session rather than
their own app: those are separate, non-Python processes, so `init()` cannot
patch them — but they write a transcript to disk.

```bash
ctxlineage import --from claude-code    # newest session for this directory
ctxlineage report --open
```

Tell the user what it cannot show, or the report will look broken: token counts
are the API's own, but the transcript **does not preserve** the system prompt,
tool definitions, or reasoning text — tens of thousands of tokens that were sent
and are counted, but whose text is unrecoverable. The report labels that
remainder explicitly. A `segment=` budget skips such calls for the same reason.
Live capture via `init()` remains the complete picture.

## Final checklist

- [ ] `init()` runs once, at process start, before the first LLM call.
- [ ] `.ctxlineage/` is gitignored.
- [ ] App behavior unchanged (capture is passive; a failing writer only warns).
- [ ] Every heavy context element is tagged with the exact interpolated content.
- [ ] `source=` / `transform=` recorded where provenance exists.
- [ ] `ctxlineage report --open` builds; match rate ≈ 1.0 or the gap is explained.
- [ ] If contracts were added: `ctxlineage test` passes, its numbers come from
      the real run, and nothing gates on inferred evidence.
