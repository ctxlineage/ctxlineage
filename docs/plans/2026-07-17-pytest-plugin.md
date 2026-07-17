# pytest plugin: run context contracts inside the test suite (#72)

Status: implementing. Issue: #72. Vision: `docs/vision/context-contract-testing.md`
§9 (integration points) and §6 (the tier rule).

## 1. Why this exists at all

`ctxlineage test` already gates a recorded run. Today that is a bolted-on second
step:

```yaml
- run: pytest tests/       # the suite produces events
- run: ctxlineage test     # a separate step re-reads the JSONL
```

It works. But the events and the assertions live in two different places, and
the CLI can only ever say *"some call in this log blew the budget"*. pytest is
already running the app that produced the events — §9's analogue of Elementary
running inside `dbt build` is for the contracts to run there too.

**So the value of this plugin over the CLI is exactly one thing: per-test
attribution.** "`test_agent_loop` blew the window budget" is a sentence the CLI
structurally cannot say. If this plugin delivers anything less, it is a worse
copy of `ctxlineage test` and should not be merged.

That single sentence decides every open question below.

## 2. Non-negotiable: the tier rule (§6) holds identically here

A rule gates only where its evidence is exact; inference warns; anything
unevaluated is reported as **skipped, never as a pass**.

The plugin **routes** severities. It never derives them: `_contract.run()` and
`_contract.has_failures()` are reused as-is, and the rules keep owning the tier
decision. There is no code path in this plugin that inspects a rule's subject
matter and decides how bad it is.

The specific way this could go wrong here — and the reason #63 and #71 are cited
in the issue — is subtler than "a skip prints as a pass". In pytest, **a test is
green by default**. So any recorded call that the plugin fails to evaluate
becomes a green test with no finding at all, and the user reads the green suite
as "my contracts hold". That is #63's lie with a new delivery mechanism: the
claim of having checked, without the check.

This shapes two decisions that would otherwise look like gold-plating:

- **the unattributed sweep** (§3.1) — events produced outside any test's window
  must still be evaluated, or they are silently unchecked;
- **the summary's wording** (§3.4) — it mirrors the CLI's rule that "passed" is
  only ever printed when nothing was skipped.

Both are pinned by tests.

## 3. Decisions

### 3.1 Per-test, plus a sweep for what no test owns — *not* session-scoped

**Decided: per-test.** Session-scoped is closer to `ctxlineage test` and safer,
which is precisely the argument against it: it is the thing we already have.

Mechanism — the event log makes this nearly free. `EventWriter` is append-only
and **unbuffered** (it reopens the file per write, `_events.py`), so a byte
offset is a reliable cursor:

1. bracket each test: record `events.jsonl`'s size at the start of its protocol
   (so a function-scoped fixture's calls count as the test's) ;
2. at the end of the **call** phase, read the bytes written since;
3. `build_report_data(slice)` → `_contract.run(data, rules)` → findings owned by
   that test.

`build_report_data` groups by session and derives everything per call, so it is
happy with a slice (verified: sessions come from the `llm_call` events present).

**The sweep.** Events written outside every test window — session-scoped
fixtures, import time, teardown — belong to no test. Dropping them would be the
§2 failure exactly. So at `pytest_sessionfinish` the byte-range complement is
evaluated as an `<unattributed>` scope; a hard gate there fails the run via the
session exit status. Every recorded call is therefore evaluated exactly once,
and attributed where attribution is possible.

**Known limitation (documented, not fixed):** slicing is per-process. Under
`pytest-xdist` each worker gets its own tmp dir by default, so offsets stay
sound; pointing every worker at one shared `--ctxlineage-dir` would interleave
writes and make attribution meaningless. Out of scope for this slice.

**Known limitation (honest, worth writing down):** a per-test slice sees only
the spans/tags declared inside that window. A tag declared by a session-scoped
fixture is not in the test's slice, so `grounded` finds no tag and **warns**
rather than gating (§6's own demotion — a warn, never a false pass or a false
fail). The consequence is real and belongs in the docs: `window_budget` is the
rule per-test attribution flatters; `grounded` is most natural over the whole
run, which is what `ctxlineage test` is still for.

### 3.2 Opt-in flag — *not* config-driven

**Decided: `--ctxlineage`, inert until passed.** Plus a `ctxlineage = true` ini
option so a project can commit the choice rather than retype the flag.

A `pytest11` entry point loads in *every* suite that has ctxlineage anywhere in
its dependency tree, including transitively. A plugin that starts capturing,
patching SDKs and failing tests because of that is hostile — the issue's word,
and the right one. Enabling on "a `ctxlineage.toml` exists" is the same failure
wearing a config file: still implicit, still not something the person running
the suite asked for.

