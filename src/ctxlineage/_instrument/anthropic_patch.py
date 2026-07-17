"""anthropic SDK instrumentation (Messages API, incl. both streaming paths).

Shared design constraints live in _common.py. Two streaming surfaces exist:

- messages.create(stream=True) returns a raw Stream[RawMessageStreamEvent],
  wrapped in the same recording proxy the openai patch uses.
- messages.stream() never goes through create(): it returns a manager that
  fires the HTTP request in __enter__ and wraps the raw stream in a
  MessageStream. The manager proxy below swaps that MessageStream's
  _raw_stream for a recording proxy, so every consumption path (iteration,
  text_stream, get_final_message) and MessageStream.close() flow through it
  and both streaming paths share one raw-event assembler.
"""

from __future__ import annotations

import threading
import time

import wrapt

from ctxlineage import _span, _state
from ctxlineage._instrument._common import (
    AsyncStreamProxy,
    StreamProxy,
    base_payload,
    finish_payload,
    record_error,
    record_response,
)

_PATCHED = False
# Guards check-then-act on _PATCHED so concurrent installs wrap each method once.
_install_lock = threading.Lock()


def install() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    with _install_lock:
        if _PATCHED:  # another thread won the race
            return True
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        wrapt.wrap_function_wrapper("anthropic.resources.messages", "Messages.create", _sync_create)
        wrapt.wrap_function_wrapper(
            "anthropic.resources.messages", "AsyncMessages.create", _async_create
        )
        wrapt.wrap_function_wrapper("anthropic.resources.messages", "Messages.stream", _sync_stream)
        wrapt.wrap_function_wrapper(
            "anthropic.resources.messages", "AsyncMessages.stream", _async_stream
        )
        _PATCHED = True
        return True


def _sync_create(wrapped, instance, args, kwargs):
    if not _state.is_configured():
        return wrapped(*args, **kwargs)
    payload = base_payload("anthropic", "messages", kwargs)
    start = time.monotonic()
    try:
        result = wrapped(*args, **kwargs)
    except Exception as exc:
        record_error(finish_payload(payload, start), exc)
        raise
    finish_payload(payload, start)
    if payload["stream"]:
        # streams may be consumed after the span exits: bind the span now
        return StreamProxy(result, payload, _assemble_messages, _span.current_id())
    record_response(payload, result)
    return result


async def _async_create(wrapped, instance, args, kwargs):
    if not _state.is_configured():
        return await wrapped(*args, **kwargs)
    payload = base_payload("anthropic", "messages", kwargs)
    start = time.monotonic()
    try:
        result = await wrapped(*args, **kwargs)
    except Exception as exc:
        record_error(finish_payload(payload, start), exc)
        raise
    finish_payload(payload, start)
    if payload["stream"]:
        # streams may be consumed after the span exits: bind the span now
        return AsyncStreamProxy(result, payload, _assemble_messages, _span.current_id())
    record_response(payload, result)
    return result


def _sync_stream(wrapped, instance, args, kwargs):
    if not _state.is_configured():
        return wrapped(*args, **kwargs)
    payload = base_payload("anthropic", "messages", kwargs)
    payload["stream"] = True  # .stream() takes no stream kwarg
    return _ManagerProxy(wrapped(*args, **kwargs), payload, _span.current_id())


def _async_stream(wrapped, instance, args, kwargs):
    # AsyncMessages.stream is a sync method returning an async manager
    if not _state.is_configured():
        return wrapped(*args, **kwargs)
    payload = base_payload("anthropic", "messages", kwargs)
    payload["stream"] = True
    return _AsyncManagerProxy(wrapped(*args, **kwargs), payload, _span.current_id())


class _ManagerProxy(wrapt.ObjectProxy):
    """Records through a MessageStreamManager; the request only fires in __enter__."""

    def __init__(self, wrapped, payload: dict, span_id=None):
        super().__init__(wrapped)
        self._self_payload = payload
        self._self_span_id = span_id

    def __enter__(self):
        start = time.monotonic()
        try:
            stream = self.__wrapped__.__enter__()
        except Exception as exc:
            record_error(finish_payload(self._self_payload, start), exc)
            raise
        finish_payload(self._self_payload, start)
        # MessageStream consumes lazily, so the fresh _raw_stream can be
        # swapped for a recording proxy; MessageStream.close() closes it too.
        # _raw_stream is a private SDK attribute: if the swap fails, hand back
        # the untouched stream (unrecorded) rather than break the host app.
        try:
            stream._raw_stream = StreamProxy(
                stream._raw_stream, self._self_payload, _assemble_messages, self._self_span_id
            )
        except Exception as exc:
            _state.warn_once(
                "anthropic_raw_stream_swap",
                f"ctxlineage: could not instrument messages.stream() ({exc!r}); "
                "this stream will not be recorded",
            )
        return stream

    def __exit__(self, *exc):
        return self.__wrapped__.__exit__(*exc)


class _AsyncManagerProxy(wrapt.ObjectProxy):
    """Async twin of _ManagerProxy (AsyncMessageStreamManager)."""

    def __init__(self, wrapped, payload: dict, span_id=None):
        super().__init__(wrapped)
        self._self_payload = payload
        self._self_span_id = span_id

    async def __aenter__(self):
        start = time.monotonic()
        try:
            stream = await self.__wrapped__.__aenter__()
        except Exception as exc:
            record_error(finish_payload(self._self_payload, start), exc)
            raise
        finish_payload(self._self_payload, start)
        # See _ManagerProxy.__enter__: a failed swap degrades to no recording.
        try:
            stream._raw_stream = AsyncStreamProxy(
                stream._raw_stream, self._self_payload, _assemble_messages, self._self_span_id
            )
        except Exception as exc:
            _state.warn_once(
                "anthropic_raw_stream_swap",
                f"ctxlineage: could not instrument messages.stream() ({exc!r}); "
                "this stream will not be recorded",
            )
        return stream

    async def __aexit__(self, *exc):
        return await self.__wrapped__.__aexit__(*exc)


def _merge_usage(usage: dict, new) -> None:
    # message_delta's usage model dumps unset fields as None (e.g. input_tokens);
    # overlaying those verbatim would erase message_start's real counts.
    if isinstance(new, dict):
        usage.update({k: v for k, v in new.items() if v is not None})


def _assemble_messages(chunks: list) -> dict:
    """Reduce raw Messages stream events into one response-like summary."""
    content: dict[int, str] = {}
    usage: dict = {}
    stop_reason = None
    message_id = None
    model = None
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        kind = chunk.get("type", "")
        if kind == "message_start":
            message = chunk.get("message") or {}
            message_id = message.get("id") or message_id
            model = message.get("model") or model
            _merge_usage(usage, message.get("usage"))
        elif kind == "content_block_delta":
            delta = chunk.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                index = chunk.get("index", 0)
                content[index] = content.get(index, "") + delta["text"]
        elif kind == "message_delta":
            _merge_usage(usage, chunk.get("usage"))
            stop_reason = (chunk.get("delta") or {}).get("stop_reason") or stop_reason
    return {
        "object": "message.assembled",
        "id": message_id,
        "model": model,
        "content": {str(i): text for i, text in content.items()},
        "stop_reason": stop_reason,
        "usage": usage or None,
        "chunk_count": len(chunks),
    }
