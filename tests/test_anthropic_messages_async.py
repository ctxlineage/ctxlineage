import anthropic
import httpx
import pytest
import respx

MESSAGES_URL = "https://api.anthropic.com/v1/messages"
MESSAGES = [{"role": "user", "content": "Say hello"}]
SSE_HEADERS = {"content-type": "text/event-stream"}


@pytest.fixture
def async_client():
    return anthropic.AsyncAnthropic(api_key="test-key", max_retries=0)


@respx.mock
async def test_async_records_schema_valid_event(
    capture, async_client, validate_event, messages_response_json
):
    respx.post(MESSAGES_URL).mock(return_value=httpx.Response(200, json=messages_response_json))
    await async_client.messages.create(model="claude-sonnet-5", max_tokens=64, messages=MESSAGES)
    (event,) = capture()
    validate_event(event)
    assert event["payload"]["usage"]["output_tokens"] == 3
    assert event["payload"]["request"]["messages"] == MESSAGES


@respx.mock
async def test_async_error_recorded_and_reraised(capture, async_client):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(500, json={"error": {"message": "boom"}})
    )
    with pytest.raises(anthropic.APIStatusError):
        await async_client.messages.create(
            model="claude-sonnet-5", max_tokens=64, messages=MESSAGES
        )
    (event,) = capture()
    assert event["payload"]["error"]["type"] == "InternalServerError"


@respx.mock
async def test_async_create_stream_assembles_content(
    capture, async_client, validate_event, messages_stream_body
):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=messages_stream_body)
    )
    stream = await async_client.messages.create(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES, stream=True
    )
    chunks = [c async for c in stream]
    assert len(chunks) == 7
    (event,) = capture()
    validate_event(event)
    assert event["payload"]["response"]["content"]["0"] == "Hello world"
    assert event["payload"]["usage"] == {"input_tokens": 9, "output_tokens": 2}


@respx.mock
async def test_async_mid_stream_error_recorded_and_reraised(
    capture, async_client, messages_error_stream_body
):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=messages_error_stream_body)
    )
    stream = await async_client.messages.create(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES, stream=True
    )
    with pytest.raises(anthropic.APIStatusError):
        _ = [c async for c in stream]
    (event,) = capture()
    assert "error" in event["payload"]
    assert event["payload"]["response"]["content"]["0"] == "Hello"


@respx.mock
async def test_builtin_anext_works_and_records(capture, async_client, messages_stream_body):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=messages_stream_body)
    )
    stream = await async_client.messages.create(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES, stream=True
    )
    first = await anext(stream)  # builtin anext() on the proxy itself
    assert first.type == "message_start"
    rest = [c async for c in stream]  # mixed consumption continues where anext() left off
    assert len(rest) == 6
    (event,) = capture()
    assert event["payload"]["response"]["content"]["0"] == "Hello world"


@respx.mock
async def test_async_stream_helper_records_full_message(
    capture, async_client, validate_event, messages_stream_body
):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=messages_stream_body)
    )
    async with async_client.messages.stream(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES
    ) as stream:
        text = "".join([t async for t in stream.text_stream])
    assert text == "Hello world"
    (event,) = capture()
    validate_event(event)
    assert event["payload"]["stream"] is True
    assert event["payload"]["response"]["content"]["0"] == "Hello world"
    assert event["payload"]["usage"] == {"input_tokens": 9, "output_tokens": 2}
