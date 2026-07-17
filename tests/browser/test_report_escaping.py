"""esc() on the values app.js interpolates (#73).

tests/test_html.py already pins the *injection* side: `</script` can never
appear inside the embedded JSON, so content cannot break out of the data block.
Nothing pinned the *rendering* side — app.js builds every view with innerHTML,
so each interpolation is its own escape site.

The payload is `<img src=x onerror=...>` rather than `<script>`: innerHTML
never executes an injected `<script>`, so a `<script>` payload would pass with
esc() deleted and prove nothing. An `<img onerror>` really fires, so these
tests fail for the right reason.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

PAYLOAD = '<img src=x onerror="window.__pwned=1">'
QUOTE_PAYLOAD = '" onmouseover="window.__pwned=1'


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


def _hostile_events() -> list[dict]:
    """Every user-controlled string app.js renders, carrying an executable payload."""
    session = f"session {PAYLOAD}"
    span = "span-1"
    return [
        _event("span_start", session, {"name": f"step {PAYLOAD}"}, span_id=span),
        _event(
            "tag",
            session,
            {
                "name": f"tag {PAYLOAD}",
                "content": f"chunk {PAYLOAD}",
                "source": f"src {QUOTE_PAYLOAD}",
            },
            span_id=span,
        ),
        _event(
            "llm_call",
            session,
            {
                "provider": "openai",
                "api": "chat.completions",
                "request": {
                    "model": f"model {PAYLOAD}",
                    "messages": [
                        {"role": "system", "content": f"system {PAYLOAD}"},
                        {"role": "user", "content": f"chunk {PAYLOAD}"},
                    ],
                },
                "response": {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": f"output {PAYLOAD}"},
                            "finish_reason": f"stop {PAYLOAD}",
                        }
                    ],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
                },
                "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
                "stream": False,
                "duration_ms": 12.5,
                "call_stack": [f"app.py:fn {PAYLOAD}:10"],
            },
            call_id="c1",
            span_id=span,
        ),
        _event("span_end", session, {"name": f"step {PAYLOAD}"}, span_id=span),
        _event(
            "llm_call",
            session,
            {
                "provider": "openai",
                "api": "chat.completions",
                "request": {
                    "model": f"model {PAYLOAD}",
                    "messages": [{"role": "user", "content": f"ask {PAYLOAD}"}],
                },
                "error": {"type": f"ErrType {PAYLOAD}", "message": f"boom {PAYLOAD}"},
                "stream": False,
                "duration_ms": 3.0,
                "call_stack": [],
            },
            call_id="c2",
            ts="2026-06-12T09:01:00+00:00",
        ),
    ]


@pytest.fixture(scope="session")
def hostile_report(render_events) -> str:
    return render_events(_hostile_events())


@pytest.mark.parametrize("view", ["overview", "calls", "chain", "graph"])
def test_injected_markup_never_becomes_an_element(
    open_report, hostile_report, view, console_errors
):
    """The payload would fire on any unescaped interpolation in any view."""
    page = open_report(hostile_report)
    page.click(f'.tab[data-view="{view}"]')
    page.wait_for_timeout(100)  # give an onerror handler time to fire

    assert page.locator("img").count() == 0, f"{view}: injected element rendered"
    assert page.evaluate("window.__pwned === undefined"), f"{view}: payload executed"
    assert not console_errors


def test_hostile_content_renders_as_literal_text(open_report, hostile_report):
    page = open_report(hostile_report)
    page.click('.tab[data-view="calls"]')
    page.wait_for_selector(".seg")

    # the segment body shows the markup as text, character for character
    assert PAYLOAD in page.locator(".col .seg .full").first.inner_text()
    assert PAYLOAD in page.locator(".out .body").inner_text()
    assert PAYLOAD in page.locator(".fn .instr .txt").inner_text()  # system prompt
    assert PAYLOAD in page.locator(".fn .model").inner_text()
    assert PAYLOAD in page.locator(".stackline code").inner_text()


def test_hostile_error_text_renders_as_literal_text(open_report, hostile_report):
    page = open_report(hostile_report)
    page.click('.tab[data-view="calls"]')
    page.wait_for_selector(".callrow[data-i='1']")
    page.click('.callrow[data-i="1"]')
    page.wait_for_selector(".out.error")

    assert PAYLOAD in page.locator(".out.error .head").inner_text()
    assert PAYLOAD in page.locator(".out.error .body").inner_text()


def test_hostile_tag_source_cannot_break_out_of_an_svg_attribute(open_report, hostile_report):
    """The graph writes node ids into `data-id="..."`, and a source node's id is
    `"src:" + the tag's source` — user-controlled, so the quote is an escape site."""
    page = open_report(hostile_report)
    page.click('.tab[data-view="graph"]')
    page.wait_for_selector("#graphwrap .nodebox")

    ids = page.locator("#graphwrap .nodebox").evaluate_all("els => els.map(e => e.dataset.id)")
    # the payload must survive into the attribute intact, or this proves nothing:
    # a broken-out quote would truncate the value instead of round-tripping.
    assert f"src:src {QUOTE_PAYLOAD}" in ids, ids
    assert page.locator("#graphwrap [onmouseover]").count() == 0

    # SVG <text> has no innerText — read textContent
    labels = page.locator("#graphwrap .nodebox text").evaluate_all(
        "els => els.map(e => e.textContent)"
    )
    assert any(PAYLOAD in t for t in labels), "the tag name renders as text"
