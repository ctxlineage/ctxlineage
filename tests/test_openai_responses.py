import json

import httpx
import openai
import respx

RESPONSES_URL = "https://api.openai.com/v1/responses"
SSE_HEADERS = {"content-type": "text/event-stream"}

RESPONSE_JSON = {
    "id": "resp_test1",
    "object": "response",
    "created_at": 1765500000,
    "model": "gpt-4o-mini",
    "status": "completed",
    "error": None,
    "incomplete_details": None,
    "instructions": None,
    "metadata": None,
    "output": [
        {
            "type": "message",
            "id": "msg_1",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Hello there!", "annotations": []}],
        }
    ],
    "parallel_tool_calls": True,
    "temperature": 1.0,
    "tool_choice": "auto",
    "tools": [],
    "top_p": 1.0,
    "usage": {
        "input_tokens": 9,
        "output_tokens": 3,
        "total_tokens": 12,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    },
}


def _sse(*payloads) -> bytes:
    body = b""
    for p in payloads:
        body += b"data: " + json.dumps(p).encode() + b"\n\n"
    return body + b"data: [DONE]\n\n"


def _text_delta(delta, seq):
    return {
        "type": "response.output_text.delta",
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "delta": delta,
        "logprobs": [],
        "sequence_number": seq,
    }


def _stream_body():
    in_progress = {**RESPONSE_JSON, "status": "in_progress", "usage": None}
    return _sse(
        {"type": "response.created", "response": in_progress, "sequence_number": 0},
        _text_delta("Hello", 1),
        _text_delta(" world", 2),
        {"type": "response.completed", "response": RESPONSE_JSON, "sequence_number": 3},
    )


@respx.mock
def test_sync_records_event(capture, openai_client, validate_event):
    respx.post(RESPONSES_URL).mock(return_value=httpx.Response(200, json=RESPONSE_JSON))
    openai_client.responses.create(model="gpt-4o-mini", input="Say hello")
    (event,) = capture()
    validate_event(event)
    payload = event["payload"]
    assert payload["api"] == "responses"
    assert payload["request"]["input"] == "Say hello"
    assert payload["usage"]["total_tokens"] == 12


@respx.mock
def test_sync_stream_assembles_output_text(capture, openai_client, validate_event):
    respx.post(RESPONSES_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=_stream_body())
    )
    stream = openai_client.responses.create(model="gpt-4o-mini", input="Say hello", stream=True)
    events_seen = list(stream)
    assert len(events_seen) == 4
    (event,) = capture()
    validate_event(event)
    payload = event["payload"]
    assert payload["stream"] is True
    assert payload["response"]["output_text"] == "Hello world"
    assert payload["response"]["chunk_count"] == 4
    assert payload["usage"]["total_tokens"] == 12


@respx.mock
async def test_async_records_event(capture, validate_event):
    respx.post(RESPONSES_URL).mock(return_value=httpx.Response(200, json=RESPONSE_JSON))
    client = openai.AsyncOpenAI(api_key="test-key")
    await client.responses.create(model="gpt-4o-mini", input="Say hello")
    (event,) = capture()
    validate_event(event)
    assert event["payload"]["api"] == "responses"
