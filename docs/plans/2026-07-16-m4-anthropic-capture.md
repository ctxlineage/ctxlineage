# M4 (capture side) — Anthropic Auto-Instrumentation + Patcher Coexistence Matrix

> **For Claude:** TDD, one PR ("part of #4"). Tracking issues: #4 (anthropic SDK
> auto-instrumentation) and #26 (coexistence test matrix, langfuse.openai item).
> Do NOT touch `src/ctxlineage/_report/` or `assets/` — a parallel session owns
> M3 report work. Dev-dependency additions are a standalone first commit.

**Goal:** `ctxlineage.init()` records anthropic Messages API calls (sync, async,
and both streaming paths) exactly like it records openai calls, and CI proves
ctxlineage coexists with the `langfuse.openai` drop-in (both wrap orders,
streaming included).

**Architecture:** one new patch module `_instrument/anthropic_patch.py` mirrors
`openai_patch.py`; the openai stream proxies and payload helpers are first
extracted into `_instrument/_common.py` (pure refactor, parameterized on an
`assemble` callable) so both providers share one recording implementation.
Coexistence tests run each scenario in a subprocess because both ctxlineage and
langfuse monkey-patch process-global SDK state — import order cannot be undone
within one pytest process.

**Tech Stack:** wrapt, respx (mocked HTTP incl. SSE), pytest(-asyncio),
anthropic ≥0.40 (dev), langfuse ≥3 (dev), stdlib `http.server` +
`opentelemetry-proto` (langfuse dependency) for OTLP export assertions.

---

## SDK facts the design rests on (verified against anthropic 0.116.0)

1. `client.messages.create(stream=True)` returns `Stream[RawMessageStreamEvent]`
   — same wrapper-proxy treatment as openai.
2. `client.messages.stream(...)` does **not** go through `create()`: it returns
   a `MessageStreamManager` holding a `partial(self._post, ..., stream=True)`;
   the HTTP request only fires inside `__enter__`, which wraps the raw stream in
   a `MessageStream`. Patching `create` alone silently misses the SDK's
   *recommended* streaming path.
3. `MessageStream.__init__` stores the raw stream at `self._raw_stream` and
   builds its iterators lazily (generators not started until first `next()`).
   Swapping `stream._raw_stream` for a recording proxy right after `__enter__`
   is therefore picked up by all consumption paths (`__iter__`, `text_stream`,
   `get_final_message()`), and `MessageStream.close()` →
   `self._raw_stream.close()` guarantees the finish hook on abandon/exit.
   → both streaming paths funnel through **one** raw-event recording proxy and
   **one** `_assemble_messages` reducer.
4. `AsyncMessages.stream(...)` is a *sync* method returning
   `AsyncMessageStreamManager`; only `create` needs an async wrapper.
5. The SDK's SSE parser dispatches on the SSE **event name** — respx stream
   bodies must be `event: <type>\ndata: <json>\n\n` (no `[DONE]` sentinel;
   anthropic streams end at `message_stop`).
6. `langfuse.openai` wraps the *same* targets as ctxlineage
   (`openai.resources.chat.completions` `Completions.create` /
   `AsyncCompletions.create`, both via `wrapt.wrap_function_wrapper`) at import
   time — double-wrap nesting order is decided purely by init/import order.

## Scope guards

- Providers/API surfaces: Messages API only. Legacy Text Completions
  (`client.completions`), `client.beta.*`, `count_tokens`, and Batches are out
  (thin-patch-layer principle; add on demand).
- Stream assembly accumulates `text_delta` content only (mirrors the openai
  assembler, which also ignores tool-call deltas). Tool-use / thinking deltas
  still land in `chunk_count` and the final `usage`; richer assembly is a
  follow-up.
- Report-side handling of anthropic payloads (usage vocabulary
  `input_tokens`/`output_tokens`, content-block shapes) belongs to `_report/`
  which this PR must not touch → file a follow-up GitHub issue instead.
- Coexistence matrix covers langfuse only; LangSmith / OpenLLMetry rows and the
  #26 decision doc stay in #26.

## Event payload contract (schema v1, no schema change needed)

