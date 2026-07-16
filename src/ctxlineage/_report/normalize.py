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
                text = part.get("text") or part.get("input_text") or ""
                if text:
                    parts.append(text)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _chat_segments(request: dict) -> list[dict]:
    segments = []
    for message in request.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "user")
        text = _content_to_text(message.get("content"))
        segment = {"role": role, "kind": role, "content": text}
        if role == "tool" and message.get("name"):
            segment["name"] = message["name"]
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
        "usage": payload.get("usage"),
        "segments": segments,
        "input_tokens_est": sum(s["tokens_est"] for s in segments),
        "tagged_tokens_est": sum(s["tokens_est"] for s in segments if s.get("tagged")),
        "output": output,
        "call_stack": payload.get("call_stack") or [],
    }
    return call, matched


def build_report_data(events: list[dict]) -> dict:
    span_names: dict = {}
    span_tags: dict = {}
    for event in events:
        kind = event.get("event_type")
        span_id = event.get("span_id")
        payload = event.get("payload") or {}
        if kind == "span_start" and span_id:
            span_names[span_id] = payload.get("name")
        elif kind == "tag" and span_id:
            span_tags.setdefault(span_id, []).append(payload)

    sessions: dict[str, list[dict]] = {}
    errors = 0
    matched_tags: set = set()
    for event in events:
        if event.get("event_type") != "llm_call":
            continue
        call, matched = _normalize_call(event, span_names, span_tags)
        matched_tags.update((event.get("span_id"), name) for name in matched)
        if call["error"]:
            errors += 1
        sessions.setdefault(event.get("session_id", "unknown"), []).append(call)

    all_tags = {(sid, t.get("name")) for sid, tags in span_tags.items() for t in tags}

    session_list = []
    for session_id, calls in sessions.items():
        calls.sort(key=lambda c: c["timestamp"] or "")
        session_list.append(
            {
                "id": session_id,
                "started_at": calls[0]["timestamp"],
                "ended_at": calls[-1]["timestamp"],
                "calls": calls,
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
