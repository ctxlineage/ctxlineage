"""The shared assertion runner: findings, severities, and dispatch.

Deliberately thin. §14's premise is that the runner/plumbing is the expensive
part and the rules are cheap because they only read `build_report_data` output;
that only stays true if the runner never grows pipeline logic of its own.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# A hard gate: exits non-zero. Only rules whose evidence is exact may emit it (§6).
FAIL = "fail"
# Advisory: reported, never gates. Where the evidence is inferred, or where a
# rule could not reach the exact tier and was demoted.
WARN = "warn"
# Not evaluated at all. Never collapse this into a pass — an unevaluated
# assertion reported as green is the failure mode this track exists to prevent.
SKIP = "skip"


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    message: str


def has_failures(findings: Iterable[Finding]) -> bool:
    """True when any hard gate failed — the CI exit-code decision."""
    return any(finding.severity == FAIL for finding in findings)


def run(data: dict, rules: Iterable) -> list[Finding]:
    """Evaluate every configured rule against one report-data dict."""
    findings: list[Finding] = []
    for rule in rules:
        findings.extend(rule.check(data))
    return findings
