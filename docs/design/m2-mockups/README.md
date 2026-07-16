# M2 design-session mockups (2026-06-12)

Interactive HTML mockups produced during the M2 design session with the maintainer.
They encode the agreed design direction for the report UI and are the reference
for the production frontend implementation (M2 phase 3).

## Decisions these mockups encode

- **Two views:** Call Anatomy (per-call detail) + Chain (session flow). Hybrid
  layout: sidebar (sessions/calls) + detail pane.
- **input → fn → output** metaphor everywhere; the fn node carries the step name
  (innermost user frame from `call_stack`, later the span name) and the
  instructions (system prompt) as its "function definition".
- **Source-based segment taxonomy:** `app · instructions` / `user input` /
  `llm output (prev)` / `tool / MCP · <name>` / `tool defs` / (M3: structured
  data, memory, …). Colors: teal/blue/violet/amber.
- **Theme:** light + dark both mandatory; default follows `prefers-color-scheme`
  with a manual toggle persisted to localStorage.
- **Chain view:** default shows only adjacent output→input hops; outputs that
  feed further calls get an `↳ n` badge; clicking fans out that output's full
  downstream (left-gutter routing). Loops (consecutive calls of the same step
  whose outputs feed the next input) are wrapped in a dashed `↺ loop ×n` box.

## Files

- `call-anatomy.template.html` — detail view ("mockup B" in the session)
- `chain.template.html` — session chain view ("mockup CHAIN v2/v3")
- `build.py` — injects fresh demo data (`examples/generate_demo_events.py`)
  into the templates and writes viewable HTML files.

```bash
uv run python docs/design/m2-mockups/build.py /tmp/ctxl-design
open /tmp/ctxl-design/chain.html
```

Templates contain a `__DATA__` placeholder that receives the report data JSON
(`ctxlineage report --json` shape, `report_version: 1`).
