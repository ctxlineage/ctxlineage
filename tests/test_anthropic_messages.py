import anthropic
import httpx
import pytest
import respx

MESSAGES_URL = "https://api.anthropic.com/v1/messages"
MESSAGES = [{"role": "user", "content": "Say hello"}]


def _create(client, **kwargs):
    return client.messages.create(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES, **kwargs
    )


@respx.mock
def test_records_one_schema_valid_event(
    capture, anthropic_client, validate_event, messages_response_json
):
    respx.post(MESSAGES_URL).mock(return_value=httpx.Response(200, json=messages_response_json))
    _create(anthropic_client)
    (event,) = capture()
    validate_event(event)
    assert event["event_type"] == "llm_call"


@respx.mock
def test_request_recorded_verbatim(capture, anthropic_client, messages_response_json):
    respx.post(MESSAGES_URL).mock(return_value=httpx.Response(200, json=messages_response_json))
    _create(anthropic_client, temperature=0.5, metadata={"user_id": "u1"})
    (event,) = capture()
    request = event["payload"]["request"]
    assert request["model"] == "claude-sonnet-5"
    assert request["max_tokens"] == 64
    assert request["messages"] == MESSAGES
    assert request["temperature"] == 0.5
    assert request["metadata"] == {"user_id": "u1"}


@respx.mock
def test_response_and_usage_recorded(capture, anthropic_client, messages_response_json):
    respx.post(MESSAGES_URL).mock(return_value=httpx.Response(200, json=messages_response_json))
    _create(anthropic_client)
    (event,) = capture()
    payload = event["payload"]
    assert payload["usage"]["output_tokens"] == 3
    assert payload["response"]["content"][0]["text"] == "Hello there!"
    assert payload["stream"] is False
    assert payload["duration_ms"] >= 0
    assert payload["provider"] == "anthropic"
    assert payload["api"] == "messages"


@respx.mock
def test_call_stack_points_at_user_code(capture, anthropic_client, messages_response_json):
    respx.post(MESSAGES_URL).mock(return_value=httpx.Response(200, json=messages_response_json))

    def my_app_function():
        return _create(anthropic_client)

    my_app_function()
    (event,) = capture()
    assert any(":my_app_function:" in frame for frame in event["payload"]["call_stack"])


@respx.mock
def test_api_error_recorded_and_reraised(capture, anthropic_client):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(500, json={"error": {"message": "boom"}})
    )
    with pytest.raises(anthropic.APIStatusError):
        _create(anthropic_client)
    (event,) = capture()
    assert "error" in event["payload"]
    assert "response" not in event["payload"]


@respx.mock
def test_without_init_call_succeeds_and_nothing_written(
    tmp_path, anthropic_client, messages_response_json
):
    import ctxlineage._instrument as instrument

    instrument.install()  # patched but state unconfigured
    respx.post(MESSAGES_URL).mock(return_value=httpx.Response(200, json=messages_response_json))
    resp = _create(anthropic_client)
    assert resp.content[0].text == "Hello there!"
    assert not (tmp_path / "events.jsonl").exists()