`payload.provider` is a free-form string in `schema/events.v1.schema.json` —
new value `"anthropic"`, `api: "messages"`. Non-stream `response` is the
`model_dump` of `Message` (usage: `{"input_tokens": …, "output_tokens": …}`
recorded as-is). Assembled stream response:

```json
{
  "object": "message.assembled",
  "id": "msg_…", "model": "claude-…",
  "content": {"0": "Hello world"},
  "stop_reason": "end_turn",
  "usage": {"input_tokens": 9, "output_tokens": 2},
  "chunk_count": 7
}
```

(`usage` = `message_start.message.usage` overlaid with `message_delta.usage`,
so `output_tokens` ends up final; `content` keyed by content-block index as
strings, like the openai assembler's choice indices.)

---

### Task 0: dev dependencies (DONE — commit `0228485`)

`uv add --group dev "anthropic>=0.40" "langfuse>=3"` — standalone commit
touching only `pyproject.toml` + `uv.lock`.

### Task 1: extract shared patch plumbing (pure refactor, no behavior change)

**Files:**
- Create: `src/ctxlineage/_instrument/_common.py`
- Modify: `src/ctxlineage/_instrument/openai_patch.py`

**Step 1:** move from `openai_patch.py` into `_common.py`, renamed public
(module is still private to the package): `base_payload(provider, api, kwargs)`
(provider becomes a parameter), `dump`, `finish_payload`, `record_response`,
`record_error`, `StreamRecorderMixin`, `StreamProxy`, `AsyncStreamProxy`.
The mixin/proxies take an `assemble: Callable[[list], dict]` instead of the
`api` string (`_self_assemble` replaces the `_self_api` dispatch in
`_self_finish`).

**Step 2:** `openai_patch.py` keeps only: `install()`, the two wrapper
factories (now calling `_common` helpers, passing
`_assemble_responses if api == "responses" else _assemble_chat` to the
proxies), and the two assemblers.

**Step 3:** `uv run pytest -q` — all 111 existing tests stay green (openai
suites exercise every moved line). `uv run ruff check . && uv run ruff format .`

**Step 4:** commit `refactor: extract shared SDK-patch plumbing into _instrument/_common.py`.

### Task 2: anthropic Messages — non-stream (sync + async), TDD

**Files:**
- Test: `tests/test_anthropic_messages.py` (new), `tests/test_anthropic_messages_async.py` (new)
- Modify: `tests/conftest.py` (fixtures below)
- Create: `src/ctxlineage/_instrument/anthropic_patch.py`
- Modify: `src/ctxlineage/_instrument/__init__.py`

**Step 1: conftest fixtures**

```python
_MESSAGES_RESPONSE = {
    "id": "msg_test1", "type": "message", "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [{"type": "text", "text": "Hello there!"}],
    "stop_reason": "end_turn", "stop_sequence": None,
    "usage": {"input_tokens": 9, "output_tokens": 3},
}

@pytest.fixture
def messages_response_json():
    return dict(_MESSAGES_RESPONSE)

@pytest.fixture
def anthropic_client():
    import anthropic
    # max_retries=0: keep the 5xx error tests from sleeping through backoff
    return anthropic.Anthropic(api_key="test-key", max_retries=0)

def _anthropic_sse(*events) -> bytes:
    """Anthropic SSE frames are named events; the SDK dispatches on the name."""
    return b"".join(
        b"event: " + e["type"].encode() + b"\ndata: " + json.dumps(e).encode() + b"\n\n"
        for e in events
    )

@pytest.fixture
def messages_stream_body():
    """'Hello' + ' world' text deltas, final usage via message_delta."""
    return _anthropic_sse(
        {"type": "message_start", "message": {
            "id": "msg_test1", "type": "message", "role": "assistant",
            "model": "claude-sonnet-5", "content": [], "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 9, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "Hello"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": " world"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta",
         "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": 2}},
        {"type": "message_stop"},
    )
```

**Step 2: failing tests** — `tests/test_anthropic_messages.py` mirrors
`test_openai_chat.py` one-to-one (`MESSAGES_URL =
"https://api.anthropic.com/v1/messages"`; create args `model="claude-sonnet-5",
max_tokens=64, messages=MESSAGES`): schema-valid event; request recorded
verbatim (incl. `max_tokens`, `temperature`, `metadata` pass-through); response
+ usage recorded (`provider == "anthropic"`, `api == "messages"`,
`usage["output_tokens"] == 3`, `response["content"][0]["text"]`); call stack
points at user code; API error (500 → `anthropic.APIStatusError`) recorded and
re-raised with no `response` key; no-init passthrough writes nothing.
Async file mirrors `test_openai_chat_async.py` (schema-valid event, error type
`InternalServerError`).

**Step 3:** `uv run pytest tests/test_anthropic_messages.py -q` → all FAIL
(no events recorded — patch module doesn't exist yet).

**Step 4: implementation** — `anthropic_patch.py`:

```python
def install() -> bool:            # same _PATCHED-once contract as openai
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    wrapt.wrap_function_wrapper("anthropic.resources.messages", "Messages.create", _sync_create)
    wrapt.wrap_function_wrapper("anthropic.resources.messages", "AsyncMessages.create", _async_create)
    wrapt.wrap_function_wrapper("anthropic.resources.messages", "Messages.stream", _sync_stream)
    wrapt.wrap_function_wrapper("anthropic.resources.messages", "AsyncMessages.stream", _async_stream)
```

`_sync_create`/`_async_create` are the openai wrappers with
`base_payload("anthropic", "messages", kwargs)` and
`StreamProxy(result, payload, _assemble_messages, _span.current_id())` for
`stream=True`. Register in `_instrument/__init__.py`:
`if anthropic_patch.install(): providers.append("anthropic")`.

`_assemble_messages(chunks)` per the contract above (`message_start` → id /
model / base usage; `content_block_delta.text_delta` → per-index concat;
`message_delta` → stop_reason + usage overlay; `usage or None` when absent).

**Step 5:** non-stream + error + no-init tests PASS (stream tests arrive in
Task 3). Commit? No — Tasks 2+3 land as one commit once the whole anthropic
suite is green (the stream wrappers are already installed above; committing
between would strand dead code).

### Task 3: anthropic streaming — both paths, TDD

**Files:**
- Test: `tests/test_anthropic_messages_stream.py` (new), append to
  `tests/test_anthropic_messages_async.py`
- Modify: `src/ctxlineage/_instrument/anthropic_patch.py`

**Step 1: failing tests** (`SSE_HEADERS = {"content-type": "text/event-stream"}`):

- `create(stream=True)` full iteration → 7 raw events seen by caller; one
  schema-valid event; `content["0"] == "Hello world"`, `stop_reason ==
  "end_turn"`, `usage == {"input_tokens": 9, "output_tokens": 2}`,
  `chunk_count == 7`, `stream is True`.
- exhausted-twice → exactly one event.
- abandoned after 3 raw events + `close()` → one event, `content["0"] == "Hello"`.
- `.stream()` helper: `with client.messages.stream(...) as s:` join
  `s.text_stream` == `"Hello world"` → one event, full assembly, `stream: True`,
  request kwargs recorded.
- `.stream()` helper + `get_final_message()` → SDK still returns the full
  `Message` (proxy transparency), one event.
- `.stream()` abandoned (break after first text) → partial `"Hello"` recorded
  on `__exit__`.
- `.stream()` manager never entered → **zero** events (no HTTP happened).
- async: `create(stream=True)` async-iterated; `async with
  client.messages.stream(...)` + `text_stream` — same assertions.

**Step 2:** run → stream tests FAIL (`.stream()` returns an unwrapped manager;
create-path proxy exists already from Task 2 wiring, those may pass).

**Step 3: implementation** — manager proxies in `anthropic_patch.py`:

```python
def _sync_stream(wrapped, instance, args, kwargs):
    if not _state.is_configured():
        return wrapped(*args, **kwargs)
    payload = base_payload("anthropic", "messages", kwargs)
    payload["stream"] = True          # .stream() has no stream kwarg
    return _ManagerProxy(wrapped(*args, **kwargs), payload, _span.current_id())

class _ManagerProxy(wrapt.ObjectProxy):
    # The manager fires the HTTP request in __enter__; swap the fresh
    # MessageStream's _raw_stream for our recording proxy so every
    # consumption path (iter/text_stream/get_final_message) and
    # MessageStream.close() flow through it.
    def __enter__(self):
        start = time.monotonic()
        try:
            stream = self.__wrapped__.__enter__()
        except Exception as exc:
            record_error(finish_payload(self._self_payload, start), exc)
            raise
        finish_payload(self._self_payload, start)
        stream._raw_stream = StreamProxy(
            stream._raw_stream, self._self_payload, _assemble_messages, self._self_span_id)
        return stream

    def __exit__(self, *exc):
        return self.__wrapped__.__exit__(*exc)   # → MessageStream.close() → proxy finish
```

(`_AsyncManagerProxy` identical with `__aenter__`/`__aexit__` +
`AsyncStreamProxy`. `duration_ms` = enter-to-headers time, matching the
create-path semantics of "request latency, not consumption time".)

**Step 4:** full anthropic suite green; whole suite green; ruff clean.

**Step 5:** commit `feat: anthropic Messages auto-instrumentation (sync/async, both stream paths)`.

### Task 4: langfuse.openai coexistence matrix (#26)

**Files:**
- Create: `tests/coexistence/langfuse_openai_scenario.py` (subprocess script, not collected)
- Test: `tests/test_coexistence_langfuse.py`

**Why subprocess:** both libraries patch process-global SDK state at
import/init time; wrap order is unreproducible within one pytest process.

**Scenario script** (argv: `order mode events_dir`):
1. Start a stdlib `ThreadingHTTPServer` on an ephemeral port recording every
   request (path, headers, body); answer 200 `{}`.
2. `os.environ` (before any langfuse import): `LANGFUSE_PUBLIC_KEY/SECRET_KEY`
   dummies, `LANGFUSE_HOST=http://127.0.0.1:<port>`, `NO_PROXY=127.0.0.1` —
   langfuse stays fully active but exports OTLP to our local sink.
3. Apply `order`: `ctxlineage-first` = `ctxlineage.init(events_dir)` then
   `from langfuse.openai import OpenAI` (langfuse wraps outermost);
   `langfuse-first` = reverse (ctxlineage outermost).
4. Under `respx.mock(assert_all_called=False)` with a
   `.route(host="127.0.0.1").pass_through()` escape hatch: mock the chat URL
   (JSON or SSE per `mode`), run the call, collect `response_text`
   (concatenated deltas for `stream`).
5. `from langfuse import get_client; get_client().flush()`.
6. Decode captured OTLP posts with
   `opentelemetry.proto...ExportTraceServiceRequest` (gunzip body when
   `content-encoding: gzip`), count spans, stringify all span attributes.
7. Print one JSON line: `response_text`, parsed `events.jsonl` lines,
   `langfuse_span_count`, `langfuse_saw_input` (`"Say hello"` appears in the
   attribute blob), export paths.

**Parent test:** `@pytest.mark.parametrize` over
`order ∈ {ctxlineage-first, langfuse-first} × mode ∈ {plain, stream}` →
`subprocess.run([sys.executable, script, …], timeout=120)`; assert exit 0, the
SDK response intact through both wrappers, **exactly one** ctxlineage event
with correct payload (assembled `"Hello world"` + `total_tokens == 11` for
stream), `langfuse_span_count >= 1`, and `langfuse_saw_input` — i.e. *both*
sides captured, neither broke the call. Four subprocesses ≈ a few seconds
each; acceptable for CI.

**Steps:** write script + failing-shape test → run matrix → fix script until
green (the assertions on ctxlineage behavior must never be weakened to pass;
if a langfuse-side assertion proves version-fragile, relax only the
`langfuse_saw_input` check to span-count and record why in a comment) →
`uv run pytest tests/test_coexistence_langfuse.py -q` → commit
`test: coexistence matrix with the langfuse.openai drop-in (#26)`.

### Task 5: wrap-up

1. `uv run pytest -q` (full suite) + `uv run ruff check . && uv run ruff format --check .`
2. File follow-up issue: report-side anthropic payload handling (usage
   vocabulary + content blocks in `_report/normalize.py` / `tokens.py`) —
   blocked on M3 session owning `_report/`.
3. Push `m4-anthropic`, open PR titled
   `feat: anthropic Messages auto-instrumentation + langfuse coexistence matrix`,
   body: demo instructions, "part of #4", "adds the langfuse row of the #26
   test matrix".
