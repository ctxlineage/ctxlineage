# Implementation Plan: `metamorphic` contract rule (vision doc §8, class 3 of 4)

> **Status:** implementation plan. Third of the four assertion classes the
> contract-testing vision doc names; the first two (structural, regression/
> differential) shipped in the v0.2.2 batch as `window_budget` /
> `requires_segment` and `segment_diff`.

## 1. The scope decision this plan resolves

The vision doc names this class **"Metamorphic / invariance (CheckList
INV/DIR)"** and gives two examples: *"perturb `rag_chunks` order → answer
should be invariant; drop a cited chunk → answer should change/degrade."*

Both examples are stated at the **output** level ("the answer"). That
collides with the doc's own boundary in §10 — *"data tests, not model
evals"* — and with §7, which reserves semantic quality for the thin
LLM-judge layer. Deciding which side of that line this rule falls on **is**
the design work here.

**Resolved (confirmed with the maintainer): this rule asserts at the
*context* level, not the output level.** The system under test is the
context pipeline, not the model. Reasoning:

- **Output-level invariance is not deterministically checkable.** Deciding
  that two different answers "mean the same thing" is a semantic judgment —
  the exact thing §10 rules out and §7 assigns to the judge tier. Exact
  string equality is far too brittle to stand in for it.
- **It is also vacuous on the runs this project recommends gating.** The
  README's own guidance is to gate mocked/replayed runs, because prompt
  *assembly* stays deterministic even when replies do not. Under a mocked
  run, perturbing the input cannot change the canned output at all, so an
  output-invariance assertion passes by construction and proves nothing.
  It only carries signal on a live run — which is non-deterministic, and so
  needs the statistical treatment §9 describes.
- **The context level is exactly expressible with the substrate we have**,
  deterministically, and it is where ctxlineage's differentiation lives.

Output-level metamorphic (with statistical gating) is deferred to a
follow-up phase, filed as its own issue for a design discussion — the same
move already made for `grounded`'s "utilization" variant, which is
permanently judge-tier by the same reasoning.

## 2. Why this rule needs tags (and why that is correct, not a limitation)

Verified empirically against the real pipeline before designing:

**Untagged**, an app's retrieved chunks are joined into one message, so the
segment decomposition sees a single `user` blob. Shuffling retrieval order
rewrites that one string. There is no way, from the recorded artifact, to
tell "same chunks, reordered" from "different chunks" — the granularity to
express the relation simply is not there.

**Tagged** (`span.tag("rag_chunks", docs, ...)`), `apply_tags` splits the
message per element (`matching._units` matches list elements individually,
precisely because apps join them into one message), so each chunk becomes
its own `rag_chunks` segment. Then:

```
ordered equal?  False     <- retrieval order really did change
multiset equal? True      <- the context's *content* did not
```

That multiset comparison is exactly the INV relation, and it exists only
because a `tag()` made the decomposition exact. So `metamorphic` is a
**tag-required rule, like `grounded`** — the differentiated class, gating
only where the evidence is exact, degrading to advisory otherwise. This is
the tier rule (§6) applying on its own terms, not a workaround.

## 3. Design

```toml
[[assert.metamorphic]]
variant = "runs/shuffled.jsonl"   # a second recorded run: the perturbed one
relation = "invariant"            # INV — the perturbation must NOT change the context
segment = "rag_chunks"            # required: which tagged kind the perturbation targeted

[[assert.metamorphic]]
variant = "runs/dropped_one.jsonl"
relation = "changed"              # DIR — the perturbation MUST change the context
segment = "rag_chunks"
```

- `@dataclass(frozen=True) Metamorphic(variant_data: dict, relation: str,
  segment: str)`. `variant_data` is the already-normalized
  `build_report_data()` output of the variant run, loaded once at
  config-parse time — the same shape `SegmentDiff.baseline_data` uses, so
  `runner.py` stays untouched and every rule keeps taking exactly one dict.
- `variant` resolves relative to the TOML file's own directory, reusing the
  convention `segment_diff` established.
- **`segment` is required.** Making it optional would let the rule compare
  whole-prompt multisets, which on an *untagged* run silently degrades into
  comparing the one blob — a shuffle would then FAIL as a "context
  regression" when the truth is "we cannot see inside it." Requiring the
  kind, and warning when it is absent, keeps the rule from gating on
  something it cannot actually resolve.

### The comparison

Per paired call, take the multiset of `content` for segments whose `kind`
matches `segment`, on both sides:

- `invariant` → the two multisets must be **equal**; otherwise FAIL.
  Catches order-sensitive dedup, order-dependent truncation, "shuffling the
  retrieval dropped a chunk."
- `changed` → the two multisets must **differ**; otherwise FAIL. Catches a
  perturbation that silently had no effect — a `k` parameter that is
  ignored, a filter that never fires.

Content is compared, not `tokens_est`: the estimate is derived from the
content, so comparing content is both stricter and more direct.

### Pairing across the two runs

