# ctxlineage — Project Plan: Visualization & Lineage of LLM Context (OSS)

**Naming (final):** Product name / PyPI package / npm scope / GitHub org are all unified as `ctxlineage`. Main repo: `ctxlineage/ctxlineage`. CLI command: `ctxlineage` (with short alias `ctxl`). Availability of PyPI / npm / GitHub org names confirmed as of 2026-06-11.

**Brand (final):** The logo is the "Merge" mark (two lineages converging into a single context node, `assets/logo.svg`). Colors: Ink `#252B33` × Lineage Teal `#1FBFAE`. The HTML report UI uses these two colors as its base palette. Assets live in `assets/` (light / dark / org avatar / wordmark).

> **For Claude Code:** This is a pre-implementation plan. Before starting implementation, read this document and write a detailed implementation plan (`docs/plans/YYYY-MM-DD-<feature>.md`) first. Do not implement anything out of scope (see Non-Goals).

**Goal (one line):** A Python library that, with a few inserted lines, shows "what context each LLM call actually consumed" and "how that context flowed into subsequent calls (lineage)" — as a single static HTML file, no server required.

**Concept:** `dbt docs generate` for LLM context. Not an observability SaaS — local-first, lightweight, OSS.

---

## 1. Background & Problem

- LLMOps tools (Langfuse etc.) cover prompt management and tracing well, but visualization that supports **context engineering** (designing what goes into the context window and how) is thin.
- A recurring developer pain: "What did this call actually feed the LLM?" is not immediately visible. Trace UIs exist but require running a server (DB), and the breakdown of components (system prompt / history / RAG chunks / tool results / memory) and token allocation is not visible at a glance.
- There is no way to see **context lineage** — the analogue of data lineage in data engineering: where a context element came from (retrieval / tools / previous turn), how it was transformed (summarization / compression / truncation), and which downstream calls it influenced.
- Nearby products: Langfuse / LangSmith / Arize Phoenix / Opik (all server-based trace viewers), Letta ADE (context visualization but tied to their agent framework). **The lightweight zone — "pip install, run, get a static HTML" — is empty.**

## 2. Target Users

1. **Python developers building LLM apps/agents** (direct OpenAI / Anthropic SDK users, and framework users such as LangChain)
2. **Users of coding agents (Claude Code / Codex, etc.)** — people who install ctxlineage into their project via an agent and have the agent read the visualization output to drive an improvement loop. An official MCP server and Agent Skill are bundled to capture this audience.

## 3. Product Principles

- **Local-first / zero-server:** the artifact is a single HTML file (JSON embedded, opens with no CDN dependency). No DB, no daemon.
- **Non-intrusive by default, explicit as an option:** works with zero tagging. Tagging makes the visualization richer (progressive enhancement).
- **YAGNI:** v1 is all-in on "making it visible". No evals, no prompt management, no SaaS integration.
- **The data belongs to the user:** capture is local JSONL. Nothing is ever sent externally.

## 4. Feature Scope

### v1 — Capture & Visualization (MVP)

**(a) Capture (recommended: SDK auto-patch + optional explicit API)**

```python
import ctxlineage
ctxlineage.init()  # auto-instruments the openai / anthropic SDKs
# subsequent LLM calls are recorded to .ctxlineage/events.jsonl
```

- Auto-patch targets (v1): `openai` (Chat Completions / Responses API), `anthropic` (Messages API). Streaming support is mandatory.
- Recorded: full messages array, model, tool definitions, response, usage (token counts), timestamp, a summary of the call stack (which function made the call).
- Optional explicit API (tagging context elements):

```python
with ctxlineage.span("answer_user_query") as span:
    span.tag("system", system_prompt)
    span.tag("rag_chunks", docs, source="qdrant:products_v2")
    span.tag("history", messages[-10:], transform="truncate_last_10")
    resp = client.chat.completions.create(...)
```

- When tagged, the corresponding strings in `messages` are matched to determine segment boundaries (exact match first, then partial match; unmatched content is shown as "untagged").
- Without tags, a minimal heuristic decomposition still happens: segmentation by role (system / user / assistant / tool) and token allocation.

**(b) Visualization (static HTML generation)**

```bash
ctxlineage report            # .ctxlineage/events.jsonl → ctxlineage-report.html
ctxlineage report --open     # generate and open in browser
```

The HTML has two views:

1. **Call Anatomy (single-call dissection):**
   - A stacked view of the context window from top to bottom. Segments (system / history / rag_chunks / tool_results / untagged…) are color-coded, with token counts and share-of-window bars.
   - Click to expand segment content. Shows utilization against the model's context limit.
   - A sidebar lists calls in the same session chronologically.

2. **Lineage Graph (chain view):**
   - Nodes = context elements (per tag) and LLM calls. Edges = "element → fed into call" and "call output → next element (history, summary, etc.)".
   - Edge detection (v1 can be simple): (i) explicit `source`/`transform` on tags, (ii) if call N's output text (partially) matches call N+1's messages, auto-create an edge, (iii) relationships within the same span.
   - Clicking an element highlights downstream calls that used it (a primitive form of impact analysis).

