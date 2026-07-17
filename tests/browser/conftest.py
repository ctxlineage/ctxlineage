"""Browser-test fixtures: report data through the real pipeline, served over HTTP.

Skip, never fail, when the browser is absent (#73). `uv sync` installs
pytest-playwright but not the ~90 MB chromium build, so the skip keys on the
executable being on disk rather than on the import working. That is also what
keeps the main test matrix fast: it runs `uv run pytest` unchanged, finds no
browser, and skips this directory — the isolation is a property of the tests,
not of the command line.
"""

from __future__ import annotations

import functools
import http.server
import itertools
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ctxlineage._report import html, tokens
from ctxlineage._report.normalize import build_report_data

REPO_ROOT = Path(__file__).parent.parent.parent
DEMO_SCRIPT = REPO_ROOT / "examples" / "generate_demo_events.py"
TRANSCRIPT = REPO_ROOT / "tests" / "fixtures" / "claude_code" / "session_tool_loop.jsonl"


def _chromium_missing() -> str | None:
    """Return a skip reason, or None when a browser can actually be driven."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "playwright is not installed (dev group: `uv sync`)"
    try:
        with sync_playwright() as p:
            if not Path(p.chromium.executable_path).exists():
                return "chromium is not installed — run `uv run playwright install chromium`"
    except Exception as exc:  # driver missing/unusable: still a skip, not a failure
        return f"playwright driver unavailable: {exc}"
    return None


def pytest_collection_modifyitems(config, items):
    """Two things, both about keeping the browser suite well-behaved in-session.

    1. Skip the whole directory when there is no browser to drive — a collection
       hook, not a fixture, so it runs before pytest-playwright's own
       session-scoped `browser` fixture and cannot race it.
    2. Sort the browser items to the very end of the run. pytest-playwright's
       sync API drives the browser through a greenlet event loop; if an
       `asyncio_mode = auto` test tears down its pytest-asyncio runner *after*
       one has run in the same session, the teardown hits "Cannot run the event
       loop while another loop is running". Running the browser tests last keeps
       `uv run pytest` (whole suite, browser present) green. The dedicated CI
       job runs `pytest tests/browser` alone and is unaffected either way.
    """
    here = Path(__file__).parent

    def is_browser(item) -> bool:
        return here in Path(str(item.fspath)).parents

    items.sort(key=is_browser)  # stable: browser items move to the end, order kept

    reason = _chromium_missing()
    if reason is not None:
        mark = pytest.mark.skip(reason=reason)
        for item in items:
            if is_browser(item):
                item.add_marker(mark)


def report_of(events: list[dict]) -> dict:
    """build_report_data with token estimation pinned to the offline fallback.

    The suite's autouse `_offline_token_estimation` is function-scoped and these
    fixtures are session-scoped, so the patch is applied here rather than
    inherited — otherwise the rendered token figures would depend on whether
    tiktoken happened to be downloadable, and the assertions below are exact.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tokens, "_encoding_for", lambda model: None)
        return build_report_data(events)


# ---------- report data: both fixtures go through build_report_data + render ----------


@pytest.fixture(scope="session")
def render_events():
    """events -> a rendered report, via the same path `ctxlineage report` takes."""

    def _render(events: list[dict]) -> str:
        return html.render(report_of(events))

    return _render


@pytest.fixture(scope="session")
def live_events(tmp_path_factory) -> list[dict]:
    """Live capture: the demo generator writes events in the shape openai's
    instrumentation produces (4 sessions, streaming, an error, a tagged span)."""
    out = tmp_path_factory.mktemp("demo")
    subprocess.run([sys.executable, str(DEMO_SCRIPT), str(out)], check=True, timeout=120)
    return [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]


@pytest.fixture(scope="session")
def imported_events(tmp_path_factory) -> list[dict]:
    """Import: a reconstructed transcript, so `segments_complete` is false and the
    reported prompt (33k) dwarfs what the segments can account for (46 est)."""
    from click.testing import CliRunner

    from ctxlineage._cli import main

    out = tmp_path_factory.mktemp("imported")
    result = CliRunner().invoke(
        main, ["import", "--from", "claude-code", str(TRANSCRIPT), "--dir", str(out)]
    )
    assert result.exit_code == 0, result.output
    return [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]


@pytest.fixture(scope="session")
def live_data(live_events) -> dict:
    return report_of(live_events)


@pytest.fixture(scope="session")
def imported_data(imported_events) -> dict:
    data = report_of(imported_events)
    calls = [c for s in data["sessions"] for c in s["calls"]]
    assert calls and not any(c["segments_complete"] for c in calls), "fixture must read as import"
    return data


@pytest.fixture(scope="session")
def live_like_data(imported_events) -> dict:
    """The imported events with their provenance stripped — i.e. the same calls,
    the same 46-est-vs-33,753-reported ratio, but declaring themselves live.

    The controlled contrast for the remainder: only the declaration differs.
    """
    events = json.loads(json.dumps(imported_events))  # deep copy: session-scoped source
    for event in events:
        (event.get("payload") or {}).pop("import", None)
    data = report_of(events)
    calls = [c for s in data["sessions"] for c in s["calls"]]
    assert calls and all(c["segments_complete"] for c in calls), "fixture must read as live"
    return data


@pytest.fixture(scope="session")
def live_report(live_data) -> str:
    return html.render(live_data)


@pytest.fixture(scope="session")
def imported_report(imported_data) -> str:
    return html.render(imported_data)


@pytest.fixture(scope="session")
def live_like_report(live_like_data) -> str:
    return html.render(live_like_data)


# ---------- serving: file:// is blocked for this page ----------


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output readable
        pass


@pytest.fixture(scope="session")
def _server(tmp_path_factory):
    root = tmp_path_factory.mktemp("served")
    handler = functools.partial(_QuietHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture
def serve(_server):
    """serve(html_text) -> url. Writes a uniquely named file into the served dir."""
    root, base = _server
    counter = itertools.count()

    def _serve(html_text: str) -> str:
        name = f"report-{next(counter)}-{threading.get_ident()}.html"
        (root / name).write_text(html_text, encoding="utf-8")
        return f"{base}/{name}"

    return _serve


@pytest.fixture
def console_errors(page):
    """Collected console errors + uncaught exceptions. Attached before navigating."""
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    return errors


@pytest.fixture
def open_report(page, serve, console_errors):
    """Navigate to a rendered report; fail loudly on any console error at load."""

    def _open(html_text: str, color_scheme: str = "light"):
        page.emulate_media(color_scheme=color_scheme)  # theme default reads matchMedia at load
        page.goto(serve(html_text))
        page.wait_for_selector("body[data-view]")  # render() has run
        assert not console_errors, console_errors
        return page

    return _open
