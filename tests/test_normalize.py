import json
import subprocess
import sys
from pathlib import Path

import pytest

from ctxlineage._report import normalize, tokens


@pytest.fixture(autouse=True)
def offline_tokens(monkeypatch):
    monkeypatch.setattr(tokens, "_encoding_for", lambda model: None)


def _event(payload, session="s1", call="c1", ts="2026-06-12T09:00:00+00:00"):
    return {
        "schema_version": 1,
        "event_type": "llm_call",
        "session_id": session,
        "span_id": None,
        "call_id": call,
        "timestamp": ts,
        "payload": payload,
    }


def _chat_payload(**overrides):
    payload = {
        "provider": "openai",
        "api": "chat.completions",
        "request": {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi there, what is up?"},
            ],
        },
        "stream": False,
        "duration_ms": 100.0,
        "call_stack": ["app.py:main:1"],
        "response": {
            "id": "x",
            "object": "chat.completion",
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Not much!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        },
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
    }
    payload.update(overrides)
    return payload


def test_groups_and_sorts_sessions():
    events = [
        _event(_chat_payload(), session="s2", call="c3", ts="2026-06-12T10:00:00+00:00"),
        _event(_chat_payload(), session="s1", call="c1", ts="2026-06-12T09:00:00+00:00"),
        _event(_chat_payload(), session="s1", call="c2", ts="2026-06-12T09:05:00+00:00"),
    ]
    data = normalize.build_report_data(events)
    assert data["report_version"] == 1
    assert data["stats"] == {
        "sessions": 2,
        "calls": 3,
        "errors": 0,
        "tags": {"total": 0, "matched": 0, "match_rate": None},
    }
    assert [s["id"] for s in data["sessions"]] == ["s1", "s2"]
    s1 = data["sessions"][0]
    assert [c["id"] for c in s1["calls"]] == ["c1", "c2"]
    assert s1["started_at"] == "2026-06-12T09:00:00+00:00"
    assert s1["ended_at"] == "2026-06-12T09:05:00+00:00"


def test_chat_segments_and_output():
    data = normalize.build_report_data([_event(_chat_payload())])
    call = data["sessions"][0]["calls"][0]
    assert [s["kind"] for s in call["segments"]] == ["system", "user"]
    assert call["segments"][0]["content"] == "You are helpful."
    assert all(s["tokens_est"] >= 1 for s in call["segments"])
    assert call["input_tokens_est"] == sum(s["tokens_est"] for s in call["segments"])
    assert call["output"] == {"content": "Not much!", "finish_reason": "stop"}
    assert call["usage"]["total_tokens"] == 25
    assert call["context_window"] == 128000
    assert call["model"] == "gpt-4o-mini"


def test_content_parts_messages():
    payload = _chat_payload()
    payload["request"]["messages"] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Part one."},
                {"type": "text", "text": "Part two."},
            ],
        }
    ]
    data = normalize.build_report_data([_event(payload)])
    (segment,) = data["sessions"][0]["calls"][0]["segments"]
    assert "Part one." in segment["content"] and "Part two." in segment["content"]


def test_streamed_assembled_output():
    payload = _chat_payload(
        stream=True,
        response={
            "object": "chat.completion.assembled",
            "id": "y",
            "model": "gpt-4o-mini",
            "content": {"0": "Streamed answer"},
            "finish_reasons": {"0": "stop"},
            "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
            "chunk_count": 4,
        },
        usage={"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
    )
    data = normalize.build_report_data([_event(payload)])
    call = data["sessions"][0]["calls"][0]
    assert call["stream"] is True
    assert call["output"] == {"content": "Streamed answer", "finish_reason": "stop"}


def test_error_call():
    payload = _chat_payload()
    del payload["response"]
    del payload["usage"]
    payload["error"] = {"type": "RateLimitError", "message": "slow down"}
    data = normalize.build_report_data([_event(payload)])
    call = data["sessions"][0]["calls"][0]
    assert call["error"]["type"] == "RateLimitError"
    assert call["output"] is None
    assert call["usage"] is None
    assert data["stats"]["errors"] == 1


def test_responses_api_segments():
    payload = {
        "provider": "openai",
        "api": "responses",
        "request": {
            "model": "gpt-4o-mini",
            "instructions": "Be terse.",
            "input": "What is ctxlineage?",
        },
        "stream": False,
        "duration_ms": 50.0,
        "call_stack": [],
        "response": {
            "id": "resp1",
            "object": "response",
            "model": "gpt-4o-mini",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "A context lineage tool."}],
                }
            ],
            "usage": {"input_tokens": 12, "output_tokens": 6, "total_tokens": 18},
        },
        "usage": {"input_tokens": 12, "output_tokens": 6, "total_tokens": 18},
    }
    data = normalize.build_report_data([_event(payload)])
    call = data["sessions"][0]["calls"][0]
    assert [s["kind"] for s in call["segments"]] == ["system", "user"]
    assert call["segments"][0]["content"] == "Be terse."
    assert call["output"]["content"] == "A context lineage tool."


