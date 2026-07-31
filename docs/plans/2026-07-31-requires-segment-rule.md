# Implementation Plan: `requires_segment` contract rule (#94, slice 1 of 2)

> **Status:** implementation plan, PR 5 of the v0.2.2 issue batch (#88–#94).
> Independent of PRs 1–4 (frontend-only) — branched from `main`, no file
> overlap with `_report/assets/`.

## 1. Why this rule, first

#94 found the second-slice gap: of the vision doc's four assertion classes
(§8), only *structural* (partially, via `window_budget`'s per-segment caps)
and *provenance* (partially, via `grounded`) exist. `requires_segment` closes
the other half of the *structural* bullet — not **how much** is in the
window, but **whether the right thing is there at all**. Same tier as
`window_budget`: deterministic from capture alone, no tagging needed, hard
gate on any run.

## 2. Design

```toml
[[assert.requires_segment]]
kind = "system"              # every call must carry a system segment
[[assert.requires_segment]]
kind = "tool_defs"
when_model = "gpt-*"         # optional glob, scopes the rule to matching models
```

- **Absence is never demoted to a warning** — the one deliberate asymmetry
  with `window_budget`. `window_budget` warns on a segment name that never
  appeared anywhere in the run, because there the ambiguity is real (a typo
  vs. a segment kind that legitimately never occurs). Here the rule's whole
  point is that absence *is* the failure — softening it would defeat the
  rule. A typo'd `kind` fails every call loudly, which is the correct
  signal to send back.
- **`when_model` scoping — two distinct outcomes, not one.** A call whose
  model doesn't match the glob is **out of scope**, not a gap — no finding
  at all (matching how `window_budget(segment=)` silently contributes zero
  for a call that doesn't carry that segment kind). A call whose `model` is
  `None` is a genuine gap — it can't be matched against `when_model` either
  way — so that gets an explicit `SKIP`.
- **Incomplete-segments calls (imports) skip, not fail** — the same
  reasoning `window_budget`'s segment form already uses (#63): an absent
  segment on a call whose segments are declared partial is ambiguous
  between "never sent" and "not preserved by the transcript," and treating
  that ambiguity as a hard failure would punish the transcript's honesty
  about what it could not recover.

## 3. Shared helper, extracted without changing `WindowBudget`

`WindowBudget._missing` was already a fully generic function of `call` (no
dependency on `self`) — moved to module level as `_incomplete_reason`,
`WindowBudget`'s own call site repointed to it, its own `_missing` method
deleted. This is a pure move, not a behaviour change — confirmed by running
the existing `test_contract_rules.py` suite unchanged before writing a
single new test; all 31 pre-existing tests passed with identical messages.

## 4. Glob matching

No existing glob helper in the codebase — `fnmatch.fnmatch` (stdlib, no new
dependency), matching the model string directly (case-sensitive; model IDs
are lowercase-canonical, e.g. `gpt-4o-mini`, and a case-insensitive match
would let `"GPT-*"` silently match nothing on a real model string, which is
worse than requiring exact case).

## 5. Config wiring

`_parse_requires_segment` clones `_parse_window_budget`'s shape exactly —
`kind` required (via the existing `_string` validator), `when_model`
optional. Registered in `_PARSERS`; no runner.py change (a rule's `check(data)`
signature is untouched).

## 6. Verification

- `tests/test_contract_rules.py`: presence passes; absence hard-fails;
  incomplete-segments imports skip (not fail); `when_model` scoping (match
  vs. silent exclusion vs. unknown-model skip) as three separate tests, not
  one, since they are three different outcomes.
- `tests/test_contract_config.py`: loads with and without `when_model`,
  required-key and unknown-key rejection, empty-string rejection — mirroring
  the existing `window_budget`/`grounded` test shapes exactly.
- End-to-end CLI smoke test against the demo generator's real output
  (`ctxlineage test`): a real absence correctly fails only the calls that
  lack the segment; a bogus kind fails every call (proves the "typo fails
  loudly" design goal); `when_model` scoping a model that doesn't appear in
  the run at all correctly excludes everything rather than failing or
  warning.
- Full suite passed (backend-only change; frontend/browser suite
  unaffected), lint clean.
