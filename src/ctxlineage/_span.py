"""Explicit span/tag API: label context elements so the report can attribute them.

Optional by design (PLAN.md §3): nothing here is required for capture to work —
tagging only enriches the report. Everything is a silent no-op before init().
"""

from __future__ import annotations

import contextvars
import json

from ctxlineage import _events, _state

_current: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "ctxlineage_span", default=None
)


def current() -> Span | None:
    """The innermost active span in this task/thread, if any."""
    return _current.get()


def _stringify(content) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
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