def test_tool_message_name_propagated():
    payload = _chat_payload()
    payload["request"]["messages"].append(
        {"role": "tool", "name": "search_docs", "tool_call_id": "tc1", "content": "result text"}
    )
    data = normalize.build_report_data([_event(payload)])
    tool_seg = data["sessions"][0]["calls"][0]["segments"][-1]
    assert tool_seg["kind"] == "tool"
    assert tool_seg["name"] == "search_docs"


def test_tool_definitions_become_segment():
    payload = _chat_payload()
    payload["request"]["tools"] = [
        {"type": "function", "function": {"name": "search_docs", "parameters": {}}}
    ]
    data = normalize.build_report_data([_event(payload)])
    segments = data["sessions"][0]["calls"][0]["segments"]
    assert segments[-1]["kind"] == "tool_defs"
    assert segments[-1]["tokens_est"] >= 1
    assert "search_docs" in segments[-1]["content"]


def test_unknown_model_context_window_is_null():
    payload = _chat_payload()
    payload["request"]["model"] = "mystery-model"
    payload["response"]["model"] = "mystery-model"
    data = normalize.build_report_data([_event(payload)])
    assert data["sessions"][0]["calls"][0]["context_window"] is None


def test_load_events_skips_malformed_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    good = _event(_chat_payload())
    path.write_text(json.dumps(good) + "\nnot json at all\n" + json.dumps(good) + "\n")
    events, skipped = normalize.load_events(path)
    assert len(events) == 2
    assert skipped == 1


def test_end_to_end_with_demo_data(tmp_path):
    script = Path(__file__).parent.parent / "examples" / "generate_demo_events.py"
    subprocess.run([sys.executable, str(script), str(tmp_path)], check=True, timeout=60)
    events, skipped = normalize.load_events(tmp_path / "events.jsonl")
    data = normalize.build_report_data(events)
    assert skipped == 0
    assert data["stats"]["sessions"] == 4
    assert data["stats"]["errors"] == 1
    rag = next(s for s in data["sessions"] if s["id"] == "demo-session-rag")
    assert len(rag["calls"]) == 6
    last_answer_call = rag["calls"][-1]
    kinds = [s["kind"] for s in last_answer_call["segments"]]
    assert kinds[0] == "system"
    assert "assistant" in kinds  # history accumulated


def _span_events(tag_content, message, span_id="sp1"):
    return [
        {
            "schema_version": 1,
            "event_type": "span_start",
            "session_id": "s1",
            "span_id": span_id,
            "call_id": None,
            "timestamp": "2026-07-16T09:00:00+00:00",
            "payload": {"name": "answer_query"},
        },
        {
            "schema_version": 1,
            "event_type": "tag",
            "session_id": "s1",
            "span_id": span_id,
            "call_id": None,
            "timestamp": "2026-07-16T09:00:01+00:00",
            "payload": {"name": "rag_chunks", "content": tag_content, "source": "qdrant:x"},
        },
        {
            "schema_version": 1,
            "event_type": "llm_call",
            "session_id": "s1",
            "span_id": span_id,
            "call_id": "c1",
            "timestamp": "2026-07-16T09:00:02+00:00",
            "payload": {
                "provider": "openai",
                "api": "chat.completions",
                "request": {
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": message}],
                },
                "stream": False,
                "duration_ms": 10.0,
                "call_stack": ["app.py:main:1"],
            },
        },
    ]


