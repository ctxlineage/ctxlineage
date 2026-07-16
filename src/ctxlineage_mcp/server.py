"""Read-only MCP server over the ctxlineage event log (PLAN.md 4c, stdio).

Every number comes from ctxlineage._report.normalize — the report pipeline is
the single source of truth for segments, elements, and edges; this layer only
selects and summarizes from that contract. The one write this server performs
is generate_report's HTML artifact, same as the CLI.
"""

from __future__ import annotations

import copy
from pathlib import Path

import click

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "The ctxlineage MCP server needs the 'mcp' extra: pip install 'ctxlineage[mcp]'"
    ) from exc

from ctxlineage._report import html, normalize

_TRUNCATE_AT = 700  # get_call default: keep tool output agent-context-friendly

mcp = FastMCP("ctxlineage")


class _EventStore:
    """Re-reads events.jsonl when it changes (append-only, may grow live)."""

    def __init__(self, directory: str | Path = ".ctxlineage") -> None:
        self.directory = Path(directory)
        self._cache_key: tuple[int, int] | None = None
        self._data: dict | None = None
        self.skipped_lines = 0

    @property
    def events_path(self) -> Path:
        return self.directory / "events.jsonl"

    def report_data(self) -> dict:
        path = self.events_path
        if not path.exists():
            raise FileNotFoundError(
                f"No events found at {path}. Run your app with ctxlineage.init() first, "
                "or start ctxlineage-mcp with --dir pointing at the right directory."
            )
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        if key != self._cache_key:
            events, self.skipped_lines = normalize.load_events(path)
            self._data = normalize.build_report_data(events)
            self._cache_key = key
        assert self._data is not None
        return self._data


_store = _EventStore()


def configure(directory: str | Path) -> None:
    """Point the server at a .ctxlineage directory (resets the cache)."""
    global _store
    _store = _EventStore(directory)


def _element_id(element: dict) -> str:
    return f"{element['span_id']}:{element['name']}"


def _find_call(data: dict, call_id: str) -> tuple[dict, dict] | None:
    for session in data["sessions"]:
        for call in session["calls"]:
            if call["id"] == call_id:
                return session, call
    return None


def _downstream_ids(edges: list[dict], roots: list[str]) -> list[str]:
    """Transitive closure over session edges, in stable discovery order."""
    seen = list(roots)
    result: list[str] = []
    frontier = list(roots)
    while frontier:
        source = frontier.pop(0)
        for edge in edges:
            if edge["from"] == source and edge["to"] not in seen:
                seen.append(edge["to"])
                result.append(edge["to"])
                frontier.append(edge["to"])
    return result


@mcp.tool()
def list_sessions() -> dict:
    """List recorded sessions with per-session summaries and global stats
    (call/error counts, tag match rate). Cheap index tool: returns ids to feed
    into get_call / get_lineage, never prompt bodies."""
    data = _store.report_data()
    sessions = [
        {
            "id": s["id"],
            "started_at": s["started_at"],
            "ended_at": s["ended_at"],
            "call_count": len(s["calls"]),
            "error_count": sum(1 for c in s["calls"] if c["error"]),
            "models": sorted({c["model"] for c in s["calls"] if c["model"]}),
            "call_ids": [c["id"] for c in s["calls"]],
            "element_ids": [_element_id(e) for e in s["elements"]],
        }
        for s in data["sessions"]
    ]
    return {"stats": data["stats"], "sessions": sessions}


@mcp.tool()
def get_call(call_id: str, full_content: bool = False) -> dict:
    """Get one LLM call's full context anatomy: segments (role/kind, tag,
    source, token estimates), usage, output, timing. Segment and output text
    longer than 700 chars is truncated (content_truncated: true) unless
    full_content=true."""
    data = _store.report_data()
    found = _find_call(data, call_id)
    if not found:
        raise ValueError(f"Unknown call_id {call_id!r}. Use list_sessions to see valid ids.")
    session, call = found
    call = copy.deepcopy(call)
    call["session_id"] = session["id"]
    if not full_content:
        texts = list(call["segments"])
        if call.get("output"):
            texts.append(call["output"])
        for item in texts:
            content = item.get("content") or ""
            if len(content) > _TRUNCATE_AT:
                item["content"] = content[:_TRUNCATE_AT]
                item["content_truncated"] = True
    return call


def _element_lineage(session: dict, element: dict) -> dict:
    node = {"type": "element", "element_id": _element_id(element), **element}
    return {
        "session_id": session["id"],
        "node": node,
        "consuming_call_ids": element["calls"],
        "downstream_call_ids": _downstream_ids(session["edges"], element["calls"]),
        "edges_truncated": session["edges_truncated"],
    }


@mcp.tool()
def get_lineage(id: str) -> dict:
    """Trace lineage for a call_id or an element_id (span_id:name; a bare tag
    name works when unambiguous). Calls: elements consumed, edges in/out, and
    the transitive downstream calls their output flowed into. Elements: the
    calls that consumed them and everything downstream (impact analysis)."""
    data = _store.report_data()
    found = _find_call(data, id)
    if found:
        session, call = found
        edges_in = [e for e in session["edges"] if e["to"] == id]
        edges_out = [e for e in session["edges"] if e["from"] == id]
        node = {k: call[k] for k in ("id", "span_id", "step", "timestamp", "model", "error")}
        node["type"] = "call"
        return {
            "session_id": session["id"],
            "node": node,
            "elements_consumed": [
                {"element_id": _element_id(e), **e} for e in session["elements"] if id in e["calls"]
            ],
            "edges_in": edges_in,
            "edges_out": edges_out,
            "downstream_call_ids": _downstream_ids(session["edges"], [id]),
            "edges_truncated": session["edges_truncated"],
        }
    matches = [
        (session, element)
        for session in data["sessions"]
        for element in session["elements"]
        if _element_id(element) == id or element["name"] == id
    ]
    if len(matches) > 1:
        candidates = ", ".join(_element_id(e) for _, e in matches)
        raise ValueError(f"Ambiguous element name {id!r} — use an element_id: {candidates}")
    if not matches:
        raise ValueError(
            f"Unknown id {id!r} (no call or element). Use list_sessions to see valid ids."
        )
    return _element_lineage(*matches[0])


@mcp.tool()
def generate_report(out: str = "ctxlineage-report.html") -> dict:
    """Build the self-contained HTML report (Call Anatomy + Lineage Graph)
    from the recorded events and write it to `out`. Returns the absolute path
    and summary counts."""
    data = _store.report_data()
    out_path = Path(out)
    out_path.write_text(html.render(data), encoding="utf-8")
    return {
        "path": str(out_path.resolve()),
        "sessions": data["stats"]["sessions"],
        "calls": data["stats"]["calls"],
        "skipped_lines": _store.skipped_lines,
    }


@click.command()
@click.option(
    "--dir",
    "-d",
    "directory",
    default=".ctxlineage",
    show_default=True,
    help="Directory containing events.jsonl.",
)
def main(directory: str) -> None:
    """Run the ctxlineage MCP server over stdio."""
    configure(directory)
    mcp.run()


if __name__ == "__main__":
    main()
