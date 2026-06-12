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
