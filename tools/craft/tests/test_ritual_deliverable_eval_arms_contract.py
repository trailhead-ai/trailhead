"""Contract tests for the `ritual-deliverable-names-its-record` eval's `arms/` directory.

Pins that every arm claiming to mirror current committed prose ("live") really is a
byte-identical rebuild of its declared source documents plus the derived reader-rule
tail, and that every file present on disk is accounted for by this manifest — either
as a live rebuild or as a declared, one-line-justified "frozen" historical record. A
frozen arm is a committed copy of the prose a measurement was taken against; nothing
here rebuilds it, because the prose it copies exists only in repository history.

Does not assert anything about the eval's *measured* result (pass/fail verdicts,
run counts) — that is recorded prose, decided by dispatched runs, not a behaviour
this suite pins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
GIT_ROOT = REPO_ROOT.parent.parent
SKILLS_DIR = REPO_ROOT / "plugins" / "craft" / "skills"
EVAL_DIR = REPO_ROOT / "plugins" / "craft" / "evals" / "ritual-deliverable-names-its-record"
ARMS_DIR = EVAL_DIR / "arms"
OUTPOST_RULES = REPO_ROOT.parent / "outpost" / "plugins" / "outpost" / "rules.md"

TAIL_HEADING = "## Record links"


def _record_links_tail() -> str:
    """The reader-rule tail every 'live' arm with a tail carries: a leading newline
    followed by the `## Record links` section of outpost's rules.md, which runs to
    end-of-file (the section IS the remainder of the file from its heading)."""
    text = OUTPOST_RULES.read_text(encoding="utf-8")
    idx = text.find(TAIL_HEADING)
    if idx == -1:
        raise AssertionError(
            f"OUTPOST_RULES no longer contains a {TAIL_HEADING!r} heading, so the "
            "reader-rule tail every live arm is built from cannot be derived. The arms "
            "are built across two tools: fix the heading or update TAIL_HEADING here."
        )
    return "\n" + text[idx:]


def _rebuild(sources: list[str], with_tail: bool) -> str:
    parts = [(SKILLS_DIR / src).read_text(encoding="utf-8") for src in sources]
    built = "\n".join(parts)
    if with_tail:
        built += _record_links_tail()
    return built


# Manifest over every file in arms/*.md. "live" entries are rebuilt and asserted
# byte-identical; "frozen" entries carry a one-line reason and are asserted on
# nothing but their own existence via the exhaustiveness check below.
LIVE_ARMS = {
    "baseline.md": (["slice/SKILL.md"], False),
    "rule-only.md": (["slice/SKILL.md"], True),
    "brainstorm-rule-only.md": (["brainstorm/SKILL.md"], True),
    "distill-rule-only.md": (["distill/SKILL.md"], True),
    "gauntlet-rule-only.md": (["gauntlet/SKILL.md"], True),
    "plan-treatment.md": (["plan/SKILL.md"], True),
    "review-treatment.md": (["review/SKILL.md"], True),
    "execute-rule-only.md": (["execute/SKILL.md", "_shared/execute.md"], True),
}

# The commit every arm frozen by the record-links rule salience treatment copies
# both its source and its tail from: the treatment's base commit. Each of those
# arms was measured against prose exactly as it stood there.
PRE_RULE_EDIT_REVISION = "73bf9b22"

# The treatment rewrites the bytes of every *tailed* live arm, so each one has a
# frozen pre-treatment twin named `<live stem>-pre-rule-edit.md`, copied from that
# live arm's own sources plus the tail, both at PRE_RULE_EDIT_REVISION. Deriving
# the twins from LIVE_ARMS keeps the two facts — which arms carry a tail, and which
# have a frozen twin — from drifting apart as arms are added.
PRE_RULE_EDIT_LIVE_ARMS = tuple(
    name for name, (_sources, with_tail) in LIVE_ARMS.items() if with_tail
)


def _pre_rule_edit_name(live_arm: str) -> str:
    return f"{live_arm.removesuffix('.md')}-pre-rule-edit.md"


_REJECTED_VARIANT = (
    "frozen record of a rejected prose variant, kept as evidence "
    "(expected.md, 'What that means for the arms')"
)

_PRE_RULE_EDIT_REASON = (
    "frozen pre-treatment copy of {live}, source and tail both copied from "
    "{rev} — the record-links rule salience treatment rewrites every tailed live "
    "arm's bytes, so this copy preserves what the committed captures under runs/ "
    "were actually measured against (expected.md, 'Extension — Task 1 of "
    "task/distill-closing-report-links-the-adr-it-wrote-every-run')"
)

FROZEN_ARMS = {
    "treatment.md": _REJECTED_VARIANT,
    "reader-absent.md": _REJECTED_VARIANT,
    "plan-rule-only.md": (
        "frozen pre-edit baseline; post-edit mirror is plan-treatment.md "
        "(expected.md, 'Extension — Task 5')"
    ),
    "review-rule-only.md": (
        "frozen pre-edit baseline; post-edit mirror is review-treatment.md "
        "(expected.md, 'Extension — Task 5')"
    ),
    "slice-frozen-rule-only.md": (
        "frozen pre-change baseline for slice's two termination sites, copied from "
        "slice/SKILL.md at 0336687c (historical by construction — its source "
        "revision predates the treatment commit and can never be a live rebuild); "
        "post-change mirror is rule-only.md (expected.md, 'Extension — Task 1 of "
        "task/both-slice-termination-outcomes-are-measured-not-assumed')"
    ),
} | {
    _pre_rule_edit_name(live): _PRE_RULE_EDIT_REASON.format(
        live=live, rev=PRE_RULE_EDIT_REVISION
    )
    for live in PRE_RULE_EDIT_LIVE_ARMS
}


def _drift_message(arm_name, sources, with_tail, expected, actual) -> str:
    """Locate the first differing offset and quote a window around it, so a drift in a
    large arm (execute-rule-only.md is ~85KB) names the region that actually moved."""
    limit = min(len(expected), len(actual))
    offset = next((i for i in range(limit) if expected[i] != actual[i]), limit)
    lo, hi = max(0, offset - 80), offset + 120
    return (
        f"{arm_name} has drifted from its declared construction "
        f"(sources={sources}, tail={with_tail}); first difference at offset {offset} "
        f"(expected {len(expected)} chars, actual {len(actual)}):\n"
        f"  expected: {expected[lo:hi]!r}\n"
        f"  actual:   {actual[lo:hi]!r}"
    )


def _arm_files() -> list[Path]:
    return sorted(ARMS_DIR.glob("*.md"))


def test_every_arm_on_disk_has_a_manifest_entry():
    """Non-vacuity + exhaustiveness guard: a new arms/*.md file with no declared
    construction must turn this red, so the frozen carve-out cannot grow silently."""
    declared = set(LIVE_ARMS) | set(FROZEN_ARMS)
    on_disk = {p.name for p in _arm_files()}
    assert on_disk, "expected at least one arm file under arms/"
    undeclared = on_disk - declared
    assert not undeclared, f"arm file(s) with no manifest entry: {sorted(undeclared)}"
    missing = declared - on_disk
    assert not missing, f"manifest entries with no file on disk: {sorted(missing)}"


@pytest.mark.parametrize("arm_name", sorted(LIVE_ARMS), ids=lambda n: n)
def test_live_arm_rebuilds_byte_identically(arm_name: str):
    sources, with_tail = LIVE_ARMS[arm_name]
    expected = _rebuild(sources, with_tail)
    actual = (ARMS_DIR / arm_name).read_text(encoding="utf-8")
    assert actual == expected, _drift_message(arm_name, sources, with_tail, expected, actual)
