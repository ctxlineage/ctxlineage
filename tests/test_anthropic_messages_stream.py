import anthropic
import httpx
import pytest
import respx

MESSAGES_URL = "https://api.anthropic.com/v1/messages"
MESSAGES = [{"role": "user", "content": "Say hello"}]
SSE_HEADERS = {"content-type": "text/event-stream"}


def _mock_stream(body):
    respx.post(MESSAGES_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=body)
    )


def _create_stream(client):
    return client.messages.create(
        model="claude-sonnet-5", max_tokens=64, messages=MESSAGES, stream=True
    )


def _stream_manager(client):
    return client.messages.stream(model="claude-sonnet-5", max_tokens=64, messages=MESSAGES)


@respx.mock
def test_create_stream_assembles_content(
    capture, anthropic_client, validate_event, messages_stream_body
):
    _mock_stream(messages_stream_body)
    stream = _create_stream(anthropic_client)
    seen = [c for c in stream]
    assert len(seen) == 7
    (event,) = capture()
    validate_event(event)
    payload = event["payload"]
    assert payload["stream"] is True
    assert payload["response"]["content"]["0"] == "Hello world"
    assert payload["response"]["stop_reason"] == "end_turn"
    assert payload["response"]["chunk_count"] == 7
    assert payload["usage"] == {"input_tokens": 9, "output_tokens": 2}


@respx.mock
def test_create_stream_records_exactly_once(capture, anthropic_client, messages_stream_body):
    _mock_stream(messages_stream_body)
    stream = _create_stream(anthropic_client)
    list(stream)
    list(stream)  # exhausted iterator, no new chunks
    assert len(capture()) == 1


@respx.mock
def test_abandoned_create_stream_records_on_close(capture, anthropic_client, messages_stream_body):
    _mock_stream(messages_stream_body)
    stream = _create_stream(anthropic_client)
    iterator = iter(stream)
    for _ in range(3):  # message_start, content_block_start, first text delta
        next(iterator)
    stream.close()
    events = capture()
    assert len(events) == 1
    assert events[0]["payload"]["response"]["content"]["0"] == "Hello"


@respx.mock
def test_stream_helper_records_full_message(
    capture, anthropic_client, validate_event, messages_stream_body
):
    _mock_stream(messages_stream_body)
    with _stream_manager(anthropic_client) as stream:
        text = "".join(stream.text_stream)
    assert text == "Hello world"
    (event,) = capture()
    validate_event(event)
    payload = event["payload"]
    assert payload["stream"] is True
    assert payload["provider"] == "anthropic"
    assert payload["api"] == "messages"
    assert payload["request"]["messages"] == MESSAGES
    assert payload["response"]["content"]["0"] == "Hello world"
    assert payload["usage"] == {"input_tokens": 9, "output_tokens": 2}


@respx.mock
def test_stream_helper_final_message_stays_intact(capture, anthropic_client, messages_stream_body):
    _mock_stream(messages_stream_body)
    with _stream_manager(anthropic_client) as stream:
        message = stream.get_final_message()
    assert message.content[0].text == "Hello world"  # proxy must not break the SDK accumulator
    assert message.usage.output_tokens == 2
    assert len(capture()) == 1


@respx.mock
def test_stream_helper_abandoned_records_partial(capture, anthropic_client, messages_stream_body):
    _mock_stream(messages_stream_body)
    with _stream_manager(anthropic_client) as stream:
        for _text in stream.text_stream:
            break  # abandon after the first text delta
    (event,) = capture()
    assert event["payload"]["response"]["content"]["0"] == "Hello"


@respx.mock
def test_stream_helper_never_entered_records_nothing(
    capture, anthropic_client, messages_stream_body
):
    _mock_stream(messages_stream_body)
    manager = _stream_manager(anthropic_client)
    del manager  # no __enter__, no HTTP request, nothing to record
    assert capture() == []


@respx.mock
def test_mid_stream_error_recorded_and_reraised(
    capture, anthropic_client, messages_error_stream_body
):
    _mock_stream(messages_error_stream_body)
    stream = _create_stream(anthropic_client)
    with pytest.raises(anthropic.APIStatusError):
        list(stream)
    stream.close()  # a defensive close after the error must not emit twice
    (event,) = capture()
    payload = event["payload"]
    assert "error" in payload
    assert payload["response"]["content"]["0"] == "Hello"  # partial kept alongside error


@respx.mock
def test_stream_helper_mid_stream_error_recorded(
    capture, anthropic_client, messages_error_stream_body
):
    _mock_stream(messages_error_stream_body)
    with pytest.raises(anthropic.APIStatusError):
        with _stream_manager(anthropic_client) as stream:
            for _text in stream.text_stream:
                pass
    (event,) = capture()
    assert "error" in event["payload"]
    assert event["payload"]["response"]["content"]["0"] == "Hello"


@respx.mock
def test_builtin_next_works_and_records(capture, anthropic_client, messages_stream_body):
    _mock_stream(messages_stream_body)
    stream = _create_stream(anthropic_client)
    first = next(stream)  # builtin next() on the proxy itself, not iter(stream)
    assert first.type == "message_start"
    rest = [c for c in stream]  # mixed consumption continues where next() left off
    assert len(rest) == 6
    (event,) = capture()
    assert event["payload"]["response"]["content"]["0"] == "Hello world"
    assert event["payload"]["response"]["chunk_count"] == 7


@respx.mock
def test_builtin_next_partial_then_close_records(capture, anthropic_client, messages_stream_body):
    _mock_stream(messages_stream_body)
    stream = _create_stream(anthropic_client)
    for _ in range(3):  # message_start, content_block_start, first text delta
        next(stream)
    stream.close()
    (event,) = capture()
    assert event["payload"]["response"]["content"]["0"] == "Hello"


@respx.mock
def test_builtin_next_to_exhaustion_records_once(capture, anthropic_client, messages_stream_body):
    """StopIteration out of __next__ is a complete consumption, not an abandon."""
    _mock_stream(messages_stream_body)
    stream = _create_stream(anthropic_client)
    pulled = 0
    with pytest.raises(StopIteration):
        while True:
            next(stream)
            pulled += 1
    assert pulled == 7
    (event,) = capture()
    assert "abandoned" not in event["payload"]
    assert event["payload"]["response"]["content"]["0"] == "Hello world"
    assert event["payload"]["response"]["chunk_count"] == 7


@respx.mock
def test_builtin_next_mid_stream_error_recorded(
    capture, anthropic_client, messages_error_stream_body
):
    """__next__ has its own error branch, separate from __iter__'s."""
    _mock_stream(messages_error_stream_body)
    stream = _create_stream(anthropic_client)
    with pytest.raises(anthropic.APIStatusError):
        while True:
            next(stream)
    (event,) = capture()
    assert event["payload"]["error"]["type"] == "APIStatusError"
    assert event["payload"]["response"]["content"]["0"] == "Hello"  # partial kept


@respx.mock
def test_multi_block_stream_degrades_but_stays_accurate(
    capture, anthropic_client, validate_event, messages_tool_use_stream_body
):
    _mock_stream(messages_tool_use_stream_body)
    stream = _create_stream(anthropic_client)
    seen = [c for c in stream]  # thinking + text + tool_use blocks must not crash
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
