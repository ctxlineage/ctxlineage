"""Process-level capture state: one writer + one session per process."""

from __future__ import annotations

import os
import warnings

from ctxlineage._events import EventWriter, make_event, new_id

_writer: EventWriter | None = None
_session_id: str | None = None
_redact_fields: list[str] = []
_warned_keys: set[str] = set()


def warn_once(key: str, message: str) -> None:
    """Warn once per key per process; recording problems must never spam or raise."""
    if key in _warned_keys:
        return
    _warned_keys.add(key)
    warnings.warn(message, RuntimeWarning, stacklevel=3)


def init(
    directory: str | os.PathLike | None = None,
    *,
    redact_fields: list[str] | None = None,
) -> None:
    """Start capturing LLM calls to <directory>/events.jsonl.

    Directory resolution: argument > CTXLINEAGE_DIR env var > ./.ctxlineage.
    Safe to call more than once; the first call wins (one session per process).

    redact_fields: dotted payload paths (e.g. "request.messages.content")
    whose values are replaced with "[redacted]" before events are written —
    the masked content never reaches disk. A list mid-path applies the rest
    of the path to every item. Note this is irreversible and blinds segment
    matching for the masked text; prefer `ctxlineage report --redact` unless
    the log itself must stay clean.
    """
    global _writer, _session_id, _redact_fields
    if _writer is None:
        resolved = directory or os.environ.get("CTXLINEAGE_DIR") or ".ctxlineage"
        _writer = EventWriter(resolved)
        _session_id = new_id()
        _redact_fields = list(redact_fields or [])
    try:
        from ctxlineage._instrument import install
    except ImportError:  # instrumentation package not present (build subset)
        return
    install()


def is_configured() -> bool:
    return _writer is not None


def events_path():
    """Where capture is writing, or None when unconfigured.

    Lets a reader in this process find the log without being told: the pytest
    plugin uses it to honour an app that called init() itself instead of
    overriding the directory the app chose.
    """
    return _writer.path if _writer is not None else None


def session_id() -> str | None:
    return _session_id


_UNSET = object()


def emit(event_type: str, payload: dict, *, call_id: str | None = None, span_id=_UNSET) -> bool:
    """Record one event. Returns False (never raises) when unconfigured or on failure.

    span_id defaults to the currently active span, so every recorder (including
    future SDK patches) gets span attribution without threading it through.
    Pass an explicit value to override, e.g. stream proxies that captured the
    span at call time.
    """
    if _writer is None or _session_id is None:
        return False
    if span_id is _UNSET:
        from ctxlineage import _span  # emit-time import avoids a module cycle

        span_id = _span.current_id()
    if _redact_fields:
        from ctxlineage import _redact

        try:
            payload = _redact.mask_payload(payload, _redact_fields)
        except Exception as exc:
            # writing the unmasked event would leak what the user asked to
            # hide — dropping the event is the lesser failure
            warn_once(
                "redact",
                f"ctxlineage: failed to redact event ({exc!r}); event dropped",
            )
            return False
    try:
        _writer.write(
            make_event(event_type, _session_id, payload, span_id=span_id, call_id=call_id)
        )
        return True
    except Exception as exc:
        warn_once(
            "emit",
            f"ctxlineage: failed to record event ({exc!r}); further failures will be silent",
        )
        return False


def _reset() -> None:
    """Test helper: forget writer/session. Cannot un-patch SDKs."""
    global _writer, _session_id, _redact_fields
    _writer = None
    _session_id = None
    _redact_fields = []
    _warned_keys.clear()
