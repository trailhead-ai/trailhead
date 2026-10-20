"""Contract tests for the `record-link-rendering` eval's `arms/` directory.

Pins that the "live" treatment arm — the one future re-run-trigger dispatches use
— really is a byte-identical rebuild of the baseline brief plus the *current*
`## Record links` section of `tools/outpost/plugins/outpost/rules.md`. The frozen
`treatment-pre-rule-edit.md` arm is a committed copy of the arm the `MANUAL-EVAL.md`
rows were measured against, with the section as it stood at 19866500; that text
exists only in repository history, so nothing here rebuilds it.

Mirrors `tools/craft/tests/test_ritual_deliverable_eval_arms_contract.py`'s
live/frozen manifest pattern for the same reason: the `## Record links` section is
edited from time to time, and every arm on disk must be declared as one or the
other.

Does not assert anything about the eval's *measured* result (pass/fail verdicts,
run counts) — that is recorded prose, decided by dispatched runs, not a behaviour
this suite pins.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
EVAL_DIR = REPO_ROOT / "plugins" / "outpost" / "evals" / "record-link-rendering"
ARMS_DIR = EVAL_DIR / "arms"
OUTPOST_RULES = REPO_ROOT / "plugins" / "outpost" / "rules.md"

TAIL_HEADING = "## Record links"
SEPARATOR = "\n---\n\n"

BASELINE = "baseline.md"
LIVE_TREATMENT = "treatment.md"
FROZEN_TREATMENT = "treatment-pre-rule-edit.md"

ALL_MANIFESTED_ARMS = {BASELINE, LIVE_TREATMENT, FROZEN_TREATMENT}


def _tail_from_text(text: str) -> str:
    idx = text.find(TAIL_HEADING)
    if idx == -1:
        raise AssertionError(
            f"text has no {TAIL_HEADING!r} heading, so the reader-rule tail this "
            "arm is built from cannot be derived."
        )
    return text[idx:]


def _current_tail() -> str:
    return _tail_from_text(OUTPOST_RULES.read_text(encoding="utf-8"))


def _baseline_text() -> str:
    return (ARMS_DIR / BASELINE).read_text(encoding="utf-8")


def _arm_files() -> list[Path]:
    return sorted(ARMS_DIR.glob("*.md"))


def _arm_texts() -> tuple[str, str]:
    live = (ARMS_DIR / LIVE_TREATMENT).read_text(encoding="utf-8")
    frozen = (ARMS_DIR / FROZEN_TREATMENT).read_text(encoding="utf-8")
    return live, frozen


def test_every_arm_on_disk_has_a_manifest_entry():
    """Non-vacuity + exhaustiveness guard: a new arms/*.md file with no declared
    construction must turn this red, so an undeclared frozen copy cannot grow
    silently."""
    on_disk = {p.name for p in _arm_files()}
    assert on_disk, "expected at least one arm file under arms/"
    undeclared = on_disk - ALL_MANIFESTED_ARMS
    assert not undeclared, f"arm file(s) with no manifest entry: {sorted(undeclared)}"
    missing = ALL_MANIFESTED_ARMS - on_disk
    assert not missing, f"manifest entries with no file on disk: {sorted(missing)}"


def test_live_treatment_arm_rebuilds_byte_identically():
    """The live `treatment.md` arm — the one every future re-run-trigger dispatch
    uses — is the baseline brief plus the *current* `## Record links` section,
    never a stale copy."""
    expected = _baseline_text() + SEPARATOR + _current_tail()
    actual = (ARMS_DIR / LIVE_TREATMENT).read_text(encoding="utf-8")
    assert actual == expected, (
        f"{LIVE_TREATMENT} has drifted from its declared construction "
        "(baseline brief + current `## Record links` section) — rebuild it from "
        "today's rules.md."
    )


def test_live_and_frozen_treatment_arms_diverge():
    """The live and frozen arms must actually differ on disk — otherwise the whole
    live/frozen split is a distinction without a difference, and a bug that made
    both arms rebuild from the same (wrong) revision would pass every test above."""
    live, frozen = _arm_texts()
    assert live != frozen, (
        "treatment.md and treatment-pre-rule-edit.md are byte-identical — the "
        "record-links rule salience treatment (bc8c493e) changed the section, so "
        "the live and frozen arms must diverge in the tail region."
    )
