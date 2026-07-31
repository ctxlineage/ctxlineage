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


@pytest.mark.parametrize(
    "model, expected",
    [
        # Homogeneous frozen families whose absence made window_budget skip.
        ("gpt-4-turbo-2024-04-09", 128_000),
        ("gpt-4-turbo-preview", 128_000),
        ("gpt-4-32k", 32_768),
        ("gpt-4-32k-0613", 32_768),
        ("o1-2024-12-17", 200_000),
        ("o1-pro", 200_000),
        ("o1-mini", 128_000),
        ("o1-mini-2024-09-12", 128_000),
        ("o1-preview", 128_000),
        # A more specific prefix must win over a shorter one, both directions.
        ("gpt-4o-mini", 128_000),  # gpt-4o, not o1/gpt-4-*
        ("gpt-4.1-mini", 1_047_576),  # gpt-4.1
    ],
)
def test_known_model_context_windows(model, expected):
    assert normalize.context_window_for(model) == expected


@pytest.mark.parametrize(
    "model",
    [
        # Mixed-window families: a bare gpt-4 / gpt-3.5-turbo prefix would
        # confidently mis-size half of these (e.g. gpt-4-1106-preview is really
        # 128k, gpt-3.5-turbo-instruct really 4k), so we skip them honestly
        # rather than return a wrong window that a CI budget gate would trust.
        "gpt-4-0613",
        "gpt-4-1106-preview",
        "gpt-4-0125-preview",
        "gpt-4-vision-preview",
        "gpt-3.5-turbo-0125",
        "gpt-3.5-turbo-instruct",
        "mystery-model",
    ],
)
def test_mixed_or_unknown_models_stay_unknown(model):
    assert normalize.context_window_for(model) is None


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


def test_action_reads_the_payload_declaration():
    """#88: the importer declares a per-call action; normalize.py's job here
    is only to expose it - the report frontend picks the label preference."""
    payload = _chat_payload(action="Read")
    data = normalize.build_report_data([_event(payload)])
    assert data["sessions"][0]["calls"][0]["action"] == "Read"


def test_action_absent_for_live_capture():
    """Live capture never sets payload['action'] (only the importer does) - the
    call carries None, not a missing key, so frontend fallback logic can rely
    on a stable shape."""
    data = normalize.build_report_data([_event(_chat_payload())])
    assert data["sessions"][0]["calls"][0]["action"] is None


def test_action_does_not_change_the_span_derived_step():
    """`step` (span_names-derived) and `action` (payload-derived) are
    independent fields - #88 split them precisely so one wouldn't shadow the
    other; a call declaring an action must still carry its real span step."""
    events = _span_events("THE CHUNK TEXT", "Context:\nTHE CHUNK TEXT\nQ: hi")
    events[-1]["payload"]["action"] = "Read"
    data = normalize.build_report_data(events)
    call = data["sessions"][0]["calls"][0]
    assert call["step"] == "answer_query"
    assert call["action"] == "Read"


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
    # to_segment (#93): c2's whole input is one user-role segment, and the
    # match lands inside it - index 0.
    assert {"from": "c1", "to": "c2", "kind": "output_text", "to_segment": 0} in edges


def test_to_segment_points_at_the_real_index_not_always_zero():
    """A system segment ahead of the matching one must not be mistaken for
    it - index 1, not a coincidental 0 (the case every other edge test
    happens to exercise)."""
    answer = "The webhook secret rotates every 90 days."
    events = [
        _call_event(
            "c1", "2026-07-16T09:00:00+00:00", [{"role": "user", "content": "rotate?"}], answer
        ),
        _call_event(
            "c2",
            "2026-07-16T09:01:00+00:00",
            [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "Earlier you said: " + answer + " Why?"},
            ],
            "Because of policy.",
        ),
    ]
    edges = normalize.build_report_data(events)["sessions"][0]["edges"]
    assert {"from": "c1", "to": "c2", "kind": "output_text", "to_segment": 1} in edges


def test_to_segment_identifies_a_tool_kind_destination():
    """#93's other bug (found alongside the missing label): Chain's arrow
    always pointed at the aggregated assistant/'fed' chip regardless of which
    kind the match actually landed in. to_segment is what lets the frontend
    target the real kind instead of assuming assistant."""
    answer = "The webhook secret rotates every 90 days."
    events = [
        _call_event(
            "c1", "2026-07-16T09:00:00+00:00", [{"role": "user", "content": "rotate?"}], answer
        ),
        _call_event(
            "c2",
            "2026-07-16T09:01:00+00:00",
            [
                {"role": "user", "content": "look this up"},
                {"role": "tool", "name": "search_docs", "content": answer},
            ],
            "Because of policy.",
        ),
    ]
    data = normalize.build_report_data(events)
    edges = data["sessions"][0]["edges"]
    edge = next(e for e in edges if e["kind"] == "output_text")
    c2 = data["sessions"][0]["calls"][1]
    assert c2["segments"][edge["to_segment"]]["kind"] == "tool"


