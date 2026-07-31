"""Chain lineage: the arrow targets the segment the match actually landed in
(#93), in a real browser.

Before this fix, the arrow always terminated on the aggregated
assistant/"fed" chip regardless of which kind the match landed in - a
tool-kind match rendered as if it landed in the assistant kind. The fixture
constructs exactly that case: call 1's output is fed back as a `tool`-role
segment on call 2, not replayed as conversation history (which would land in
an `assistant`-kind segment instead, the case the other Chain browser test
already covers).
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

ANSWER = "The webhook secret rotates every 90 days, verified against the audit log."


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


def _tool_kind_edge_events() -> list[dict]:
    return [
        _event(
            "llm_call",
            "session-tool-edge",
            {
                "provider": "openai",
                "api": "chat.completions",
                "request": {
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "How often does it rotate?"}],
                },
                "response": {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": ANSWER},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 15, "total_tokens": 24},
                },
                "usage": {"prompt_tokens": 9, "completion_tokens": 15, "total_tokens": 24},
                "stream": False,
                "duration_ms": 10.0,
                "call_stack": [],
            },
            call_id="c1",
        ),
        _event(
            "llm_call",
            "session-tool-edge",
            {
                "provider": "openai",
                "api": "chat.completions",
                "request": {
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "user", "content": "log this and confirm"},
                        # a real assistant-kind segment too, so the "fed" chip
                        # genuinely exists as a competing (wrong) target -
                        # without it, tool would be the only chip either way
                        # and the test would prove nothing about targeting.
                        {"role": "assistant", "content": "Unrelated prior turn, not the match."},
                        {"role": "tool", "name": "log_answer", "content": ANSWER},
                    ],
                },
                "response": {
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "Logged."},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 3, "total_tokens": 33},
                },
                "usage": {"prompt_tokens": 30, "completion_tokens": 3, "total_tokens": 33},
                "stream": False,
                "duration_ms": 12.0,
                "call_stack": [],
            },
            call_id="c2",
            ts="2026-06-12T09:01:00+00:00",
        ),
    ]


@pytest.fixture(scope="session")
def tool_edge_report(render_events) -> str:
    return render_events(_tool_kind_edge_events())


def test_arrow_targets_the_tool_chip_when_the_match_landed_there(open_report, tool_edge_report):
    page = open_report(tool_edge_report)
    page.click('.tab[data-view="chain"]')
    page.wait_for_selector("#chain .node")
    # svg#edges path also matches the <defs> block's arrowhead-marker shapes
    # (rendered first in DOM order) - scope to the actual edge path, which is
    # wrapped in its own <g> alongside the label; the marker defs are not.
    page.wait_for_function("document.querySelectorAll('svg#edges g path').length > 0")

    tool_chip = page.locator('.node[data-n="1"] .chips .chip[data-kind="tool"]')
    fed_chip = page.locator('.node[data-n="1"] .chips .chip.fed')
    assert tool_chip.count() == 1
    assert fed_chip.count() == 1  # both exist - the assertion below proves WHICH one is targeted

    path_end = page.evaluate("""() => {
        const p = document.querySelector('svg#edges g path');
        const pt = p.getPointAtLength(p.getTotalLength());
        return [pt.x, pt.y];
    }""")
    wrap = page.locator("#wrap").bounding_box()
    tool_box = tool_chip.bounding_box()
    fed_box = fed_chip.bounding_box()

    def dist_to_box(px, py, box):
        cx, cy = box["x"] + box["width"] / 2 - wrap["x"], box["y"] + box["height"] / 2 - wrap["y"]
        return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5

    d_tool = dist_to_box(*path_end, tool_box)
    d_fed = dist_to_box(*path_end, fed_box)
    assert d_tool < d_fed, f"arrow endpoint {path_end} is closer to the fed chip than the tool chip"
