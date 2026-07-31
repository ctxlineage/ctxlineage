import pytest

from ctxlineage._contract import config
from ctxlineage._contract.config import ConfigError
from ctxlineage._contract.rules import Grounded, RequiresSegment, WindowBudget

# The shape sketched in docs/vision/context-contract-testing.md §14.
SKETCH = """
[[assert.window_budget]]
max_pct = 80

[[assert.window_budget]]
segment = "assistant"
max_pct = 40

[[assert.grounded]]
tag = "rag_chunks"
warn_dead = true
"""


def _write(tmp_path, text):
    path = tmp_path / "ctxlineage.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_the_sketch_shape(tmp_path):
    rules = config.load(_write(tmp_path, SKETCH))
    assert rules == [
        WindowBudget(max_pct=80, segment=None),
        WindowBudget(max_pct=40, segment="assistant"),
        Grounded(tag="rag_chunks", warn_dead=True),
    ]


def test_warn_dead_defaults_to_false(tmp_path):
    (rule,) = config.load(_write(tmp_path, '[[assert.grounded]]\ntag = "x"\n'))
    assert rule.warn_dead is False


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        config.load(tmp_path / "nope.toml")


def test_malformed_toml_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not valid TOML"):
        config.load(_write(tmp_path, "[[assert.grounded]\ntag = "))


def test_no_assert_section_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match=r"\[assert\]"):
        config.load(_write(tmp_path, "[something_else]\nx = 1\n"))


def test_empty_assert_section_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="no assertions"):
        config.load(_write(tmp_path, "[assert]\n"))


# A typo in a CI gate config must not be silently ignored — a gate that passes
# for the wrong reason is worse than no gate.
def test_unknown_rule_name_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown rule 'window_bugdet'"):
        config.load(_write(tmp_path, "[[assert.window_bugdet]]\nmax_pct = 80\n"))


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown key 'max_pcnt'"):
        config.load(_write(tmp_path, "[[assert.window_budget]]\nmax_pct = 80\nmax_pcnt = 10\n"))


def test_single_table_instead_of_table_array_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match=r"\[\[assert.window_budget\]\]"):
        config.load(_write(tmp_path, "[assert.window_budget]\nmax_pct = 80\n"))


@pytest.mark.parametrize(
    "body, message",
    [
        ("[[assert.window_budget]]\nsegment = 'user'\n", "max_pct is required"),
        ("[[assert.window_budget]]\nmax_pct = 'eighty'\n", "max_pct must be a number"),
        ("[[assert.window_budget]]\nmax_pct = true\n", "max_pct must be a number"),
        ("[[assert.window_budget]]\nmax_pct = 0\n", "max_pct must be"),
        ("[[assert.window_budget]]\nmax_pct = 101\n", "max_pct must be"),
        ("[[assert.window_budget]]\nmax_pct = -5\n", "max_pct must be"),
        ("[[assert.window_budget]]\nmax_pct = 80\nsegment = 12\n", "segment must be a string"),
        ("[[assert.window_budget]]\nmax_pct = 80\nsegment = ''\n", "segment must not be empty"),
        ("[[assert.grounded]]\nwarn_dead = true\n", "tag is required"),
        ("[[assert.grounded]]\ntag = 12\n", "tag must be a string"),
        ("[[assert.grounded]]\ntag = ''\n", "tag must not be empty"),
        ("[[assert.grounded]]\ntag = 'x'\nwarn_dead = 'yes'\n", "warn_dead must be a boolean"),
    ],
)
def test_invalid_entries_are_rejected(tmp_path, body, message):
    with pytest.raises(ConfigError, match=message):
        config.load(_write(tmp_path, body))


def test_error_names_the_offending_entry(tmp_path):
    body = "[[assert.window_budget]]\nmax_pct = 80\n\n[[assert.window_budget]]\nmax_pct = 500\n"
    with pytest.raises(ConfigError, match=r"assert.window_budget\[1\]"):
        config.load(_write(tmp_path, body))


def test_float_max_pct_is_accepted(tmp_path):
    (rule,) = config.load(_write(tmp_path, "[[assert.window_budget]]\nmax_pct = 99.5\n"))
    assert rule.max_pct == 99.5


# --------------------------------------------------------------------------
# requires_segment
# --------------------------------------------------------------------------


def test_requires_segment_loads(tmp_path):
    (rule,) = config.load(_write(tmp_path, "[[assert.requires_segment]]\nkind = 'system'\n"))
    assert rule == RequiresSegment(kind="system", when_model=None)


def test_requires_segment_when_model_loads(tmp_path):
    body = "[[assert.requires_segment]]\nkind = 'tool_defs'\nwhen_model = 'gpt-*'\n"
    (rule,) = config.load(_write(tmp_path, body))
    assert rule == RequiresSegment(kind="tool_defs", when_model="gpt-*")


def test_requires_segment_kind_is_required(tmp_path):
    with pytest.raises(ConfigError, match="kind is required"):
        config.load(_write(tmp_path, "[[assert.requires_segment]]\nwhen_model = 'gpt-*'\n"))


def test_requires_segment_rejects_unknown_key(tmp_path):
    body = "[[assert.requires_segment]]\nkind = 'system'\nkinds = 'system'\n"
    with pytest.raises(ConfigError, match="unknown key 'kinds'"):
        config.load(_write(tmp_path, body))


def test_requires_segment_kind_must_not_be_empty(tmp_path):
    with pytest.raises(ConfigError, match="kind must not be empty"):
        config.load(_write(tmp_path, "[[assert.requires_segment]]\nkind = ''\n"))
