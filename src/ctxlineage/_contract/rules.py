"""The built-in relations.

A *handful* of built-in rules is the whole scope (§12) — this is not a
framework, and these are deliberately small pure readers of
`build_report_data` output.

Each rule declares the tier it can reach (§6) and the runner never overrides it:

- `window_budget` is deterministic from capture alone → hard-gates untagged.
- `requires_segment` is likewise deterministic from capture alone → hard-gates
  untagged, always (unlike `window_budget`, absence is never demoted to a
  warning: "required" means the absence itself is the failure).
- `grounded` gates only where a `tag()` made the lineage exact, and demotes to
  advisory otherwise.
- `segment_diff` gates a growth threshold between two recorded runs; a pairing
  gap between them (a step present on only one side) has no content to
  compare, so it warns rather than fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch

from ctxlineage._contract.runner import FAIL, SKIP, WARN, Finding
from ctxlineage._report.normalize import _PROMPT_BEARING

# The segment kinds `build_report_data` actually produces, for error messages.
# Tagged parts additionally carry their tag name as `kind`.
KNOWN_SEGMENT_KINDS = ("system", "user", "assistant", "tool", "tool_defs")


def _locate(session: dict, call: dict) -> str:
    return f"session {session['id']}, call {call['id']}"


def _incomplete_reason(call: dict) -> str:
    """Name what the producer said it could not recover, for a skip reason.

    Shared by any rule that reads segments and must not treat
    `segments_complete=False` as measurable: an absence there is ambiguous
    between "never sent" and "not preserved by the transcript", which is
    exactly the distinction a skip (not a pass or a fail) exists to avoid
    collapsing.
    """
    meta = call.get("import") if isinstance(call.get("import"), dict) else {}
    missing = [p for p in (meta.get("not_preserved") or ()) if p in _PROMPT_BEARING]
    source = meta.get("source")
    origin = f"imported from {source}" if source else "reconstructed"
    return f"{origin}; not preserved: {', '.join(missing)}" if missing else origin


@dataclass(frozen=True)
class WindowBudget:
    """Assert a call (or one kind of segment within it) stays under a share of
    the model's context window.

    The wide on-ramp: every call has a window percentage, so this gates without
    any tagging. Catches silent context bloat — agent-loop accumulation, RAG
    top-k creep — like a bundle-size budget.
    """

    max_pct: float
    segment: str | None = None

    NAME = "window_budget"

    def check(self, data: dict) -> list[Finding]:
        findings: list[Finding] = []
        segment_seen = False
        evaluated_any = False
        for session in data["sessions"]:
            for call in session["calls"]:
                window = call.get("context_window")
                if not window:
                    findings.append(
                        Finding(
                            self.NAME,
                            SKIP,
                            f"{_locate(session, call)}: context window unknown for model "
                            f"{call.get('model')!r} - not evaluated",
                        )
                    )
                    continue
                unmeasurable = self._unmeasurable(call)
                if unmeasurable:
                    # The producer declared parts of the real prompt missing, and
                    # what is left cannot stand in for it. Passing here is exactly
                    # the failure SKIP exists to prevent (#63).
                    findings.append(
                        Finding(
                            self.NAME,
                            SKIP,
                            f"{_locate(session, call)}: {self._subject()} not evaluated - "
                            f"{unmeasurable} ({_incomplete_reason(call)})",
                        )
                    )
                    continue
                evaluated_any = True
                used, basis, present = self._used(call)
                segment_seen = segment_seen or present
                pct = used / window * 100
                if pct > self.max_pct:
                    findings.append(
                        Finding(
                            self.NAME,
                            FAIL,
                            f"{_locate(session, call)}: {self._subject()} is {pct:.1f}% of the "
                            f"{window:,}-token window, over the {self.max_pct}% budget "
                            f"({used:,} {basis} tokens)",
                        )
                    )
        # Only meaningful if some call was actually evaluated: when every call
        # skipped, the segment's absence is unknown, not established, and
        # telling the user to check the name sends them after a typo that is
        # not there.
        if self.segment and evaluated_any and not segment_seen:
            findings.append(
                Finding(
                    self.NAME,
                    WARN,
                    f"segment {self.segment!r} never appeared in any recorded call, so nothing "
                    f"was asserted - check the name (kinds are "
                    f"{', '.join(KNOWN_SEGMENT_KINDS)}, or a tag name)",
                )
            )
        return findings

    def _subject(self) -> str:
        return f"segment {self.segment!r}" if self.segment else "the prompt"

    def _unmeasurable(self, call: dict) -> str | None:
        """Why this call cannot be scored, or None when it can.

        Only ever true for a call whose producer declared its segments partial
        (an imported transcript). Two ways that bites:

        - a `segment=` budget reads segments directly, so it measures a fraction
          and would call it the whole;
        - a whole-prompt budget normally reads the provider's own `usage`, which
          stays exact no matter what the segments are missing — but an import
          may carry no usage at all (`import.usage == "unavailable"`), and then
          the only number left is an estimate over those same partial segments.

        The second case is the one that reads as safe and is not: nothing about
        "whole prompt" makes a sliver of it measurable.
        """
        if call.get("segments_complete", True):
            return None
        if self.segment:
            return (
                "this call's segments do not cover the whole prompt, so a segment budget "
                "would measure a fraction and call it the whole; segment budgets need the "
                "exact segments of native ctxlineage.init() capture, which an import cannot "
                "reconstruct"
            )
        if self._reported_prompt(call) is None:
            return (
                "this call's segments do not cover the whole prompt and no usage was "
                "reported, so the only number available is an estimate over a fraction of it"
            )
        return None

    @staticmethod
    def _reported_prompt(call: dict) -> int | None:
        """The provider's own prompt-token count, or None if it did not report one."""
        usage = call.get("usage")
        if isinstance(usage, dict):
            prompt = usage.get("prompt_tokens")
            if isinstance(prompt, (int, float)) and not isinstance(prompt, bool) and prompt > 0:
                return int(prompt)
        return None

    def _used(self, call: dict) -> tuple[int, str, bool]:
        """(tokens, how-we-know, whether the selector matched anything here)."""
        if self.segment:
            # A per-call `usage` total cannot be apportioned across segments, so
            # a segment budget is necessarily an estimate — say so in the basis
            # rather than let it read as measured.
            segments = [s for s in call["segments"] if s.get("kind") == self.segment]
            return sum(s["tokens_est"] for s in segments), "est.", bool(segments)
        reported = self._reported_prompt(call)
        if reported is not None:
            return reported, "reported", True
        return call["input_tokens_est"], "est.", True


