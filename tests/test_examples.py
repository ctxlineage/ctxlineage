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

from ctxlineage._report import normalize

EXAMPLES = Path(__file__).parent.parent / "examples"


def _run_example(script: str, out_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CTXLINEAGE_DIR": str(out_dir)}
    env.pop("OPENAI_API_KEY", None)  # keyless: --mock must be fully offline
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
