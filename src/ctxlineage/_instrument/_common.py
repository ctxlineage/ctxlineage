"""Shared plumbing for SDK patch modules.

Every provider patch stays thin: record kwargs wholesale, dump responses with
model_dump, pass unknown fields through untouched. Recording must never break
the host call: wrappers no-op when ctxlineage is unconfigured, and
_state.emit() already swallows write failures.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import wrapt

from ctxlineage import _events, _state
from ctxlineage._stack import stack_summary


def base_payload(provider: str, api: str, kwargs: dict) -> dict:
    return {
        "provider": provider,
        "api": api,
        "request": dict(kwargs),
        "stream": bool(kwargs.get("stream")),
        "call_stack": stack_summary(),
    }


def dump(obj):
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


class StreamRecorderMixin:
    """Shared chunk accounting; subclasses only differ in (a)sync plumbing."""

    def _self_init(self, payload: dict, assemble: Callable[[list], dict], span_id=None) -> None:
        self._self_payload = payload
        self._self_assemble = assemble
        self._self_span_id = span_id
        self._self_chunks: list = []
        self._self_done = False

    def _self_add(self, chunk) -> None:
        self._self_chunks.append(dump(chunk))

    def _self_finish(self) -> None:
        if self._self_done:
            return
        self._self_done = True
        payload = self._self_payload
        payload["response"] = self._self_assemble(self._self_chunks)
        payload["usage"] = payload["response"].get("usage")
        _state.emit("llm_call", payload, call_id=_events.new_id(), span_id=self._self_span_id)


class StreamProxy(wrapt.ObjectProxy, StreamRecorderMixin):
    def __init__(self, wrapped, payload: dict, assemble: Callable[[list], dict], span_id=None):
        super().__init__(wrapped)
        self._self_init(payload, assemble, span_id)

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


class AsyncStreamProxy(wrapt.ObjectProxy, StreamRecorderMixin):
    def __init__(self, wrapped, payload: dict, assemble: Callable[[list], dict], span_id=None):
        super().__init__(wrapped)
        self._self_init(payload, assemble, span_id)

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
