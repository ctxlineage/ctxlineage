"""Rule behaviour, exercised over real `build_report_data` output.

Fixtures are built from synthetic *events* and pushed through the real pipeline
rather than hand-written report dicts: the rules' whole premise (§14) is that
they only read what the report pipeline already produces, so the tests must
break if that shape drifts.
"""

from __future__ import annotations

import json

from ctxlineage._contract import runner
from ctxlineage._contract.rules import (
    Grounded,
    Metamorphic,
    RequiresSegment,
    SegmentDiff,
    WindowBudget,
)
from ctxlineage._report import normalize

CHUNK = "ALPHA-CHUNK-CONTENT-ONE: ctxlineage records every call locally."
LONG_ANSWER = "This answer is long enough to count as lineage evidence."


def _llm_call(
    call_id,
    *,
    session="s1",
    span=None,
    model="gpt-4o-mini",
    messages=None,
    usage=None,
    answer=None,
    ts="2026-07-17T00:00:00+00:00",
):
    payload = {
        "provider": "openai",
        "api": "chat.completions",
        "request": {"model": model, "messages": messages or []},
        "stream": False,
    }
    if usage is not None:
        payload["usage"] = usage
    if answer is not None:
        payload["response"] = {
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
        }
    return {
        "schema_version": 1,
        "event_type": "llm_call",
        "session_id": session,
        "span_id": span,
        "call_id": call_id,
        "timestamp": ts,
        "payload": payload,
    }


def _imported_call(call_id, *, session="s1", messages=None, usage=None, not_preserved=None):
    """A call reconstructed from an agent transcript: the segments are only the
    part that could be rebuilt, and the producer says so."""
    event = _llm_call(call_id, session=session, messages=messages, usage=usage)
    event["payload"]["import"] = {
        "source": "claude-code",
        "usage": "reconstructed",
        "segment_tokens": "estimated",
        "not_preserved": list(
            not_preserved
            if not_preserved is not None
            else ("system_prompt", "tool_definitions", "reasoning_text")
        ),
    }
    return event


def _span_start(span, name, *, session="s1"):
    return {
        "schema_version": 1,
        "event_type": "span_start",
        "session_id": session,
        "span_id": span,
        "call_id": None,
        "timestamp": "2026-07-16T23:59:00+00:00",
        "payload": {"name": name},
    }


def _tag(span, name, content, *, session="s1", source=None):
    payload = {"name": name, "content": content}
    if source:
        payload["source"] = source
    return {
        "schema_version": 1,
        "event_type": "tag",
        "session_id": session,
        "span_id": span,
        "call_id": None,
        "timestamp": "2026-07-16T23:59:01+00:00",
        "payload": payload,
    }


def _data(*events):
    return normalize.build_report_data(list(events))


def _sev(findings, severity):
    return [f for f in findings if f.severity == severity]


def _messages(findings):
    return " | ".join(f.message for f in findings)


# --------------------------------------------------------------------------
# window_budget — deterministic from capture alone, hard-gates without tags
# --------------------------------------------------------------------------


def test_window_budget_fails_when_over_threshold():
    data = _data(_llm_call("c1", usage={"prompt_tokens": 120_000, "completion_tokens": 10}))
    findings = runner.run(data, [WindowBudget(max_pct=80)])
    assert len(_sev(findings, "fail")) == 1
    assert "c1" in _messages(findings)


def test_window_budget_passes_when_under_threshold():
    data = _data(_llm_call("c1", usage={"prompt_tokens": 1_000, "completion_tokens": 10}))
    assert runner.run(data, [WindowBudget(max_pct=80)]) == []


def test_window_budget_gates_untagged_calls():
    """The tagless on-ramp (§6/§14): no span, no tag, still a hard gate."""
    data = _data(_llm_call("c1", span=None, usage={"prompt_tokens": 120_000}))
    findings = runner.run(data, [WindowBudget(max_pct=80)])
    assert _sev(findings, "fail")


def test_window_budget_prefers_real_usage_over_the_estimate():
    # Huge prompt text (est. ~125k tokens => over budget) but the provider
    # reported a small real usage: the real number must win, so this passes.
    messages = [{"role": "user", "content": "word " * 100_000}]
    data = _data(_llm_call("c1", messages=messages, usage={"prompt_tokens": 1_000}))
    assert runner.run(data, [WindowBudget(max_pct=80)]) == []


def test_window_budget_falls_back_to_the_estimate_without_usage():
    messages = [{"role": "user", "content": "word " * 100_000}]
    data = _data(_llm_call("c1", messages=messages))
    findings = runner.run(data, [WindowBudget(max_pct=80)])
    assert _sev(findings, "fail")
    assert "est." in _messages(findings)


# --- #63: segments that do not cover the prompt must not be scored ----------


