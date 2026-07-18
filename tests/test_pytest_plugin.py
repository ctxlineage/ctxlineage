"""The pytest plugin — contracts evaluated inside the suite, attributed to tests.

Each test runs a sub-pytest over a generated sub-suite. The bulk run **in
process** via the `pytester` fixture, for two reasons: coverage instrumentation
can see the plugin code (a subprocess run cannot, which reads as 0% coverage
over genuinely-exercised code), and it is the idiomatic way to test a `pytest11`
plugin. The sub-suites make real openai SDK calls against `respx`, so what is
asserted is the whole chain — capture, slicing, rules, gate — not the hooks in
isolation. `init()` patches the SDKs process-globally, but `install()` is
idempotent (guarded by `_installed_providers`), so repeated in-process runs do
not stack wrappers.

One end-to-end test (`test_real_subprocess_end_to_end`) still shells out to a
fresh `python -m pytest`, because the in-process path cannot catch an entry-point
or registration break — the plugin is already imported in the parent.

The load-bearing tests are attribution (what the CLI structurally cannot do) and
the tier rule (§6): `test_skip_*` and `test_warning_*` are the ones that would
let #63 back in through a new door.
"""

import importlib
import re
import subprocess
import sys

import httpx
import pytest
import respx

CHAT_URL = "https://api.openai.com/v1/chat/completions"

# A sub-suite conftest: hermetic token estimation + a `call_llm` fixture that
# makes one mocked, recorded openai call reporting a chosen prompt-token count.
CONFTEST = '''
import httpx
import pytest
import respx
from ctxlineage._report import tokens


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Never let tiktoken reach the network from a sub-suite."""
    monkeypatch.setattr(tokens, "_encoding_for", lambda model: None)


def _mock_call(prompt_tokens, model):
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


@pytest.fixture
def call_llm():
    def _call(prompt_tokens=9, model="gpt-4o-mini"):
        _mock_call(prompt_tokens, model)

    return _call
'''

# 0.5% of gpt-4o-mini's 128k window is 640 tokens: 9 passes, 5000 breaches.
BUDGET = "[[assert.window_budget]]\nmax_pct = 0.5\n"
PASS_TOKENS = 9
FAIL_TOKENS = 5000