@dataclass(frozen=True)
class Grounded:
    """Assert tagged context actually reached the window, and optionally flag
    context nothing downstream consumed.

    The differentiation proof: it needs the tag/lineage substrate, so a
    trace-only or graph-only competitor cannot express it.
    """

    tag: str
    warn_dead: bool = False

    NAME = "grounded"

    def check(self, data: dict) -> list[Finding]:
        findings: list[Finding] = []
        declared = False
        for session in data["sessions"]:
            for element in session["elements"]:
                if element["name"] != self.tag:
                    continue
                declared = True
                if not element["matched"]:
                    findings.append(
                        Finding(
                            self.NAME,
                            FAIL,
                            f"tag {self.tag!r} ({self._origin(session, element)}) never landed in "
                            f"any call's context window",
                        )
                    )
                    continue  # one root cause, one finding: not also 'dead'
                if self.warn_dead:
                    dead = self._dead(session, element)
                    if dead is not None:
                        findings.append(dead)
        if not declared:
            # §6: with no tag there is no exact lineage, so there is nothing to
            # gate on. Demote rather than fail — gating on inferred lineage is
            # exactly what makes a gate flaky.
            findings.append(
                Finding(
                    self.NAME,
                    WARN,
                    f"no {self.tag!r} tag recorded in this run - cannot gate, because without a "
                    f"tag the lineage is inferred rather than exact. Tag the content "
                    f"(span.tag({self.tag!r}, ...)) to turn this into a hard gate.",
                )
            )
        return findings

    def _origin(self, session: dict, element: dict) -> str:
        span = element.get("span_name") or element.get("span_id")
        return f"span {span!r}, session {session['id']}"

    def _dead(self, session: dict, element: dict) -> Finding | None:
        """Advisory only: 'influence' is read off inferred lineage edges (§6)."""
        calls = session["calls"]
        position = {call["id"]: i for i, call in enumerate(calls) if call.get("id")}
        # elements carry consumers across the whole run; only this session's
        # calls can be positioned against this session's edges
        consumers = [call_id for call_id in element["calls"] if call_id in position]
        if not consumers:
            return None
        last = len(calls) - 1
        if all(position[call_id] == last for call_id in consumers):
            # Nothing downstream exists to influence, so "dead" is vacuous: the
            # single-call RAG shape, where the chunks fed the answer and the
            # answer went to the user.
            return None
        # A same_span edge is structural adjacency, not evidence that anything
        # flowed — only a text match downstream is.
        influencing = {e["from"] for e in session["edges"] if e.get("kind") == "output_text"}
        if any(call_id in influencing for call_id in consumers):
            return None
        if session.get("edges_truncated"):
            return Finding(
                self.NAME,
                WARN,
                f"tag {self.tag!r} ({self._origin(session, element)}): dead-context detection is "
                f"unreliable here - edge inference hit its fan-out cap for this session",
            )
        return Finding(
            self.NAME,
            WARN,
            f"tag {self.tag!r} ({self._origin(session, element)}) occupied "
            f"~{element['tokens_est']:,} est. tokens across {len(consumers)} call(s), but no "
            f"downstream call used their output - possible dead context "
            f"[advisory: lineage edges are inferred]",
        )


