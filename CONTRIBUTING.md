# Contributing

Thanks for your interest! ctxlineage is a deliberately low-maintenance,
single-maintainer project — small, focused contributions land fastest.

## Setup

```bash
git clone https://github.com/ctxlineage/ctxlineage && cd ctxlineage
uv sync                                  # installs everything (Python 3.10+)
uv run pytest                            # tests (hermetic — no network)
uv run ruff check . && uv run ruff format .   # lint / format
```

Useful while hacking on the report UI:

```bash
uv run python examples/generate_demo_events.py /tmp/demo
CTXLINEAGE_DIR=/tmp/demo uv run ctxlineage report --open
```

## Ground rules

- **DCO, not CLA.** Sign off every commit: `git commit -s`
  (adds `Signed-off-by`, certifying [developercertificate.org](https://developercertificate.org/)).
- **Never copy-paste external code** — take it as a dependency instead.
- **TDD**: changes come with tests; SDK patches are tested against mocked HTTP
  (respx). The suite must stay hermetic (no network).
- Everything in the repo is English.
- Pre-1.0, minor versions may contain breaking changes (CLI output, public
  API, event schema constraints). The event schema (`schema/`) is the
  versioned contract — additive changes only within a schema version.

## Scope

The roadmap lives in the milestone issues. Non-Goals
([docs/PLAN.md](docs/PLAN.md) §5) are hard guardrails: no SaaS, no evals, no
prompt management, no persistent DBs, no LLM proxying. Off-roadmap issues and
PRs may be closed without much ceremony — please open an issue before large
changes.
