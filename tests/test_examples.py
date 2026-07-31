"""End-to-end tests for the runnable examples in --mock (keyless) mode.

Each example is executed as a real subprocess — exactly how a newcomer runs it
(PLAN.md §10: first report within 5 minutes, key or no key) — and its recorded
events must not only validate against the schema but also survive the full
report pipeline with matched tags and lineage edges. The examples are the
instrumentation exemplars; if their tags stopped matching, the docs would lie.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from ctxlineage._cli import main as cli_main
from ctxlineage._report import normalize

EXAMPLES = Path(__file__).parent.parent / "examples"


def _run_example(script: str, out_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CTXLINEAGE_DIR": str(out_dir)}
    for var in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
    ):
        env.pop(var, None)  # keyless: --mock must be fully offline
    return subprocess.run(
        [sys.executable, str(EXAMPLES / script), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _load_events(out_dir: Path) -> list[dict]:
    lines = (out_dir / "events.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


@pytest.fixture(scope="module")
def rag_events(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("rag")
    proc = _run_example("rag_app.py", out_dir, "--mock")
    assert proc.returncode == 0, proc.stderr
    return _load_events(out_dir)


@pytest.fixture(scope="module")
def rag_report(rag_events):
    return normalize.build_report_data(rag_events)


def test_rag_all_events_schema_valid(rag_events, validate_event):
    assert len(rag_events) >= 12  # 3 turns x (span_start + tags + call + span_end)
    for event in rag_events:
        validate_event(event)


def test_rag_spans_and_tags(rag_events):
    span_starts = [e for e in rag_events if e["event_type"] == "span_start"]
    assert [e["payload"]["name"] for e in span_starts] == ["answer_query"] * 3

    tags_by_span: dict[str, dict[str, dict]] = {}
    for event in rag_events:
        if event["event_type"] == "tag":
            tags_by_span.setdefault(event["span_id"], {})[event["payload"]["name"]] = event[
                "payload"
            ]

    calls = [e for e in rag_events if e["event_type"] == "llm_call"]
    assert len(calls) == 3
    assert all(e["span_id"] for e in calls)

    for i, call in enumerate(calls):
        tags = tags_by_span[call["span_id"]]
        assert "system" in tags and tags["system"]["source"]
        assert tags["rag_chunks"]["source"] and tags["rag_chunks"]["transform"]
        if i > 0:  # history only exists from the second turn on
            assert tags["history"]["transform"]


def test_rag_tags_all_match(rag_report):
    (session,) = rag_report["sessions"]
    elements = session["elements"]
    assert elements, "tag elements missing from report data"
    unmatched = [el["name"] for el in elements if not el["matched"]]
    assert not unmatched, f"exemplar tags must match 100%: {unmatched}"


def test_rag_lineage_edges(rag_report):
    (session,) = rag_report["sessions"]
    kinds = {edge["kind"] for edge in session["edges"]}
    assert "output_text" in kinds, "turn N answer should flow into turn N+1 input"
    rag_elements = [el for el in session["elements"] if el["name"] == "rag_chunks"]
    assert rag_elements and all(el["source"] and el["transform"] for el in rag_elements)


def test_rag_no_key_prints_hint(tmp_path):
    proc = _run_example("rag_app.py", tmp_path)  # no --mock, no OPENAI_API_KEY
    assert proc.returncode == 2
    assert "--mock" in proc.stderr
    assert not (tmp_path / "events.jsonl").exists()


@pytest.fixture(scope="module")
def agent_events(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("agent")
    proc = _run_example("agent_app.py", out_dir, "--mock")
    assert proc.returncode == 0, proc.stderr
    return _load_events(out_dir)


def test_agent_all_events_schema_valid(agent_events, validate_event):
    for event in agent_events:
        validate_event(event)


def test_agent_tool_loop(agent_events):
    calls = [e["payload"] for e in agent_events if e["event_type"] == "llm_call"]
    assert len(calls) >= 4  # 2 user turns x (tool step + answer step)
    assert all(c["request"].get("tools") for c in calls)
    finish_reasons = {
        c["response"]["choices"][0]["finish_reason"] for c in calls if c.get("response")
    }
    assert "tool_calls" in finish_reasons
    tool_roles = [m for c in calls for m in c["request"]["messages"] if m.get("role") == "tool"]
    assert tool_roles, "tool results must be fed back into a later call"


def test_agent_spans_and_tool_tags(agent_events):
    span_names = [e["payload"]["name"] for e in agent_events if e["event_type"] == "span_start"]
    assert span_names == ["agent_turn"] * 2

    tool_tags = [
        e["payload"]
        for e in agent_events
        if e["event_type"] == "tag" and e["payload"]["name"] == "tool_result"
    ]
    assert tool_tags and all(t["source"] == "tool:search_notes" for t in tool_tags)

    report = normalize.build_report_data(agent_events)
    (session,) = report["sessions"]
    matched = {el["name"] for el in session["elements"] if el["matched"]}
    assert "tool_result" in matched


@pytest.fixture(scope="module")
def anthropic_dir(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("anthropic")
    proc = _run_example("anthropic_app.py", out_dir, "--mock")
    assert proc.returncode == 0, proc.stderr
    return out_dir


@pytest.fixture(scope="module")
def anthropic_events(anthropic_dir):
    return _load_events(anthropic_dir)


def test_anthropic_all_events_schema_valid(anthropic_events, validate_event):
    for event in anthropic_events:
        validate_event(event)


def test_anthropic_tool_round_trip(anthropic_events):
    calls = [e["payload"] for e in anthropic_events if e["event_type"] == "llm_call"]
    assert len(calls) == 2
    assert all(c["provider"] == "anthropic" and c["api"] == "messages" for c in calls)

    first, second = calls
    assert first["stream"] is False
    assert first["request"]["system"] == second["request"]["system"]  # top-level kwarg
    assert first["response"]["stop_reason"] == "tool_use"

    tool_results = [
        part
        for message in second["request"]["messages"]
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if isinstance(part, dict) and part.get("type") == "tool_result"
    ]
    assert tool_results, "the tool result must be fed back into the second call"
    assert second["stream"] is True
    assert second["response"]["stop_reason"] == "end_turn"
    assert second["usage"]["output_tokens"] > 0


def test_anthropic_span_and_tags(anthropic_events):
    span_names = [e["payload"]["name"] for e in anthropic_events if e["event_type"] == "span_start"]
    assert span_names == ["deploy_check"]

    tags = {
        e["payload"]["name"]: e["payload"] for e in anthropic_events if e["event_type"] == "tag"
    }
    assert tags["system"]["source"]
    assert tags["tool_result"]["source"] == "tool:check_service"
    assert tags["tool_result"]["transform"]


def test_anthropic_report_json_segments_and_edges(anthropic_dir):
    result = CliRunner().invoke(cli_main, ["report", "--dir", str(anthropic_dir), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["stats"]["tags"]["match_rate"] > 0
    (session,) = data["sessions"]
    unmatched = [el["name"] for el in session["elements"] if not el["matched"]]
    assert not unmatched, f"exemplar tags must match 100%: {unmatched}"

    kinds = {seg["kind"] for call in session["calls"] for seg in call["segments"]}
    assert "system" in kinds, "the top-level system kwarg must surface as a segment"
    assert "tool_result" in kinds, "the tool_result turn must surface as a tagged tool segment"
    assert "tool_defs" in kinds

    assert all(call["output"]["content"] for call in session["calls"])
    assert "output_text" in {edge["kind"] for edge in session["edges"]}


def test_anthropic_no_key_prints_hint(tmp_path):
    proc = _run_example("anthropic_app.py", tmp_path)  # no --mock, no ANTHROPIC_API_KEY
    assert proc.returncode == 2
    assert "--mock" in proc.stderr
    assert not (tmp_path / "events.jsonl").exists()


# --- the documented CI gate, run exactly as the README tells people to run it ---


def test_reference_config_gates_the_rag_exemplar(tmp_path):
    """`examples/ctxlineage.toml` must hold against a real recorded run.

    This is the whole on-ramp the README sells — record keyless, then gate — so
    if the rules, the exemplar's tags, or the config drift apart, the docs are
    lying and this test is how we find out.
    """
    proc = _run_example("rag_app.py", tmp_path, "--mock")
    assert proc.returncode == 0, proc.stderr

    config = EXAMPLES / "ctxlineage.toml"
    result = CliRunner().invoke(cli_main, ["test", "-d", str(tmp_path), "-c", str(config)])
    assert result.exit_code == 0, (
        f"the shipped config must pass the shipped example:\n{result.output}"
    )
    assert "4 assertion(s)" in result.output
    # grounded gates on the tag, so a silent pass here means rag_chunks really
    # landed in the window — not that the rule was skipped for want of evidence
    assert "SKIP" not in result.output
    assert "WARN" not in result.output


def test_reference_config_fails_a_run_that_breaks_it(tmp_path):
    """The gate must be able to fail, or passing means nothing."""
    proc = _run_example("rag_app.py", tmp_path, "--mock")
    assert proc.returncode == 0, proc.stderr

    tight = tmp_path / "tight.toml"
    tight.write_text(
        (EXAMPLES / "ctxlineage.toml").read_text().replace("max_pct = 80", "max_pct = 0.05")
    )
    result = CliRunner().invoke(cli_main, ["test", "-d", str(tmp_path), "-c", str(tight)])
    assert result.exit_code == 1
    assert "FAIL" in result.output
