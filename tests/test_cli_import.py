"""CLI tests for `ctxlineage import` (#57)."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ctxlineage._cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "claude_code"
TRANSCRIPT = FIXTURES / "session_tool_loop.jsonl"
# A call whose reported prompt is 1 token but whose reconstructed user text
# estimates to several — the estimator (tiktoken) overshooting the provider's
# own count on a short prompt.
OVERSHOOT = FIXTURES / "session_overshoot.jsonl"


@pytest.fixture
def runner():
    return CliRunner()


def run_import(runner, tmp_path, *extra):
    return runner.invoke(
        main, ["import", "--from", "claude-code", str(TRANSCRIPT), "--dir", str(tmp_path), *extra]
    )


def test_import_writes_events(runner, tmp_path):
    result = run_import(runner, tmp_path)
    assert result.exit_code == 0, result.output
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["event_type"] for line in lines].count("llm_call") == 2


def test_import_reports_what_it_did(runner, tmp_path):
    result = run_import(runner, tmp_path)
    assert "2 call(s)" in result.output
    assert "1 span(s)" in result.output


def test_import_discloses_provenance(runner, tmp_path):
    result = run_import(runner, tmp_path)
    assert "reconstructed" in result.output
    assert "estimated" in result.output


def test_import_discloses_the_unpreserved_remainder(runner, tmp_path):
    """The gap is stated on stdout, not buried in the payload — and names all
    three causes, not just the system prompt."""
    result = run_import(runner, tmp_path)
    assert "system prompt" in result.output
    assert "tool definitions" in result.output
    assert "reasoning text" in result.output


def test_import_reports_coverage_per_call_never_summed(runner, tmp_path):
    """Every prompt re-sends the whole conversation, so a summed token gap would
    describe no real quantity. Coverage is expressed per call instead."""
    result = run_import(runner, tmp_path)
    assert "of each call's real prompt tokens" in result.output


def test_import_coverage_never_exceeds_100pct(runner, tmp_path):
    """When the estimate overshoots the reported count, coverage clamps to 100%
    rather than printing an impossible >100% share followed by a 'the rest is
    the system prompt...' clause that then describes nothing."""
    import re

    result = runner.invoke(
        main, ["import", "--from", "claude-code", str(OVERSHOOT), "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    cover_line = next(
        line for line in result.output.splitlines() if "reconstructed segments cover" in line
    )
    assert "cover 100%" in cover_line
    assert "an estimate" in cover_line
    assert all(int(pct) <= 100 for pct in re.findall(r"(\d+)%", cover_line))
    # No false remainder claim when there is no remainder.
    assert "the rest is the system prompt" not in cover_line


def test_import_discloses_stripped_reasoning_blocks(runner, tmp_path):
    result = run_import(runner, tmp_path)
    assert "1 reasoning block(s) were recorded with their text stripped" in result.output


def test_dry_run_writes_nothing(runner, tmp_path):
    result = run_import(runner, tmp_path, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "Would import" in result.output
    assert not (tmp_path / "events.jsonl").exists()


def test_reimport_refused_to_avoid_double_counting(runner, tmp_path):
    assert run_import(runner, tmp_path).exit_code == 0
    result = run_import(runner, tmp_path)
    assert result.exit_code != 0
    assert "already in" in result.output


def test_unknown_adapter_rejected(runner, tmp_path):
    result = runner.invoke(main, ["import", "--from", "nope", str(TRANSCRIPT)])
    assert result.exit_code != 0


def test_from_is_required(runner):
    assert runner.invoke(main, ["import", str(TRANSCRIPT)]).exit_code != 0


def test_missing_transcript_for_cwd_is_a_clean_error(runner, tmp_path, monkeypatch):
    from ctxlineage._import import claude_code

    monkeypatch.setattr(claude_code, "PROJECTS_DIR", tmp_path / "nope")
    result = runner.invoke(main, ["import", "--from", "claude-code", "--dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "No claude-code transcript found" in result.output


def test_imported_events_render_through_report(runner, tmp_path):
    """The point of the whole track: the existing report renders it unchanged."""
    assert run_import(runner, tmp_path).exit_code == 0
    out = tmp_path / "report.html"
    result = runner.invoke(main, ["report", "--dir", str(tmp_path), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text().startswith("<!DOCTYPE html>")


def test_report_json_contract_holds_for_imported_session(runner, tmp_path):
    assert run_import(runner, tmp_path).exit_code == 0
    result = runner.invoke(main, ["report", "--dir", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    session = data["sessions"][0]
    assert data["stats"]["calls"] == 2
    assert session["calls"][0]["step"]  # span name -> the four views have a step
    assert session["edges"]  # lineage graph has something to draw
    assert data["stats"]["tags"]["match_rate"] is None  # untagged tier, honestly


def test_transcript_discovery_by_session_id(runner, tmp_path, monkeypatch):
    from ctxlineage._import import claude_code

    projects = tmp_path / "projects" / "-tmp-proj"
    projects.mkdir(parents=True)
    (projects / "session_tool_loop.jsonl").write_text(TRANSCRIPT.read_text())
    monkeypatch.setattr(claude_code, "PROJECTS_DIR", tmp_path / "projects")
    result = runner.invoke(
        main,
        [
            "import",
            "--from",
            "claude-code",
            "--session",
            "session_tool_loop",
            "--dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output


def test_transcript_discovery_by_recorded_cwd(runner, tmp_path, monkeypatch):
    """Discovery matches the cwd the records recorded, not the encoded dir name."""
    from ctxlineage._import import claude_code

    projects = tmp_path / "projects" / "-whatever-encoding"
    projects.mkdir(parents=True)
    (projects / "session_tool_loop.jsonl").write_text(TRANSCRIPT.read_text())
    monkeypatch.setattr(claude_code, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: Path("/tmp/proj")))
    result = runner.invoke(main, ["import", "--from", "claude-code", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
