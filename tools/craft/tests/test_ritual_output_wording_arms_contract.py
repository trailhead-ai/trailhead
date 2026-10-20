"""Contract tests for the `ritual-output-wording` eval's `arms/` directory.

Every arm is a committed copy of the source documents it was measured against, most
of them as they stood at a past commit. The eval case's `expected.md` records which
sources and which commit each arm was copied from. That text exists only in
repository history, so nothing here recomposes an arm; these tests pin that the
manifest and the directory list the same files.

Nothing here asserts the eval's measured result; that is decided by dispatched runs.
"""

from __future__ import annotations

from pathlib import Path

ARMS_DIR = (
    Path(__file__).resolve().parents[1]
    / "plugins" / "craft" / "evals" / "ritual-output-wording" / "arms"
)

ARMS = {
    "baseline-noritual.md",
    "baseline-slice.md",
    "baseline-reviewer.md",
    "treated-noritual.md",
    "treated-slice-rules-only.md",
    "treated-reviewer.md",
    "second-noritual.md",
    "second-slice-rules-only.md",
    "second-reviewer.md",
    "third-noritual.md",
    "third-slice-rules-only.md",
    "third-reviewer.md",
    "shipped-slice.md",
    "baseline-advocate.md",
    "third-advocate.md",
    "summary-baseline-noritual.md",
    "summary-baseline-build.md",
    "summary-noritual.md",
    "summary-build-rules-only.md",
    "fourth-noritual.md",
    "fourth-reviewer.md",
    "fourth-advocate.md",
    "fourth-slice.md",
}


def manifest_mismatches(manifest: set[str], arms_dir: Path) -> tuple[set[str], set[str]]:
    """(files on disk with no manifest entry, manifest entries with no file)."""
    on_disk = {p.name for p in arms_dir.glob("*.md")}
    return on_disk - manifest, manifest - on_disk


def test_every_arm_file_has_a_manifest_entry_and_every_entry_a_file():
    undeclared, missing = manifest_mismatches(ARMS, ARMS_DIR)
    assert not undeclared, f"arm file(s) with no manifest entry: {sorted(undeclared)}"
    assert not missing, f"manifest entries with no file on disk: {sorted(missing)}"


def test_an_arm_file_with_no_manifest_entry_is_reported(tmp_path: Path):
    (tmp_path / "declared.md").write_text("a")
    (tmp_path / "stray.md").write_text("b")
    undeclared, missing = manifest_mismatches({"declared.md"}, tmp_path)
    assert undeclared == {"stray.md"}
    assert missing == set()


def test_a_manifest_entry_with_no_file_is_reported(tmp_path: Path):
    (tmp_path / "declared.md").write_text("a")
    undeclared, missing = manifest_mismatches({"declared.md", "gone.md"}, tmp_path)
    assert undeclared == set()
    assert missing == {"gone.md"}


def test_a_directory_matching_its_manifest_reports_nothing(tmp_path: Path):
    (tmp_path / "declared.md").write_text("a")
    assert manifest_mismatches({"declared.md"}, tmp_path) == (set(), set())
