"""Claude Code session transcript -> ctxlineage events (schema v1).

Claude Code is a separate, non-Python process, so init()'s monkey-patch cannot
see its LLM calls. But it already writes a per-session transcript to
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`, so we read that local
artifact instead: no server, no injection, no proxying.

Assistant records already carry an Anthropic Messages response verbatim, which
_report/normalize.py renders as-is (post-#30). So this module assembles rather
than translates, and the rendering side stays untouched.

Three transcript facts drive everything here (verified against real sessions,
2026-07-17 — see docs/plans/2026-07-17-v02-claude-code-import.md):

1. One API response is fanned out across several records, one per content
   block, each repeating an identical copy of the response's `usage`. So one
   call is one `message.id`, NOT one record — per-record mapping would both
   miscount calls and multiply tokens by the fan-out factor.
2. Records form a tree via `parentUuid` (rewind/retry branches it), so a call's
   request is its ancestry, not the lines above it in the file.
3. `usage` is the API's own, and cache-heavy: `input_tokens` alone can be ~2
   while the real prompt is ~33k. normalize._canonical_usage already folds the
   cache counters back in.

What a transcript does not preserve is disclosed rather than invented; see
NOT_PRESERVED and _import_meta().
"""

from __future__ import annotations

import json
from pathlib import Path

from ctxlineage._events import SCHEMA_VERSION
from ctxlineage._report import normalize
from ctxlineage._report.tokens import estimate_tokens

SOURCE = "claude-code"
PROJECTS_DIR = Path.home() / ".claude" / "projects"

#: Facts the transcript structurally does not record. Each was really sent and
#: really consumed window tokens; none is recoverable from the file. Measured on
#: a real session (2026-07-17): the first call's prompt was 33,631 tokens while
#: its only reconstructable message was 318, so system_prompt + tool_definitions
#: alone accounted for ~33.3k. reasoning_text explains the rest of the gap, which
#: grows with the conversation (r=0.99 against the stripped-block count).
NOT_PRESERVED = (
    "system_prompt",
    "tool_definitions",
    "reasoning_text",
    "request_params",
    "duration_ms",
    "stream_flag",
)

_TURN_TYPES = ("user", "assistant")
_LABEL_MAX = 60


def read_transcript(path: str | Path) -> tuple[list[dict], int]:
    """Parse a transcript JSONL. Returns (records, skipped_line_count).

    Read with ``errors="replace"`` for the same reason ``load_events`` does: a
    corrupt or truncated byte in the agent's transcript degrades to a skipped
    line, not a raw ``UnicodeDecodeError`` that aborts the whole import.
    """
    records: list[dict] = []
    skipped = 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(record, dict) and record.get("type"):
            records.append(record)
        else:
            skipped += 1
    return records, skipped


def _is_turn(record: dict) -> bool:
    """A conversation turn, as opposed to Claude Code's own bookkeeping records
    (ai-title, file-history-snapshot, queue-operation, ...). Unknown types from
    future versions fall out here too, rather than raising."""
    return record.get("type") in _TURN_TYPES and isinstance(record.get("message"), dict)


def _is_human_turn(record: dict) -> bool:
    """A prompt a person actually typed.

    Excludes tool results (a user-role message of tool_result blocks), injected
    reminders (isMeta) and the post-compaction carry-over (isCompactSummary) —
    none of those are someone asking for something.
    """
    if record.get("type") != "user" or record.get("isMeta") or record.get("isCompactSummary"):
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list):
        return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    return bool(content)


def _label(record: dict) -> str:
    content = (record.get("message") or {}).get("content")
    text = content if isinstance(content, str) else normalize._content_to_text(content)
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= _LABEL_MAX else line[: _LABEL_MAX - 1] + "…"
    return "turn"


