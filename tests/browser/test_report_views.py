"""The four views render, on real data, without console errors (#73).

"No console errors" is worthless on a blank page, so every view also asserts a
marker that only exists once that view has actually drawn its own content —
and the markers are counted against the report data, not merely found.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

VIEW_MARKERS = {
    "overview": ".ov .statcard",
    "calls": ".windowbar .bar i",
    "chain": "#chain .node",
    "graph": "#graphwrap svg .nodebox",
}


@pytest.fixture(params=["live", "imported"])
def report_and_data(request, live_report, live_data, imported_report, imported_data):
    """Both producers: live capture and a reconstructed transcript."""
    if request.param == "live":
        return live_report, live_data
    return imported_report, imported_data


@pytest.mark.parametrize("view", ["overview", "calls", "chain", "graph"])
def test_view_renders_without_console_errors(open_report, report_and_data, view, console_errors):
    report, _ = report_and_data
    page = open_report(report)
    page.click(f'.tab[data-view="{view}"]')
    page.wait_for_selector(VIEW_MARKERS[view])

    assert page.locator(VIEW_MARKERS[view]).count() > 0
    assert page.evaluate("document.body.dataset.view") == view
    assert not console_errors


def test_every_call_renders_without_console_errors(open_report, report_and_data, console_errors):
    """Each call's anatomy, not just the first — segments differ per call."""
    report, data = report_and_data
    calls = [c for s in data["sessions"] for c in s["calls"]]
    page = open_report(report)
    page.click('.tab[data-view="calls"]')

    for i in range(len(calls)):
        page.click(f'.callrow[data-i="{i}"]')
        page.wait_for_selector(f".callrow.sel[data-i='{i}']")
        assert page.locator(".callhead h2").inner_text() == f"call {i + 1}"
        assert not console_errors, f"call {i}: {console_errors}"


def test_chain_view_draws_its_inferred_edges(open_report, live_report, live_data):
    """The edge SVG is generated after layout via requestAnimationFrame — pin
    that it produces paths for a session the backend inferred edges for."""
    index, session = next(
        (i, s)
        for i, s in enumerate(live_data["sessions"])
        if [e for e in s.get("edges", []) if e["kind"] == "output_text"]
    )
    page = open_report(live_report)
    page.click('.tab[data-view="chain"]')
    page.wait_for_selector(f'.sessrow[data-i="{index}"]')
    page.click(f'.sessrow[data-i="{index}"]')
    page.wait_for_selector("#chain .node")
    page.wait_for_function("document.querySelectorAll('svg#edges path').length > 0")

    assert page.locator("#chain .node").count() == len(session["calls"])


def _open_graph(open_report, report: str, index: int):
    page = open_report(report)
    page.click('.tab[data-view="graph"]')
    page.wait_for_selector(f'.sessrow[data-i="{index}"]')
    page.click(f'.sessrow[data-i="{index}"]')
    page.wait_for_selector("#graphwrap svg .nodebox")
    return page


def test_graph_collapses_empty_columns_for_an_untagged_session(open_report, live_report, live_data):
    """#89: untagged is the default experience, not an import artifact - the
    demo's own sessions 1-3 carry no tags (only session 4 does)."""
    index, session = next(
        (i, s) for i, s in enumerate(live_data["sessions"]) if not s.get("elements")
    )
    page = _open_graph(open_report, live_report, index)

    assert page.locator("#graphwrap svg text:has-text('SOURCES')").count() == 0
    assert page.locator("#graphwrap svg text:has-text('CONTEXT ELEMENTS')").count() == 0
    assert page.locator("#graphwrap svg text:has-text('LLM CALLS')").count() == 1
    # the call column reclaims the space the source/element columns left
    # blank - offset from the very edge (not 0) to leave room for a span
    # bracket, which a session can carry even when untagged (span() and
    # tag() are independent APIs).
    call_rect_x = page.locator("#graphwrap svg .nodebox rect").first.get_attribute("x")
    assert call_rect_x == "30"
    assert page.locator("#graphwrap .note", has_text="no tagged context elements").count() == 1
    assert "imported from an agent transcript" not in page.locator("#graphwrap").inner_text()
    assert len(session["calls"]) > 0  # the collapse must not drop any call node
    assert page.locator("#graphwrap svg .nodebox").count() == len(session["calls"])


