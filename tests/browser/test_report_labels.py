"""Per-call action labels for imported agent-loop sessions (#88), in a real
browser.

The bug: every call in one span shared the identical label (the human turn's
sentence) - 38 consecutive calls in the real trial that prompted this. The
fixture reproduces the pattern in miniature: 4 calls, one span, Read -> Edit
-> Bash -> a final answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

REPO_ROOT = Path(__file__).parent.parent.parent
TRANSCRIPT = REPO_ROOT / "tests" / "fixtures" / "claude_code" / "session_multi_tool_loop.jsonl"


@pytest.fixture(scope="session")
def loop_events() -> list[dict]:
    from ctxlineage._import.claude_code import import_transcript

    return import_transcript(TRANSCRIPT)


@pytest.fixture(scope="session")
def loop_report(render_events, loop_events) -> str:
    return render_events(loop_events)


def test_overview_heaviest_calls_are_not_all_the_same_label(open_report, loop_report):
    """The Overview view's ranked lists (HEAVIEST CALLS) are exactly where the
    bug read as a rendering glitch - four rows, one repeated title.

    HEAVIEST CALLS and WINDOW PRESSURE both render `.toprow` rows with no
    distinguishing wrapper; HEAVIEST CALLS always comes first in DOM order
    and this 4-call fixture fills it exactly, so the first 4 rows are it.
    """
    page = open_report(loop_report)
    page.wait_for_selector(".toprow")
    all_rows = page.locator(".toprow .st").all_inner_texts()
    labels = [row.split("()")[0].strip() for row in all_rows[:4]]
    assert len(labels) == 4
    assert len(set(labels)) > 1, f"every row still shares one label: {labels}"


def test_fn_card_stepname_shows_distinct_actions(open_report, loop_report):
    """The Calls-view fn card (`.stepname`) is `stepOf(c)` rendered as
    `NAME()` - the same field Overview and Chain read, so fixing it here
    fixes all three per #88's own analysis."""
    page = open_report(loop_report)
    page.click('.tab[data-view="calls"]')
    page.wait_for_selector(".stepname")

    names = []
    for i in range(4):
        page.click(f'.callrow[data-i="{i}"]')
        page.wait_for_selector(f".callrow.sel[data-i='{i}']")
        names.append(page.locator(".stepname").inner_text())
    assert names == ["Read()", "Read()", "Edit()", "Bash()"]


def test_sidebar_rows_show_distinct_labels_not_just_model(open_report, loop_report):
    """#91's own claim: the label fix improves the Calls sidebar too, not just
    Overview/Chain - "94 entries that all read claude-fable-5 + timestamp, so
    the only way to navigate is by token count." The sidebar has its own
    template (not stepOf-driven before this fix); verify it directly rather
    than trusting the fn-card test above to cover it by implication."""
    page = open_report(loop_report)
    page.click('.tab[data-view="calls"]')
    page.wait_for_selector(".callrow")

    primaries = page.locator(".callrow .m .model").all_inner_texts()
    assert primaries == ["Read", "Read", "Edit", "Bash"]
    # the model name is not lost - demoted to the sub line, not dropped
    assert all("claude-fable-5" in sub for sub in page.locator(".callrow .sub").all_inner_texts())


def test_span_row_still_carries_the_real_span_name(open_report, loop_report):
    """#88 splits step (per-call) from span (per-episode) - the fn card's
    'span' row must still show the human turn's own label, not the action."""
    page = open_report(loop_report)
    page.click('.tab[data-view="calls"]')
    page.click('.callrow[data-i="2"]')  # msg_003, action="Edit"
    page.wait_for_selector(".callrow.sel[data-i='2']")

    rows = page.locator(".fn .row").all_inner_texts()
    span_row = next((r for r in rows if r.lower().startswith("span")), None)
    assert span_row is not None, "the span row must render when action != span name"
    assert "fix the failing test" in span_row.lower()
