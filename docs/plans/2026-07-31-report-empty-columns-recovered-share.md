# Implementation Plan: Graph empty-column collapse (#89) + segment share-of-recovered (#90)

> **Status:** implementation plan, PR 1 of the v0.2.2 issue batch (#88–#94), all from
> a real company trial of v0.2.1. See the parent context: these two issues share no
> data-model change and both touch only `src/ctxlineage/_report/assets/app.js`
> (plus one line in `normalize.py` for #90's reasoning-marker cleanup), so they ship
> together as the smallest, lowest-risk PR in the batch.

## 1. #89 — Graph view renders a mostly-blank screen when a session has no tags

**Root cause:** `renderGraphView()` hardcodes 3 column x-offsets (`COLX =
{source, element, call}`). Sources are always derived FROM elements
(`elemSources`/`buildGraph`), so zero elements implies zero sources too — the
SOURCES/CONTEXT ELEMENTS columns render as headers over blank space with all
call nodes crushed into the fixed-position CALLS column at x=560.

**Correction from the issue's own follow-up trial:** this is not import-specific.
A native, untagged capture (no `span()`/`tag()` calls) produces the identical
empty-column layout — it's the default first-run experience for every v0.2.x
user, since nobody has adopted the tag API on day one.

**Fix:** compute `hasElements = (s.elements || []).length > 0` once; collapse
`COLX` to a single left-aligned column when false (headers for the absent
columns are conditionally omitted, not just visually empty). The banner
("wrap calls in span()/tag()") is reworded for the general untagged case, with
an import-specific variant when every call in the session is imported (tagging
is structurally impossible there — the agent process can't call `span()`).

**Deliberately deferred** (per the issue's own scoping): edge-density handling
for large sessions (thousands of overlapping `output_text` edges). Filed as a
fast-follow once this lands.

## 2. #90 — Every visible segment reads 0% on an imported call

**Root cause:** #64 (v0.2.0) correctly re-based segment shares against the real
prompt (`total = segTotal + unaccounted`) rather than against the segments'
own sum — otherwise a 4-token segment of a 33k prompt read "50% of input".
Correct, but at very low recovery (~0.1%), every real segment now rounds to
0%, and the reader has no number to act on.

**Fix:** add a second, explicitly-labelled share — `X tok · Y% of prompt · Z%
of recovered` — computed against `segTotal` (already available, already
computed) instead of `total`. Shown **only** when `total !== segTotal` (an
imported/incomplete-segments call), so live capture renders unchanged. This
keeps #64's real-prompt basis as the honest top line; the second number is
scoped by its own label so it can't be misread as a share of the whole
prompt (the exact failure mode #64 fixed).

**Also included** (issue's proposal 4, small and independent): the
`[thinking: 0 chars not shown]` placeholder — which `_part_text` in
normalize.py prints for every stripped reasoning block, regardless of
whether the transcript actually preserved any text — is suppressed when the
block is genuinely empty (the import case). A block with real, policy-hidden
content (native capture with extended thinking) keeps its marker; the
stripped-count is already surfaced once, in the provenance panel's
`reasoning_blocks_stripped`, so a per-occurrence empty placeholder repeats
information that conveys nothing new.

**Not included:** a second visual bar (issue's proposal 2) — the dual text
label already answers the question without a new widget.

## 3. Verification

- `tests/test_normalize.py::test_empty_thinking_block_leaves_no_placeholder` —
  pins the suppression; the existing
  `test_anthropic_thinking_blocks_visibly_marked` (non-empty case) is
  unaffected.
- `tests/browser/test_report_views.py` — three new tests: empty-column
  collapse on an untagged live session, three-column regression guard on the
  tagged demo session, and the import-specific banner wording.
- `tests/browser/test_report_anatomy.py` — extended the existing #64
  contract tests (`prompt_share_text` helper) for the new " of prompt"/" of
  recovered" wording. Two pre-existing assertions hardcoded a fixture-derived
  "59%" substring that went stale because of this same PR's thinking-marker
  fix (a replayed assistant turn's stripped-thinking placeholder shrinks that
  segment's `tokens_est` on the `session_tool_loop.jsonl` fixture) —
  rewritten as data-derived invariants (buggy-formula inflation vs. honest,
  order-of-magnitude apart) so they can't go stale again for unrelated
  reasons.
- Live Playwright pass (both themes, live + imported demo reports): confirmed
  visually — empty-column collapse, 3-column regression guard, import banner
  wording, dual percentage on an imported call, its absence on a live call,
  and the suppressed thinking marker in a real rendered output body.
