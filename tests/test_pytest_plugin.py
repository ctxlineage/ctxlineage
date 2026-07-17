"""The pytest plugin — contracts evaluated inside the suite, attributed to tests.

Every test here runs a *real* pytest process over a generated sub-suite, rather
than exercising the hooks in-process: the plugin calls `ctxlineage.init()`,
which patches the SDKs process-globally, so an in-process run would leak capture
into this suite. The sub-suites make real openai SDK calls against `respx`, so
what is asserted is the whole chain — capture, slicing, rules, gate.

The load-bearing tests are attribution (what the CLI structurally cannot do) and
the tier rule (§6): `test_skip_*` and `test_warning_*` are the ones that would
let #63 back in through a new door.
"""

import re
import subprocess
import sys

import pytest

# A sub-suite conftest: hermetic token estimation + one mocked, recorded call.
CONFTEST = '''
import httpx
import pytest
import respx
from ctxlineage._report import tokens


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Never let tiktoken reach the network from a sub-suite."""
    monkeypatch.setattr(tokens, "_encoding_for", lambda model: None)


def call_llm(prompt_tokens=9, model="gpt-4o-mini"):
    """One real openai call over mocked HTTP, reporting `prompt_tokens`."""
    import openai

    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "created": 1765500000,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": 2,
                        "total_tokens": prompt_tokens + 2,
                    },
                },
            )
        )
        openai.OpenAI(api_key="test-key").chat.completions.create(
            model=model, messages=[{"role": "user", "content": "hi"}]
        )
'''

# 0.5% of gpt-4o-mini's 128k window is 640 tokens: 9 passes, 5000 breaches.
BUDGET = "[[assert.window_budget]]\nmax_pct = 0.5\n"
PASS_TOKENS = 9
FAIL_TOKENS = 5000


def contract_section(stdout: str) -> str:
    """Just the plugin's own summary section.

    Splitting on the header alone would swallow pytest's trailing '1 passed in
    0.02s' line and make 'does not claim a pass' assertions pass for the wrong
    reason. The section ends at the next '=' separator or pytest's timing line
    (which '-q' prints without a separator).
    """
    out: list[str] = []
    started = False
    for line in stdout.splitlines():
        if not started:
            started = "ctxlineage contracts" in line
            continue
        if line.startswith("=") or re.search(r" in \d[\d.]*s$", line):
            break
        out.append(line)
    assert started, f"no contract section in output:\n{stdout}"
    return "\n".join(out)


@pytest.fixture
def suite(tmp_path):
    """Write a sub-suite to tmp_path and run real pytest over it."""

    def _make(tests, config=BUDGET, conftest=CONFTEST):
        (tmp_path / "conftest.py").write_text(conftest, encoding="utf-8")
        (tmp_path / "test_suite.py").write_text(tests, encoding="utf-8")
        if config is not None:
            (tmp_path / "ctxlineage.toml").write_text(config, encoding="utf-8")

        def _run(*args):
            return subprocess.run(
                [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *args],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=180,
            )

        return _run

    return _make


def test_a_breach_fails_the_test_that_caused_it(suite):
    """The deliverable: a contract breach surfaces as a failure of *that* test."""
    run = suite(
        "from conftest import call_llm\n"
        "\n"
        "def test_blows_the_budget():\n"
        f"    call_llm(prompt_tokens={FAIL_TOKENS})\n"
    )
    result = run("--ctxlineage")
    assert result.returncode == 1, result.stdout
    assert "test_blows_the_budget" in result.stdout
    assert "window_budget" in result.stdout
    assert "1 failed" in result.stdout


def test_a_passing_call_stays_green(suite):
    run = suite(
        "from conftest import call_llm\n"
        "\n"
        "def test_within_budget():\n"
        f"    call_llm(prompt_tokens={PASS_TOKENS})\n"
    )
    result = run("--ctxlineage")
    assert result.returncode == 0, result.stdout
    assert "1 passed" in result.stdout


def test_only_the_breaching_test_fails(suite):
    """Attribution — the whole reason this exists rather than `ctxlineage test`.

    The CLI can only say "some call in this log blew the budget". If the innocent
    test fails too, the plugin has bought nothing over it.

    The guilty test runs *first* on purpose: a plugin that scored each test
    against the whole log so far (no real per-test slicing) would then also fail
    the innocent test, since the breaching call is already on disk by the time it
    runs. Ordering it this way is what makes this a real attribution test.
    """
    run = suite(
        "from conftest import call_llm\n"
        "\n"
        "def test_guilty():\n"
        f"    call_llm(prompt_tokens={FAIL_TOKENS})\n"
        "\n"
        "def test_innocent():\n"
        f"    call_llm(prompt_tokens={PASS_TOKENS})\n"
    )
    result = run("--ctxlineage")
    assert result.returncode == 1, result.stdout
    assert "1 failed, 1 passed" in result.stdout
    assert "test_guilty" in result.stdout
    # the innocent test must not be named as a failure
    assert "test_innocent" not in result.stdout.split("short test summary")[-1]


def test_a_tests_own_failure_is_not_masked(suite):
    """A test that fails on its own reason keeps that reason."""
    run = suite(
        "from conftest import call_llm\n"
        "\n"
        "def test_own_reason():\n"
        f"    call_llm(prompt_tokens={FAIL_TOKENS})\n"
        "    assert False, 'my own reason'\n"
    )
    result = run("--ctxlineage")
    assert result.returncode == 1
    assert "my own reason" in result.stdout


