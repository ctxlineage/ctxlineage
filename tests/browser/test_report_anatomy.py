"""The #64 rendering contract, in a real browser (#73).

#64: the anatomy proportioned a partial prompt as if it were whole. An imported
transcript cannot recover the system prompt or the tool definitions, but they
were in the window and cost their tokens — so a segment's share must be taken
against the *whole* prompt, and the part no one can name must be drawn rather
than silently dropped from the denominator.

The fixture reproduces that literally, through the real importer: `msg_002`
reports a 33,753-token prompt whose segments account for 46 est. tokens. Under
the bug its 27-token assistant segment reads **59% of input**. It must read 0%.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest

pytest.importorskip("playwright.sync_api")


def calls_of(data: dict) -> list[dict]:
    return [c for s in data["sessions"] for c in s["calls"]]


def prompt_tokens(call: dict) -> int:
    return call["usage"]["prompt_tokens"] if call.get("usage") else call["input_tokens_est"]


def seg_total(call: dict) -> int:
    return sum(g["tokens_est"] for g in call["segments"])


def unaccounted(call: dict) -> int:
    """The contract's own arithmetic — deliberately not app.js's."""
    if call.get("segments_complete") is not False:
        return 0
    return max(prompt_tokens(call) - seg_total(call), 0)


def share_text(tok: int, total: int) -> str:
    """Mirror of app.js's `${fmt(tok)} tok · ${pct.toFixed(0)}%` (unaccounted-seg only)."""
    pct = Decimal(100 * tok) / Decimal(total)
    return f"{tok:,} tok · {pct.quantize(Decimal('1'), rounding=ROUND_HALF_UP)}%"


def max_share_pct(segments: list[dict], denom: int) -> Decimal:
    return max(Decimal(100 * g["tokens_est"]) / Decimal(denom) for g in segments)


def prompt_share_text(tok: int, total: int, recovered_total: int | None = None) -> str:
    """Mirror of app.js's #90 dual-basis segment/instructions share label -
    real segment cards and the INSTRUCTIONS panel, not the unaccounted-seg
    placeholder (which stays plain `share_text`, untouched by #90)."""
    pct = Decimal(100 * tok) / Decimal(total)
    text = f"{tok:,} tok · {pct.quantize(Decimal('1'), rounding=ROUND_HALF_UP)}% of prompt"
    if recovered_total:
        pct2 = Decimal(100 * tok) / Decimal(recovered_total)
        text += f" · {pct2.quantize(Decimal('1'), rounding=ROUND_HALF_UP)}% of recovered"
    return text


def open_calls_view(open_report, html_text: str, index: int = 0):
    page = open_report(html_text)
    page.click('.tab[data-view="calls"]')
    page.wait_for_selector(".windowbar")
    if index:
        page.click(f'.callrow[data-i="{index}"]')
        page.wait_for_selector(f".callrow.sel[data-i='{index}']")
    return page


# ---------------- 1. the #64 contract: segments_complete === false ----------------


def test_imported_call_draws_the_unaccounted_remainder(open_report, imported_report, imported_data):
    """The tokens the transcript cannot name are drawn, not dropped."""
    call = calls_of(imported_data)[1]
    assert unaccounted(call) > 0, "fixture no longer exercises the remainder"
    page = open_calls_view(open_report, imported_report, index=1)

    assert page.locator(".windowbar .bar i.unaccounted").count() == 1
    assert page.locator(".seg.unaccounted-seg").count() == 1
    assert page.locator(".seg.unaccounted-seg .share").inner_text() == share_text(
        unaccounted(call), prompt_tokens(call)
    )


def test_imported_segment_share_is_of_the_whole_prompt(open_report, imported_report, imported_data):
    """#64 itself: a 27-token segment of a 33,753-token prompt reads 0%, not 59%."""
    call = calls_of(imported_data)[1]
    total = seg_total(call) + unaccounted(call)
    assert total == prompt_tokens(call)

    page = open_calls_view(open_report, imported_report, index=1)
    rendered = page.locator(".col .seg:not(.unaccounted-seg) .share").all_inner_texts()

    # #90: at this coverage every segment's real-prompt share also grows a
    # second, explicitly-labelled "of recovered" clause - it must not silently
    # replace the honest of-prompt share the #64 contract established.
    honest = [prompt_share_text(g["tokens_est"], total, seg_total(call)) for g in call["segments"]]
    buggy = [share_text(g["tokens_est"], seg_total(call)) for g in call["segments"]]
    assert rendered == honest
    # guard the guard: the two formulas must disagree by an order of magnitude
    # on this fixture (the wrong, too-small seg_total denominator inflates
    # every share), or the assertion above proves nothing. Not a hardcoded
    # percentage - #90 changes segment token counts on this same fixture
    # (a replayed assistant turn's stripped-thinking placeholder shrinks),
    # so a fixed magic number goes stale for reasons unrelated to #64 itself.
    buggy_max = max_share_pct(call["segments"], seg_total(call))
    honest_max = max_share_pct(call["segments"], total)
    assert honest != buggy
    assert buggy_max > honest_max * 10