**(c) Official integrations (for Claude Code / Codex)**

- **MCP server (`ctxlineage-mcp`):** runs over stdio. Exposes tools: `list_sessions` / `get_call(call_id)` / `get_lineage(element_id)` / `generate_report`. Lets coding agents do analyses like "read the context breakdown of the latest call and point out waste".
- **Agent Skill (`skills/ctxlineage-instrument/SKILL.md`):** a procedure document so an agent told "add ctxlineage to this project" correctly decides where to insert `init`, tags the main context-assembly sites, and generates a report. Bundled in the repo.

### v1.5 — Tag API expansion & integrations

- Standardize `transform` kinds (`summarize` / `truncate` / `filter` / `rerank`) and render them as transform nodes in the Lineage Graph.
- **Langfuse trace import (`ctxlineage import --from langfuse`):** an entry point for existing Langfuse users to generate reports with no extra instrumentation. Doubles as awareness-building inside the largest adjacent community.
- Callback handlers for LangChain / LlamaIndex (a plugin interface intended for community contribution).

### v2 — ON/OFF control (future concept; NOT in v1)

- Toggle tagged elements in the HTML report → reflected into `ctxlineage.toml` → `span.tag()` reads the config and excludes those elements from context.
- Key design decision: ON/OFF is **only for users of the explicit tag API**. Not offered to auto-patch-only users (the tool does not own context assembly there). Write-back from static HTML goes through `ctxlineage serve` (a temporary local server).

## 5. Non-Goals (explicitly not doing)

- SaaS / hosting / accounts
- Evals (LLM-as-judge quality scoring, eval datasets, benchmarks / leaderboards), prompt version management, cost-optimization suggestions. *(Distinction: **deterministic contract / regression tests over the recorded artifact** — "data tests, not model evals" — are a separate **post-v0.1.0** direction, see §13. They are explicitly **not** part of v1 and do not soften this guardrail.)*
- DB server (no persistent DB incl. SQLite in v1 — JSONL is enough)
- Non-Python SDKs (but keep the JSONL schema language-agnostic to enable TS etc. later)
- LLM proxying (no request rewriting / forwarding)

## 6. Architecture

```
[user code]
   │  ctxlineage.init() → openai/anthropic SDK monkey-patch
   │  ctxlineage.span()/tag() → explicit metadata
   ▼
[capture layer] ──→ .ctxlineage/events.jsonl  (append-only, 1 line = 1 event)
                          │
                          ▼
[report builder] ── ctxlineage report ──→ ctxlineage-report.html
   │   ・parse JSONL, normalize into sessions/calls/elements/edges
   │   ・segment matching, lineage edge inference
   │   ・inject JSON into template HTML (prebuilt JS bundle)
   ▼
[mcp server] ─ ctxlineage-mcp ─ read-only tools over the same JSONL
```

**Event schema (JSONL, language-agnostic):** `event_type` (llm_call / tag / span_start / span_end), `session_id`, `span_id`, `call_id`, `timestamp`, `payload`. The schema is versioned as JSON Schema under `schema/` (the foundation for future multi-language SDKs and external tool integration).

**Frontend (decided 2026-07-16, supersedes the original React plan):** server-rendered by Python. A deterministic layout engine emits the lineage graph as static inline SVG; the page ships hand-written CSS (single `:root` token block, light + dark both mandatory — default follows `prefers-color-scheme` with a persisted toggle) and a small vanilla-JS layer for interactivity (segment expansion, downstream highlighting, theme toggle). SVG colors reference CSS custom properties so the graph re-themes for free. No external CDN references, no node toolchain, no build step. Reference implementation: drt-hub/drt `drt docs` (deterministic layout, reserved-gutter edge routing); agreed visual direction: `docs/design/m2-mockups/`.

**Sensitive data:** reports contain full prompts. Provide at minimum `ctxlineage report --redact "pattern"` and field-level masking via init options. State clearly in the README: "be careful with sensitive data when sharing reports".

## 7. Tech Stack

- Python 3.10+ / minimal dependencies (`wrapt` for patching, `tiktoken` for token estimation — prefer actual usage values for anthropic, `click` or `typer` for CLI)
- MCP: official `mcp` Python SDK (FastMCP)
- Frontend: server-rendered HTML/SVG from Python; hand-written vanilla JS + CSS shipped as package resources (no node toolchain — decided 2026-07-16, supersedes TS+React+Vite)
- Tests: pytest. SDK patches verified against mocked HTTP (respx etc.). TDD.
- Packaging: `uv` / `hatchling`, PyPI publication assumed
- License: **Apache-2.0 (final).** Contributions via DCO (Developer Certificate of Origin); copyright unified under the org to keep IP clean. No copy-pasting external code (take it as a dependency instead).

