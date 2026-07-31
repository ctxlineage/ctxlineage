/* ctxlineage report app — hand-written vanilla JS, no build step (PLAN.md §6).
   Reads the embedded report_version:1 JSON and renders two views:
   Calls (per-call anatomy: input segments → fn → output) and Chain (session flow). */
"use strict";

const data = JSON.parse(document.getElementById("ctxlineage-data").textContent);

/* ---------- shared vocabulary ---------- */
const KIND = { system: "--sys", user: "--user", assistant: "--assistant",
               tool: "--tool", tool_defs: "--tooldef" };
const SEG_LABEL = { system: "app · instructions", user: "user input",
                    assistant: "llm output (prev)", tool: "tool / MCP",
                    tool_defs: "tool definitions" };
const CHIP_LABEL = { system: "app", user: "user", assistant: "llm out", tool_defs: "tool defs" };
const TAG_VARS = ["--tag1", "--tag2", "--tag3", "--tag4", "--tag5"];
const kindColor = (k) => {
  if (KIND[k]) return `var(${KIND[k]})`;
  let h = 0;
  for (const ch of String(k)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `var(${TAG_VARS[h % TAG_VARS.length]})`;
};
const segLabel = (g) => {
  if (g.kind === "tool" && g.name) return `tool / MCP · ${g.name}`;
  if (SEG_LABEL[g.kind]) return SEG_LABEL[g.kind];
  return g.source ? `${g.kind} · ${g.source}` : g.kind;  // tag-named segment
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n) => (n == null ? "–" : n.toLocaleString("en-US"));
const clip = (t, n) => {
  let s = String(t).slice(0, n);
  const last = s.charCodeAt(s.length - 1);
  if (last >= 0xd800 && last <= 0xdbff) s = s.slice(0, -1);  // no torn emoji
  return s;
};
/* ---------- #92: structure-aware rendering for JSON segment/output bodies ----------
   A wall of quotes and braces shows nothing at a glance; a collapsed tree does.
   Only the top-level content is ever JSON.parse'd — a string value that merely
   looks like JSON is rendered as a plain (escaped) string, never re-parsed, so
   this cannot recurse into content the app never declared as structured. */
const parseJsonMaybe = (text) => {
  let value;
  try { value = JSON.parse(text); } catch { return null; }
  return value !== null && typeof value === "object" ? value : null;  // a bare scalar isn't worth a tree
};
const jsonKind = (v) => Array.isArray(v)
  ? `array · ${v.length} item${v.length === 1 ? "" : "s"}`
  : `object · ${Object.keys(v).length} key${Object.keys(v).length === 1 ? "" : "s"}`;
const jsonLeafText = (v) => {
  if (v === null) return "null";
  if (typeof v !== "string") return String(v);
  return v.length > 60 ? `"${clip(v, 60)}…"` : `"${v}"`;
};
// A parsed value can nest arbitrarily deep (a hostile or just very large RAG
// chunk, a deeply nested tool-call trace) - JSON.parse succeeding says
// nothing about whether the walk below can afford to recurse that deep.
// Found by adversarial review: an ~2000-level-deep array blew the call stack
// mid-render, leaving #main showing stale content with no visible error.
// This cap is far above any realistic payload's real nesting.
const JSON_TREE_MAX_DEPTH = 24;
const jsonTreeHtml = (value, depth = 0) => {
  const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v]) : Object.entries(value);
  if (!entries.length) return `<div class="jempty">${Array.isArray(value) ? "[]" : "{}"}</div>`;
  if (depth >= JSON_TREE_MAX_DEPTH) {
    return `<div class="jempty">nested deeper than ${JSON_TREE_MAX_DEPTH} levels, not expanded further</div>`;
  }
  return `<div class="jchildren">${entries.map(([k, v]) => {
    const branch = v !== null && typeof v === "object";
    const kv = `<span class="jkey">${esc(k)}</span>: <span class="jval">${esc(branch ? jsonKind(v) : jsonLeafText(v))}</span>`;
    return branch
      ? `<details class="jrow jbranch"><summary>${kv}</summary>${jsonTreeHtml(v, depth + 1)}</details>`
      : `<div class="jrow">${kv}</div>`;
  }).join("")}</div>`;
};

/* ---------- #103: structure the provider declared, not structure we guessed ----------
   parseJsonMaybe above only fires when a whole body parses, which across the
   demo report meant 3 of 64 segments and 0 of 15 outputs — every one of them a
   tool_defs blob. The richest structure in an agent trace, a tool call's own
   arguments, arrives wrapped as `[tool_use: Read({...})]` and never parsed.
   normalize.py now carries those arguments through as `structured` instead of
   flattening them away, so this renders declared structure and never sniffs. */
const STRUCT_LABEL = { tool_call: "tool call" };
const structLabel = (s) =>
  `${STRUCT_LABEL[s.kind] ?? s.kind}${s.name ? " · " + s.name : ""}`;
const structOf = (item) => (item && Array.isArray(item.structured) ? item.structured : []);
const structChip = (list) => list.length
  ? `<span class="jkind">${esc(list.map(structLabel).join(" · "))}</span>` : "";
/* Emitted on one line on purpose: this lands inside `.full`/`.body`, which are
   `white-space: pre-wrap` so the raw text keeps its own formatting. A newline
   in the markup would render as a blank line in the page. */
const structHtml = (list) => list.map((s) => {
  const branch = s.value !== null && typeof s.value === "object";
  const head = `<div class="jhead">${esc(structLabel(s))}${
    branch ? ` · <span class="jkind">${esc(jsonKind(s.value))}</span>` : ""}</div>`;
  const body = branch ? jsonTreeHtml(s.value) : `<div class="jrow">${esc(jsonLeafText(s.value))}</div>`;
  return `<div class="jstruct">${head}${body}</div>`;
}).join("");

const stepOf = (c) => {
  // label split (design decision 6, extended by #88): the span name is
  // grouping info (spanNameOf - brackets, fn-card "span" row), never this.
  // Per-call label preference: native capture's own call stack (real, exact)
  // > an importer's derived per-call action (#88 - what THIS call did, not
  // which turn it descends from) > the span name as a last-resort fallback
  // (correct for a call with no discernible activity of its own, e.g. an
  // episode's first call).
  const frame = c.call_stack && c.call_stack[0];
  const fn = frame ? frame.split(":")[1] : null;
  return fn || c.action || c.step || null;
};
const spanNameOf = (c) => c.step || null;

/* ---------- state ---------- */
let view = "overview";
let selCall = 0;
let selSession = 0;
let hiFrom = null;
let chainEdges = [];

const calls = [];
data.sessions.forEach((s, si) => s.calls.forEach((c) => calls.push({ s, si, c })));
const callIndex = new Map(calls.map((x, i) => [x.c, i]));

document.getElementById("stats").textContent =
  `${data.stats.calls} calls · ${data.stats.sessions} sessions · ${data.stats.errors} errors`;