def test_to_segment_absent_when_the_match_spans_a_segment_boundary():
    """The joined-haystack match test is unchanged (no regression to what
    counts as a flow) - only localization is best-effort. A match straddling
    two segments (the join has no separator) has no single segment to blame,
    so the edge is still recorded, just without to_segment."""
    events = [
        _call_event(
            "c1",
            "2026-07-16T09:00:00+00:00",
            [{"role": "user", "content": "rotate?"}],
            "The webhook secret rotates every 90 days.",
        ),
        _call_event(
            "c2",
            "2026-07-16T09:01:00+00:00",
            [
                {"role": "system", "content": "Earlier you said: The webhook se"},
                {"role": "user", "content": "cret rotates every 90 days. Why?"},
            ],
            "Because of policy.",
        ),
    ]
    edges = normalize.build_report_data(events)["sessions"][0]["edges"]
    edge = next(e for e in edges if e["kind"] == "output_text")
    assert "to_segment" not in edge


def test_to_segment_skips_a_boundary_artifact_for_a_later_clean_occurrence():
    """A regression an adversarial review caught: localizing only the FIRST
    haystack position is wrong when that position is a straddling artifact
    (the join has no separator) and a later segment independently, cleanly
    contains the real occurrence. Must keep looking rather than giving up
    at the first (spurious) hit."""
    answer = "ROTATE-EVERY-90-DAYS-PRECISELY"
    events = [
        _call_event(
            "c1", "2026-07-16T09:00:00+00:00", [{"role": "user", "content": "rotate?"}], answer
        ),
        _call_event(
            "c2",
            "2026-07-16T09:01:00+00:00",
            [
                # the join stitches these into "...ROTATE-EVERY-90-DAYS-PRECISELY..."
                # at the boundary - a straddling artifact, not a real occurrence.
                {"role": "system", "content": "preamble... ROTATE-EVERY-90-"},
                {"role": "assistant", "content": "DAYS-PRECISELY (unrelated aside)"},
                # the real, clean, single-segment occurrence - later in the haystack.
                {"role": "tool", "name": "log", "content": f"logged: {answer}"},
            ],
            "ok",
        ),
    ]
    edges = normalize.build_report_data(events)["sessions"][0]["edges"]
    edge = next(e for e in edges if e["kind"] == "output_text")
    assert edge["to_segment"] == 2


def test_to_segment_ties_off_deterministically_on_duplicated_text():
    """When the identical text genuinely appears in two segments, there is no
    way to attribute it to one copy over the other from text alone - the
    first non-straddling occurrence wins, a documented tie-break rather than
    an attempt at a semantically 'correct' answer that cannot exist."""
    boilerplate = "I will look into that and follow up with you shortly."
    events = [
        _call_event(
            "c1",
            "2026-07-16T09:00:00+00:00",
            [{"role": "user", "content": "hi"}],
            boilerplate,
        ),
        _call_event(
            "c2",
            "2026-07-16T09:01:00+00:00",
            [
                {"role": "assistant", "content": boilerplate},  # an unrelated earlier turn
                {"role": "user", "content": "do the thing"},
                {"role": "tool", "name": "log", "content": boilerplate},  # the real feed-back
            ],
            "ok",
        ),
    ]
    edges = normalize.build_report_data(events)["sessions"][0]["edges"]
    edge = next(e for e in edges if e["kind"] == "output_text")
    assert edge["to_segment"] == 0  # first occurrence, deterministic - not the "tool" segment


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


def test_same_name_tags_aggregate_all_provenance():
    # #44: two tags with the same name in one span must not collapse
    # last-write-wins — every occurrence's provenance is preserved.
    events = _span_events("THE CHUNK TEXT", "Context:\nTHE CHUNK TEXT\nQ: hi")
    retag = dict(events[1])
    retag["payload"] = {"name": "rag_chunks", "content": "THE CHUNK TEXT", "source": "qdrant:y"}
    retag["timestamp"] = "2026-07-16T09:00:01.500000+00:00"
    events.insert(2, retag)
    data = normalize.build_report_data(events)
    (element,) = data["sessions"][0]["elements"]
    assert element["occurrences"] == 2
    assert element["sources"] == ["qdrant:x", "qdrant:y"]  # nothing silently dropped
    assert element["source"] == "qdrant:x"  # singular = first non-null (back-compat)


