"""Token estimation for report display.

Provider-reported `usage` is always preferred where it exists; these estimates
only apportion tokens across segments. tiktoken is used when it works, and any
failure (offline, unknown model, missing package) falls back to chars/4 —
estimation must never break report generation.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=8)
def _encoding_for(model: str):
    try:
        import tiktoken

        try:
            return tiktoken.encoding_for_model(model)
        except KeyError:
            return tiktoken.get_encoding("o200k_base")
    except Exception:
        return None


def estimate_tokens(text: str, model: str) -> int:
    if not text:
        return 0
    try:
        encoding = _encoding_for(model)
        if encoding is not None:
            return len(encoding.encode(text, disallowed_special=()))
    except Exception:
        pass
    return max(1, len(text) // 4)
