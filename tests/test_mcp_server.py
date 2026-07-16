"""MCP server (PLAN.md 4c): read-only tools over the same JSONL.

Tools are tested as plain functions (FastMCP's decorator returns the original
callable); one registration test and one call_tool round-trip cover the MCP
wiring. stdio transport is SDK plumbing, not ours.
"""

import json

import pytest

from ctxlineage._report import tokens
from ctxlineage_mcp import server


@pytest.fixture(autouse=True)
def offline_tokens(monkeypatch):
    monkeypatch.setattr(tokens, "_encoding_for", lambda model: None)


def _llm_call(call_id, ts, messages, answer, session="s1", span_id=None, error=None):
    payload = {
        "provider": "openai",
        "api": "chat.completions",
        "request": {"model": "gpt-4o-mini", "messages": messages},
        "stream": False,
        "duration_ms": 10.0,
        "call_stack": ["app.py:main:1"],
    }
    if error is not None:
        payload["error"] = error
    else:
        payload["response"] = {
            "id": "x",
            "object": "chat.completion",
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
        }
        payload["usage"] = {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
    return {
        "schema_version": 1,
        "event_type": "llm_call",
        "session_id": session,
        "span_id": span_id,
        "call_id": call_id,
        "timestamp": ts,
        "payload": payload,
    }


def _span_start(span_id, name, session, ts):
    return {
        "schema_version": 1,
        "event_type": "span_start",
        "session_id": session,
        "span_id": span_id,
        "call_id": None,
        "timestamp": ts,
        "payload": {"name": name},
    }


def _tag(span_id, name, content, session, ts, **meta):
    return {
        "schema_version": 1,
        "event_type": "tag",
        "session_id": session,
        "span_id": span_id,
        "call_id": None,
        "timestamp": ts,
        "payload": {"name": name, "content": content, **meta},
    }


C1_ANSWER = "The answer is 42, definitely."


def _fixture_events():
    return [
        # s1: tagged span, c1's output flows into c2 (output_text + same_span)
        _span_start("sp1", "answer_query", "s1", "2026-07-16T09:00:00+00:00"),
        _tag(
            "sp1",
            "rag_chunks",
            "THE CHUNK TEXT ALPHA",
            "s1",
            "2026-07-16T09:00:01+00:00",
            source="qdrant:x",
        ),
        _llm_call(
            "c1",
            "2026-07-16T09:00:02+00:00",
            [{"role": "user", "content": "Context:\nTHE CHUNK TEXT ALPHA\nQ: hi"}],
            C1_ANSWER,
            span_id="sp1",
        ),
        _llm_call(
            "c2",
            "2026-07-16T09:05:00+00:00",
            [{"role": "user", "content": f"Previously: {C1_ANSWER}\n" + "x" * 900}],
            "done and dusted okay!",
            span_id="sp1",
        ),
        # s2: second span reuses the tag name rag_chunks; one error call
        _span_start("sp2", "other_span", "s2", "2026-07-16T10:00:00+00:00"),
        _tag("sp2", "rag_chunks", "BETA CHUNK CONTENT", "s2", "2026-07-16T10:00:01+00:00"),
        _tag("sp2", "sysprompt", "You are terse.", "s2", "2026-07-16T10:00:02+00:00"),
        _llm_call(
            "c3",
            "2026-07-16T10:00:03+00:00",
            [
                {"role": "system", "content": "You are terse."},
                {"role": "user", "content": "Context: BETA CHUNK CONTENT"},
            ],
            "Sure thing, here you go.",
            session="s2",
            span_id="sp2",
        ),
        _llm_call(
            "c4",
            "2026-07-16T10:05:00+00:00",
            [{"role": "user", "content": "hello?"}],
            None,
            session="s2",
            error={"type": "APIError", "message": "boom"},
        ),
    ]


@pytest.fixture()
def events_dir(tmp_path):
    directory = tmp_path / ".ctxlineage"
    directory.mkdir()
    path = directory / "events.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in _fixture_events()), encoding="utf-8")
    server.configure(directory)
    return directory


async def test_tools_registered():
    tools = {t.name for t in await server.mcp.list_tools()}
    assert tools == {"list_sessions", "get_call", "get_lineage", "generate_report"}
    assert server.mcp.name == "ctxlineage"


async def test_call_tool_roundtrip(events_dir):
    result = await server.mcp.call_tool("list_sessions", {})
    assert "s1" in str(result)


def test_list_sessions_summaries(events_dir):
    data = server.list_sessions()
    assert data["stats"]["calls"] == 4
    assert data["stats"]["tags"]["total"] == 3
    assert [s["id"] for s in data["sessions"]] == ["s1", "s2"]
    s1, s2 = data["sessions"]
    assert s1["call_count"] == 2
    assert s1["error_count"] == 0
    assert s1["call_ids"] == ["c1", "c2"]
    assert s1["element_ids"] == ["sp1:rag_chunks"]
    assert s1["models"] == ["gpt-4o-mini"]
    assert s1["started_at"] == "2026-07-16T09:00:02+00:00"
    assert s2["error_count"] == 1
    assert s2["element_ids"] == ["sp2:rag_chunks", "sp2:sysprompt"]
    # index tool: no prompt bodies
    assert "segments" not in json.dumps(data)


def test_get_call_truncates_by_default(events_dir):
    call = server.get_call("c2")
    assert call["session_id"] == "s1"
    segment = call["segments"][0]
    assert segment["content_truncated"] is True
    assert len(segment["content"]) <= server._TRUNCATE_AT
    full = server.get_call("c2", full_content=True)
    assert "x" * 900 in full["segments"][0]["content"]
    assert "content_truncated" not in full["segments"][0]


def test_get_call_does_not_mutate_cache(events_dir):
    server.get_call("c2")
    assert "x" * 900 in server.get_call("c2", full_content=True)["segments"][0]["content"]


def test_get_call_keeps_short_calls_and_usage(events_dir):
    call = server.get_call("c1")
    assert call["usage"]["total_tokens"] == 25
    assert call["step"] == "answer_query"
    assert all("content_truncated" not in s for s in call["segments"])
    assert call["output"]["content"] == C1_ANSWER


def test_get_call_unknown_id(events_dir):
    with pytest.raises(ValueError, match="list_sessions"):
        server.get_call("nope")


def test_get_lineage_by_call_id(events_dir):
    lineage = server.get_lineage("c1")
    assert lineage["node"]["type"] == "call"
    assert lineage["node"]["id"] == "c1"
    kinds = {(e["from"], e["to"], e["kind"]) for e in lineage["edges_out"]}
    assert ("c1", "c2", "output_text") in kinds
    assert ("c1", "c2", "same_span") in kinds
    assert lineage["edges_in"] == []
    assert lineage["downstream_call_ids"] == ["c2"]
    assert [e["element_id"] for e in lineage["elements_consumed"]] == ["sp1:rag_chunks"]


def test_get_lineage_by_element_id(events_dir):
    lineage = server.get_lineage("sp1:rag_chunks")
    assert lineage["node"]["type"] == "element"
    assert lineage["node"]["source"] == "qdrant:x"
    assert lineage["consuming_call_ids"] == ["c1"]
    assert lineage["downstream_call_ids"] == ["c2"]


def test_get_lineage_by_unique_bare_name(events_dir):
    lineage = server.get_lineage("sysprompt")
    assert lineage["node"]["element_id"] == "sp2:sysprompt"
    assert lineage["consuming_call_ids"] == ["c3"]


def test_get_lineage_ambiguous_name(events_dir):
    with pytest.raises(ValueError, match="sp1:rag_chunks") as exc:
        server.get_lineage("rag_chunks")
    assert "sp2:rag_chunks" in str(exc.value)


def test_get_lineage_unknown_id(events_dir):
    with pytest.raises(ValueError, match="list_sessions"):
        server.get_lineage("ghost")


def test_generate_report(events_dir, tmp_path):
    out = tmp_path / "report.html"
    result = server.generate_report(str(out))
    assert result["path"] == str(out.resolve())
    assert result["sessions"] == 2
    assert result["calls"] == 4
    page = out.read_text(encoding="utf-8")
    assert "report_version" in page


def test_reflects_appended_events(events_dir):
    assert len(server.list_sessions()["sessions"]) == 2
    new_call = _llm_call(
        "c9",
        "2026-07-16T11:00:00+00:00",
        [{"role": "user", "content": "another session entirely"}],
        "yes indeed it works",
        session="s3",
    )
    with (events_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(new_call) + "\n")
    assert len(server.list_sessions()["sessions"]) == 3


def test_missing_events_file(tmp_path):
    server.configure(tmp_path / "empty")
    with pytest.raises(FileNotFoundError, match=r"ctxlineage\.init\(\)"):
        server.list_sessions()
