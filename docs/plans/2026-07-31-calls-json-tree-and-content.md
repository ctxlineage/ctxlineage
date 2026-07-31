# Implementation Plan: Structure-aware Calls-view rendering (#92)

> **Status:** implementation plan, PR 2 of the v0.2.2 issue batch (#88–#94).
> Stacked on PR 1 (#89/#90) since both touch `app.js`'s `renderCallDetail()`.
> Scope confirmed with the maintainer: include the JSON-tree renderer (not
> deferred to a fast-follow, despite it being the larger of the two fixes
> #92 asked for).

## 1. The two readability failures #92 names

- **A. Segment/output bodies are raw text with no structure.** A JSON payload
  renders as an undifferentiated wall of quotes and braces; the reader lands
  wherever the collapsed preview happens to clip, with no sense of shape.
- **B. The INSTRUCTIONS panel does not survive a real system prompt**, and
  the output body has no expand affordance at all — both true, but on
  inspection the underlying scroll mechanics already existed (`.instr.open
  .txt` was already `max-height: 300px; overflow-y: auto`); what was
  actually missing was **discoverability** — nothing on the page indicated
  either panel was clickable to expand.

## 2. JSON-tree renderer

Client-side only (`app.js`), no `normalize.py`/schema change: `parseJsonMaybe`
attempts `JSON.parse` once on a segment/output's raw content; only a parsed
object or array is treated as structured (a bare string/number/bool renders
as plain text — not worth a tree). `jsonTreeHtml` recurses into nested
`<details>`/`<summary>` elements — native disclosure widgets, so expand/
collapse needs no click-handler wiring of its own and comes with baseline
keyboard accessibility for free. Top-level keys are always visible; nested
branches start closed (`shape at a glance`, not a fully-exploded dump). A
string value that merely *looks* like JSON is never re-parsed — only the
segment's own top-level content is ever handed to `JSON.parse`.

The collapsed **preview** line also benefits: `object · 6 keys` instead of a
90-char clip of raw JSON text, answering "what is this?" before the reader
expands anything.

Applied to both segment bodies (`.seg .full`) and the output body (`#outwrap
.body`) — one shared render path, since both can carry structured content
(a function-calling app's response is commonly JSON too).

## 3. Discoverability: the toggle chevron

Added a small `▸`/`▾` (`<b class="toggle">`) to the INSTRUCTIONS panel's
label row and the output header, rotating via CSS on `.open`. The
INSTRUCTIONS panel's existing toggle mechanics are otherwise untouched — this
is purely the missing visual affordance. The output body **gained a new
toggle** (it previously had none): clicking now removes its `max-height` cap
entirely (`.out.open .body { max-height: none; overflow: visible }`) so long
output flows with the page instead of fighting an internal scrollbar.

**Scroll-reset on reopen:** a toggle only flips a CSS class — it does not
re-render — so a panel scrolled, closed, and reopened would otherwise still
show wherever the reader last left it. A shared `toggleOpen(el,
scrollSelector, event)` helper resets `scrollTop = 0` on open, applied
uniformly to segments, the instructions panel, and the new output toggle.

## 4. A real bug found during visual verification: nested-toggle bubbling

A click on a nested JSON `<details>`/`<summary>` bubbles up through the DOM
to the containing `.seg`/`#outwrap`'s own click-to-toggle listener —
expanding one JSON key was **also closing the whole segment/output panel
around it**. Fixed by having `toggleOpen` bail out when the click's target
is (or is inside) a `details`/`summary` element, letting the native
disclosure toggle be fully independent of the outer open/close toggle. Found
by driving the real rendered HTML in a browser (Playwright), not by the
unit-level DOM assertions alone — logged as
`test_expanding_a_nested_json_branch_does_not_close_the_segment`.

## 5. Verification

- New file `tests/browser/test_report_content.py`: tree rendering + key
  count, structural preview summary, non-JSON regression guard, output-tree
  + expand-toggle, instructions-panel toggle discoverability + rotation,
  scroll-reset-on-reopen, the nested-toggle-bubbling regression above, and a
  hostile-JSON-payload escaping test (the tree is a new render path with its
  own `esc()` call sites, not exercised by the existing hostile fixture
  since that payload is deliberately not valid JSON).
- Full suite **469 passed**, lint clean.
- Live Playwright pass (both themes): confirmed visually — collapsed preview
  summary, expanded tree with a nested branch opened, the instructions
  chevron rotating, the output panel's unbounded expand, and — the important
  negative check — that expanding a nested branch does **not** collapse the
  segment or output panel around it.

## 6. Adversarial review, pre-merge: one real crash bug found and fixed

- **Major — unbounded recursion in `jsonTreeHtml` crashed the whole Calls
  view on deeply-nested JSON.** `parseJsonMaybe` only guards against
  `JSON.parse` itself throwing; it can't predict that the subsequent pure-JS
  tree walk has no depth cap. A JSON value nested ~2000+ levels deep (a
  plausible shape for a hostile RAG chunk, or a legitimate deeply-nested
  tool trace) threw `Maximum call stack size exceeded` mid-template-literal
  construction inside `renderCallDetail()` — `main.innerHTML` never got
  reassigned, so `#main` was left showing the stale Overview HTML with no
  visible error, and the Calls tab was silently broken for that report
  load. Confirmed the crash reproduces by temporarily reverting the fix and
  re-running the new regression test: it timed out waiting for `.windowbar`
  to ever appear (30s), matching the reviewer's description exactly.
  Fixed by adding `JSON_TREE_MAX_DEPTH = 24` (far above any realistic
  payload's real nesting) — past that depth, a branch renders a `not
  expanded further` marker instead of recursing, so the walk always
  terminates. New regression test
  `test_a_deeply_nested_json_segment_does_not_crash_the_calls_view` pins
  this at depth 3000, and independently proves the fix is load-bearing (not
  just present) by reproducing the pre-fix crash locally before landing.
- **Minor, fixed** — `toggleOpen`'s nested-`<details>` guard was inert at
  the `#instr` call site (missing the `event` argument that `.seg`/
  `#outwrap` correctly pass). Not exploitable today since `.instr .txt` is
  never JSON-tree-rendered, but it was a landmine: the exact bug this PR
  fixed for segments/output would have silently reappeared the moment JSON
  rendering extends to the INSTRUCTIONS panel. Fixed by passing the event
  through at that call site too, matching the other two.
- **Nit, not fixed** — click-to-close behavior differs slightly depending
  on whether a JSON leaf row sits inside an already-expanded branch or not
  (both non-crashing, just inconsistent); left as-is, not worth the
  complexity for a cosmetic edge case.

Full suite after fixes: **470 passed**, lint clean.