@dataclass(frozen=True)
class RequiresSegment:
    """Assert every call carries a segment of a given kind - the structural
    counterpart to `window_budget`'s cap: not *how much* is in the window,
    but whether the right thing is there at all (§8's "required-segment
    presence"). Optionally scoped to models matching `when_model` (a glob).

    Deterministic from capture alone, so it hard-gates untagged like
    `window_budget` - but unlike it, absence is never demoted to a warning.
    `window_budget`'s warn-on-absence guards against a typo'd segment name
    reading as a false failure; here the rule's whole point is that absence
    *is* the failure, so a typo surfaces loudly (the correct signal) rather
    than being softened.
    """

    kind: str
    when_model: str | None = None

    NAME = "requires_segment"

    def check(self, data: dict) -> list[Finding]:
        findings: list[Finding] = []
        for session in data["sessions"]:
            for call in session["calls"]:
                if self.when_model:
                    model = call.get("model")
                    if model is None:
                        findings.append(
                            Finding(
                                self.NAME,
                                SKIP,
                                f"{_locate(session, call)}: model unknown, cannot match against "
                                f"when_model {self.when_model!r} - not evaluated",
                            )
                        )
                        continue
                    if not fnmatch(model, self.when_model):
                        continue  # out of scope for this rule, not a gap - no finding
                if not call.get("segments_complete", True):
                    # Absent here is ambiguous between "never sent" and "not
                    # preserved by the transcript" - the same reasoning
                    # WindowBudget's segment form skips on (#63).
                    findings.append(
                        Finding(
                            self.NAME,
                            SKIP,
                            f"{_locate(session, call)}: segment {self.kind!r} presence not "
                            f"evaluated - absence would be ambiguous ({_incomplete_reason(call)})",
                        )
                    )
                    continue
                if not any(s.get("kind") == self.kind for s in call["segments"]):
                    findings.append(
                        Finding(
                            self.NAME,
                            FAIL,
                            f"{_locate(session, call)}: required segment {self.kind!r} is absent",
                        )
                    )
        return findings


def _group_by_step(calls: list[dict]) -> dict:
    """Group calls by their span name, preserving order within each group.

    A call with no span (`step` is None) groups under the key `None` -
    pairing still works because it is occurrence-order within a group, which
    for an unnamed call is exactly ordinal position among the other unnamed
    calls, with no special case needed.
    """
    groups: dict = {}
    for call in calls:
        groups.setdefault(call.get("step"), []).append(call)
    return groups