def test_same_name_tags_distinct_sources_and_transforms():
    # a tool loop tagging each result `tool_result` with a different source
    events = _span_events("R1 R2", "used R1 R2 in the prompt")
    events[1]["payload"] = {"name": "tool_result", "content": "R1", "source": "tool:search"}
    second = dict(events[1])
    second["payload"] = {
        "name": "tool_result",
        "content": "R2",
        "source": "tool:fetch",
        "transform": "truncate",
    }
    second["timestamp"] = "2026-07-16T09:00:01.500000+00:00"
    events.insert(2, second)
    data = normalize.build_report_data(events)
    (element,) = data["sessions"][0]["elements"]
    assert element["occurrences"] == 2
    assert element["sources"] == ["tool:search", "tool:fetch"]
    assert element["transforms"] == ["truncate"]


def test_single_tag_element_shape_unchanged():
    events = _span_events("THE CHUNK TEXT", "Context:\nTHE CHUNK TEXT\nQ: hi")
    data = normalize.build_report_data(events)
    (element,) = data["sessions"][0]["elements"]
    assert element["occurrences"] == 1
    assert element["source"] == "qdrant:x"
    assert element["sources"] == ["qdrant:x"]


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


# --- anthropic Messages payloads (#30) -------------------------------------


def _anthropic_payload(request, response=None, usage=None, **extra):
    payload = {
        "provider": "anthropic",
        "api": "messages",
        "request": {"model": "claude-sonnet-5", **request},
        "stream": False,
        "duration_ms": 10.0,
        "call_stack": [],
    }
    if response is not None:
        payload["response"] = response
    if usage is not None:
        payload["usage"] = usage
    payload.update(extra)
    return payload


def test_anthropic_system_kwarg_becomes_segment():
    payload = _anthropic_payload(
        {"system": "You are Claude.", "messages": [{"role": "user", "content": "Hi"}]}
    )
    segs = normalize.build_report_data([_event(payload)])["sessions"][0]["calls"][0]["segments"]
    assert [s["kind"] for s in segs] == ["system", "user"]
    assert segs[0]["content"] == "You are Claude."


def test_anthropic_system_kwarg_as_content_blocks():
    payload = _anthropic_payload(
        {
            "system": [{"type": "text", "text": "Block system."}],
            "messages": [{"role": "user", "content": "Hi"}],
        }
    )
    segs = normalize.build_report_data([_event(payload)])["sessions"][0]["calls"][0]["segments"]
    assert segs[0]["kind"] == "system"
    assert "Block system." in segs[0]["content"]


def test_anthropic_tool_use_block_surfaced_not_dropped():
    # honest data: an assistant tool_use block carries no `text`; it must not
    # vanish from the recorded turn.
    payload = _anthropic_payload(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me check."},
                        {
                            "type": "tool_use",
                            "id": "tu1",
                            "name": "get_weather",
                            "input": {"city": "Paris"},
                        },
                    ],
                }
            ]
        }
    )
    (seg,) = normalize.build_report_data([_event(payload)])["sessions"][0]["calls"][0]["segments"]
    assert seg["kind"] == "assistant"
    assert "Let me check." in seg["content"]
    assert "get_weather" in seg["content"] and "Paris" in seg["content"]


def test_anthropic_tool_result_block_surfaced_as_tool_segment():
    # anthropic feeds tool output back as a user-role message of tool_result
    # blocks — surface it as a tool segment, not as user input.
    payload = _anthropic_payload(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu1", "content": "Sunny, 21C"}
                    ],
                }
            ]
        }
    )
    (seg,) = normalize.build_report_data([_event(payload)])["sessions"][0]["calls"][0]["segments"]
    assert seg["kind"] == "tool"
    assert "Sunny, 21C" in seg["content"]


def test_anthropic_tool_result_nested_content_blocks():
    payload = _anthropic_payload(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu1",
                            "content": [{"type": "text", "text": "Nested result."}],
                        }
                    ],
                }
            ]
        }
    )
    (seg,) = normalize.build_report_data([_event(payload)])["sessions"][0]["calls"][0]["segments"]
    assert "Nested result." in seg["content"]


