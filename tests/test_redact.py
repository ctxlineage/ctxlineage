"""Redaction (PLAN.md §6 sensitive data, docs/plans/2026-07-16-redact.md).

Report-side: `ctxlineage report --redact PATTERN` masks regex matches in the
built report data — after matching/edge inference, so stats stay honest — and
discloses the replacement count. Capture-side: init(redact_fields=...) masks
payload fields before they ever reach events.jsonl.
"""

import json
import re

import pytest
from click.testing import CliRunner

from ctxlineage._cli import main
from ctxlineage._report import normalize, redact

SECRET = "sk-abc123SECRET"


def _events():
    return [
        {
            "schema_version": 1,
            "event_type": "span_start",
            "session_id": "s1",
            "span_id": "sp1",
            "call_id": None,
            "timestamp": "2026-07-16T09:00:00+00:00",
            "payload": {"name": "answer"},
        },
        {
            "schema_version": 1,
            "event_type": "tag",
            "session_id": "s1",
            "span_id": "sp1",
            "call_id": None,
            "timestamp": "2026-07-16T09:00:01+00:00",
            "payload": {
                "name": "rag_chunks",
                "content": f"THE CHUNK {SECRET} TEXT",
                "source": f"qdrant:{SECRET}",
                "transform": f"filter({SECRET})",
            },
        },
        {
            "schema_version": 1,
            "event_type": "llm_call",
            "session_id": "s1",
            "span_id": "sp1",
            "call_id": "c1",
            "timestamp": "2026-07-16T09:00:02+00:00",
            "payload": {
                "provider": "openai",
                "api": "chat.completions",
                "request": {
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "user", "content": f"Context:\nTHE CHUNK {SECRET} TEXT\nQ: hi"}
                    ],
                    "tools": [
                        {
                            "type": "function",
                            "function": {"name": "lookup", "description": f"uses {SECRET}"},
                        }
                    ],
                },
                "stream": False,
                "duration_ms": 5.0,
                "call_stack": [],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
                "response": {
                    "object": "chat.completion",
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": f"answer with {SECRET}"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            },
        },
        {
            "schema_version": 1,
            "event_type": "llm_call",
            "session_id": "s1",
            "span_id": None,
            "call_id": "c2",
            "timestamp": "2026-07-16T09:01:00+00:00",
            "payload": {
                "provider": "openai",
                "api": "chat.completions",
                "request": {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "x"}]},
                "stream": False,
                "duration_ms": 5.0,
                "call_stack": [],
                "error": {"type": "APIError", "message": f"prompt rejected: {SECRET}"},
            },
        },
    ]


def _data():
    return normalize.build_report_data(_events())


# ---------- report-side: redact.apply ----------


def test_apply_masks_all_text_carriers():
    data = _data()
    count = redact.apply(data, [re.escape(SECRET)])
    assert count > 0
    assert SECRET not in json.dumps(data)
    call = data["sessions"][0]["calls"][0]
    assert any("[redacted]" in s["content"] for s in call["segments"])
    assert "[redacted]" in call["output"]["content"]
    error = data["sessions"][0]["calls"][1]["error"]
    assert error["message"] == "prompt rejected: [redacted]"
    element = data["sessions"][0]["elements"][0]
    assert element["source"] == "qdrant:[redacted]"
    assert element["transform"] == "filter([redacted])"


def test_apply_masks_declared_structure():
    # #103 gave segments and outputs a `structured` field carrying a tool
    # call's own arguments. It is the same text as the flattened `content`,
    # so an unwalked field would hand back verbatim what the mask removed one
    # field over - and a tool argument is exactly where a key gets passed.
    data = {
        "sessions": [
            {
                "calls": [
                    {
                        "segments": [
                            {
                                "content": f"[tool_use: Fetch({{'token': '{SECRET}'}})]",
                                "structured": [
                                    {
                                        "kind": "tool_call",
                                        "name": "Fetch",
                                        "value": {"token": SECRET, "nested": [{"k": SECRET}]},
                                    }
                                ],
                            }
                        ],
                        "output": {
                            "content": SECRET,
                            "structured": [
                                {"kind": "tool_call", "name": SECRET, "value": {"q": SECRET}}
                            ],
                        },
                    }
                ]
            }
        ]
    }
    count = redact.apply(data, [re.escape(SECRET)])

    assert count > 0
    assert SECRET not in json.dumps(data)
    call = data["sessions"][0]["calls"][0]
    part = call["segments"][0]["structured"][0]
    assert part["value"]["token"] == "[redacted]"
    assert part["value"]["nested"][0]["k"] == "[redacted]"
    assert part["kind"] == "tool_call"  # structural fields are never touched
    assert part["name"] == "Fetch"
    out = call["output"]["structured"][0]
    assert out["name"] == "[redacted]"
    assert out["value"]["q"] == "[redacted]"


