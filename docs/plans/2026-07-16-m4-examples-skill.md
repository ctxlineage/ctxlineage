# M4 Examples + Agent Skill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship the M4 (part of #4) `examples/` runnable sample apps and the `skills/ctxlineage-instrument/SKILL.md` Agent Skill, so a newcomer gets a first report in under 5 minutes — with or without an API key (PLAN.md §10).

**Architecture:** Two self-contained example scripts (`examples/rag_app.py`, `examples/agent_app.py`) that exercise the full public API (`init()`, `span()`, `tag(source=, transform=)`) against the real OpenAI API when `OPENAI_API_KEY` is set, or against a deterministic respx mock with `--mock` (no key, no network). One Agent Skill document that teaches a coding agent the instrumentation procedure end-to-end. Tests follow the existing `test_demo_generator.py` pattern: run the script via subprocess with `--mock` and `CTXLINEAGE_DIR`, then assert on the recorded events and on `build_report_data()` output (tags actually match, edges actually appear — the examples must be *working* exemplars, not just plausible-looking ones).

**Tech Stack:** Python 3.10+, openai SDK + respx (both already dev deps), pytest, no new dependencies.

**Explicitly out of scope:** `examples/generate_demo_events.py` (untouched), README (owned by another session), anthropic-based examples (anthropic patch is still in PR #31; both examples use the openai SDK), streaming in examples (already covered by capture tests; keep the exemplars minimal).

---

## Shared design decisions

- **Key/mock two-stage behavior:** explicit `--mock` always mocks (respx intercepts `https://api.openai.com/v1/chat/completions`, client gets a dummy key). Without `--mock` and without `OPENAI_API_KEY`, print a two-line hint (`set OPENAI_API_KEY, or re-run with --mock`) and exit with code 2 — never crash with an SDK auth traceback.
- **Mock via respx side_effect with a per-call counter** returning canned deterministic responses, so lineage is reproducible and testable. respx import happens only inside the `--mock` branch, with a friendly error if it is missing (`uv sync` / `pip install respx`).
- **Tag exactly what is interpolated into `messages`.** Chunk lists are tagged as lists (matching.py handles JSON arrays element-wise); everything else is tagged as the exact string. This is the pattern the SKILL.md teaches, and the tests enforce a 100% match rate for rag_app.
- **Output→input lineage arises naturally:** answers are appended to `history` and fed to the next call, so `output_text` edges appear without artifice.
- **Each script is standalone** (own mock helper, own toy retrieval). Examples optimize for copy-paste readability over DRY between files.
- **Ending banner** prints the events path and `Next: uv run ctxlineage report --open` — the §10 five-minute path.

---

### Task 1: `examples/rag_app.py` (span/tag exemplar) + tests

**Files:**
- Create: `examples/rag_app.py`
- Create: `tests/test_examples.py`

**Step 1: Write the failing tests** (`tests/test_examples.py`)

Module-scoped fixture runs `sys.executable examples/rag_app.py --mock` via subprocess with `env={**os.environ, "CTXLINEAGE_DIR": tmp}`, loads events. Tests:

- `test_rag_all_events_schema_valid` — every event passes `validate_event`.
- `test_rag_spans_and_tags` — 3 spans named `answer_query`; each span has tags `system`, `rag_chunks` (with `source` and `transform`), and (turns 2–3) `history` (with `transform`); every `llm_call` carries a `span_id`.
- `test_rag_tags_all_match` — `build_report_data(events)` reports `match` on every element (no unmatched tag: the exemplar must demo an honest 100%).
- `test_rag_lineage_edges` — the session has at least one `output_text` edge (turn N answer → turn N+1 input) and `rag_chunks` element carries `source`/`transform`.
- `test_rag_no_key_hint` — run *without* `--mock` and with `OPENAI_API_KEY` removed from env → exit code 2, stderr/stdout mentions `--mock`.

**Step 2: Run tests, verify they fail** (`uv run pytest tests/test_examples.py -v` → all FAIL: script missing).

**Step 3: Implement `examples/rag_app.py`**

Shape (final code in repo is authoritative):

```python
#!/usr/bin/env python3
"""Minimal RAG app instrumented with ctxlineage — the span()/tag() exemplar. ..."""
SYSTEM_PROMPT = ...                     # answer-from-context prompt, cites [doc-N]
DOCS = [...]                            # 5 short chunks about a fictional CLI ("aurora")
QUESTIONS = [...]                       # 3 fixed turns
def retrieve(query, k=2): ...           # keyword-overlap scoring → top-k chunks
def answer_turn(client, history, question):
    with ctxlineage.span("answer_query") as span:
        span.tag("system", SYSTEM_PROMPT, source="rag_app.py:SYSTEM_PROMPT")
        chunks = retrieve(question)
        span.tag("rag_chunks", chunks, source="keyword_index:aurora_docs", transform="top_k(2)")
        recent = history[-6:]
        if recent: span.tag("history", recent, transform="last_6_messages")
        resp = client.chat.completions.create(model=..., messages=[system, *recent, user(question+chunks)])
    history += [user, assistant]; return answer
def mock_router(): ...                  # respx router; side_effect returns MOCK_ANSWERS[i]
def main(argv=None) -> int: ...         # argparse --mock / --model; init(); loop; banner
if __name__ == "__main__": raise SystemExit(main())
```

Mock answers cite the doc ids and restate enough unique text that turn-to-turn `output_text` edges clear the matching threshold.

**Step 4: Run tests → PASS.** Also eyeball once end-to-end: `CTXLINEAGE_DIR=/tmp/ragdemo uv run python examples/rag_app.py --mock && uv run ctxlineage report --dir /tmp/ragdemo -o /tmp/rag.html`.

**Step 5: Commit** — `feat(examples): rag_app.py, keyless via --mock (part of #4)`.

### Task 2: `examples/agent_app.py` (multi-turn + tool loop) + tests

**Files:**
- Create: `examples/agent_app.py`
- Modify: `tests/test_examples.py` (add agent fixture + tests)

**Step 1: Failing tests**

- `test_agent_all_events_schema_valid`.
- `test_agent_tool_loop` — ≥2 sessions? No: 1 session, 2 user turns, ≥4 `llm_call`s; at least one request carries `tools`; at least one recorded response has `finish_reason == "tool_calls"`; a later call's messages contain a `role == "tool"` message.
- `test_agent_spans_and_tool_tags` — spans `agent_turn` per user turn; `tool_result` tags with `source="tool:search_notes"`; tool-result tag content matches (appears in `build_report_data` as matched element).

**Step 2: Run → FAIL.**

**Step 3: Implement `examples/agent_app.py`**

- One tool `search_notes(query)` over a small in-memory notes list; OpenAI function-calling schema.
- Per user turn: `with ctxlineage.span("agent_turn"):` tag `system`; agentic loop (max 4 steps): call with `tools=`, if `tool_calls` → execute, `span.tag("tool_result", result, source="tool:search_notes")`, append `{"role": "tool", ...}` and continue; else final answer, extend shared history.
- Mock: counter-driven side_effect — turn 1: tool_call → answer; turn 2 (question refers to turn 1's answer): tool_call → answer. Deterministic tool arguments so the tool output text is stable.
- Same key-or-`--mock` gate and ending banner as rag_app.

**Step 4: Run tests → PASS** (plus one manual end-to-end report build).

**Step 5: Commit** — `feat(examples): agent_app.py multi-turn tool loop (part of #4)`.

### Task 3: `skills/ctxlineage-instrument/SKILL.md`

**Files:**
- Create: `skills/ctxlineage-instrument/SKILL.md`

**Step 1: Write the skill.** Frontmatter `name: ctxlineage-instrument` + trigger-rich `description`. Body sections:

1. **What you are installing** — one paragraph + the 2-line `init()` contract; safety properties (append-only local JSONL, no network egress, silent no-op on failure).
2. **Step 1 — Install & wire `init()`** — decision criteria for placement: process entry point (main()/CLI callback/app factory/worker boot), before the first LLM call, once per process (first call wins); `CTXLINEAGE_DIR`; add `.ctxlineage/` to `.gitignore`.
3. **Step 2 — Find the context-assembly sites** — grep patterns (`chat.completions.create`, `responses.create`, `messages.create`, plus `messages=` builders); trace upstream to where the messages array is composed; that composing function is where the span goes.
4. **Step 3 — Add spans and tags** — code pattern; tag-naming table (`system`, `rag_chunks`, `history`, `tool_result`, `memory`, `few_shot`…); `source=` conventions (`store:collection`, `file`, `tool:name`, `call:<id>`), `transform=` conventions (`top_k(n)`, `last_n(n)`, `summarize`, `truncate(n)`); **rule: tag the exact object you interpolate** (string, or list for chunk sets); spans don't cross threads.
5. **Step 4 — Verify capture & build the report** — run the app, check `events.jsonl` grew, `ctxlineage report --open`.
6. **Step 5 — Read the report (or `--json`)** — JSON shape (`stats`, `sessions[].calls[].segments`, `sessions[].elements`, `sessions[].edges`, match rate); how to answer §10's three questions; untagged share = the TODO list → iterate Step 3.
7. **Pitfalls & checklist** — no behavior change expected (read-only capture, never proxies); tag before the call inside the span; match rate < 100% means tag content ≠ interpolated content; examples/ links.

**Step 2: Verify** — proofread against the real API (`_span.py`, `_cli.py`) and run one of the examples following only the skill's own instructions mentally; `uv run ruff check` (markdown untouched by ruff, but run suite anyway).

**Step 3: Commit** — `docs(skill): ctxlineage-instrument agent skill (part of #4)`.

### Task 4: Full verification + PR

1. `uv run pytest` (whole suite) and `uv run ruff check . && uv run ruff format --check .` → green.
2. Fresh-clone simulation of the 5-minute path: `uv run python examples/rag_app.py --mock && uv run ctxlineage report --open` — confirm report opens with tagged segments and edges.
3. Push branch `m4-examples`, open PR to `main` titled `M4: examples + ctxlineage-instrument agent skill` with body `part of #4`. **Do not merge — user decides.**
