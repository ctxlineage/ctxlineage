import pytest

from ctxlineage._report import tokens


@pytest.fixture
def force_fallback(monkeypatch):
    monkeypatch.setattr(tokens, "_encoding_for", lambda model: None)


def test_estimate_positive_for_nonempty(force_fallback):
    assert tokens.estimate_tokens("hello world", "gpt-4o-mini") >= 1


def test_estimate_zero_for_empty(force_fallback):
    assert tokens.estimate_tokens("", "gpt-4o-mini") == 0


def test_estimate_grows_with_length(force_fallback):
    short = tokens.estimate_tokens("word " * 10, "gpt-4o-mini")
    long = tokens.estimate_tokens("word " * 1000, "gpt-4o-mini")
    assert long > short * 50


def test_never_raises_on_weird_model(force_fallback):
    assert tokens.estimate_tokens("text", "some-future-model-9000") >= 1


def test_never_raises_when_tiktoken_blows_up(monkeypatch):
    def boom(model):
        raise RuntimeError("no network")

    monkeypatch.setattr(tokens, "_encoding_for", boom)
    assert tokens.estimate_tokens("some text here", "gpt-4o-mini") >= 1


def test_cjk_fallback_not_grossly_underestimated(force_fallback):
    japanese = "東京は日本の首都です。" * 10  # 110 chars
    estimate = tokens.estimate_tokens(japanese, "gpt-4o-mini")
    # real tokenizers put Japanese near 1 token/char; chars//4 (27) was ~2.6x low
    assert estimate >= len(japanese) * 0.8


def test_cyrillic_fallback_not_grossly_overestimated(force_fallback):
    russian = "Привет, как дела сегодня? " * 5  # ~130 chars, mostly Cyrillic
    estimate = tokens.estimate_tokens(russian, "gpt-4o-mini")
    # Cyrillic runs ~0.3-0.5 tokens/char in real tokenizers; the tier keeps the
    # estimate in a sane band instead of 1 token/char
    assert len(russian) * 0.25 <= estimate <= len(russian) * 0.8