def _ancestry(record: dict, by_uuid: dict) -> list[dict]:
    """The record's ancestors, root-first, excluding the record itself.

    This is what makes a rewind safe: after a branch the file still holds the
    abandoned turns, but they are not ancestors of the surviving call, so they
    are correctly absent from its request.
    """
    chain: list[dict] = []
    seen = {record.get("uuid")}
    current = by_uuid.get(record.get("parentUuid"))
    while current is not None and current.get("uuid") not in seen:
        seen.add(current.get("uuid"))
        chain.append(current)
        current = by_uuid.get(current.get("parentUuid"))
    chain.reverse()
    return chain


def _nearest_human(record: dict, by_uuid: dict) -> dict | None:
    """The human turn this call is working on.

    Resolved through ancestry, not file order: a Task subagent's turns are
    interleaved into the same file, so file order would hand a main-chain call
    the subagent's span.
    """
    for ancestor in reversed(_ancestry(record, by_uuid)):
        if _is_human_turn(ancestor):
            return ancestor
    return None


def _as_messages(chain: list[dict]) -> list[dict]:
    """Turn a record chain into the messages array that was really sent,
    collapsing a fanned-out response back into one assistant message."""
    messages: list[dict] = []
    open_id = None
    for record in chain:
        if not _is_turn(record):
            continue
        message = record["message"]
        if record["type"] == "assistant":
            blocks = message.get("content") or []
            message_id = message.get("id")
            if message_id is not None and message_id == open_id and messages:
                messages[-1]["content"].extend(blocks)
                continue
            open_id = message_id
            messages.append({"role": "assistant", "content": list(blocks)})
        else:
            open_id = None
            messages.append(
                {"role": message.get("role", "user"), "content": message.get("content")}
            )
    return messages


def _group_calls(records: list[dict]) -> list[list[dict]]:
    """Assistant records grouped into one entry per API response.

    Keyed on `message.id` (1:1 with `requestId` in practice), falling back to
    the record uuid so a record without an id still yields its own call.
    """
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for record in records:
        if record.get("type") != "assistant" or not _is_turn(record):
            continue
        key = (record["message"].get("id")) or record.get("uuid")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(record)
    return [groups[k] for k in order]


def _stripped_reasoning_blocks(messages: list[dict]) -> int:
    """Thinking blocks the transcript kept the shape of but emptied the text of.

    Claude Code writes each thinking block with its `signature` but with
    `thinking: ""` (verified: 887 of 887 blocks across every local session). The
    reasoning was really sent back on later turns and really cost tokens — ~923
    each in the sampled session — so we count what we know is missing instead of
    reading the empty string as "there was nothing here".
    """
    return sum(
        1
        for message in messages
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "thinking" and not block.get("thinking")
    )


def _import_meta(path, request: dict, usage, sidechain: bool) -> dict:
    """Provenance, and an honest account of what we could not reconstruct.

    The gap is measured, not hand-waved: we know the real prompt size (usage)
    and the size of what we rebuilt, and the difference is everything in
    NOT_PRESERVED that costs tokens — the system prompt, the tool definitions,
    and the stripped reasoning text. It is computed through normalize's own
    functions so it matches the figure the report shows, and it stays signed: a
    negative value would be evidence the estimator overshot, which is worth
    seeing rather than clamping away.

    Do not sum unaccounted_prompt_tokens across calls. Each call's prompt
    re-sends the whole conversation, so the totals overlap heavily and a sum
    reads as a far bigger number than any real quantity.
    """
    meta = {
        "source": SOURCE,
        "transcript": str(path),
        "usage": "reconstructed" if usage else "unavailable",
        "segment_tokens": "estimated",
        "not_preserved": list(NOT_PRESERVED),
        "reasoning_blocks_stripped": _stripped_reasoning_blocks(request.get("messages") or []),
    }
    if sidechain:
        meta["sidechain"] = True
    reported = (normalize._canonical_usage(usage) or {}).get("prompt_tokens") if usage else None
    if reported is None:
        return meta
    model = request.get("model") or ""
    reconstructed = sum(
        estimate_tokens(segment["content"], model) for segment in normalize._chat_segments(request)
    )
    meta["prompt_tokens_reported"] = reported
    meta["prompt_tokens_reconstructed_est"] = reconstructed
    meta["unaccounted_prompt_tokens"] = reported - reconstructed
    return meta


