import builtins
import gc
import threading
import time
import types

import httpx
import pytest
import respx

import ctxlineage
from ctxlineage._events import EventWriter
from ctxlineage._instrument import anthropic_patch, openai_patch
from ctxlineage._instrument._common import base_payload

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

    stream._self_record.assemble = boom
    with pytest.warns(RuntimeWarning, match="ctxlineage"):
        chunks = list(stream)  # exhausting the stream must not raise into the consumer
    assert len(chunks) == 7
    assert capture() == []  # the recording is lost, the host app is not


def _mock_openai_stream(body):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, headers=SSE_HEADERS, content=body))


# --- #34 item 1: streams the host drops without ever finishing or closing ---
# The request already reached the provider, so losing the event would hide a
# call that really consumed context. Only a finalizer can catch these.


@respx.mock
def test_untouched_stream_records_on_gc(capture, openai_client, chat_stream_body):
    _mock_openai_stream(chat_stream_body)
    # never iterated, closed, or entered: there is no other emit path
    openai_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES, stream=True)
    gc.collect()
    (event,) = capture()
    assert event["payload"]["abandoned"] is True
    assert event["payload"]["request"]["messages"] == MESSAGES
    assert event["payload"]["response"]["chunk_count"] == 0


@respx.mock
def test_next_then_dropped_stream_records_partial_on_gc(capture, openai_client, chat_stream_body):
    _mock_openai_stream(chat_stream_body)
    stream = openai_client.chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, stream=True
    )
    next(stream)  # __next__ directly: no __iter__ generator whose finally could record
    del stream
    gc.collect()
    (event,) = capture()
    assert event["payload"]["abandoned"] is True
    assert event["payload"]["response"]["content"]["0"] == "Hello"  # partial, but kept


@respx.mock
def test_completed_stream_is_not_flagged_and_records_once(capture, openai_client, chat_stream_body):
    _mock_openai_stream(chat_stream_body)
    stream = openai_client.chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, stream=True
    )
    list(stream)
    del stream
    gc.collect()  # the finalizer must not double-record what the host finished
    (event,) = capture()
    assert "abandoned" not in event["payload"]
    assert event["payload"]["response"]["content"]["0"] == "Hello world"


@respx.mock
async def test_untouched_async_stream_records_on_gc(capture, chat_stream_body):
    import openai

    client = openai.AsyncOpenAI(api_key="test-key")
    _mock_openai_stream(chat_stream_body)
    await client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES, stream=True)
    gc.collect()
    (event,) = capture()
    assert event["payload"]["abandoned"] is True


# --- #34 item 4: the recorded request must not follow the host's mutations ---


@respx.mock
def test_request_snapshot_survives_host_mutation_mid_stream(
    capture, openai_client, chat_stream_body
):
    _mock_openai_stream(chat_stream_body)
    messages = [{"role": "user", "content": "Say hello"}]
    stream = openai_client.chat.completions.create(
        model="gpt-4o-mini", messages=messages, stream=True
    )
    # a chat loop appending to its own list before the stream ends must not
    # rewrite what we record as the request that was actually sent
    messages.append({"role": "assistant", "content": "a later turn"})
    messages[0]["content"] = "tampered"
    list(stream)
    (event,) = capture()
    assert event["payload"]["request"]["messages"] == [{"role": "user", "content": "Say hello"}]


def test_base_payload_snapshots_each_key_independently():
    """One un-deepcopyable kwarg must not cost `messages` its snapshot."""
    messages = [{"role": "user", "content": "hi"}]
    module = types.ModuleType("uncopyable")  # deepcopy raises TypeError on modules
    payload = base_payload("openai", "chat.completions", {"messages": messages, "extra": module})
    messages[0]["content"] = "mutated"
    assert payload["request"]["messages"] == [{"role": "user", "content": "hi"}]
    assert payload["request"]["extra"] is module  # fell back to the live reference


def _run_concurrently(target, n=8):
    """Line n threads up on a barrier so they all hit `target` together."""
    barrier = threading.Barrier(n)

    def run():
        barrier.wait()
        target()

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_concurrent_install_patches_each_provider_once(monkeypatch):
    """The install lock makes concurrent init() calls patch each provider once."""
    import ctxlineage._instrument as instrument

    calls = {"openai": 0, "anthropic": 0}

    def make_stub(name):
        def stub():
            time.sleep(0.01)  # widen the window a broken check-then-act would lose
            calls[name] += 1
            return True

        return stub

    monkeypatch.setattr(openai_patch, "install", make_stub("openai"))
    monkeypatch.setattr(anthropic_patch, "install", make_stub("anthropic"))
    monkeypatch.setattr(instrument, "_installed_providers", None)

    results = []
    _run_concurrently(lambda: results.append(instrument.install()))

    assert calls == {"openai": 1, "anthropic": 1}
    assert all(r == ["openai", "anthropic"] for r in results)  # every caller sees the same list


def test_concurrent_openai_install_wraps_once(monkeypatch):
    """openai_patch's own _PATCHED lock: the wrap step runs once under a race."""
    patched = []

    def fake_patch():
        time.sleep(0.01)  # hold the critical section open to expose a missing lock
        patched.append(1)

    monkeypatch.setattr(openai_patch, "_PATCHED", False)
    monkeypatch.setattr(openai_patch, "_patch", fake_patch)

    _run_concurrently(openai_patch.install)

    assert len(patched) == 1


def test_concurrent_anthropic_install_wraps_each_method_once(monkeypatch):
    """anthropic_patch's own _PATCHED lock: each method is wrapped exactly once."""
    wrapped = []

    class _FakeWrapt:
        @staticmethod
        def wrap_function_wrapper(module, name, wrapper):
            time.sleep(0.005)  # hold the critical section open to expose a missing lock
            wrapped.append(name)

    monkeypatch.setattr(anthropic_patch, "wrapt", _FakeWrapt)
    monkeypatch.setattr(anthropic_patch, "_PATCHED", False)

    _run_concurrently(anthropic_patch.install)

    assert sorted(wrapped) == [
        "AsyncMessages.create",
        "AsyncMessages.stream",
        "Messages.create",
        "Messages.stream",
    ]


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
