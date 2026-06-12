import json

import ctxlineage
from ctxlineage import _state


def read_events(directory):
    path = directory / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _payload():
    return {
        "provider": "openai",
        "api": "chat.completions",
        "request": {"model": "gpt-4o-mini", "messages": []},
    }


def test_init_configures_and_emit_writes(tmp_path, validate_event):
    ctxlineage.init(tmp_path)
    assert _state.is_configured()
    assert _state.emit("llm_call", _payload(), call_id="c1") is True
    (event,) = read_events(tmp_path)
    validate_event(event)
    assert event["session_id"]


def test_env_var_dir_respected(tmp_path, monkeypatch):
    monkeypatch.setenv("CTXLINEAGE_DIR", str(tmp_path / "from-env"))
    ctxlineage.init()
    _state.emit("llm_call", _payload(), call_id="c1")
    assert (tmp_path / "from-env" / "events.jsonl").exists()


def test_double_init_keeps_session(tmp_path):
    ctxlineage.init(tmp_path)
    first = _state.session_id()
    ctxlineage.init(tmp_path)
    assert _state.session_id() == first


def test_emit_before_init_is_noop(tmp_path):
    assert not _state.is_configured()
    assert _state.emit("llm_call", _payload(), call_id="c1") is False
    assert read_events(tmp_path) == []


def test_emit_never_raises_on_write_failure(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory")
    ctxlineage.init(blocker)
    assert _state.emit("llm_call", _payload(), call_id="c1") is False
