"""Mapping tests for the Claude Code transcript importer (#57).

Fixtures are hand-written: real transcripts under ~/.claude/projects/ contain
prompt bodies and never enter the repo. The fixtures encode the transcript
facts that actually drive the mapping (see
docs/plans/2026-07-17-v02-claude-code-import.md), verified against real
sessions on 2026-07-17.
"""

from pathlib import Path

import pytest

from ctxlineage._import import claude_code
from ctxlineage._report import normalize

FIXTURES = Path(__file__).parent / "fixtures" / "claude_code"


def events_for(name):
    return claude_code.import_transcript(FIXTURES / name)


@pytest.fixture
def tool_loop_events():
    return events_for("session_tool_loop.jsonl")


def calls(events):
    return [e for e in events if e["event_type"] == "llm_call"]


# --- fan-out: one call per message.id, not per record -----------------------


def test_one_call_per_message_id_not_per_record(tool_loop_events):
    """3 assistant records share msg_001 -> ONE call. Per-record mapping would
    report 4 calls instead of 2 and multiply tokens by the fan-out factor."""
    assert len(calls(tool_loop_events)) == 2


def test_fanned_out_blocks_merge_in_record_order(tool_loop_events):
    first = calls(tool_loop_events)[0]
    blocks = first["payload"]["response"]["content"]
    assert [b["type"] for b in blocks] == ["thinking", "text", "tool_use"]


def test_duplicated_usage_counted_once_not_summed(tool_loop_events):
    """Every record of a fanned-out response repeats the SAME usage. Summing
    them would inflate the reported token count ~3x here."""
    first = calls(tool_loop_events)[0]
    assert first["payload"]["usage"]["output_tokens"] == 655


def test_call_ids_are_distinct_per_message(tool_loop_events):
    ids = [c["call_id"] for c in calls(tool_loop_events)]
    assert len(set(ids)) == len(ids)


# --- usage is real, and cache-aware ----------------------------------------


def test_usage_is_reconstructed_verbatim(tool_loop_events):
    usage = calls(tool_loop_events)[0]["payload"]["usage"]
    assert usage["input_tokens"] == 2
    assert usage["cache_read_input_tokens"] == 19078
    assert usage["cache_creation_input_tokens"] == 14551


def test_cached_prompt_tokens_fold_into_real_context_size(tool_loop_events):
    """Under Claude Code's caching input_tokens alone is ~2; the real prompt is
    33631. The existing _canonical_usage must fold cache reads/creations."""
    data = normalize.build_report_data(tool_loop_events)
    call = data["sessions"][0]["calls"][0]
    assert call["usage"]["prompt_tokens"] == 2 + 19078 + 14551


def test_usage_labelled_reconstructed(tool_loop_events):
    assert calls(tool_loop_events)[0]["payload"]["import"]["usage"] == "reconstructed"


def test_missing_usage_is_labelled_unavailable_not_invented():
    events = events_for("session_edge_cases.jsonl")
    no_usage = [
        c for c in calls(events) if c["payload"]["request"]["model"] and "usage" not in c["payload"]
    ]
    assert no_usage, "fixture must contain an assistant record without usage"
    assert no_usage[0]["payload"]["import"]["usage"] == "unavailable"


# --- request reconstruction via ancestry ------------------------------------


def test_request_excludes_the_calls_own_response(tool_loop_events):
    """The request is the ancestry of the group's FIRST record. Walking from the
    last record would feed the model's own blocks back as its own prompt."""
    first = calls(tool_loop_events)[0]
    messages = first["payload"]["request"]["messages"]
    assert [m["role"] for m in messages] == ["user"]
    assert messages[0]["content"] == "Fix the failing test in test_math.py"


def test_second_call_request_carries_collapsed_prior_turn(tool_loop_events):
    """The prior fanned-out response must appear as ONE assistant message with
    its 3 blocks, mirroring what was really sent."""
    second = calls(tool_loop_events)[1]
    messages = second["payload"]["request"]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert [b["type"] for b in messages[1]["content"]] == ["thinking", "text", "tool_use"]
    assert messages[2]["content"][0]["type"] == "tool_result"


