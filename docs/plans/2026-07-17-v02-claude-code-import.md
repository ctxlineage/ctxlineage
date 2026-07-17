# `ctxlineage import --from claude-code` (v0.2 "width" PoC)

> **For Claude:** TDD, one PR. Implements #57 (the "width" half of v0.2; "depth"
> is #14). Direction settled in the #17 positioning discussion — this plan is
> about *how*, not *whether*. Spec anchors: PLAN.md §4 (progressive enhancement
> tier; `ctxlineage import --from langfuse` already establishes the `import`
> verb), PLAN.md §5 Non-Goals, `docs/positioning.md`.

**Goal:** read a Claude Code session transcript — a local artifact the tool has
*already* written — and normalize it into the existing event schema, so the
existing `report` / `normalize` / MCP pipeline renders it unchanged.

**Why an importer and not capture:** `claude -p` / Claude Code are separate,
non-Python processes, so `ctxlineage.init()`'s monkey-patch cannot see their
internal LLM calls. But the context is already on disk. Reading a local file the
tool already produced is *more* on-ethos than capture: no server, no injection,
no proxying. Non-Goals are untouched.

**Not in scope:** #26 (OTel GenAI semconv) is the *sibling* adapter for tools
that emit spans but no local transcript. Both sit over the same schema; this PR
does not touch it. Codex/other agents are later adapters behind the same verb.

## The transcript format (verified against real sessions, 2026-07-17)

