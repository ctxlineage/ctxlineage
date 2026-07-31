# Report UI: default-state legibility (#102, #103, #104)

Status: **agreed 2026-07-31.** Decisions are recorded inline under each issue;
the rejected options are kept because they document what the chosen one is
being weighed against.

All three issues came out of a hands-on review of a real report. #102 and #104
share one root cause: **the default drawing state does not carry the
information the view exists to carry.** #103 is a separate problem (rank), plus
one feature that is general in name only.

Reproduction used throughout:

```
uv run python examples/generate_demo_events.py /tmp/ctxl-ui-demo/.ctxlineage
CTXLINEAGE_DIR=/tmp/ctxl-ui-demo/.ctxlineage uv run ctxlineage import \
  --from claude-code tests/fixtures/claude_code/session_multi_tool_loop.jsonl
CTXLINEAGE_DIR=/tmp/ctxl-ui-demo/.ctxlineage uv run ctxlineage report \
  --out /tmp/ctxl-ui-demo/report.html
```

16 calls across 5 sessions: 4 native-capture demo sessions (one tagged) and one
imported Claude Code session.

---

## 1. Measurements (what the default state actually shows)

### 1.1 Chain hides the majority of the lineage (#104)

`drawEdges()` filters to `j === i + 1` at rest; everything else waits for a
click on an output chip.

| session | calls | inferred edges | adjacent (drawn) | non-adjacent (hidden) |
|---|---|---|---|---|
| demo-session-rag | 6 | 6 | 2 | **4** (2→4, 2→5, 2→6, 4→6) |
| demo-session-agent | 3 | 3 | 2 | **1** (1→3) |
| sess-multi-tool-loop | 4 | 6 | 3 | **3** (1→3, 1→4, 2→4) |
| **total** | | **15** | 7 | **8 (53%)** |

**53% of all inferred output→input edges are invisible until clicked**, and 67%
in the flagship RAG session. The hidden half is the *informative* half: "call N
feeds call N+1" is the expected case, while "call 2's output was still in the
window at call 6" is the observation the product exists to surface.

The Graph view already draws all of them by default, so the two views disagree
about the same data.

Secondary cause: every non-adjacent edge is routed at the same
`GUTTER = 24` x-position, so drawing them all at once today would stack them on
top of each other. A lane allocator is a prerequisite for any "show all" option.

### 1.2 Graph flow edges route into empty space after column collapse (#102)

Collapsed layout (`hasElements === false`, `app.js:708-710`) sets
`COLX = {source: 10, element: 10, call: 30}`, but `laneBase` is still
`COLX.call + W.call + 30`. Measured on the imported session:

- call boxes: `x = 30 … 270`
- flow-edge lanes: `x = 300, 314, 328, 342, 356`
- declared `<svg width>`: 410; actual content bbox: 346 wide

So every flow edge exits the **right** edge of a call box, swings into a band of
otherwise-empty canvas, and comes back — inside a `main` area ~1220px wide that
is blank everywhere else. The lane is a vestige of the three-column layout,
where a right-side gutter is correct *because the left edge of each call box is
already taken by incoming provenance edges*. Once the source and element columns
collapse there are no provenance edges at all, so the reserved gutter is on the
wrong side.

### 1.3 Calls view has no rank, and its JSON tree is a tool_defs viewer (#103)

**Rank.** The fn card renders every fact as an identical label/value row. On
call 13 (imported):

```
Read()
claude-fable-5
api        messages          <- 2 distinct values in the whole report
duration   –                 <- null for all 4 imported calls
mode       sync              <- "sync" for 15 of 16 calls
span       Fix the failing…  <- actually informative
usage      212 tok           <- restates the window bar above it
```

Three of five rows are near-constant boilerplate carrying the same visual weight
as the one row that varies. Segment headers stack up to three numbers at one
weight too: `8 tok · 4% of prompt · 100% of recovered`.

The Chain view already solved this in-repo: `.fnpill` renders `step()` bold,
model in teal, then a single muted meta line `api · duration · stream`.

**JSON tree.** `parseJsonMaybe` requires the *entire* segment body to parse.
Measured across the report:

| segment kind | segments | tree fires |
|---|---|---|
| tool_defs | 3 | **3** |
| tool | 9 | 0 |
| assistant | 15 | 0 |
| user | 24 | 0 |
| system | 10 | 0 |
| tagged (app_prompt, rag_chunks) | 3 | 0 |
| **outputs** | **15** | **0** |

3 of 64 segments (4.7%), all `tool_defs`; zero outputs. The maintainer's read is
exactly right.

The cause is not the heuristic being too weak — it is that **the backend already
holds the structure and flattens it away.** `normalize.py:94-104`:

```python
if ptype == "tool_use":
    name = part.get("name", "tool")
    args = json.dumps(part.get("input", {}), ensure_ascii=False)
    return f"[tool_use: {name}({args})]"
```