def test_anthropic_user_text_with_tool_result_stays_user():
    # a message that also carries real user text is not reduced to a tool segment
    payload = _anthropic_payload(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "and also"},
                        {"type": "tool_result", "tool_use_id": "tu1", "content": "R"},
                    ],
                }
            ]
        }
    )
    (seg,) = normalize.build_report_data([_event(payload)])["sessions"][0]["calls"][0]["segments"]
    assert seg["kind"] == "user"
    assert "and also" in seg["content"] and "R" in seg["content"]


def test_anthropic_nonstream_output_content_blocks():
    payload = _anthropic_payload(
        {"messages": [{"role": "user", "content": "hi"}]},
        response={
            "id": "msg1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [{"type": "text", "text": "The answer is 42."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 9, "output_tokens": 5},
        },
        usage={"input_tokens": 9, "output_tokens": 5},
    )
    call = normalize.build_report_data([_event(payload)])["sessions"][0]["calls"][0]
    assert call["output"] == {"content": "The answer is 42.", "finish_reason": "end_turn"}


def test_anthropic_assembled_stream_output():
    payload = _anthropic_payload(
        {"messages": [{"role": "user", "content": "hi"}]},
        response={
            "object": "message.assembled",
            "id": "msg1",
            "model": "claude-sonnet-5",
            "content": {"0": "Streamed claude answer"},
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 9, "output_tokens": 3},
            "chunk_count": 4,
        },
        usage={"input_tokens": 9, "output_tokens": 3},
        stream=True,
    )
    call = normalize.build_report_data([_event(payload)])["sessions"][0]["calls"][0]
    assert call["stream"] is True
    assert call["output"] == {"content": "Streamed claude answer", "finish_reason": "end_turn"}


def test_anthropic_assembled_joins_multiple_text_indices():
    # text_delta can land at index >= 1 (a thinking block occupies index 0);
    # every text index must survive, in index order.
    payload = _anthropic_payload(
        {"messages": [{"role": "user", "content": "hi"}]},
        response={
            "object": "message.assembled",
            "id": "m",
            "model": "claude-sonnet-5",
            "content": {"1": "second part", "0": "first part"},
            "stop_reason": "end_turn",
            "usage": None,
            "chunk_count": 2,
        },
        stream=True,
    )
    call = normalize.build_report_data([_event(payload)])["sessions"][0]["calls"][0]
    assert call["output"]["content"] == "first part\nsecond part"


def test_anthropic_output_text_edge_inferred():
    answer = "The webhook secret rotates every 90 days precisely."
    e1 = _event(
        _anthropic_payload(
            {"messages": [{"role": "user", "content": "How often does it rotate?"}]},
            response={
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": answer}],
                "stop_reason": "end_turn",
            },
        ),
        call="c1",
        ts="2026-07-16T09:00:00+00:00",
    )
    e2 = _event(
        _anthropic_payload(
            {"messages": [{"role": "user", "content": "Earlier you said: " + answer + " Why?"}]},
            response={
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "Because of policy."}],
                "stop_reason": "end_turn",
            },
        ),
        call="c2",
        ts="2026-07-16T09:01:00+00:00",
    )
    edges = normalize.build_report_data([e1, e2])["sessions"][0]["edges"]
    assert {"from": "c1", "to": "c2", "kind": "output_text", "to_segment": 0} in edges


def test_anthropic_usage_folds_cache_tokens():
    # anthropic bills cached prompt tokens separately from input_tokens; the
    # report's prompt/window figures must reflect the real context size.
    event = _event(
        _anthropic_payload(
            {"messages": [{"role": "user", "content": "hi there my friend"}]},
            usage={
                "input_tokens": 12,
                "output_tokens": 5,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 30,
            },
        )
    )
    usage = normalize.build_report_data([event])["sessions"][0]["calls"][0]["usage"]
    assert usage["prompt_tokens"] == 142  # 12 + 100 + 30
    assert usage["completion_tokens"] == 5
    assert usage["total_tokens"] == 147
    assert usage["cache_read_input_tokens"] == 100  # originals pass through untouched


def test_anthropic_usage_fold_overrides_carried_total():
    # a middleware-carried total_tokens computed pre-fold would be smaller than
    # the folded prompt alone (>100% window figures) — the fold recomputes it.
    event = _event(
        _anthropic_payload(
            {"messages": [{"role": "user", "content": "hi there my friend"}]},
            usage={
                "input_tokens": 12,
                "output_tokens": 5,
                "cache_read_input_tokens": 100,
                "total_tokens": 17,
            },
        )
    )
    usage = normalize.build_report_data([event])["sessions"][0]["calls"][0]["usage"]
    assert usage["prompt_tokens"] == 112
    assert usage["total_tokens"] == 117  # recomputed, not the carried 17


