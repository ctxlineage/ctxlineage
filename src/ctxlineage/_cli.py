"""ctxlineage CLI (installed as `ctxlineage` and `ctxl`)."""

from __future__ import annotations

import json
import re
import webbrowser
from pathlib import Path

import click

from ctxlineage import _contract
from ctxlineage._events import EventWriter
from ctxlineage._import import ADAPTERS
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


def _imported_session_ids(events_path: Path) -> set:
    if not events_path.exists():
        return set()
    events, _ = normalize.load_events(events_path)
    return {
        e.get("session_id")
        for e in events
        if (e.get("payload") or {}).get("import", {}).get("source")
    }


@main.command("import")  # `import` is a keyword, hence the function name
@click.argument(
    "transcript",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--from",
    "source",
    required=True,
    type=click.Choice(sorted(ADAPTERS)),
    help="Which agent's local artifact to read.",
)
@click.option("--session", help="Session id. Default: the newest session for this directory.")
@click.option(
    "--dir",
    "-d",
    "directory",
    default=".ctxlineage",
    show_default=True,
    help="Directory holding events.jsonl.",
)
@click.option("--dry-run", is_flag=True, help="Report what would be imported; write nothing.")
def import_(
    transcript: Path | None,
    source: str,
    session: str | None,
    directory: str,
    dry_run: bool,
) -> None:
    """Import a coding-agent session into the event log.

    Reads a transcript the agent already wrote to disk — nothing is proxied,
    injected, or sent anywhere. Then `ctxlineage report` renders it like any
    captured session.
    """
    adapter = ADAPTERS[source]
    path = transcript or adapter.find_transcript(session, cwd=str(Path.cwd()))
    if path is None:
        raise click.ClickException(
            f"No {source} transcript found for session {session!r}."
            if session
            else f"No {source} transcript found for {Path.cwd()}. Pass a path or --session <id>."
        )

    records, skipped = adapter.read_transcript(path)
    events = adapter.to_events(records, path=path)
    calls = [e for e in events if e["event_type"] == "llm_call"]
    if not calls:
        raise click.ClickException(f"No LLM calls found in {path}.")

    session_id = calls[0]["session_id"]
    events_path = Path(directory) / "events.jsonl"
    if session_id in _imported_session_ids(events_path):
        raise click.ClickException(
            f"Session {session_id} is already in {events_path}; importing again "
            "would double-count every call. Use --dir to import elsewhere."
        )

    spans = len({e["span_id"] for e in events if e["event_type"] == "span_start"})
    if dry_run:
        click.echo(f"Would import {len(calls)} call(s), {spans} span(s) from {path}")
    else:
        writer = EventWriter(directory)
        for event in events:
            writer.write(event)
        click.echo(f"Imported {len(calls)} call(s), {spans} span(s) from {path} into {writer.path}")
    if skipped:
        click.echo(f"  {skipped} malformed line(s) skipped")

    # Honest data: say which numbers are the agent's own and which are ours, and
    # size what the transcript simply did not keep rather than quietly absorbing it.
    metas = [c["payload"]["import"] for c in calls]
    reconstructed = sum(1 for m in metas if m["usage"] == "reconstructed")
    click.echo(
        f"  usage: reconstructed from the transcript for {reconstructed}/{len(calls)} call(s)"
    )
    click.echo("  segment token counts: estimated")

    # Coverage per call, never summed: every prompt re-sends the whole
    # conversation, so adding the gaps up would describe no real quantity.
    shares = [
        m["prompt_tokens_reconstructed_est"] / m["prompt_tokens_reported"]
        for m in metas
        if m.get("prompt_tokens_reported")
    ]
    if shares:
        click.echo(
            f"  reconstructed segments cover {min(shares):.0%}-{max(shares):.0%} of each "
            "call's real prompt tokens; the rest is the system prompt, the tool "
            "definitions and reasoning text, which the transcript does not preserve."
        )
    stripped = sum(
        1
        for call in calls
        for block in call["payload"]["response"]["content"]
        if block.get("type") == "thinking" and not block.get("thinking")
    )
    if stripped:
        click.echo(
            f"  {stripped} reasoning block(s) were recorded with their text stripped "
            "(the transcript keeps only the signature)."
        )
    if not dry_run:
        click.echo("Run `ctxlineage report` to render it.")
