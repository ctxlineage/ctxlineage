"""Event construction and the append-only JSONL writer (schema v1)."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1


def new_id() -> str:
    return uuid.uuid4().hex


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_event(
    event_type: str,
    session_id: str,
    payload: dict,
    *,
    span_id: str | None = None,
    call_id: str | None = None,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "session_id": session_id,
        "span_id": span_id,
        "call_id": call_id,
        "timestamp": utc_now_iso(),
        "payload": payload,
    }


class EventWriter:
    """Append-only JSONL writer. One event per line, no buffering.

    Opens the file per write so a crash never loses buffered events and
    multiple processes can append to the same file (POSIX append mode).
    """

    def __init__(self, directory: str | os.PathLike):
        self._dir = Path(directory)
        self.path = self._dir / "events.jsonl"
        self._lock = threading.Lock()

    def write(self, event: dict) -> None:
        # default=str: non-JSON values (datetimes, pydantic leftovers, ...)
        # degrade to strings instead of losing the whole event.
        line = json.dumps(event, ensure_ascii=False, default=str)
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
