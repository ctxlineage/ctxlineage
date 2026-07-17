# Segments that don't cover the prompt — #63 + #64

> **For Claude:** one PR, closes #63 and #64. They are two views of one fact and
> must not be split — see "Why one change" below.

**Goal:** when a call's segments are only part of the prompt that was really
sent, neither the CI gate nor the report may present them as the whole.

---

## Why one change, not two

Both issues need the same quantity — the segments' share of the real prompt:

```
sum(call["segments"][*]["tokens_est"])   vs   call["usage"]["prompt_tokens"]
(= call["input_tokens_est"])                  (= the provider's own count)
```

#63 gates on it; #64 displays it. Split across two PRs, the second silently
breaks the first: if #64 renders the missing remainder by appending it to
`segments`, `input_tokens_est` jumps to ~100% of the prompt and #63's guard
stops firing — the false green returns. So: one definition, two read-only
consumers, and **the display remainder never enters `segments`**.

## The decision: a declaration, not a ratio

The obvious guard is "skip when est/reported is below some threshold". Rejected
after measuring:

- A ratio conflates two unrelated things — **estimator disagreement** (tiktoken
  vs the provider's real tokenizer, plus chat-format overhead the segments don't
  model) and **content that is structurally absent** (an import).
- Calibrating a threshold needs real captured data across providers. The demo
  generator's `usage` is fabricated (`prompt_tokens=` is passed in literally),
  so it cannot calibrate anything — measuring it gives 26%–88% "coverage" purely
  as an artifact. Any threshold picked from that would be a guess that either
  lets imports through or starts skipping legitimate live captures.
- The producer already **knows**. `_import/claude_code.py` declares exactly what
  it could not recover in `payload["import"]["not_preserved"]`. That is exact
  evidence, and §6 says gate on exact evidence and never on inference.

So `normalize` derives `segments_complete` from the declaration, not from
arithmetic. Live capture has no `import` key → complete (its numbers are
estimated, but nothing is *missing*, which is the distinction that matters).

Keyed on *what* is missing, not on *being an import*: only `system_prompt`,
`tool_definitions` and `reasoning_text` are prompt-bearing. A transcript that
lost only `duration_ms` / `stream_flag` still supports a segment budget.

## Changes

1. **`normalize`** — `_segments_complete(payload)`; each call gains
   `segments_complete: bool` and an `import` passthrough. `input_tokens_est`
   keeps its existing meaning (segments only) — the invariant #63 rests on.
2. **`_contract/rules.py` (#63)** — `window_budget(segment=)` emits `SKIP`
   (naming what is missing) instead of evaluating an incomplete call. The
   whole-prompt form is untouched: it reads reported `usage`, so it stays exact
   and keeps gating imports.
3. **`_contract/rules.py` (bonus)** — the "segment never appeared, check the
   name" warning now requires that some call was actually evaluated. Previously,
   when every call skipped (e.g. all models had unknown windows), it told the
   user to go hunt a typo that wasn't there.
4. **`_cli.py`** — a run whose findings are all skips no longer summarises as
   "All N assertions passed". Skips don't gate, but calling an unevaluated run
   "passed" is the same lie `SKIP` exists to prevent, moved from the rule to the
   summary line.
5. **`_report/assets` (#64)** — the anatomy proportions against the *whole*
   prompt and renders the unaccounted remainder explicitly (hatched, `--muted`,
   both themes), plus a provenance panel naming the source and what was not
   preserved. Computed at render time from `inTok - segTotal`, so `segments`
   stays clean (see "Why one change").
6. **`ctxlineage_mcp`** — `get_call` docstring tells an agent to check
   `segments_complete`, so it doesn't reason about "what filled the window" from
   partial segments.

## What the report showed before this

The bug was worse than "an unexplained gap". `total` was the sum of segments
(8 tok) while the bar was labelled with the reported prompt (33,631 tok), so
**8 tokens were stretched across the full width** and a 4-token segment rendered
as "50% of input". The segments actively claimed to account for the whole
prompt. Now: `user input 8 tok · 0%` + `not preserved by the transcript
33,623 tok · 100%`.

## Verification

Tests cover normalize's flag (live / import-missing-prompt / import-missing-only
-metadata) and the rules (skip, no-regression on whole-prompt, no-regression on
live capture, imports that did preserve the prompt, and the warning fix). Each
new guard was confirmed to **fail with the fix reverted** — a test that only
executes lines proves nothing.

The report side is client-side JS with no JS test runner in this repo, so it was
verified in a real browser: the rendered anatomy, and `getComputedStyle` in both
light and dark (the hatch and panel resolve per theme). The report stays
self-contained (0 external refs).
