import httpx
import respx

CHAT_URL = "https://api.openai.com/v1/chat/completions"
MESSAGES = [{"role": "user", "content": "Say hello"}]
SSE_HEADERS = {"content-type": "text/event-stream"}


def _mock_stream(body):
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(200, headers=SSE_HEADERS, content=body)
    )


@respx.mock
def test_stream_assembles_content(capture, openai_client, validate_event, chat_stream_body):
    _mock_stream(chat_stream_body)
    stream = openai_client.chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, stream=True
    )
    seen = [c for c in stream]
    assert len(seen) == 4
    (event,) = capture()
    validate_event(event)
    payload = event["payload"]
    assert payload["stream"] is True
    assert payload["response"]["content"]["0"] == "Hello world"
    assert payload["response"]["finish_reasons"]["0"] == "stop"
    assert payload["response"]["chunk_count"] == 4
    assert payload["usage"]["total_tokens"] == 11


@respx.mock
def test_full_iteration_records_exactly_once(capture, openai_client, chat_stream_body):
    _mock_stream(chat_stream_body)
    stream = openai_client.chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, stream=True
    )
    list(stream)
    list(stream)  # exhausted iterator, no new chunks
    assert len(capture()) == 1


@respx.mock
def test_abandoned_stream_records_on_close(capture, openai_client, chat_stream_body):
    _mock_stream(chat_stream_body)
    stream = openai_client.chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, stream=True
    )
    iterator = iter(stream)
    next(iterator)  # consume a single chunk, then abandon
    stream.close()
    events = capture()
    assert len(events) == 1
    assert events[0]["payload"]["response"]["content"]["0"] == "Hello"


@respx.mock
def test_context_manager_records_once(capture, openai_client, chat_stream_body):
    _mock_stream(chat_stream_body)
    with openai_client.chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, stream=True
    ) as stream:
        for _chunk in stream:
            pass
    assert len(capture()) == 1
