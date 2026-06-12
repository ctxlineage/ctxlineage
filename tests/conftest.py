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
