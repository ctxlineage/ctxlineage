<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img src="assets/logo.svg" alt="ctxlineage logo" width="96">
  </picture>
</p>

<h1 align="center">ctxlineage</h1>

<p align="center">
  See exactly what context each LLM call consumed — and how it flowed into the next one.
</p>

---

**ctxlineage** is a local-first Python library that records every LLM call in your app (OpenAI / Anthropic SDKs, auto-instrumented) and renders the result as a **single static HTML report** — no server, no database, no account.

Think `dbt docs generate`, but for your LLM context windows:

- **Call Anatomy** — a stacked view of each context window: system prompt, history, RAG chunks, tool results — color-coded with token counts and share of the window.
- **Lineage Graph** — how context elements flow between calls: where a chunk came from, how it was transformed, and which downstream calls it influenced.

```python
import ctxlineage
ctxlineage.init()  # auto-instruments openai / anthropic SDKs
```

```bash
ctxlineage report --open   # .ctxlineage/events.jsonl → ctxlineage-report.html
```

## Status

🚧 **Pre-alpha — under active development.** Nothing is published to PyPI yet.

The roadmap lives in [GitHub Issues](https://github.com/ctxlineage/ctxlineage/issues). The full project plan is in [docs/PLAN.md](docs/PLAN.md).

## Principles

- **Local-first / zero-server** — output is one self-contained HTML file. Your data never leaves your machine.
- **Non-intrusive by default** — works with zero tagging; explicit `span`/`tag` APIs progressively enhance the visualization.
- **Your data is yours** — capture is an append-only local JSONL file. No telemetry, no external calls.

## License

[Apache-2.0](LICENSE)