/* ---------- theme: follow OS, manual toggle persisted ---------- */
const themeBtn = document.getElementById("theme");
let theme = localStorage.getItem("ctxlineage-theme") ??
  (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
function applyTheme() {
  document.body.dataset.theme = theme;
  themeBtn.textContent = theme === "dark" ? "☀ light" : "☾ dark";
  if (view === "chain") drawEdges();
}
themeBtn.addEventListener("click", () => {
  theme = theme === "dark" ? "light" : "dark";
  localStorage.setItem("ctxlineage-theme", theme);
  applyTheme();
});

/* ---------- filter ---------- */
let query = "";
const filterEl = document.getElementById("filter");
const norm = (s) => String(s ?? "").toLowerCase();
function callMatches(s, c) {
  if (!query) return true;
  const q = norm(query);
  return norm(c.model).includes(q) || norm(stepOf(c)).includes(q) || norm(s.id).includes(q) ||
    (c.error && norm(c.error.type + " " + c.error.message).includes(q)) ||
    norm(c.output && c.output.content).includes(q) ||
    c.segments.some((g) => norm(g.content).includes(q) || norm(g.name).includes(q));
}
const sessionMatches = (s) => s.calls.some((c) => callMatches(s, c));
filterEl.addEventListener("input", () => { query = filterEl.value.trim(); render(); });
addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== filterEl) { e.preventDefault(); filterEl.focus(); }
});

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => {
    if (view === t.dataset.view) return;
    view = t.dataset.view;
    if ((view === "chain" || view === "graph") && calls[selCall]) selSession = calls[selCall].si;
    hiFrom = null;
    graphFocus = null;
    render();
  }));

/* ================= overview view (home) ================= */

const callTok = (c) => (c.usage ? c.usage.total_tokens : c.input_tokens_est) ?? 0;
const windowPct = (c) => {
  if (!c.context_window) return null;
  const used = c.usage ? c.usage.prompt_tokens : c.input_tokens_est;
  return (100 * used) / c.context_window;
};

function renderOverviewNav() {
  let h = "<h3>sessions</h3>";
  data.sessions.forEach((s, i) => {
    if (!sessionMatches(s)) return;
    h += `<div class="sessrow" data-i="${i}">
      <div class="id">${esc(s.id)}</div>
      <div class="sub">${s.calls.length} calls</div></div>`;
  });
  const nav = document.getElementById("navlist");
  nav.innerHTML = h;
  nav.querySelectorAll(".sessrow").forEach((el) =>
    el.addEventListener("click", () => {
      view = "chain"; selSession = +el.dataset.i; hiFrom = null; render();
    }));
}

function renderOverview() {
  const main = document.getElementById("main");
  if (!calls.length) {
    main.innerHTML = '<div class="empty">No LLM calls recorded yet.</div>';
    return;
  }
  const totalTok = calls.reduce((a, x) => a + callTok(x.c), 0);
  const promptTok = calls.reduce((a, x) => a + (x.c.usage ? x.c.usage.prompt_tokens : x.c.input_tokens_est ?? 0), 0);
  const outTok = calls.reduce((a, x) => a + (x.c.usage ? x.c.usage.completion_tokens : 0), 0);

  const heaviest = calls.map((x, i) => ({ ...x, i })).filter((x) => callMatches(x.s, x.c))
    .sort((a, b) => callTok(b.c) - callTok(a.c)).slice(0, 5);
  const pressure = calls.map((x, i) => ({ ...x, i, pct: windowPct(x.c) })).filter((x) => callMatches(x.s, x.c))
    .filter((x) => x.pct !== null)
    .sort((a, b) => b.pct - a.pct).slice(0, 3);

  const stat = (num, cap, cls = "") =>
    `<div class="statcard"><div class="num ${cls}">${num}</div><div class="cap">${cap}</div></div>`;
  const topRow = (x, right) =>
    `<div class="toprow" data-i="${x.i}">
       <span class="rk">${heaviest.indexOf(x) >= 0 ? heaviest.indexOf(x) + 1 : ""}</span>
       <span class="st">${esc(stepOf(x.c) ?? "llm call")}() · ${esc(x.c.model)}</span>
       <span class="ss">${esc(x.s.id)}</span>${right}</div>`;

  main.innerHTML = `<div class="ov">
    ${data.redaction ? `<div class="note" style="margin:0 0 14px">🔒 redacted report —
      ${fmt(data.redaction.matches)} match(es) of ${data.redaction.patterns} pattern(s)
      replaced with [redacted]. Counts and match rates reflect the original text.</div>` : ""}
    <div class="cards">
      ${stat(fmt(data.stats.calls), "llm calls")}
      ${stat(fmt(data.stats.sessions), "sessions")}
      ${stat(fmt(totalTok), "total tokens")}
      ${stat(fmt(promptTok) + " / " + fmt(outTok), "input / output tok")}
      ${stat(fmt(data.stats.errors), "errors", data.stats.errors ? "errn" : "")}
      ${data.stats.tags && data.stats.tags.total
        ? stat((data.stats.tags.match_rate * 100).toFixed(0) + "%",
               `tag match rate (${data.stats.tags.matched}/${data.stats.tags.total})`)
        : ""}
    </div>
    <h4>views</h4>
    <div class="guides">
      <div class="guide" data-view="calls"><b>Calls — call anatomy</b>
        <p>Dissect one call: what filled the context window (user input, app
        instructions, tool results, previous outputs) and at what token cost.</p></div>
      <div class="guide" data-view="chain"><b>Chain — session flow</b>
        <p>Follow a session: how each output becomes the next call's input —
        loops, fan-out, and where context accumulates.</p></div>
    </div>
    <h4>heaviest calls (by tokens)</h4>
    ${heaviest.map((x) => topRow(x, `<span class="tk">${fmt(callTok(x.c))} tok</span>`)).join("")}
    ${pressure.length ? `<h4>window pressure (input vs model limit)</h4>
      ${pressure.map((x) => topRow(x, `<span class="pressure"><span class="bar"><i style="width:${Math.min(x.pct, 100).toFixed(1)}%"></i></span></span><span class="tk">${x.pct.toFixed(1)}%</span>`)).join("")}` : ""}
  </div>`;

  main.querySelectorAll(".toprow").forEach((el) =>
    el.addEventListener("click", () => { view = "calls"; selCall = +el.dataset.i; render(); }));
  main.querySelectorAll(".guide").forEach((el) =>
    el.addEventListener("click", () => { view = el.dataset.view; render(); }));
}

/* ================= calls view (anatomy) ================= */

function renderCallsNav() {
  let h = "";
  data.sessions.forEach((s) => {
    h += `<h3>${esc(s.id)}</h3>`;
    s.calls.forEach((c) => {
      if (!callMatches(s, c)) return;
      const i = callIndex.get(c);
      const tok = c.usage ? fmt(c.usage.total_tokens) + " tok" : "–";
      // #88/#91: the sidebar was the third place named alongside Overview and
      // Chain - every row read only model+timestamp, so on an agent-loop
      // session the only way to navigate was by token count. stepOf(c) is
      // the same per-call label those two views already use.
      h += `<div class="callrow ${i === selCall ? "sel" : ""}" data-i="${i}">
        <span class="n">${i + 1}</span>
        <span class="m"><span class="model">${esc(stepOf(c) ?? c.model)}</span>
          <div class="sub">${esc(c.model)} · ${esc((c.timestamp ?? "").slice(11, 19))} · ${tok}</div></span>
        ${c.stream ? '<span class="badge stream">stream</span>' : ""}
        ${c.error ? '<span class="badge err">error</span>' : ""}</div>`;
    });
  });
  const nav = document.getElementById("navlist");
  nav.innerHTML = h;
  nav.querySelectorAll(".callrow").forEach((el) =>
    el.addEventListener("click", () => { selCall = +el.dataset.i; render(); }));
}

