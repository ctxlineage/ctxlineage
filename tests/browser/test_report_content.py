"""Structure-aware content rendering in the Calls view (#92), in a real browser.

A JSON-shaped segment or output body renders as a collapsed tree — top-level
keys visible, expandable — instead of an undifferentiated wall of quotes and
braces. Non-JSON content is unaffected. The INSTRUCTIONS panel and the output
body also gained a discoverable, click-to-expand toggle (the panel already
toggled; it had no visible affordance saying so, and the output body had no
toggle at all).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

JSON_PAYLOAD = {"session_id": "abc123", "answers": [1, 2, 3], "narrative": "a long analysis field"}
LONG_SYSTEM_PROMPT = (
    "You are a careful assistant. " * 60
)  # long enough to overflow the collapsed panel


def _event(
    event_type, session, payload, call_id=None, span_id=None, ts="2026-06-12T09:00:00+00:00"
):
    return {
        "schema_version": 1,
        "event_type": event_type,
        "session_id": session,
        "span_id": span_id,
        "call_id": call_id,
        "timestamp": ts,
        "payload": payload,
    }


def _content_events() -> list[dict]:
    return [
        _event(
            "llm_call",
            "session-content",
            {
                "provider": "openai",
                "api": "chat.completions",
                "request": {
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": LONG_SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(JSON_PAYLOAD)},
                    ],
                },
                "response": {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "plain text output"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 5, "total_tokens": 125},
                },
                "usage": {"prompt_tokens": 120, "completion_tokens": 5, "total_tokens": 125},
                "stream": False,
                "duration_ms": 12.5,
                "call_stack": ["app.py:handle:10"],
            },
            call_id="c1",
        ),
        _event(
            "llm_call",
            "session-content",
            {
                "provider": "openai",
                "api": "chat.completions",
                "request": {
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "plain, not json"}],
                },
                "response": {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": json.dumps([1, 2, 3])},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
                },
                "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
                "stream": False,
                "duration_ms": 8.0,
                "call_stack": ["app.py:handle:11"],
            },
            call_id="c2",
            ts="2026-06-12T09:01:00+00:00",
        ),
    ]


@pytest.fixture(scope="session")
def content_report(render_events) -> str:
    return render_events(_content_events())


def open_calls_view(open_report, html_text: str, index: int = 0):
    page = open_report(html_text)
    page.click('.tab[data-view="calls"]')
    page.wait_for_selector(".windowbar")
    if index:
        page.click(f'.callrow[data-i="{index}"]')
        page.wait_for_selector(f".callrow.sel[data-i='{index}']")
    return page


def test_json_segment_renders_a_collapsed_tree(open_report, content_report):
    """The user-input segment (call 1) is a 3-key JSON object."""
    page = open_calls_view(open_report, content_report, index=0)
    page.click(".col .seg:not(.unaccounted-seg)")  # open the segment
    tree = page.locator(".col .seg.open .full .jchildren").first
    assert tree.count() == 1
    # top-level keys visible, none pre-expanded (nested content stays collapsed
    # inside a closed <details> - present in the DOM, so count direct children
    # only, or "answers" would also count its own 3 hidden array items).
    assert page.locator(".col .seg.open .full > .jchildren > .jrow").count() == 3
    assert "session_id" in page.locator(".col .seg.open .full").inner_text()
    assert page.locator(".col .seg.open .full details[open]").count() == 0


def test_expanding_a_nested_json_branch_does_not_close_the_segment(open_report, content_report):
    """A click on a nested <details>/<summary> bubbles up to the segment's own
    click-to-toggle listener - it must not also close the segment that
    contains it (the outer toggle and the inner disclosure are independent)."""
    page = open_calls_view(open_report, content_report, index=0)
    page.click(".col .seg:not(.unaccounted-seg)")  # open
    assert "open" in page.locator(".col .seg:not(.unaccounted-seg)").get_attribute("class")

    page.click(".col .seg.open .full summary")  # expand the "answers" branch
    seg_class = page.locator(".col .seg:not(.unaccounted-seg)").get_attribute("class")
    assert "open" in seg_class, "the nested toggle closed the whole segment"
    assert page.locator(".col .seg.open .full details[open]").count() == 1


def test_json_segment_preview_shows_structural_summary(open_report, content_report):
    """Collapsed (unclicked) state: 'object · 3 keys', not a clipped wall of text."""
    page = open_calls_view(open_report, content_report, index=0)
    preview = page.locator(".col .seg:not(.unaccounted-seg) .preview").first
    assert "object" in preview.inner_text()
    assert "3 key" in preview.inner_text()
    assert '"session_id"' not in preview.inner_text()  # not a raw text clip


def test_non_json_segment_is_unaffected(open_report, content_report):
    """Regression guard: plain text still renders as plain text, no tree."""
    page = open_calls_view(open_report, content_report, index=1)
    page.click(".col .seg:not(.unaccounted-seg)")
    body = page.locator(".col .seg.open .full").first
    assert body.locator(".jtree, .jchildren").count() == 0
    assert body.inner_text().strip() == "plain, not json"


def test_json_output_renders_as_a_tree_and_is_expandable(open_report, content_report):
    """Call 2's output is a JSON array - same tree treatment, plus the new
    click-to-expand toggle (#92: the output body previously had none at all)."""
    page = open_calls_view(open_report, content_report, index=1)
    out = page.locator("#outwrap")
    assert out.count() == 1
    assert "array" in out.locator(".head").inner_text()
    assert out.locator(".body .jrow").count() == 3  # [1, 2, 3]

    assert "open" not in (out.get_attribute("class") or "")
    out.click()
    assert "open" in out.get_attribute("class")
    max_height = page.evaluate(
        "() => getComputedStyle(document.querySelector('#outwrap .body')).maxHeight"
    )
    assert max_height == "none"


def test_instructions_panel_has_a_discoverable_toggle(open_report, content_report):
    """#92: the panel already toggled open/closed but gave no visual hint that
    it was clickable - it must now show one, and reflect open state."""
    page = open_calls_view(open_report, content_report, index=0)
    toggle = page.locator("#instr .toggle")
    assert toggle.count() == 1

    before = page.evaluate(
        "() => getComputedStyle(document.querySelector('#instr .toggle')).transform"
    )
    page.click("#instr")
    assert "open" in page.locator("#instr").get_attribute("class")
    after = page.evaluate(
        "() => getComputedStyle(document.querySelector('#instr .toggle')).transform"
    )
    assert before != after  # the chevron visibly rotates on open


def test_reopening_a_scrolled_panel_returns_to_the_head(open_report, content_report):
    """#92: a toggle only flips a CSS class (no re-render), so a scrolled panel
    must be reset to the top on reopen or it silently reopens mid-content."""
    page = open_calls_view(open_report, content_report, index=0)
    page.click("#instr")  # open
    page.evaluate("() => { document.querySelector('#instr .txt').scrollTop = 20; }")
    page.click("#instr")  # close
    page.click("#instr")  # reopen
    scroll_top = page.evaluate("() => document.querySelector('#instr .txt').scrollTop")
    assert scroll_top == 0


# ---------------- the new render path is its own escape site ----------------

JSON_PAYLOAD_HOSTILE = '<img src=x onerror="window.__pwned=1">'


def _hostile_json_events() -> list[dict]:
    """A JSON-shaped segment whose value carries the same executable payload
    test_report_escaping.py uses - jsonTreeHtml/jsonLeafText are a NEW render
    path with their own esc() calls, not exercised by the existing hostile
    fixture (its content is deliberately not valid JSON, so it never reaches
    this path)."""
    return [
        _event(
            "llm_call",
            "session-hostile-json",
            {
                "provider": "openai",
                "api": "chat.completions",
                "request": {
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "user",
                            "content": json.dumps({JSON_PAYLOAD_HOSTILE: JSON_PAYLOAD_HOSTILE}),
                        }
                    ],
                },
                "response": {
                    "choices": [
                        {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                },
                "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
                "stream": False,
                "duration_ms": 5.0,
                "call_stack": [],
            },
            call_id="c1",
        )
    ]


def test_json_tree_escapes_a_hostile_key_and_value(open_report, render_events, console_errors):
    report = render_events(_hostile_json_events())
    page = open_calls_view(open_report, report)
    page.click(".col .seg:not(.unaccounted-seg)")  # open, forcing the tree to render
    page.wait_for_timeout(100)  # give an onerror handler time to fire, if it can

    assert page.locator("img").count() == 0
    assert page.evaluate("window.__pwned === undefined")
    assert not console_errors
    # the payload still renders as literal text, both as the key and the value
    text = page.locator(".col .seg.open .full").inner_text()
    assert text.count(JSON_PAYLOAD_HOSTILE) == 2