def test_segment_budget_skips_when_segments_do_not_cover_the_prompt():
    """The #63 regression: an imported call's segments are a sliver of the real
    prompt, so scoring them as a share of the window passes for the wrong
    reason. It must skip, not pass."""
    data = _data(
        _imported_call(
            "c1",
            messages=[{"role": "user", "content": "tiny"}],
            usage={"prompt_tokens": 33_631},
        )
    )
    findings = runner.run(data, [WindowBudget(segment="user", max_pct=10)])
    assert _sev(findings, "skip"), "an unevaluated assertion must never report as a pass"
    assert not _sev(findings, "fail")
    assert "do not cover the whole prompt" in _messages(findings)
    assert "system_prompt" in _messages(findings)  # says *what* is missing
    # #83: the skip names the remedy, not just the problem - native capture.
    assert "native ctxlineage.init() capture" in _messages(findings)


def test_whole_prompt_budget_still_gates_imported_calls():
    """Only the segment form is unsafe: the whole-prompt form reads the real
    reported usage, so it must keep gating imports exactly as before."""
    data = _data(_imported_call("c1", usage={"prompt_tokens": 33_631}))
    findings = runner.run(data, [WindowBudget(max_pct=10)])
    assert _sev(findings, "fail")
    assert "33,631 reported tokens" in _messages(findings)


def test_whole_prompt_budget_skips_an_import_that_reported_no_usage():
    """The gap #67 left: the whole-prompt form is exact *because* it reads the
    provider's usage — so when an import carries none, it silently falls back to
    an estimate over the same partial segments and passes for the wrong reason.
    The importer allows this (import.usage == "unavailable")."""
    data = _data(_imported_call("c1", messages=[{"role": "user", "content": "tiny"}], usage=None))
    findings = runner.run(data, [WindowBudget(max_pct=10)])
    assert _sev(findings, "skip"), "a sliver of a prompt is not measurable as the prompt"
    assert not _sev(findings, "fail")
    assert "no usage was reported" in _messages(findings)


def test_whole_prompt_budget_evaluates_live_capture_without_usage():
    """No regression: live capture without usage is complete, just estimated —
    the estimate covers the whole prompt, so it must still gate."""
    messages = [{"role": "user", "content": "word " * 100_000}]
    data = _data(_llm_call("c1", messages=messages))  # no usage
    findings = runner.run(data, [WindowBudget(max_pct=80)])
    assert _sev(findings, "fail")
    assert not _sev(findings, "skip")


def test_segment_budget_still_evaluates_live_capture():
    """Live capture is complete (estimated, but nothing absent) — the guard must
    not turn the normal path into a skip."""
    messages = [{"role": "user", "content": "word " * 100_000}]
    data = _data(_llm_call("c1", messages=messages))
    findings = runner.run(data, [WindowBudget(segment="user", max_pct=10)])
    assert _sev(findings, "fail")
    assert not _sev(findings, "skip")


def test_segment_budget_evaluates_imports_that_preserved_the_prompt():
    """The guard keys on the declaration, not on 'is it an import': a transcript
    missing only non-prompt metadata still supports a segment budget."""
    messages = [{"role": "user", "content": "word " * 100_000}]
    data = _data(
        _imported_call("c1", messages=messages, not_preserved=("duration_ms", "stream_flag"))
    )
    findings = runner.run(data, [WindowBudget(segment="user", max_pct=10)])
    assert _sev(findings, "fail")
    assert not _sev(findings, "skip")


def test_unknown_window_does_not_claim_the_segment_is_missing():
    """When every call skipped, the segment's absence was never established —
    telling the user to check the name sends them after a typo that isn't there."""
    data = _data(
        _llm_call(
            "c1", model="some-unknown-model-v9", messages=[{"role": "system", "content": "x"}]
        )
    )
    findings = runner.run(data, [WindowBudget(segment="system", max_pct=40)])
    assert _sev(findings, "skip")
    assert "never appeared" not in _messages(findings)


def test_window_budget_segment_scope_sums_only_that_kind():
    messages = [
        {"role": "system", "content": "short system prompt"},
        {"role": "assistant", "content": "word " * 20_000},  # ~25k tokens ≈ 19.5%
    ]
    data = _data(_llm_call("c1", messages=messages, usage={"prompt_tokens": 120_000}))

    over = runner.run(data, [WindowBudget(max_pct=10, segment="assistant")])
    assert _sev(over, "fail"), "assistant segment is ~19.5% of the window"

    under = runner.run(data, [WindowBudget(max_pct=10, segment="system")])
    assert under == [], "system segment is a handful of tokens"


def test_segment_scope_ignores_real_usage_and_says_so():
    """Real usage is only ever a per-call total, so a segment budget is an
    estimate — the message must not let it read as measured."""
    messages = [{"role": "assistant", "content": "word " * 100_000}]
    data = _data(_llm_call("c1", messages=messages, usage={"prompt_tokens": 10}))
    findings = runner.run(data, [WindowBudget(max_pct=80, segment="assistant")])
    assert _sev(findings, "fail")
    assert "est." in _messages(findings)


