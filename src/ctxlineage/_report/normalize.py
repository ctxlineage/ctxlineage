"""Normalize raw JSONL events into the report data contract (report_version 1).

The output of build_report_data() is the JSON the report frontend consumes —
treat its shape as an API within a milestone.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ctxlineage._report.matching import apply_tags
from ctxlineage._report.tokens import estimate_tokens

REPORT_VERSION = 1

# Prefix-matched, longest prefix wins. Deliberately small: an honest null
# ("unknown") beats a stale guess for models not listed here.
MODEL_CONTEXT_WINDOWS = {
    "gpt-4o": 128_000,
    "gpt-4.1": 1_047_576,
    "gpt-5": 400_000,
    "o3": 200_000,
    "o4": 200_000,
    "claude-": 200_000,
}


def load_events(path: str | os.PathLike) -> tuple[list[dict], int]:
    """Parse a JSONL file; returns (events, skipped_line_count)."""
    events: list[dict] = []
    skipped = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(event, dict) and event.get("event_type"):
            events.append(event)
        else:
            skipped += 1
    return events, skipped


def context_window_for(model: str | None) -> int | None:
    if not model:
        return None
    best = None
    best_len = -1
    for prefix, window in MODEL_CONTEXT_WINDOWS.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = window, len(prefix)
    return best


def _part_text(part: dict) -> str:
    """Text for one content block. anthropic tool_use / tool_result blocks carry
    no `text` field; surface them honestly instead of dropping them silently."""
    ptype = part.get("type")
    if ptype == "tool_use":
        name = part.get("name", "tool")
        try:
            args = json.dumps(part.get("input", {}), ensure_ascii=False)
        except (TypeError, ValueError):
            args = str(part.get("input", {}))
        return f"[tool_use: {name}({args})]"
    if ptype == "tool_result":
        # content is a string or a nested block list (recurse to flatten it)
        return _content_to_text(part.get("content"))
    return part.get("text") or part.get("input_text") or ""


def _content_to_text(content) -> str:
    """Flatten message content (string or content-parts list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = _part_text(part)
                if text:
                    parts.append(text)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _block_types(content) -> set:
    """The set of content-block `type`s in a message (empty for string content)."""
    if isinstance(content, list):
        return {p.get("type") for p in content if isinstance(p, dict)}
    return set()


def _chat_segments(request: dict) -> list[dict]:
    segments = []
    # anthropic carries the system prompt as a top-level kwarg (str or blocks),
    # not as a message — without this it would be invisible in the report.
    system = request.get("system")
    if system:
        segments.append({"role": "system", "kind": "system", "content": _content_to_text(system)})
    for message in request.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "user")
        content = message.get("content")
        segment = {"role": role, "kind": role, "content": _content_to_text(content)}
        if role == "tool" and message.get("name"):
            segment["name"] = message["name"]
        # anthropic feeds tool output back as a user-role message of tool_result
        # blocks — render it as a tool segment (unless the turn also has real
        # user text, in which case it stays user input).
        types = _block_types(content)
        if role == "user" and "tool_result" in types and "text" not in types:
            segment["kind"] = "tool"
        segments.append(segment)
    return segments


def _responses_segments(request: dict) -> list[dict]:
    segments = []
    instructions = request.get("instructions")
    if instructions:
        text = _content_to_text(instructions)
        segments.append({"role": "system", "kind": "system", "content": text})
    input_value = request.get("input")
    if isinstance(input_value, str):
        segments.append({"role": "user", "kind": "user", "content": input_value})
    elif isinstance(input_value, list):
        for item in input_value:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "user")
            text = _content_to_text(item.get("content"))
            segments.append({"role": role, "kind": role, "content": text})
    return segments


def _chat_output(response) -> dict | None:
    if not isinstance(response, dict):
        return None
    if response.get("object") == "chat.completion.assembled":
        return {
            "content": response.get("content", {}).get("0", ""),
            "finish_reason": response.get("finish_reasons", {}).get("0"),
        }
    if response.get("object") == "message.assembled":
        # anthropic streamed assembly: content is {index: text} (text_delta can
        # land at index >= 1), stop_reason is the finish signal.
        content = response.get("content")
        text = ""
        if isinstance(content, dict):
            text = "\n".join(v for _, v in sorted(content.items()) if v)
        return {"content": text, "finish_reason": response.get("stop_reason")}
    if isinstance(response.get("content"), list):
        # anthropic non-stream Messages: top-level content-block list + stop_reason
        return {
            "content": _content_to_text(response["content"]),
            "finish_reason": response.get("stop_reason"),
        }
    choices = response.get("choices") or []
    if not choices:
        return None
    first = choices[0]
    message = first.get("message") or {}
    return {
        "content": _content_to_text(message.get("content")),
        "finish_reason": first.get("finish_reason"),
    }


