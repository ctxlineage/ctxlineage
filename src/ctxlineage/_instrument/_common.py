"""Shared plumbing for SDK patch modules.

Every provider patch stays thin: record kwargs wholesale, dump responses with
model_dump, pass unknown fields through untouched. Recording must never break
the host call: wrappers no-op when ctxlineage is unconfigured, and
_state.emit() already swallows write failures.
"""

from __future__ import annotations

import copy
import time
import weakref
from collections.abc import Callable

import wrapt

from ctxlineage import _events, _state
from ctxlineage._stack import stack_summary


def _snapshot(kwargs: dict) -> dict:
    """Copy the request so later host mutations cannot rewrite what we recorded.

    Stream events emit when the stream ends, so a host that appends to its own
    `messages` list in the meantime would otherwise pollute the record of what
    was actually sent. Copying per key (rather than the dict as a whole) keeps
    one un-copyable value — a module, a live client — from costing every other
    key its snapshot. Cheap: strings are immutable and therefore shared, so the
    cost tracks the structure, not the text (~16µs for a 156KB message list).
    """
    snapshot = {}
    for key, value in kwargs.items():
        try:
            snapshot[key] = copy.deepcopy(value)
        except Exception:  # un-copyable value: the live reference beats no record
            snapshot[key] = value
    return snapshot


def base_payload(provider: str, api: str, kwargs: dict) -> dict:
    return {
        "provider": provider,
        "api": api,
        "request": _snapshot(kwargs),
        "stream": bool(kwargs.get("stream")),
        "call_stack": stack_summary(),
    }


def dump(obj):
    # A response that is already a plain dict (a raw-response wrapper, a mocked
    # client, or a non-pydantic SDK shape) has no model_dump, so without this
    # it would fall through to str(obj) and be stored as a Python repr — losing
    # the structured body and, with it, usage (record_response only reads usage
    # off a dict). Keep the dict.
    if isinstance(obj, dict):
        return obj
    try:
        return obj.model_dump(mode="json")
    except Exception:
        return str(obj)


def finish_payload(payload: dict, start: float) -> dict:
    payload["duration_ms"] = (time.monotonic() - start) * 1000
    return payload


def record_response(payload: dict, result) -> None:
    data = dump(result)
    payload["response"] = data
    payload["usage"] = data.get("usage") if isinstance(data, dict) else None
    _state.emit("llm_call", payload, call_id=_events.new_id())


def record_error(payload: dict, exc: BaseException) -> None:
    payload["error"] = {"type": type(exc).__name__, "message": str(exc)}
    _state.emit("llm_call", payload, call_id=_events.new_id())


class _StreamRecord:
    """Recording state for one stream, held deliberately *off* the proxy.

    weakref.finalize's callback must not reference the proxy — a strong ref
    would keep it alive forever and the finalizer would never run — so every
    piece of state the finalizer needs lives here instead.
    """

    __slots__ = ("payload", "assemble", "span_id", "chunks", "done")

    def __init__(self, payload: dict, assemble: Callable[[list], dict], span_id) -> None:
        self.payload = payload
        self.assemble = assemble
        self.span_id = span_id
        self.chunks: list = []
        self.done = False


def _finish(record: _StreamRecord, abandoned: bool = False) -> None:
    """Emit the assembled stream event. Idempotent: only the first call records."""
    if record.done:
        return
    record.done = True
    try:
        payload = record.payload
        payload["response"] = record.assemble(record.chunks)
        payload["usage"] = payload["response"].get("usage")
        if abandoned:
            # Recorded only because the object was collected: the host never
            # iterated to completion, closed, or exited this stream. Not a
            # general "output unconsumed" flag — see StreamRecorderMixin.
            payload["abandoned"] = True
        _state.emit("llm_call", payload, call_id=_events.new_id(), span_id=record.span_id)
    except Exception as exc:  # e.g. malformed chunk shapes: never raise into the host
        _state.warn_once(
            "stream_finish",
            f"ctxlineage: failed to record a stream ({exc!r}); the stream itself is intact",
        )


