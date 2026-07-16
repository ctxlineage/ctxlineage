#!/usr/bin/env python3
"""Rebuild the M2 design mockups with fresh demo data.

Usage: uv run python docs/design/m2-mockups/build.py [output_dir]
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
REPO_ROOT = HERE.parents[2]
TEMPLATES = ["call-anatomy", "chain"]


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
    for name in TEMPLATES:
        template = (HERE / f"{name}.template.html").read_text(encoding="utf-8")
        target = out / f"{name}.html"
        target.write_text(template.replace("__DATA__", payload), encoding="utf-8")
        print(f"wrote {target}")


if __name__ == "__main__":
    main(*sys.argv[1:2])
