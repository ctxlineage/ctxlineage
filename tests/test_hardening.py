import builtins

import httpx
import pytest
import respx

import ctxlineage
from ctxlineage._events import EventWriter
from ctxlineage._instrument import openai_patch

CHAT_URL = "https://api.openai.com/v1/chat/completions"
MESSAGES_URL = "https://api.anthropic.com/v1/messages"
SSE_HEADERS = {"content-type": "text/event-stream"}
MESSAGES = [{"role": "user", "content": "Say hello"}]


@respx.mock
def test_write_failure_does_not_break_call(capture, openai_client, chat_response_json, monkeypatch):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_response_json))

    def boom(self, event):
        raise OSError("disk full")

    monkeypatch.setattr(EventWriter, "write", boom)
    with pytest.warns(RuntimeWarning, match="ctxlineage"):
        resp = openai_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)
    assert resp.choices[0].message.content == "Hello there!"


def test_install_returns_false_without_openai(monkeypatch):
    monkeypatch.setattr(openai_patch, "_PATCHED", False)
    real_import = builtins.__import__

    def no_openai(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("No module named 'openai'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_openai)
    assert openai_patch.install() is False


def test_init_succeeds_without_openai(tmp_path, monkeypatch):
    monkeypatch.setattr(openai_patch, "_PATCHED", False)
    real_import = builtins.__import__

    def no_openai(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("No module named 'openai'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_openai)
    import ctxlineage._instrument as instrument

    monkeypatch.setattr(instrument, "_installed_providers", None)
    ctxlineage.init(tmp_path)  # must not raise


@respx.mock
def test_double_init_records_once(tmp_path, openai_client, chat_response_json):
    ctxlineage.init(tmp_path)
    ctxlineage.init(tmp_path)
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_response_json))
    openai_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert len(lines) == 1


@respx.mock
def test_poisoned_assembler_does_not_break_stream_consumer(
    capture, anthropic_client, messages_stream_body
):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=messages_stream_body)
    )
    stream = anthropic_client.messages.create(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES, stream=True
    )

    def boom(chunks):
        raise ValueError("malformed chunk shape")

    stream._self_assemble = boom
    with pytest.warns(RuntimeWarning, match="ctxlineage"):
        chunks = list(stream)  # exhausting the stream must not raise into the consumer
    assert len(chunks) == 7
    assert capture() == []  # the recording is lost, the host app is not


def test_stream_manager_without_raw_stream_still_returns_stream():
    from ctxlineage._instrument.anthropic_patch import _ManagerProxy

    class PlainStream:
        pass  # nothing to swap: reading _raw_stream raises AttributeError

    class Manager:
        def __enter__(self):
            return PlainStream()

        def __exit__(self, *exc):
            return False

    proxy = _ManagerProxy(Manager(), {"stream": True})
    with pytest.warns(RuntimeWarning, match="not be recorded"):
        with proxy as stream:
            assert isinstance(stream, PlainStream)  # unwrapped, but working
