"""Shared pieces for the contract tests that pin an eval case's `arms/` directory.

An arm is a brief with a ruleset appended. A "live" arm must rebuild byte-for-byte
from today's `rules.md`; a "frozen" arm must rebuild byte-for-byte from `rules.md`
at the commit its committed measurements were taken against. The helpers here carry
what every such test needs so each case's test states only its own manifest and
pins.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

OUTPOST_ROOT = Path(__file__).parent.parent
GIT_ROOT = OUTPOST_ROOT.parent.parent
EVALS_DIR = OUTPOST_ROOT / "plugins" / "outpost" / "evals"
OUTPOST_RULES = OUTPOST_ROOT / "plugins" / "outpost" / "rules.md"
OUTPOST_RULES_GIT_PATH = "tools/outpost/plugins/outpost/rules.md"

SEPARATOR = "\n---\n\n"


def git_show(commit: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=GIT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"`git show {commit}:{path}` failed (exit {result.returncode}): "
            f"{result.stderr.strip()}\n"
            "This most often means the checkout is shallow and does not carry "
            f"commit {commit} — confirm `fetch-depth: 0` is still set in "
            ".github/workflows/tests.yml before treating this as arm corruption."
        )
    return result.stdout


def rules_at(revision: str) -> str:
    return git_show(revision, OUTPOST_RULES_GIT_PATH)


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