## 8. Repository Layout (proposed)

```
ctxlineage/
├── src/ctxlineage/          # capture, report builder, CLI
├── src/ctxlineage_mcp/      # MCP server
├── assets/                  # logo & brand assets (logo.svg / logo-dark.svg / avatar.svg / wordmark.svg)
├── schema/                  # event JSON Schema
├── skills/ctxlineage-instrument/SKILL.md
├── examples/                # RAG app & multi-turn agent samples
├── docs/plans/              # implementation plans (written by Claude Code)
└── tests/
```

## 9. Milestones

1. **M1:** finalize event schema + openai SDK patch + JSONL capture (incl. streaming)
2. **M2:** report CLI + Call Anatomy view (down to untagged heuristic decomposition)
3. **M3:** span/tag API + segment matching + Lineage Graph view
4. **M4:** anthropic SDK support + MCP server + Agent Skill + examples + README → **v0.1.0 release**

Each milestone must be independently demoable. At the end of M2, the experience "I can see my RAG app's context" must already work.

## 10. Success Criteria (v1)

- Adoption: `pip install` + 2 lines of code + 1 command → first report within **5 minutes**
- Experience: the report alone can answer "which call consumes the most tokens", "what % of context do RAG chunks occupy", "does call A's output flow into call B"
- Post-release: prioritize "actual-usage feedback in issues" over GitHub stars

## 11. Project Positioning & Operating Policy

- **Temperature:** build it for fun, and make it stand as a career/portfolio asset. Acquisition/hiring outcomes are "lucky if they happen" side goals — no unsustainable operations premised on them.
- **Low-maintenance by design:** reflected in architecture — keep the public API surface minimal, keep the SDK patch layer thin, delegate framework integrations to the community via a plugin interface, close off-roadmap issues without hesitation (state the maintenance policy in the README).
- **Visibility via low-cost measures only:** one launch announcement at v0.1.0 (HN / X / Zenn etc.), a demo GIF in the README, ecosystem exposure via the Langfuse integration. No ongoing community-management commitment.
- **Keep IP clean (to preserve exit options):** Apache-2.0 + DCO, unified copyright, dependency license checks. Near-zero cost for future flexibility, so do it.

## 12. Risks & Mitigations

- **SDK patch maintenance cost:** fragile against openai/anthropic API changes → keep the patch layer thin, pass unknown fields through into `payload`. Run CI integration tests against the latest SDK versions.
- **Segment matching inaccuracy:** non-exact matches (template variable expansion etc.) → in v1, degrade gracefully to "untagged" without breaking, and honestly display the match rate in the report.
- **HTML bloat with huge contexts:** hundreds of KB of prompts × many calls → collapse + lazy-expand bodies; above a threshold, optionally split bodies into a separate JSON file.
- **Name squatting:** before release: (i) create the GitHub org `ctxlineage`, (ii) publish a `ctxlineage` 0.0.1 placeholder to PyPI, (iii) reserve the `ctxlineage` npm package name (for a future TS SDK). Availability was checked on 2026-06-11 — re-verify immediately before executing.

## 13. Future Direction — Context Contract Testing (post-v0.1.0, NOT v1)

> **Not committed scope.** Recorded 2026-06-13 as a positioning direction. Gated behind v0.1.0
> (M1–M4) shipping first; depends on the M3 lineage graph existing. Full write-up:
> [docs/vision/context-contract-testing.md](vision/context-contract-testing.md).

**Thesis:** the captured artifact (`events.jsonl` + segment decomposition + lineage graph) is
also a substrate for **deterministic, CI-gate-able tests on LLM context** — an evolution of the
visualization mission, not a pivot. Positioning: the **"Elementary" of the LLM-context stack** —
a lightweight OSS layer that rides on the artifact and adds tests-in-CI, below the SaaS
observability players. It operationalizes established theory (the oracle problem; metamorphic
testing, Chen 1998; behavioral/CheckList testing, Ribeiro 2020) rather than inventing it: assert
a **relation** over (input, output), not the output value — most useful relations are cheap,
deterministic, LLM-free checks, with LLM-as-judge pushed to a thin top layer (a "test pyramid").

**Locked decisions (2026-06-13):**

1. **Placement:** post-v0.1.0 track (future v0.2 / "M5"). v1 (M2–M4) is untouched.
2. **Gate substrate:** the **tag API is ctxlineage's `ref()`** — tagged ⇒ exact lineage ⇒ hard CI
   gate; untagged ⇒ inferred lineage ⇒ advisory only. Same line as progressive enhancement (§3)
   and the tag-API-only scope of v2 ON/OFF (§4).
3. **Boundary vs §5:** deterministic **contract/regression tests over the recorded artifact**
   ("data tests, not model evals"); LLM-as-judge quality scoring / eval datasets / benchmarks stay
   Non-Goals. Unique lane: **lineage-grounded provenance assertions** (no competitor has
   context-segment lineage as a first-class artifact).