def test_tagged_call_gets_named_segments_and_step():
    events = _span_events("THE CHUNK TEXT", "Context:\nTHE CHUNK TEXT\nQ: hi")
    data = normalize.build_report_data(events)
    call = data["sessions"][0]["calls"][0]
    assert call["step"] == "answer_query"
    kinds = [(s["kind"], s.get("tagged")) for s in call["segments"]]
    assert ("rag_chunks", True) in kinds
    tagged = next(s for s in call["segments"] if s.get("tagged"))
    assert tagged["source"] == "qdrant:x"
    assert call["tagged_tokens_est"] > 0
    assert data["stats"]["tags"] == {"total": 1, "matched": 1, "match_rate": 1.0}


def test_unmatched_tag_lowers_match_rate():
    events = _span_events("text that appears nowhere at all", "Q: hi there friend")
    data = normalize.build_report_data(events)
    call = data["sessions"][0]["calls"][0]
    assert all(not s.get("tagged") for s in call["segments"])
    assert data["stats"]["tags"]["match_rate"] == 0.0


def _call_event(call_id, ts, messages, answer, session="s1", span_id=None):
    return {
        "schema_version": 1,
        "event_type": "llm_call",
        "session_id": session,
        "span_id": span_id,
        "call_id": call_id,
        "timestamp": ts,
        "payload": {
            "provider": "openai",
            "api": "chat.completions",
            "request": {"model": "gpt-4o-mini", "messages": messages},
            "stream": False,
            "duration_ms": 5.0,
            "call_stack": [],
            "response": {
                "object": "chat.completion",
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
            },
        },
    }


def test_output_text_edge_inferred():
    answer = "The webhook secret rotates every 90 days."
    events = [
        _call_event(
            "c1",
            "2026-07-16T09:00:00+00:00",
            [{"role": "user", "content": "How often does it rotate?"}],
            answer,
        ),
        _call_event(
            "c2",
            "2026-07-16T09:01:00+00:00",
            [{"role": "user", "content": "Earlier you said: " + answer + " Why?"}],
            "Because of the security policy.",
        ),
    ]
    data = normalize.build_report_data(events)
    edges = data["sessions"][0]["edges"]
    assert {"from": "c1", "to": "c2", "kind": "output_text"} in edges


def test_short_output_produces_no_edge():
    events = [
        _call_event("c1", "2026-07-16T09:00:00+00:00", [{"role": "user", "content": "hi"}], "yes"),
        _call_event(
            "c2",
            "2026-07-16T09:01:00+00:00",
            [{"role": "user", "content": "you said yes before"}],
            "indeed I did friend",
        ),
    ]
    data = normalize.build_report_data(events)
    assert data["sessions"][0]["edges"] == []


def test_same_span_edge_between_consecutive_calls():
    events = [
        _call_event(
            "c1",
            "2026-07-16T09:00:00+00:00",
            [{"role": "user", "content": "step one"}],
            "short",
            span_id="sp9",
        ),
        _call_event(
            "c2",
            "2026-07-16T09:01:00+00:00",
            [{"role": "user", "content": "step two"}],
            "short",
            span_id="sp9",
        ),
    ]
    data = normalize.build_report_data(events)
    assert {"from": "c1", "to": "c2", "kind": "same_span"} in data["sessions"][0]["edges"]


def test_elements_list_carries_provenance_and_consumers():
    events = _span_events("THE CHUNK TEXT", "Context:\nTHE CHUNK TEXT\nQ: hi")
    data = normalize.build_report_data(events)
    (element,) = data["sessions"][0]["elements"]
    assert element["name"] == "rag_chunks"
    assert element["source"] == "qdrant:x"
    assert element["span_name"] == "answer_query"
    assert element["matched"] is True
    assert element["calls"] == ["c1"]


def test_edge_survives_tag_split_in_consumer():
    answer = "The webhook secret rotates every 90 days."
    consumer = _call_event(
        "c2",
        "2026-07-16T09:01:00+00:00",
        [{"role": "user", "content": "Earlier: " + answer + " Why?"}],
        "Because policy.",
        span_id="sp1",
    )
    events = _span_events("webhook secret", "unused") + [
        _call_event(
            "c1", "2026-07-16T09:00:30+00:00", [{"role": "user", "content": "How often?"}], answer
        ),
    ]
    # the tag splits c2's message right through the echoed answer
    events[2] = consumer  # replace the span-events template call with the consumer
    data = normalize.build_report_data(events)
    edges = data["sessions"][0]["edges"]
    assert {"from": "c1", "to": "c2", "kind": "output_text"} in edges


