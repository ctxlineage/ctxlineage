import httpx
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
