<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
  <img src="assets/logo.svg" alt="ctxlineage logo" width="120">
</picture>

# ctxlineage

### See exactly what context each LLM call consumed — and how it flowed into the next.

Two lines of code turn your app's LLM calls into a single static HTML report:
the anatomy of every context window, and the lineage of every context element.
**No server, no database, no account. Your prompts never leave your machine.**

Not another trace viewer: ctxlineage makes your runtime context an **engineered
artifact** — decomposed, provenance-tracked, and *(v0.2)* testable in CI.

[![CI](https://img.shields.io/github/actions/workflow/status/ctxlineage/ctxlineage/test.yml?branch=main&style=flat-square&logo=githubactions&logoColor=white&label=CI)](https://github.com/ctxlineage/ctxlineage/actions/workflows/test.yml)
[![Coverage](https://img.shields.io/codecov/c/github/ctxlineage/ctxlineage?style=flat-square&logo=codecov&logoColor=white)](https://codecov.io/gh/ctxlineage/ctxlineage)
[![PyPI](https://img.shields.io/pypi/v/ctxlineage?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/ctxlineage/)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](LICENSE)

</div>

<p align="center">
  <img src="docs/assets/report-calls.png" alt="Call anatomy view — what filled the context window, segment by segment" width="800">
</p>

## Quickstart

```bash
pip install ctxlineage
```

```python
import ctxlineage
ctxlineage.init()  # auto-instruments the openai and anthropic SDKs
# ... run your app: every LLM call is recorded to .ctxlineage/events.jsonl
```

```bash
ctxlineage report --open   # one self-contained HTML file
```

That's the whole integration. Streaming, async, tool calls — captured.
Add `.ctxlineage/` to your `.gitignore`.

Every command is also available as `ctxl`. `ctxlineage report --json` prints the
normalized report data to stdout instead of writing HTML, if you want to pipe it
somewhere.

## What you get

| View | Question it answers |
|---|---|
| **Overview** | Which calls are heaviest? How close to the window limit? Did my tags match? |
| **Calls** | What *actually* filled this call's context window — and at what token cost? |
| **Chain** | How did each output become the next call's input? Where do agent loops accumulate context? |
| **Graph** | Where did each context element *come from* (vector DB, prompt file, memory) and which downstream calls did it influence? |

Think **`dbt docs generate`, for your context windows**: calls are functions,
context elements are typed inputs with provenance, outputs flow into
downstream inputs.

<p align="center">
  <img src="docs/assets/report-tour.gif" alt="Ten-second tour: Overview, call anatomy, session chain, lineage graph with click-to-trace, dark mode" width="800">
</p>

## Tagging (optional, unlocks lineage)

Everything above works with zero tagging. Label your context assembly and the
report upgrades from role-based heuristics to real, provenance-carrying
segments:

```python
with ctxlineage.span("answer_user_query") as span:
    span.tag("rag_chunks", docs, source="qdrant:products_v2", transform="top_k(8)")
    span.tag("memory", user_profile, source="memory:user_prefs")
    resp = client.chat.completions.create(...)
```

Tagged content is matched back into the actual prompts (exact → partial →
honestly-untagged, with the match rate displayed — never fabricated).

## Testing context in CI (`ctxlineage test`)

The same recorded artifact is a substrate for **deterministic assertions** — no
LLM judge, no eval dataset. Write a `ctxlineage.toml`:

```toml
[[assert.window_budget]]
max_pct = 80                 # no call may exceed 80% of the model's window

[[assert.window_budget]]
segment = "tool_defs"        # a segment kind, or a tag name
max_pct = 20                 # tool definitions may not eat >20% of the window

[[assert.grounded]]
tag = "rag_chunks"           # every rag_chunks tag must land in the window
warn_dead = true             # advisory: flag chunks nothing downstream consumed

[[assert.requires_segment]]
kind = "system"              # every call must carry a system segment
when_model = "gpt-*"         # optional glob: scope to matching models only

[[assert.segment_diff]]
baseline = "baselines/golden.jsonl"  # a prior recorded run, resolved relative to this file
max_token_delta = 200         # a segment may grow by at most this many tokens vs. baseline
segment = "tool_defs"         # optional; omitted = the whole prompt

[[assert.metamorphic]]
variant = "runs/shuffled.jsonl"  # the same scenario, recorded with one input perturbed
relation = "invariant"        # "invariant": the perturbation must NOT change this segment
segment = "rag_chunks"        # required, and must be a tag name to gate (see below)
```

```bash
ctxlineage test              # exits non-zero on a hard-gate failure → CI gate
```

**A rule only gates where its evidence is exact**, which decides what can fail
your build:

| | Gates on | Because |
| --- | --- | --- |
| `window_budget` | any call, **no tagging needed** | token counts and the model window are deterministic from capture alone |
| `requires_segment` | any call, **no tagging needed** | segment presence is deterministic from capture alone — this asserts *whether*, not *how much* |
| `segment_diff` | any call, **no tagging needed** | comparing two recorded runs' own token counts is deterministic from capture alone |
| `grounded` presence | tagged content only | the `tag()` is your declaration, so "it never reached the window" is exact |
| `metamorphic` | tagged content only | untagged, your chunks are one joined string — "reordered" and "rewritten" are indistinguishable, so the relation can't be expressed at all |
| `grounded` dead-context | nothing — **advisory** | "nothing downstream used it" is read off *inferred* lineage edges |

Gating on inference is how you get a flaky gate, so ctxlineage won't do it: an
untagged run warns instead of failing, and anything that couldn't be evaluated
(an unknown model window, a segment name that never appeared) is reported as
skipped or warned — never silently passed.

`segment` selects on the kinds the pipeline really produces — `system`, `user`,
`assistant`, `tool`, `tool_defs` — or any tag name.

`segment_diff` pairs calls across the two runs positionally — by session
order, then by span name within a session — since there is no other stable
identity to match on. A call whose pairing can't be found (the pipeline's
shape changed since the baseline was recorded) warns rather than fails: a
missing counterpart is not itself a content regression. Positional session
pairing only warns when session **counts** differ; if counts match but a
session's identity has quietly changed (a new session type inserted ahead
of an old one, same-second sessions reordering), the diff compares
unrelated sessions with no warning — a baseline is only trustworthy against
a pipeline whose session shape hasn't changed since it was recorded.

`metamorphic` asserts how your **assembled context** was allowed to respond
to a perturbation: record the run twice, once normally and once with one
input deliberately changed, then state the relation.

- `relation = "invariant"` — the perturbation must **not** change this
  segment's contents. Shuffling retrieval order is the canonical case:
  the order may differ, the chunks may not. Catches order-sensitive dedup
  and order-dependent truncation — a shuffle that quietly drops a chunk.
- `relation = "changed"` — the perturbation **must** change them. Dropping
  a chunk, or lowering `k`, has to actually reach the prompt. Catches a
  parameter your pipeline silently ignores.

It asserts on the context, not on the model's answer, even though "the
answer should be invariant" is the more familiar phrasing. Judging that two
different answers mean the same thing is a semantic call — that's the
LLM-judge layer, not a deterministic gate — and it would be vacuous on
exactly the mocked/replayed runs this page recommends gating, since a canned
reply can't respond to a perturbed input at all.

This one needs `tag()`. Untagged, your retrieved chunks arrive as one joined
message and land as a single segment, so a reorder rewrites one opaque
string and "same chunks, reordered" is indistinguishable from "different
chunks". `span.tag("rag_chunks", docs)` splits them per chunk, and only then
does the comparison mean what it says — so an untagged run warns that it
couldn't gate, rather than failing or quietly passing.

### Try it in 30 seconds (no API key)

```bash
python examples/rag_app.py --mock            # records a real run, fully offline
ctxlineage test -c examples/ctxlineage.toml  # → All 4 assertion(s) over 3 call(s) passed
```

[`examples/ctxlineage.toml`](examples/ctxlineage.toml) is a commented reference
config; CI runs exactly the two commands above, so it cannot drift from the code.

`segment_diff` and `metamorphic` compare **two** recorded runs, so they need a
second recording rather than a second block in the same config:

```bash
python examples/rag_app.py --mock                                             # baseline
CTXLINEAGE_DIR=.ctxlineage-shuffled python examples/rag_app.py --mock --shuffle-chunks
ctxlineage test -c examples/ctxlineage-metamorphic.toml   # → All 1 assertion(s) over 3 call(s) passed
```

`--shuffle-chunks` reverses the retrieval order and changes nothing else, so
the `invariant` relation must hold. CI runs these commands too — and also
asserts the gate can *fail*, by flipping the same pair to `changed`.

### Wiring it into CI

`ctxlineage test` gates a **recorded run**, so CI needs one. It does not have to
be a live one — assertions read the JSONL, not the network:

```yaml
# .github/workflows/context.yml
- run: pip install ctxlineage
- run: pytest tests/            # your existing suite, with ctxlineage.init() in it
- run: ctxlineage test          # fails the build on a hard-gate breach
```

Where the events come from is your choice, and the trade is real:

| | Deterministic? | Cost |
| --- | --- | --- |
| Your existing tests, with mocked/replayed responses | yes — **start here** | none |
| A live run against the real API | no (temperature, model drift) | tokens |
| `ctxlineage import --from claude-code` | yes (a recorded session) | none |

Prompt *assembly* is usually deterministic even when the model's replies are
not — which is the point: a mocked run still proves what your code put in the
window. The examples take this route (`--mock` replays canned responses), and
so can your test suite. Live runs need a statistical gate (run N times, assert a
pass rate) rather than a single hard gate — that is not built yet, so keep hard
gates on recorded runs.

### Inside pytest, per test (`pytest --ctxlineage`)

The two-step wiring above keeps the events and the assertions in separate
places, and can only ever fail the *whole* run — "some call in this log blew the
budget". If your suite already runs the app under `ctxlineage.init()`, the
bundled pytest plugin evaluates the same `ctxlineage.toml` **inside the suite**
and fails **the test whose call breached it**:

```bash
pytest --ctxlineage
```

```
FAILED tests/test_agent.py::test_long_loop
  FAIL  window_budget: ... is 91.4% of the 200,000-token window, over the 80% budget
```

Per-test attribution is the whole reason to prefer it over `ctxlineage test`;
that is the one thing the CLI cannot do. Everything else is identical — same
config, same tier rule (a warning never gates, an unevaluated call is reported
as skipped, never a green test). It is **inert until you pass `--ctxlineage`**
(installing ctxlineage never changes a suite's behaviour), or you can commit the
choice:

```toml
# pyproject.toml
[tool.pytest.ini_options]
addopts = "--ctxlineage"
```

The plugin owns `ctxlineage.init()` into a throwaway temp dir for the run, unless
your app already called `init()` — then it uses your app's directory and leaves
capture alone. `ctxlineage test` is still the tool for a recorded run produced
*outside* pytest (a script, a `--mock` example, `ctxlineage import`), and for a
`grounded` tag declared once for the whole run rather than inside one test.

| | Gate granularity | Recorded run comes from |
| --- | --- | --- |
| `ctxlineage test` | the whole run | anywhere — a script, an example, an import |
| `pytest --ctxlineage` | **the individual test** | the suite itself, as it runs |

> **It gates _real_ LLM calls.** A suite that mocks its provider — the usual way
> to keep tests deterministic and free — stubs the call site, so `init()`'s patch
> never runs and there is nothing to record. The run is green because nothing was
> gated, not because your context is under budget. The plugin says so —
> `note: 0 of N test(s) produced a gateable LLM call (provider mocked? this
> plugin gates real calls)` — rather than passing in silence. To actually gate,
> add one test that makes a real call (a nano model, well under a cent), or run
> `ctxlineage test` / `ctxlineage import` over a recorded run.

## Importing coding-agent sessions (`ctxlineage import`)

Claude Code and `claude -p` are separate, non-Python processes, so
`ctxlineage.init()` can't patch them. But they already write a session
transcript to disk — so ctxlineage reads that local file. Nothing is proxied,
injected, or sent anywhere; it is the same local-first bargain as capture.

```bash
ctxlineage import --from claude-code       # newest session for this directory
ctxlineage import --from claude-code --session <id>
ctxlineage import --from claude-code path/to/session.jsonl
ctxlineage import --from claude-code --dry-run   # show what it would do, write nothing
ctxlineage report --open                   # renders like any captured session
```

Imported events land in `.ctxlineage/` (retarget with `-d/--dir`), alongside
anything `init()` already captured. Re-importing a session already in the log is
refused rather than silently double-counting it — pass `--dir` to import a copy
elsewhere. The transcript becomes ordinary `events.jsonl`, so the four views,
`ctxlineage test`, and the MCP server all work on it unchanged.

**What a transcript cannot tell you.** This is reconstruction, not capture, and
the gaps are real — the report is honest about them rather than inventing
numbers:

| | |
| --- | --- |
| Token counts | **The API's own** `usage`, reconstructed from the transcript — not estimated |
| Segment sizes | **Estimated** (the prompt text is rebuilt from the conversation tree) |
| System prompt, tool definitions | **Not preserved** — they were sent and cost tokens, but the transcript never records them |
| Reasoning text | **Not preserved** — kept as a signature with the text stripped |
| Request params, duration, stream flag | **Not preserved** |

So an imported call's segments account for only part of its real prompt: the
system prompt and tool definitions alone can be tens of thousands of tokens.
`ctxlineage import` prints the coverage it achieved, and the unaccounted
remainder is recorded per call. Live capture via `ctxlineage.init()` remains the
complete picture; import is the bridge to processes you cannot patch.

**What this means for `ctxlineage test`.** Segment-level contracts need *exact*
segments, so they gate only on native `init()` capture — not on an import, where
`segments_complete` is `false`. A `window_budget` with `segment=` **skips**
imported calls (and says why, naming native capture as the fix) rather than
scoring a fraction as the whole. The whole-prompt `window_budget` still gates an
import, because it reads the provider's own `usage`. So: **use import to explore
the anatomy and the whole-prompt budget; use native capture to gate segments.**

## Querying the report from an agent (MCP server)

The same recorded artifact is exposed to MCP-speaking clients (Claude Code,
Claude Desktop, …) through a **read-only** server, so an agent can ask about a
run instead of you eyeballing the HTML. It reads the same local `events.jsonl`;
nothing is transmitted.

```bash
pip install "ctxlineage[mcp]"     # the server ships in the mcp extra
ctxlineage-mcp --dir .ctxlineage  # stdio transport; -d defaults to .ctxlineage
```

Point an MCP client at it — e.g. for Claude Code:

```jsonc
// .mcp.json  (or your client's MCP config)
{
  "mcpServers": {
    "ctxlineage": { "command": "ctxlineage-mcp", "args": ["--dir", ".ctxlineage"] }
  }
}
```

Four tools, all read-only:

| Tool | Returns |
|---|---|
| `list_sessions` | Recorded sessions + global stats (call/error counts, tag match rate); ids to feed the other tools, never prompt bodies |
| `get_call` | One call's full anatomy — segments, usage, output, timing (long text truncated unless `full_content=true`) |
| `get_lineage` | Lineage for one node id (a call or a context element): what it consumed, edges in/out, everything downstream it influenced |
| `generate_report` | Builds the static HTML report (refuses to write inside the read-only event-log dir) |

## Principles

- **Local-first, zero-server.** The artifact is one HTML file that opens
  offline. Capture is an append-only local JSONL. Nothing is ever transmitted.
- **Non-intrusive by default.** `init()` and you're done; tags are progressive
  enhancement. The capture layer never breaks your app — failures degrade to
  warnings, not exceptions.
- **Honest data.** Real `usage` counts preferred over estimates, estimates
  labeled, unmatched tags shown as unmatched, inference caps disclosed.

## Scope & limits

Designed as a **per-run, dev-time artifact**. Comfortable up to ~5,000 calls
(~15 MB report); usable to ~20,000 calls (~65 MB). Reports contain full prompt
text — treat them like logs with sensitive data (see [SECURITY.md](SECURITY.md)).
Before sharing, mask secrets with `ctxlineage report --redact "sk-[A-Za-z0-9]+"`
(repeatable regex; the report discloses what was redacted), or keep them out of
the log entirely with `ctxlineage.init(redact_fields=["request.messages.content"])`.
Each `redact_fields` entry is a dotted path into the recorded event payload
(`request.messages.content` masks the content of every recorded message); the
matched values are replaced before anything is written to disk.

## Status

**v0.2.1** — the capture layer (openai + anthropic), the four-view report, the
span/tag lineage pipeline, the read-only MCP server, and runnable examples, plus:

- **`ctxlineage test`** — deterministic context contracts (`window_budget`,
  `grounded`) that gate a recorded run in CI, and a **pytest plugin**
  (`pytest --ctxlineage`) that attributes a breach to the test that caused it.
- **`ctxlineage import --from claude-code`** — bring a Claude Code / `claude -p`
  session into the same report, honest about what a transcript cannot preserve.

See the [CHANGELOG](CHANGELOG.md) and the
[issues](https://github.com/ctxlineage/ctxlineage/issues).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — DCO sign-off, hermetic tests,
deliberately small maintenance surface ([off-roadmap issues may be closed](docs/PLAN.md)).

## License

[Apache-2.0](LICENSE)