def test_unknown_context_window_skips_rather_than_passes():
    """An unknown window cannot produce a percentage; silently passing an
    unevaluated call is the failure mode this track exists to prevent."""
    data = _data(_llm_call("c1", model="mystery-model-9", usage={"prompt_tokens": 999_999}))
    findings = runner.run(data, [WindowBudget(max_pct=80)])
    assert _sev(findings, "fail") == []
    assert len(_sev(findings, "skip")) == 1
    assert "mystery-model-9" in _messages(findings)


def test_segment_selector_matching_nothing_warns_rather_than_passes():
    """§14 sketches `segment = "history"`, which is not a kind the pipeline
    produces — a typo'd selector must not look green forever."""
    data = _data(_llm_call("c1", messages=[{"role": "user", "content": "hi"}]))
    findings = runner.run(data, [WindowBudget(max_pct=40, segment="history")])
    assert _sev(findings, "fail") == []
    assert len(_sev(findings, "warn")) == 1
    assert "history" in _messages(findings)


def test_segment_absent_from_one_call_is_a_real_pass():
    """Per-call absence is a legitimate 0%: only a run-wide miss is suspicious."""
    data = _data(
        _llm_call("c1", messages=[{"role": "user", "content": "hi"}]),
        _llm_call(
            "c2", messages=[{"role": "system", "content": "sys"}], ts="2026-07-17T00:00:01+00:00"
        ),
    )
    assert runner.run(data, [WindowBudget(max_pct=40, segment="system")]) == []


# --------------------------------------------------------------------------
# grounded — presence (hard gate, tag = exact lineage)
# --------------------------------------------------------------------------


def test_grounded_presence_passes_when_the_tag_landed():
    data = _data(
        _span_start("sp1", "answer"),
        _tag("sp1", "rag_chunks", CHUNK, source="qdrant:docs"),
        _llm_call("c1", span="sp1", messages=[{"role": "user", "content": f"Context:\n{CHUNK}"}]),
    )
    assert runner.run(data, [Grounded(tag="rag_chunks")]) == []


def test_grounded_presence_hard_fails_when_the_tag_never_landed():
    """The tag is a declaration, so 'it never reached the window' is exact."""
    data = _data(
        _span_start("sp1", "answer"),
        _tag("sp1", "memory", "user prefers concise answers"),
        _llm_call("c1", span="sp1", messages=[{"role": "user", "content": "unrelated prompt"}]),
    )
    findings = runner.run(data, [Grounded(tag="memory")])
    assert len(_sev(findings, "fail")) == 1
    assert "memory" in _messages(findings)


# --------------------------------------------------------------------------
# grounded — the tier rule (§6): no tag => inferred lineage => advisory
# --------------------------------------------------------------------------


def test_grounded_demotes_to_a_warning_when_the_tag_is_absent():
    """The load-bearing tier behaviour: with nothing declared there is no exact
    lineage, so grounded must warn instead of gating. Mixing these tiers is
    what makes a gate flaky and the positioning dishonest."""
    data = _data(_llm_call("c1", messages=[{"role": "user", "content": "no tags here"}]))
    findings = runner.run(data, [Grounded(tag="rag_chunks")])
    assert _sev(findings, "fail") == []
    assert len(_sev(findings, "warn")) == 1
    assert "rag_chunks" in _messages(findings)


def test_grounded_demotion_mentions_how_to_earn_the_gate():
    data = _data(_llm_call("c1", messages=[{"role": "user", "content": "no tags here"}]))
    (warning,) = _sev(runner.run(data, [Grounded(tag="rag_chunks")]), "warn")
    assert "tag" in warning.message.lower()


# --------------------------------------------------------------------------
# grounded — dead context (advisory always: edges are inferred)
# --------------------------------------------------------------------------


def _dead_context_events(*, follow_up_content, follow_up_span=None):
    return (
        _span_start("sp1", "answer"),
        _tag("sp1", "rag_chunks", CHUNK, source="qdrant:docs"),
        _llm_call(
            "c1",
            span="sp1",
            messages=[{"role": "user", "content": f"Context:\n{CHUNK}"}],
            answer=LONG_ANSWER,
            ts="2026-07-17T00:00:00+00:00",
        ),
        _llm_call(
            "c2",
            span=follow_up_span,
            messages=[{"role": "user", "content": follow_up_content}],
            answer="Second answer, also long enough.",
            ts="2026-07-17T00:00:01+00:00",
        ),
    )


def test_dead_context_warns_when_no_downstream_call_used_the_output():
    data = _data(*_dead_context_events(follow_up_content="A totally unrelated follow-up."))
    findings = runner.run(data, [Grounded(tag="rag_chunks", warn_dead=True)])
    assert _sev(findings, "fail") == []
    assert len(_sev(findings, "warn")) == 1
    assert "rag_chunks" in _messages(findings)