def test_branch_uses_ancestry_not_file_order():
    """A rewind branches the tree. The abandoned branch is not part of the
    surviving call's request, even though it precedes it in the file."""
    events = events_for("session_branched.jsonl")
    surviving = calls(events)[-1]
    text = str(surviving["payload"]["request"]["messages"])
    assert "ABANDONED BRANCH" not in text
    assert "Gadget" in text


# --- honest data: the unpreserved remainder ---------------------------------


def test_no_synthetic_system_segment_is_invented(tool_loop_events):
    """The transcript has no system prompt. Inventing a placeholder would be
    fabricating data; absence must stay absent."""
    for call in calls(tool_loop_events):
        assert "system" not in call["payload"]["request"]
    data = normalize.build_report_data(tool_loop_events)
    kinds = {s["kind"] for c in data["sessions"][0]["calls"] for s in c["segments"]}
    assert "system" not in kinds


def test_unpreserved_fields_disclosed(tool_loop_events):
    disclosed = calls(tool_loop_events)[0]["payload"]["import"]["not_preserved"]
    assert "system_prompt" in disclosed
    assert "tool_definitions" in disclosed


def test_unaccounted_prompt_tokens_quantify_the_gap(tool_loop_events):
    """The gap between real prompt tokens and what we could reconstruct is
    measured, not hidden."""
    meta = calls(tool_loop_events)[0]["payload"]["import"]
    assert meta["prompt_tokens_reported"] == 33631
    gap = meta["prompt_tokens_reported"] - meta["prompt_tokens_reconstructed_est"]
    assert meta["unaccounted_prompt_tokens"] == gap
    assert meta["unaccounted_prompt_tokens"] > 0


def test_segment_tokens_labelled_estimated(tool_loop_events):
    assert calls(tool_loop_events)[0]["payload"]["import"]["segment_tokens"] == "estimated"


def test_stripped_reasoning_text_is_disclosed(tool_loop_events):
    """Claude Code writes thinking blocks with a signature but no text (887/887
    blocks across real sessions). The reasoning was really re-sent and really
    cost tokens (~923 each), so it belongs in the disclosure."""
    assert "reasoning_text" in calls(tool_loop_events)[0]["payload"]["import"]["not_preserved"]


def test_stripped_reasoning_blocks_counted_where_they_land(tool_loop_events):
    """Counted per call's own request: the first call's prompt predates any
    thinking; the second one carries the stripped block from the first."""
    first, second = calls(tool_loop_events)
    assert first["payload"]["import"]["reasoning_blocks_stripped"] == 0
    assert second["payload"]["import"]["reasoning_blocks_stripped"] == 1


def test_reasoning_with_text_is_not_counted_as_stripped():
    """Only blocks whose text is actually gone count. If a future Claude Code
    keeps the text, the disclosure must stop claiming it was dropped."""
    events = events_for("session_edge_cases.jsonl")
    with_text = [
        c
        for c in calls(events)
        if any(
            isinstance(b, dict) and b.get("type") == "thinking" and b.get("thinking")
            for m in c["payload"]["request"]["messages"]
            if isinstance(m.get("content"), list)
            for b in m["content"]
        )
    ]
    assert with_text, "fixture must carry a thinking block that kept its text"
    assert with_text[0]["payload"]["import"]["reasoning_blocks_stripped"] == 0


def test_no_duration_is_invented(tool_loop_events):
    """Records carry one timestamp, not request start/end."""
    for call in calls(tool_loop_events):
        assert "duration_ms" not in call["payload"]


# --- no tags: the untagged/heuristic tier -----------------------------------


def test_no_tag_events_emitted(tool_loop_events):
    assert not [e for e in tool_loop_events if e["event_type"] == "tag"]


