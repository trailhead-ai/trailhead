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


def _record_links_tail() -> str:
    """The reader-rule tail every 'live' arm with a tail carries: a leading newline
    followed by the `## Record links` section of outpost's rules.md, which runs to
    end-of-file (the section IS the remainder of the file from its heading)."""
    text = OUTPOST_RULES.read_text(encoding="utf-8")
    idx = text.find(TAIL_HEADING)
    if idx == -1:
        raise AssertionError(
            f"{OUTPOST_RULES} no longer contains a {TAIL_HEADING!r} heading, so the "
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

_REJECTED_VARIANT = (
    "frozen record of a rejected prose variant, kept as evidence "
    "(expected.md, 'What that means for the arms')"
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


# The frozen arm's source revision: the run base of the slice that treated slice's
# two termination sites (bd72afa1), so its content is slice/SKILL.md as it stood
# immediately before that edit.
FROZEN_SLICE_ARM = "slice-frozen-rule-only.md"
FROZEN_SLICE_SOURCE_COMMIT = "0336687c"
FROZEN_SLICE_SOURCE_PATH = "tools/craft/plugins/craft/skills/slice/SKILL.md"


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


def test_frozen_slice_arm_matches_its_declared_provenance():
    """Pins that slice-frozen-rule-only.md really is `git show 0336687c:.../SKILL.md`
    plus the reader-rule tail — not a hand-edit, a wrong commit, or a stale
    concatenation — so a corrupted baseline cannot pass this suite on existence
    alone (the risk `test_every_arm_on_disk_has_a_manifest_entry` does not cover
    for a frozen entry).

    Design decision on tail drift: this reconstructs against the *current* reader
    tail via `_record_links_tail()`, the same helper the live arms use, rather than
    pinning the tail to a fixed revision of its own. A live arm with a tail is
    already coupled to today's `## Record links` section the same way, so this
    frozen arm inherits an existing coupling rather than introducing a new one. If
    the tail changes, this test goes red for the same reason every tailed live arm
    would — `_drift_message` below reports the offset, which lands inside the tail
    region (at or after the historical source's own length) and is distinguishable
    from a drift inside the source region, which would instead indicate a wrong
    commit or a hand-edit.

    IF THIS GOES RED BECAUSE OUTPOST'S `## Record links` SECTION CHANGED: the
    cheapest-looking fix — regenerating `slice-frozen-rule-only.md` from the new
    tail — is wrong. This arm is frozen precisely so the committed captures under
    `evals/ritual-deliverable-names-its-record/runs/` stay measured against the
    exact prose they were actually dispatched against; regenerating it would
    silently rewrite that evidence out from under the already-committed captures
    without re-running anything. The correct fix is to re-record the measurement —
    dispatch new baseline runs against the frozen arm's real historical tail (built
    from the `## Record links` section as it stood at the commit named by
    `FROZEN_SLICE_SOURCE_COMMIT`'s neighborhood, not today's), and commit the new
    captures alongside a corrected arm. Never edit this arm to make the test pass.
    """
    historical_source = _git_show(FROZEN_SLICE_SOURCE_COMMIT, FROZEN_SLICE_SOURCE_PATH)
    expected = historical_source + _record_links_tail()
    actual = (ARMS_DIR / FROZEN_SLICE_ARM).read_text(encoding="utf-8")
    assert actual == expected, (
        _drift_message(
            FROZEN_SLICE_ARM,
            [f"{FROZEN_SLICE_SOURCE_COMMIT}:{FROZEN_SLICE_SOURCE_PATH}"],
            True,
            expected,
            actual,
        )
        + "\n\nIf this drift lands inside the tail region (outpost's `## Record "
        "links` section changed): do NOT regenerate slice-frozen-rule-only.md to "
        "match. This frozen arm preserves the exact prose the committed captures "
        "under evals/ritual-deliverable-names-its-record/runs/ were measured "
        "against — regenerating it silently rewrites that evidence. The fix is to "
        "re-record the measurement (dispatch new runs against the historical tail, "
        "commit new captures), never to edit this file."
    )
