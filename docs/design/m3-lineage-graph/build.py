#!/usr/bin/env python3
"""Rebuild the M3 lineage-graph mockup with fresh demo data.

Usage: uv run python docs/design/m3-lineage-graph/build.py [output_dir]
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
REPO_ROOT = HERE.parents[2]


def main(out_dir: str = "/tmp/ctxl-design") -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "examples" / "generate_demo_events.py"), tmp],
            check=True,
        )
        from ctxlineage._report import normalize

        events, _skipped = normalize.load_events(Path(tmp) / "events.jsonl")
        data = normalize.build_report_data(events)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    template = (HERE / "lineage-graph.template.html").read_text(encoding="utf-8")
    target = out / "lineage-graph.html"
    target.write_text(template.replace("__DATA__", payload), encoding="utf-8")
    print(f"wrote {target}")


if __name__ == "__main__":
    main(*sys.argv[1:2])