def test_match_rate_stays_null_rather_than_faked(tool_loop_events):
    data = normalize.build_report_data(tool_loop_events)
    assert data["stats"]["tags"]["match_rate"] is None


# --- spans = user turns -----------------------------------------------------


def test_human_turn_opens_a_span(tool_loop_events):
    starts = [e for e in tool_loop_events if e["event_type"] == "span_start"]
    assert len(starts) == 1
    assert starts[0]["payload"]["name"]


def test_tool_loop_calls_share_the_turns_span(tool_loop_events):
    span_ids = {c["span_id"] for c in calls(tool_loop_events)}
    assert len(span_ids) == 1 and None not in span_ids


def test_span_is_closed(tool_loop_events):
    starts = [e for e in tool_loop_events if e["event_type"] == "span_start"]
    ends = [e for e in tool_loop_events if e["event_type"] == "span_end"]
    assert {e["span_id"] for e in starts} == {e["span_id"] for e in ends}


def test_injected_and_compact_records_do_not_open_spans():
    """isMeta and isCompactSummary user records are not human turns."""
    events = events_for("session_edge_cases.jsonl")
    names = [e["payload"]["name"] for e in events if e["event_type"] == "span_start"]
    assert not any("system-reminder" in n or "continuation of a prior" in n for n in names)


def test_sidechain_kept_in_its_own_span():
    """Task-subagent turns must never merge into the main chain."""
    events = events_for("session_edge_cases.jsonl")
    main = [c for c in calls(events) if not c["payload"]["import"].get("sidechain")]
    side = [c for c in calls(events) if c["payload"]["import"].get("sidechain")]
    assert side and main
    assert not ({c["span_id"] for c in side} & {c["span_id"] for c in main})


# --- robustness -------------------------------------------------------------


def test_unknown_and_bookkeeping_records_ignored():
    events = events_for("session_edge_cases.jsonl")
    assert calls(events)


def test_malformed_lines_skipped(tmp_path):
    path = tmp_path / "broken.jsonl"
    good = (FIXTURES / "session_tool_loop.jsonl").read_text().splitlines()
    path.write_text("\n".join(["{not json", *good, "["]) + "\n")
    assert len(calls(claude_code.import_transcript(path))) == 2


def test_timestamps_come_from_records_not_import_time(tool_loop_events):
    assert calls(tool_loop_events)[0]["timestamp"].startswith("2026-07-17T10:00:05")


def test_session_id_from_transcript(tool_loop_events):
    assert {e["session_id"] for e in tool_loop_events} == {"sess-tool-loop"}


def test_import_is_deterministic(tool_loop_events):
    assert events_for("session_tool_loop.jsonl") == tool_loop_events


def test_events_validate_against_schema_v1(validate_event):
    for name in ("session_tool_loop.jsonl", "session_branched.jsonl", "session_edge_cases.jsonl"):
        for event in events_for(name):
            validate_event(event)


# --- end to end: the existing report renders it unchanged -------------------


def test_report_pipeline_renders_imported_events(tool_loop_events):
    data = normalize.build_report_data(tool_loop_events)
    assert data["stats"]["calls"] == 2
    call = data["sessions"][0]["calls"][0]
    assert call["provider"] == "anthropic"
    assert call["model"] == "claude-fable-5"
    assert call["context_window"] == 200_000
    assert call["step"]
    assert [s["kind"] for s in call["segments"]] == ["user"]
    assert "tool_use: Read" in call["output"]["content"]


def test_tool_result_renders_as_tool_segment(tool_loop_events):
    data = normalize.build_report_data(tool_loop_events)
    second = data["sessions"][0]["calls"][1]
    assert "tool" in [s["kind"] for s in second["segments"]]


def test_lineage_edges_form_across_the_tool_loop(tool_loop_events):
    data = normalize.build_report_data(tool_loop_events)
    assert data["sessions"][0]["edges"]
