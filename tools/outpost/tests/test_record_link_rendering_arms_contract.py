"""Contract tests for the `record-link-rendering` eval's `arms/` directory.

Pins that the "live" treatment arm — the one future re-run-trigger dispatches use
— really is a byte-identical rebuild of the baseline brief plus the *current*
`## Record links` section of `tools/outpost/plugins/outpost/rules.md`, and that the
frozen `treatment-pre-rule-edit.md` arm really is that same construction pinned to
the exact commit the committed `MANUAL-EVAL.md` rows were measured against — not a
hand-edit, a wrong commit, or a stale concatenation.

Mirrors `tools/craft/tests/test_ritual_deliverable_eval_arms_contract.py`'s
live/frozen manifest pattern for the same reason: the `## Record links` section is
edited from time to time, and every arm built from it needs its provenance pinned
rather than assumed.

Does not assert anything about the eval's *measured* result (pass/fail verdicts,
run counts) — that is recorded prose, decided by dispatched runs, not a behaviour
this suite pins.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GIT_ROOT = REPO_ROOT.parent.parent
EVAL_DIR = REPO_ROOT / "plugins" / "outpost" / "evals" / "record-link-rendering"
ARMS_DIR = EVAL_DIR / "arms"
OUTPOST_RULES = REPO_ROOT / "plugins" / "outpost" / "rules.md"
OUTPOST_RULES_GIT_PATH = "tools/outpost/plugins/outpost/rules.md"

TAIL_HEADING = "## Record links"
SEPARATOR = "\n---\n\n"

# The commit the frozen arm's `## Record links` tail is pinned to: the last commit
# that touched the section before the record-links rule salience treatment
# (`bc8c493e`) landed. `MANUAL-EVAL.md`'s committed rows for this case were measured
# against exactly this text.
FROZEN_TAIL_REVISION = "19866500"

# A revision whose `## Record links` text differs from both the frozen arm's pinned
# text and the live arm's current text — used only to prove the reconstruction
# below is sensitive to which revision is passed, not merely to its own existence.
_WRONG_TAIL_REVISION = "2550a3fa"

BASELINE = "baseline.md"
LIVE_TREATMENT = "treatment.md"
FROZEN_TREATMENT = "treatment-pre-rule-edit.md"

ALL_MANIFESTED_ARMS = {BASELINE, LIVE_TREATMENT, FROZEN_TREATMENT}


def _git_show(commit: str, path: str) -> str:
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


def _tail_at_revision(revision: str) -> str:
    return _tail_from_text(_git_show(revision, OUTPOST_RULES_GIT_PATH))


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


def test_frozen_treatment_arm_matches_its_declared_provenance():
    """Pins that the frozen `treatment-pre-rule-edit.md` arm really is the baseline
    brief plus the `## Record links` section exactly as it stood at
    `FROZEN_TAIL_REVISION` — not a hand-edit or a wrong commit — so a corrupted
    frozen arm cannot pass this suite on existence alone.

    IF THIS GOES RED: never edit the frozen arm to make it pass. The committed
    `MANUAL-EVAL.md` rows for this case were measured against exactly the text
    pinned at `FROZEN_TAIL_REVISION`; regenerating the frozen file from today's
    `rules.md` would silently rewrite the evidence those rows describe.
    """
    expected = _baseline_text() + SEPARATOR + _tail_at_revision(FROZEN_TAIL_REVISION)
    actual = (ARMS_DIR / FROZEN_TREATMENT).read_text(encoding="utf-8")
    assert actual == expected, (
        f"{FROZEN_TREATMENT} has drifted from its declared provenance "
        f"(baseline brief + `## Record links` at {FROZEN_TAIL_REVISION!r})."
    )


def test_frozen_treatment_arm_reconstruction_is_sensitive_to_tail_revision():
    """Proves the provenance pin above is not vacuous: reconstructing the frozen
    arm against a deliberately wrong tail revision must NOT match the committed
    file, so a correct match against `FROZEN_TAIL_REVISION` is evidence the pin is
    actually load-bearing rather than trivially satisfied regardless of which
    revision is named."""
    wrong = _baseline_text() + SEPARATOR + _tail_at_revision(_WRONG_TAIL_REVISION)
    actual = (ARMS_DIR / FROZEN_TREATMENT).read_text(encoding="utf-8")
    assert wrong != actual, (
        f"{FROZEN_TREATMENT} matched a reconstruction built from the wrong tail "
        f"revision ({_WRONG_TAIL_REVISION!r}) — the tail-revision pin is not "
        "discriminating anything."
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
