# M3 Lineage Graph — design session record (2026-07-16)

Interactive mockup + the six decisions made with the maintainer during the
phase-D design session. This is the spec for the phase-E implementation
(the report's fourth view).

## Decisions

1. **Structure A — three columns + right lanes.** SOURCES (provenance:
   `qdrant:docs_v1`, prompt files, memory stores) → CONTEXT ELEMENTS (tags;
   dashed when unmatched) → LLM CALLS (time-ordered ↓, fn pills). Outputs stay
   embedded in call nodes; output→later-input flows route through subway lanes
   on the right. (Full dbt-style output nodes rejected for density; revisit if
   transforms land in v1.5.)
2. **same_span = bracket + span name** to the left of the calls column — same
   visual language as the chain view's loop box, no extra edge lines.
3. **Scope: one session per graph** (sidebar switches). Whole-log graphs and
   cross-session edges are out until the data has them.
4. **Element tokens: text + mini bar.** Element nodes show consumed tokens
   (`84 tok · top_k(2)`) plus a small bar proportional to the session max —
   requires the backend to aggregate per-element token sums (phase E).
5. **Untagged sessions: honest empty state.** Calls + flows still render; the
   elements column shows a hint pointing at `span()`/`tag()`. No pseudo-role
   elements — we do not fabricate provenance.
6. **Label split:** fn pill = innermost `call_stack` function, bracket = span
   name; the pill falls back to the span name when the call stack is missing
   or useless. Applied to `stepOf()` globally (all views) so a call never
   changes names between tabs.

Interaction: clicking any node dims everything outside its lineage closure
(upstream + downstream over provenance/feeds/flows edges) — the impact-analysis
primitive from PLAN.md §4(b).

## Files

- `lineage-graph.template.html` — the session's final mockup (vanilla JS,
  `__DATA__` placeholder takes report JSON).
- `build.py` — injects fresh demo data for viewing.

> Historical record: the template captures the agreed design; the shipped view
> lives in `src/ctxlineage/_report/assets/` and is the source of truth.
