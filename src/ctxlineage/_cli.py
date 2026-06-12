"""ctxlineage CLI (installed as `ctxlineage` and `ctxl`)."""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path

import click

from ctxlineage._report import html, normalize


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
def report(directory: str, out: str, open_browser: bool, as_json: bool) -> None:
    """Build the HTML report from recorded events."""
    events_path = Path(directory) / "events.jsonl"
    if not events_path.exists():
        raise click.ClickException(
            f"No events found at {events_path}. "
            "Run your app with ctxlineage.init() first (or pass --dir)."
        )
    events, skipped = normalize.load_events(events_path)
    data = normalize.build_report_data(events)

    if as_json:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return

    out_path = Path(out)
    out_path.write_text(html.render(data), encoding="utf-8")
    summary = f"{data['stats']['calls']} call(s) across {data['stats']['sessions']} session(s)" + (
        f", {skipped} malformed line(s) skipped" if skipped else ""
    )
    click.echo(f"Wrote {out_path} ({summary})")
    if open_browser:
        webbrowser.open(out_path.resolve().as_uri())