A structured `part` dict becomes `[tool_use: Read({"file_path": "test_math.py"})]`,
and the frontend then fails to parse it because of the text envelope. The most
structure-rich content in an agent trace — tool calls and their arguments — is
the content the tree never fires on.

---

## 2. Design options

### #104 — Chain: lineage visible at rest (highest priority)

**A. All edges, two weights** — **CHOSEN**
Draw every inferred edge at rest. Adjacent hops keep today's subway hop through
the row gap at full weight; non-adjacent hops get a widened left gutter with a
lane allocator so parallel hops never overlap, drawn thinner and at lower
opacity. Clicking an output still promotes its edges to `--edge-hi` and dims the
rest; `↳ n` stays as the numeric summary.
*Pros:* core value visible with zero interaction; agrees with the Graph view.
*Cons:* needs a lane allocator and a wider gutter (today 24px against a 40px
node padding); a long session with many hops needs a lane cap and a graceful
fallback.

**B. Spine + branches**
One continuous vertical spine in the gutter; each hop is a branch off it.
*Pros:* scales to long sessions. *Cons:* harder to read *which* call feeds which.

**C. Bounded window**
Show hops spanning ≤ N rows, collapse the rest into `↳ n`.
*Cons:* the cutoff is arbitrary and still hides real flows.

**D. Louder `↳ n` affordance only**
Keep the default, make the badge a real button.
*Cons:* cheapest, but the arrow still requires a click — does not fix the issue.

**Open question (honesty).** Every `output_text` edge is inferred from a
substring match, and today they all render solid, with the caveat only in a
footnote. Drawing *more* of them by default raises the stakes. Option: render
inferred edges dashed and reserve solid for declared structure. `same_span`
edges (`normalize.py:441`) *are* declared and exact, and both views currently
drop them — so this would also give the distinction something real to say.
That is a scope increase and needs an explicit call.

**Decided: keep the current treatment** — inferred edges stay solid, with the
caveat in the footnote. Dashed strokes and `same_span` rendering are out of
scope here. The footnote is reworded so it plainly covers the gutter arrows too,
since the default state now asserts more than it did.

### #102 — Graph: reserved gutter on the free side

**A. Two-tier flow edges** — **CHOSEN** (shares #104's model)
Adjacent flows become a short direct vertical arrow between stacked call boxes;
only non-adjacent hops take a gutter lane. The gutter sits on whichever side is
free: **left** in the collapsed layout (no provenance edges), **right** in the
three-column layout (left edge occupied). This is the drt reserved-gutter rule
applied adaptively rather than a fixed side.
On the imported session, 3 of 6 flows stop needing a lane at all.
*Pros:* removes the swing into empty space, makes "time flows down" literal,
unifies Chain and Graph. *Cons:* gutter side differs between the two layouts.

**B. Mirror the lane only**
When `hasElements` is false, move the lane to the left of the call column and
fit the SVG to content. Smallest change that closes the issue.
*Cons:* adjacent flows keep swinging out and back.

**C. Keep the right gutter, make it intentional**
Fit `<svg width>` to the real bbox, tighten `laneBase` when collapsed, add a
`FLOWS` column header.
*Cons:* labels the vestige rather than removing it.

### #103 — Calls: rank, and a tree that fires where structure is

**Rank** (little disagreement expected, listed for confirmation):
adopt the Chain view's `.fnpill` hierarchy in the Calls fn card — `step()` +
model primary, token cost secondary, and `api · duration · mode` collapsed into
one muted meta line. Segment headers lead with the token count and demote the
percentages. `% of recovered` is kept (it is #90's deliberate second basis) but
demoted rather than dropped.

**JSON tree**

**A. Carry the structure through from the backend** — **CHOSEN**
`_part_text` already has the parsed `part`. Emit the structured payload as a
sibling field on the segment/output instead of only the flattened string, and
let the frontend render a tree from declared structure. No sniffing: the report
stops throwing away what it already knows.
*Pros:* most honest, fires exactly where structure genuinely exists (tool calls,
tool results), and is equally correct for native capture and for imports.
*Cons:* touches `normalize.py` and the event/report payload shape.

**B. Detect embedded structure in the frontend**
Recognize a JSON region inside a text envelope and render the surrounding text
as text with a tree scoped to the recognized region.
*Pros:* frontend-only. *Cons:* it is content inference — the tree would have to
be visibly scoped so a detected region never reads as declared structure.

**C. Reframe as a tool-definition viewer**
Name the feature for what it does today.
*Pros:* cheapest and honest. *Cons:* gives up the real value.

---

## 3. Sequencing

#104 first (highest priority, and its lane allocator is reusable), then #102
(shares the two-tier edge model), then #103. Suggested as three PRs, one per
issue, matching the #95–#98 pattern.

Light and dark are both mandatory; all new strokes and fills go through CSS
custom properties, no hardcoded colors. No build step, no CDN, offline-openable
output, static SVG. The `input → fn → output` metaphor is unchanged by all of
the above.