def test_dead_context_is_quiet_when_the_output_flowed_downstream():
    data = _data(*_dead_context_events(follow_up_content=f"Earlier: {LONG_ANSWER}\nNow what?"))
    assert runner.run(data, [Grounded(tag="rag_chunks", warn_dead=True)]) == []


def test_same_span_adjacency_alone_is_not_evidence_of_influence():
    """A same_span edge means two calls shared a span, not that anything flowed."""
    data = _data(
        *_dead_context_events(follow_up_content="Unrelated follow-up.", follow_up_span="sp1")
    )
    assert len(_sev(runner.run(data, [Grounded(tag="rag_chunks", warn_dead=True)]), "warn")) == 1


def test_terminal_consumer_is_not_dead_context():
    """A single-call RAG app: the chunks fed the answer and the answer went to
    the user. There is no downstream to influence, so 'dead' is vacuous —
    flagging it would be the noise that discredits the signal."""
    data = _data(
        _span_start("sp1", "answer"),
        _tag("sp1", "rag_chunks", CHUNK),
        _llm_call(
            "c1", span="sp1", messages=[{"role": "user", "content": CHUNK}], answer=LONG_ANSWER
        ),
    )
    assert runner.run(data, [Grounded(tag="rag_chunks", warn_dead=True)]) == []


def test_dead_context_is_off_by_default():
    data = _data(*_dead_context_events(follow_up_content="A totally unrelated follow-up."))
    assert runner.run(data, [Grounded(tag="rag_chunks")]) == []


def test_dead_context_never_hard_gates():
    """Edges are inferred (§6), so this can only ever advise."""
    data = _data(*_dead_context_events(follow_up_content="A totally unrelated follow-up."))
    findings = runner.run(data, [Grounded(tag="rag_chunks", warn_dead=True)])
    assert all(f.severity != "fail" for f in findings)


def test_dead_context_declares_itself_unreliable_when_edges_were_truncated():
    data = _data(*_dead_context_events(follow_up_content="A totally unrelated follow-up."))
    data["sessions"][0]["edges_truncated"] = True
    (warning,) = _sev(runner.run(data, [Grounded(tag="rag_chunks", warn_dead=True)]), "warn")
    assert "unreliable" in warning.message


def test_unmatched_tag_reports_presence_only_not_dead_context():
    """One root cause, one finding: content that never landed is a presence
    failure, not additionally 'dead'."""
    data = _data(
        _span_start("sp1", "answer"),
        _tag("sp1", "rag_chunks", CHUNK),
        _llm_call("c1", span="sp1", messages=[{"role": "user", "content": "nothing relevant"}]),
        _llm_call(
            "c2", messages=[{"role": "user", "content": "more"}], ts="2026-07-17T00:00:01+00:00"
        ),
    )
    findings = runner.run(data, [Grounded(tag="rag_chunks", warn_dead=True)])
    assert len(findings) == 1
    assert findings[0].severity == "fail"


# --------------------------------------------------------------------------
# requires_segment — structural presence, deterministic, hard-gates untagged
# --------------------------------------------------------------------------


def test_requires_segment_passes_when_present():
    data = _data(
        _llm_call(
            "c1",
            messages=[
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "hi"},
            ],
            usage={"prompt_tokens": 10},
        )
    )
    assert runner.run(data, [RequiresSegment(kind="system")]) == []


def test_requires_segment_hard_fails_when_absent():
    """Unlike window_budget's typo guard, absence here is never demoted - it
    is the whole point of the rule, so a typo'd kind fails loudly."""
    data = _data(
        _llm_call("c1", messages=[{"role": "user", "content": "hi"}], usage={"prompt_tokens": 10})
    )
    findings = runner.run(data, [RequiresSegment(kind="system")])
    assert len(findings) == 1
    assert findings[0].severity == "fail"
    assert "system" in findings[0].message


def test_requires_segment_skips_incomplete_imports_rather_than_failing():
    """#63's reasoning, reused: an imported call's absent segment is ambiguous
    between 'never sent' and 'not preserved' - failing it would punish the
    transcript's honesty about what it could not recover, not a real gap."""
    data = _data(
        _imported_call(
            "c1", messages=[{"role": "user", "content": "hi"}], usage={"prompt_tokens": 30_000}
        )
    )
    findings = runner.run(data, [RequiresSegment(kind="system")])
    assert len(findings) == 1
    assert findings[0].severity == "skip"
    assert "ambiguous" in findings[0].message


def test_requires_segment_when_model_scopes_to_matching_calls_only():
    data = _data(
        _llm_call(
            "c1", model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}], usage={}
        ),
        _llm_call(
            "c2", model="claude-sonnet-5", messages=[{"role": "user", "content": "hi"}], usage={}
        ),
    )
    findings = runner.run(data, [RequiresSegment(kind="tool_defs", when_model="gpt-*")])
    assert len(findings) == 1
    assert "c1" in findings[0].message
    assert "c2" not in findings[0].message