Registered-but-not-enabled must therefore be **completely** inert: no `init()`,
no patching, no hooks that do work. Pinned by a test.

Also: `--ctxlineage-config` (default `ctxlineage.toml`) and `--ctxlineage-dir`
(default: a per-session tmp dir, thrown away).

### 3.3 The plugin owns `init()` — but only if the app has not

**Decided: own it conditionally.** `_state.is_configured()` decides. If the app
already called `ctxlineage.init()`, use its directory and never re-init.

This is not just friendliness. `init()` is *first-call-wins*, so a plugin that
calls it unconditionally at `pytest_configure` would silently win over an app
that inits later at import time and redirect its events into our tmp dir. That
is "touching host behaviour", and the check is what avoids it.

The residual honesty cost is small and explicit: when you pass `--ctxlineage`
and your app never inits, the plugin owns capture for the session. You asked for
that by passing the flag; `--ctxlineage-dir` is there when you want the events
kept.

### 3.4 Reporting: real pytest failures for gates, a summary section for the rest

**Decided: both, split by severity — because they answer different questions.**

- **FAIL** → raise `ContextContractError` from a `pytest_runtest_call` wrapper,
  carrying the runner's finding lines verbatim. A real test failure, in the test
  that caused it, with pytest's own output. This is the deliverable.
  - New-style `@pytest.hookimpl(wrapper=True)` (pytest ≥ 8; old-style
    `hookwrapper` raising post-yield emits `PluggyTeardownRaisedWarning` — the
    wrong tool, verified by spike).
  - If the test already failed on its own, **re-raise its exception and never
    mask it**. Its own failure is the primary fact; the findings still reach the
    summary.
- **WARN / SKIP** → never gate, per §6. They go to a `ctxlineage contracts`
  terminal summary section, per-test attributed. A skip that fails a test would
  break the tier rule as surely as a skip that passes one hides it.
- **Summary wording** mirrors `_cli.test` exactly: `passed` is printed only when
  nothing was skipped, otherwise `No hard-gate failures`. Zero recorded calls
  says so plainly and never claims a pass.

**Zero recorded calls: warn, do not fail** — a deliberate divergence from the
CLI, which errors. `ctxlineage test` errors because you pointed it at a log and
asked it to gate; `pytest -k something_unrelated --ctxlineage` recording no
calls is normal pytest usage, not a broken capture. The lie in #63 was the
*claim* ("All 1 assertion(s) over 2 call(s) passed"); "0 calls recorded — no
assertion was evaluated" is not that claim. Pinned by a test that the summary
never says "passed" here.

## 4. Shape

```
src/ctxlineage/_pytest_plugin.py   # the whole plugin; a thin integration layer
tests/test_pytest_plugin.py        # via pytester, sub-suites in subprocesses
```

`pyproject.toml` gains exactly one line — the `pytest11` entry point. (Session E
is editing dev-deps in the same file; keeping the edit to one line keeps the
rebase trivial.)

`normalize.load_events` grows a text-parsing sibling so the plugin can parse a
byte slice without duplicating the JSONL parse policy.

Nothing in `_contract/` changes. No new rules: `window_budget` and `grounded`
already prove the integration (issue: "does not need new rules").

## 5. Tests (TDD; each confirmed to fail against a broken implementation)

Via `pytester` with `runpytest_subprocess` — the plugin calls `init()`, which
patches SDKs process-globally, so in-process runs would pollute the parent.
Sub-suites use the real openai SDK against `respx`, as the rest of the suite
does: real capture, real rules, real gate.

1. a breaching call **fails its own test**, and the failure names the test
2. a passing suite stays green, and the plugin adds no failure
3. **attribution**: with two tests, only the breaching one fails
4. a test's own failure is not masked by the plugin
5. **skip stays a skip**: an unmeasurable call does not fail the test *and* the
   summary never says "passed"
6. a skip does not suppress a real FAIL elsewhere
7. warnings alone never fail the run (tier rule)
8. not passing `--ctxlineage` is completely inert (no capture, no findings)
9. events from a session fixture are swept as `<unattributed>` and can gate
10. zero recorded calls never reads as "passed"
11. a missing/invalid `ctxlineage.toml` is a clean usage error

## 6. Non-Goals (PLAN.md §5 + this slice)

Unchanged: no evals, no LLM-judge, no proxying, no DB.

**Live-run statistical gating is #74 and stays deferred** — this plugin gates
single recorded runs only, exactly like the CLI. Nothing here runs a test N
times or computes a pass rate.

Not in this slice: new rules, xdist-shared-dir attribution, a strict mode that
turns skips into failures (plausible later; scope creep now).
</content>
