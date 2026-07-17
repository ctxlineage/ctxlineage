import gc

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


@respx.mock
async def test_async_create_stream_records_exactly_once(
    capture, async_client, messages_stream_body
):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=messages_stream_body)
    )
    stream = await async_client.messages.create(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES, stream=True
    )
    _ = [c async for c in stream]
    _ = [c async for c in stream]  # exhausted iterator, no new chunks
    assert len(capture()) == 1


@respx.mock
async def test_async_stream_helper_abandoned_records_partial(
    capture, async_client, messages_stream_body
):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=messages_stream_body)
    )
    async with async_client.messages.stream(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES
    ) as stream:
        async for _text in stream.text_stream:
            break  # abandon after the first text delta; __aexit__ records the partial
    (event,) = capture()
    assert event["payload"]["response"]["content"]["0"] == "Hello"


@respx.mock
async def test_async_stream_helper_never_entered_records_nothing(
    capture, async_client, messages_stream_body
):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=messages_stream_body)
    )
    manager = async_client.messages.stream(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES
    )
    del manager  # no __aenter__, no HTTP request, nothing to record
    assert capture() == []


@respx.mock
async def test_async_stream_helper_final_message_stays_intact(
    capture, async_client, messages_stream_body
):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=messages_stream_body)
    )
    async with async_client.messages.stream(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES
    ) as stream:
        message = await stream.get_final_message()
    assert message.content[0].text == "Hello world"  # proxy must not break the accumulator
    assert message.usage.output_tokens == 2
    assert len(capture()) == 1


# --- #35: async abnormal paths the sync suite already covers ---


def _mock_stream(body):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=body)
    )


async def _create_stream(client):
    return await client.messages.create(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES, stream=True
    )


@respx.mock
async def test_async_abandoned_create_stream_records_on_close(
    capture, async_client, messages_stream_body
):
    """AsyncStreamProxy.close() is a coroutine; awaiting it must record the partial."""
    _mock_stream(messages_stream_body)
    stream = await _create_stream(async_client)
    for _ in range(3):  # message_start, content_block_start, first text delta
        await stream.__anext__()
    await stream.close()
    events = capture()
    assert len(events) == 1
    assert events[0]["payload"]["response"]["content"]["0"] == "Hello"
    assert "abandoned" not in events[0]["payload"]  # closed deliberately, not dropped


@respx.mock
async def test_async_close_without_await_still_records_via_finalizer(
    capture, async_client, messages_stream_body
):
    """A forgotten `await stream.close()` loses the close, not the event.

    close() being a coroutine means a missing await silently skips the recording
    path. The finalizer is the backstop: the event survives, marked abandoned
    because nothing ever completed, closed, or exited the stream.
    """
    _mock_stream(messages_stream_body)
    stream = await _create_stream(async_client)
    for _ in range(3):
        await stream.__anext__()
    forgotten = stream.close()  # never awaited: the close body never runs
    assert capture() == []
    forgotten.close()  # drop the coroutine as an un-awaited one would be dropped
    del forgotten, stream
    gc.collect()
    (event,) = capture()
    assert event["payload"]["abandoned"] is True
    assert event["payload"]["response"]["content"]["0"] == "Hello"  # partial still kept


@respx.mock
async def test_async_mid_stream_error_then_close_records_once(
    capture, async_client, messages_error_stream_body
):
    """A defensive `await close()` after an in-band error must not emit twice."""
    _mock_stream(messages_error_stream_body)
    stream = await _create_stream(async_client)
    with pytest.raises(anthropic.APIStatusError):
        _ = [c async for c in stream]
    await stream.close()
    (event,) = capture()  # exactly one: _finish is idempotent
    assert event["payload"]["error"]["type"] == "APIStatusError"
    assert event["payload"]["response"]["content"]["0"] == "Hello"  # partial kept alongside error


