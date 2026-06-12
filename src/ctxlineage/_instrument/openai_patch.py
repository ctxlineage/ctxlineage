"""openai SDK instrumentation (Chat Completions + Responses API).

Design constraints:
- The patch layer stays thin: record kwargs wholesale, dump responses with
  model_dump, pass unknown fields through untouched.
- Recording must never break the host call: wrappers no-op when ctxlineage is
  unconfigured, and _state.emit() already swallows write failures.
"""

from __future__ import annotations

import time

import wrapt

from ctxlineage import _events, _state
from ctxlineage._stack import stack_summary

_PATCHED = False


def install() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    wrapt.wrap_function_wrapper(
        "openai.resources.chat.completions",
        "Completions.create",
        _make_sync_wrapper("chat.completions"),
    )
    wrapt.wrap_function_wrapper(
        "openai.resources.chat.completions",
        "AsyncCompletions.create",
        _make_async_wrapper("chat.completions"),
    )
    _PATCHED = True
    return True


def _base_payload(api: str, kwargs: dict) -> dict:
    return {
        "provider": "openai",
        "api": api,
        "request": dict(kwargs),
        "stream": bool(kwargs.get("stream")),
        "call_stack": stack_summary(),
    }


def _dump(obj):
    try:
        return obj.model_dump(mode="json")
    except Exception:
        return str(obj)


def _finish_payload(payload: dict, start: float) -> dict:
    payload["duration_ms"] = (time.monotonic() - start) * 1000
    return payload


def _record_response(payload: dict, result) -> None:
    data = _dump(result)
    payload["response"] = data
    payload["usage"] = data.get("usage") if isinstance(data, dict) else None
    _state.emit("llm_call", payload, call_id=_events.new_id())


def _record_error(payload: dict, exc: BaseException) -> None:
    payload["error"] = {"type": type(exc).__name__, "message": str(exc)}
    _state.emit("llm_call", payload, call_id=_events.new_id())


def _make_sync_wrapper(api: str):
    def wrapper(wrapped, instance, args, kwargs):
        if not _state.is_configured():
            return wrapped(*args, **kwargs)
        payload = _base_payload(api, kwargs)
        start = time.monotonic()
        try:
            result = wrapped(*args, **kwargs)
        except Exception as exc:
            _record_error(_finish_payload(payload, start), exc)
            raise
        _finish_payload(payload, start)
        if payload["stream"]:
            return _StreamProxy(result, payload, api)
        _record_response(payload, result)
        return result

    return wrapper


def _make_async_wrapper(api: str):
    async def wrapper(wrapped, instance, args, kwargs):
        if not _state.is_configured():
            return await wrapped(*args, **kwargs)
        payload = _base_payload(api, kwargs)
        start = time.monotonic()
        try:
            result = await wrapped(*args, **kwargs)
        except Exception as exc:
            _record_error(_finish_payload(payload, start), exc)
            raise
        _finish_payload(payload, start)
        if payload["stream"]:
            return _AsyncStreamProxy(result, payload, api)
        _record_response(payload, result)
        return result

    return wrapper


class _StreamRecorderMixin:
    """Shared chunk accounting; subclasses only differ in (a)sync plumbing."""

    def _self_init(self, payload: dict, api: str) -> None:
        self._self_payload = payload
        self._self_api = api
        self._self_chunks: list = []
        self._self_done = False

    def _self_add(self, chunk) -> None:
        self._self_chunks.append(_dump(chunk))

    def _self_finish(self) -> None:
        if self._self_done:
            return
        self._self_done = True
        payload = self._self_payload
        payload["response"] = _assemble_chat(self._self_chunks)
        payload["usage"] = payload["response"].get("usage")
        _state.emit("llm_call", payload, call_id=_events.new_id())


class _StreamProxy(wrapt.ObjectProxy, _StreamRecorderMixin):
    def __init__(self, wrapped, payload: dict, api: str):
        super().__init__(wrapped)
        self._self_init(payload, api)

    def __iter__(self):
        try:
            for chunk in self.__wrapped__:
                self._self_add(chunk)
                yield chunk
        finally:
            self._self_finish()

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


class _AsyncStreamProxy(wrapt.ObjectProxy, _StreamRecorderMixin):
    def __init__(self, wrapped, payload: dict, api: str):
        super().__init__(wrapped)
        self._self_init(payload, api)

    async def __aiter__(self):
        try:
            async for chunk in self.__wrapped__:
                self._self_add(chunk)
                yield chunk
        finally:
            self._self_finish()

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


def _assemble_chat(chunks: list) -> dict:
    """Reduce chat.completion.chunk dicts into one response-like summary."""
    content: dict[int, str] = {}
    finish_reasons: dict[int, str] = {}
    usage = None
    model = None
    response_id = None
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        model = chunk.get("model") or model
        response_id = chunk.get("id") or response_id
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            index = choice.get("index", 0)
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content[index] = content.get(index, "") + delta["content"]
            if choice.get("finish_reason"):
                finish_reasons[index] = choice["finish_reason"]
    return {
        "object": "chat.completion.assembled",
        "id": response_id,
        "model": model,
        "content": {str(i): text for i, text in content.items()},
        "finish_reasons": {str(i): r for i, r in finish_reasons.items()},
        "usage": usage,
        "chunk_count": len(chunks),
    }
