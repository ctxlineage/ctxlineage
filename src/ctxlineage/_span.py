"""Explicit span/tag API: label context elements so the report can attribute them.

Optional by design (PLAN.md §3): nothing here is required for capture to work —
tagging only enriches the report. Everything is a silent no-op before init().

Span context propagates like contextvars do: each async task sees its own
span, but NEW THREADS DO NOT inherit the caller's span — propagate manually
with contextvars.copy_context() when fanning work out to threads.
"""

from __future__ import annotations

import contextvars

from ctxlineage import _events, _state

_current: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "ctxlineage_span", default=None
)


def current() -> Span | None:
    """The innermost active span in the current context, if any.

    Async tasks are isolated; new threads start with no span (contextvars
    semantics) — see the module docstring for cross-thread propagation.
    """
    return _current.get()


def current_id() -> str | None:
    """span_id of the active span, or None."""
    active = _current.get()
    return active.span_id if active else None


def _stringify(content) -> str:
    if isinstance(content, str):
        return content
    try:
        return _events.json_str(content)
    except Exception:
        return str(content)


class Span:
    """Handle yielded by `with ctxlineage.span(...)`; use `.tag()` to label context."""

    def __init__(self, name: str):
        self.name = name
        self.span_id = _events.new_id()
        self._token: contextvars.Token | None = None

    def tag(
        self, name: str, content, *, source: str | None = None, transform: str | None = None
    ) -> None:
        if not _state.is_configured():
            return  # true no-op: don't pay serialization for a discarded event
        payload: dict = {"name": name, "content": _stringify(content)}
        if source is not None:
            payload["source"] = source
        if transform is not None:
            payload["transform"] = transform
        _state.emit("tag", payload, span_id=self.span_id)

    def __enter__(self) -> Span:
        self._token = _current.set(self)
        _state.emit("span_start", {"name": self.name}, span_id=self.span_id)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _state.emit("span_end", {"name": self.name}, span_id=self.span_id)
        if self._token is not None:
            _current.reset(self._token)
            self._token = None
        return False


def span(name: str) -> Span:
    """Open a named span; LLM calls inside it are attributed to it in the report."""
    return Span(name)
