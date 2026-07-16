"""Coexistence matrix: ctxlineage + the langfuse.openai drop-in (#26).

Both libraries wrap the same SDK surface (Completions.create via wrapt), so
each (wrap order, call mode) cell runs in a subprocess — patched SDK state
cannot be undone within one interpreter. The scenario script asserts nothing
itself; it reports both sides' captures as JSON and the assertions live here.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCENARIO = Path(__file__).parent / "coexistence" / "langfuse_openai_scenario.py"


def _run_scenario(order: str, mode: str, events_dir: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCENARIO), order, mode, str(events_dir)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=events_dir,
    )
    assert proc.returncode == 0, f"scenario crashed:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("order", ["ctxlineage-first", "langfuse-first"])
@pytest.mark.parametrize("mode", ["plain", "stream"])
def test_coexists_with_langfuse_openai_dropin(tmp_path, order, mode):
    result = _run_scenario(order, mode, tmp_path)

    # the call itself survives double-wrapping
    expected_text = "Hello world" if mode == "stream" else "Hello there!"
    assert result["response_text"] == expected_text

    # ctxlineage side: exactly one event, correct payload
    (event,) = result["ctx_events"]
    payload = event["payload"]
    assert payload["provider"] == "openai"
    assert payload["api"] == "chat.completions"
    if mode == "stream":
        assert payload["stream"] is True
        assert payload["response"]["content"]["0"] == "Hello world"
        assert payload["usage"]["total_tokens"] == 11
    else:
        assert payload["response"]["choices"][0]["message"]["content"] == "Hello there!"
        assert payload["usage"]["total_tokens"] == 12

    # langfuse side: still exports spans that saw the request content
    assert result["decode_errors"] == []
    assert result["langfuse_span_count"] >= 1, result
    assert result["langfuse_saw_input"], result