@dataclass(frozen=True)
class SegmentDiff:
    """Regression/differential testing (vision doc §8's "natural first
    deliverable"): compare this run's segment token counts against a
    recorded golden run, call for call, and fail when one grew past a
    tolerance.

    There is no cross-run call identity in this codebase, so pairing is
    positional: sessions pair by position (Nth vs Nth, both already sorted by
    start time); calls within a paired session pair by `step` (the span
    name), matching the Kth occurrence of a step in this run to the Kth
    occurrence of the same step in the baseline (`_group_by_step`). A step
    present on only one side is a pairing gap, not a content regression, so
    it warns rather than fails - the same posture `Grounded` takes on an
    unmatched tag.

    Positional session pairing has a silent failure mode worth naming
    explicitly rather than only implying: when session *counts* match but
    *identities* differ (a new session type inserted ahead of an old one,
    same-second sessions reordering under timestamp jitter), this compares
    unrelated sessions with no warning at all - unlike a count mismatch or a
    per-call pairing gap, which do surface. A `ctxlineage.toml` baseline is
    only trustworthy against a pipeline whose session shape hasn't changed
    since it was recorded.
    """

    baseline_data: dict
    max_token_delta: float
    segment: str | None = None

    NAME = "segment_diff"

    def check(self, data: dict) -> list[Finding]:
        findings: list[Finding] = []
        evaluated_any = False
        # strict=False: a session count mismatch has no natural identity to
        # name the extras by - the extra sessions on the longer side are
        # simply not compared, not an error.
        pairs = zip(data["sessions"], self.baseline_data["sessions"], strict=False)
        for session, baseline_session in pairs:
            session_findings, session_evaluated = self._check_session(session, baseline_session)
            findings.extend(session_findings)
            evaluated_any = evaluated_any or session_evaluated
        # Mirrors WindowBudget's typo guard: a segment kind that never
        # appears on either side of the diff is indistinguishable, by the
        # math alone, from "genuinely never grew" - delta is always 0 either
        # way. Warn rather than let a typo read as a permanently-passing gate.
        if self.segment and evaluated_any and not self._segment_ever_present(data):
            findings.append(
                Finding(
                    self.NAME,
                    WARN,
                    f"segment {self.segment!r} never appeared in any call in this run or the "
                    f"baseline, so nothing was compared - check the name (kinds are "
                    f"{', '.join(KNOWN_SEGMENT_KINDS)}, or a tag name)",
                )
            )
        return findings

    def _check_session(self, session: dict, baseline_session: dict) -> tuple[list[Finding], bool]:
        findings: list[Finding] = []
        evaluated = False
        current_groups = _group_by_step(session["calls"])
        baseline_groups = _group_by_step(baseline_session["calls"])
        for step, calls in current_groups.items():
            baseline_calls = baseline_groups.get(step, [])
            for index, call in enumerate(calls):
                if index >= len(baseline_calls):
                    findings.append(
                        Finding(
                            self.NAME,
                            WARN,
                            f"{_locate(session, call)}: no baseline call to compare against "
                            f"(step {step!r}) - pairing gap, not a content regression",
                        )
                    )
                    continue
                finding = self._compare(session, call, baseline_calls[index])
                if finding is None or finding.severity != SKIP:
                    evaluated = True
                if finding is not None:
                    findings.append(finding)
        for step, baseline_calls in baseline_groups.items():
            calls = current_groups.get(step, [])
            for orphan in baseline_calls[len(calls) :]:
                findings.append(
                    Finding(
                        self.NAME,
                        WARN,
                        f"{_locate(baseline_session, orphan)}: baseline call has no "
                        f"counterpart in this run for step {step!r} - pairing gap, not a "
                        f"content regression",
                    )
                )
        return findings, evaluated

    def _segment_ever_present(self, data: dict) -> bool:
        for dataset in (data, self.baseline_data):
            for session in dataset["sessions"]:
                for call in session["calls"]:
                    if any(s.get("kind") == self.segment for s in call["segments"]):
                        return True
        return False

    def _compare(self, session: dict, call: dict, baseline_call: dict) -> Finding | None:
        if not call.get("segments_complete", True):
            return Finding(
                self.NAME,
                SKIP,
                f"{_locate(session, call)}: {self._subject()} diff not evaluated - this run's "
                f"call is {_incomplete_reason(call)}",
            )
        if not baseline_call.get("segments_complete", True):
            return Finding(
                self.NAME,
                SKIP,
                f"{_locate(session, call)}: {self._subject()} diff not evaluated - the "
                f"baseline call is {_incomplete_reason(baseline_call)}",
            )
        current_tokens = self._tokens(call)
        baseline_tokens = self._tokens(baseline_call)
        delta = current_tokens - baseline_tokens
        if delta > self.max_token_delta:
            return Finding(
                self.NAME,
                FAIL,
                f"{_locate(session, call)}: {self._subject()} grew by {delta:,} tokens vs "
                f"baseline ({baseline_tokens:,} -> {current_tokens:,}), over the "
                f"{self.max_token_delta:,} budget",
            )
        return None

    def _subject(self) -> str:
        return f"segment {self.segment!r}" if self.segment else "the prompt"

    def _tokens(self, call: dict) -> int:
        if self.segment:
            return sum(s["tokens_est"] for s in call["segments"] if s.get("kind") == self.segment)
        return call["input_tokens_est"]
