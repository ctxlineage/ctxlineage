# M3 Phase B — Segment Matching (tags → real segment boundaries)

> **For Claude:** TDD, one PR. Tracking issue: #3, phase B of five. Follows the
> matching contract in PLAN.md §4(a): exact match → partial match → honest
> "untagged" fallback, with the match rate displayed in the report.

**Goal:** when a call was made inside a span with tags, the report shows *tag-named*
segments (`rag_chunks · qdrant:products_v2`) with real boundaries instead of the
role heuristic — and states honestly how much of the tagging actually matched.

## Design

- **`_report/matching.py`** — pure functions, no I/O:
  - A tag yields *match units*: its content string; if the content parses as a JSON
    array of strings, each element is a unit (the `rag_chunks=[...]` case — the app
    joins chunks into one message, so the whole-list JSON never matches but each
    chunk does).
  - For each role segment of the call, find non-overlapping occurrences of every
    unit (leftmost-first; longer units win ties). Split the segment into parts:
    matched parts get `kind = tag name`, `source`/`transform`, `tagged: true`,
    `match: "exact"|"partial"` (exact = unit equals the whole segment content);
    unmatched gaps keep the role kind with `tagged: false`.
  - A tag counts as *matched* if ≥1 of its units matched somewhere in the call's
    segments.
- **normalize.py** —
  - Build span maps from events: `span_id → name` (span_start) and
    `span_id → [tags]` (tag events, in order).
  - Calls with a `span_id` get: `step` = span name (frontend prefers it over
    `call_stack`), and their segments run through matching when tags exist.
  - `stats.tags = {total, matched, match_rate}` (null-safe when no tags exist);
    per-call `tagged_tokens_est` so the UI can show attribution coverage.
- **Frontend** — `stepOf` prefers `call.step`; tag-kind segments get deterministic
  colors from a small extra palette (`--tag1..5`, hash by kind name) with the tag
  name as label (+ `· source` when present); Overview gains a "tag match rate"
  stat card when tags exist; chain chips/legend pick up tag kinds automatically.
- **Demo generator** — session 4 uses the span/tag API shape (span_start/tag/
  llm_call-with-span_id/span_end written directly): tagged system prompt, tagged
  chunk list (matching the embedded message text), plus one deliberately
  non-matching tag so the demo shows a <100% match rate honestly.

## Tasks

1. `matching.py` + unit tests: exact, partial-with-split, JSON-list elements,
   overlap resolution, no-match → untagged, unicode safety.
2. `normalize.py` integration + tests: span-name step, tag segments on calls,
   stats.tags, calls outside spans unaffected.
3. Demo generator session 4 + end-to-end test (match rate between 0 and 1).
4. Frontend updates + template test; rebuild demo, visual check.
5. PR "M3 phase B (part of #3)".
