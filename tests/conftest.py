import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "events.v1.schema.json"
_VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))


def _validate(event: dict) -> None:
    """Raise jsonschema.ValidationError if the event does not match schema v1."""
    _VALIDATOR.validate(event)


@pytest.fixture
def validate_event():
    return _validate


@pytest.fixture(autouse=True)
def _offline_token_estimation(monkeypatch):
    """Keep the suite hermetic: never let tiktoken hit the network in tests."""
    from ctxlineage._report import tokens

    monkeypatch.setattr(tokens, "_encoding_for", lambda model: None)


@pytest.fixture(autouse=True)
def _reset_ctxlineage_state():
    from ctxlineage import _state

    _state._reset()
    yield
    _state._reset()


_CHAT_COMPLETION_RESPONSE = {
    "id": "chatcmpl-test1",
    "object": "chat.completion",
    "created": 1765500000,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello there!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
}


@pytest.fixture
def chat_response_json():
    return dict(_CHAT_COMPLETION_RESPONSE)


def _sse(*payloads) -> bytes:
    body = b""
    for p in payloads:
        body += b"data: " + json.dumps(p).encode() + b"\n\n"
    return body + b"data: [DONE]\n\n"


def _chat_chunk(delta=None, finish_reason=None, usage=None):
    return {
        "id": "chatcmpl-test1",
        "object": "chat.completion.chunk",
        "created": 1765500000,
        "model": "gpt-4o-mini",
        "choices": []
        if delta is None and finish_reason is None
        else [{"index": 0, "delta": delta or {}, "finish_reason": finish_reason}],
        "usage": usage,
    }


@pytest.fixture
def chat_stream_body():
    """SSE stream: 'Hello' + ' world', then finish, then a usage-only chunk."""
    return _sse(
        _chat_chunk(delta={"role": "assistant", "content": "Hello"}),
        _chat_chunk(delta={"content": " world"}),
        _chat_chunk(finish_reason="stop"),
        _chat_chunk(usage={"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11}),
    )


@pytest.fixture
def capture(tmp_path):
    """init() into tmp_path and return a reader for the events written there."""
    import ctxlineage

    ctxlineage.init(tmp_path)

    def read_events():
        path = tmp_path / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    return read_events


@pytest.fixture
def openai_client():
    import openai

    return openai.OpenAI(api_key="test-key")


_MESSAGES_RESPONSE = {
    "id": "msg_test1",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [{"type": "text", "text": "Hello there!"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 9, "output_tokens": 3},
}


@pytest.fixture
def messages_response_json():
    return dict(_MESSAGES_RESPONSE)


def _anthropic_sse(*events) -> bytes:
    """Anthropic SSE frames are named events; the SDK dispatches on the name."""
    return b"".join(
        b"event: " + e["type"].encode() + b"\ndata: " + json.dumps(e).encode() + b"\n\n"
        for e in events
    )


@pytest.fixture
def messages_stream_body():
    """'Hello' + ' world' text deltas, final usage via message_delta."""
    return _anthropic_sse(
        {
            "type": "message_start",
            "message": {
                "id": "msg_test1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 9, "output_tokens": 1},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": " world"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 2},
        },
        {"type": "message_stop"},
    )


@pytest.fixture
def chat_error_stream_body():
    """One good chunk, then an in-band error frame the SDK raises on."""
    return _sse(
        _chat_chunk(delta={"role": "assistant", "content": "Hello"}),
        {"error": {"message": "server overloaded"}},
    )


@pytest.fixture
def messages_error_stream_body():
    """Partial text, then an in-band `event: error` frame (raises mid-stream)."""
    return _anthropic_sse(
        {
            "type": "message_start",
            "message": {
                "id": "msg_test1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 9, "output_tokens": 1},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        },
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )


@pytest.fixture
def anthropic_client():
    import anthropic

    # max_retries=0: keep the 5xx error tests from sleeping through retry backoff
    return anthropic.Anthropic(api_key="test-key", max_retries=0)


@pytest.fixture
def valid_llm_call_event():
    return {
        "schema_version": 1,
        "event_type": "llm_call",
        "session_id": "abc123",
        "span_id": None,
        "call_id": "call456",
        "timestamp": "2026-06-12T00:00:00+00:00",
        "payload": {
            "provider": "openai",
            "api": "chat.completions",
            "request": {"model": "gpt-4o-mini", "messages": []},
            "stream": False,
            "duration_ms": 12.5,
            "call_stack": ["app.py:main:10"],
        },
    }