function renderCallDetail() {
  const main = document.getElementById("main");
  if (!calls.length) {
    main.innerHTML = '<div class="empty">No LLM calls recorded yet.</div>';
    return;
  }
  const { c } = calls[selCall];
  const segTotal = c.segments.reduce((a, g) => a + g.tokens_est, 0);
  const inTok = c.usage ? c.usage.prompt_tokens : c.input_tokens_est;
  // An imported transcript cannot recover the system prompt or tool
  // definitions, but they were in the window and cost these tokens. Proportion
  // against the whole prompt, not against the fraction we can name — otherwise
  // a 4-token segment of a 33k prompt renders as "50% of input".
  const unaccounted = c.segments_complete === false ? Math.max(inTok - segTotal, 0) : 0;
  const total = segTotal + unaccounted || 1;
  // #90: at low recovery (an import can reconstruct <1% of the real prompt),
  // every real segment rounds to "0% of prompt" and conveys nothing. Add a
  // second, explicitly-labelled basis - share of what was actually recovered
  // - only when the two bases differ; a live-capture call (unaccounted=0)
  // sees no change, keeping #64's real-prompt basis as the honest top line.
  const showRecovered = unaccounted > 0 && segTotal > 0;
  const pct = c.context_window ? (100 * inTok / c.context_window) : null;
  // role (not kind): a tagged system prompt stays in the fn card, with provenance
  const sys = c.segments.filter((g) => g.role === "system");
  const rest = c.segments.filter((g) => g.role !== "system");

  const winSegs = c.segments.map((g) =>
    `<i style="width:${(100 * g.tokens_est / total).toFixed(2)}%;background:${kindColor(g.kind)}"></i>`).join("")
    + (unaccounted ? `<i class="unaccounted" style="width:${(100 * unaccounted / total).toFixed(2)}%"
         title="${fmt(unaccounted)} tok the transcript does not preserve"></i>` : "");
  const windowbar = `
    <div class="windowbar">
      <div class="lbl"><span>context window — input ${fmt(inTok)} tok${c.usage ? "" : " (est.)"}</span>
        <span>${pct === null ? "window size unknown" : pct.toFixed(2) + "% of " + fmt(c.context_window)}</span></div>
      <div class="bar">${pct !== null
        ? `<i style="width:${Math.max(pct, 0.6)}%;display:flex;overflow:hidden">${winSegs}</i>` : winSegs}</div>
    </div>`;

  // Imported sessions: say what could not be reconstructed, on the page. The
  // CLI says it at import time, but the report is what gets reopened and shared.
  const imp = c["import"];
  const provenance = imp ? `
    <div class="provenance">
      <div class="lbl"><span>imported from ${esc(imp.source || "an agent transcript")}</span>
        <span>${fmt(unaccounted)} of ${fmt(inTok)} prompt tok not preserved</span></div>
      <div class="txt">Reconstructed from a session transcript, not captured live. Token counts
        are the API's own; segment sizes are estimated. Not preserved by the transcript:
        ${esc((imp.not_preserved || []).join(", ") || "some fields")}${
          imp.reasoning_blocks_stripped
            ? ` (${fmt(imp.reasoning_blocks_stripped)} reasoning block(s) kept only as a signature)` : ""
        }. The ones that were part of the prompt still cost the tokens counted above — only
        their text is unavailable; the rest is missing metadata that cost nothing.</div>
    </div>` : "";

  const segs = rest.map((g) => {
    const ws = !g.content.trim();
    const parsed = ws ? null : parseJsonMaybe(g.content);
    const struct = structOf(g);
    // Structure at a glance in the collapsed preview too - "json object · 6
    // keys", or "tool call · Read", answers "what is this?" without reading a
    // character of it.
    const preview = ws ? "(whitespace separator)"
      : parsed ? `<span class="jkind">${esc(jsonKind(parsed))}</span>`
      : struct.length ? structChip(struct) + " " + esc(clip(g.content, 70))
      : esc(clip(g.content, 90));
    const full = ws ? "(whitespace only — separates the surrounding segments)"
      : parsed ? jsonTreeHtml(parsed) : esc(g.content) + structHtml(struct);
    // #103: one number leads. The token cost is what a reader is here for; the
    // shares are context for it, not peers of it.
    return `
    <div class="seg ${ws ? "ws" : ""}" style="border-left-color:${kindColor(g.kind)}">
      <div class="top"><span class="kind" style="color:${kindColor(g.kind)}">${esc(segLabel(g))}</span>
        <span class="share"><b>${fmt(g.tokens_est)}</b> tok ·
          <span class="pct">${(100 * g.tokens_est / total).toFixed(0)}% of prompt${
          showRecovered ? ` · ${(100 * g.tokens_est / segTotal).toFixed(0)}% of recovered` : ""}</span></span></div>
      <div class="preview" dir="auto">${preview}</div>
      <div class="full" dir="auto">${full}</div>
    </div>`;
  }).join("");

  const sysTok = sys.reduce((a, g) => a + g.tokens_est, 0);
  const instr = sys.length ? `
    <div class="instr" id="instr">
      <div class="lbl"><span>instructions${
        (() => { const t = [...new Set(sys.filter((g) => g.tagged).map((g) => segLabel(g)))];
                 return t.length ? " — " + esc(t.join(" + ")) : ""; })()
      }</span><span>${fmt(sysTok)} tok · ${(100 * sysTok / total).toFixed(0)}% of prompt${
        showRecovered ? ` · ${(100 * sysTok / segTotal).toFixed(0)}% of recovered` : ""}
        <b class="toggle" title="show all / show less">▸</b></span></div>
      <div class="txt">${esc(sys.map((g) => g.content).join("\n\n"))}</div>
    </div>` : "";

  // #103: the card used to render api / duration / mode / span / usage as five
  // identical label-value rows, so nothing led. Across the demo report `api`
  // has two distinct values, `mode` reads "sync" on 15 of 16 calls and
  // `duration` is empty on every imported one — three rows of near-constant
  // boilerplate carrying the same weight as the one number worth reading.
  // Rank them: what ran, what it cost, then the fixed facts on one quiet line
  // — the shape .fnpill in the Chain view already uses.
  const meta = [c.api, c.duration_ms ? c.duration_ms.toFixed(0) + " ms" : null,
                c.stream ? "streaming" : "sync"].filter(Boolean);
  const fn = `
    <div class="fn">
      <div class="stepname">${esc(stepOf(c) ?? "llm call")}()</div>
      <div class="model">${esc(c.model)}</div>
      ${c.usage ? `<div class="cost"><b>${fmt(c.usage.total_tokens)}</b> tok
        <span>${fmt(c.usage.prompt_tokens)} in · ${fmt(c.usage.completion_tokens)} out</span></div>` : ""}
      <div class="meta">${esc(meta.join(" · "))}</div>
      ${spanNameOf(c) && spanNameOf(c) !== stepOf(c)
        ? `<div class="row"><span>span</span><span>${esc(spanNameOf(c))}</span></div>` : ""}
      ${instr}
    </div>`;

  const outContent = c.output ? c.output.content : "";
  const outParsed = c.error ? null : parseJsonMaybe(outContent);
  const outStruct = c.error ? [] : structOf(c.output);
  const out = c.error
    ? `<div class="out error"><div class="head"><span>error</span><span>${esc(c.error.type)}</span></div>
       <div class="body">${esc(c.error.message)}</div></div>`
    : `<div class="out" id="outwrap"><div class="head"><span class="ol">llm output${
         outParsed ? ` · <span class="jkind">${esc(jsonKind(outParsed))}</span>`
           : outStruct.length ? " · " + structChip(outStruct) : ""
       }</span>
         <span>${c.usage ? fmt(c.usage.completion_tokens) + " tok · " : ""}${esc(c.output && c.output.finish_reason || "")}
         <b class="toggle" title="show all / show less">▸</b></span></div>
       <div class="body" dir="auto">${outParsed ? jsonTreeHtml(outParsed)
         : esc(outContent) + structHtml(outStruct)}</div></div>`;

  main.innerHTML = `
    <div class="callhead"><h2>call ${selCall + 1}</h2>
      <span class="meta">${esc(c.id)} · ${esc(c.timestamp)}</span></div>
    ${windowbar}
    ${provenance}
    <div class="flow">
      <div class="col"><h4>input — context</h4>${segs}
        ${unaccounted ? `<div class="seg unaccounted-seg">
          <div class="top"><span class="kind">not preserved by the transcript</span>
            <span class="share">${fmt(unaccounted)} tok · ${(100 * unaccounted / total).toFixed(0)}%</span></div>
          <div class="preview">system prompt, tool definitions and reasoning text — sent, counted, not recorded</div>
        </div>` : ""}
        ${c.segments.some((g) => g.tagged) ? "" : '<div class="hint">user input may contain app-injected context (RAG, templates) — tag it with the span API to split and attribute it</div>'}</div>
      <div class="arrow">→</div>
      <div class="col"><h4>fn — step + model + instructions</h4>${fn}</div>
      <div class="arrow">→</div>
      <div class="col"><h4>output</h4>${out}</div>
    </div>
    ${c.call_stack.length ? `<div class="stackline">called from <code>${c.call_stack.map(esc).join(" ← ")}</code></div>` : ""}`;
  // A toggle just flips a CSS class - it doesn't re-render, so a scrolled
  // panel that's closed and reopened would otherwise still show wherever the
  // reader last scrolled it, not the head of the content.
  const toggleOpen = (el, scrollSelector, event) => {
    // A click on a nested JSON-tree <details>/<summary> (#92) bubbles up to
    // this container's own listener - let the native disclosure toggle be
    // independent, or expanding one JSON key would also close the whole
    // segment/output it lives in.
    if (event && event.target.closest("details, summary")) return;
    const opening = !el.classList.contains("open");
    el.classList.toggle("open");
    const target = opening && el.querySelector(scrollSelector);
    if (target) target.scrollTop = 0;
  };
  main.querySelectorAll(".seg").forEach((el) =>
    el.addEventListener("click", (e) => toggleOpen(el, ".full", e)));
  const instrEl = document.getElementById("instr");
  if (instrEl) instrEl.addEventListener("click", (e) => toggleOpen(instrEl, ".txt", e));
  const outEl = document.getElementById("outwrap");
  if (outEl) outEl.addEventListener("click", (e) => toggleOpen(outEl, ".body", e));
}