def test_skip_does_not_fail_the_test(suite):
    """Tier rule (§6): an unevaluated assertion never gates."""
    run = suite(
        "from conftest import call_llm\n"
        "\n"
        "def test_unknown_window():\n"
        "    call_llm(prompt_tokens=99, model='mystery-model-1')\n"
    )
    result = run("--ctxlineage")
    assert result.returncode == 0, result.stdout
    assert "1 passed" in result.stdout


def test_skip_is_never_reported_as_a_pass(suite):
    """#63 in a new place: a green test whose contract was never evaluated,
    summarised as if it had been, is the exact lie this track exists to prevent.

    The test itself is green — it passed its own assertions, and a skip must not
    change that. What must not happen is the plugin *claiming* the contract
    passed.
    """
    run = suite(
        "from conftest import call_llm\n"
        "\n"
        "def test_unknown_window():\n"
        "    call_llm(prompt_tokens=99, model='mystery-model-1')\n"
    )
    result = run("--ctxlineage")
    assert result.returncode == 0, result.stdout
    section = contract_section(result.stdout)
    assert "SKIP" in section
    assert "not evaluated" in section
    assert "1 skipped" in section
    # the summary must not claim a pass it did not earn
    assert "passed" not in section.lower()
    assert "No hard-gate failures" in section


def test_a_skip_does_not_suppress_a_real_failure(suite):
    """A skip in one test must not soften the gate in another."""
    run = suite(
        "from conftest import call_llm\n"
        "\n"
        "def test_skipped_window():\n"
        "    call_llm(prompt_tokens=99, model='mystery-model-1')\n"
        "\n"
        "def test_guilty():\n"
        f"    call_llm(prompt_tokens={FAIL_TOKENS})\n"
    )
    result = run("--ctxlineage")
    assert result.returncode == 1, result.stdout
    assert "1 failed, 1 passed" in result.stdout
    assert "test_guilty" in result.stdout


def test_warnings_alone_do_not_fail_the_run(suite):
    """Tier rule: an untagged name is advisory, never a gate."""
    run = suite(
        "from conftest import call_llm\n"
        "\n"
        "def test_untagged():\n"
        f"    call_llm(prompt_tokens={PASS_TOKENS})\n",
        config='[[assert.grounded]]\ntag = "never_tagged_anywhere"\n',
    )
    result = run("--ctxlineage")
    assert result.returncode == 0, result.stdout
    assert "WARN" in result.stdout


def test_inert_without_the_flag(suite):
    """Installed is not enabled. A plugin that changes a shared suite's
    behaviour because ctxlineage is a transitive dependency is hostile."""
    run = suite(
        "from conftest import call_llm\n"
        "\n"
        "def test_blows_the_budget():\n"
        f"    call_llm(prompt_tokens={FAIL_TOKENS})\n"
    )
    result = run()
    assert result.returncode == 0, result.stdout
    assert "1 passed" in result.stdout
    assert "ctxlineage" not in result.stdout.lower()


def test_a_tag_that_never_landed_fails_its_test(suite):
    """`grounded` gates where the tag made the lineage exact — per test."""
    run = suite(
        "import ctxlineage\n"
        "from conftest import call_llm\n"
        "\n"
        "def test_tag_never_reaches_the_window():\n"
        "    with ctxlineage.span('build') as s:\n"
        "        s.tag('memory', 'never injected anywhere')\n"
        f"        call_llm(prompt_tokens={PASS_TOKENS})\n",
        config='[[assert.grounded]]\ntag = "memory"\n',
    )
    result = run("--ctxlineage")
    assert result.returncode == 1, result.stdout
    assert "test_tag_never_reaches_the_window" in result.stdout
    assert "memory" in result.stdout


def test_events_outside_any_test_are_swept_not_dropped(suite):
    """A call no test owns (here: at import time) must still be evaluated.

    Dropping it would be the §63 failure by omission — an unchecked call that
    reads as a green suite.
    """
    run = suite(
        "from conftest import call_llm\n"
        "\n"
        f"call_llm(prompt_tokens={FAIL_TOKENS})  # at collection, inside no test\n"
        "\n"
        "def test_innocent():\n"
        f"    call_llm(prompt_tokens={PASS_TOKENS})\n"
    )
    result = run("--ctxlineage")
    assert result.returncode != 0, result.stdout
    assert "unattributed" in result.stdout.lower()


def test_zero_recorded_calls_never_reads_as_passed(suite):
    """Nothing was asserted; saying so is honest, claiming a pass is not."""
    run = suite("def test_no_llm_call():\n    assert True\n")
    result = run("--ctxlineage")
    assert result.returncode == 0, result.stdout
    section = contract_section(result.stdout)
    assert "passed" not in section.lower()
    assert "0 calls recorded" in section


def test_missing_config_is_a_usage_error(suite):
    run = suite(
        "def test_anything():\n    assert True\n",
        config=None,
    )
    result = run("--ctxlineage")
    assert result.returncode != 0
    assert "ctxlineage.toml" in result.stdout + result.stderr


def test_invalid_config_is_a_usage_error(suite):
    run = suite(
        "def test_anything():\n    assert True\n",
        config="[[assert.window_budget]]\nmax_pct = 900\n",
    )
    result = run("--ctxlineage")
    assert result.returncode != 0
    assert "max_pct" in result.stdout + result.stderr
