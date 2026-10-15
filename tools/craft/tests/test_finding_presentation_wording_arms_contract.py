"""Contract tests for the `finding-presentation-wording` eval's `arms/` and `fixtures/`.

Every arm is the concatenation of source documents, each read at a pinned git revision.
The manifest below names those sources per arm. These tests pin that each committed arm
file is exactly what its manifest composes, that composing from a different revision of a
pinned source does not give the same bytes, that the manifest and the directory list the
same files, and that the grader reads every reviewer label a canned report carries as the
case's pre-registered grammar classes it.

Nothing here asserts the eval's measured result; that is decided by dispatched runs.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from test_ritual_output_wording_arms_contract import (
    GIT_ROOT,
    Source,
    _earlier_revision,
    _git,
    arm_matches,
    compose,
    manifest_mismatches,
)

CASE_DIR = (
    GIT_ROOT / "tools" / "craft" / "plugins" / "craft" / "evals" / "finding-presentation-wording"
)
ARMS_DIR = CASE_DIR / "arms"
FIXTURES_DIR = CASE_DIR / "fixtures"
GRADER = GIT_ROOT / "tools" / "craft" / "plugins" / "craft" / "scripts" / "wording_grader.py"

# origin/main when the case was cut: every baseline arm reads every source here.
BASELINE_REVISION = "f68200933f69e0c00bc79634fb4758b8fa039c1c"

# The commit carrying the shared-contract edit: treated arms read `council.md` (and, at
# consult, `consult/SKILL.md`) from here and every other source from the baseline.
EDIT_REVISION = "42e1b088cbb9809c9bd4cf41d226c96da8f480b9"

# The commit before the calibration prose edit: the `precal-consult` arm reads `council.md` and
# `consult/SKILL.md` from here and every other source from the baseline.
PRECAL_REVISION = "f98d25850e9a770fd4a14ac585e5029931782ac8"

PLUGIN = "tools/craft/plugins/craft"
RULES = f"{PLUGIN}/rules.md"
COUNCIL = f"{PLUGIN}/skills/_shared/council.md"
CONSULT_SKILL = f"{PLUGIN}/skills/consult/SKILL.md"
PLAN_SKILL = f"{PLUGIN}/skills/plan/SKILL.md"
GAUNTLET_SKILL = f"{PLUGIN}/skills/gauntlet/SKILL.md"
GAUNTLET_DISPOSITIONS = f"{PLUGIN}/skills/gauntlet/dispositions.md"
DRIVE_SKILL = f"{PLUGIN}/skills/drive/SKILL.md"

ARM_SOURCES: dict[str, list[Source]] = {
    "baseline-consult.md": [
        (RULES, BASELINE_REVISION),
        (CONSULT_SKILL, BASELINE_REVISION),
        (COUNCIL, BASELINE_REVISION),
    ],
    "baseline-plan.md": [
        (RULES, BASELINE_REVISION),
        (PLAN_SKILL, BASELINE_REVISION),
        (COUNCIL, BASELINE_REVISION),
    ],
    "baseline-specreview.md": [
        (RULES, BASELINE_REVISION),
        (GAUNTLET_SKILL, BASELINE_REVISION),
        (GAUNTLET_DISPOSITIONS, BASELINE_REVISION),
        (COUNCIL, BASELINE_REVISION),
    ],
    "baseline-driver.md": [
        (RULES, BASELINE_REVISION),
        (DRIVE_SKILL, BASELINE_REVISION),
        (COUNCIL, BASELINE_REVISION),
    ],
    "treated-consult.md": [
        (RULES, BASELINE_REVISION),
        (CONSULT_SKILL, EDIT_REVISION),
        (COUNCIL, EDIT_REVISION),
    ],
    "treated-plan.md": [
        (RULES, BASELINE_REVISION),
        (PLAN_SKILL, BASELINE_REVISION),
        (COUNCIL, EDIT_REVISION),
    ],
    "treated-specreview.md": [
        (RULES, BASELINE_REVISION),
        (GAUNTLET_SKILL, BASELINE_REVISION),
        (GAUNTLET_DISPOSITIONS, BASELINE_REVISION),
        (COUNCIL, EDIT_REVISION),
    ],
    "treated-driver.md": [
        (RULES, BASELINE_REVISION),
        (DRIVE_SKILL, BASELINE_REVISION),
        (COUNCIL, EDIT_REVISION),
    ],
    "precal-consult.md": [
        (RULES, BASELINE_REVISION),
        (CONSULT_SKILL, PRECAL_REVISION),
        (COUNCIL, PRECAL_REVISION),
    ],
}


@pytest.mark.parametrize("arm_name", sorted(ARM_SOURCES))
def test_arm_composed_from_its_manifest_equals_the_committed_file(arm_name: str):
    sources = ARM_SOURCES[arm_name]
    expected = compose(sources)
    actual = (ARMS_DIR / arm_name).read_text(encoding="utf-8")
    assert actual == expected, (
        f"{arm_name} differs from its manifest sources {sources}: "
        f"expected {len(expected)} chars, committed {len(actual)}"
    )


def _has_earlier_revision(path: str, revision: str) -> bool:
    return len(_git("log", "--format=%H", "-n", "2", revision, "--", path).split()) == 2


# A source with a single revision up to its pin (a file added shortly before the pin) has
# no earlier blob to compose from; the byte-identity test above is what constrains it.
_PINNED_SOURCES = [
    (arm, index)
    for arm, sources in sorted(ARM_SOURCES.items())
    for index, (path, revision) in enumerate(sources)
    if _has_earlier_revision(path, revision)
]


@pytest.mark.parametrize(
    "arm_name,index", _PINNED_SOURCES, ids=[f"{a}:{i}" for a, i in _PINNED_SOURCES]
)
def test_composing_one_source_from_a_different_revision_does_not_match(
    arm_name: str, index: int
):
    sources = list(ARM_SOURCES[arm_name])
    path, revision = sources[index]
    sources[index] = (path, _earlier_revision(path, revision))
    assert not arm_matches(ARMS_DIR / arm_name, sources), (
        f"{arm_name} still matches when source {path} is read from an earlier "
        "revision, so the revision pin does not constrain the arm"
    )


def test_every_arm_file_has_a_manifest_entry_and_every_entry_a_file():
    undeclared, missing = manifest_mismatches(ARM_SOURCES, ARMS_DIR)
    assert not undeclared, f"arm file(s) with no manifest entry: {sorted(undeclared)}"
    assert not missing, f"manifest entries with no file on disk: {sorted(missing)}"


def report_labels(text: str) -> list[str]:
    """The `### <label>` headings of a canned reports file, outside fenced blocks."""
    labels, fenced = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("### "):
            labels.append(line[4:].strip())
    return labels


def test_report_labels_reads_headings_and_skips_fenced_ones():
    text = "# T\n### Builder\nbody\n```\n### Fenced\n```\n### Security\n"
    assert report_labels(text) == ["Builder", "Security"]


REPORT_FILES = ["reports-lenses.md", "reports-consult.md", "reports-spec-review.md", "reports-calibrated.md"]


@pytest.mark.parametrize("report_file", REPORT_FILES)
def test_the_grader_reads_every_label_a_canned_report_carries_as_the_grammar_implies(report_file: str):
    path = FIXTURES_DIR / report_file
    labels = report_labels(path.read_text(encoding="utf-8"))
    assert labels, f"{report_file} carries no `### <label>` heading"
    for label in labels:
        result = subprocess.run(
            [sys.executable, str(GRADER), "--check", "reviewer-name", "--reports", str(path)],
            input=f"{label} found nothing that blocks the work.\n".encode(),
            capture_output=True,
            timeout=60,
        )
        expected = "plain" if label == "Security" else "internal"
        verdict = f"reviewer-name: {expected}\n".encode()
        assert result.stdout == verdict, (report_file, label, result.stdout, result.stderr)


BARS = GIT_ROOT / "tools" / "craft" / "plugins" / "craft" / "scripts" / "maturity_bars.py"

CALIBRATED_SPEC = (
    "# Spec\n\n## Maturity\n\n- riverside-sheet: {level}\n\n## Non-Goals\n\n"
    "- Waives: backwards compatibility \u2014 the paper booking sheets are not carried over to the new page.\n"
)


def _fenced_block(text: str) -> str:
    lines = text.splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("```"))
    last = next(i for i in range(first + 1, len(lines)) if lines[i].startswith("```"))
    return "\n".join(lines[first + 1 : last]) + "\n"


def _render(spec: str) -> str:
    result = subprocess.run(
        [sys.executable, str(BARS)], input=spec.encode(), capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.decode("utf-8")


def test_the_calibrated_reports_carry_the_block_the_renderer_prints_for_a_prototype_spec():
    fixture = (FIXTURES_DIR / "reports-calibrated.md").read_text(encoding="utf-8")
    rendered = _render(CALIBRATED_SPEC.format(level="prototype"))
    assert _fenced_block(fixture) == rendered
    assert _render(CALIBRATED_SPEC.format(level="production")) != rendered
