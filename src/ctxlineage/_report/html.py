"""Self-contained HTML report rendering.

The UI ships as three plain-file assets inside the package
(assets/template.html, assets/style.css, assets/app.js) — hand-written,
no build step, no CDN (PLAN.md §6 as amended 2026-07-16). render() inlines
the assets and embeds the report data JSON. __DATA__ is substituted last so
payload text can never re-trigger marker substitution.
"""

from __future__ import annotations

import json
from importlib import resources


def _asset(name: str) -> str:
    return (resources.files("ctxlineage._report") / "assets" / name).read_text(encoding="utf-8")


def render(report_data: dict) -> str:
    # "</" escaped as "<\/" so prompt content can never close the script tag.
    payload = json.dumps(report_data, ensure_ascii=False).replace("</", "<\\/")
    page = _asset("template.html")
    page = page.replace("/*__STYLE__*/", _asset("style.css"))
    page = page.replace("/*__APP__*/", _asset("app.js"))
    return page.replace("__DATA__", payload)
