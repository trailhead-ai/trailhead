"""Shared pieces for the contract tests that pin an eval case's `arms/` directory.

An arm is a brief with a ruleset appended. A "live" arm must rebuild byte-for-byte
from today's `rules.md`. A "frozen" arm is a committed copy of the ruleset as it
stood when its measurements were taken; that text exists only in repository history,
so nothing here rebuilds it. The helpers here carry what every such test needs so
each case's test states only its own manifest and pins.
"""

from __future__ import annotations

from pathlib import Path

OUTPOST_ROOT = Path(__file__).parent.parent
EVALS_DIR = OUTPOST_ROOT / "plugins" / "outpost" / "evals"
OUTPOST_RULES = OUTPOST_ROOT / "plugins" / "outpost" / "rules.md"

SEPARATOR = "\n---\n\n"


def current_rules() -> str:
    return OUTPOST_RULES.read_text(encoding="utf-8")


def read_arm(arms_dir: Path, name: str) -> str:
    return (arms_dir / name).read_text(encoding="utf-8")


def build_arm(baseline: str, ruleset: str) -> str:
    """The arm construction: the baseline brief, a rule, then the ruleset text."""
    return baseline + SEPARATOR + ruleset


def assert_arms_accounted_for(arms_dir: Path, manifest: set[str]) -> None:
    on_disk = {p.name for p in arms_dir.glob("*.md")}
    assert on_disk, f"expected at least one arm file under {arms_dir}"
    undeclared = on_disk - manifest
    assert not undeclared, f"arm file(s) with no manifest entry: {sorted(undeclared)}"
    missing = manifest - on_disk
    assert not missing, f"manifest entries with no file on disk: {sorted(missing)}"
