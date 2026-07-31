# Implementation Plan: `segment_diff` contract rule (#94, slice 2 of 2)

> **Status:** implementation plan, PR 6 (last) of the v0.2.2 issue batch
> (#88–#94). Stacked on PR 5 (`feat/requires-segment-rule`) — both touch
> `_contract/rules.py` and `_contract/config.py`, and this rule reuses PR 5's
> `_incomplete_reason()` extraction directly, so building on its tip avoids a
> guaranteed rebase conflict and a duplicated helper.

## 1. Why this rule

The vision doc (`docs/vision/context-contract-testing.md` §8) names four
assertion classes; `window_budget`/`grounded`/`requires_segment` cover
structural and provenance. **Regression/differential** — "record a golden
run, re-run in CI, diff at the **segment** level" — is explicitly called out
as "the natural first deliverable" (§8) and is still unimplemented going into
this slice. `segment_diff` closes it: compare this run's segment token
counts against a previously-recorded baseline run, call for call, and fail
when a segment grew past a tolerance. Fully deterministic offline over two
recorded JSONLs — no live variance to paper over.

## 2. Design

```toml
[[assert.segment_diff]]
baseline = "baselines/golden.jsonl"   # resolved relative to this TOML file
max_token_delta = 200                  # segment may grow by at most this many tokens
segment = "tool_defs"                  # optional; omitted = whole prompt
```

- `SegmentDiff` (`@dataclass(frozen=True)`): `baseline_data: dict`,
  `max_token_delta: float`, `segment: str | None = None`. `baseline_data` is
  the *already-normalized* `build_report_data()` output of the baseline
  JSONL — loaded once, at config-parse time, not on every `check()` call.
  `runner.py` stays untouched: `check(data)` still takes exactly one dict,
  matching every other rule; the baseline is a field on the rule instance,
  not a second runner parameter. This is the design decision confirmed with
  the user before starting this batch (over the alternative of `runner.py`
  taking an optional second baseline argument) — it keeps the runner's
  "rules are pure readers, plumbing stays thin" property (§14 of the vision
  doc) intact for every rule, this one included.

### Baseline loading: the one place this slice changes precedent

No existing rule takes a filesystem path — `_parse_segment_diff` is the
first, so path resolution is a **deliberate, explicit convention**, not an
accident: `baseline = "..."` resolves **relative to the TOML file's own
directory**, not the process's CWD. `config.parse()` gained a `base_dir`
parameter for this (only `_parse_segment_diff` reads it — `parse()`
special-cases the one rule that needs it rather than threading an unused
parameter through every other parser, which stays a 2-arg `(entry, where)`
function). `config.load()` passes `path.resolve().parent`.

The baseline file is read with the exact same two ordinary functions the
CLI itself uses (`normalize.load_events` + `normalize.build_report_data`) —
no hidden second code path for "the other run."

## 3. Call pairing across runs — the genuinely new piece

No existing cross-run identity concept exists in this codebase (a call's
`id` is only unique *within* one recorded run). Pairing is positional, in
two nested passes, both already deterministic in `build_report_data`'s own
output ordering:

1. **Sessions pair by position** — Nth session vs Nth session, both already
   sorted by `started_at`. `zip()` over the two session lists; if the counts
   differ, the extra sessions on the longer side are simply not compared
   (no natural identity to name them by — a session gaining/losing entirely
   is a baseline-maintenance question, not a per-run signal this rule
   should generate noise about).
2. **Calls within a paired session pair by `step`** (the span name) —
   grouped in order of appearance (`_group_by_step`), then the Kth
   occurrence of a given step in this run pairs with the Kth occurrence of
   the same step in the baseline. A call with no span (`step is None`)
   groups under the key `None` the same way — which reduces exactly to
   ordinal position among the other unnamed calls, so no special case is
   needed for it.
3. A step present on only one side (count mismatch, or the step name simply
   doesn't exist on the other side) is a **pairing gap**, not a content
   regression — reported as **WARN**, not FAIL, mirroring `Grounded`'s
   posture on absence (an unmatched tag doesn't fail the run either).

## 4. Diff computation and the incompleteness guard

- `_tokens(call)`: `sum(tokens_est for segments where kind == self.segment)`
  when `segment` is set, else `call["input_tokens_est"]` (already the sum
  over all segments — same number `window_budget`'s whole-prompt case
  reads, restated here for clarity since this rule always compares at
  segment granularity, never the provider's own `usage` total).
- `delta = current_tokens - baseline_tokens`; **FAIL only when `delta >
  max_token_delta`** — shrinkage is never flagged (trimming is not a
  regression this rule exists to catch; a segment disappearing entirely
  shows up as a 0-vs-N delta, already caught by the same growth check
  reversed... no: shrinking to zero is a *negative* delta, so it does NOT
  fail under this rule by design — content loss is `grounded`'s and
  `requires_segment`'s job, not this one's. `segment_diff` is a budget in
  one direction, matching the plan's own scoping.).
- Reuses PR 5's `_incomplete_reason()` **on both sides of a pairing**: if
  either the current call or its paired baseline call has
  `segments_complete=False` (an imported call on either side), the
  comparison is **SKIP**, not evaluated — a baseline captured via import
  makes the diff as meaningless as `window_budget` already treats it,
  and for the identical reason (a fraction cannot stand in for the whole).
  Current-side incompleteness is named first if both sides are incomplete
  (arbitrary but deterministic priority — the message still names the
  right call either way, only the reason ordering is fixed).

## 5. What's deliberately out of scope for this slice

- No cross-run call identity beyond position + step name (e.g. no content
  hashing, no fuzzy matching). If a pipeline's call sequence shape changes
  between baseline and current runs, pairing degrades gracefully to WARNs,
  not a crash — which is the honest signal ("the baseline no longer
  describes this pipeline's shape," a maintenance prompt, not silence).
- No CLI affordance to *record* a baseline (e.g. `ctxlineage test --update-baseline`).
  The baseline is just a JSONL — any prior `.ctxlineage/events.jsonl`,
  committed once, already works as one. A recording helper is a
  fast-follow if adoption shows it's needed.

## 6. Verification

- `tests/test_contract_rules.py`: identical baseline/current → no findings;
  a segment that grew beyond `max_token_delta` → FAIL with the delta
  stated; a segment that shrank → no FAIL; a step present only in current
  or only in baseline → WARN, not FAIL; either side's call incomplete →
  SKIP; `segment=None` diffs the whole prompt.
- `tests/test_contract_config.py`: `baseline` path resolution relative to
  the TOML file's own directory (not CWD) — pinned explicitly, since
  nothing established this convention before; `baseline` and
  `max_token_delta` both required; unknown baseline path is a `ConfigError`
  naming the resolved path.
- Full suite + `ruff check`/`ruff format --check` before opening the PR.
- `docs/vision/context-contract-testing.md` §8 gets a one-line note marking
  regression/differential as shipped, since this was the doc's own
  "natural first deliverable" call-out.

## 7. Adversarial review, pre-merge: two real issues found and fixed

Given this PR's novel cross-run pairing logic (no prior art elsewhere in
the codebase), it got a dedicated adversarial review after the PR opened.
It found one real bug and one real gap, both fixed (full suite now
**488 passed**, lint clean):

- **Bug — the baseline-orphan WARN cited the wrong session.**
  `_check_session`'s second loop (baseline calls with no current-side
  counterpart) built its message with `_locate(session, orphan)` — pairing
  the *current* session's id with a *baseline* call's id. Session and call
  ids are independent per-run UUIDs, so the resulting message named a call
  that provably does not exist under that session anywhere in the current
  run. Every existing fixture hardcoded `session="s1"` on both sides, which
  made the bug structurally invisible to the test suite. Fixed to
  `_locate(baseline_session, orphan)`; added
  `test_segment_diff_baseline_orphan_names_the_baseline_session_not_current`
  with deliberately distinct session ids on each side to pin it.
- **Gap — no typo/never-matched protection, unlike `window_budget`.**
  `WindowBudget` warns when a configured `segment` never appears in any
  evaluated call, guarding against a typo reading as a false pass.
  `SegmentDiff` had no analog: a misspelled `segment` sums to `0` on both
  sides for every pair forever, so `delta` is always `0` and the rule
  silently, permanently passes — indistinguishable from "genuinely never
  grew." Added the same guard: `check()` now tracks whether any pair was
  actually evaluated (not skipped) and, if `segment` is set and evaluated
  but never once appeared in either run's data, emits a WARN naming the
  configured value. Mirrors `WindowBudget`'s own `evaluated_any`/
  `segment_seen` pattern. Two new tests:
  `test_segment_diff_warns_when_the_segment_never_appears_on_either_side`
  and `test_segment_diff_does_not_claim_the_segment_is_missing_when_nothing_was_evaluated`
  (the latter mirrors `WindowBudget`'s own
  `test_unknown_window_does_not_claim_the_segment_is_missing` — don't warn
  about a typo when absence was never established because everything was
  skipped).
- **Documented, not code-fixed — positional session pairing's silent
  failure mode.** If session *counts* match but *identities* differ (a new
  session type inserted ahead of an old one, same-second sessions
  reordering under timestamp jitter), the diff compares unrelated sessions
  with **no warning at all** — worse than the count-mismatch and per-call
  pairing-gap cases, which do surface. There's no cross-run session
  identity to detect this against without inventing one (out of scope for
  this slice), so it's called out explicitly in the `SegmentDiff` docstring
  and the README instead of silently relying on the reader to infer it from
  "positional pairing."
