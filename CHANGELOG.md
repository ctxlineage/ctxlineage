# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning:
[SemVer](https://semver.org/spec/v2.0.0.html) (pre-1.0: breaking changes may
land in minor versions).

## [Unreleased]

### Added
- Capture core: `ctxlineage.init()` auto-instruments the **openai** (Chat
  Completions + Responses API) and **anthropic** (Messages) SDKs — sync,
  async, and streaming — into append-only `.ctxlineage/events.jsonl`
  (versioned JSON Schema).
- Explicit **span/tag API**: `with ctxlineage.span(...) as sp: sp.tag(name,
  content, source=..., transform=...)` for context provenance.
- **Report** (`ctxlineage report`): one self-contained HTML file with four
  views — Overview, Calls (context-window anatomy), Chain (session flow),
  Graph (sources → elements → calls lineage with impact tracing) — plus
  client-side search, light/dark themes, i18n-safe rendering.
- Segment matching (exact → partial → honest untagged) with a visible tag
  match rate; lineage edge inference (output→input, same-span) computed in
  the report backend.
- Coexistence test matrix with the Langfuse OpenAI drop-in.