def test_unaccounted_slice_is_drawn_to_scale(open_report, imported_report, imported_data):
    """Not just present — sized against the whole prompt (99.9% of the input bar)."""
    call = calls_of(imported_data)[1]
    expected = 100 * unaccounted(call) / prompt_tokens(call)
    assert expected > 99

    page = open_calls_view(open_report, imported_report, index=1)
    ratio = page.evaluate("""() => {
      const i = document.querySelector('.windowbar .bar i.unaccounted');
      return 100 * i.getBoundingClientRect().width / i.parentElement.getBoundingClientRect().width;
    }""")
    assert ratio == pytest.approx(expected, abs=0.5)


def test_imported_call_shows_the_provenance_panel(open_report, imported_report, imported_data):
    call = calls_of(imported_data)[1]
    page = open_calls_view(open_report, imported_report, index=1)

    panel = page.locator(".provenance")
    assert panel.count() == 1
    label = panel.locator(".lbl").inner_text()
    assert "imported from claude-code" in label
    assert f"{unaccounted(call):,} of {prompt_tokens(call):,} prompt tok not preserved" in label

    body = panel.locator(".txt").inner_text()
    for field in ("system_prompt", "tool_definitions", "reasoning_text"):
        assert field in body
    assert "1 reasoning block(s) kept only as a signature" in body


# ---------------- 2. live capture is untouched ----------------


def test_live_capture_never_shows_a_remainder_or_provenance(open_report, live_report, live_data):
    """Every call of the demo report — this is what keeps the README screenshots honest."""
    page = open_calls_view(open_report, live_report)
    total_calls = len(calls_of(live_data))
    assert total_calls >= 6

    for i in range(total_calls):
        page.click(f'.callrow[data-i="{i}"]')
        page.wait_for_selector(f".callrow.sel[data-i='{i}']")
        assert page.locator(".windowbar .bar i.unaccounted").count() == 0, f"call {i}"
        assert page.locator(".seg.unaccounted-seg").count() == 0, f"call {i}"
        assert page.locator(".provenance").count() == 0, f"call {i}"


def test_live_segment_shares_proportion_against_the_segments(open_report, live_report, live_data):
    """Live segments *are* the whole prompt, so their shares sum over themselves."""
    index, call = next((i, c) for i, c in enumerate(calls_of(live_data)) if len(c["segments"]) >= 2)
    page = open_calls_view(open_report, live_report, index=index)

    rest = [g for g in call["segments"] if g["role"] != "system"]
    # #90: total === segTotal for a complete-segments call, so no "of recovered"
    # clause appears - only the "of prompt" wording is new here.
    expected = [prompt_share_text(g["tokens_est"], seg_total(call)) for g in rest]
    assert page.locator(".col .seg:not(.unaccounted-seg) .share").all_inner_texts() == expected


def test_the_remainder_keys_on_the_declaration_not_a_token_ratio(
    open_report, live_like_report, live_like_data
):
    """The same 33k-reported / 46-est. call, declaring itself live: no remainder.

    normalize._segments_complete says this is load-bearing (#63): a ratio
    conflates an estimator disagreeing with the provider's tokenizer against
    content that is structurally missing. An implementation that inferred the
    remainder from est-vs-reported would pass every other test here and fail
    this one.
    """
    call = calls_of(live_like_data)[1]
    assert prompt_tokens(call) > 100 * seg_total(call), "fixture must keep the wild ratio"

    page = open_calls_view(open_report, live_like_report, index=1)
    assert page.locator(".windowbar .bar i.unaccounted").count() == 0
    assert page.locator(".seg.unaccounted-seg").count() == 0
    assert page.locator(".provenance").count() == 0

    rendered = page.locator(".col .seg:not(.unaccounted-seg) .share").all_inner_texts()
    expected = [prompt_share_text(g["tokens_est"], seg_total(call)) for g in call["segments"]]
    assert rendered == expected
    # A buggy implementation that inferred the remainder from the wild
    # reported-vs-segments ratio would suppress every share toward ~0%, same
    # as the imported case. Prove at least one share stayed non-trivially
    # large - i.e. genuinely against seg_total, not silently against the huge
    # reported prompt_tokens. Not a hardcoded percentage: #90 changes segment
    # token counts on this fixture (a stripped-thinking placeholder shrinks),
    # so a fixed magic number goes stale for reasons unrelated to #63 itself.
    assert max_share_pct(call["segments"], seg_total(call)) >= 20, (
        "live shares are of the segments, and stay so"
    )