class StreamRecorderMixin:
    """Shared chunk accounting; subclasses only differ in (a)sync plumbing."""

    def _self_init(self, payload: dict, assemble: Callable[[list], dict], span_id=None) -> None:
        record = _StreamRecord(payload, assemble, span_id)
        self._self_record = record
        # A stream the host drops without iterating, closing, or exiting has no
        # other emit path, yet its request already reached the provider — losing
        # the event would hide a call that really did consume context. The
        # finalizer is the only hook that catches those; _finish is idempotent,
        # so for a stream that ends normally this is a no-op. Left registered
        # for atexit (the default): EventWriter opens per write and has no
        # teardown, so emitting from the atexit pass is safe.
        weakref.finalize(self, _finish, record, abandoned=True)

    def _self_add(self, chunk) -> None:
        self._self_record.chunks.append(dump(chunk))

    def _self_record_error(self, exc: BaseException) -> None:
        # mid-stream failure (in-band SSE error, network drop): keep the
        # partial assembly but mark the event so it is distinguishable from
        # a client-side abandon. GeneratorExit is excluded at the call sites.
        record = self._self_record
        if not record.done:
            record.payload["error"] = {"type": type(exc).__name__, "message": str(exc)}

    def _self_finish(self) -> None:
        _finish(self._self_record)


class StreamProxy(wrapt.ObjectProxy, StreamRecorderMixin):
    """Recording proxy over an SDK stream.

    Dunder protocol methods must be defined here: ObjectProxy does not fill
    type slots, so builtins like next()/anext() bypass __getattr__ delegation
    and would raise TypeError without explicit definitions.

    Known limitation (#34, WONTFIX): isinstance(proxy, anthropic.Stream) is
    False. Its metaclass __instancecheck__ returns False for everything except
    MessageStream, so a real Stream only passes via CPython's exact-type fast
    path, which skips __instancecheck__ entirely. Nothing we can be — not a
    subclass, not an ABC registration (the metaclass never calls super()) —
    passes that check; only an object whose type *is* Stream does. Host code
    branching on it therefore misbehaves while ctxlineage is active. Swapping
    the SDK's private Stream._iterator would preserve the real type, but it
    would couple capture to an SDK internal for a check anthropic itself
    deprecates, and leave two recording paths to maintain. Revisit only if a
    user actually hits this.
    """

    def __init__(self, wrapped, payload: dict, assemble: Callable[[list], dict], span_id=None):
        super().__init__(wrapped)
        self._self_init(payload, assemble, span_id)

    def __iter__(self):
        try:
            for chunk in self.__wrapped__:
                self._self_add(chunk)
                yield chunk
        except Exception as exc:  # not GeneratorExit: abandonment is not an error
            self._self_record_error(exc)
            raise
        finally:
            self._self_finish()

    def __next__(self):
        try:
            chunk = self.__wrapped__.__next__()
        except StopIteration:
            self._self_finish()
            raise
        except Exception as exc:
            self._self_record_error(exc)
            self._self_finish()
            raise
        self._self_add(chunk)
        return chunk

    def __enter__(self):
        self.__wrapped__.__enter__()
        return self

    def __exit__(self, *exc):
        try:
            return self.__wrapped__.__exit__(*exc)
        finally:
            self._self_finish()

    def close(self):
        try:
            return self.__wrapped__.close()
        finally:
            self._self_finish()


class AsyncStreamProxy(wrapt.ObjectProxy, StreamRecorderMixin):
    """Async twin of StreamProxy; see its docstring for the dunder rationale."""

    def __init__(self, wrapped, payload: dict, assemble: Callable[[list], dict], span_id=None):
        super().__init__(wrapped)
        self._self_init(payload, assemble, span_id)

    async def __aiter__(self):
        try:
            async for chunk in self.__wrapped__:
                self._self_add(chunk)
                yield chunk
        except Exception as exc:  # not GeneratorExit: abandonment is not an error
            self._self_record_error(exc)
            raise
        finally:
            self._self_finish()

    async def __anext__(self):
        try:
            chunk = await self.__wrapped__.__anext__()
        except StopAsyncIteration:
            self._self_finish()
            raise
        except Exception as exc:
            self._self_record_error(exc)
            self._self_finish()
            raise
        self._self_add(chunk)
        return chunk

    async def __aenter__(self):
        await self.__wrapped__.__aenter__()
        return self

    async def __aexit__(self, *exc):
        try:
            return await self.__wrapped__.__aexit__(*exc)
        finally:
            self._self_finish()

    async def close(self):
        try:
            return await self.__wrapped__.close()
        finally:
            self._self_finish()
