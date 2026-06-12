import httpx
import openai
import pytest
import respx

CHAT_URL = "https://api.openai.com/v1/chat/completions"
MESSAGES = [{"role": "user", "content": "Say hello"}]
SSE_HEADERS = {"content-type": "text/event-stream"}


@pytest.fixture
def async_client():
    return openai.AsyncOpenAI(api_key="test-key")


@respx.mock
async def test_async_records_schema_valid_event(
    capture, async_client, validate_event, chat_response_json
):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(200, json=chat_response_json))
    await async_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)
    (event,) = capture()
    validate_event(event)
    assert event["payload"]["usage"]["total_tokens"] == 12
    assert event["payload"]["request"]["messages"] == MESSAGES


@respx.mock
async def test_async_error_recorded_and_reraised(capture, async_client):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500, json={"error": {"message": "boom"}}))
    with pytest.raises(openai.APIStatusError):
        await async_client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)
    (event,) = capture()
    assert event["payload"]["error"]["type"] == "InternalServerError"


@respx.mock
async def test_async_stream_assembles_content(
    capture, async_client, validate_event, chat_stream_body
):
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=chat_stream_body)
    )
    stream = await async_client.chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, stream=True
    )
    chunks = [c async for c in stream]
    assert len(chunks) == 4
    (event,) = capture()
    validate_event(event)
    assert event["payload"]["response"]["content"]["0"] == "Hello world"
    assert event["payload"]["usage"]["total_tokens"] == 11