def test_edge_fanout_cap_sets_truncated_flag():
    answer = "This exact sentence gets echoed everywhere downstream."
    events = [
        _call_event("src", "2026-07-16T08:00:00+00:00", [{"role": "user", "content": "go"}], answer)
    ] + [
        _call_event(
            f"t{i:03d}",
            f"2026-07-16T09:{i // 60:02d}:{i % 60:02d}+00:00",
            [{"role": "user", "content": "ref: " + answer}],
            "ok fine done today",
        )
        for i in range(40)
    ]
    data = normalize.build_report_data(events)
    session = data["sessions"][0]
    out_edges = [e for e in session["edges"] if e["kind"] == "output_text" and e["from"] == "src"]
    assert len(out_edges) == 32  # capped
    assert session["edges_truncated"] is True


def test_same_span_chain_survives_interleaving():
    events = [
        _call_event(
            "a1",
            "2026-07-16T09:00:00+00:00",
            [{"role": "user", "content": "one"}],
            "short",
            span_id="spA",
        ),
        _call_event(
            "b1",
            "2026-07-16T09:01:00+00:00",
            [{"role": "user", "content": "two"}],
            "short",
            span_id="spB",
        ),
        _call_event(
            "a2",
            "2026-07-16T09:02:00+00:00",
            [{"role": "user", "content": "three"}],
            "short",
            span_id="spA",
        ),
    ]
    data = normalize.build_report_data(events)
    edges = data["sessions"][0]["edges"]
    assert {"from": "a1", "to": "a2", "kind": "same_span"} in edges


def test_retagging_updates_element_provenance():
    events = _span_events("THE CHUNK TEXT", "Context:\nTHE CHUNK TEXT\nQ: hi")
    retag = dict(events[1])
    retag["payload"] = {"name": "rag_chunks", "content": "THE CHUNK TEXT", "source": "qdrant:y"}
    retag["timestamp"] = "2026-07-16T09:00:01.500000+00:00"
    events.insert(2, retag)
    data = normalize.build_report_data(events)
    (element,) = data["sessions"][0]["elements"]
    assert element["source"] == "qdrant:y"  # last write wins


def test_element_token_aggregation():
    events = _span_events("THE CHUNK TEXT", "Context:\nTHE CHUNK TEXT\nQ: hi")
    data = normalize.build_report_data(events)
    (element,) = data["sessions"][0]["elements"]
    call = data["sessions"][0]["calls"][0]
    tagged_tok = sum(s["tokens_est"] for s in call["segments"] if s.get("tagged"))
    assert element["tokens_est"] == tagged_tok > 0


def test_unmatched_element_has_zero_tokens():
    events = _span_events("text that appears nowhere at all", "Q: hi there friend")
    data = normalize.build_report_data(events)
    (element,) = data["sessions"][0]["elements"]
    assert element["tokens_est"] == 0


def test_anthropic_usage_vocabulary_canonicalized():
    event = _call_event(
        "c1",
        "2026-07-16T09:00:00+00:00",
        [{"role": "user", "content": "hello there my friend"}],
        "hi from claude model",
    )
    event["payload"]["provider"] = "anthropic"
    event["payload"]["api"] = "messages"
    event["payload"]["usage"] = {"input_tokens": 12, "output_tokens": 5}
    data = normalize.build_report_data([event])
    usage = data["sessions"][0]["calls"][0]["usage"]
    assert usage["prompt_tokens"] == 12
    assert usage["completion_tokens"] == 5
    assert usage["total_tokens"] == 17
    assert usage["input_tokens"] == 12  # original keys pass through


def test_openai_usage_untouched():
    event = _call_event(
        "c1",
        "2026-07-16T09:00:00+00:00",
        [{"role": "user", "content": "hello there my friend"}],
        "hi from the assistant",
    )
    event["payload"]["usage"] = {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
    usage = normalize.build_report_data([event])["sessions"][0]["calls"][0]["usage"]
    assert usage == {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}


def test_both_usage_vocabularies_present_no_double_count():
    event = _call_event(
        "c1",
        "2026-07-16T09:00:00+00:00",
        [{"role": "user", "content": "hello there my friend"}],
        "hi from claude model",
    )
    event["payload"]["usage"] = {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
        "input_tokens": 12,
        "output_tokens": 5,
    }
    usage = normalize.build_report_data([event])["sessions"][0]["calls"][0]["usage"]
    assert usage == event["payload"]["usage"]  # openai vocabulary wins, nothing recomputed
