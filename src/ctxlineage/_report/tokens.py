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
    # offline fallback: ~4 ASCII chars per token, but roughly 1 token per
    # non-ASCII char (CJK etc.) — plain len//4 undercounts Japanese ~3x
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return max(1, ascii_chars // 4 + (len(text) - ascii_chars))