/* ================= chain view (session flow) ================= */

function findEdges(session) {
  // edges are inferred in the report backend (normalize.py) — single source
  // of truth shared with the Lineage Graph and the MCP server
  const idx = new Map(session.calls.map((c, i) => [c.id, i]));
  return (session.edges || [])
    .filter((e) => e.kind === "output_text")
    .map((e) => [idx.get(e.from), idx.get(e.to)])
    .filter(([a, b]) => a != null && b != null);
}

/* a loop = consecutive calls of the SAME step whose outputs feed the next input */
function findLoops(session, edges) {
  const next = new Set(edges.filter(([i, j]) => j === i + 1).map(([i]) => i));
  const runs = [];
  let start = null;
  session.calls.forEach((c, i) => {
    const nx = session.calls[i + 1];
    const chained = next.has(i) && nx && nx.model === c.model && stepOf(nx) === stepOf(c);
    if (chained && start === null) start = i;
    if (!chained && start !== null) { runs.push([start, i]); start = null; }
  });
  if (start !== null) runs.push([start, session.calls.length - 1]);
  return runs.filter(([a, b]) => b - a >= 1);
}

function renderChainNav() {
  let h = "";
  data.sessions.forEach((s, i) => {
    if (!sessionMatches(s)) return;
    h += `<div class="sessrow ${i === selSession ? "sel" : ""}" data-i="${i}">
      <div class="id">${esc(s.id)}</div>
      <div class="sub">${s.calls.length} calls</div></div>`;
  });
  const nav = document.getElementById("navlist");
  nav.innerHTML = h;
  nav.querySelectorAll(".sessrow").forEach((el) =>
    el.addEventListener("click", () => { selSession = +el.dataset.i; hiFrom = null; graphFocus = null; render(); }));
}

function chainNodeHtml(sess, c, i, targets, downstream) {
  const agg = new Map();
  c.segments.forEach((g) => {
    const key = g.kind === "tool" ? `tool:${g.name ?? "tool"}` : g.kind;
    const cur = agg.get(key) ?? {
      kind: g.kind,
      label: g.kind === "tool" ? (g.name ?? "tool/MCP") : (CHIP_LABEL[g.kind] ?? g.kind),
      tok: 0, n: 0,
    };
    cur.tok += g.tokens_est; cur.n += 1; agg.set(key, cur);
  });
  const total = c.segments.reduce((a, g) => a + g.tokens_est, 0) || 1;
  /* llm-out chip first: it is the lineage edge target, keep it on the top line
     so incoming edges can always enter from the free gap above the row */
  const entries = [...agg.values()].sort(
    (a, b) => (a.kind === "assistant" ? 0 : 1) - (b.kind === "assistant" ? 0 : 1));
  // data-kind (#93): lets drawEdges() target the chip the match actually
  // landed in, instead of always assuming the assistant/"fed" chip.
  const chips = entries.map((a) =>
    `<span class="chip ${a.kind === "assistant" ? "fed" : ""}" data-kind="${esc(a.kind)}">
       <i style="background:${kindColor(a.kind)}"></i>${esc(a.label)}${a.n > 1 ? " ×" + a.n : ""} · ${fmt(a.tok)}</span>`).join("");
  const minibar = c.segments.map((g) =>
    `<i style="width:${(100 * g.tokens_est / total).toFixed(2)}%;background:${kindColor(g.kind)}"></i>`).join("");
  const ds = downstream
    ? `<span class="ds" title="feeds ${downstream} downstream call(s)">↳ ${downstream}</span>` : "";
  const out = c.error
    ? `<div class="outchip err" data-i="${i}"><div class="t"><span>error</span></div>
       <div class="p">${esc(c.error.type)}: ${esc(c.error.message)}</div></div>`
    : `<div class="outchip ${hiFrom === i ? "hi" : ""}" data-i="${i}">
       <div class="t"><b>output</b><span>${ds} ${c.usage ? fmt(c.usage.completion_tokens) + " tok" : ""}</span></div>
       <div class="p" dir="auto">${esc(c.output ? c.output.content : "")}</div></div>`;
  return `<div class="node ${targets.includes(i) ? "hi-target" : ""}${query && !callMatches(sess, c) ? " dim" : ""}" data-n="${i}">
    <span class="nlabel">${i + 1}</span>
    <div><div class="chips">${chips}</div><div class="minibar">${minibar}</div></div>
    <div class="fnpill"><div class="step">${esc(stepOf(c) ?? "llm call")}()</div>
      <div class="model">${esc(c.model)}</div>
      <div class="meta">${esc(c.api)} · ${c.duration_ms ? c.duration_ms.toFixed(0) + "ms" : "–"}${c.stream ? " · stream" : ""}</div></div>
    ${out}</div>`;
}