def _responses_output(response) -> dict | None:
    if not isinstance(response, dict):
        return None
    if response.get("object") == "response.assembled":
        return {"content": response.get("output_text", ""), "finish_reason": None}
    texts = []
    for item in response.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            texts.append(_content_to_text(item.get("content")))
    if not texts:
        return None
    return {"content": "\n".join(texts), "finish_reason": None}


def _canonical_usage(usage):
    """Provider-agnostic usage: anthropic's input_/output_tokens gain the
    prompt_/completion_ vocabulary the report reads; original keys pass through."""
    if not isinstance(usage, dict):
        return usage
    if "prompt_tokens" in usage or "input_tokens" not in usage:
        return usage
    prompt = usage.get("input_tokens") or 0
    # anthropic reports cached prompt tokens separately from input_tokens; fold
    # them back so prompt/window figures reflect the real context size.
    prompt += usage.get("cache_read_input_tokens") or 0
    prompt += usage.get("cache_creation_input_tokens") or 0
    completion = usage.get("output_tokens") or 0
    return {
        **usage,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": usage.get("total_tokens", prompt + completion),
    }


def _normalize_call(event: dict, span_names=None, span_tags=None) -> tuple[dict, set]:
    payload = event.get("payload") or {}
    api = payload.get("api", "")
    request = payload.get("request") or {}
    model = request.get("model")
    if api == "responses":
        segments = _responses_segments(request)
        output = None if "error" in payload else _responses_output(payload.get("response"))
    else:
        segments = _chat_segments(request)
        output = None if "error" in payload else _chat_output(payload.get("response"))
    span_id = event.get("span_id")
    tags = (span_tags or {}).get(span_id, [])
    matched: set = set()
    if tags:
        segments, matched = apply_tags(segments, tags)
    tools = request.get("tools")
    if tools:
        # Tool/function definitions are serialized into the prompt and consume
        # window tokens — surface them as their own segment so they are not invisible.
        segments.append(
            {
                "role": "tool_defs",
                "kind": "tool_defs",
                "content": json.dumps(tools, ensure_ascii=False, indent=1),
            }
        )
    for index, segment in enumerate(segments):
        segment["index"] = index
        segment["tokens_est"] = estimate_tokens(segment["content"], model or "")
    call = {
        "id": event.get("call_id"),
        "span_id": span_id,
        "step": (span_names or {}).get(span_id),
        "timestamp": event.get("timestamp"),
        "provider": payload.get("provider"),
        "api": api,
        "model": model,
        "stream": bool(payload.get("stream")),
        "duration_ms": payload.get("duration_ms"),
        "error": payload.get("error"),
        "context_window": context_window_for(model),
        "usage": _canonical_usage(payload.get("usage")),
        "segments": segments,
        "input_tokens_est": sum(s["tokens_est"] for s in segments),
        "tagged_tokens_est": sum(s["tokens_est"] for s in segments if s.get("tagged")),
        "output": output,
        "call_stack": payload.get("call_stack") or [],
    }
    return call, matched


_MIN_EDGE_TEXT = 15  # shorter outputs match everywhere; not evidence of flow
_MAX_EDGE_LOOKAHEAD = 500  # calls scanned ahead of each source
_MAX_EDGES_PER_SOURCE = 32  # fan-out cap per source call


def _session_edges(calls: list[dict]) -> tuple[list[dict], bool]:
    """PLAN 4(b) inference: output->later-input text match + same-span chains.

    Matches against each call's JOINED input text (tag splitting must not hide
    a flow), with honest caps for pathological sessions (accumulating chats
    make every output a substring of every later input). A pair can carry both
    an output_text and a same_span edge — consumers dedupe by (from, to) when
    counting. Returns (edges, truncated).
    """
    edges: list[dict] = []
    truncated = False
    haystacks = ["".join(seg["content"] for seg in c["segments"]) for c in calls]
    for i, call in enumerate(calls):
        output = ((call.get("output") or {}).get("content")) or ""
        if len(output) < _MIN_EDGE_TEXT or not call.get("id"):
            continue
        if len(calls) - (i + 1) > _MAX_EDGE_LOOKAHEAD:
            truncated = True
        hits = 0
        for j in range(i + 1, min(len(calls), i + 1 + _MAX_EDGE_LOOKAHEAD)):
            later = calls[j]
            if later.get("id") and later["id"] != call["id"] and output in haystacks[j]:
                edges.append({"from": call["id"], "to": later["id"], "kind": "output_text"})
                hits += 1
                if hits >= _MAX_EDGES_PER_SOURCE:
                    truncated = True
                    break
    # same-span chains link consecutive calls OF THE SAME SPAN, surviving
    # interleaved spans in one session
    by_span: dict = {}
    for call in calls:
        if call.get("span_id") and call.get("id"):
            by_span.setdefault(call["span_id"], []).append(call["id"])
    for ids in by_span.values():
        for a, b in zip(ids, ids[1:], strict=False):
            if a != b:
                edges.append({"from": a, "to": b, "kind": "same_span"})
    return edges, truncated


