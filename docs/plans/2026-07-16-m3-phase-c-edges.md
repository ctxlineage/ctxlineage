# M3 Phase C — Lineage Edges in the Backend

> **For Claude:** TDD, one PR. Tracking issue: #3, phase C of five. PLAN.md §4(b)
> edge inference: (i) explicit tag source/transform, (ii) output→later-input text
> match, (iii) same-span relationships.

**Goal:** edges stop being a client-side visualization trick and become report
data — one inference, consumed by the Chain view now, the Lineage Graph (phase E)
and the MCP server (M4) later.

## Data contract additions (additive; report_version stays 1)

Per session:

```json
"edges": [
  {"from": "call-a", "to": "call-b", "kind": "output_text"},
  {"from": "call-a", "to": "call-b", "kind": "same_span"}
],
"elements": [
  {"name": "rag_chunks", "span_id": "sp1", "span_name": "answer_query",
   "source": "qdrant:docs_v1", "transform": "top_k(2)", "matched": true,
   "calls": ["call-x"]}
]
```

- `output_text`: call i's output content (≥15 chars) appears inside a later call
  j's segment content, same session. Mirrors the current JS inference exactly.
- `same_span`: consecutive calls sharing a non-null span_id — the explicit
  chain signal tags give us for free.
- `elements`: one entry per tag event (session comes from the tag's own
  session_id): provenance (`source`/`transform`), whether it matched, and which
  calls consumed it — the node list phase E's graph draws.

## Changes

1. `normalize.py`: `_session_edges(calls)` + element assembly inside
   `build_report_data` (matching already tells us tag→call attribution; extend
   `apply_tags`/call path to record per-call matched tag names — currently the
   matched set is aggregated then discarded per call).
2. Frontend `app.js`: `findEdges(s)` now reads `s.edges` (kind `output_text`)
   and maps call ids → indices; `findLoops` unchanged semantics on top. No
   visual change — the Chain view must render byte-identically for the demo.
3. Tests: edges present/absent (short outputs), same-span edges, elements list
   with matched/calls, demo end-to-end (rag session has output_text edges;
   tagged session has elements + same_span), template smoke unchanged.
