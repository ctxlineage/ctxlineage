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
    assert data["stats"]["sessions"] == 2


def test_report_missing_dir_fails_gracefully(tmp_path):
    result = CliRunner().invoke(main, ["report", "--dir", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "No events found" in result.output


def test_skipped_lines_reported(tmp_path):
    (tmp_path / "events.jsonl").write_text("not json\n")
    result = CliRunner().invoke(main, ["report", "--dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "1 malformed" in result.output