function renderChain() {
  const main = document.getElementById("main");
  if (!data.sessions.length) {
    main.innerHTML = '<div class="empty">No LLM calls recorded yet.</div>';
    return;
  }
  const s = data.sessions[selSession];
  const edges = findEdges(s);
  const loops = findLoops(s, edges);
  chainEdges = edges;  // cache for drawEdges (avoid recomputing per frame)
  const targets = hiFrom === null ? [] : edges.filter((e) => e[0] === hiFrom).map((e) => e[1]);
  const dsMap = new Map();
  edges.forEach(([a, b]) => { if (b > a + 1) dsMap.set(a, (dsMap.get(a) || 0) + 1); });
  const dsCount = (i) => dsMap.get(i) || 0;

  let h = `<div class="legend">
      <span><i style="background:var(--sys)"></i>app</span>
      <span><i style="background:var(--user)"></i>user</span>
      <span><i style="background:var(--assistant)"></i>llm output</span>
      <span><i style="background:var(--tool)"></i>tool/MCP</span>
      <span><i style="background:var(--tooldef)"></i>tool defs</span></div>
    <p class="sesshead">session <b>${esc(s.id)}</b> — ${s.calls.length} calls, time flows ↓ ;
      bold arrows feed the next call, thin gutter arrows feed a later one
      (<b>↳ n</b> = how many). Click an output to trace just its flows.</p>`;
  let body = "";
  let i = 0;
  while (i < s.calls.length) {
    const loop = loops.find(([a]) => a === i);
    if (loop) {
      const [a, b] = loop;
      body += `<div class="loopbox"><span class="loophead">↺ loop ×${b - a + 1}
        <span class="why">${esc(stepOf(s.calls[a]) ?? "same fn")}() repeats — each output feeds the next input</span></span>`;
      for (let k = a; k <= b; k++) body += chainNodeHtml(s, s.calls[k], k, targets, dsCount(k));
      body += `</div>`;
      i = b + 1;
    } else {
      body += chainNodeHtml(s, s.calls[i], i, targets, dsCount(i));
      i += 1;
    }
  }
  main.innerHTML = `${h}<div id="wrap"><svg id="edges"></svg><div id="chain">${body}</div></div>
    <div class="note">every arrow here, next-call and later-call alike, is inferred from the
    data — an output's text found inside a later call's input. Tagging (span API) will add
    source-level precision.</div>`;
  main.querySelectorAll(".outchip").forEach((el) =>
    el.addEventListener("click", () => {
      hiFrom = hiFrom === +el.dataset.i ? null : +el.dataset.i; render();
    }));
  requestAnimationFrame(drawEdges);
}

/* axis-aligned polyline with rounded corners */
function orthPath(pts, r = 8) {
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let k = 1; k < pts.length - 1; k++) {
    const [px, py] = pts[k - 1], [cx, cy] = pts[k], [nx, ny] = pts[k + 1];
    const inLen = Math.abs(cx - px) + Math.abs(cy - py);
    const outLen = Math.abs(nx - cx) + Math.abs(ny - cy);
    const rr = Math.min(r, inLen / 2, outLen / 2);
    const ix = cx - Math.sign(cx - px) * rr, iy = cy - Math.sign(cy - py) * rr;
    const ox = cx + Math.sign(nx - cx) * rr, oy = cy + Math.sign(ny - cy) * rr;
    d += ` L ${ix} ${iy} Q ${cx} ${cy} ${ox} ${oy}`;
  }
  d += ` L ${pts[pts.length - 1][0]} ${pts[pts.length - 1][1]}`;
  return d;
}

/* #104: lanes for the non-adjacent flows, by greedy interval colouring over the
   row ranges they span. Two hops share a lane only when their ranges cannot
   touch. Before this, every hop was routed at one shared x — which is why
   drawing them all at rest was not an option and they were hidden behind a
   click instead. Ranges that merely meet at a row (2->4 and 4->6) are treated
   as conflicting, so the shared row never reads as one continuous line. */
const GUTTER_LANES = 5;
function assignLanes(hops) {
  const lane = new Map();
  const lastRow = [];
  [...hops].sort((a, b) => a[0] - b[0] || a[1] - b[1]).forEach(([i, j]) => {
    let k = lastRow.findIndex((end) => end < i);
    if (k === -1) k = lastRow.length;
    lastRow[k] = j;
    lane.set(i + ">" + j, k);
  });
  return lane;
}

