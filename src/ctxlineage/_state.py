"""Process-level capture state: one writer + one session per process."""

from __future__ import annotations

import os
import warnings

from ctxlineage._events import EventWriter, make_event, new_id

_writer: EventWriter | None = None
_session_id: str | None = None
_warned = False


def init(directory: str | os.PathLike | None = None) -> None:
    """Start capturing LLM calls to <directory>/events.jsonl.

    Directory resolution: argument > CTXLINEAGE_DIR env var > ./.ctxlineage.
    Safe to call more than once; the first call wins (one session per process).
    """
    global _writer, _session_id
    if _writer is None:
        resolved = directory or os.environ.get("CTXLINEAGE_DIR") or ".ctxlineage"
        _writer = EventWriter(resolved)
        _session_id = new_id()
    try:
        from ctxlineage._instrument import install
    except ImportError:  # instrumentation package not present (build subset)
        return
    install()


def is_configured() -> bool:
    return _writer is not None


def session_id() -> str | None:
    return _session_id


def emit(event_type: str, payload: dict, *, call_id: str | None = None, span_id: str | None = None) -> bool:
    """Record one event. Returns False (never raises) when unconfigured or on failure."""
    global _warned
    if _writer is None or _session_id is None:
        return False
    try:
        _writer.write(
            make_event(event_type, _session_id, payload, span_id=span_id, call_id=call_id)
        )
        return True
    except Exception as exc:
        if not _warned:
            _warned = True
            warnings.warn(
                f"ctxlineage: failed to record event ({exc!r}); further failures will be silent",
                RuntimeWarning,
                stacklevel=2,
            )
        return False


def _reset() -> None:
    """Test helper: forget writer/session. Cannot un-patch SDKs."""
    global _writer, _session_id, _warned
    _writer = None
    _session_id = None
    _warned = False
