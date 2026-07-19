import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from ctxlineage._cli import main


@pytest.fixture
def demo_dir(tmp_path):
    script = Path(__file__).parent.parent / "examples" / "generate_demo_events.py"
    subprocess.run([sys.executable, str(script), str(tmp_path)], check=True, timeout=60)
    return tmp_path


def test_report_writes_html(demo_dir, tmp_path):
    out = tmp_path / "report.html"
    result = CliRunner().invoke(main, ["report", "--dir", str(demo_dir), "--out", str(out)])
    assert result.exit_code == 0, result.output
    content = out.read_text()
    assert content.startswith("<!DOCTYPE html>")
    assert "ctxlineage-data" in content
    assert str(out) in result.output


def test_report_json_emits_contract(demo_dir):
    result = CliRunner().invoke(main, ["report", "--dir", str(demo_dir), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["report_version"] == 1
    assert data["stats"]["sessions"] == 4


def test_report_missing_dir_fails_gracefully(tmp_path):
    result = CliRunner().invoke(main, ["report", "--dir", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "No events found" in result.output


def test_skipped_lines_reported(tmp_path):
    (tmp_path / "events.jsonl").write_text("not json\n")
    result = CliRunner().invoke(main, ["report", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "1 malformed" in result.output


def test_report_tolerates_corrupt_bytes(demo_dir, tmp_path):
    """A non-UTF-8 byte (a process killed mid-write) must not abort the read."""
    log = demo_dir / "events.jsonl"
    log.write_bytes(log.read_bytes() + b"\xff\xfe torn line\n")
    out = tmp_path / "report.html"
    result = CliRunner().invoke(main, ["report", "--dir", str(demo_dir), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "malformed" in result.output
    # The valid calls before the bad bytes still rendered.
    assert out.read_text().startswith("<!DOCTYPE html>")


def test_report_creates_missing_out_dir(demo_dir, tmp_path):
    out = tmp_path / "nested" / "deep" / "report.html"
    result = CliRunner().invoke(main, ["report", "--dir", str(demo_dir), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()


def test_report_honors_ctxlineage_dir_env(demo_dir, tmp_path):
    """Capture writes to $CTXLINEAGE_DIR; the read side must resolve it too."""
    out = tmp_path / "report.html"
    result = CliRunner().invoke(
        main,
        ["report", "--out", str(out)],
        env={"CTXLINEAGE_DIR": str(demo_dir)},
    )
    assert result.exit_code == 0, result.output
    assert out.exists()


def test_report_hints_when_no_calls(tmp_path):
    (tmp_path / "events.jsonl").write_text("")
    out = tmp_path / "report.html"
    result = CliRunner().invoke(main, ["report", "--dir", str(tmp_path), "--out", str(out)])
    assert result.exit_code == 0
    assert "no LLM calls were recorded" in result.output


def test_explicit_dir_overrides_ctxlineage_dir_env(demo_dir, tmp_path):
    """Precedence must match init(): explicit --dir wins over the env var."""
    out = tmp_path / "report.html"
    result = CliRunner().invoke(
        main,
        ["report", "--dir", str(demo_dir), "--out", str(out)],
        env={"CTXLINEAGE_DIR": str(tmp_path / "does_not_exist")},  # bad env, good flag
    )
    assert result.exit_code == 0, result.output
    assert out.exists()


def test_test_command_honors_ctxlineage_dir_env(demo_dir, tmp_path):
    config = tmp_path / "ctxlineage.toml"
    config.write_text("[[assert.window_budget]]\nmax_pct = 100\n")
    result = CliRunner().invoke(
        main,
        ["test", "-c", str(config)],
        env={"CTXLINEAGE_DIR": str(demo_dir)},
    )
    assert result.exit_code == 0, result.output