function drawEdges() {
  const svg = document.getElementById("edges");
  const wrap = document.getElementById("wrap");
  if (!svg || !wrap) return;
  const s = data.sessions[selSession];
  if (!s) return;
  const wr = wrap.getBoundingClientRect();
  svg.setAttribute("viewBox", `0 0 ${wr.width} ${wr.height}`);
  const bodyStyle = getComputedStyle(document.body);
  const edgeCol = bodyStyle.getPropertyValue("--edge").trim() || "#1FBFAE";
  const hiCol = bodyStyle.getPropertyValue("--edge-hi").trim() || "#11897d";
  const dimCol = bodyStyle.getPropertyValue("--edge-dim").trim() || "rgba(107,118,130,.35)";
  /* Markers cannot inherit the path's stroke-opacity, so the quieter weights
     need their own arrowhead rather than one shared marker. */
  const arrow = (id, fill, w, op) =>
    `<marker id="${id}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="${w}"
       markerHeight="${w}" orient="auto-start-reverse">
       <path d="M0,0 L10,5 L0,10 z" fill="${fill}" fill-opacity="${op}"/></marker>`;
  let h = `<defs>${arrow("arr", edgeCol, 7, 1)}${arrow("arrhi", hiCol, 8, 1)}
    ${arrow("arrsub", edgeCol, 6, 0.55)}${arrow("arrdim", dimCol, 6, 1)}</defs>`;
  const all = chainEdges.length ? chainEdges : findEdges(s);
  /* #104: everything is drawn at rest. Adjacent hops keep the full-weight
     subway hop through the row gap; the later flows — the ones worth the
     product's name, and 53% of all edges in the demo report — run quietly
     down the gutter instead of waiting for a click. Clicking now promotes one
     source's flows and dims the rest, rather than being the only way to see
     them at all. */
  const lanes = assignLanes(all.filter(([i, j]) => j > i + 1));
  /* Several hops can leave one row or arrive at another; stagger their
     horizontal runs through the row gap so they stack instead of overprinting. */
  const outN = new Map(), inN = new Map();
  const seat = new Map();
  const bump = (m, k) => { const n = m.get(k) || 0; m.set(k, n + 1); return n; };
  [...all].sort((a, b) => a[0] - b[0] || a[1] - b[1]).forEach(([i, j]) => {
    seat.set(i + ">" + j, { out: bump(outN, i), in: bump(inN, j) });
  });
  /* lane x inside the reserved gutter — see --chain-gutter in style.css */
  const laneX = (k) => 22 + (k % GUTTER_LANES) * 8;
  all.forEach(([i, j]) => {
    const a = document.querySelector(`.node[data-n="${i}"] .outchip`);
    const bNode = document.querySelector(`.node[data-n="${j}"]`);
    // #93: which segment the match actually landed in, so the arrow can
    // target that kind's chip instead of always assuming assistant/"fed" -
    // a tool-kind match previously rendered as if it landed in assistant.
    const rawEdge = (s.edges || []).find(
      (e) => e.kind === "output_text" && e.from === s.calls[i].id && e.to === s.calls[j].id);
    const toSeg = rawEdge && rawEdge.to_segment != null
      ? s.calls[j].segments[rawEdge.to_segment] : null;
    // A tag name (a segment's `kind` when it came from tag(), not a fixed
    // vocabulary) is arbitrary user text with no validation - compare
    // .dataset.kind directly rather than splicing it into a CSS attribute
    // selector string, whose escaping rules esc() (HTML-only) does not
    // cover. A newline/CR/form-feed in a tag name previously threw inside
    // querySelector and blanked every edge in this render pass.
    const kindChip = (node, kind) =>
      Array.from(node.querySelectorAll(".chips .chip")).find((el) => el.dataset.kind === kind);
    const b = bNode && (
      (toSeg && kindChip(bNode, toSeg.kind)) ||
      bNode.querySelector(".chips .chip.fed") ||
      bNode.querySelector(".chips"));
    if (!a || !b) return;
    const ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
    const hi = hiFrom === i;
    const sub = j > i + 1;                     // a later flow, not the next call
    const dim = hiFrom !== null && !hi;        // something else is being traced
    const marker = hi ? "arrhi" : dim ? "arrdim" : sub ? "arrsub" : "arr";
    const stroke = `stroke="${hi ? "var(--edge-hi)" : dim ? "var(--edge-dim)" : "var(--edge)"}"
      stroke-width="${hi ? 2.5 : sub ? 1.5 : 2}"
      ${sub && !hi && !dim ? 'stroke-opacity=".55"' : ""}
      fill="none" stroke-linejoin="round" marker-end="url(#${marker})"`;
    // #93: what flowed, not just that something did - a token count (the
    // source call's own reported output size) plus a snippet of the matched
    // text. Free: the matched substring is what created the edge already.
    const outTok = s.calls[i].usage ? s.calls[i].usage.completion_tokens : null;
    const snippet = clip((s.calls[i].output && s.calls[i].output.content) || "", 40);
    const labelShort = outTok != null ? `${fmt(outTok)} tok` : "";
    const labelFull = (outTok != null ? `${fmt(outTok)} tok · ` : "") + snippet;
    /* At rest, label the adjacent chain only — labelling all 15 demo edges at
       once buries the rows under text. While tracing, label exactly the traced
       flows. Everything unlabelled still carries its <title> on hover. */
    const showLabel = labelShort && (hiFrom === null ? !sub : hi);
    const label = (x, y) => showLabel
      ? `<title>${esc(labelFull)}</title>
         <text x="${x}" y="${y}" text-anchor="middle" class="edgelabel">${esc(labelShort)}</text>`
      : `<title>${esc(labelFull)}</title>`;
    const st = seat.get(i + ">" + j) || { out: 0, in: 0 };
    const x1 = ar.left - wr.left + 18, y1 = ar.bottom - wr.top - 2;
    const x2 = br.left + br.width / 2 - wr.left, y2 = br.top - wr.top - 3;
    if (!sub) {
      /* subway hop through the row gap: down → left → down into the fed chip's top */
      const gapY = (y1 + y2) / 2;
      h += `<g>${label((x1 + x2) / 2, gapY - 4)}
        <path d="${orthPath([[x1, y1], [x1, gapY], [x2, gapY], [x2, y2]])}" ${stroke}/></g>`;
    } else {
      /* later flow: down into its own gutter lane, then across the free gap
         ABOVE the target row, entering the fed chip from the top (never crosses
         other chips). The two horizontal runs are seated per row so several
         hops leaving or arriving together stay readable. */
      const lx = laneX(lanes.get(i + ">" + j) ?? 0);
      const gapY1 = y1 + 8 + st.out * 5;
      const gapY2 = y2 - 10 - st.in * 5;
      h += `<g>${label((lx + x2) / 2, gapY2 - 4)}
        <path d="${orthPath([[x1, y1], [x1, gapY1], [lx, gapY1], [lx, gapY2], [x2, gapY2], [x2, y2]])}" ${stroke}/></g>`;
    }
  });
  svg.innerHTML = h;
}


/* ================= graph view (lineage) ================= */

let graphFocus = null;

const elemSources = (e) => (e.sources && e.sources.length ? e.sources : (e.source ? [e.source] : []));

function buildGraph(s) {
  const nodes = [], edges = [];
  const elements = s.elements || [];
  const srcNames = [...new Set(elements.flatMap(elemSources))];
  srcNames.forEach((name) => nodes.push({ id: "src:" + name, type: "source", label: name }));
  elements.forEach((e, i) => {
    const id = "el:" + i;
    nodes.push({ id, type: "element", label: e.name, source: e.source, tok: e.tokens_est || 0,
                 transform: e.transform, matched: e.matched, occ: e.occurrences || 1 });
    elemSources(e).forEach((src) => edges.push({ from: "src:" + src, to: id, kind: "provenance" }));
    (e.calls || []).forEach((cid) => edges.push({ from: id, to: "call:" + cid, kind: "feeds" }));
  });
  s.calls.forEach((c) => nodes.push({ id: "call:" + c.id, type: "call", label: (stepOf(c) ?? "llm call") + "()",
    model: c.model, tok: c.usage ? c.usage.total_tokens : c.input_tokens_est, error: !!c.error }));
  const seen = new Set();
  (s.edges || []).forEach((e) => {
    const key = e.from + ">" + e.to;
    if (e.kind === "output_text" && !seen.has(key)) {
      seen.add(key);
      edges.push({ from: "call:" + e.from, to: "call:" + e.to, kind: "flows" });
    }
  });
  /* adjacency maps: closure and layout stay linear on big sessions */
  const succ = new Map(), pred = new Map();
  const push = (m, k, v) => { const a = m.get(k); if (a) a.push(v); else m.set(k, [v]); };
  edges.forEach((e) => { push(succ, e.from, e.to); push(pred, e.to, e.from); });
  return { nodes, edges, succ, pred };
}