def contract_section(stdout: str) -> str:
    """Just the plugin's own summary section, from its header to the next '='
    separator (or pytest's timing line under '-q', which has no separator).

    Splitting on the header alone would swallow pytest's trailing 'N passed'
    line and make 'does not claim a pass' assertions pass for the wrong reason.
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
def suite(pytester):
    """Write a sub-suite into pytester's tmp dir and run it in-process.

    Returns a `run(*args)` that invokes the sub-pytest; pass '--ctxlineage' to
    enable the plugin. The conftest supplies the `call_llm` fixture.
    """

    def _make(tests, config=BUDGET, conftest=CONFTEST):
        if conftest is not None:
            pytester.makeconftest(conftest)
        pytester.makepyfile(test_suite=tests)
        if config is not None:
            pytester.makefile(".toml", ctxlineage=config)

        def _run(*args):
            # Disable pytest-playwright in the sub-run: it is installed in CI
            # (a sibling feature's browser tests) and wraps `pytest_runtest_call`
            # with a process-global "soft assertion scope" that refuses to nest.
            # Because runpytest_inprocess runs the sub-pytest inside this parent
            # test — itself already inside playwright's scope — the sub-run's
            # wrapper would raise "nested soft assertion scopes are not
            # supported". '-p no:<name>' is a no-op when the plugin is absent.
            #
            # The sub-pytest gets a throwaway rootdir with no config, so mirror
            # the repo's async-fixture loop scope here too — otherwise every
            # sub-run re-emits the pytest-asyncio "unset default" deprecation.
            return pytester.runpytest_inprocess(
                "-p",
                "no:playwright",
                "-o",
                "asyncio_default_fixture_loop_scope=function",
                *args,
            )

        return _run

    return _make


def test_a_breach_fails_the_test_that_caused_it(suite):
    """The deliverable: a contract breach surfaces as a failure of *that* test."""
    run = suite(
        f"def test_blows_the_budget(call_llm):\n    call_llm(prompt_tokens={FAIL_TOKENS})\n"
    )
    result = run("--ctxlineage")
    assert result.ret != 0
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*test_blows_the_budget*", "*window_budget*"])


def test_a_passing_call_stays_green(suite):
    run = suite(f"def test_within_budget(call_llm):\n    call_llm(prompt_tokens={PASS_TOKENS})\n")
    result = run("--ctxlineage")
    assert result.ret == 0, result.stdout.str()
    result.assert_outcomes(passed=1)


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
        "def test_guilty(call_llm):\n"
        f"    call_llm(prompt_tokens={FAIL_TOKENS})\n"
        "\n"
        "def test_innocent(call_llm):\n"
        f"    call_llm(prompt_tokens={PASS_TOKENS})\n"
    )
    result = run("--ctxlineage")
    assert result.ret != 0
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(["*test_guilty*"])


def test_a_tests_own_failure_is_not_masked(suite):
    """A test that fails on its own reason keeps that reason."""
    run = suite(
        "def test_own_reason(call_llm):\n"
        f"    call_llm(prompt_tokens={FAIL_TOKENS})\n"
        "    assert False, 'my own reason'\n"
    )
    result = run("--ctxlineage")
    assert result.ret != 0
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*my own reason*"])


def test_skip_does_not_fail_the_test(suite):
    """Tier rule (§6): an unevaluated assertion never gates."""
    run = suite(
        "def test_unknown_window(call_llm):\n"
        "    call_llm(prompt_tokens=99, model='mystery-model-1')\n"
    )
    result = run("--ctxlineage")
    assert result.ret == 0, result.stdout.str()
    result.assert_outcomes(passed=1)


def test_skip_is_never_reported_as_a_pass(suite):
    """#63 in a new place: a green test whose contract was never evaluated,
    summarised as if it had been, is the exact lie this track exists to prevent.

    The test itself is green — it passed its own assertions, and a skip must not
    change that. What must not happen is the plugin *claiming* the contract
    passed.
    """
    run = suite(
        "def test_unknown_window(call_llm):\n"
        "    call_llm(prompt_tokens=99, model='mystery-model-1')\n"
    )
    result = run("--ctxlineage")
    assert result.ret == 0, result.stdout.str()
    section = contract_section(result.stdout.str())
    assert "SKIP" in section
    assert "not evaluated" in section
    assert "1 skipped" in section
    # the summary must not claim a pass it did not earn
    assert "passed" not in section.lower()
    assert "No hard-gate failures" in section


def test_a_skip_does_not_suppress_a_real_failure(suite):
    """A skip in one test must not soften the gate in another."""
    run = suite(
        "def test_skipped_window(call_llm):\n"
        "    call_llm(prompt_tokens=99, model='mystery-model-1')\n"
        "\n"
        "def test_guilty(call_llm):\n"
        f"    call_llm(prompt_tokens={FAIL_TOKENS})\n"
    )
    result = run("--ctxlineage")
    assert result.ret != 0
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(["*test_guilty*"])


def test_warnings_alone_do_not_fail_the_run(suite):
    """Tier rule: an untagged name is advisory, never a gate."""
    run = suite(
        f"def test_untagged(call_llm):\n    call_llm(prompt_tokens={PASS_TOKENS})\n",
        config='[[assert.grounded]]\ntag = "never_tagged_anywhere"\n',
    )
    result = run("--ctxlineage")
    assert result.ret == 0, result.stdout.str()
    result.assert_outcomes(passed=1)
    assert "WARN" in contract_section(result.stdout.str())


def test_inert_without_the_flag(suite):
    """Installed is not enabled. A plugin that changes a shared suite's
    behaviour because ctxlineage is a transitive dependency is hostile."""
    run = suite(
        f"def test_blows_the_budget(call_llm):\n    call_llm(prompt_tokens={FAIL_TOKENS})\n"
    )
    result = run()  # no --ctxlineage
    assert result.ret == 0, result.stdout.str()
    result.assert_outcomes(passed=1)
    assert "ctxlineage contracts" not in result.stdout.str()


def test_enabled_via_ini_without_the_flag(suite):
    """The opt-in can be committed in the ini instead of retyped each run."""
    run = suite(
        f"def test_blows_the_budget(call_llm):\n    call_llm(prompt_tokens={FAIL_TOKENS})\n"
    )
    result = run("-o", "ctxlineage=true")  # enabled by ini, no flag
    assert result.ret != 0
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*window_budget*"])


def test_a_tag_that_never_landed_fails_its_test(suite):
    """`grounded` gates where the tag made the lineage exact — per test."""
    run = suite(
        "import ctxlineage\n"
        "\n"
        "def test_tag_never_reaches_the_window(call_llm):\n"
        "    with ctxlineage.span('build') as s:\n"
        "        s.tag('memory', 'never injected anywhere')\n"
        f"        call_llm(prompt_tokens={PASS_TOKENS})\n",
        config='[[assert.grounded]]\ntag = "memory"\n',
    )
    result = run("--ctxlineage")
    assert result.ret != 0
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*test_tag_never_reaches_the_window*", "*memory*"])


def test_events_outside_any_test_are_swept_not_dropped(suite):
    """A call no test owns (here: at import time) must still be evaluated.

    Dropping it would be the #63 failure by omission — an unchecked call that
    reads as a green suite.
    """
    run = suite(
        "from conftest import _mock_call\n"
        "\n"
        f"_mock_call({FAIL_TOKENS}, 'gpt-4o-mini')  # at collection, inside no test\n"
        "\n"
        "def test_innocent(call_llm):\n"
        f"    call_llm(prompt_tokens={PASS_TOKENS})\n"
    )
    result = run("--ctxlineage")
    assert result.ret != 0, result.stdout.str()
    assert "unattributed" in result.stdout.str().lower()


def test_zero_recorded_calls_never_reads_as_passed(suite):
    """Nothing was asserted; saying so is honest, claiming a pass is not."""
    run = suite("def test_no_llm_call():\n    assert True\n")
    result = run("--ctxlineage")
    assert result.ret == 0, result.stdout.str()
    section = contract_section(result.stdout.str())
    assert "passed" not in section.lower()
    assert "0 calls recorded" in section


def test_missing_config_is_a_usage_error(suite):
    run = suite(
        "def test_anything():\n    assert True\n",
        config=None,
    )
    result = run("--ctxlineage")
    assert result.ret != 0
    assert "ctxlineage.toml" in result.stdout.str() + result.stderr.str()


def test_invalid_config_is_a_usage_error(suite):
    run = suite(
        "def test_anything():\n    assert True\n",
        config="[[assert.window_budget]]\nmax_pct = 900\n",
    )
    result = run("--ctxlineage")
    assert result.ret != 0
    assert "max_pct" in result.stdout.str() + result.stderr.str()


def test_real_subprocess_end_to_end(tmp_path):
    """One genuine `python -m pytest` process, so an entry-point or registration
    break — invisible to the in-process runs, where the plugin is already
    imported — cannot pass silently. Coverage of the plugin comes from the
    in-process tests above; this one guards the real invocation path.
    """
    (tmp_path / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (tmp_path / "ctxlineage.toml").write_text(BUDGET, encoding="utf-8")
    (tmp_path / "test_suite.py").write_text(
        f"def test_blows_the_budget(call_llm):\n    call_llm(prompt_tokens={FAIL_TOKENS})\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--ctxlineage"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 1, result.stdout
    assert "test_blows_the_budget" in result.stdout
    assert "window_budget" in result.stdout


# --- Direct unit tests of the routing layer -------------------------------
#
# A handful of branches are awkward to reach through a sub-suite because of
# hook ordering (an app that inits before the plugin configures; events that
# land only at session teardown). Exercising them directly is both simpler and
# more deterministic than contorting a sub-suite to reproduce the timing.


class _FakeConfig:
    """Enough of a pytest Config for the plugin's constructor/helpers."""

    def __init__(self, rootpath):
        self.rootpath = rootpath

    def getoption(self, name):
        return None  # no --ctxlineage-config / --ctxlineage-dir override


