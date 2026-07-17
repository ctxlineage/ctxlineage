"""`ctxlineage test` — the CI contract: non-zero exit on a hard-gate failure.

End-to-end over the demo event generator, so the rules run against really
captured data rather than only synthetic fixtures. The demo's tagged session
(session 4) deliberately tags `memory` without ever injecting it into a prompt,
which is exactly a presence failure.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from ctxlineage._cli import main


@pytest.fixture
def demo_dir(tmp_path):
    script = Path(__file__).parent.parent / "examples" / "generate_demo_events.py"
    events = tmp_path / "events"
    subprocess.run([sys.executable, str(script), str(events)], check=True, timeout=60)
    return events


def _config(tmp_path, body):
    path = tmp_path / "ctxlineage.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _invoke(demo_dir, config):
    return CliRunner().invoke(main, ["test", "--dir", str(demo_dir), "--config", str(config)])


def test_passing_run_exits_zero(demo_dir, tmp_path):
    config = _config(tmp_path, "[[assert.window_budget]]\nmax_pct = 80\n")
    result = _invoke(demo_dir, config)
    assert result.exit_code == 0, result.output
    assert "passed" in result.output


def test_hard_gate_failure_exits_non_zero(demo_dir, tmp_path):
    """The whole point of the slice."""
    config = _config(tmp_path, "[[assert.window_budget]]\nmax_pct = 0.5\n")
    result = _invoke(demo_dir, config)
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_grounded_presence_failure_exits_non_zero(demo_dir, tmp_path):
    config = _config(tmp_path, '[[assert.grounded]]\ntag = "memory"\n')
    result = _invoke(demo_dir, config)
    assert result.exit_code == 1
    assert "memory" in result.output


def test_grounded_passes_for_a_tag_that_landed(demo_dir, tmp_path):
    config = _config(tmp_path, '[[assert.grounded]]\ntag = "rag_chunks"\nwarn_dead = true\n')
    result = _invoke(demo_dir, config)
    assert result.exit_code == 0, result.output


def test_warnings_alone_do_not_fail_the_build(demo_dir, tmp_path):
    """Tier rule end-to-end: an untagged name is advisory, never a gate."""
    config = _config(tmp_path, '[[assert.grounded]]\ntag = "never_tagged_anywhere"\n')
    result = _invoke(demo_dir, config)
    assert result.exit_code == 0, result.output
    assert "WARN" in result.output


def test_findings_are_listed_with_a_summary(demo_dir, tmp_path):
    config = _config(tmp_path, "[[assert.window_budget]]\nmax_pct = 0.5\n")
    result = _invoke(demo_dir, config)
    assert "window_budget" in result.output
    assert "failed" in result.output


def test_default_config_path_is_discovered(demo_dir, tmp_path, monkeypatch):
    _config(tmp_path, "[[assert.window_budget]]\nmax_pct = 80\n")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["test", "--dir", str(demo_dir)])
    assert result.exit_code == 0, result.output


def test_missing_events_exits_non_zero(tmp_path):
    config = _config(tmp_path, "[[assert.window_budget]]\nmax_pct = 80\n")
    result = CliRunner().invoke(
        main, ["test", "--dir", str(tmp_path / "nope"), "--config", str(config)]
    )
    assert result.exit_code == 1
    assert "No events found" in result.output


def test_missing_config_exits_non_zero(demo_dir, tmp_path):
    result = CliRunner().invoke(
        main, ["test", "--dir", str(demo_dir), "--config", str(tmp_path / "nope.toml")]
    )
    assert result.exit_code == 1
    assert "not found" in result.output


def test_invalid_config_exits_non_zero(demo_dir, tmp_path):
    config = _config(tmp_path, "[[assert.window_budget]]\nmax_pct = 900\n")
    result = _invoke(demo_dir, config)
    assert result.exit_code == 1
    assert "max_pct" in result.output


def test_empty_capture_is_not_green(tmp_path):
    """A gate that runs over zero recorded calls and reports success is the
    silently-green failure this track exists to prevent."""
    events = tmp_path / "events"
    events.mkdir()
    (events / "events.jsonl").write_text("")
    config = _config(tmp_path, "[[assert.window_budget]]\nmax_pct = 80\n")
    result = CliRunner().invoke(main, ["test", "--dir", str(events), "--config", str(config)])
    assert result.exit_code == 1
    assert "no llm calls" in result.output.lower()


def test_report_command_still_works(demo_dir, tmp_path):
    """The `test` command is additive: it must not disturb `report` (#57 is
    editing the same CLI module in parallel)."""
    out = tmp_path / "r.html"
    result = CliRunner().invoke(main, ["report", "--dir", str(demo_dir), "--out", str(out)])
    assert result.exit_code == 0, result.output
