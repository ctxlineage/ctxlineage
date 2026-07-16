import asyncio
import json

import httpx
import respx

import ctxlineage
from ctxlineage import _span

CHAT_URL = "https://api.openai.com/v1/chat/completions"
MESSAGES = [{"role": "user", "content": "Say hello"}]
SSE_HEADERS = {"content-type": "text/event-stream"}


def test_span_emits_start_and_end(capture, validate_event):
    with ctxlineage.span("answer_user_query"):
        pass
    events = capture()
    assert [e["event_type"] for e in events] == ["span_start", "span_end"]
    for event in events:
        validate_event(event)
        assert event["payload"]["name"] == "answer_user_query"
    assert events[0]["span_id"] == events[1]["span_id"]


def test_tag_event_content_and_provenance(capture, validate_event):
    with ctxlineage.span("qa") as sp:
        sp.tag("rag_chunks", ["doc a", "doc b"], source="qdrant:products_v2", transform="top_k")
        sp.tag("system", "You are helpful.")
    tags = [e for e in capture() if e["event_type"] == "tag"]
    assert len(tags) == 2
    for tag in tags:
        validate_event(tag)
        assert tag["span_id"] == sp.span_id
    rag = tags[0]["payload"]
    assert rag["name"] == "rag_chunks"
    assert json.loads(rag["content"]) == ["doc a", "doc b"]
    assert rag["source"] == "qdrant:products_v2"
    assert rag["transform"] == "top_k"
    assert tags[1]["payload"]["content"] == "You are helpful."
    assert "source" not in tags[1]["payload"]


def test_nested_spans_restore_outer(capture):
    with ctxlineage.span("outer") as outer:
        assert _span.current() is outer
        with ctxlineage.span("inner") as inner:
            assert _span.current() is inner
        assert _span.current() is outer
    assert _span.current() is None


def test_span_before_init_is_silent(tmp_path):
    with ctxlineage.span("x") as sp:
        sp.tag("a", "b")
    assert not (tmp_path / "events.jsonl").exists()


def test_tag_skips_serialization_when_unconfigured():
    class Unserializable:
        def __iter__(self):  # would explode if _stringify iterated it
            raise RuntimeError("must not be serialized")

    with ctxlineage.span("x") as sp:
        sp.tag("chunks", Unserializable())  # must not raise, must not serialize


def test_direct_emit_inside_span_defaults_span_id(capture):
    from ctxlineage import _state

    with ctxlineage.span("qa") as sp:
        _state.emit(
            "llm_call",
            {"provider": "other", "api": "messages", "request": {}},
            call_id="c1",
        )
    (event,) = [e for e in capture() if e["event_type"] == "llm_call"]
    assert event["span_id"] == sp.span_id


async def test_async_tasks_have_isolated_spans(capture):
    seen = {}

    async def work(name):
        with ctxlineage.span(name) as sp:
            await asyncio.sleep(0.01)
            seen[name] = (sp.span_id, _span.current().span_id)

    await asyncio.gather(work("task-a"), work("task-b"))
    assert seen["task-a"][0] == seen["task-a"][1]
    assert seen["task-b"][0] == seen["task-b"][1]
    assert seen["task-a"][0] != seen["task-b"][0]


@respx.mock
def test_llm_call_inside_span_carries_span_id(capture, openai_client, chat_response_json):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_response_json))
    with ctxlineage.span("qa") as sp:
        openai_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)
    calls = [e for e in capture() if e["event_type"] == "llm_call"]
    assert len(calls) == 1
    assert calls[0]["span_id"] == sp.span_id


@respx.mock
def test_llm_call_outside_span_has_null_span_id(capture, openai_client, chat_response_json):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_response_json))
    openai_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)
    (call,) = [e for e in capture() if e["event_type"] == "llm_call"]
    assert call["span_id"] is None


@respx.mock
def test_stream_consumed_after_span_exit_keeps_span_id(capture, openai_client, chat_stream_body):
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=chat_stream_body)
    )
    with ctxlineage.span("streamer") as sp:
        stream = openai_client.chat.completions.create(
            model="gpt-4o-mini", messages=MESSAGES, stream=True
        )
    list(stream)  # consumed after the span closed
    calls = [e for e in capture() if e["event_type"] == "llm_call"]
    assert calls[0]["span_id"] == sp.span_id
