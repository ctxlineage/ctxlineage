"""pytest integration: context contracts evaluated inside the suite (#72).

`ctxlineage test` gates a recorded run from outside, as a second CI step. This
runs the same rules where the events are actually produced — §9's analogue of
Elementary running inside `dbt build`.

**The one thing this buys over the CLI is per-test attribution.** The CLI can
only say "some call in this log blew the budget"; here the failure lands on the
test whose call did it. Everything below is arranged around that sentence.

Thin by construction: `_contract.run` / `has_failures` and the rules are reused
untouched. This module owns exactly two ideas of its own — *which events belong
to which test* (a byte cursor over the append-only log) and *how a finding
reaches the user* (a gate raises, anything else is reported).

**The tier rule (§6) is routed, never re-decided.** A rule's severity is the
rule's business: FAIL gates, WARN and SKIP are reported and never gate. The
failure mode to keep in mind here is subtler than a skip printing as a pass — in
pytest a test is *green by default*, so any recorded call this plugin fails to
evaluate becomes a silent pass. That is why events belonging to no test are
swept rather than dropped, and why the summary only ever says "passed" when
nothing was skipped (both pinned by tests in tests/test_pytest_plugin.py).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

# `ctxlineage._contract` (and `_report.normalize`, which it pulls in) is
# imported locally inside each function that needs it, not here at module
# level. The pytest11 entry point loads this module during pytest's plugin
# discovery, before pytest-cov attaches its tracer (same reasoning as
# `_capture_path`'s local `ctxlineage`/`_state` import below): importing
# anything from `_contract`, even just `runner`'s FAIL/WARN/SKIP constants,
# first runs `_contract/__init__.py`, which re-exports from `config` ->
# `rules` -> `_report.normalize`, pulling in the whole tree pre-coverage and
# permanently misreporting real, exercised code as uncovered on every suite
# run in this repo, not just this plugin's own.

HEADER = "ctxlineage contracts"
UNATTRIBUTED = "<unattributed>"


class ContextContractError(AssertionError):
    """A hard gate failed for the call(s) this test made.

    AssertionError so it reads as what it is: an assertion about the context
    this test put in the window, failing in the test that put it there.
    """


@dataclass(frozen=True)
class _Scope:
    """One evaluated slice of the log: a test, or the sweep."""

    label: str
    findings: list
    calls: int


def pytest_addoption(parser) -> None:
    group = parser.getgroup("ctxlineage", "context contracts (ctxlineage)")
    group.addoption(
        "--ctxlineage",
        action="store_true",
        default=False,
        help="Evaluate ctxlineage.toml context contracts per test, and fail a test "
        "whose calls breach a hard gate.",
    )
    group.addoption(
        "--ctxlineage-config",
        default=None,
        metavar="PATH",
        help="Contract config. Default: ctxlineage.toml in the rootdir.",
    )
    group.addoption(
        "--ctxlineage-dir",
        default=None,
        metavar="PATH",
        help="Where to record events. Default: a temporary directory, discarded "
        "after the run. Ignored when the app called ctxlineage.init() itself.",
    )
    parser.addini(
        "ctxlineage",
        "Evaluate ctxlineage context contracts per test (same as --ctxlineage).",
        type="bool",
        default=False,
    )


def pytest_configure(config) -> None:
    # Installed is not enabled. The pytest11 entry point loads this in every
    # suite with ctxlineage anywhere in its dependency tree, including
    # transitively; capturing, patching SDKs and failing tests because of that
    # would be hostile. Opt in explicitly, or commit the choice in the ini.
    if not (config.getoption("--ctxlineage") or config.getini("ctxlineage")):
        return
    config.pluginmanager.register(ContractPlugin(config), "ctxlineage-contracts")


def _load_rules(config) -> list:
    from ctxlineage import _contract

    given = config.getoption("--ctxlineage-config")
    path = Path(given) if given else Path(config.rootpath) / "ctxlineage.toml"
    try:
        return _contract.load(path)
    except _contract.ConfigError as exc:
        # A config that cannot be read is a usage error, not a test failure:
        # nothing was asserted, so failing tests would misattribute the cause.
        raise pytest.UsageError(f"ctxlineage: {exc}") from exc


def _capture_path(config) -> Path:
    from ctxlineage import _state

    if _state.is_configured():
        # The app owns capture. init() is first-call-wins, so calling it here
        # anyway would silently redirect the app's events into our directory —
        # that is host behaviour, and not ours to change.
        return _state.events_path()

    import ctxlineage

    directory = config.getoption("--ctxlineage-dir")
    if directory is None:
        directory = tempfile.mkdtemp(prefix="ctxlineage-pytest-")
    ctxlineage.init(directory)
    return _state.events_path()


class ContractPlugin:
    """Slices the append-only log per test and routes the findings.

    Attribution rides on a property of the writer rather than on any bookkeeping
    of its own: `EventWriter` appends unbuffered (it reopens the file per write),
    so the file size at two moments brackets exactly the events written between
    them. A test's window runs from the start of its protocol — so a
    function-scoped fixture's calls count as the test's — to the end of its call
    phase.

    Byte ranges no window covers (import time, teardown, session fixtures) are
    kept and swept at the end: unevaluated is not the same as fine.
    """

    def __init__(self, config) -> None:
        self._rules = _load_rules(config)
        self._path = _capture_path(config)
        self._cursor = 0  # end of the last window we evaluated
        self._start = 0  # the current test's window start
        self._gaps: list[tuple[int, int]] = []  # ranges no test owns
        self._scopes: list[_Scope] = []
        self._tests_run = 0  # call phases seen, to nudge when few produced calls

    def _size(self) -> int:
        try:
            return self._path.stat().st_size
        except OSError:
            return 0  # nothing recorded yet

    def _read(self, start: int, end: int) -> list:
        from ctxlineage._report import normalize

        if end <= start:
            return []
        with self._path.open("rb") as handle:
            handle.seek(start)
            raw = handle.read(end - start)
        events, _ = normalize.parse_events(raw.decode("utf-8", errors="replace"))
        return events

    def _evaluate(self, label: str, events: list) -> list:
        """Run the rules over one slice; record the scope. Returns the findings."""
        from ctxlineage import _contract
        from ctxlineage._report import normalize

        if not events:
            return []
        data = normalize.build_report_data(events)
        if not data["stats"]["calls"]:
            return []  # spans/tags but no call: nothing to assert against
        findings = _contract.run(data, self._rules)
        self._scopes.append(_Scope(label, findings, data["stats"]["calls"]))
        return findings

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        start = self._size()
        if start > self._cursor:
            # written since the last window closed: the previous test's
            # teardown, a session fixture, collection. Nobody's test — swept.
            self._gaps.append((self._cursor, start))
            self._cursor = start
        self._start = start
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item):
        from ctxlineage import _contract

        try:
            result = yield
        except BaseException:
            # The test failed for its own reason. That reason is the primary
            # fact and is never masked; the findings still reach the summary.
            self._close(item)
            raise
        findings = self._close(item)
        if _contract.has_failures(findings):
            raise ContextContractError(_message(findings))
        return result

    def _close(self, item) -> list:
        self._tests_run += 1
        end = self._size()
        events = self._read(self._start, end)
        self._cursor = max(self._cursor, end)
        return self._evaluate(item.nodeid, events)

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self, session, exitstatus) -> None:
        from ctxlineage import _contract

        end = self._size()
        if end > self._cursor:
            self._gaps.append((self._cursor, end))
            self._cursor = end
        events = [event for start, stop in self._gaps for event in self._read(start, stop)]
        findings = self._evaluate(UNATTRIBUTED, events)
        if _contract.has_failures(findings):
            # No test to fail: these calls belong to none. Gating the session is
            # the honest alternative to letting them pass unremarked.
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter) -> None:
        reporter = terminalreporter
        reporter.write_sep("=", HEADER)
        if not self._scopes:
            # Not a pass: nothing was asserted, and saying otherwise here is the
            # same lie as a green gate over an empty capture.
            reporter.write_line("0 calls recorded - no assertion was evaluated.")
            self._write_gateable_note(reporter)
            return
        for scope in self._scopes:
            if not scope.findings:
                continue
            reporter.write_line(f"{scope.label}")
            for finding in scope.findings:
                reporter.write_line(
                    f"  {finding.severity.upper():<4}  {finding.rule}: {finding.message}"
                )
        reporter.write_line(self._summary())
        self._write_gateable_note(reporter)

    def _write_gateable_note(self, reporter) -> None:
        """Nudge when tests ran but few produced a call to gate.

        A suite that mocks its LLM provider stubs the call site, so `init()`'s
        patch never fires and no events are recorded — the run is green because
        nothing was gated, not because the context was under budget. The plugin
        can't know a call was intended, but it can say how many tests produced
        one, so "green" is not mistaken for "gated" (#82).
        """
        gating = sum(1 for scope in self._scopes if scope.label != UNATTRIBUTED)
        if not self._tests_run or gating >= self._tests_run:
            return
        note = f"note: {gating} of {self._tests_run} test(s) produced a gateable LLM call"
        if gating == 0:
            note += " (provider mocked? this plugin gates real calls)"
        reporter.write_line(note)

    def _summary(self) -> str:
        from ctxlineage._contract.runner import FAIL, SKIP, WARN

        findings = [f for scope in self._scopes for f in scope.findings]
        counts = {
            level: sum(1 for f in findings if f.severity == level) for level in (FAIL, WARN, SKIP)
        }
        calls = sum(scope.calls for scope in self._scopes)
        scope = f"{len(self._rules)} assertion(s) over {calls} call(s)"
        tail = f"{counts['warn']} warning(s), {counts['skip']} skipped"
        if counts["fail"]:
            return f"{counts['fail']} check(s) failed - {scope}, {tail}"
        # "passed" only when something was actually checked — skips do not gate,
        # but summarising an unevaluated run as passing just moves the lie from
        # the rule to the summary (the same rule `ctxlineage test` follows).
        if counts["skip"]:
            return f"No hard-gate failures - {scope}, {tail}"
        return f"All {scope} passed - {tail}"


def _message(findings) -> str:
    from ctxlineage._contract.runner import FAIL

    lines = [f"{f.severity.upper():<4}  {f.rule}: {f.message}" for f in findings]
    gates = sum(1 for f in findings if f.severity == FAIL)
    return "\n".join(
        [f"ctxlineage: {gates} context contract hard gate(s) failed for this test", "", *lines]
    )
