"""Contract tests for the `ritual-output-wording` eval's `arms/` directory.

Every arm is the concatenation of source documents, each read either at a pinned git
revision or from the working tree. The manifest below names those sources per arm.
These tests pin that each committed arm file is exactly what its manifest composes,
that composing from a different revision of a pinned source does not give the same
bytes, and that the manifest and the directory list the same files.

Nothing here asserts the eval's measured result; that is decided by dispatched runs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

GIT_ROOT = Path(__file__).resolve().parents[3]
ARMS_DIR = (
    GIT_ROOT / "tools" / "craft" / "plugins" / "craft" / "evals" / "ritual-output-wording" / "arms"
)

# The last revision before the wording section existed on the main branch.
BASELINE_REVISION = "9458af4e"

# The commit that added the first wording of the rules section.
FIRST_WORDING_REVISION = "b2a8affc"

# The commit that carries the second wording of the rules section.
SECOND_WORDING_REVISION = "06a3aa22"

# The commit that carries the third wording of the rules section and the council template's
# report-citation bullet. Every third-wording arm and the fixture-derivation check read from it.
THIRD_WORDING_REVISION = "2de62373"

# The commit that carries slice step 8 with the instruction to name covered criteria by
# identifier removed. The shipped-slice arm pins its slice skill and rules file to it.
STEP8_REVISION = "b599c254"

RULES = "tools/craft/plugins/craft/rules.md"
SLICE_SKILL = "tools/craft/plugins/craft/skills/slice/SKILL.md"
RELIABILITY_AGENT = "tools/craft/plugins/craft/agents/breaker.md"
ADVOCATE_AGENT = "tools/craft/plugins/craft/agents/advocate.md"
COUNCIL_TEMPLATE = "tools/craft/plugins/craft/skills/_shared/council.md"

# A source is (repo-relative path, revision). A revision of None reads the working tree.
Source = tuple[str, str | None]

ARM_SOURCES: dict[str, list[Source]] = {
    "baseline-noritual.md": [(RULES, BASELINE_REVISION)],
    "baseline-slice.md": [(SLICE_SKILL, BASELINE_REVISION), (RULES, BASELINE_REVISION)],
    "baseline-reviewer.md": [(RELIABILITY_AGENT, BASELINE_REVISION), (RULES, BASELINE_REVISION)],
    "treated-noritual.md": [(RULES, FIRST_WORDING_REVISION)],
    "treated-slice-rules-only.md": [
        (SLICE_SKILL, BASELINE_REVISION),
        (RULES, FIRST_WORDING_REVISION),
    ],
    "treated-reviewer.md": [(RELIABILITY_AGENT, None), (RULES, FIRST_WORDING_REVISION)],
    "second-noritual.md": [(RULES, SECOND_WORDING_REVISION)],
    "second-slice-rules-only.md": [(SLICE_SKILL, BASELINE_REVISION), (RULES, SECOND_WORDING_REVISION)],
    "second-reviewer.md": [(RELIABILITY_AGENT, None), (RULES, SECOND_WORDING_REVISION)],
    "third-noritual.md": [(RULES, THIRD_WORDING_REVISION)],
    "third-slice-rules-only.md": [
        (SLICE_SKILL, BASELINE_REVISION),
        (RULES, THIRD_WORDING_REVISION),
    ],
    "third-reviewer.md": [
        (RELIABILITY_AGENT, THIRD_WORDING_REVISION),
        (RULES, THIRD_WORDING_REVISION),
    ],
    "shipped-slice.md": [(SLICE_SKILL, STEP8_REVISION), (RULES, STEP8_REVISION)],
    "baseline-advocate.md": [(ADVOCATE_AGENT, BASELINE_REVISION), (RULES, BASELINE_REVISION)],
    "third-advocate.md": [
        (ADVOCATE_AGENT, THIRD_WORDING_REVISION),
        (RULES, THIRD_WORDING_REVISION),
    ],
}


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=GIT_ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise AssertionError(
            f"`git {' '.join(args)}` failed (exit {result.returncode}): "
            f"{result.stderr.strip()}\n"
            "If the checkout is shallow, the pinned revision is not available; CI "
            "fetches full history for this reason."
        )
    return result.stdout


def read_source(path: str, revision: str | None) -> str:
    if revision is None:
        return (GIT_ROOT / path).read_text(encoding="utf-8")
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


_PINNED_SOURCES = [
    (arm, index)
    for arm, sources in sorted(ARM_SOURCES.items())
    for index, (_path, revision) in enumerate(sources)
    if revision is not None
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
    undeclared, missing = manifest_mismatches(
        {"declared.md": [], "gone.md": []}, tmp_path
    )
    assert undeclared == set()
    assert missing == {"gone.md"}


def test_a_directory_matching_its_manifest_reports_nothing(tmp_path: Path):
    (tmp_path / "declared.md").write_text("a")
    assert manifest_mismatches({"declared.md": []}, tmp_path) == (set(), set())


FIXTURES_DIR = ARMS_DIR.parent / "fixtures"
TEMPLATE_ANCHOR = "- One-line Confidence at the end"

TEMPLATE_FIXTURES = [
    ("prompt-reviewer.md", "prompt-reviewer-template.md"),
    ("prompt-advocate.md", "prompt-advocate-template.md"),
]


def _index_after_anchor(lines: list[str]) -> int:
    assert lines.count(TEMPLATE_ANCHOR) == 1, f"anchor appears {lines.count(TEMPLATE_ANCHOR)} times"
    return lines.index(TEMPLATE_ANCHOR) + 1


def line_after_anchor(text: str) -> str:
    lines = text.splitlines()
    return lines[_index_after_anchor(lines)]


def with_line_inserted_after_anchor(text: str, line: str) -> str:
    lines = text.splitlines()
    at = _index_after_anchor(lines)
    return "\n".join([*lines[:at], line, *lines[at:]]) + "\n"


@pytest.mark.parametrize("plain,template", TEMPLATE_FIXTURES, ids=[t for _, t in TEMPLATE_FIXTURES])
def test_template_fixture_is_its_plain_prompt_plus_the_line_the_council_template_carries(
    plain: str, template: str
):
    shipped_line = line_after_anchor(read_source(COUNCIL_TEMPLATE, THIRD_WORDING_REVISION))
    plain_text = (FIXTURES_DIR / plain).read_text(encoding="utf-8")
    template_text = (FIXTURES_DIR / template).read_text(encoding="utf-8")
    assert template_text == with_line_inserted_after_anchor(plain_text, shipped_line), (
        f"{template} is not {plain} with the council template's line after "
        f"{TEMPLATE_ANCHOR!r} at {THIRD_WORDING_REVISION} inserted at that position"
    )
