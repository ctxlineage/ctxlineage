# Implementation Plan: Chain lineage points at what flowed (#93)

> **Status:** implementation plan, PR 4 of the v0.2.2 issue batch (#88–#94) —
> the riskiest and most novel change in the batch (the product's headline
> lineage claim), stacked on PR 3. Gets an adversarial review before merge
> in addition to self-review, per the batch plan.

## 1. The gap

Chain draws an arrow from call N's output to call N+1's input, but the arrow
always terminates on the whole input bar / the aggregated assistant ("fed")
chip — never on the specific part of the input the match actually landed in.
The footer already says edges are inferred from a text match, meaning the
matched span is known at render time; that information was computed and
then discarded before drawing.

## 2. Scope locked (proposals 1+2 of the issue; 3+4 deferred)

- **Terminate the edge on the segment it landed in**, not always the
  aggregated assistant chip.
- **Label the edge** with a token count + a snippet of what flowed.

Deferred (issue's own proposals 3 "carried-fraction overlay" and 4
"click-to-diff highlight"): both are enhancements on an already-correct
core; proposals 1+2 alone directly answer the issue's stated complaint
("what part of the output became part of the next input"). Full
per-segment DOM targeting (today's chips are aggregated by kind, not one
node per raw segment) is a larger rendering change, filed as fast-follow.

## 3. `normalize.py`: `to_segment`, added without changing what matches

`_session_edges` searched a **joined** haystack per later call and recorded
only `{from, to, kind}`. The match *condition* is unchanged — still against
the joined string, so this carries zero risk of finding fewer/more edges
than before. `_matching_segment` runs **only after** a match is already
confirmed, to *localize* it: it checks each of the destination's segments
individually and returns the first one containing the whole matched text.

A match can in principle straddle a segment boundary (the join has no
separator) — no single segment to blame, so `to_segment` is simply absent
from the edge in that case; the edge itself is still recorded exactly as
before. This graceful-degradation path is deliberately not synthesized
around — see `test_to_segment_absent_when_the_match_spans_a_segment_boundary`.

## 4. `app.js`: kind-aware targeting + a label

- Each aggregated chip in `chainNodeHtml` gained a `data-kind` attribute
  (its underlying segment kind — a `tool` chip's name varies, its kind
  doesn't). `drawEdges` looks up the raw edge (which carries `to_segment`)
  by `(from id, to id)`, resolves the destination segment's `kind`, and
  queries `.chips .chip[data-kind="..."]` — falling back to the old
  `.chip.fed` → whole-`.chips` chain when there's no `to_segment` (the
  boundary-spanning case, or a `same_span` edge, which never had one).
- The label: a visible `<text class="edgelabel">` (token count — the
  source call's own `usage.completion_tokens`, already on the call object,
  no new field needed) plus a `<title>` (hover tooltip carrying the token
  count + a clipped snippet of the matched text). `svg#edges` is
  `pointer-events: none` for click-through to the chips beneath it; only
  `.edgelabel` re-enables pointer events, so the tooltip works without the
  rest of the overlay blocking clicks on the underlying nodes.

## 5. Two real things found only by driving the rendered page

- **A pre-existing test-writing trap**: `document.querySelector('svg#edges
  path')` matches the `<defs>` block's arrowhead-marker shapes before the
  actual edge path, because `<defs>` renders first in DOM order. Not an
  app bug — a test-script trap that would silently validate the wrong
  element. Scoped queries to `svg#edges g path` (edges are now wrapped in
  their own `<g>` alongside the label; the marker defs are not).
- **`Element.innerText` is unreliable on SVG `<text>`** across engines (it's
  CSS-layout-dependent; SVG doesn't participate in the same box model).
  `all_inner_texts()` intermittently returned `None` for the new label
  elements. Switched to `textContent` (`all_text_contents()` in Playwright),
  which works uniformly. Both were caught by actually running the assertions
  against a real browser, not by reasoning about the DOM structure alone.

## 6. Verification

- `tests/test_normalize.py`: `to_segment` on the two existing edge-inference
  tests (updated for the new field); a new test proving the index is the
  *real* one, not coincidentally 0 (a system segment ahead of the match);
  a tool-kind destination; the boundary-spanning graceful-degradation case.
- `tests/browser/test_report_views.py`: extended the existing Chain-edges
  test with the visible label + tooltip.
- New `tests/browser/test_report_lineage.py`: a purpose-built fixture where
  a destination call has **both** an assistant chip and a tool chip, so the
  test can prove *which* one the arrow's actual rendered endpoint is closer
  to — not just that a chip of the right kind exists somewhere.
- Full suite **486 passed**, lint clean. Live Playwright pass (both light
  and dark, both a real multi-turn RAG session and the purpose-built
  tool-kind fixture) — confirmed visually: labels render correctly on both
  adjacent and long-hop edges, click-to-trace fan-out still works and
  labels the fanned-out edges independently, and the arrow visibly
  terminates on the tool chip rather than the assistant chip when that is
  where the match landed.

## 7. Adversarial review, pre-merge: two real bugs found and fixed

Per this plan's own §0 note, PR 4 got a dedicated adversarial Agent review
before merge given its risk. It found two genuine issues, both fixed and
covered by new regression tests (full suite now **489 passed**, lint clean):

- **BLOCKER — CSS-selector-string injection crashes `drawEdges()`.** A tag
  name is arbitrary user text (`Span.tag()` has no validation; `apply_tags`
  sets a segment's `kind` to it verbatim) — it is not limited to the
  built-in kind vocabulary (`system`/`user`/`assistant`/`tool`/`tool_defs`).
  §4's `.chips .chip[data-kind="${esc(toSeg.kind)}"]` interpolated that
  value into `querySelector`'s CSS-selector syntax. `esc()` only
  HTML-entity-escapes (`&<>"`); it does nothing for CSS-selector escaping,
  which has entirely different (backslash-based) rules and flatly rejects
  raw control characters. A tag name containing `\n`, `\r`, or `\x0c` threw
  a `SyntaxError` inside the single `forEach` that builds the whole edges
  string before one `innerHTML` assignment — one hostile tag name blanked
  *every* edge in the render, not just its own.
  Fix: stopped interpolating into CSS-selector syntax at all. Chips now
  carry `data-kind` as before, but the destination chip is found via a
  plain array search comparing `.dataset.kind` by JS string equality:
  `Array.from(node.querySelectorAll(".chips .chip")).find(el =>
  el.dataset.kind === kind)`. This is correct for *any* string, not just a
  denylist of dangerous characters — verified live (Playwright) against tag
  names containing `\n`, `\r`, `\x0c`, `"`, and `\`, all rendering exactly
  one correctly-targeted edge with no console error, plus a new pinned
  regression test (`test_a_tag_name_with_a_newline_does_not_crash_the_edges`,
  `tests/browser/test_report_lineage.py`).
- **MAJOR — `_matching_segment` could misattribute a match to the wrong
  segment when text is duplicated.** The original per-segment independent
  `in` check tested each segment in isolation, so if the destination's
  segment 0 happened to also contain a copy of the matched text (coincidence
  or a boundary-spanning artifact — segments are joined with no separator),
  it could report segment 0 even when the *real* semantic source was a later
  segment. A first-draft fix (single `haystack.find()` + cumulative-offset
  mapping) was checked against the reviewer's own scenario and found to
  return the same answer as the old code either way (duplicate text is
  genuinely ambiguous — there is no textual way to tell which occurrence is
  "the real" one, so segment 0 is the only defensible deterministic answer)
  — but that draft introduced a *new* regression: a boundary-spanning
  artifact earlier in the haystack would make `.find()` give up with `None`
  entirely, even when a later, clean, unambiguous occurrence existed.
  Final fix: `_matching_segment` walks *all* occurrences of the matched text
  in the haystack (`haystack.find(output, offset + 1)` in a loop), skips any
  that straddle a segment boundary, and returns the first that fits cleanly
  within one segment — falling back to `None` only if every occurrence
  straddles a boundary. Two new tests in `tests/test_normalize.py` pin both
  behaviors: a boundary-artifact-then-clean-occurrence case now correctly
  resolves to the later segment, and a pure-duplicate-text case
  deterministically (and, by design, unavoidably) resolves to the first
  occurrence.