def test_requires_segment_when_model_no_match_is_silent_not_skipped():
    """A call outside when_model's scope is not a gap - the rule simply does
    not apply to it, so it gets no finding at all (skip is reserved for
    'could not evaluate', not 'was never in scope')."""
    data = _data(
        _llm_call(
            "c1", model="claude-sonnet-5", messages=[{"role": "user", "content": "hi"}], usage={}
        )
    )
    assert runner.run(data, [RequiresSegment(kind="tool_defs", when_model="gpt-*")]) == []


def test_requires_segment_when_model_unknown_model_is_skipped():
    """A call whose model is genuinely unknown cannot be matched against
    when_model at all - that IS a gap, unlike a known model that simply
    doesn't match, so it gets an explicit skip rather than silent exclusion."""
    event = _llm_call("c1", messages=[{"role": "user", "content": "hi"}], usage={})
    event["payload"]["request"]["model"] = None
    data = _data(event)
    findings = runner.run(data, [RequiresSegment(kind="tool_defs", when_model="gpt-*")])
    assert len(findings) == 1
    assert findings[0].severity == "skip"
    assert "model unknown" in findings[0].message


# --------------------------------------------------------------------------
# segment_diff — regression/differential, positional pairing across two runs
# --------------------------------------------------------------------------


def test_segment_diff_passes_when_identical_to_the_baseline():
    baseline = _data(_llm_call("b1", messages=[{"role": "user", "content": "hi there"}]))
    current = _data(_llm_call("c1", messages=[{"role": "user", "content": "hi there"}]))
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=0, segment="user")
    assert runner.run(current, [rule]) == []


def test_segment_diff_fails_when_a_segment_grows_past_the_budget():
    baseline = _data(_llm_call("b1", messages=[{"role": "user", "content": "hi"}]))
    current = _data(
        _llm_call(
            "c1",
            messages=[{"role": "user", "content": "hi " * 200}],
        )
    )
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=5, segment="user")
    findings = runner.run(current, [rule])
    assert len(findings) == 1
    assert findings[0].severity == "fail"
    assert "grew by" in findings[0].message
    assert "c1" in findings[0].message


def test_segment_diff_does_not_fail_on_shrinkage():
    """A segment shrinking vs. the baseline is not this rule's concern - only
    growth past the budget is (content loss is requires_segment's job)."""
    baseline = _data(_llm_call("b1", messages=[{"role": "user", "content": "hi " * 200}]))
    current = _data(_llm_call("c1", messages=[{"role": "user", "content": "hi"}]))
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=0, segment="user")
    assert runner.run(current, [rule]) == []


def test_segment_diff_whole_prompt_when_segment_is_none():
    baseline = _data(
        _llm_call(
            "b1",
            messages=[
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "hi"},
            ],
        )
    )
    current = _data(
        _llm_call(
            "c1",
            messages=[
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "hi " * 200},
            ],
        )
    )
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=5)
    findings = runner.run(current, [rule])
    assert len(findings) == 1
    assert findings[0].severity == "fail"
    assert "the prompt" in findings[0].message


def test_segment_diff_warns_when_a_step_only_exists_in_current():
    baseline = _data(_llm_call("b0", messages=[{"role": "user", "content": "hi"}]))
    current = _data(
        _llm_call(
            "c0", messages=[{"role": "user", "content": "hi"}], ts="2026-07-17T00:00:00+00:00"
        ),
        _span_start("sp1", "new_step"),
        _llm_call(
            "c1",
            span="sp1",
            messages=[{"role": "user", "content": "hi"}],
            ts="2026-07-17T00:01:00+00:00",
        ),
    )
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=1000, segment="user")
    findings = runner.run(current, [rule])
    assert len(findings) == 1
    assert findings[0].severity == "warn"
    assert "pairing gap" in findings[0].message
    assert "c1" in findings[0].message


def test_segment_diff_warns_when_a_step_only_exists_in_baseline():
    baseline = _data(
        _llm_call(
            "b0", messages=[{"role": "user", "content": "hi"}], ts="2026-07-17T00:00:00+00:00"
        ),
        _span_start("sp1", "retired_step"),
        _llm_call(
            "b1",
            span="sp1",
            messages=[{"role": "user", "content": "hi"}],
            ts="2026-07-17T00:01:00+00:00",
        ),
    )
    current = _data(_llm_call("c0", messages=[{"role": "user", "content": "hi"}]))
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=1000, segment="user")
    findings = runner.run(current, [rule])
    assert len(findings) == 1
    assert findings[0].severity == "warn"
    assert "pairing gap" in findings[0].message
    assert "b1" in findings[0].message


