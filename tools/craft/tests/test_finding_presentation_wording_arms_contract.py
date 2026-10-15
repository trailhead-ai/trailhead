"""Contract tests for the `finding-presentation-wording` eval's `arms/` and `fixtures/`.

Every arm is the concatenation of source documents, each read at a pinned git revision.
The manifest below names those sources per arm. These tests pin that each committed arm
file is exactly what its manifest composes, that composing from a different revision of a
pinned source does not give the same bytes, that the manifest and the directory list the
same files, and that every reviewer label a canned report carries is one the case's
pre-registered grammar names.

Nothing here asserts the eval's measured result; that is decided by dispatched runs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

GIT_ROOT = Path(__file__).resolve().parents[3]
CASE_DIR = (
    GIT_ROOT / "tools" / "craft" / "plugins" / "craft" / "evals" / "finding-presentation-wording"
)
ARMS_DIR = CASE_DIR / "arms"
FIXTURES_DIR = CASE_DIR / "fixtures"
EXPECTED_MD = CASE_DIR / "expected.md"

# origin/main when the case was cut: every baseline arm reads every source here.
BASELINE_REVISION = "f68200933f69e0c00bc79634fb4758b8fa039c1c"

# The commit carrying the shared-contract edit: treated arms read `council.md` (and, at
# consult, `consult/SKILL.md`) from here and every other source from the baseline.
EDIT_REVISION = "42e1b088cbb9809c9bd4cf41d226c96da8f480b9"

PLUGIN = "tools/craft/plugins/craft"
RULES = f"{PLUGIN}/rules.md"
COUNCIL = f"{PLUGIN}/skills/_shared/council.md"
CONSULT_SKILL = f"{PLUGIN}/skills/consult/SKILL.md"
PLAN_SKILL = f"{PLUGIN}/skills/plan/SKILL.md"
GAUNTLET_SKILL = f"{PLUGIN}/skills/gauntlet/SKILL.md"
GAUNTLET_DISPOSITIONS = f"{PLUGIN}/skills/gauntlet/dispositions.md"
DRIVE_SKILL = f"{PLUGIN}/skills/drive/SKILL.md"

# A source is (repo-relative path, revision).
Source = tuple[str, str]

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
}


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=GIT_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(
            f"`git {' '.join(args)}` failed (exit {result.returncode}): "
            f"{result.stderr.strip()}\n"
            "If the checkout is shallow, the pinned revision is not available; CI "
            "fetches full history for this reason."
        )
    return result.stdout


def read_source(path: str, revision: str) -> str:
    return _git("show", f"{revision}:{path}")


def compose(sources: list[Source]) -> str:
    return "\n".join(read_source(path, revision) for path, revision in sources)


def arm_matches(arm_file: Path, sources: list[Source]) -> bool:
    return compose(sources) == arm_file.read_text(encoding="utf-8")


def manifest_mismatches(
    manifest: dict[str, list[Source]], arms_dir: Path
) -> tuple[set[str], set[str]]:
    """(files on disk with no manifest entry, manifest entries with no file)."""
    on_disk = {p.name for p in arms_dir.glob("*.md")}
    declared = set(manifest)
    return on_disk - declared, declared - on_disk


def _earlier_revision(path: str, revision: str) -> str:
    """The commit that last touched `path` before the one `revision` sees, so the
    blob there differs from the blob at `revision` by construction."""
    log = _git("log", "--format=%H", "-n", "2", revision, "--", path).split()
    assert len(log) == 2, f"{path} has fewer than two revisions up to {revision}"
    return log[1]


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


def test_an_arm_file_with_no_manifest_entry_is_reported(tmp_path: Path):
    (tmp_path / "declared.md").write_text("a")
    (tmp_path / "stray.md").write_text("b")
    undeclared, missing = manifest_mismatches({"declared.md": []}, tmp_path)
    assert undeclared == {"stray.md"}
    assert missing == set()


def test_a_manifest_entry_with_no_file_is_reported(tmp_path: Path):
    (tmp_path / "declared.md").write_text("a")
    undeclared, missing = manifest_mismatches({"declared.md": [], "gone.md": []}, tmp_path)
    assert undeclared == set()
    assert missing == {"gone.md"}


def report_labels(text: str) -> list[str]:
    """The `### <label>` headings of a canned reports file, outside fenced blocks."""
    labels, fenced = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("### "):
            labels.append(line[4:].strip())
    return labels


def grammar_labels(expected_text: str) -> set[str]:
    """The first-column labels of the `### Reviewer labels` table in expected.md."""
    section = expected_text.split("### Reviewer labels", 1)[1].split("\n#", 1)[0]
    return {
        match.group(1)
        for line in section.splitlines()
        if (match := re.match(r"\|\s*`([^`]+)`\s*\|", line))
    }


def test_report_labels_reads_headings_and_skips_fenced_ones():
    text = "# T\n### Builder\nbody\n```\n### Fenced\n```\n### Security\n"
    assert report_labels(text) == ["Builder", "Security"]


def test_grammar_labels_reads_the_first_column_of_the_labels_table():
    text = "### Reviewer labels\n\n| Label | Class |\n|---|---|\n| `A` | x |\n| `B b` | y |\n\n### Next\n| `C` | z |\n"
    assert grammar_labels(text) == {"A", "B b"}


REPORT_FILES = ["reports-lenses.md", "reports-consult.md", "reports-spec-review.md"]


@pytest.mark.parametrize("report_file", REPORT_FILES)
def test_every_label_in_a_canned_report_is_one_the_grammar_names(report_file: str):
    known = grammar_labels(EXPECTED_MD.read_text(encoding="utf-8"))
    labels = report_labels((FIXTURES_DIR / report_file).read_text(encoding="utf-8"))
    assert labels, f"{report_file} carries no `### <label>` heading"
    unknown = [label for label in labels if label not in known]
    assert not unknown, f"{report_file} has label(s) the grammar does not name: {unknown}"
