import httpx
import openai
import pytest
import respx

CHAT_URL = "https://api.openai.com/v1/chat/completions"
MESSAGES = [{"role": "user", "content": "Say hello"}]


@respx.mock
def test_records_one_schema_valid_event(capture, openai_client, validate_event, chat_response_json):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_response_json))
    openai_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)
    (event,) = capture()
    validate_event(event)
    assert event["event_type"] == "llm_call"


@respx.mock
def test_request_recorded_verbatim(capture, openai_client, chat_response_json):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_response_json))
    openai_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES, temperature=0.5)
    (event,) = capture()
    request = event["payload"]["request"]
    assert request["model"] == "gpt-4o-mini"
    assert request["messages"] == MESSAGES
    assert request["temperature"] == 0.5


@respx.mock
def test_response_and_usage_recorded(capture, openai_client, chat_response_json):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_response_json))
    openai_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)
    (event,) = capture()
    payload = event["payload"]
    assert payload["usage"]["total_tokens"] == 12
    assert payload["response"]["choices"][0]["message"]["content"] == "Hello there!"
    assert payload["stream"] is False
    assert payload["duration_ms"] >= 0
    assert payload["provider"] == "openai"
    assert payload["api"] == "chat.completions"


@respx.mock
def test_unknown_kwargs_pass_through(capture, openai_client, chat_response_json):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_response_json))
    openai_client.chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, metadata={"experiment": "x1"}
    )
    (event,) = capture()
    assert event["payload"]["request"]["metadata"] == {"experiment": "x1"}


@respx.mock
def test_call_stack_points_at_user_code(capture, openai_client, chat_response_json):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_response_json))

    def my_app_function():
        return openai_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)

    my_app_function()
    (event,) = capture()
    assert any(":my_app_function:" in frame for frame in event["payload"]["call_stack"])


@respx.mock
def test_api_error_recorded_and_reraised(capture, openai_client):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500, json={"error": {"message": "boom"}}))
    with pytest.raises(openai.APIStatusError):
        openai_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)
    (event,) = capture()
    assert "error" in event["payload"]
    assert "response" not in event["payload"]


@respx.mock
def test_without_init_call_succeeds_and_nothing_written(
    tmp_path, openai_client, chat_response_json
):
    import ctxlineage._instrument as instrument

    instrument.install()  # patched but state unconfigured
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_response_json))
    resp = openai_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)
    assert resp.choices[0].message.content == "Hello there!"
    assert not (tmp_path / "events.jsonl").exists()
