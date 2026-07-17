# Implementation Plan: v0.2 Context Contract Testing — first slice (#14)

> **Status:** implementation plan for the first slice of the v0.2 "depth" track.
> Design is settled in [docs/vision/context-contract-testing.md](../vision/context-contract-testing.md)
> §14; this document records the *implementation* decisions that §14 left open.
> This plan does not re-open the design.

## 1. Scope

§14's build order, verbatim:

1. The shared runner + `ctxlineage.toml` schema + `ctxlineage test` exit-code plumbing.
2. `window_budget` — the wide, tagless on-ramp.
3. `grounded` — presence + dead-context, the tag-gated differentiation proof.

Out of scope for this slice, explicitly deferred:

- **Utilization** ("did the output actually use it?") — not deterministic; belongs to
  the LLM-judge tier (§7). Never shipped as a deterministic claim.
- **Regression / differential** and **metamorphic** assertions (§8) — the *next* slices.
- **Plugin entry-point loading.** §12's scope guard is "a handful of built-in relations
  plus a plugin hook — not a framework". The rule registry is built so a hook is a
  small addition later, but no third-party loading ships here (not in §14's build
  order; YAGNI until someone asks).

## 2. The load-bearing constraint: the tier rule (§6)

The tier rule is what keeps the positioning honest, so it is a structural property of
the runner rather than a per-rule detail. Every rule declares the tier it can reach,
and the runner is what demotes:

| Rule | Evidence | Tier |
| --- | --- | --- |
| `window_budget` | capture alone (token counts, model window) — deterministic | **hard gate**, tags not required |
| `grounded` presence | the `tag()` declaration + segment matching — exact lineage | **hard gate** |
| `grounded` presence, tag absent from the run | nothing declared → only inferred lineage | **demoted to warn** |
| `grounded` dead-context | call→call lineage **edges**, which are inferred (substring + same-span heuristics) | **always advisory** (hence §14's `warn_dead`, not `fail_dead`) |

The demotion path is why `grounded` cannot simply fail when a configured tag is
missing from the run: with no tag there is no exact lineage, so an assertion about it
is not gate-able. It warns ("no `rag_chunks` tag recorded — cannot gate") and exits 0.
Mixing these tiers is what §5/§6 warn produces a flaky gate, so the demotion is
covered by tests, not just by convention.

## 3. Rule semantics

### 3.1 `window_budget`

```toml
[[assert.window_budget]]
max_pct = 80                 # no call may exceed 80% of the model window
[[assert.window_budget]]
segment = "assistant"
max_pct = 40                 # assistant turns may not eat >40% of the window
```

Per call: `used / context_window * 100 > max_pct` → **fail**.

- `used`, unscoped: the real `usage.prompt_tokens` when the provider reported it,
  else `input_tokens_est`. Preferring real usage over estimates is the existing house
  rule (CLAUDE.md); the result records which one was used so the failure message can
  say so.
- `used`, with `segment = "<kind>"`: the summed `tokens_est` of that kind's segments.
  Real usage is only ever a per-call total, so a segment-scoped budget is necessarily
  an estimate — the message says `est.` so nobody reads it as measured.
- `context_window is None` (model not in `MODEL_CONTEXT_WINDOWS`) → **skip**, not pass.
  An unknown window cannot produce a percentage, and silently passing an unevaluated
  call is the "silently green" failure mode this whole track exists to prevent.

### 3.2 Deviation from §14's sketch: `segment = "history"`

§14 sketches `segment = "history"`, but **no segment of kind `history` exists** in the
pipeline. `build_report_data` produces role-derived kinds — `system`, `user`,
`assistant`, `tool`, `tool_defs` — plus a tag name for tagged parts. "history" is a
concept from the doc's prose (§2), not a value in the data.

Decision: `segment` selects on the **real `kind` vocabulary**, and the docs state that
vocabulary. Inventing a synthetic `history` grouping (e.g. "every message but the last
user turn") would mean shipping a guess about the user's chat structure as if it were
captured fact — the same dishonesty the tier rule guards against. §14 flagged its names
as provisional; this is that provision being used.

Consequence to guard: a `segment` selector that matches nothing sums to 0 and would
pass every call — a typo would look green forever. So a rule whose selector matched
**no segment in any call across the whole run** is reported as a **warn**, not a pass.
Per-call absence stays a legitimate 0% pass (a call with no `tool` segments really does
spend 0% of its window on tools).

### 3.3 `grounded`

```toml
[[assert.grounded]]
tag = "rag_chunks"           # presence: every rag_chunks tag must land in the window
warn_dead = true             # advisory: flag rag_chunks no downstream call consumed
```

- **presence** — for each element named `tag`: `element["matched"]` must be true, i.e.
  the tagged content actually landed in some call's window. This is the per-element
  match rate as an assertion (§14). Hard gate: the tag is the declaration, so the claim
  is exact.
- **dead-context** (`warn_dead = true`, default `false`) — an element that *did* land in
  a window but never influenced any downstream call. #14's review comment fixes the
  wording: "this segment appeared in N windows and never influenced any downstream
  call". Implementation: the element is dead when **no consuming call has an outgoing
  `output_text` edge** to a later call.
  - Only `output_text` edges count as influence. A `same_span` edge is structural
    adjacency (two calls in one span), not evidence that anything flowed.
  - **Terminal calls are excluded.** If every call that consumed the element is the last
    call in its session, there is no downstream to influence and "dead" is vacuous — a
    single-call RAG app would otherwise have all of its chunks flagged, which is exactly
    the noise that would discredit the signal.
  - When `edges_truncated` is set for the session, the inference hit its fan-out cap, so
    the dead signal is unreliable and the warning says so rather than asserting.
  - Advisory always: edges are inferred, so per §6 this can never be a gate.

## 4. TOML parsing: a new runtime dependency

`tomllib` is stdlib only on 3.11+; `requires-python` is `>=3.10`. Runtime deps are
deliberately minimal (`wrapt`, `click`, `tiktoken`) because low-maintenance is an
explicit design goal, so adding one is a real decision and not a default.

**Decision: `dependencies += ["tomli>=2; python_version<'3.11'"]`**, with
`tomllib if sys.version_info >= (3, 11) else tomli` at the import site.

- The environment marker means 3.11+ users install **nothing new** — the cost lands only
  on 3.10, and disappears entirely when 3.10 is eventually dropped.
- `tomli` is the reference implementation that *became* `tomllib` (same author, same
  API), so it is as close to a stdlib backport as a dependency gets — very low
  maintenance risk.
- Rejected: **dropping 3.10** (a breaking change in a minor release, to save one
  markered dep — not worth it) and **a non-TOML format** (§14 settled on TOML).

## 5. Module layout

```
src/ctxlineage/_contract/__init__.py     # public-ish entry: run(data, config) -> Report
src/ctxlineage/_contract/config.py       # load + validate ctxlineage.toml
src/ctxlineage/_contract/runner.py       # Finding/Report model, rule registry, dispatch
src/ctxlineage/_contract/rules.py        # window_budget + grounded (both are small)
```

Both rules read only what `build_report_data` already produces. **The pipeline is not
re-implemented and not modified**: `ctxlineage test` calls `normalize.load_events` +
`normalize.build_report_data`, exactly as `ctxlineage report` does. That is the premise
of §14's "the runner is expensive, the rules are cheap" — it only holds if the rules stay
pure readers.

`rules.py` stays one file while there are two rules; it splits when a third arrives.

## 6. CLI

```
ctxlineage test [--dir .ctxlineage] [--config ctxlineage.toml]
```

Grown next to `report` with `@main.command()`. `_cli.py` is the one file the parallel
WIDTH track (#57) also touches, so the edit is strictly additive: one import, one
command function, nothing in `report` or `main` reshaped.

Exit codes:

- **0** — no hard-gate failures (warnings and skips are fine, and are printed).
- **1** — at least one hard-gate failure, or the run could not be evaluated (missing
  events, missing/invalid config). Both are "not green" for CI; the message
  distinguishes them for the human. Matches how `report` already surfaces input errors
  (`ClickException` → 1).

Config validation is strict: unknown rule names and unknown keys are errors, not
ignored. A silently-ignored typo in a CI gate config is a gate that passes for the wrong
reason.

Output: one line per finding, `FAIL`/`WARN`/`SKIP` prefixed, then a summary line. No
`--json` in this slice (nothing consumes it yet).

## 7. Test plan (TDD, pytest)

`tests/test_contract_config.py`

- Parses §14's sketch shape; `[[assert.x]]` arrays with multiple entries.
- Rejects: unknown rule name, unknown key, missing `max_pct`/`tag`, out-of-range
  `max_pct`, wrong types, malformed TOML.

`tests/test_contract_rules.py` (synthetic report data, no capture needed)

- `window_budget`: over/under threshold; real `usage` preferred over estimate;
  segment-scoped sums only that kind; unknown `context_window` → skip not pass;
  selector matching nothing anywhere → warn not pass.
- `grounded` presence: matched element passes; unmatched element **fails hard**.
- `grounded` **tier demotion**: configured tag absent from the run → **warn, exit 0**
  (the §6 behaviour — the one test that protects the positioning).
- `grounded` dead-context: consumed-then-flowed → clean; consumed-but-no-`output_text`-
  edge on a non-terminal call → warn; terminal-only consumer → not flagged;
  `warn_dead = false` → never flagged; `edges_truncated` → warning states unreliability.

`tests/test_cli_test_command.py`

- Exit 0 when clean, **exit 1 on hard-gate failure** (the CI contract).
- Warn-only run exits 0.
- Missing events / missing config / invalid config → exit 1 with a readable message.
- End-to-end over the existing demo event generator, so the rules are exercised against
  real captured data and not only synthetic fixtures.

## 8. Docs

- README: a short `ctxlineage test` section (config sketch + the tier rule in one line).
- CHANGELOG: Unreleased → Added.
- §14 stays the design record; this plan records the implementation deviations
  (`history`, the `tomli` dep, the dead-context predicate).