def test_anthropic_thinking_blocks_visibly_marked():
    # thinking blocks carry no `text`; they must leave a visible marker, not
    # vanish (honest data — the tokens they consume need an explanation).
    payload = _anthropic_payload(
        {"messages": [{"role": "user", "content": "hi"}]},
        response={
            "id": "msg1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [
                {"type": "thinking", "thinking": "let me reason about this", "signature": "s"},
                {"type": "redacted_thinking", "data": "opaque"},
                {"type": "text", "text": "the final answer"},
            ],
            "stop_reason": "end_turn",
        },
    )
    output = normalize.build_report_data([_event(payload)])["sessions"][0]["calls"][0]["output"]
    assert "the final answer" in output["content"]
    assert "[thinking: 24 chars not shown]" in output["content"]
    assert "[redacted thinking]" in output["content"]


def test_empty_thinking_block_leaves_no_placeholder():
    # #90: an imported transcript strips reasoning text but keeps the block
    # shape (thinking=""). A per-occurrence "[thinking: 0 chars not shown]"
    # conveys nothing that isn't already counted once, elsewhere
    # (import.reasoning_blocks_stripped) - it should not print at all. A
    # block with real (but hidden-by-policy) content still gets its marker.
    payload = _anthropic_payload(
        {"messages": [{"role": "user", "content": "hi"}]},
        response={
            "id": "msg1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "text", "text": "the final answer"},
            ],
            "stop_reason": "end_turn",
        },
    )
    output = normalize.build_report_data([_event(payload)])["sessions"][0]["calls"][0]["output"]
    assert "the final answer" in output["content"]
    assert "chars not shown" not in output["content"]
    assert "thinking" not in output["content"]


def test_anthropic_assembled_indices_sort_numerically():
    # capture stringifies block indices; ten-plus blocks must not join in
    # lexicographic order ("10" before "2").
    payload = _anthropic_payload(
        {"messages": [{"role": "user", "content": "hi"}]},
        response={
            "object": "message.assembled",
            "id": "m",
            "model": "claude-sonnet-5",
            "content": {"10": "TENTH", "2": "SECOND"},
            "stop_reason": "end_turn",
            "usage": None,
            "chunk_count": 2,
        },
        stream=True,
    )
    call = normalize.build_report_data([_event(payload)])["sessions"][0]["calls"][0]
    assert call["output"]["content"] == "SECOND\nTENTH"


# --- #63/#64: does the pipeline know the segments are only part of the prompt? ---


def _imported_event(not_preserved, *, call_id="c1"):
    return {
        "schema_version": 1,
        "event_type": "llm_call",
        "session_id": "s1",
        "span_id": None,
        "call_id": call_id,
        "timestamp": "2026-07-17T00:00:00+00:00",
        "payload": {
            "provider": "anthropic",
            "api": "messages",
            "request": {
                "model": "claude-sonnet-5",
                "messages": [{"role": "user", "content": "hi"}],
            },
            "usage": {"input_tokens": 33_631},
            "import": {"source": "claude-code", "not_preserved": list(not_preserved)},
        },
    }


def test_live_capture_segments_are_complete():
    data = normalize.build_report_data(
        [
            {
                "schema_version": 1,
                "event_type": "llm_call",
                "session_id": "s1",
                "span_id": None,
                "call_id": "c1",
                "timestamp": "2026-07-17T00:00:00+00:00",
                "payload": {
                    "provider": "openai",
                    "api": "chat.completions",
                    "request": {
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                },
            }
        ]
    )
    call = data["sessions"][0]["calls"][0]
    assert call["segments_complete"] is True
    assert call["import"] is None


def test_import_missing_prompt_bearing_parts_is_incomplete():
    data = normalize.build_report_data(
        [_imported_event(("system_prompt", "tool_definitions", "reasoning_text"))]
    )
    call = data["sessions"][0]["calls"][0]
    assert call["segments_complete"] is False
    assert call["import"]["source"] == "claude-code"


def test_import_missing_only_metadata_is_still_complete():
    """The flag is about content that was in the window, not about being an
    import: duration/stream flags cost no tokens and hide no context."""
    data = normalize.build_report_data([_imported_event(("duration_ms", "stream_flag"))])
    assert data["sessions"][0]["calls"][0]["segments_complete"] is True
