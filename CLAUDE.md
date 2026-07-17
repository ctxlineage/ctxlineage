# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ctxlineage: a local-first Python library that records every LLM call (OpenAI / Anthropic SDKs, auto-instrumented via monkey-patch) to an append-only `.ctxlineage/events.jsonl`, then renders a **single static HTML report** (Call Anatomy + Lineage Graph views). Zero server, zero DB, no external data transmission — ever.

**Current state: pre-implementation.** No Python/frontend code exists yet. There are no build/test commands yet — add them to this file as they come into existence.

## Required workflow

- [docs/PLAN.md](docs/PLAN.md) is the canonical spec. Read it before implementing anything.
- Before coding a feature, write a detailed implementation plan under `docs/plans/YYYY-MM-DD-<feature>.md`.
- **Non-Goals (PLAN.md §5) are hard guardrails** — do not implement: SaaS/hosting/accounts, evals, prompt versioning, persistent DBs (incl. SQLite; JSONL is the store), non-Python SDKs, LLM proxying (never rewrite/forward requests).
- TODOs live in GitHub issues (`ctxlineage/ctxlineage`), not in local files. Milestones M1–M4 are issues #1–#4; each milestone must be independently demoable.
- TDD with pytest; SDK patches are tested against mocked HTTP (respx).

## Maintainer commands (`.claude/commands/`)

Repo-scoped slash commands for the maintainer's own workflow (not end-user
features), adapted from the drt-hub operations playbook:

- `/ctxlineage-release-check` — version/doc consistency sweep, then the
  tag → Trusted Publishing flow.
- `/ctxlineage-review-pr` — PR review keyed on ctxlineage's load-bearing rules
  (host-app safety, no proxying, honest data, self-contained report, Non-Goals).
- `/ctxlineage-triage` — open PR/issue triage and release-readiness check.

## Conventions

- **Everything in the repo is English** (code, comments, docs, issues, commits) even when the conversation with the user is in Japanese.
- `base_plan.md` (Japanese) is a local, gitignored draft — never commit it; docs/PLAN.md is the source of truth.
- Commits use the personal identity `K.Masuda <me@masukai.dev>` (already set in repo-local git config). License is Apache-2.0 with DCO; never copy-paste external code — take it as a dependency instead.
- Keep the public API surface and the SDK patch layer minimal (low-maintenance is an explicit design goal); unknown SDK fields pass through into `payload` rather than being modeled.

## Architecture (planned, PLAN.md §6)

Three loosely-coupled layers sharing one artifact, the JSONL event log:

1. **Capture** — `ctxlineage.init()` monkey-patches openai/anthropic SDKs (wrapt); optional explicit `span()`/`tag()` API adds metadata. Writes 1 event per line to `.ctxlineage/events.jsonl`. Streaming support is mandatory.
2. **Report builder** — `ctxlineage report` CLI parses the JSONL, normalizes sessions/calls/elements/edges, runs segment matching (exact → partial → "untagged" fallback, match rate shown honestly) and lineage edge inference, then injects JSON into a prebuilt HTML template.
3. **MCP server** (`src/ctxlineage_mcp/`, FastMCP/stdio) — read-only tools over the same JSONL.

The event schema (`schema/`, versioned JSON Schema) is language-agnostic by design — it is the contract between all layers and future non-Python SDKs.

The report frontend is **server-rendered by Python** (decided 2026-07-16 — no React/Vite, no node toolchain): templates, vanilla JS, and a single CSS-token block live as package resources under `src/ctxlineage/_report/`; the lineage graph is emitted as static SVG whose colors reference CSS custom properties. Light and dark mode are both mandatory (default follows `prefers-color-scheme`, manual toggle persisted). The HTML must open offline (no CDN references). Design reference: the maintainer's drt-hub/drt `drt docs` (deterministic layout engine, reserved-gutter edge routing) and the agreed mockups in `docs/design/m2-mockups/`. Report UI palette: Ink `#252B33` × Lineage Teal `#1FBFAE`.

## Stack

Python 3.10+, `uv` + `hatchling`, src layout. Minimal runtime deps: `wrapt`, `tiktoken` (prefer real `usage` values over estimates). CLI ships as `ctxlineage` with alias `ctxl`.