def _element_tokens(calls: list[dict], sid: str, name: str) -> int:
    return sum(
        seg["tokens_est"]
        for call in calls
        if call.get("span_id") == sid
        for seg in call["segments"]
        if seg.get("tagged") and seg["kind"] == name
    )


def _distinct(values) -> list:
    """Non-null values, de-duplicated, in first-seen order."""
    seen: list = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


def _build_element(sid, name, payloads, span_names, matched_tags, calls, consumers) -> dict:
    """One report element per (span, tag name), aggregating every occurrence so
    no provenance is silently dropped when a name is tagged more than once (#44).
    `source`/`transform` stay singular (first non-null) for back-compat; the
    full de-duplicated lists live in `sources`/`transforms` and the count in
    `occurrences`."""
    sources = _distinct(p.get("source") for p in payloads)
    transforms = _distinct(p.get("transform") for p in payloads)
    return {
        "name": name,
        "span_id": sid,
        "span_name": span_names.get(sid),
        "source": sources[0] if sources else None,
        "transform": transforms[0] if transforms else None,
        "sources": sources,
        "transforms": transforms,
        "occurrences": len(payloads),
        "matched": (sid, name) in matched_tags,
        "tokens_est": _element_tokens(calls, sid, name),
        "calls": consumers.get((sid, name), []),
    }


def build_report_data(events: list[dict]) -> dict:
    span_names: dict = {}
    span_tags: dict = {}
    tag_meta: dict = {}
    for event in events:
        kind = event.get("event_type")
        span_id = event.get("span_id")
        payload = event.get("payload") or {}
        if kind == "span_start" and span_id:
            span_names[span_id] = payload.get("name")
        elif kind == "tag" and span_id:
            span_tags.setdefault(span_id, []).append(payload)
            # accumulate every occurrence: same-name tags in one span (e.g. a
            # per-tool-call `tool_result` in an agent loop) keep all their
            # provenance instead of collapsing last-write-wins (#44)
            entry = tag_meta.setdefault(
                (span_id, payload.get("name")),
                {"session": event.get("session_id", "unknown"), "payloads": []},
            )
            entry["payloads"].append(payload)

    sessions: dict[str, list[dict]] = {}
    errors = 0
    matched_tags: set = set()
    consumers: dict = {}
    for event in events:
        if event.get("event_type") != "llm_call":
            continue
        call, matched = _normalize_call(event, span_names, span_tags)
        matched_tags.update((event.get("span_id"), name) for name in matched)
        for name in matched:
            consumers.setdefault((event.get("span_id"), name), []).append(event.get("call_id"))
        if call["error"]:
            errors += 1
        sessions.setdefault(event.get("session_id", "unknown"), []).append(call)

    all_tags = {(sid, t.get("name")) for sid, tags in span_tags.items() for t in tags}

    session_list = []
    for session_id, calls in sessions.items():
        calls.sort(key=lambda c: c["timestamp"] or "")
        edges, truncated = _session_edges(calls)

        elements = [
            _build_element(sid, name, meta["payloads"], span_names, matched_tags, calls, consumers)
            for (sid, name), meta in tag_meta.items()
            if meta["session"] == session_id
        ]
        session_list.append(
            {
                "id": session_id,
                "started_at": calls[0]["timestamp"],
                "ended_at": calls[-1]["timestamp"],
                "calls": calls,
                "edges": edges,
                "edges_truncated": truncated,
                "elements": elements,
            }
        )
    session_list.sort(key=lambda s: s["started_at"] or "")

    tags_total = len(all_tags)
    tags_matched = len(matched_tags & all_tags)
    return {
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": {
            "sessions": len(session_list),
            "calls": sum(len(s["calls"]) for s in session_list),
            "errors": errors,
            "tags": {
                "total": tags_total,
                "matched": tags_matched,
                "match_rate": round(tags_matched / tags_total, 4) if tags_total else None,
            },
        },
        "sessions": session_list,
    }