function lineageClosure(graph, id) {
  const out = new Set([id]);
  const walk = (adj) => {
    const stack = [id];
    while (stack.length) {
      const cur = stack.pop();
      (adj.get(cur) || []).forEach((nxt) => {
        if (!out.has(nxt)) { out.add(nxt); stack.push(nxt); }
      });
    }
  };
  walk(graph.succ); walk(graph.pred);
  return out;
}

function renderGraphView() {
  const main = document.getElementById("main");
  const s = data.sessions[selSession];
  if (!s) { main.innerHTML = '<div class="empty">No LLM calls recorded yet.</div>'; return; }
  const g = buildGraph(s);
  const lit = graphFocus ? lineageClosure(g, graphFocus) : null;

  // Sources are always derived FROM elements (buildGraph), so zero elements
  // means zero sources too - one condition collapses both empty columns.
  // Untagged is the default experience for every user who hasn't adopted the
  // span/tag API, not an import artifact - #89's own follow-up trial found
  // this identical empty-column layout on a native, untagged capture too.
  const hasElements = (s.elements || []).length > 0;
  // call: 30, not 10, in the collapsed layout - span() and tag() are
  // independent APIs, so a session can group calls with span() while
  // tagging nothing (hasElements false with real span_ids). The span
  // bracket below sits at `COLX.call - 16`; 10 would put it at negative x,
  // bleeding past the SVG's own left edge (found by adversarial review).
  // #102: in the collapsed layout the call column also has to leave room for
  // the flow gutter that moves to its left (see flowGutter below) and for the
  // span bracket at `COLX.call - 16` that sits between the two.
  const COLX = hasElements
    ? { source: 10, element: 260, call: 560 }
    : { source: 10, element: 10, call: 120 };
  const W = { source: 210, element: 250, call: 240 };
  const H = { source: 34, element: 46, call: 52 };
  const GAPY = 26;

  const y = {};
  s.calls.forEach((c, i) => { y["call:" + c.id] = 40 + i * (H.call + GAPY + 14); });
  const els = g.nodes.filter((n) => n.type === "element");
  els.forEach((n, i) => {
    const feeds = (g.succ.get(n.id) || []).filter((t) => t.startsWith("call:")).map((t) => y[t] ?? 0);
    n.want = feeds.length ? feeds.reduce((a, b) => a + b, 0) / feeds.length : 40 + i * 60;
  });
  els.sort((a, b) => a.want - b.want);
  let cursor = 30;
  els.forEach((n) => { y[n.id] = Math.max(n.want, cursor); cursor = y[n.id] + H.element + GAPY; });
  const srcs = g.nodes.filter((n) => n.type === "source");
  srcs.forEach((n, i) => {
    const outs = (g.succ.get(n.id) || []).map((t) => y[t] ?? 0);
    n.want = outs.length ? outs.reduce((a, b) => a + b, 0) / outs.length : 30 + i * 60;
  });
  srcs.sort((a, b) => a.want - b.want);
  cursor = 30;
  srcs.forEach((n) => { y[n.id] = Math.max(n.want, cursor); cursor = y[n.id] + H.source + GAPY; });

  const height = Object.values(y).reduce((a, b) => Math.max(a, b), 60) + 120;
  // #102: the flow gutter goes wherever the free space is. With the source and
  // element columns present, the left edge of every call box is already taken
  // by incoming provenance edges, so the gutter belongs on the right — that is
  // the layout it was designed for. Once those columns collapse there are no
  // provenance edges at all, and a right-hand gutter leaves the flows swinging
  // out into blank canvas, pointing at nothing. Then the free side is the left.
  const callIdx = new Map(s.calls.map((c, i) => ["call:" + c.id, i]));
  const flows = g.edges.filter((e) => e.kind === "flows");
  const hops = flows
    .map((e) => [callIdx.get(e.from), callIdx.get(e.to)])
    .filter(([a, b]) => a != null && b != null && b > a + 1);
  const flowLanes = assignLanes(hops);
  const laneStep = hasElements ? 14 : -14;
  const laneBase = hasElements ? COLX.call + W.call + 30 : 76;
  const laneX = (k) => laneBase + (k % GUTTER_LANES) * laneStep;
  const svgW = hasElements ? laneBase + 110 : COLX.call + W.call + 30;
  const maxTok = els.reduce((a, n) => Math.max(a, n.tok || 0), 1);
  const nodeDim = (id) => (lit && !lit.has(id) ? "dimmed" : "");
  const edgeLit = (e) => lit && lit.has(e.from) && lit.has(e.to);
  const edgeStroke = (e) => (lit ? (edgeLit(e) ? "var(--edge-hi)" : "var(--edge-dim)") : "var(--edge)");

  let defs = `<defs>
    <marker id="garr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="var(--edge)"/></marker>
    <marker id="garrhi" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="var(--edge-hi)"/></marker>
  </defs>`;

  let eh = "";
  let lanesUsed = 0;
  g.edges.forEach((e) => {
    const hi = edgeLit(e);
    if (e.kind === "flows") {
      const fi = callIdx.get(e.from), ti = callIdx.get(e.to);
      const stroke = `fill="none" stroke="${edgeStroke(e)}" stroke-width="${hi ? 2.5 : 1.8}"
        stroke-linejoin="round" marker-end="url(#${hi ? "garrhi" : "garr"})"`;
      if (fi != null && ti === fi + 1) {
        // feeds the very next call: say it with the shortest line that can —
        // straight down the gap between two stacked boxes. No gutter needed,
        // and "time flows down" becomes literal rather than implied.
        const cx = COLX.call + W.call / 2;
        eh += `<path d="M ${cx} ${y[e.from] + H.call} L ${cx} ${y[e.to] - 2}" ${stroke}/>`;
        return;
      }
      const lx = laneX(flowLanes.get(fi + ">" + ti) ?? 0);
      lanesUsed += 1;
      const y1 = y[e.from] + H.call / 2, y2 = y[e.to] + H.call / 2;
      // exit and re-enter on whichever side the gutter is on
      const dir = hasElements ? 1 : -1;
      const x1 = hasElements ? COLX.call + W.call : COLX.call;
      eh += `<path d="M ${x1} ${y1} L ${lx - 8 * dir} ${y1} Q ${lx} ${y1} ${lx} ${y1 + 8}
        L ${lx} ${y2 - 8} Q ${lx} ${y2} ${lx - 8 * dir} ${y2} L ${x1 + 2 * dir} ${y2}" ${stroke}/>`;
    } else {
      const fromType = e.from.startsWith("src:") ? "source" : "element";
      const x1 = COLX[fromType] + W[fromType], y1 = y[e.from] + H[fromType] / 2;
      const toType = e.to.startsWith("el:") ? "element" : "call";
      const x2 = COLX[toType], y2 = y[e.to] + H[toType] / 2;
      const mid = (x1 + x2) / 2;
      eh += `<path d="M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2 - 2} ${y2}"
        fill="none" stroke="${edgeStroke(e)}" stroke-width="${hi ? 2.5 : 1.6}"
        marker-end="url(#${hi ? "garrhi" : "garr"})"/>`;
    }
  });

  // span brackets (design decision 2)
  let bh = "";
  let runStart = 0;
  for (let i = 1; i <= s.calls.length; i++) {
    const prev = s.calls[i - 1], cur = s.calls[i];
    if (!cur || cur.span_id !== prev.span_id) {
      if (prev.span_id) {
        const y1 = y["call:" + s.calls[runStart].id];
        const y2 = y["call:" + prev.id] + H.call;
        const bx = COLX.call - 16;
        bh += `<path d="M ${bx + 6} ${y1} L ${bx} ${y1} L ${bx} ${y2} L ${bx + 6} ${y2}"
                 fill="none" stroke="var(--muted)" stroke-width="1.5"/>
               <text x="${bx - 4}" y="${y1 - 4}" style="fill:var(--muted)" font-size="10.5">${esc(spanNameOf(prev) || "span")}</text>`;
      }
      runStart = i;
    }
  }

  let nh = "";
  g.nodes.forEach((n) => {
    const cls = `nodebox ${nodeDim(n.id)}`;
    if (n.type === "source") {
      nh += `<g class="${cls}" data-id="${esc(n.id)}">
        <rect x="${COLX.source}" y="${y[n.id]}" width="${W.source}" height="${H.source}" rx="7"
          fill="var(--src-bg)" stroke="var(--border)"/>
        <text x="${COLX.source + 10}" y="${y[n.id] + 22}" style="fill:var(--muted)">${esc(n.label)}</text></g>`;
    } else if (n.type === "element") {
      nh += `<g class="${cls}" data-id="${esc(n.id)}">
        <rect x="${COLX.element}" y="${y[n.id]}" width="${W.element}" height="${H.element}" rx="9"
          fill="var(--panel)" stroke="${n.matched ? kindColor(n.label) : "var(--muted)"}"
          stroke-width="1.6" ${n.matched ? "" : 'stroke-dasharray="5 4"'}/>
        <rect x="${COLX.element}" y="${y[n.id]}" width="5" height="${H.element}" rx="2.5" fill="${kindColor(n.label)}"/>
        <text x="${COLX.element + 14}" y="${y[n.id] + 19}" font-weight="700" style="fill:${kindColor(n.label)}">${esc(n.label)}${n.occ > 1 ? ` ×${n.occ}` : ""}</text>
        <text x="${COLX.element + 14}" y="${y[n.id] + 36}" style="fill:var(--muted)" font-size="11">
          ${n.tok ? fmt(n.tok) + " tok · " : ""}${esc(n.transform ? n.transform : n.matched ? "matched" : "unmatched")}</text>
        ${n.tok ? `<rect x="${COLX.element + 14}" y="${y[n.id] + H.element - 7}" width="${Math.max(6, (W.element - 28) * n.tok / maxTok)}" height="3" rx="1.5" fill="${kindColor(n.label)}" opacity=".8"/>` : ""}</g>`;
    } else {
      nh += `<g class="${cls}" data-id="${esc(n.id)}">
        <rect x="${COLX.call}" y="${y[n.id]}" width="${W.call}" height="${H.call}" rx="11"
          fill="var(--fn-bg)" stroke="${n.error ? "var(--err)" : "var(--fn-border)"}"/>
        <text x="${COLX.call + 14}" y="${y[n.id] + 21}" font-weight="700" style="fill:var(--fn-text)"
          font-family="ui-monospace, monospace" font-size="12.5">${esc(n.label)}</text>
        <text x="${COLX.call + 14}" y="${y[n.id] + 38}" style="fill:var(--teal)" font-size="11">
          ${esc(n.model)} · ${n.tok != null ? fmt(n.tok) : "–"} tok</text></g>`;
    }
  });

  const head = (x, t, anchor) =>
    `<text style="fill:var(--muted)" font-size="11" letter-spacing=".08em" x="${x}" y="16"
       ${anchor ? `text-anchor="${anchor}"` : ""}>${t}</text>`;
  const heads = `
    ${hasElements ? head(COLX.source, "SOURCES") + head(COLX.element, "CONTEXT ELEMENTS") : ""}
    ${head(COLX.call, "LLM CALLS ↓ TIME")}
    ${lanesUsed ? head(laneBase, "FLOWS", hasElements ? "start" : "end") : ""}`;

  // Untagged banner: actionable for a native-capture user (they can tag);
  // honest, not actionable, for an imported session (tagging is structurally
  // impossible there - the agent process can't call span()/tag()).
  const allImported = s.calls.length > 0 && s.calls.every((c) => c.import);
  const hint = hasElements ? "" : `<div class="note" style="margin:0 0 12px; max-width:560px">
    This session has no tagged context elements, so the sources and provenance
    columns are empty. ${allImported
      ? "It was imported from an agent transcript, where tagging isn't possible — native <b>ctxlineage.init()</b> capture is what unlocks them."
      : "Wrap calls in <b>ctxlineage.span()</b> and <b>tag()</b> your chunks/prompts to see where context comes from."
    } Output→input flows are still shown below.</div>`;
  main.innerHTML = `<div id="graphwrap">${hint}
    <svg width="${svgW}" height="${height}" style="overflow:visible">${defs}${bh}${heads}${eh}${nh}</svg>
    <div class="note">click any node to trace its lineage (upstream + downstream); click again to clear.
    dashed element = tagged but never matched.</div></div>`;
  main.querySelectorAll(".nodebox").forEach((el) =>
    el.addEventListener("click", () => {
      graphFocus = graphFocus === el.dataset.id ? null : el.dataset.id;
      render();
    }));
}

