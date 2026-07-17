"""ctxlineage CLI (installed as `ctxlineage` and `ctxl`)."""

from __future__ import annotations

import json
import re
import webbrowser
from pathlib import Path

import click

from ctxlineage import _contract
from ctxlineage._report import html, normalize, redact


@click.group()
@click.version_option(package_name="ctxlineage")
def main() -> None:
    """See exactly what context your LLM calls consumed."""


@main.command()
@click.option(
    "--dir",
    "-d",
    "directory",
    default=".ctxlineage",
    show_default=True,
    help="Directory containing events.jsonl.",
)
@click.option(
    "--out",
    "-o",
    default="ctxlineage-report.html",
    show_default=True,
    help="Output HTML path.",
)
@click.option("--open", "open_browser", is_flag=True, help="Open the report in a browser.")
@click.option("--json", "as_json", is_flag=True, help="Print report data as JSON instead of HTML.")
@click.option(
    "--redact",
    "redact_patterns",
    multiple=True,
    metavar="PATTERN",
    help="Regex; every match in prompt/output text becomes [redacted]. Repeatable. "
    "Applied after matching, so token counts and match rates stay honest.",
)
def report(
    directory: str,
    out: str,
    open_browser: bool,
    as_json: bool,
    redact_patterns: tuple[str, ...],
) -> None:
    """Build the HTML report from recorded events."""
    events_path = Path(directory) / "events.jsonl"
    if not events_path.exists():
        raise click.ClickException(
            f"No events found at {events_path}. "
            "Run your app with ctxlineage.init() first (or pass --dir)."
        )
    events, skipped = normalize.load_events(events_path)
    data = normalize.build_report_data(events)

    redacted = 0
    if redact_patterns:
        try:
            redacted = redact.apply(data, list(redact_patterns))
        except re.error as exc:
            raise click.ClickException(f"Invalid --redact pattern {exc.pattern!r}: {exc}") from exc

    if as_json:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    out_path = Path(out)
    out_path.write_text(html.render(data), encoding="utf-8")
    summary = (
        f"{data['stats']['calls']} call(s) across {data['stats']['sessions']} session(s)"
        + (f", {skipped} malformed line(s) skipped" if skipped else "")
        + (f", {redacted} match(es) redacted" if redact_patterns else "")
    )
    click.echo(f"Wrote {out_path} ({summary})")
    if open_browser:
        webbrowser.open(out_path.resolve().as_uri())


@main.command()
@click.option(
    "--dir",
    "-d",
    "directory",
    default=".ctxlineage",
    show_default=True,
    help="Directory containing events.jsonl.",
)
@click.option(
    "--config",
    "-c",
    "config_path",
    default="ctxlineage.toml",
    show_default=True,
    help="Assertion config (TOML).",
)
def test(directory: str, config_path: str) -> None:
    """Assert context contracts over recorded events.

    Exits non-zero when a hard gate fails, so it works as a CI gate. Warnings
    are advisory and never fail the build: a rule warns instead of gating where
    its evidence is inferred rather than exact.
    """
    try:
        rules = _contract.load(config_path)
    except _contract.ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    events_path = Path(directory) / "events.jsonl"
    if not events_path.exists():
        raise click.ClickException(
            f"No events found at {events_path}. "
            "Run your app with ctxlineage.init() first (or pass --dir)."
        )
    events, skipped = normalize.load_events(events_path)
    data = normalize.build_report_data(events)
    if not data["stats"]["calls"]:
        # Passing over an empty capture would report green for a broken capture.
        raise click.ClickException(
            f"No LLM calls recorded in {events_path} - nothing to assert."
            + (f" ({skipped} malformed line(s) skipped.)" if skipped else "")
        )

    findings = _contract.run(data, rules)
    for finding in findings:
        click.echo(f"{finding.severity.upper():<4}  {finding.rule}: {finding.message}")

    counts = {
        level: sum(1 for f in findings if f.severity == level) for level in ("fail", "warn", "skip")
    }
    tail = f"{counts['warn']} warning(s), {counts['skip']} skipped"
    scope = f"{len(rules)} assertion(s) over {data['stats']['calls']} call(s)"
    if _contract.has_failures(findings):
        click.echo(f"{counts['fail']} check(s) failed - {scope}, {tail}")
        raise SystemExit(1)
    click.echo(f"All {scope} passed - {tail}")
