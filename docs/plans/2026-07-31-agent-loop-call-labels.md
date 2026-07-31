# Implementation Plan: Per-call action labels for imported agent loops (#88)

> **Status:** implementation plan, PR 3 of the v0.2.2 issue batch (#88–#94).
> Stacked on PR 2 (both touch `renderCallDetail()`/`stepOf` in `app.js`).

## 1. The bug

`step` (`normalize.py`, `span_names.get(span_id)`) is a **per-span** label —
every call in one agent-loop episode shares one `span_id`, so all of them
share one label: the human turn's own sentence. In the real trial that
prompted this, one user message produced 38 consecutive calls that all read
identically across Overview's ranked lists, Chain's node headings, and the
Calls sidebar — "the only way to navigate is by token count."

## 2. The fix: a new per-call field, distinct from the per-span one

`app.js`'s `spanNameOf(c) = c.step` already reads `step` for the fn card's
secondary "span" row and the graph's span brackets — repointing `step`
itself to a per-call value would silently break that second consumer. Two
fields, two jobs:

- **`step`** (unchanged) — the span/episode label, still what `spanNameOf`
  and grouping read.
- **`action`** (new) — what *this call* did, populated only by the importer
  (`claude_code.py`; native capture's own per-call `call_stack[0]` already
  serves the same purpose and is checked first).

Preference order, matching the issue's own proposal exactly:

1. The tool name(s) whose result fed **this call's own input** — the
   `tool_result` block(s) in the last message of its request. A
   `tool_result` only carries `tool_use_id`, not a name, so it resolves
   through the earlier `tool_use` block the id points back to
   (`_tool_use_names`).
2. Else the tool name(s) **this call's own output** emitted (`tool_use`
   blocks in its response).
3. Else `None` — falls through to `stepOf`'s existing span fallback, correct
   for a call with no discernible tool activity (an episode's first call,
   where the human's own request already is the right label).

`app.js`: `stepOf(c) = c.call_stack?.[0] || c.action || c.step` — a
three-tier fallback (native per-call > import per-call action > span).

## 3. Why preference (1) before (2)

A call fed a prior tool's result and going on to invoke a *different* tool
of its own is labelled by what it **received**, not what it does next —
because what it does next becomes the *following* call's label anyway. Over
a sequence this produces a chain that traces the episode step by step
(`Read, Read, Edit, Bash` in the test fixture: call 2 is fed Read's result
and itself calls Edit, but is labelled "Read" — the label for call 3, which
is fed *that* Edit's result, becomes "Edit").

## 4. A gap found while verifying visually, not in the original triage

#88 and the umbrella #91 both name the **Calls sidebar** as a third place
sharing the bug ("94 entries that all read `claude-fable-5` + timestamp"),
alongside Overview and Chain. Overview/Chain both already read `stepOf(c)`,
so fixing that one function fixed them automatically — but the sidebar
(`renderCallsNav`) has its **own** template that only ever showed
`c.model` + timestamp, never `stepOf`. Fixed by making `stepOf(c) ?? c.model`
the sidebar's primary label, demoting the model name to the sub line rather
than dropping it.

## 5. Verification

- `tests/fixtures/claude_code/session_multi_tool_loop.jsonl` (new): 4 calls,
  one span, Read → Edit → Bash → a final text answer — chosen because the
  existing `session_tool_loop.jsonl` fixture (2 calls) happens to resolve
  both calls to "Read" (call 1 emits it, call 2 is correctly fed its
  result), which is right but not a compelling demonstration of
  differentiation across a longer sequence.
- `tests/test_import_claude_code.py`: preference (1) over (2) even when a
  call also emits its own different tool; preference (2) for an episode's
  first call; preference (3) (no `action` key at all) via `_call_action`
  directly, since no existing fixture happens to contain a call with
  neither an input tool_result nor an output tool_use.
- `tests/test_normalize.py`: `action` reads the payload declaration, is
  `None` for live capture (which never sets it), and does not shadow
  `step` — a call can carry both.
- `tests/browser/test_report_labels.py` (new): Overview's heaviest-calls
  rows are no longer identical; the fn card's `.stepname` shows the
  Read/Read/Edit/Bash sequence; the "span" row still shows the real span
  label when it differs from the action; the sidebar's own primary label
  shows the same sequence, with the model name still visible on the sub
  line.
- Full suite **481 passed**, lint clean. Live Playwright pass (both views,
  both the fn card and the sidebar) — confirmed visually.

## Adversarial review, pre-merge: one minor issue found and fixed

An adversarial review found the design and its preference order sound
(hand-traced through the fixture to confirm preference 1 correctly beats
preference 2), but one minor defensive gap: `block.get("name") or "tool"`
doesn't crash on a malformed `tool_use` block whose `name` is not a string
(a hand-edited or corrupted transcript), but a non-empty non-string value
(e.g. a dict) is truthy, so it flows through unstrung into `payload["action"]`
and would render as `[object Object]` in the report instead of degrading to
the same `"tool"` fallback used elsewhere. Fixed with a small `_tool_name(block)`
helper that explicitly checks `isinstance(name, str)`, used at both call
sites (`_tool_use_names` and `_call_action`'s output-tool-use scan). New
test `test_a_non_string_tool_name_falls_back_rather_than_flowing_through_raw`
pins it — confirmed it fails without the fix (the raw dict compares unequal
to `"tool"`).

Full suite after the fix: **482 passed**, lint clean.