@respx.mock
def _record_breaching_call(directory):
    """Record one real, budget-breaching openai call into `directory`."""
    import ctxlineage

    ctxlineage.init(directory)
    respx.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1765500000,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": FAIL_TOKENS,
                    "completion_tokens": 2,
                    "total_tokens": FAIL_TOKENS + 2,
                },
            },
        )
    )
    import openai

    openai.OpenAI(api_key="test-key").chat.completions.create(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}]
    )


def test_capture_path_honours_an_app_that_already_inited(tmp_path):
    """When the app owns capture, the plugin uses its directory and never
    re-inits — init() is first-call-wins, so claiming it would redirect the
    app's own events."""
    import ctxlineage
    from ctxlineage import _state
    from ctxlineage._pytest_plugin import _capture_path

    ctxlineage.init(tmp_path)  # the app configured capture first
    path = _capture_path(_FakeConfig(tmp_path))
    assert path == _state.events_path()
    assert path.parent == tmp_path


def test_evaluate_records_nothing_when_a_slice_has_no_calls(tmp_path):
    """A slice with spans/tags but no llm_call is not an assertable scope."""
    from ctxlineage._pytest_plugin import ContractPlugin

    (tmp_path / "ctxlineage.toml").write_text(BUDGET, encoding="utf-8")
    plugin = ContractPlugin(_FakeConfig(tmp_path))
    span_only = [
        {
            "event_type": "span_start",
            "span_id": "s1",
            "session_id": "sess",
            "payload": {"name": "build"},
        }
    ]
    assert plugin._evaluate("scope", span_only) == []
    assert plugin._scopes == []


def test_sessionfinish_sweeps_and_gates_on_trailing_events(tmp_path):
    """Events that land after the last test (session teardown, module teardown)
    belong to no test; the final sweep must still evaluate and gate on them."""
    from ctxlineage._pytest_plugin import UNATTRIBUTED, ContractPlugin

    (tmp_path / "ctxlineage.toml").write_text(BUDGET, encoding="utf-8")
    _record_breaching_call(tmp_path)  # written while the plugin's cursor is 0

    plugin = ContractPlugin(_FakeConfig(tmp_path))  # reuses the app's dir

    class _Session:
        exitstatus = 0

    session = _Session()
    plugin.pytest_sessionfinish(session, 0)
    assert any(scope.label == UNATTRIBUTED for scope in plugin._scopes)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED


def test_module_reimports_cleanly():
    """Re-importing the plugin is safe and keeps its hooks intact.

    It also lets coverage account for the module's import-time definition lines:
    a `pytest11` plugin is imported by pytest's entry-point loader *before*
    pytest-cov starts tracing, so under `pytest --cov` those lines would
    otherwise read as uncovered though they run on every startup.
    """
    module = importlib.import_module("ctxlineage._pytest_plugin")
    reloaded = importlib.reload(module)
    assert hasattr(reloaded, "pytest_configure")
    assert hasattr(reloaded, "pytest_addoption")
    assert issubclass(reloaded.ContextContractError, AssertionError)
