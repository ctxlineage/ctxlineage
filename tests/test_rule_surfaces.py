"""Every built-in rule must be reachable from the surfaces that teach it.

Adding a rule to `_PARSERS` makes it work; it does not make anyone able to
find it. This repo has shipped that gap three times — #65 (`import` with no
CHANGELOG or README), #69 (v0.2 features with no on-ramp), and #108 (the
v0.2.2 rules absent from SKILL.md and CLAUDE.md while README had them) — and
each time it was caught by hand, after merge.

These tests are the mechanical version of that hand-check, so the fourth time
fails CI instead. They deliberately assert only *presence of the rule name*:
anything stricter would ossify the prose, and the point is to force an author
to visit each surface, not to dictate what they write there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ctxlineage._contract import config

REPO = Path(__file__).parent.parent

#: Surfaces a user or an agent actually reads to discover that a rule exists.
#: README is the human on-ramp; SKILL.md is what a coding agent follows and is
#: the one that decides whether the feature is reachable at all through the
#: path built to surface it; CLAUDE.md is what a contributor reads before
#: adding the next rule.
TEACHING_SURFACES = [
    "README.md",
    "skills/ctxlineage-instrument/SKILL.md",
    "CLAUDE.md",
]

RULE_NAMES = sorted(config._PARSERS)


def test_there_is_at_least_one_rule():
    """Guard the guard: an empty registry would make every test below vacuous."""
    assert RULE_NAMES


@pytest.mark.parametrize("surface", TEACHING_SURFACES)
@pytest.mark.parametrize("rule", RULE_NAMES)
def test_every_rule_is_named_on_every_teaching_surface(rule: str, surface: str):
    text = (REPO / surface).read_text(encoding="utf-8")
    assert rule in text, (
        f"{surface} never mentions the {rule!r} rule. A rule that only exists in "
        f"_PARSERS works but cannot be found — see #108. Add it to {surface} "
        f"(and check SKILL.md's frontmatter description too: it decides whether "
        f"the skill fires at all)."
    )


def test_skill_frontmatter_advertises_what_the_rules_do():
    """The frontmatter `description` decides whether the skill fires, so it has
    to carry the *vocabulary a user would ask in*, not just the rule names.

    #108 shipped with a description that said only "window budgets, grounding
    checks" — v0.2.0's vocabulary — so a request for regression or invariance
    testing never matched, and three shipped rules were unreachable through
    the exact path built to surface them.
    """
    text = (REPO / "skills/ctxlineage-instrument/SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---")[1]
    for phrase in ("window budget", "grounding", "regression", "invariance", "structure"):
        assert phrase in frontmatter.lower(), (
            f"the skill's frontmatter description does not mention {phrase!r}, so a user "
            f"asking for it in those words will not trigger the skill"
        )
