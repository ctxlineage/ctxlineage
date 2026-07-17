# Stream-proxy hardening — #34 items 1, 3, 4

> **For Claude:** TDD, one PR ("closes #34"). Touches `_instrument/` only — two
> parallel sessions own `_cli.py` (#14 contract testing, #57 transcript import),
> so do not touch it. Items 1 and 4 are code; item 3 is a documented WONTFIX.

**Goal:** close the last three deferred items from the PR #31 second-opinion
review. Item 2 (the `install()` lock) already shipped in PR #52.

---

## SDK/runtime facts this design rests on (measured 2026-07-17, not assumed)

1. **`weakref.finalize` works on `wrapt.ObjectProxy`.** A finalizer registered
   on the proxy runs on `del` + `gc.collect()`; `weakref.ref(proxy)` resolves to
   the proxy (not the wrapped object) and dies with it. So item 1 does not need
   `__del__` and its interpreter-shutdown hazards.
2. **`EventWriter` opens the file per write, holds no buffer, and registers no
   `atexit` teardown** (`_events.py`). `weakref.finalize`'s atexit hook runs
   before module teardown, so emitting from a finalizer at exit is safe — there
   is no closed writer or torn-down global to trip over. Hence `atexit` is left
   at its default (`True`); a stream still alive at exit is recorded, not lost.
3. **`copy.deepcopy` of a 40-message / 156KB kwargs dict costs ~16µs** — 0.005%
   of a ~300ms LLM call. Strings are immutable so deepcopy shares them; only the
   containers are new, so the memory cost is proportional to structure, not text.
4. **Deepcopy failure modes are narrow:** modules raise `TypeError`; plain
   objects and callables copy fine. So a per-key fallback is enough.
5. **`payload` is `additionalProperties: true`** in `schema/events.v1.schema.json`
   (only the envelope is strict) — a new `abandoned` field needs no schema bump.
6. **anthropic's `_SyncStreamMeta.__instancecheck__` returns `False` for
   everything except `MessageStream`.** A real `Stream` passes `isinstance` only
   via CPython's exact-type fast path (`Py_IS_TYPE`), which skips
   `__instancecheck__` entirely. Verified: a *genuine subclass* of such a class
   is also `False`. ABC registration cannot help either — the metaclass does not
   call `super()`.

---

## Item 1 — GC-abandoned streams never record

**Gap:** `create(stream=True)` whose return value is never iterated, closed, or
exited emits nothing. The request was sent (the context reached the provider),
so the call is invisible in the report — a hole in the product's central claim.
`__iter__`'s `finally` only saves the case where iteration *started*; a
`next()`-then-drop also leaks today (the issue did not mention this second gap).

**Design:** the finalizer callback must not reference the proxy (a strong ref
would make it immortal), so recording state moves off the proxy into a small
`_StreamRecord` (`payload`, `assemble`, `span_id`, `chunks`, `done`) held by
both the proxy and the finalizer. `StreamRecorderMixin` keeps its `_self_*` API
and delegates to the record, so `StreamProxy` / `AsyncStreamProxy` and both
patch modules are untouched.

- `_finish(record, abandoned=False)` becomes a module function (idempotent via
  `record.done`), so the finalizer can call it without the proxy.
- `_self_init` registers `weakref.finalize(self, _finish, record, abandoned=True)`.
- Normal completion sets `done=True`; the finalizer later no-ops.

**`abandoned: true` means exactly:** *recorded from the finalizer — the host
never iterated to completion, closed, or exited this stream.* It is not a
general "output unconsumed" flag: `for c in stream: break` (no `with`/`close`)
still records unflagged, because the generator's `finally` fires first and marks
the record done. That is today's behaviour and stays unchanged.

**Tests:** create a stream, drop it without touching it, `gc.collect()` → one
event with `abandoned: true`; `next()` once then drop → one event, partial
chunks, `abandoned: true`; normal completion → one event, no flag (no
regression); async twin of the first.

## Item 4 — `base_payload` shallow-copies kwargs

**Gap:** `"request": dict(kwargs)` shares the caller's `messages` list. A host
mutating it between the call and stream completion pollutes the recorded
request. The window is widest on the stream path (emit happens at stream end).

**Design:** snapshot per key — `copy.deepcopy` each value independently, falling
back to the original reference for any key that raises. Per-key (not whole-dict)
fallback means one un-copyable key (e.g. a module) cannot cost `messages` its
protection. Applied in `base_payload` for every path: at 16µs it is not worth a
stream/non-stream branch, and symmetry keeps the patch layer minimal.

**Tests:** mutate `messages` after the call, before stream completion → the
recorded request keeps the original; a kwarg that cannot be deepcopied → the
call still records and `messages` is still snapshotted.

## Item 3 — `isinstance(stream, anthropic.Stream)` is False for the proxy — WONTFIX

**No fix exists** (fact 6): not via the proxy, not via subclassing, not via ABC
registration. Only an object whose type is *exactly* `Stream` passes, which a
recording wrapper can never be.

The one theoretical route is swapping the real `Stream`'s private `_iterator`
(it exists, and `__iter__`/`__next__` both delegate to it) so the genuine object
is returned. Rejected: it couples capture to an SDK private for a check
anthropic itself deprecates (its own metaclass warns that `isinstance(_,
Stream)` is deprecated), and it would leave two recording paths to maintain and
test. Note the codebase *does* swap `_raw_stream` for `messages.stream()` — but
there it is the only way to record at all, whereas here the proxy already
records correctly and the swap would buy nothing but `isinstance`.

**Action:** no code. Sharpen `StreamProxy`'s docstring with the *reason* (exact
type match only; subclasses fail too) so a future reader does not re-derive it,
and record the evidence on #34 before closing.

---

## Build order

1. Plan doc (this file).
2. Item 4 (self-contained, no refactor) + tests.
3. Item 1: `_StreamRecord` extraction (pure refactor, suite stays green) →
   finalizer + tests.
4. Item 3 docstring; close #34 with the evidence.
