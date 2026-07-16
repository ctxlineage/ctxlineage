# M3 Phase E — Lineage Graph View

> **For Claude:** TDD, one PR. Tracking issue: #3 (final phase). Spec:
> `docs/design/m3-lineage-graph/` (six decisions from the phase-D session).
> Also part of #30 (the usage-vocabulary item only) — same backend touch;
> #30 stays open for anthropic output shapes and content-block decomposition.

**Goal:** the report's fourth view (Overview | Calls | Chain | **Graph**):
sources → elements → calls with lineage-closure highlighting, per the agreed
mockup, rendered like every other view (vanilla JS over the embedded JSON).

**Note on PLAN §6 wording:** the amended plan mentions a server-side SVG layout
engine. M3's graph has fixed ranks (source/element/call) and session-sized
node counts, so the layout is computed client-side like the rest of the report;
the Python engine remains the documented option if graph scale ever demands it
(PLAN.md gets a one-line note).

## Backend (normalize.py)

1. **Element token aggregation (decision 4):** `elements[].tokens_est` = sum of
   `tokens_est` over tagged segments with that element's kind across the
   session's calls (matching span). Tests: tagged demo element sums; zero for
   unmatched.
2. **Usage canonicalization (#30):** provider-agnostic usage — when `usage` has
   `input_tokens`/`output_tokens` (anthropic vocabulary) and no
   `prompt_tokens`, merge in `prompt_tokens`/`completion_tokens`/`total_tokens`
   (computed when absent). Original keys pass through. Tests with
   anthropic-shaped payloads.

## Frontend (assets)

3. **stepOf split (decision 6):** prefer the innermost `call_stack` function,
   fall back to `c.step` (span name). Calls-view fn card gains a `span` row
   when a span name exists and differs from the pill label.
4. **Graph view:** port the mockup — buildGraph (elements/calls/edges from the
   session contract, output_text deduped by pair), 3-column layout with
   collision-resolved y placement, span brackets, element mini bars, subway
   lanes, lineage-closure dim on click, untagged empty-state hint. New tab in
   the template; nav = session list (shared with chain behavior, filter-aware).
5. Template test: `data-view="graph"` present; existing tests untouched.

## Verification

Rebuild demo → browser check: synthetic-free (real demo data), tagged session
shows sources/elements/brackets/bars; rag session shows empty-state + flows;
click-to-trace works; both themes. Then PR (feature — maintainer merges).