@respx.mock
async def test_async_stream_helper_mid_stream_error_recorded(
    capture, async_client, messages_error_stream_body
):
    _mock_stream(messages_error_stream_body)
    with pytest.raises(anthropic.APIStatusError):
        async with async_client.messages.stream(
            model="claude-sonnet-5", max_tokens=64, messages=MESSAGES
        ) as stream:
            async for _text in stream.text_stream:
                pass
    (event,) = capture()
    assert "error" in event["payload"]
    assert event["payload"]["response"]["content"]["0"] == "Hello"


@respx.mock
async def test_async_multi_block_stream_degrades_but_stays_accurate(
    capture, async_client, validate_event, messages_tool_use_stream_body
):
    _mock_stream(messages_tool_use_stream_body)
    stream = await _create_stream(async_client)
    seen = [c async for c in stream]  # thinking + text + tool_use blocks must not crash
    assert len(seen) == 13
    (event,) = capture()
    validate_event(event)
    response = event["payload"]["response"]
    # scope guard: only the text_delta block is accumulated (here at index >= 1);
    # the thinking and tool_use blocks are intentionally absent from content.
    assert response["content"] == {"1": "Hello world"}
    assert response["stop_reason"] == "tool_use"
    assert response["chunk_count"] == 13  # every raw event still counted
    assert event["payload"]["usage"] == {"input_tokens": 9, "output_tokens": 20}


@respx.mock
async def test_async_with_on_create_stream_records_partial_promptly(
    capture, async_client, messages_stream_body
):
    """`async with` over a raw create(stream=True) proxy: __aenter__/__aexit__.

    The sync twin (`with stream:`) has covered these slots since M1. Unlike a
    bare `async for` + break, whose recording waits for the loop to finalize the
    async generator, __aexit__ runs the finish path inline — so the partial is
    recorded by the time the block is left, with no loop tick needed.
    """
    _mock_stream(messages_stream_body)
    stream = await _create_stream(async_client)
    async with stream as entered:
        assert entered is stream  # __aenter__ returns the proxy, not the raw stream
        n = 0
        async for _chunk in entered:
            n += 1
            if n == 3:  # message_start, content_block_start, first text delta
                break
    (event,) = capture()  # already recorded: no awaiting the loop
    assert event["payload"]["response"]["content"]["0"] == "Hello"
    assert event["payload"]["response"]["chunk_count"] == 3
    assert "abandoned" not in event["payload"]  # exited deliberately, not dropped


@respx.mock
async def test_async_anext_to_exhaustion_records_once(capture, async_client, messages_stream_body):
    """Driving __anext__ to StopAsyncIteration is a complete consumption, not an abandon."""
    _mock_stream(messages_stream_body)
    stream = await _create_stream(async_client)
    pulled = 0
    with pytest.raises(StopAsyncIteration):
        while True:
            await stream.__anext__()
            pulled += 1
    assert pulled == 7
    del stream
    gc.collect()  # StopAsyncIteration already finished it; the finalizer must not re-record
    (event,) = capture()
    assert "abandoned" not in event["payload"]
    assert event["payload"]["response"]["content"]["0"] == "Hello world"
    assert event["payload"]["response"]["chunk_count"] == 7


@respx.mock
async def test_async_stream_helper_multi_block_final_message_stays_intact(
    capture, async_client, messages_tool_use_stream_body
):
    """The SDK's own accumulator must still see every block the proxy passes through.

    The assembler drops thinking/tool_use text by design, but that is our
    summary only: get_final_message() is the SDK's, and a tool_use round-trip
    breaks outright if the proxy costs it a block.
    """
    _mock_stream(messages_tool_use_stream_body)
    async with async_client.messages.stream(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES
    ) as stream:
        message = await stream.get_final_message()
    kinds = [block.type for block in message.content]
    assert kinds == ["thinking", "text", "tool_use"]
    assert message.content[2].input == {"city": "Paris"}  # input_json_delta parsed by the SDK
    assert message.stop_reason == "tool_use"
    assert len(capture()) == 1
