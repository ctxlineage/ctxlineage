import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "examples" / "generate_demo_events.py"


@pytest.fixture(scope="module")
def demo_events(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("demo")
    subprocess.run([sys.executable, str(SCRIPT), str(out_dir)], check=True, timeout=60)
    lines = (out_dir / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def test_all_events_schema_valid(demo_events, validate_event):
    assert len(demo_events) >= 6
    for event in demo_events:
        validate_event(event)


def test_contains_two_sessions(demo_events):
    assert len({e["session_id"] for e in demo_events}) == 2


def test_contains_streamed_and_error_calls(demo_events):
    payloads = [e["payload"] for e in demo_events]
    assert any(p.get("stream") for p in payloads)
    assert any("error" in p for p in payloads)


def test_deterministic_content(demo_events):
    assert all(e["payload"]["provider"] == "openai" for e in demo_events)
    models = {e["payload"]["request"].get("model") for e in demo_events}
    assert "gpt-4o-mini" in models
