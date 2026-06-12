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
