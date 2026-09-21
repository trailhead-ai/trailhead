"""Contract tests for the `ritual-deliverable-names-its-record` eval's `arms/` directory.

Pins that every arm claiming to mirror current committed prose ("live") really is a
byte-identical rebuild of its declared source documents plus the derived reader-rule
tail, and that every file present on disk is accounted for by this manifest — either
as a live rebuild or as a declared, one-line-justified "frozen" historical record.

Does not assert anything about the eval's *measured* result (pass/fail verdicts,
run counts) — that is recorded prose, decided by dispatched runs, not a behaviour
this suite pins.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
GIT_ROOT = REPO_ROOT.parent.parent
SKILLS_DIR = REPO_ROOT / "plugins" / "craft" / "skills"
EVAL_DIR = REPO_ROOT / "plugins" / "craft" / "evals" / "ritual-deliverable-names-its-record"
ARMS_DIR = EVAL_DIR / "arms"
OUTPOST_RULES = REPO_ROOT.parent / "outpost" / "plugins" / "outpost" / "rules.md"

TAIL_HEADING = "## Record links"
OUTPOST_RULES_GIT_PATH = "tools/outpost/plugins/outpost/rules.md"


def _git_show(commit: str, path: str) -> str:
    """Read a historical blob via `git show`, never the working tree.

    This couples the test to reading repository history at full depth — the same
    coupling that failed under CI's shallow clone until `fetch-depth: 0` landed
    (fix(craft): fetch full history in CI so the grader regression test can read
    its base-commit blob, currently `.github/workflows/tests.yml`). If that
    setting is ever reverted, `git show` fails here for an environment reason,
    not a content one, and the assertion below names that possibility explicitly
    rather than letting a shallow-clone failure read as arm corruption.
    """
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


def _record_links_tail(revision: str | None = None) -> str:
    """The reader-rule tail every 'live' arm with a tail carries: a leading newline
    followed by the `## Record links` section of outpost's rules.md, which runs to
    end-of-file (the section IS the remainder of the file from its heading).

    With `revision=None` (every live arm's own reconstruction), this reads the
    *working tree* — correct, because "live" means "matches current committed
    prose," and current committed prose includes whatever tail is committed right
    now. With a `revision`, this reads the tail via `git show <revision>:...`
    instead — what every *frozen* arm now reconstructs against, so a frozen arm's
    expected content stays pinned to the revision it was actually measured
    against rather than silently tracking a later edit to `rules.md`.
    """
    if revision is None:
        text = OUTPOST_RULES.read_text(encoding="utf-8")
    else:
        text = _git_show(revision, OUTPOST_RULES_GIT_PATH)
    idx = text.find(TAIL_HEADING)
    if idx == -1:
        raise AssertionError(
            f"{'OUTPOST_RULES' if revision is None else f'{revision}:{OUTPOST_RULES_GIT_PATH}'} "
            f"no longer contains a {TAIL_HEADING!r} heading, so the reader-rule tail every "
            "live arm is built from cannot be derived. The arms are built across two "
            "tools: fix the heading or update TAIL_HEADING here."
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

# The revision every arm frozen by the record-links rule salience treatment has
# BOTH its source and its tail pinned to: the treatment's base commit. Each of
# those arms was measured (or is pre-registered to be measured) against prose
# exactly as it stood there.
PRE_RULE_EDIT_REVISION = "73bf9b22"

_SKILLS_GIT_PREFIX = "tools/craft/plugins/craft/skills"

# The treatment rewrites the bytes of every *tailed* live arm, so each one has a
# frozen pre-treatment twin named `<live stem>-pre-rule-edit.md`, built from that
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
    "frozen pre-treatment copy of {live}, source and tail both pinned at "
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
        "frozen pre-change baseline for slice's two termination sites, recovered from "
        "git show 0336687c:.../slice/SKILL.md (historical by construction — its source "
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


# The frozen slice arm's source revision: the run base of the slice that treated
# slice's two termination sites (bd72afa1), so its content is slice/SKILL.md as it
# stood immediately before that edit.
FROZEN_SLICE_SOURCE_COMMIT = "0336687c"
FROZEN_SLICE_SOURCE_PATH = "tools/craft/plugins/craft/skills/slice/SKILL.md"

# Every frozen arm that carries the reader-rule tail: arm name -> (source specs,
# tail revision). Each source spec is (git-relative path, revision) so a frozen
# arm's *source* revision and *tail* revision can be pinned independently — the
# pre-existing slice-frozen arm's source predates the treatment commit that
# produced today's rule-only.md, while the seven newly frozen arms' sources are
# this same commit as their tail.
FROZEN_TAILED_ARMS: dict[str, tuple[list[tuple[str, str]], str]] = {
    _pre_rule_edit_name(live): (
        [
            (f"{_SKILLS_GIT_PREFIX}/{src}", PRE_RULE_EDIT_REVISION)
            for src in LIVE_ARMS[live][0]
        ],
        PRE_RULE_EDIT_REVISION,
    )
    for live in PRE_RULE_EDIT_LIVE_ARMS
} | {
    "slice-frozen-rule-only.md": (
        [(FROZEN_SLICE_SOURCE_PATH, FROZEN_SLICE_SOURCE_COMMIT)],
        PRE_RULE_EDIT_REVISION,
    ),
}

# A tail revision that predates the current `## Record links` wording (confirmed
# by inspection: its prose differs from every wording the live/frozen arms above
# are built from) — used only to prove the reconstruction below is sensitive to
# which revision is passed, not merely to its own existence.
_WRONG_TAIL_REVISION = "d4230e6c"


def _rebuild_frozen(sources: list[tuple[str, str]], tail_revision: str) -> str:
    parts = [_git_show(rev, path) for path, rev in sources]
    built = "\n".join(parts)
    built += _record_links_tail(tail_revision)
    return built


@pytest.mark.parametrize("arm_name", sorted(FROZEN_TAILED_ARMS), ids=lambda n: n)
def test_frozen_tailed_arm_matches_its_declared_provenance(arm_name: str):
    """Pins that every tailed frozen arm really is its declared source revision(s)
    plus its declared tail revision — not a hand-edit, a wrong commit, or a stale
    concatenation — so a corrupted frozen arm cannot pass this suite on existence
    alone (the risk `test_every_arm_on_disk_has_a_manifest_entry` does not cover
    for a frozen entry).

    Both the source and the tail are now pinned to an explicit revision via
    `_record_links_tail(revision=...)` rather than either one reading the working
    tree. This replaces the previous design, which reconstructed a frozen arm's
    tail from the *current* `rules.md` — sound only until the tail ever changed,
    which is exactly what the record-links rule salience treatment this task
    pre-registers is about to do. Pinning the tail here means a frozen arm no
    longer goes red for an environment reason (the rule text moved) once that
    edit lands; it only goes red for a content reason (the frozen file itself
    was altered).

    IF THIS GOES RED BECAUSE OUTPOST'S `## Record links` SECTION CHANGED AT ITS
    PINNED REVISION: that should be impossible — a pinned revision's blob never
    changes — so this instead means the wrong revision was pinned, or the frozen
    file was hand-edited. The cheapest-looking fix — regenerating the frozen file
    from today's tail — is wrong regardless: these arms are frozen precisely so
    the committed captures under `evals/ritual-deliverable-names-its-record/runs/`
    stay measured against the exact prose they were actually dispatched against.
    Never edit a frozen arm to make this test pass; re-record the measurement
    instead.
    """
    sources, tail_revision = FROZEN_TAILED_ARMS[arm_name]
    expected = _rebuild_frozen(sources, tail_revision)
    actual = (ARMS_DIR / arm_name).read_text(encoding="utf-8")
    assert actual == expected, (
        _drift_message(arm_name, sources, True, expected, actual)
        + f"\n\nExpected source revision(s): {sources}; expected tail revision: "
        f"{tail_revision!r}. If this drift lands inside the tail region: do NOT "
        "regenerate this frozen arm to match today's rules.md — that silently "
        "rewrites the evidence the committed captures under runs/ were measured "
        "against. Re-record the measurement instead."
    )


@pytest.mark.parametrize("arm_name", sorted(FROZEN_TAILED_ARMS), ids=lambda n: n)
def test_frozen_tailed_arm_reconstruction_is_sensitive_to_tail_revision(arm_name: str):
    """Proves `test_frozen_tailed_arm_matches_its_declared_provenance` is not a
    vacuous pass — reconstructing the same arm against a deliberately wrong tail
    revision must NOT match the committed file, so a correct match against the
    declared revision is evidence the pin is actually load-bearing rather than
    trivially satisfied regardless of which revision is named."""
    sources, _correct_tail_revision = FROZEN_TAILED_ARMS[arm_name]
    wrong = _rebuild_frozen(sources, _WRONG_TAIL_REVISION)
    actual = (ARMS_DIR / arm_name).read_text(encoding="utf-8")
    assert wrong != actual, (
        f"{arm_name} matched a reconstruction built from the wrong tail revision "
        f"({_WRONG_TAIL_REVISION!r}) — the tail-revision pin is not discriminating "
        "anything, which means the 'matches its declared provenance' test above "
        "could pass on coincidence rather than on the pin actually being checked."
    )


def test_frozen_slice_arm_reconstruction_is_sensitive_to_source_revision():
    """Proves the source-revision pin for slice-frozen-rule-only.md is load-bearing:
    reconstructing it from HEAD's slice/SKILL.md (post-edit, per the diff this arm
    is frozen specifically to predate) instead of its declared 0336687c must NOT
    match the committed file."""
    sources, tail_revision = FROZEN_TAILED_ARMS["slice-frozen-rule-only.md"]
    wrong_sources = [(path, "HEAD") for path, _rev in sources]
    wrong = _rebuild_frozen(wrong_sources, tail_revision)
    actual = (ARMS_DIR / "slice-frozen-rule-only.md").read_text(encoding="utf-8")
    assert wrong != actual, (
        "slice-frozen-rule-only.md matched a reconstruction built from HEAD's "
        "slice/SKILL.md instead of its declared 0336687c — the source-revision "
        "pin is not discriminating anything."
    )
