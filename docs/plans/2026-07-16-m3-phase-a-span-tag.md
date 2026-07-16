# M3 Phase A — span/tag Capture API

> **For Claude:** TDD, one PR. Tracking issue: #3 (phase A of five: A capture API →
> B segment matching → C lineage edges in normalize → D graph design session (gate) →
> E Lineage Graph view).

**Goal:** the explicit API from PLAN.md §4(a):

```python
with ctxlineage.span("answer_user_query") as span:
    span.tag("rag_chunks", docs, source="qdrant:products_v2", transform="top_k")
    resp = client.chat.completions.create(...)
```

emits `span_start` / `tag` / `span_end` events, and every `llm_call` recorded inside
the span carries its `span_id`.

**Design:**
- `src/ctxlineage/_span.py`: `Span` (context manager + `.tag()`), current-span tracking
  via **`contextvars`** so async tasks and threads are isolated. Public surface stays
  minimal: `ctxlineage.span(name)` re-exported in `__init__` — nothing else.
- `tag` payload: `{name, content (stringified: str as-is, else JSON), source?, transform?}`.
  Full content is stored — phase B matches it against message segments.
- `span_start`/`span_end` payloads: `{name}`; same `span_id` envelope field.
- openai instrumentation binds the **span at call time** (stream proxies keep it so a
  stream consumed after the span exits still records the right `span_id`).
- Schema v1 additions (additive `allOf` branches): `tag` requires `payload.name` +
  `payload.content` and a non-null `span_id`; `span_start`/`span_end` require
  `payload.name`. No version bump (no prior writers emitted these events).
- Everything is init-safe: span/tag before `init()` are silent no-ops (state.emit
  already guarantees it) — the capture layer never breaks the host app.
- `normalize` passes `span_id` through on calls (used by phases B/C).

**Tasks (each: failing test → code → green → commit):**
1. `_span.py` + `tests/test_span.py`: start/end events, tag stringification +
   source/transform, nesting restore, async isolation, no-init safety.
2. Schema `allOf` branches + validation tests.
3. `openai_patch` span binding (sync/async/stream, incl. finish-after-exit) + tests.
4. `normalize` span_id passthrough; full suite + ruff; PR "M3 phase A (part of #3)".
