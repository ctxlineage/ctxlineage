"""Light and dark both resolve (#73), which CLAUDE.md makes mandatory.

The unaccounted hatch is the case worth pinning: style.css paints it with
`repeating-linear-gradient(..., currentColor ...)` over `color: var(--muted)`,
and `--muted` is re-declared under `[data-theme="dark"]`. Nothing short of a
real engine resolves that chain, which is the argument for a browser over a
JSDOM approximation. These are the exact values #67 was verified against by hand.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

MUTED = {"light": "rgb(107, 118, 130)", "dark": "rgb(148, 160, 172)"}
SRC_BG = {"light": "rgb(238, 242, 245)", "dark": "rgb(45, 52, 61)"}


def open_calls_view(open_report, html_text, color_scheme):
    page = open_report(html_text, color_scheme=color_scheme)
    page.click('.tab[data-view="calls"]')
    page.wait_for_selector(".callrow[data-i='1']")
    page.click('.callrow[data-i="1"]')
    page.wait_for_selector(".windowbar .bar i.unaccounted")
    return page


def computed(page, selector, prop):
    return page.evaluate(
        "([s, p]) => getComputedStyle(document.querySelector(s)).getPropertyValue(p)",
        [selector, prop],
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_theme_follows_the_os_by_default(open_report, imported_report, scheme):
    page = open_calls_view(open_report, imported_report, scheme)
    assert page.evaluate("document.body.dataset.theme") == scheme


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_unaccounted_hatch_resolves_per_theme(open_report, imported_report, scheme):
    """Both the color and the gradient that actually paints it."""
    page = open_calls_view(open_report, imported_report, scheme)
    assert computed(page, ".windowbar .bar i.unaccounted", "color") == MUTED[scheme]

    # currentColor inside the hatch resolves to the theme's muted — i.e. the
    # stripes are really painted in it, not merely inherited by the box.
    hatch = computed(page, ".windowbar .bar i.unaccounted", "background-image")
    assert "repeating-linear-gradient" in hatch
    assert hatch.count(MUTED[scheme]) == 2, hatch
    assert MUTED["dark" if scheme == "light" else "light"] not in hatch


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_provenance_panel_resolves_per_theme(open_report, imported_report, scheme):
    page = open_calls_view(open_report, imported_report, scheme)
    assert computed(page, ".provenance", "background-color") == SRC_BG[scheme]


def test_toggle_repaints_the_hatch_in_the_other_theme(open_report, imported_report):
    """The user-facing path: click the toggle, the hatch follows."""
    page = open_calls_view(open_report, imported_report, "light")
    assert computed(page, ".windowbar .bar i.unaccounted", "color") == MUTED["light"]

    page.click("#theme")
    page.wait_for_function("document.body.dataset.theme === 'dark'")
    assert computed(page, ".windowbar .bar i.unaccounted", "color") == MUTED["dark"]
    assert MUTED["dark"] in computed(page, ".windowbar .bar i.unaccounted", "background-image")
    assert computed(page, ".provenance", "background-color") == SRC_BG["dark"]

    page.click("#theme")
    page.wait_for_function("document.body.dataset.theme === 'light'")
    assert computed(page, ".windowbar .bar i.unaccounted", "color") == MUTED["light"]