def test_segment_diff_baseline_orphan_names_the_baseline_session_not_current():
    """Regression: the orphan-in-baseline-only warning must cite the
    baseline call's own session, not the current session it's positionally
    paired against - the two runs' session ids are independent per-run
    UUIDs in practice, so naming the wrong one points at a call that
    provably does not exist under that session in this run."""
    baseline = _data(
        _llm_call(
            "b0",
            session="baseline-session",
            messages=[{"role": "user", "content": "hi"}],
            ts="2026-07-17T00:00:00+00:00",
        ),
        _span_start("sp1", "retired_step", session="baseline-session"),
        _llm_call(
            "b1",
            session="baseline-session",
            span="sp1",
            messages=[{"role": "user", "content": "hi"}],
            ts="2026-07-17T00:01:00+00:00",
        ),
    )
    current = _data(
        _llm_call("c0", session="current-session", messages=[{"role": "user", "content": "hi"}])
    )
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=1000, segment="user")
    findings = runner.run(current, [rule])
    assert len(findings) == 1
    assert "baseline-session" in findings[0].message
    assert "current-session" not in findings[0].message


def test_segment_diff_warns_when_the_segment_never_appears_on_either_side():
    """Mirrors WindowBudget's typo guard: without this, a misspelled segment
    kind sums to 0 on both sides for every pair forever, and the rule passes
    silently - indistinguishable, by the math alone, from 'genuinely never
    grew'."""
    baseline = _data(_llm_call("b1", messages=[{"role": "user", "content": "hi"}]))
    current = _data(_llm_call("c1", messages=[{"role": "user", "content": "hi"}]))
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=0, segment="toll_defs")
    findings = runner.run(current, [rule])
    assert len(findings) == 1
    assert findings[0].severity == "warn"
    assert "toll_defs" in findings[0].message
    assert "never appeared" in findings[0].message


def test_segment_diff_does_not_claim_the_segment_is_missing_when_nothing_was_evaluated():
    """When every pair was skipped (incomplete on both sides), the segment's
    absence was never established - telling the user to check the name
    sends them after a typo that may not be there."""
    baseline = _data(_imported_call("b1", messages=[{"role": "user", "content": "hi"}]))
    current = _data(_imported_call("c1", messages=[{"role": "user", "content": "hi"}]))
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=0, segment="toll_defs")
    findings = runner.run(current, [rule])
    assert _sev(findings, "skip")
    assert "never appeared" not in _messages(findings)


def test_segment_diff_warns_rather_than_passing_when_nothing_could_be_paired():
    """Same blocker as metamorphic's, found by the same review: an empty or
    unrelated baseline pairs with nothing, and every guard downstream is
    gated on having evaluated something - so the whole assertion silently
    reported green."""
    current = _data(_llm_call("c1", messages=[{"role": "user", "content": "hi"}]))
    rule = SegmentDiff(
        baseline_data=normalize.build_report_data([]), max_token_delta=0, segment="user"
    )
    findings = runner.run(current, [rule])
    assert findings, "an empty baseline must not report as a silent pass"
    assert not _sev(findings, "fail")
    assert "nothing was compared" in _messages(findings)


def test_segment_diff_warns_when_the_two_runs_hold_different_session_counts():
    current = _data(
        _llm_call("c1", session="s1", messages=[{"role": "user", "content": "hi"}]),
        _llm_call("c2", session="s2", messages=[{"role": "user", "content": "hi"}]),
    )
    baseline = _data(_llm_call("b1", session="s1", messages=[{"role": "user", "content": "hi"}]))
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=1000, segment="user")
    findings = runner.run(current, [rule])
    assert not _sev(findings, "fail")
    assert "were not compared at all" in _messages(findings)


def test_segment_diff_pairs_repeated_steps_by_occurrence_order():
    """An agent loop's calls all share one span - pairing must match the Kth
    occurrence to the Kth occurrence, not collapse them into one comparison."""
    baseline = _data(
        _span_start("sp1", "loop"),
        _llm_call("b1", span="sp1", messages=[{"role": "user", "content": "step one"}]),
        _llm_call("b2", span="sp1", messages=[{"role": "user", "content": "step two " * 50}]),
    )
    current = _data(
        _span_start("sp1", "loop"),
        _llm_call("c1", span="sp1", messages=[{"role": "user", "content": "step one"}]),
        _llm_call("c2", span="sp1", messages=[{"role": "user", "content": "step two " * 50}]),
    )
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=0, segment="user")
    assert runner.run(current, [rule]) == []


def test_segment_diff_skips_when_the_current_call_is_incomplete():
    baseline = _data(_llm_call("b1", messages=[{"role": "user", "content": "hi"}]))
    current = _data(_imported_call("c1", messages=[{"role": "user", "content": "hi"}]))
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=0, segment="user")
    findings = runner.run(current, [rule])
    assert len(findings) == 1
    assert findings[0].severity == "skip"
    assert "this run's call" in findings[0].message


