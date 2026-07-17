# Positioning — ctxlineage in the landscape

> Status: **resolved** (#17, 2026-07-17). Living document; feeds the launch
> messaging (#8) and the README pitch. The one-line spine below is the
> settled differentiation.

## The spine (settled #17)

**Everyone else draws a graph and stops at description. ctxlineage makes
runtime context an engineered artifact you can improve and — the part nobody
else can do — test.**

The nearby "graph" tools each visualize a *different* structure, and all three
stop at **description** ("here is what relates to what"), never reaching the
engineering loop (understand → improve → **verify in CI**):

- **Ontologies / semantic layers** → the *domain* structure ("what context
  *should* exist"). Prescriptive, ex-ante.
- **Trace viewers (Langfuse / LangSmith / Phoenix)** → the *execution* structure
  (span tree: what called what, timing/nesting). They show a call happened and
  its I/O — not how the window was *composed* (40% RAG, 30% stale history).
- **Knowledge / note graphs (GraphRAG, Claude+Obsidian, etc.)** → the
  *knowledge-base* structure (how documents link). Unrelated to what a specific
  call actually consumed.

ctxlineage's object is none of these: it is **runtime context lineage** — the
anatomy of what one call consumed and where each element came from / flowed to.
That substrate is the only one you can *test*, because testing needs a
first-class `(context element → call → output)` relation to assert over. This
is why the dbt analogy holds: dbt didn't invent SQL transforms, it made them an
*engineered discipline* (versioned, tested, documented, in CI). ctxlineage aims
to be that for context.

## One-liner

**See exactly what context each LLM call consumed — and how it flowed into the
next one.** A Python library that turns your app's LLM calls into a single
static HTML report: context-window anatomy per call, context lineage across
calls. No server, no DB, no account.

## The landscape

| Product | Form factor | Center of gravity |
|---|---|---|
| Langfuse | OSS, server + DB (or cloud) | Tracing, sessions, dashboards, prompt mgmt, evals |
| LangSmith | SaaS | Tracing, datasets/evals, prompt hub, monitoring |
| Arize Phoenix | OSS, server | OTel tracing, evals, embedding analysis |
| Opik (Comet) | OSS, server | Tracing, evals, prompt library |
| Letta ADE | Vendor IDE (framework-locked) | Context-window inspector, agent memory |
| OTel GenAI semconv | Standard | Generic spans; no context semantics |

The category's common surface: ① searchable trace list ② span-tree detail
③ aggregate dashboards ④ sessions ⑤ prompt management ⑥ evals — all behind a
server you have to run (or a SaaS you have to trust with your prompts).

## What makes ctxlineage different

Three pillars, in order of defensibility:

1. **Context lineage (nobody has this).** The dbt-docs mental model applied to
   context engineering: calls are functions, context elements are typed inputs
   with provenance (user / app / previous LLM output / tool·MCP / structured
   data), outputs flow into downstream inputs. Loops, fan-out, and accumulation
   become visible objects. Competitors show *that* calls happened; ctxlineage
   shows *how context moved between them*.
2. **Context-window anatomy.** Not a span tree — a dissection of what actually
   filled the window and at what token cost (real `usage`, honest estimates,
   window-pressure vs the model limit). Only Letta ADE is nearby, and it is
   locked to their agent framework.
3. **Zero-infrastructure artifact.** `pip install` + 2 lines + 1 command →
   a self-contained HTML file. Local-first, nothing ever transmitted, diffable
   output, trivially hostable (CI → S3/GCS, #9). The "run a server first" tax
   is the category's biggest adoption filter, and we simply don't charge it.

What we deliberately do NOT do (Non-Goals, PLAN.md §5): evals, prompt
management, SaaS/accounts, proxying. The report *observes*; it does not sit in
the request path or grade outputs.

## vs. ontologies / semantic layers (draft for #17)

The trend: modeling your domain up front — Palantir-style ontologies, semantic
layers, knowledge-graph/GraphRAG context assembly — so that LLM context can be
*composed from a governed model*.

- **They are prescriptive and ex-ante**: invest in modeling the world, then
  assemble context from the model. Value scales with modeling effort.
- **ctxlineage is observational and ex-post**: zero modeling required; it
  records what your app *actually* fed the model and where it came from —
  whatever assembly strategy produced it.

They answer different questions: an ontology answers *"what context should
exist?"*; ctxlineage answers *"what context was actually used, and did it
matter?"*. Complementary, not competing:

- Ontology-assembled context is just another **source kind** in the lineage
  (`source="ontology:customer_360.orders"`), so ontology users get attribution
  for free.
- Lineage evidence (which elements were actually consumed downstream, which
  were dead weight) is exactly the feedback an ontology/semantic-layer team
  needs to prune and prioritize their model.

Candidate one-liner: *"Ontologies design the pantry; ctxlineage shows you what
actually went into the meal."*

## Launch stance (settled #17)

Lead with the **engineering/testing trajectory** (so we don't read as "yet
another trace viewer"), but stay honest about what ships when — testing is
v0.2, not v0.1.0:

- **v0.1.0 claims (shipped, defensible today):** runtime context anatomy
  (window composition, token cost), context lineage (provenance + downstream
  impact), local-first / zero-server. None of the three "graph" categories
  above overlaps all three.
- **The arc we tell (roadmap, not a shipped claim):** from *see & understand*
  → *engineer & **test***. The README hero uses the engineering framing; a
  one-line "context testing lands in v0.2" keeps it honest (the honest-data
  ethos applies to our own marketing too — never sell an unshipped capability).

## Differentiation levers on the roadmap

- **Agent-native analysis (M4, planned)**: the MCP server lets a coding agent
  read the lineage and *advise* — "this call wastes 40% of its window on stale
  history" — without ctxlineage itself ever calling an LLM or sending data
  anywhere. Advice stays opt-in and agent-driven; the core tool stays inert.
- **In-report AI advice (post-v1, undecided)**: a "suggest improvements" button
  using a user-supplied API key. Relaxes a v1 Non-Goal (optimization
  suggestions) and the zero-transmission default — needs an explicit product
  decision (issue #21).
- Search/filter across calls (#20), context contract testing (#14), report
  hosting in CI (#9), landing page (#10).

## v0.2 direction (settled #17): two parallel tracks

Post-v0.1.0, run **depth and width together** (both cheap to slice; parallel
sessions):

- **Depth — context contract testing (#14).** The defensible core and the proof
  the positioning isn't vapor. Ship one deterministic, LLM-free assertion first
  (candidate: a *grounding* check — was a tagged element actually consumed by
  the call? — which requires lineage and so no competitor can copy it; or a
  *window-budget* assert). Write the design doc during the v0.1.0 release (it
  doesn't delay the tag) so the announcement can point at a concrete plan.
- **Width — coding-agent reach via transcript import.** `claude -p` / Claude
  Code / Codex are **separate non-Python processes**, so the SDK monkey-patch
  can't capture them — but Claude Code already writes local session transcript
  JSONL (`~/.claude/projects/.../*.jsonl`; `-p` also emits `--output-format
  json`). An **importer** (`ctxlineage import --from claude-code`) reads that
  on-disk artifact into the same report — no server, no runtime hook,
  local-first-native, and it opens the (large, growing) coding-agent audience.
  Cheaper and more on-ethos than the OTel-ingestion path (#26), which stays the
  bridge for tools that emit OTel GenAI spans but not a local transcript.
  Sequence the two either way; the transcript importer is a 1–2 day PoC.
