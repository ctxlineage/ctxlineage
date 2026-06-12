import json
from datetime import datetime, timezone

from ctxlineage._events import EventWriter, make_event, new_id


def _llm_payload(**extra):
    payload = {
        "provider": "openai",
        "api": "chat.completions",
        "request": {"model": "gpt-4o-mini", "messages": []},
    }
    payload.update(extra)
    return payload


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_writer_creates_directory_and_appends(tmp_path):
    writer = EventWriter(tmp_path / "nested" / ".ctxlineage")
    e1 = make_event("llm_call", "s1", _llm_payload(), call_id=new_id())
    e2 = make_event("llm_call", "s1", _llm_payload(), call_id=new_id())
    writer.write(e1)
    writer.write(e2)
    events = read_lines(writer.path)
    assert len(events) == 2
    assert events[0]["call_id"] == e1["call_id"]
    assert events[1]["call_id"] == e2["call_id"]


def test_make_event_is_schema_valid(validate_event):
    event = make_event("llm_call", "s1", _llm_payload(), call_id=new_id())
    validate_event(event)


def test_timestamp_is_utc_iso():
    event = make_event("llm_call", "s1", _llm_payload(), call_id=new_id())
    parsed = datetime.fromisoformat(event["timestamp"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_non_json_values_degrade_to_strings(tmp_path):
    writer = EventWriter(tmp_path)
    payload = _llm_payload(weird=datetime(2026, 6, 12, tzinfo=timezone.utc))
    writer.write(make_event("llm_call", "s1", payload, call_id=new_id()))
    (event,) = read_lines(writer.path)
    assert isinstance(event["payload"]["weird"], str)


def test_new_ids_are_unique():
    assert new_id() != new_id()
