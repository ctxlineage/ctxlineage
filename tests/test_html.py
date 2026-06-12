import json

from ctxlineage._report import html


def _data():
    return {
        "report_version": 1,
        "generated_at": "2026-06-12T09:00:00+00:00",
        "stats": {"sessions": 1, "calls": 1, "errors": 0},
        "sessions": [
            {
                "id": "s1",
                "started_at": "2026-06-12T09:00:00+00:00",
                "ended_at": "2026-06-12T09:00:00+00:00",
                "calls": [
                    {
                        "id": "c1",
                        "timestamp": "2026-06-12T09:00:00+00:00",
                        "provider": "openai",
                        "api": "chat.completions",
                        "model": "gpt-4o-mini",
                        "stream": False,
                        "duration_ms": 10.0,
                        "error": None,
                        "context_window": 128000,
                        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
                        "segments": [
                            {
                                "index": 0,
                                "role": "user",
                                "kind": "user",
                                "content": "hi</script><script>alert(1)</script>",
                                "tokens_est": 5,
                            }
                        ],
                        "input_tokens_est": 5,
                        "output": {"content": "hello", "finish_reason": "stop"},
                        "call_stack": [],
                    }
                ],
            }
        ],
    }


def test_render_is_self_contained_html():
    out = html.render(_data())
    assert out.startswith("<!DOCTYPE html>")
    assert "ctxlineage" in out
    assert "http://" not in out and "https://" not in out  # no CDN / external refs


def test_embedded_json_round_trips():
    out = html.render(_data())
    start = out.index('<script type="application/json" id="ctxlineage-data">')
    payload = out[start:]
    payload = payload[payload.index(">") + 1 : payload.index("</script>")]
    data = json.loads(payload)
    assert data["sessions"][0]["calls"][0]["id"] == "c1"


def test_script_tag_in_content_cannot_break_out():
    out = html.render(_data())
    start = out.index('<script type="application/json" id="ctxlineage-data">')
    payload = out[start:]
    payload = payload[payload.index(">") + 1 : payload.index("</script>")]
    # the raw sequence "</script" must never appear inside the JSON block
    assert "</script" not in payload
    assert json.loads(payload)["sessions"][0]["calls"][0]["segments"][0]["content"].startswith("hi")
