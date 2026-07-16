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
    # all substitution markers consumed
    assert "/*__STYLE__*/" not in out
    assert "/*__APP__*/" not in out
    assert "__DATA__" not in out


def test_render_contains_all_views_and_theme_toggle():
    out = html.render(_data())
    assert 'id="filter"' in out  # client-side search box
    assert 'data-view="overview"' in out
    assert 'data-view="calls"' in out
    assert 'data-view="chain"' in out
    assert 'id="theme"' in out
    assert "prefers-color-scheme" in out  # OS-follow default
    assert '[data-theme="dark"]' in out  # dark tokens present


def test_assets_resolve_from_package():
    from importlib import resources

    base = resources.files("ctxlineage._report") / "assets"
    for name in ("template.html", "style.css", "app.js"):
        assert (base / name).read_text(encoding="utf-8").strip()


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