Claude Code writes one JSONL per session at
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`. Verified facts that drive
the design — each one changes the mapping:

1. **One API response is fanned out across multiple records.** In a sampled
   session, 130 `assistant` records carried only **54 distinct `message.id`** —
   Claude Code writes roughly one record per content block, and **every record
   repeats an identical copy of the same `usage`**. `requestId` is 1:1 with
   `message.id` (54/54).
   → **One `llm_call` per `message.id`, not per record.** Mapping per-record
   would report 130 calls instead of 54 and inflate tokens ~2.4× by summing
   duplicated usage. This is the single highest-risk correctness trap here.
2. **The transcript is a tree, not a list.** Records link via
   `parentUuid`; rewind/retry branches it (sampled session: 2 parents with >1
   distinct child; 1 root; 0 dangling parents).
   → Reconstruct each call's request by **walking `parentUuid` ancestry** from
   the assistant record back to the root. The ancestor chain *is* what was sent;
   "all preceding lines" would be wrong across a branch.
3. **`usage` is real, and cache-aware.** Assistant records carry the API's own
   `input_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens` /
   `output_tokens` (all 2669 sampled assistant records had usage). Under Claude
   Code's prompt caching `input_tokens` alone is tiny (observed: `2`) while the
   real prompt is ~33.6k — `normalize._canonical_usage` **already** folds cache
   reads/creations into `prompt_tokens`, so we get the true figure for free.
4. **Assistant `message` is already an Anthropic Messages response** (`id`,
   `model`, `role`, `content` blocks, `stop_reason`, `usage`), and `user`
   messages carry `tool_result` blocks. `normalize.py` already renders exactly
   this shape post-#30 (`_chat_segments`, `_part_text` for
   `tool_use`/`tool_result`/`thinking`, `_chat_output` for
   `{"type": "message"}`).
   → The mapper is mostly **assembly, not translation**. Do not touch the
   rendering side.
5. **~10 bookkeeping record types exist** beyond `user`/`assistant`: `ai-title`,
   `attachment`, `file-history-snapshot`, `file-history-delta`, `last-prompt`,
   `mode`, `pr-link`, `queue-operation`, `relocated`, `worktree-state`,
   `system`. Ignore all of them (forward-compatibly: ignore unknown types).
6. **`cwd` is recorded inside the records** — authoritative project path. Use it
   for session discovery instead of reverse-engineering the directory-name
   encoding.
7. `isSidechain` marks Task-subagent turns; `isCompactSummary` marks a
   compaction boundary; `isMeta` marks injected (non-human) user records.

## What the transcript does NOT preserve (the honest-data core)

Establish these by absence, and **disclose rather than invent**:

- **The system prompt.** Claude Code's system prompt is never written to the
  transcript. It is a large share of the real prompt tokens.
- **Tool definitions.** The `tools` array (Read/Edit/Bash/…) is not in the
  transcript, and it too consumes real window tokens.
- **Request params** — `temperature`, `max_tokens`, `top_p`, `stop_sequences`,
  `cache_control` breakpoints.
- **`duration_ms`** — records carry a single timestamp, not request start/end.
- Any send-time injection (reminders etc.) not echoed into a record.

**The disclosure is quantified, not hand-waved.** We know the *real* prompt size
(`usage`, fact 3) and we know the size of what we could reconstruct (sum of
segment `tokens_est`). The difference is exactly the unpreserved system prompt +
tool definitions:

```
unaccounted_tokens = usage.prompt_tokens − Σ segment.tokens_est
```

So the importer *measures* the gap instead of hiding it. That is the feature.
We do **not** synthesize a placeholder system segment — that would be inventing
data.

### Estimated vs reconstructed

The report already separates real `usage` from estimated `tokens_est`; we lean
on that convention rather than inventing a second one.

- `usage` → **reconstructed** (the API's own numbers, verbatim).
- per-segment token counts → **estimated** (`tokens.estimate_tokens`, tiktoken
  against a Claude model — an approximation; Anthropic's tokenizer differs).
- Labelled explicitly in `payload["import"]`, which the schema permits
  (`payload` is intentionally open; CLAUDE.md: unknown fields pass through into
  `payload` rather than being modeled).

```jsonc
"import": {
  "source": "claude-code",
  "transcript": "<path>",
  "session_id": "<uuid>",
  "usage": "reconstructed",          // real API numbers from the transcript
  "segment_tokens": "estimated",     // tiktoken approximation
  "not_preserved": ["system_prompt", "tool_definitions", "request_params", "duration_ms"],
  "unaccounted_prompt_tokens": 21437 // real prompt_tokens − Σ reconstructed segments
}
```

## Mapping

| transcript | event |
|---|---|
| one `message.id` (all its assistant records merged, blocks in record order) | one `llm_call` |
| ancestry (`parentUuid` walk) of that record, user/assistant only | `payload.request.messages` |
| assistant `message` (merged blocks + `stop_reason` + `model`) | `payload.response` |
| assistant `message.usage` (one copy, **never summed**) | `payload.usage` |
| each human user turn → the tool loop that follows it | `span_start` / `span_end` |
| — | **no `tag` events** |

- `provider: "anthropic"`, `api: "messages"` → routes to the existing
  `_chat_segments` / `_chat_output` path.
- **No tags.** Per #57, a transcript has no tags → "auto/untagged" heuristic
  decomposition tier (PLAN §4: "Without tags, a minimal heuristic decomposition
  still happens: segmentation by role"). Emitting synthetic tags would fake a
  match rate; `stats.tags.match_rate` stays `null`, honestly.
- **Spans = user turns.** A human user prompt opens a span; it closes at the
  next human turn. This gives the Lineage Graph its `same_span` chains, so the
  agent's whole tool loop for one request reads as one unit. `isMeta` /
  `isCompactSummary` records are not human turns and do not open spans.
- **Sidechains** (`isSidechain: true`, Task subagents) are imported but kept in
  their own spans, never merged into the main chain.
- IDs are derived deterministically from `message.id` / record `uuid` so a
  re-import is byte-identical (no `uuid4()`).
- `timestamp` is the record's own — never `utc_now_iso()`, or the report's
  chronology would collapse to import time.

## CLI

```
ctxlineage import --from claude-code [--session <id> | <path>] [--dir .ctxlineage] [--dry-run]
```

- `click`, added to `src/ctxlineage/_cli.py` as `@main.command("import")` on
  `def import_(...)` (`import` is a reserved word). **Additive and minimal** —
  #14 (DEPTH) touches this same file in a parallel worktree; keep the diff
  small so whoever rebases second has an easy time.
- `--from` is required and validated against a known-adapters list, so
  `--from langfuse` (PLAN §4 v1.5) and #26 slot in later without a CLI redesign.
- Target selection: explicit `<path>` → `--session <id>` (searched across
  projects by filename, which *is* the session id) → default: newest transcript
  whose recorded `cwd` matches the current directory.
- Appends to `.ctxlineage/events.jsonl` via the existing `EventWriter`
  (append-only store preserved; JSONL stays the store — no DB).
- **Refuses to import a session already present** in the target log (a second
  import would double-count every call). Clear message; no `--force` in the PoC
  — rewriting an append-only log is against the ethos.
- `--dry-run` prints the summary (calls, spans, unaccounted tokens, what was not
  preserved) and writes nothing.
- Output discloses the gap on stdout, e.g.:
  `Imported 54 call(s), 7 span(s) … usage reconstructed; 21,437 prompt token(s)
  unaccounted (system prompt + tool definitions are not preserved in the
  transcript).`

## Layout

```
src/ctxlineage/_import/__init__.py      # adapter registry (claude-code today)
src/ctxlineage/_import/claude_code.py   # reader + mapper (pure: path -> events)
tests/fixtures/claude_code/*.jsonl      # hand-written fixtures
tests/test_import_claude_code.py        # mapper tests
tests/test_cli_import.py                # CLI tests
```

Mapper is pure (`transcript path → list[event]`) so tests need no CLI, and the
CLI stays a thin shell.

## Privacy

Real transcripts under `~/.claude/projects/` contain the maintainer's own prompt
bodies. **Fixtures are hand-written; no real session content is committed.**
This session inspected real transcripts for *structure only* (`jq` over keys,
types, and counts). Fixtures are minimal but must cover the traps above.

## Tests (TDD)

1. **Multi-record fan-out** — 3 records sharing one `message.id` → **1** call,
   blocks merged in order, usage counted **once** (the ~2.4× trap).
2. **Cache-aware usage** — `input_tokens: 2` + `cache_read: 19078` +
   `cache_creation: 14551` → `prompt_tokens == 33631` through `_canonical_usage`.
3. **Ancestry, not file order** — a branched transcript (rewind): the request
   contains only the ancestor chain, not the abandoned branch.
4. **Honest gap** — `unaccounted_prompt_tokens` > 0 and equals
   `prompt_tokens − Σ tokens_est`; no synthetic system segment is emitted.
5. **No tags** — zero `tag` events; `stats.tags.match_rate is None`.
6. **Spans** — one span per human turn; tool loop grouped; `isMeta` /
   `isCompactSummary` do not open spans.
7. **Bookkeeping/unknown record types ignored** without error.
8. **Schema conformance** — emitted events validate against
   `schema/events.v1.schema.json` (reuse `test_schema.py`'s validator).
9. **End-to-end** — fixture → `import` → `report` renders; 4 views hold
   (assert on `--json` contract, matching `test_cli.py` style).
10. **Determinism** — importing twice yields identical events.
11. **Duplicate guard** — importing an already-imported session is refused.
12. **Timestamps** — come from records, not import time.

## Definition of done

Real transcript → `ctxlineage import --from claude-code` → existing
`ctxlineage report` renders it (4 views hold). Reconstructed vs estimated
labelled. Fixture tests cover the mapping. This plan committed. CI green.
Comment on #57 recording what the transcript did *not* preserve.
