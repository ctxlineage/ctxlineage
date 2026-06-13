# Future Direction: Context Contract Testing (post-v0.1.0)

> **Status:** Direction note, recorded 2026-06-13. **Not committed scope.** This is a
> forward-looking positioning document, not part of the v1 spec. Implementation is
> gated behind v0.1.0 (the visualization MVP, milestones M1–M4) shipping first.
> The canonical spec remains [PLAN.md](../PLAN.md); this document explores where the
> product could go *after* it. Names here (e.g. "Context Contract Testing", `ctxlineage check`)
> are provisional.

## 1. Thesis

ctxlineage's captured artifact — `.ctxlineage/events.jsonl` plus the segment decomposition
and the lineage graph built on top of it — is not only a *visualization* substrate. It is a
substrate for **deterministic, CI-gate-able tests on LLM context.**

Positioning analogy: **the "Elementary" of the LLM-context stack.** A lightweight OSS layer
that rides on an existing artifact and adds *tests-in-CI*, sitting *below* the SaaS
observability players — analogous to how
[Elementary](https://github.com/elementary-data/elementary) is a dbt-native, OSS data
observability + testing layer that rides on dbt's run artifacts and sits below Monte Carlo /
Bigeye in the data stack.

This is an **evolution of the existing mission, not a pivot.** The same lineage data that
powers "see what context each call consumed" also powers "assert that the output is grounded
in that context" and "flag context that was never used." Visualization and testing are two
readings of one artifact.

## 2. Why this direction, why us

- **LLM app quality is increasingly gated in CI.** The dominant approach — LLM-as-a-judge —
  is stochastic, costly, and flaky. There is room for a cheaper, deterministic first line.
- **The theory already exists.** "How do you test a system with no exact oracle?" is a
  central, solved-in-principle question in software testing:
  - **The oracle problem** — Barr et al., *The Oracle Problem in Software Testing: A Survey* (2015).
  - **Metamorphic testing** — Chen et al. (1998): when you cannot assert `f(x) == expected`,
    assert a **relation between multiple runs** (e.g. `f(x)` vs `f(perturb(x))`). This is exactly
    "a relationship determined by the function between static inputs and outputs."
  - **Behavioral / property-based testing** — *CheckList* (Ribeiro et al., ACL 2020) formalizes
    three classes that map cleanly onto this product: **INV** (invariance under
    output-preserving perturbation), **DIR** (output moves in a known direction), **MFT**
    (minimum functionality).

  We **operationalize known theory on a novel substrate** — we do not need to invent theory.
- **A unique asset.** Black-box frameworks treat an LLM call as `prompt → output`. ctxlineage
  decomposes the input into typed segments (system / history / rag_chunks / tool_results /
  untagged) and carries lineage edges. Nobody else has **context-segment-level lineage as a
  first-class artifact**, which is what enables provenance/groundedness assertions and
  dead-context detection.

## 3. One correction to the framing: "relation", not "function"

An LLM is not a function (it is non-deterministic, context-sensitive, non-compositional).
Contract-testing `input → output` as functional equality breaks. The correct frame is
**relational and statistical**: do not assert the output *value*; assert a **predicate over
(input, output)** or a **relation between pairs of (input, output)**. This sidesteps both the
oracle problem and non-determinism at once — and crucially, most useful relations are cheap,
deterministic checks that **call no LLM at all** (containment, monotonicity, invariance, schema
conformance, provenance).

## 4. The Elementary analogy (mapping)

| dbt + Elementary | ctxlineage |
| --- | --- |
| `manifest.json` / `run_results.json` (the artifact) | `.ctxlineage/events.jsonl` |
| dbt tests (schema / anomaly tests) | metamorphic / invariance / provenance assertions |
| `edr report` static HTML | `ctxlineage report` static HTML |
| Monte Carlo / Bigeye (SaaS above) | Langfuse / LangSmith / Arize |
| `dbt build` run lifecycle (where tests run) | pytest in CI / agent-framework callback |
| dbt `ref()` graph (lineage, **exact & declarative**) | **see §5 — this is the crux** |

## 5. The one critical disanalogy: exact vs inferred lineage

Elementary's leverage is that it **does not build lineage — it inherits exact, declarative
lineage from dbt's `ref()` graph.** Its tests can be hard CI gates because the foundation is
exact.

ctxlineage **manufactures** lineage via segment matching and edge inference — which is
heuristic and lossy ([PLAN.md §12](../PLAN.md) already commits to showing match-rate honestly
and degrading to "untagged"). **This difference decides what we can safely fail CI on.** Gating
on an inferred edge produces a flaky gate.

## 6. Resolution: the tag API is ctxlineage's `ref()`

The existing explicit `span()` / `tag()` API ([PLAN.md §4](../PLAN.md)) is the device that turns
**inferred** lineage into **declarative** lineage. So the test layer is two-tier:

- **Tag present → lineage is exact → assertions can be hard gates (fail CI).**
- **No tag → lineage is inferred → assertions are advisory (warn only).**

This adds no new principle. It is the same line as **progressive enhancement** (PLAN §3) and the
decision that **v2 ON/OFF is tag-API-only** (PLAN §4): the people who can *gate* are the people
who *tagged*, exactly as Elementary's benefits accrue to people who use dbt.

**Locked decision (2026-06-13): the tag API is the gate substrate.** Raw-SDK inferred lineage is
advisory only. An agent-framework (e.g. LangGraph) integration could later supply exact lineage
from an explicit node graph — a possible future exact-lineage source, **not committed now**.

## 7. The test pyramid for LLM context

```
        ┌──────────────────┐
        │   LLM judge       │  expensive, flaky, FEW — semantic quality only (helpfulness/tone)
        ├──────────────────┤
        │ metamorphic / INV │  medium — robustness & directional expectations
        ├──────────────────┤
        │ static / contract │  cheap, deterministic, MANY — schema / containment / provenance / regression
        └──────────────────┘
```

The judge is the **thin top, not the foundation.** Deterministic checks at the bottom catch
structure, robustness, grounding, and regressions; the judge is reserved for the residual
open-ended semantic quality. The two are **complementary, not either/or** — which is precisely
why "I don't like LLM-as-judge" resolves to "push the judge into a thin top layer," not "remove it."

## 8. Assertion classes the substrate enables

- **Structural / schema:** window composition, per-segment token budgets/caps, required-segment
  presence, context-limit utilization thresholds. (Deterministic; available from capture alone.)
- **Regression / differential:** record a golden run, re-run in CI, diff at the **segment** level.
  Fully deterministic when run **offline over recorded JSONL**. The natural first deliverable.
- **Metamorphic / invariance (CheckList INV/DIR):** perturb `rag_chunks` order → answer should be
  invariant; drop a cited chunk → answer should change/degrade.
- **Provenance / groundedness (the unique class):** every claim in the output should trace to a
  context segment via lineage; flag **dead context** (a chunk with no downstream edge). This last
  one *is* the original context-engineering mission re-read as a test.

## 9. CI reality: non-determinism

- **Offline metamorphic over recorded JSONL = deterministic → safe hard gate.** Start here.
- **Live property tests = need statistical gates** (run N times, assert pass-rate ≥ threshold;
  `temperature=0`/seed reduces but does not eliminate variance). Making a flaky signal into a CI
  gate is a real design problem to face head-on, not paper over.

Integration points ("part of the orchestrator"): a **pytest plugin** (CI) and/or an
**agent-framework callback / graph-node hook** (runtime) — the analogue of Elementary running
inside `dbt build`.

## 10. Boundary vs [PLAN.md §5](../PLAN.md) Non-Goals

This is the guardrail this direction must respect. The Elementary vocabulary draws the line
cleanly: Elementary ships **data tests / anomaly tests, not model evals** — it never grades
whether data is "good", only structural properties (freshness/volume/schema).

- **Stays a Non-Goal:** LLM-as-judge quality scoring, eval datasets, benchmarks/leaderboards,
  prompt version management, cost-optimization suggestions.
- **This track is:** deterministic **contract / regression tests over the recorded artifact** —
  "data tests, not model evals" — gate-able only via the tag API, shipped **post-v0.1.0**.

The distinction is real (structural assertions vs quality scoring), but it *is* a refinement of a
hard guardrail and must stay clearly marked future + non-judge.

## 11. Competitive map (honest)

- **Crowded — judge / eval:** promptfoo, DeepEval (Confident AI), Ragas (RAG faithfulness),
  TruLens (feedback functions), Giskard, Patronus, Inspect (UK AISI), OpenAI Evals,
  LangSmith / Arize Phoenix evals.
- **Closest to the metamorphic / invariance niche (less crowded):** LangTest (John Snow Labs;
  explicit robustness/invariance tests) and the academic CheckList lineage. TruLens feedback
  functions and Ragas faithfulness are nearest on groundedness.
- **Open lane:** **context-segment-level, lineage-grounded provenance assertions.** No one else
  has the lineage graph as a first-class artifact to assert against. If we enter, enter on this
  angle — a head-on general-eval fight against DeepEval/promptfoo is unwinnable.

## 12. Risks & open questions

- **Inferred-lineage trust:** provenance gates require the tag API → adoption friction. The whole
  value of hard gates is bounded by tag adoption.
- **Flaky gates** from non-determinism; statistical treatment adds real complexity.
- **§11 low-maintenance tension:** new surface area fights the "minimal API, thin layer" principle.
  Mitigation: ship a *handful* of built-in relations + a plugin hook. **Do not build a framework.**
- **Position borrowed, business model not:** Elementary's position is a funnel into Elementary
  Cloud (SaaS). PLAN §11 explicitly rejects SaaS/unsustainable ops. We take the **position**, not
  the **monetization** — fine under the §11 portfolio framing, but the analogy must not smuggle in
  SaaS expectations.
- **Hard dependency:** this needs the M3 lineage graph to exist. **Do not start before v0.1.0.**

## 13. Decisions locked (2026-06-13)

1. **Placement:** post-v0.1.0 track (a future v0.2 / "M5"). v1 visualization (M2–M4) is untouched.
2. **Gate substrate:** the tag API = ctxlineage's `ref()`. Tagged → hard gate; untagged → advisory.
3. **Issues:** a single tracking issue for this track; the M2–M4 milestone issues are not touched.