def test_segment_diff_skips_when_the_baseline_call_is_incomplete():
    baseline = _data(_imported_call("b1", messages=[{"role": "user", "content": "hi"}]))
    current = _data(_llm_call("c1", messages=[{"role": "user", "content": "hi"}]))
    rule = SegmentDiff(baseline_data=baseline, max_token_delta=0, segment="user")
    findings = runner.run(current, [rule])
    assert len(findings) == 1
    assert findings[0].severity == "skip"
    assert "baseline call" in findings[0].message


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


def test_runner_reports_findings_from_every_rule():
    data = _data(
        _span_start("sp1", "answer"),
        _tag("sp1", "memory", "never injected"),
        _llm_call(
            "c1",
            span="sp1",
            messages=[{"role": "user", "content": "hi"}],
            usage={"prompt_tokens": 120_000},
        ),
    )
    findings = runner.run(data, [WindowBudget(max_pct=80), Grounded(tag="memory")])
    assert {f.rule for f in findings} == {"window_budget", "grounded"}
    assert len(_sev(findings, "fail")) == 2


# --------------------------------------------------------------------------
# metamorphic - INV/DIR over the assembled context, tag-required
# --------------------------------------------------------------------------

RAG = [
    "[c1] alpha chunk about widgets and their care",
    "[c2] beta chunk about gadgets and their care",
    "[c3] gamma chunk about gizmos and their care",
]


def _rag_run(chunks, *, tagged=True, call_id="c1", session="s1"):
    """A recorded run whose retrieved chunks are (optionally) tagged.

    Built from real span_start/tag events through the real pipeline, because
    the whole premise of this rule is that `apply_tags` splits a joined
    message per chunk - a hand-written report dict would assume away the
    exact thing under test.
    """
    events = [_span_start("sp1", "answer", session=session)]
    if tagged:
        events.append(_tag("sp1", "rag_chunks", json.dumps(chunks), session=session))
    events.append(
        _llm_call(
            call_id,
            session=session,
            span="sp1",
            messages=[{"role": "user", "content": "Context:\n" + "\n".join(chunks) + "\n\nQ?"}],
        )
    )
    return _data(*events)


def test_metamorphic_invariant_holds_when_only_the_order_changed():
    """The INV case the vision doc names: perturbing retrieval ORDER must not
    change what the context actually contains."""
    current = _rag_run(RAG)
    variant = _rag_run(list(reversed(RAG)))
    rule = Metamorphic(variant_data=variant, relation="invariant", segment="rag_chunks")
    assert runner.run(current, [rule]) == []


def test_metamorphic_invariant_fails_when_a_chunk_changed():
    current = _rag_run(RAG)
    variant = _rag_run([RAG[0], RAG[1], "[c9] an entirely different chunk about sprockets"])
    rule = Metamorphic(variant_data=variant, relation="invariant", segment="rag_chunks")
    findings = runner.run(current, [rule])
    assert len(findings) == 1
    assert findings[0].severity == "fail"
    assert "not invariant under the perturbation" in findings[0].message


def test_metamorphic_invariant_fails_when_the_reorder_silently_dropped_a_chunk():
    """The bug this rule exists to catch: a shuffle that also loses content,
    which an order-sensitive dedup would produce and nothing else would see."""
    current = _rag_run(RAG)
    variant = _rag_run([RAG[2], RAG[1]])  # reordered AND one short
    rule = Metamorphic(variant_data=variant, relation="invariant", segment="rag_chunks")
    findings = runner.run(current, [rule])
    assert len(findings) == 1
    assert findings[0].severity == "fail"
    assert "1 part(s) only in this run" in findings[0].message


def test_metamorphic_changed_holds_when_a_chunk_was_dropped():
    """The DIR case: dropping a chunk MUST reach the assembled context."""
    current = _rag_run(RAG)
    variant = _rag_run(RAG[:2])
    rule = Metamorphic(variant_data=variant, relation="changed", segment="rag_chunks")
    assert runner.run(current, [rule]) == []


def test_metamorphic_changed_fails_when_the_perturbation_had_no_effect():
    """A `k` that is ignored, a filter that never fires: the caller thinks
    they perturbed the input, but the context came out identical."""
    current = _rag_run(RAG)
    variant = _rag_run(RAG)
    rule = Metamorphic(variant_data=variant, relation="changed", segment="rag_chunks")
    findings = runner.run(current, [rule])
    assert len(findings) == 1
    assert findings[0].severity == "fail"
    assert "unchanged under the perturbation" in findings[0].message


def test_metamorphic_warns_rather_than_gating_an_untagged_run():
    """Untagged, the chunks are one joined string: a reorder is
    indistinguishable from a rewrite, so the relation cannot be expressed at
    all. That must warn - never fail, and never silently pass (§6)."""
    current = _rag_run(RAG, tagged=False)
    variant = _rag_run(list(reversed(RAG)), tagged=False)
    rule = Metamorphic(variant_data=variant, relation="invariant", segment="rag_chunks")
    findings = runner.run(current, [rule])
    assert not _sev(findings, "fail")
    assert _sev(findings, "warn")
    assert "never appeared" in _messages(findings)
    assert "span.tag('rag_chunks', ...)" in _messages(findings)