/* ---------- root render ---------- */
function render() {
  /* filtering away the current selection jumps to the first match */
  if (query) {
    if ((view === "chain" || view === "graph") &&
        data.sessions[selSession] && !sessionMatches(data.sessions[selSession])) {
      const idx = data.sessions.findIndex(sessionMatches);
      if (idx >= 0) { selSession = idx; hiFrom = null; graphFocus = null; }
    }
    if (view === "calls" && calls[selCall] && !callMatches(calls[selCall].s, calls[selCall].c)) {
      const idx = calls.findIndex((x) => callMatches(x.s, x.c));
      if (idx >= 0) selCall = idx;
    }
  }
  const matching = calls.filter((x) => callMatches(x.s, x.c)).length;
  document.getElementById("fcount").textContent =
    query ? `${matching} / ${calls.length} calls match` : "";
  document.querySelectorAll(".tab").forEach((t) =>
    t.classList.toggle("sel", t.dataset.view === view));
  document.body.dataset.view = view;
  if (view === "overview") { renderOverviewNav(); renderOverview(); }
  else if (view === "calls") { renderCallsNav(); renderCallDetail(); }
  else if (view === "graph") { renderChainNav(); renderGraphView(); }
  else { renderChainNav(); renderChain(); }
}

render();
applyTheme();
addEventListener("resize", () => { if (view === "chain") drawEdges(); });
