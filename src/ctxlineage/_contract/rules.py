"""The built-in relations.

A *handful* of built-in rules is the whole scope (§12) — this is not a
framework, and the two here are deliberately small pure readers of
`build_report_data` output.

Each rule declares the tier it can reach (§6) and the runner never overrides it:

- `window_budget` is deterministic from capture alone → hard-gates untagged.
- `grounded` gates only where a `tag()` made the lineage exact, and demotes to
  advisory otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

from ctxlineage._contract.runner import FAIL, SKIP, WARN, Finding
from ctxlineage._report.normalize import _PROMPT_BEARING

# The segment kinds `build_report_data` actually produces, for error messages.
# Tagged parts additionally carry their tag name as `kind`.
KNOWN_SEGMENT_KINDS = ("system", "user", "assistant", "tool", "tool_defs")


def _locate(session: dict, call: dict) -> str:
    return f"session {session['id']}, call {call['id']}"


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
                            f"{unmeasurable} ({self._missing(call)})",
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
                "would measure a fraction and call it the whole"
            )
        if self._reported_prompt(call) is None:
            return (
                "this call's segments do not cover the whole prompt and no usage was "
                "reported, so the only number available is an estimate over a fraction of it"
            )
        return None

    def _missing(self, call: dict) -> str:
        """Name what the producer said it could not recover, for the skip reason."""
        meta = call.get("import") if isinstance(call.get("import"), dict) else {}
        missing = [p for p in (meta.get("not_preserved") or ()) if p in _PROMPT_BEARING]
        source = meta.get("source")
        origin = f"imported from {source}" if source else "reconstructed"
        return f"{origin}; not preserved: {', '.join(missing)}" if missing else origin

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