def to_events(records: list[dict], *, path: str | Path = "") -> list[dict]:
    """Normalize transcript records into schema-v1 events."""
    by_uuid = {r["uuid"]: r for r in records if r.get("uuid")}
    session_id = next(
        (r["sessionId"] for r in records if r.get("sessionId")),
        Path(path).stem or "claude-code",
    )

    spans: dict[str, dict] = {}
    calls: list[dict] = []
    for group in _group_calls(records):
        head = group[0]
        message = head["message"]
        # blocks from every record of the response, in record order
        blocks = [b for record in group for b in (record["message"].get("content") or [])]
        # ancestry of the FIRST record: from the last one, the model's own
        # earlier blocks would come back as part of its own prompt
        request = {
            "model": message.get("model"),
            "messages": _as_messages(_ancestry(head, by_uuid)),
        }
        # one copy — every record of a fanned-out response repeats it
        usage = message.get("usage")
        human = _nearest_human(head, by_uuid)
        span_id = human.get("uuid") if human else None
        if human and span_id not in spans:
            spans[span_id] = {"name": _label(human), "start": human.get("timestamp")}

        payload = {
            "provider": "anthropic",
            "api": "messages",
            "request": request,
            "response": {
                "id": message.get("id"),
                "type": "message",
                "role": "assistant",
                "model": message.get("model"),
                "content": blocks,
                "stop_reason": message.get("stop_reason"),
                "stop_sequence": message.get("stop_sequence"),
            },
            "import": _import_meta(path, request, usage, bool(head.get("isSidechain"))),
        }
        if usage:
            payload["usage"] = usage
        calls.append(
            {
                "schema_version": SCHEMA_VERSION,
                "event_type": "llm_call",
                "session_id": session_id,
                "span_id": span_id,
                "call_id": message.get("id") or head.get("uuid"),
                "timestamp": head.get("timestamp"),
                "payload": payload,
            }
        )

    def span_event(kind: str, span_id: str, name: str, timestamp) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "event_type": kind,
            "session_id": session_id,
            "span_id": span_id,
            "call_id": None,
            "timestamp": timestamp,
            "payload": {"name": name},
        }

    starts, ends = [], []
    for span_id, span in spans.items():
        in_span = [c["timestamp"] for c in calls if c["span_id"] == span_id and c["timestamp"]]
        if not in_span:
            continue  # a turn the agent never answered opens nothing
        starts.append(span_event("span_start", span_id, span["name"], span["start"]))
        ends.append(span_event("span_end", span_id, span["name"], max(in_span)))

    # stable sort: on equal timestamps a span_start still precedes its calls and
    # a span_end still follows them
    return sorted(starts + calls + ends, key=lambda e: e["timestamp"] or "")


def import_transcript(path: str | Path) -> list[dict]:
    records, _ = read_transcript(path)
    return to_events(records, path=path)


def transcript_cwd(path: Path) -> str | None:
    """The project directory a transcript was recorded in, read from the records
    themselves — authoritative, unlike reverse-engineering the encoded dir name."""
    try:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("cwd"):
                    return record["cwd"]
    except OSError:
        return None
    return None


def iter_transcripts(projects_dir: Path | None = None) -> list[Path]:
    """Every transcript on this machine, newest first."""
    root = projects_dir or PROJECTS_DIR
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)


def find_transcript(
    session: str | None = None,
    *,
    cwd: str | None = None,
    projects_dir: Path | None = None,
) -> Path | None:
    """Locate a transcript by session id, else the newest one for `cwd`.

    The filename is the session id; `cwd` is matched against what the records
    recorded, not against the encoded directory name.
    """
    transcripts = iter_transcripts(projects_dir)
    if session:
        return next((p for p in transcripts if p.stem == session), None)
    return next((p for p in transcripts if transcript_cwd(p) == cwd), None)