def test_metamorphic_does_not_gate_an_absence_in_a_run_that_never_declared_the_tag():
    """A run with no `tag()` gives no visibility, so its "absence" proves
    nothing - that must warn, not fail."""
    current = _rag_run(RAG)
    variant = _rag_run(RAG, tagged=False)
    rule = Metamorphic(variant_data=variant, relation="invariant", segment="rag_chunks")
    findings = runner.run(current, [rule])
    assert not _sev(findings, "fail")
    assert _sev(findings, "warn")
    assert "never declared it as a tag" in _messages(findings)


def test_metamorphic_gates_a_total_drop_when_the_variant_did_declare_the_tag():
    """The other half of the same branch, and the one that matters: if the
    variant DID declare the tag and still carries none of it, the content
    really is gone - exact evidence, so dropping everything must gate at
    least as hard as dropping one (found by adversarial review: it used to
    be the reverse, drop-one FAILed while drop-all only warned)."""
    current = _rag_run(RAG)
    variant = _data(
        _span_start("sp1", "answer"),
        _tag("sp1", "rag_chunks", json.dumps(RAG)),
        _llm_call(
            "v1", span="sp1", messages=[{"role": "user", "content": "Context:\n(none)\n\nQ?"}]
        ),
    )
    rule = Metamorphic(variant_data=variant, relation="invariant", segment="rag_chunks")
    findings = runner.run(current, [rule])
    assert _sev(findings, "fail"), "a declared-but-entirely-absent tag is a real INV violation"
    assert "not invariant under the perturbation" in _messages(findings)


def test_metamorphic_warns_rather_than_passing_when_nothing_could_be_paired():
    """An empty or unrelated variant pairs with nothing, so the relation is
    never checked - reporting that as a green pass is the exact 'unevaluated
    reads as a pass' failure the tier rule exists to prevent."""
    current = _rag_run(RAG)
    rule = Metamorphic(
        variant_data=normalize.build_report_data([]), relation="invariant", segment="rag_chunks"
    )
    findings = runner.run(current, [rule])
    assert findings, "an empty variant must not report as a silent pass"
    assert not _sev(findings, "fail")
    assert "never checked" in _messages(findings)


def test_metamorphic_warns_when_the_two_runs_hold_different_session_counts():
    """zip() drops the extras, so the unpaired sessions are never compared -
    that has to surface rather than being passed over."""
    current = _data(
        _llm_call("c1", session="s1", messages=[{"role": "user", "content": "hi"}]),
        _llm_call("c2", session="s2", messages=[{"role": "user", "content": "hi"}]),
    )
    variant = _data(_llm_call("v1", session="s1", messages=[{"role": "user", "content": "hi"}]))
    rule = Metamorphic(variant_data=variant, relation="invariant", segment="rag_chunks")
    findings = runner.run(current, [rule])
    assert not _sev(findings, "fail")
    assert "were not compared at all" in _messages(findings)


def test_metamorphic_skips_an_incomplete_import():
    """An import cannot reconstruct exact segments, so the multiset it would
    compare is a fraction of the real one - the same guard every other
    segment-reading rule applies."""
    current = _data(_imported_call("c1", messages=[{"role": "user", "content": "hi"}]))
    variant = _data(_imported_call("v1", messages=[{"role": "user", "content": "hi"}]))
    rule = Metamorphic(variant_data=variant, relation="invariant", segment="rag_chunks")
    findings = runner.run(current, [rule])
    assert _sev(findings, "skip")
    assert not _sev(findings, "fail")


def test_metamorphic_warns_on_a_pairing_gap():
    current = _data(
        _llm_call("c1", messages=[{"role": "user", "content": "hi"}]),
        _span_start("sp9", "extra_step"),
        _llm_call(
            "c2",
            span="sp9",
            messages=[{"role": "user", "content": "hi"}],
            ts="2026-07-17T00:01:00+00:00",
        ),
    )
    variant = _data(_llm_call("v1", messages=[{"role": "user", "content": "hi"}]))
    rule = Metamorphic(variant_data=variant, relation="invariant", segment="rag_chunks")
    findings = runner.run(current, [rule])
    assert not _sev(findings, "fail")
    assert "pairing gap, not a relation violation" in _messages(findings)


def test_has_failures_only_counts_hard_gates():
    warn = runner.Finding(rule="grounded", severity="warn", message="advisory")
    skip = runner.Finding(rule="window_budget", severity="skip", message="unknown window")
    assert runner.has_failures([warn, skip]) is False
    assert runner.has_failures([warn, runner.Finding("grounded", "fail", "boom")]) is True
