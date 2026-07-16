import json

from ctxlineage._report import matching


def _seg(content, kind="user"):
    return {"role": kind, "kind": kind, "content": content}


def _tag(name, content, source=None, transform=None):
    payload = {"name": name, "content": content}
    if source:
        payload["source"] = source
    if transform:
        payload["transform"] = transform
    return payload


def test_exact_match_replaces_whole_segment():
    segments = [_seg("You are helpful.", kind="system")]
    tags = [_tag("app_prompt", "You are helpful.", source="prompts/base.txt")]
    out, matched = matching.apply_tags(segments, tags)
    assert matched == {"app_prompt"}
    (seg,) = out
    assert seg["kind"] == "app_prompt"
    assert seg["tagged"] is True
    assert seg["match"] == "exact"
    assert seg["source"] == "prompts/base.txt"
    assert seg["role"] == "system"


def test_partial_match_splits_segment():
    segments = [_seg("Context:\nCHUNK ONE\n\nQuestion: hi")]
    tags = [_tag("rag_chunks", "CHUNK ONE", source="qdrant:x")]
    out, matched = matching.apply_tags(segments, tags)
    assert matched == {"rag_chunks"}
    kinds = [(s["kind"], s["tagged"]) for s in out]
    assert kinds == [("user", False), ("rag_chunks", True), ("user", False)]
    assert out[1]["content"] == "CHUNK ONE"
    assert out[1]["match"] == "partial"
    assert out[0]["content"] == "Context:\n"
    assert out[2]["content"] == "\n\nQuestion: hi"


def test_json_list_tag_matches_each_element():
    chunks = ["alpha doc text", "beta doc text"]
    segments = [_seg(f"Context:\n{chunks[0]}\n\n{chunks[1]}\n\nQ: hi")]
    tags = [_tag("rag_chunks", json.dumps(chunks))]
    out, matched = matching.apply_tags(segments, tags)
    assert matched == {"rag_chunks"}
    tagged = [s for s in out if s["tagged"]]
    assert [s["content"] for s in tagged] == chunks
    assert all(s["kind"] == "rag_chunks" for s in tagged)


def test_unmatched_tag_reported_and_segments_untouched():
    segments = [_seg("completely unrelated")]
    tags = [_tag("memory", "user prefers dark mode")]
    out, matched = matching.apply_tags(segments, tags)
    assert matched == set()
    assert out == [
        {"role": "user", "kind": "user", "content": "completely unrelated", "tagged": False}
    ]


def test_overlapping_units_longest_leftmost_wins():
    segments = [_seg("abc def ghi")]
    tags = [_tag("short", "def"), _tag("long", "abc def")]
    out, matched = matching.apply_tags(segments, tags)
    assert "long" in matched
    assert "short" not in matched
    assert [s["kind"] for s in out] == ["long", "user"]


def test_multiple_tags_in_one_segment():
    segments = [_seg("A: first B: second C")]
    tags = [_tag("t1", "first"), _tag("t2", "second")]
    out, matched = matching.apply_tags(segments, tags)
    assert matched == {"t1", "t2"}
    assert [s["kind"] for s in out] == ["user", "t1", "user", "t2", "user"]


def test_empty_or_tiny_units_are_ignored():
    segments = [_seg("some content here")]
    tags = [_tag("empty", ""), _tag("tiny", "e")]
    out, matched = matching.apply_tags(segments, tags)
    assert matched == set()
    assert len(out) == 1 and out[0]["tagged"] is False


def test_japanese_partial_match_splits_correctly():
    chunk = "東京は日本の首都です。"
    segments = [_seg("コンテキスト:\n" + chunk + "\n\n質問: 首都は?")]
    tags = [_tag("rag_chunks", json.dumps([chunk], ensure_ascii=False), source="qdrant:jp")]
    out, matched = matching.apply_tags(segments, tags)
    assert matched == {"rag_chunks"}
    tagged = next(s for s in out if s["tagged"])
    assert tagged["content"] == chunk
