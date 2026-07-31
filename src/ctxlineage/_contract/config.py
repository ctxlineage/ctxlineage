"""`ctxlineage.toml` loading and validation.

Validation is strict — unknown rule names and unknown keys are errors, never
ignored. A silently-ignored typo in a CI gate config is a gate that passes for
the wrong reason, which is worse than having no gate at all.

    [[assert.window_budget]]
    max_pct = 80

    [[assert.window_budget]]
    segment = "assistant"
    max_pct = 40

    [[assert.grounded]]
    tag = "rag_chunks"
    warn_dead = true
"""

from __future__ import annotations

import sys
from pathlib import Path

from ctxlineage._contract.rules import (
    Grounded,
    Metamorphic,
    RequiresSegment,
    SegmentDiff,
    WindowBudget,
)
from ctxlineage._report.normalize import build_report_data, load_events

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 has no tomllib; tomli is the same API (and became tomllib)
    import tomli as tomllib


class ConfigError(Exception):
    """Malformed or unreadable ctxlineage.toml."""


def load(path) -> list:
    """Parse and validate a config file into rule objects."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"Config not found: {path}. Create one with e.g.\n\n"
            "  [[assert.window_budget]]\n  max_pct = 80\n"
        )
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    return parse(raw, source=str(path), base_dir=path.resolve().parent)


def parse(raw: dict, source: str = "ctxlineage.toml", base_dir: Path | None = None) -> list:
    section = raw.get("assert")
    if section is None:
        raise ConfigError(
            f"{source}: no [assert] section, so there is nothing to test. Add e.g.\n\n"
            "  [[assert.window_budget]]\n  max_pct = 80\n"
        )
    if not isinstance(section, dict):
        raise ConfigError(f"{source}: [assert] must be a table")

    rules: list = []
    for name, entries in section.items():
        parser = _PARSERS.get(name)
        if parser is None:
            raise ConfigError(
                f"{source}: unknown rule '{name}' - built-in rules are: "
                f"{', '.join(sorted(_PARSERS))}"
            )
        if not isinstance(entries, list):
            raise ConfigError(
                f"{source}: [assert.{name}] must be a table array - write [[assert.{name}]] "
                f"with double brackets"
            )
        for index, entry in enumerate(entries):
            where = f"{source}: assert.{name}[{index}]"
            if not isinstance(entry, dict):
                raise ConfigError(f"{where}: must be a table")
            # Rules that read a second recorded run resolve its path relative
            # to this config file's own directory, not the process CWD - so
            # they alone need base_dir; every other parser stays a plain
            # (entry, where) function.
            if name in _PATH_TAKING_RULES:
                rules.append(parser(entry, where, base_dir or Path(".")))
            else:
                rules.append(parser(entry, where))
    if not rules:
        raise ConfigError(f"{source}: no assertions configured under [assert]")
    return rules


def _reject_unknown(entry: dict, allowed: set, where: str) -> None:
    unknown = sorted(set(entry) - allowed)
    if unknown:
        raise ConfigError(
            f"{where}: unknown key '{unknown[0]}' - allowed keys are: {', '.join(sorted(allowed))}"
        )


def _number(entry: dict, key: str, where: str):
    value = entry[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: {key} must be a number, got {value!r}")
    return value


def _string(entry: dict, key: str, where: str) -> str:
    value = entry[key]
    if not isinstance(value, str):
        raise ConfigError(f"{where}: {key} must be a string, got {value!r}")
    if not value.strip():
        raise ConfigError(f"{where}: {key} must not be empty")
    return value


def _parse_window_budget(entry: dict, where: str) -> WindowBudget:
    _reject_unknown(entry, {"max_pct", "segment"}, where)
    if "max_pct" not in entry:
        raise ConfigError(f"{where}: max_pct is required")
    max_pct = _number(entry, "max_pct", where)
    if not 0 < max_pct <= 100:
        raise ConfigError(
            f"{where}: max_pct must be greater than 0 and at most 100, got {max_pct!r}"
        )
    segment = _string(entry, "segment", where) if "segment" in entry else None
    return WindowBudget(max_pct=max_pct, segment=segment)


def _parse_grounded(entry: dict, where: str) -> Grounded:
    _reject_unknown(entry, {"tag", "warn_dead"}, where)
    if "tag" not in entry:
        raise ConfigError(f"{where}: tag is required")
    tag = _string(entry, "tag", where)
    warn_dead = entry.get("warn_dead", False)
    if not isinstance(warn_dead, bool):
        raise ConfigError(f"{where}: warn_dead must be a boolean, got {warn_dead!r}")
    return Grounded(tag=tag, warn_dead=warn_dead)


def _parse_requires_segment(entry: dict, where: str) -> RequiresSegment:
    _reject_unknown(entry, {"kind", "when_model"}, where)
    if "kind" not in entry:
        raise ConfigError(f"{where}: kind is required")
    kind = _string(entry, "kind", where)
    when_model = _string(entry, "when_model", where) if "when_model" in entry else None
    return RequiresSegment(kind=kind, when_model=when_model)


def _load_run(entry: dict, key: str, where: str, base_dir: Path) -> dict:
    """Resolve and normalize a second recorded run named by `entry[key]`.

    Read through the same two ordinary functions the CLI itself calls, so a
    rule's second run goes through exactly one code path, not a private one.
    """
    path = base_dir / _string(entry, key, where)
    if not path.exists():
        raise ConfigError(f"{where}: {key} not found: {path}")
    events, _ = load_events(path)
    return build_report_data(events)


def _parse_metamorphic(entry: dict, where: str, base_dir: Path) -> Metamorphic:
    _reject_unknown(entry, {"variant", "relation", "segment"}, where)
    for key in ("variant", "relation", "segment"):
        if key not in entry:
            raise ConfigError(f"{where}: {key} is required")
    relation = _string(entry, "relation", where)
    if relation not in Metamorphic.RELATIONS:
        raise ConfigError(
            f"{where}: relation must be one of {', '.join(Metamorphic.RELATIONS)}, got {relation!r}"
        )
    return Metamorphic(
        variant_data=_load_run(entry, "variant", where, base_dir),
        relation=relation,
        segment=_string(entry, "segment", where),
    )


def _parse_segment_diff(entry: dict, where: str, base_dir: Path) -> SegmentDiff:
    _reject_unknown(entry, {"baseline", "max_token_delta", "segment"}, where)
    if "baseline" not in entry:
        raise ConfigError(f"{where}: baseline is required")
    if "max_token_delta" not in entry:
        raise ConfigError(f"{where}: max_token_delta is required")
    max_token_delta = _number(entry, "max_token_delta", where)
    if max_token_delta < 0:
        raise ConfigError(f"{where}: max_token_delta must not be negative, got {max_token_delta!r}")
    segment = _string(entry, "segment", where) if "segment" in entry else None
    return SegmentDiff(
        baseline_data=_load_run(entry, "baseline", where, base_dir),
        max_token_delta=max_token_delta,
        segment=segment,
    )


# Built-in relations only. A plugin hook (§12) would register here; nothing
# third-party loads in this slice.
_PARSERS = {
    WindowBudget.NAME: _parse_window_budget,
    Grounded.NAME: _parse_grounded,
    RequiresSegment.NAME: _parse_requires_segment,
    SegmentDiff.NAME: _parse_segment_diff,
    Metamorphic.NAME: _parse_metamorphic,
}

#: Rules whose config names a second recorded run, so their parser takes the
#: config file's own directory to resolve that path against.
_PATH_TAKING_RULES = {
    SegmentDiff.NAME,
    Metamorphic.NAME,
}