def test_apply_masks_structured_object_keys():
    """A tool argument is routinely a map keyed by an address, an id or a path,
    and a key reaches the rendered tree exactly as a value does. Before
    `structured` existed keys could only arrive by parsing an already-masked
    `content`, so they were covered for free; the new field has to mask them
    itself or `--redact` hands back verbatim what it removed one field over.
    """
    data = {
        "sessions": [
            {
                "calls": [
                    {
                        "segments": [
                            {
                                "content": f'[tool_use: Notify({{"{SECRET}": "cc"}})]',
                                "structured": [
                                    {
                                        "kind": "tool_call",
                                        "name": "Notify",
                                        "value": {"recipients": {SECRET: "cc"}},
                                    }
                                ],
                            }
                        ],
                        "output": None,
                    }
                ]
            }
        ]
    }
    redact.apply(data, [re.escape(SECRET)])

    part = data["sessions"][0]["calls"][0]["segments"][0]["structured"][0]
    assert part["value"]["recipients"] == {"[redacted]": "cc"}
    assert SECRET not in json.dumps(data)


def test_apply_keeps_colliding_structured_keys_distinct():
    """Masking is many-to-one, so a plain dict comprehension drops a key *and
    its value* and then reports `object · 1 key` for what were two. Numbering
    the repeats keeps every value and keeps the arity the UI prints true.
    Keys the mask did not touch are reserved first, so a real key is never
    renamed to make room for a masked one."""
    data = {
        "sessions": [
            {
                "calls": [
                    {
                        "segments": [
                            {
                                "content": "x",
                                "structured": [
                                    {
                                        "kind": "tool_call",
                                        "name": "N",
                                        "value": {
                                            "[redacted]": 0,  # a literal collision target
                                            "a@x.example": 1,
                                            "b@x.example": 2,
                                            "keep": 3,
                                        },
                                    }
                                ],
                            }
                        ],
                        "output": None,
                    }
                ]
            }
        ]
    }
    redact.apply(data, [r"[\w.]+@[\w.]+"])

    value = data["sessions"][0]["calls"][0]["segments"][0]["structured"][0]["value"]
    assert len(value) == 4, f"an entry was dropped: {value}"
    assert sorted(value.values()) == [0, 1, 2, 3], "a value was lost"
    assert value["[redacted]"] == 0, "an unmasked key was renamed"
    assert value["keep"] == 3
    assert not any("@" in k for k in value), value


def test_apply_does_not_double_count_structured_matches():
    """`structured` is a second representation of text `content` already
    carries. The disclosed count says how much was found, not how many copies
    of the report data happen to hold it."""
    data = {
        "sessions": [
            {
                "calls": [
                    {
                        "segments": [
                            {
                                "content": f'[tool_use: Send({{"to": "{SECRET}"}})]',
                                "structured": [
                                    {"kind": "tool_call", "name": "Send", "value": {"to": SECRET}}
                                ],
                            }
                        ],
                        "output": None,
                    }
                ]
            }
        ]
    }
    count = redact.apply(data, [re.escape(SECRET)])

    assert count == 1, f"one occurrence disclosed as {count}"
    assert SECRET not in json.dumps(data)


def test_apply_masks_aggregated_element_provenance():
    # #44 added elements[].sources / .transforms lists; the redactor must walk
    # them too or a secret survives in the aggregated provenance.
    events = _events()
    second = dict(events[1])
    second["payload"] = {
        "name": "rag_chunks",
        "content": f"SECOND CHUNK {SECRET}",
        "source": f"pinecone:{SECRET}",
    }
    second["timestamp"] = "2026-07-16T09:00:01.500000+00:00"
    events.insert(2, second)
    data = normalize.build_report_data(events)
    redact.apply(data, [re.escape(SECRET)])
    assert SECRET not in json.dumps(data)
    element = data["sessions"][0]["elements"][0]
    assert element["occurrences"] == 2
    assert all(SECRET not in s for s in element["sources"])


def test_apply_keeps_stats_and_structure_honest():
    before = _data()
    after = _data()
    redact.apply(after, [re.escape(SECRET)])
    assert after["stats"] == before["stats"]  # matching ran on real text
    b, a = before["sessions"][0]["calls"][0], after["sessions"][0]["calls"][0]
    assert a["usage"] == b["usage"]
    assert a["input_tokens_est"] == b["input_tokens_est"]
    assert [s["tokens_est"] for s in a["segments"]] == [s["tokens_est"] for s in b["segments"]]
    assert a["model"] == b["model"]
    assert a["id"] == b["id"]
    assert after["sessions"][0]["edges"] == before["sessions"][0]["edges"]


