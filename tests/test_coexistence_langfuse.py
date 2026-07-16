"""Coexistence matrix: ctxlineage + the langfuse.openai drop-in (#26).

Both libraries wrap the same SDK surface (Completions.create via wrapt), so
each (wrap order, call mode) cell runs in a subprocess — patched SDK state
cannot be undone within one interpreter. The scenario script asserts nothing
itself; it reports both sides' captures as JSON and the assertions live here.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCENARIO = Path(__file__).parent / "coexistence" / "langfuse_openai_scenario.py"
RESULT_MARKER = "CTXL_RESULT: "

# A developer's real env must not reach the scenario: LANGFUSE_BASE_URL would
# outrank the scenario's sink URL (sending test payloads to a real host),
# OPENAI_BASE_URL would redirect the mocked call, proxies break the local sink.
_STRIPPED_PREFIXES = ("LANGFUSE_", "OPENAI_", "ANTHROPIC_", "OTEL_", "CTXLINEAGE")


def _sanitized_env() -> dict:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(_STRIPPED_PREFIXES) and "PROXY" not in key.upper()
    }


def _run_scenario(order: str, mode: str, events_dir: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCENARIO), order, mode, str(events_dir)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=events_dir,
        env=_sanitized_env(),
    )
    assert proc.returncode == 0, f"scenario crashed:\n{proc.stderr}"
    results = [
        line.removeprefix(RESULT_MARKER)
        for line in proc.stdout.splitlines()
        if line.startswith(RESULT_MARKER)
    ]
    assert len(results) == 1, f"expected one result line, stdout was:\n{proc.stdout}"
    return json.loads(results[0])


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

    # langfuse side: exactly one span (>1 would be double-counting, the #26
    # failure mode) that saw both the request and the (assembled) response —
    # i.e. our proxy did not starve langfuse's own stream accumulation.
    assert result["decode_errors"] == []
    assert result["langfuse_span_count"] == 1, result
    assert result["langfuse_saw_input"], result
    assert result["langfuse_saw_output"], result
