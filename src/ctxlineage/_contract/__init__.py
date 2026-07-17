"""Context contract testing: deterministic assertions over the recorded artifact.

The layer behind `ctxlineage test` (#14, docs/vision/context-contract-testing.md).
Rules are pure readers of `build_report_data` output — the capture and report
pipelines are never re-run or re-implemented here.

Two things keep this honest and are not incidental:

- **The tier rule (§6).** A rule may only hard-gate where its evidence is exact.
  `window_budget` is deterministic from capture alone, so it gates untagged.
  `grounded` gates only where a `tag()` made the lineage exact, and degrades to
  advisory otherwise — gating on inferred lineage is a flaky gate.
- **Never silently green.** Anything that could not be evaluated is reported as
  a skip or a warning, never as a pass.
"""

from ctxlineage._contract.config import ConfigError, load
from ctxlineage._contract.runner import Finding, has_failures, run

__all__ = ["ConfigError", "Finding", "has_failures", "load", "run"]