def test_graph_keeps_three_columns_for_a_tagged_session(open_report, live_report, live_data):
    """Regression guard: the collapse must not fire when there is real
    provenance to show (session 4 in the demo generator is span()/tag()'d)."""
    index, session = next((i, s) for i, s in enumerate(live_data["sessions"]) if s.get("elements"))
    page = _open_graph(open_report, live_report, index)

    assert page.locator("#graphwrap svg text:has-text('SOURCES')").count() == 1
    assert page.locator("#graphwrap svg text:has-text('CONTEXT ELEMENTS')").count() == 1
    assert page.locator("#graphwrap .note", has_text="no tagged context elements").count() == 0
    assert len(session["elements"]) > 0  # guard the guard: fixture must carry elements


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


def _spanned_untagged_events() -> list[dict]:
    """Two calls sharing a span, zero tag() calls - span() and tag() are
    independent APIs, so this combination is real and reachable, unlike a
    "some calls imported, some native" session in the same run."""
    call_payload = {
        "provider": "openai",
        "api": "chat.completions",
        "request": {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        "response": {
            "choices": [
                {"message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
            ]
        },
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        "stream": False,
        "duration_ms": 5.0,
        "call_stack": [],
    }
    return [
        _event(
            "span_start",
            "session-spanned-untagged",
            {"name": "answer_user_query"},
            span_id="sp1",
        ),
        _event("llm_call", "session-spanned-untagged", call_payload, call_id="c1", span_id="sp1"),
        _event(
            "llm_call",
            "session-spanned-untagged",
            call_payload,
            call_id="c2",
            span_id="sp1",
            ts="2026-06-12T09:01:00+00:00",
        ),
    ]


def test_graph_span_bracket_has_no_negative_coordinates_when_untagged(open_report, render_events):
    """Regression: a session can group calls with span() while tagging
    nothing, so it hits the collapsed (no SOURCES/CONTEXT ELEMENTS) layout
    while still drawing a span bracket. The bracket sits left of the call
    column at `COLX.call - 16` - if the collapsed call column sits flush
    against the SVG's own left edge, that bracket (and its label) render at
    a negative x, bleeding past the SVG's origin."""
    report = render_events(_spanned_untagged_events())
    page = _open_graph(open_report, report, 0)

    assert page.locator("#graphwrap svg text:has-text('SOURCES')").count() == 0
    # svg path also matches the <defs> block's arrowhead-marker shapes
    # (rendered first, but nested two levels inside <defs><marker>) - scope
    # to direct children of <svg>, where the bracket paths are added first
    # in source order, ahead of any edge paths.
    path_d = page.locator("#graphwrap svg > path").first.get_attribute("d")
    assert path_d is not None
    # every y-coordinate in this bracket path is already guaranteed positive
    # by the layout (H.call/GAPY are positive, the running cursor starts at
    # 40), so any negative number in the path can only be the bracket's own
    # x - checking every coordinate (not just x) is still a precise test.
    coords = [float(tok) for tok in path_d.replace("M", "").replace("L", "").split()]
    assert min(coords) >= 0, f"bracket path has a negative coordinate: {path_d}"


def test_graph_banner_is_honest_about_import_when_untagged(
    open_report, imported_report, imported_data
):
    """An imported session can never carry tags - the banner must say so
    plainly rather than instructing an impossible span()/tag() fix (#89's own
    follow-up correction: this is the untagged case, worded for import)."""
    page = _open_graph(open_report, imported_report, 0)

    note = page.locator("#graphwrap .note").first.inner_text()
    assert "no tagged context elements" in note
    assert "imported from an agent transcript" in note
    assert "wrap calls in" not in note  # not an actionable instruction here


def test_overview_reports_the_recorded_totals(open_report, live_report, live_data):
    """The home view's headline numbers come from the data, not from a template."""
    page = open_report(live_report)
    nums = page.locator(".ov .cards .statcard .num").all_inner_texts()
    assert nums[0] == f"{live_data['stats']['calls']:,}"
    assert nums[1] == f"{live_data['stats']['sessions']:,}"


def test_filter_narrows_the_call_list(open_report, live_report, live_data):
    """The filter is pure app.js — nothing else exercises it."""
    calls = [c for s in live_data["sessions"] for c in s["calls"]]
    page = open_report(live_report)
    page.click('.tab[data-view="calls"]')
    assert page.locator(".callrow").count() == len(calls)

    page.fill("#filter", "no-such-model-anywhere")
    page.wait_for_function("document.querySelectorAll('.callrow').length === 0")
    assert page.locator("#fcount").inner_text() == f"0 / {len(calls)} calls match"

    page.fill("#filter", "")
    page.wait_for_function(
        "n => document.querySelectorAll('.callrow').length === n", arg=len(calls)
    )
