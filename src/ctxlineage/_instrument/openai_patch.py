"""openai SDK instrumentation (Chat Completions + Responses API).

Shared design constraints live in _common.py; this module only knows the
openai patch targets and how to reduce its stream chunks.
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
            import openai  # noqa: F401
        except ImportError:
            return False
        _patch()
        _PATCHED = True
        return True


def _patch() -> None:
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
    try:  # Responses API: absent in older SDKs
        wrapt.wrap_function_wrapper(
            "openai.resources.responses", "Responses.create", _make_sync_wrapper("responses")
        )
        wrapt.wrap_function_wrapper(
            "openai.resources.responses",
            "AsyncResponses.create",
            _make_async_wrapper("responses"),
        )
    except (ImportError, AttributeError):
        pass


def _assembler_for(api: str):
    return _assemble_responses if api == "responses" else _assemble_chat


def _make_sync_wrapper(api: str):
    def wrapper(wrapped, instance, args, kwargs):
        if not _state.is_configured():
            return wrapped(*args, **kwargs)
        payload = base_payload("openai", api, kwargs)
        start = time.monotonic()
        try:
            result = wrapped(*args, **kwargs)
        except Exception as exc:
            record_error(finish_payload(payload, start), exc)
            raise
        finish_payload(payload, start)
        if payload["stream"]:
            # streams may be consumed after the span exits: bind the span now
            return StreamProxy(result, payload, _assembler_for(api), _span.current_id())
        record_response(payload, result)
        return result

    return wrapper


def _make_async_wrapper(api: str):
    async def wrapper(wrapped, instance, args, kwargs):
        if not _state.is_configured():
            return await wrapped(*args, **kwargs)
        payload = base_payload("openai", api, kwargs)
        start = time.monotonic()
        try:
            result = await wrapped(*args, **kwargs)
        except Exception as exc:
            record_error(finish_payload(payload, start), exc)
            raise
        finish_payload(payload, start)
        if payload["stream"]:
            # streams may be consumed after the span exits: bind the span now
            return AsyncStreamProxy(result, payload, _assembler_for(api), _span.current_id())
        record_response(payload, result)
        return result

    return wrapper


def _assemble_responses(chunks: list) -> dict:
    """Reduce Responses API stream events into one response-like summary.

    The final `response.completed` event already carries the full response
    (incl. usage); the concatenated output_text covers aborted streams.
    """
    output_text = ""
    final = None
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        kind = chunk.get("type", "")
        if kind == "response.output_text.delta" and chunk.get("delta"):
            output_text += chunk["delta"]
        elif kind == "response.completed" and isinstance(chunk.get("response"), dict):
            final = chunk["response"]
    return {
        "object": "response.assembled",
        "id": (final or {}).get("id"),
        "model": (final or {}).get("model"),
        "output_text": output_text,
        "final": final,
        "usage": (final or {}).get("usage"),
        "chunk_count": len(chunks),
    }


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
