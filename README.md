<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
  <img src="assets/logo.svg" alt="ctxlineage logo" width="120">
</picture>

# ctxlineage

### See exactly what context each LLM call consumed — and where it flowed next.

Two lines of code turn your app's LLM calls into a single static HTML report:
the anatomy of every context window, and the lineage of every context element.
**No server, no database, no account. Your prompts never leave your machine.**

[![CI](https://img.shields.io/github/actions/workflow/status/ctxlineage/ctxlineage/test.yml?branch=main&style=flat-square&logo=githubactions&logoColor=white&label=CI)](https://github.com/ctxlineage/ctxlineage/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/ctxlineage?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/ctxlineage/)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square)](LICENSE)

</div>

<p align="center">
  <img src="docs/assets/report-calls.png" alt="Call anatomy view — what filled the context window, segment by segment" width="800">
</p>

## Quickstart

```bash
pip install ctxlineage
```

```python
import ctxlineage
ctxlineage.init()  # auto-instruments the openai and anthropic SDKs
# ... run your app: every LLM call is recorded to .ctxlineage/events.jsonl
```

```bash
ctxlineage report --open   # one self-contained HTML file
```

That's the whole integration. Streaming, async, tool calls — captured.
Add `.ctxlineage/` to your `.gitignore`.

## What you get

| View | Question it answers |
|---|---|
| **Overview** | Which calls are heaviest? How close to the window limit? Did my tags match? |
| **Calls** | What *actually* filled this call's context window — and at what token cost? |
| **Chain** | How did each output become the next call's input? Where do agent loops accumulate context? |
| **Graph** | Where did each context element *come from* (vector DB, prompt file, memory) and which downstream calls did it influence? |

Think **`dbt docs generate`, for your context windows**: calls are functions,
context elements are typed inputs with provenance, outputs flow into
downstream inputs.

<p align="center">
  <img src="docs/assets/report-tour.gif" alt="Ten-second tour: Overview, call anatomy, session chain, lineage graph with click-to-trace, dark mode" width="800">
</p>

## Tagging (optional, unlocks lineage)

Everything above works with zero tagging. Label your context assembly and the
report upgrades from role-based heuristics to real, provenance-carrying
segments:

```python
with ctxlineage.span("answer_user_query") as span:
    span.tag("rag_chunks", docs, source="qdrant:products_v2", transform="top_k(8)")
    span.tag("memory", user_profile, source="memory:user_prefs")
    resp = client.chat.completions.create(...)
```

Tagged content is matched back into the actual prompts (exact → partial →
honestly-untagged, with the match rate displayed — never fabricated).

## Principles

- **Local-first, zero-server.** The artifact is one HTML file that opens
  offline. Capture is an append-only local JSONL. Nothing is ever transmitted.
- **Non-intrusive by default.** `init()` and you're done; tags are progressive
  enhancement. The capture layer never breaks your app — failures degrade to
  warnings, not exceptions.
- **Honest data.** Real `usage` counts preferred over estimates, estimates
  labeled, unmatched tags shown as unmatched, inference caps disclosed.

## Scope & limits

Designed as a **per-run, dev-time artifact**. Comfortable up to ~5,000 calls
(~15 MB report); usable to ~20,000 calls (~65 MB). Reports contain full prompt
text — treat them like logs with sensitive data (see [SECURITY.md](SECURITY.md)).
Before sharing, mask secrets with `ctxlineage report --redact "sk-[A-Za-z0-9]+"`
(repeatable regex; the report discloses what was redacted), or keep them out of
the log entirely with `ctxlineage.init(redact_fields=["request.messages.content"])`.

## Status

🚧 **Pre-release.** v0.1.0 (PyPI) is around the corner — the capture layer
(openai + anthropic), the four-view report, and the span/tag lineage pipeline
are done; MCP server and examples are landing now. Roadmap: the
[milestone issues](https://github.com/ctxlineage/ctxlineage/issues?q=label%3Amilestone).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — DCO sign-off, hermetic tests,
deliberately small maintenance surface ([off-roadmap issues may be closed](docs/PLAN.md)).

## License

[Apache-2.0](LICENSE)
