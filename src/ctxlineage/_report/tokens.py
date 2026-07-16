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
    return _fallback_estimate(text)


_CJK_RANGES = (
    (0x3040, 0x30FF),  # hiragana / katakana
    (0x3400, 0x4DBF),  # CJK ext A
    (0x4E00, 0x9FFF),  # CJK unified
    (0xAC00, 0xD7AF),  # hangul
    (0xF900, 0xFAFF),  # CJK compat
)


def _fallback_estimate(text: str) -> int:
    # offline heuristic, tiered by script: ~4 ASCII chars/token, ~1 token per
    # CJK char, ~2 chars/token for other non-ASCII (Cyrillic, Arabic, accents…)
    ascii_chars = cjk = other = 0
    for ch in text:
        cp = ord(ch)
        if cp < 128:
            ascii_chars += 1
        elif any(lo <= cp <= hi for lo, hi in _CJK_RANGES):
            cjk += 1
        else:
            other += 1
    return max(1, ascii_chars // 4 + cjk + other // 2)
