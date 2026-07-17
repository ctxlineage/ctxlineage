# Implementation Plan: automated tests for the report frontend (#73)

> **Status:** implementation plan. The mechanism is settled in #73 (2026-07-17):
> drive a real browser from Python via `pytest-playwright`. This document records
> the *implementation* decisions that the issue left open. It does not re-open the
> mechanism.

## 1. The hole

`src/ctxlineage/_report/assets/app.js` is 734 lines and `style.css` is 218, and
nothing executes either. `tests/test_html.py` asserts the **bundle**: the HTML is
self-contained, the views and the theme toggle exist as strings, assets resolve from
the package, the embedded JSON round-trips, a `<script>` in content cannot break out.
All of that stays — it is cheap, it runs in the main matrix, and it covers the
*build* side. None of it renders a pixel or runs a line of JS.

So today the rendering contract is verified by a human opening the report. #67 was
checked that way. That does not survive to the next change, which is the whole
argument of #73.

## 2. Why a browser and not JSDOM

`CLAUDE.md`: the frontend is **server-rendered by Python — no React/Vite, no node
toolchain**. vitest/jest would walk that decision back for tests alone. Playwright
driven from Python keeps the toolchain Python-only, and tests what actually renders
rather than what an approximation renders.

That distinction is load-bearing for the two things most worth pinning here:

- **`getComputedStyle` on a CSS custom property cascade.** The unaccounted hatch is
  `repeating-linear-gradient(..., currentColor ...)` where `color: var(--muted)` and
  `--muted` is re-declared under `[data-theme="dark"]`. Only a real engine resolves
  that chain to a used color.
- **`<img onerror>` actually firing.** An escaping test is only evidence if the
  unescaped payload would really execute. In a real browser it does.

## 3. Test data: the real pipeline, not hand-built dicts

Both data sources already exist and both go through `build_report_data` →
`html.render`, so the tests exercise the same path `ctxlineage report` does.

| Fixture | Source | `segments_complete` |
| --- | --- | --- |
| live | `examples/generate_demo_events.py` (4 sessions, streaming, error, tagged span) | `true` |
| imported | `ctxlineage import --from claude-code tests/fixtures/claude_code/session_tool_loop.jsonl` | `false` |
| live-like | the imported events with `payload["import"]` removed | `true` |

The imported fixture reproduces #64 **literally**, with no synthetic numbers needed:

| call | reported prompt | segments est. | a segment reads… (buggy) | …(correct) |
| --- | --- | --- | --- | --- |
| `msg_001` | 33,631 | 8 | `100%` | `0%` |
| `msg_002` | 33,753 | 56 | `55%` (31 tok assistant segment) | `0%` |

That 55% is the bug in the issue's own words ("a 4-token segment of a 33k prompt must
not read 50%"), produced by the real importer on a committed fixture. The test asserts
against the whole-prompt share, so it fails the moment the remainder stops being
counted into the denominator.

The **live-like** fixture is the controlled contrast, and it is the sharpest test in
the set. It is the same call with the same wild est-vs-reported ratio (56 vs 33,753)
and *only* the provenance removed. It pins what `normalize._segments_complete`'s
docstring says is load-bearing: the remainder keys on the producer's **declaration**,
never on a token ratio (#63). A ratio-based implementation would pass every other test
in this file and fail this one.

## 4. Skip, don't fail, when the browser is absent

`uv sync` installs `pytest-playwright` (small); it does not download a browser (~90 MB,
the actual cost). So:

- **skip condition** = the chromium executable is not on disk
  (`Path(p.chromium.executable_path).exists()`), not merely "playwright not imported".
- implemented as a `pytest_collection_modifyitems` hook in `tests/browser/conftest.py`.
  A collection hook runs before any fixture, so there is no ordering hazard against
  pytest-playwright's own session-scoped `browser` fixture.
- `importorskip` covers the contributor who installed without the dev group.

This is also what keeps the **main matrix fast without an `--ignore` flag**: it runs
`uv run pytest` unchanged, finds no browser, and skips. The isolation is a property of
the tests, not of the command line — one less thing to keep in sync.

## 5. Serving over HTTP

`file://` is blocked for this page, so a session-scoped `ThreadingHTTPServer` on port 0
in a daemon thread serves a tmp dir; a `serve(html) -> url` helper writes a uniquely
named file and returns its URL. In-process, no subprocess, no fixed port.

## 6. What gets asserted

Priority order from #73. Every assertion is on a **rendered number, resolved color, or
observable side effect** — never on "an element exists".

1. **#64 contract** (imported): remainder slice present; its width matches
   `unaccounted/total`; segment shares are of the whole prompt (`0%`, not `55%`);
   the remainder pseudo-segment reads `33,697 tok · 100%`; provenance panel reports
   `33,697 of 33,753 prompt tok not preserved`.
2. **Live untouched**: across *every* call of the demo report — no `.unaccounted`, no
   `.provenance`; shares proportion against the segment total. Plus the live-like
   anti-ratio pin (§3).
3. **Both themes**: `.windowbar .bar i.unaccounted` resolves `color` **and its
   `background-image` hatch** to `rgb(107,118,130)` light / `rgb(148,160,172)` dark;
   `.provenance` background resolves per theme. Driven through `emulate_media` for the
   OS-follow default and a real `#theme` click for the toggle.
4. **Escaping**: payloads carrying `<img src=x onerror=...>` through segment content,
   model, session id, output, error message and a tag name → no `<img>` in the DOM, no
   `window.__pwned`, text renders literally.
5. **Four views, no console errors**, on both fixtures, with a per-view marker
   assertion so "no errors" cannot pass on a blank page.

## 7. Deliberately out of scope

- **Layout/visual regression** (screenshot diffing). High maintenance, fires on font
  and engine changes, and #73 does not ask for it. The rendered *numbers* and *resolved
  colors* are the contract; pixel positions are not.
- **The chain SVG edge geometry** (`orthPath`, `drawEdges`). Its output is a path `d`
  string whose correctness is a drawing judgement, not a contract. Covered only by
  "renders without console errors".
- **Firefox/WebKit.** One engine catches the regressions #73 names; three triples CI
  cost for no additional contract.
- **`localStorage` theme persistence across reloads.** Covered indirectly; a dedicated
  test would pin `localStorage` internals rather than behaviour.

## 8. CI shape

A separate `browser` job in `.github/workflows/test.yml`: one runner, one Python, one
`uv run playwright install --with-deps chromium`, `uv run pytest tests/browser`. The
main 5-way matrix is untouched and gains no cost. The job is not in `needs` of
anything.

## 9. Constraint

**No production code changes.** This is a test addition (#73 is a test issue). Any real
bug found is reported and pinned, not fixed — a fix is a different merge tier.