def test_apply_discloses_counts_never_patterns():
    data = _data()
    count = redact.apply(data, [re.escape(SECRET), "nomatchxyz"])
    assert data["redaction"] == {"patterns": 2, "matches": count}
    assert SECRET not in json.dumps(data["redaction"])


def test_apply_zero_matches():
    data = _data()
    assert redact.apply(data, ["nomatchxyz"]) == 0
    assert data["redaction"]["matches"] == 0


def test_apply_multiple_patterns():
    data = _data()
    redact.apply(data, [re.escape(SECRET), r"Q: \w+"])
    text = json.dumps(data)
    assert SECRET not in text
    assert "Q: hi" not in text


def test_apply_invalid_regex_raises():
    with pytest.raises(re.error):
        redact.apply(_data(), ["(unclosed"])


# ---------- report-side: CLI ----------


@pytest.fixture
def events_dir(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in _events()), encoding="utf-8")
    return tmp_path


def test_cli_redact_html(events_dir, tmp_path):
    out = tmp_path / "report.html"
    result = CliRunner().invoke(
        main,
        ["report", "--dir", str(events_dir), "--out", str(out), "--redact", re.escape(SECRET)],
    )
    assert result.exit_code == 0, result.output
    page = out.read_text(encoding="utf-8")
    assert SECRET not in page
    assert "redact" in result.output  # summary discloses the redaction count


def test_cli_redact_json(events_dir):
    result = CliRunner().invoke(
        main, ["report", "--dir", str(events_dir), "--json", "--redact", re.escape(SECRET)]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert SECRET not in result.output
    assert data["redaction"]["matches"] > 0


def test_cli_redact_invalid_pattern(events_dir):
    result = CliRunner().invoke(main, ["report", "--dir", str(events_dir), "--redact", "(oops"])
    assert result.exit_code == 1
    assert "(oops" in result.output


def test_cli_without_redact_unchanged(events_dir):
    result = CliRunner().invoke(main, ["report", "--dir", str(events_dir), "--json"])
    data = json.loads(result.output)
    assert "redaction" not in data


# ---------- capture-side: init(redact_fields=...) ----------


def test_capture_masks_configured_fields(tmp_path):
    import ctxlineage

    ctxlineage.init(tmp_path, redact_fields=["request.messages.content"])
    from ctxlineage import _state

    _state.emit(
        "llm_call",
        {
            "provider": "openai",
            "api": "chat.completions",
            "request": {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": SECRET}]},
            "stream": False,
        },
        call_id="c1",
    )
    raw = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert SECRET not in raw
    event = json.loads(raw.splitlines()[0])
    assert event["payload"]["request"]["messages"][0]["content"] == "[redacted]"
    assert event["payload"]["request"]["messages"][0]["role"] == "user"  # siblings kept


def test_capture_masks_whole_field_and_tags(tmp_path):
    import ctxlineage

    ctxlineage.init(tmp_path, redact_fields=["response", "content"])
    from ctxlineage import _state

    _state.emit("llm_call", {"request": {"model": "m"}, "response": {"big": SECRET}})
    _state.emit("tag", {"name": "memory", "content": SECRET})
    raw = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert SECRET not in raw
    events = [json.loads(line) for line in raw.splitlines()]
    assert events[0]["payload"]["response"] == "[redacted]"
    assert events[1]["payload"]["content"] == "[redacted]"
    assert events[1]["payload"]["name"] == "memory"


def test_capture_missing_path_is_noop(tmp_path):
    import ctxlineage

    ctxlineage.init(tmp_path, redact_fields=["request.nonexistent.deep"])
    from ctxlineage import _state

    _state.emit("llm_call", {"request": {"model": "m"}})
    event = json.loads((tmp_path / "events.jsonl").read_text().splitlines()[0])
    assert event["payload"]["request"] == {"model": "m"}


def test_capture_caller_payload_not_mutated(tmp_path):
    import ctxlineage

    ctxlineage.init(tmp_path, redact_fields=["request.messages.content"])
    from ctxlineage import _state

    payload = {"request": {"messages": [{"role": "user", "content": SECRET}]}}
    _state.emit("llm_call", payload)
    assert payload["request"]["messages"][0]["content"] == SECRET


def test_capture_mask_failure_drops_event(tmp_path, monkeypatch):
    import ctxlineage
    from ctxlineage import _redact, _state

    ctxlineage.init(tmp_path, redact_fields=["request"])

    def boom(payload, fields):
        raise RuntimeError("poisoned")

    monkeypatch.setattr(_redact, "mask_payload", boom)
    with pytest.warns(RuntimeWarning, match="redact"):
        ok = _state.emit("llm_call", {"request": {"secret": SECRET}})
    assert ok is False
    path = tmp_path / "events.jsonl"
    assert not path.exists() or SECRET not in path.read_text()