Identical to `segment_diff`'s: sessions by position, calls within a session
by span-name occurrence order. Rather than duplicate that subtle algorithm,
this slice **extracts it into a shared `_paired_calls(session, other)`
helper** returning `(pairs, unpaired_here, unpaired_there)`, and repoints
`SegmentDiff` at it. Pure refactor, no behaviour change — the same move the
`_incomplete_reason` extraction made in the previous slice, and verified the
same way (run `segment_diff`'s full test set unchanged before and after).

### Tier: what gates, what warns, what skips

| situation | severity | why |
| --- | --- | --- |
| relation violated, kind present on both sides | **FAIL** | the tag made it exact |
| kind absent on **both** sides | WARN | never appeared — untagged run, or a typo'd name. Nothing was asserted; saying otherwise would be the same lie a silent pass is. Mirrors `grounded`'s untagged demotion and `window_budget`'s typo guard. |
| kind absent on **exactly one** side | WARN | genuinely ambiguous: either the perturbation dropped the content entirely (a real INV violation) **or** that run simply was not tagged. Ambiguous evidence must not hard-gate (§6). |
| either side's call has `segments_complete=False` | SKIP | an import cannot reconstruct exact segments — the same guard `window_budget`/`requires_segment`/`segment_diff` already apply. |
| a call has no counterpart in the other run | WARN | a pairing gap is a shape change, not a content regression. |

## 4. Verification

- `tests/test_contract_rules.py`: INV holds on a reorder → no findings; INV
  violated (a chunk changed) → FAIL; DIR holds (a chunk dropped) → no
  findings; DIR violated (nothing actually changed) → FAIL; kind absent on
  both sides → WARN naming the tag remedy, never FAIL; kind absent on one
  side → WARN, not FAIL; imported call on either side → SKIP; pairing gap →
  WARN.
- Fixtures go through **real `span_start`/`tag` events and the real
  pipeline**, not hand-written report dicts — the whole premise is that the
  rule reads what the pipeline already produces, so the tests must break if
  that shape drifts (the existing convention in this file).
- `tests/test_contract_config.py`: loading, `variant`/`relation`/`segment`
  all required, unknown `relation` rejected with the valid values named,
  unknown key rejected, missing variant file is a `ConfigError`, and the
  variant path resolves relative to the TOML's directory rather than CWD.
- Re-run `segment_diff`'s full test set unchanged, to prove the
  `_paired_calls` extraction changed no behaviour.
- Full suite + `ruff check` / `ruff format --check` before opening the PR.
- `docs/vision/context-contract-testing.md` §8: mark the metamorphic class
  as shipped **at the context level**, and record that the output-level
  half is deferred to the judge/statistical phase, with the issue link — so
  the doc stops reading as if the whole class were still open.

## 5. Adversarial review, pre-merge: one blocker and one major, both fixed

Reviewed before merge, as `segment_diff` was. Every finding below was
reproduced independently before acting on it.

- **Blocker — an empty or unrelated second run reported as a green pass.**
  `zip(strict=False)` yields no pairs, so nothing is compared; and the
  "never appeared" safety net was itself gated on having evaluated
  something, which disarmed it in exactly the case it should catch.
  Confirmed end to end: a present-but-empty variant produced
  `All 1 assertion(s) ... passed - 0 warning(s), 0 skipped`, exit 0. That is
  precisely the "unevaluated reads as green" failure this whole track exists
  to prevent. **This was inherited from `segment_diff`** (it reproduces on
  `main`), so the fix is applied to both: a WARN when the two runs hold
  different session counts, and a WARN when no call could be paired at all.
- **Major — the severity was inverted: dropping *one* chunk FAILed, dropping
  *all* of them only warned.** The original code treated "absent on one
  side" as ambiguous between a real total drop and an untagged run. That
  reasoning was wrong, and the data proves it: `session["elements"]` records
  every `tag()` a run *declared*, with its own `matched` flag, independent
  of whether it matched anything. So `elements == []` means the run never
  claimed to have the content (no visibility → WARN), while a declared tag
  with no surviving segment means the content really is gone (exact
  evidence → gates like any other difference). The rule's own headline bug —
  a perturbation that silently drops chunks — was the case it refused to
  gate. Fixed, with tests for both halves of the branch.
- **Considered and deliberately not taken: demoting `partial` tag matches to
  WARN.** The review argued that `metamorphic` hard-gates on best-effort
  substring matching, and suggested demoting when any matched part is
  `match: "partial"`. Checking `matching.py:95` first showed why that would
  be wrong: `match` is `"exact"` only when a tag covers a segment's *entire*
  content, so a chunk embedded in a larger `Context: …` message is **always**
  `"partial"` — the normal, healthy case. Demoting on it would reduce
  essentially every real RAG use to advisory, destroying the rule. The
  underlying concern is real but narrower than claimed (a perturbation
  confined to content the matcher cannot track — e.g. units below
  `_MIN_UNIT_LEN`), and gating on tag-matched segments is the same bar
  `grounded` already sets, so this is not a new tier violation. Recorded as
  a documented limitation rather than a code change.
- **Minor, fixed** — both parsers now run their own cheap validation before
  reading and normalizing the second run, so an already-invalid config does
  not pay for the file read nor report that file's problems ahead of its
  own. This deliberately changes `segment_diff`'s previous error precedence
  (baseline-existence used to be checked before `max_token_delta`), so both
  orderings are now pinned by tests rather than left incidental.
- **Verified clean by the review**: the `_paired_calls` extraction is
  byte-identical to the old `SegmentDiff._check_session` in both findings
  and their order (checked against `[A,B,A,A]` vs `[A,A,C]` and a
  `None`-step mix, plus a brute force over all 3-call step combinations);
  `s["content"]` cannot `KeyError`; duplicate, empty and whitespace chunks
  all behave correctly; neither orphan branch misattributes its session.

Full suite after the fixes: **548 passed**, lint clean.
