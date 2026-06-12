"""Summarize the user's call stack: where in *their* code an LLM call came from."""

from __future__ import annotations

import sys
from pathlib import Path

_PKG_DIR = str(Path(__file__).resolve().parent)


def _is_user_frame(filename: str) -> bool:
    if filename.startswith("<"):  # <frozen ...>, <string>, REPL
        return False
    resolved = str(Path(filename).resolve())
    if resolved.startswith(_PKG_DIR):
        return False
    if "site-packages" in resolved or "dist-packages" in resolved:
        return False
    if resolved.startswith(sys.base_prefix) or resolved.startswith(sys.prefix):
        return False
    return True


def stack_summary(limit: int = 5) -> list[str]:
    """Up to `limit` user frames as "<file>:<function>:<lineno>", innermost first."""
    frames: list[str] = []
    frame = sys._getframe(1)
    while frame is not None and len(frames) < limit:
        code = frame.f_code
        if _is_user_frame(code.co_filename):
            frames.append(f"{Path(code.co_filename).name}:{code.co_name}:{frame.f_lineno}")
        frame = frame.f_back
    return frames
