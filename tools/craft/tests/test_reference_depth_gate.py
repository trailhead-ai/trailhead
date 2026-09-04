"""Tests for the reference-depth gate.

The gate certifies that a document names no sibling `_shared` document by
filename. A reference to a sibling sends a reader to a whole other document
for what should be a pointer to one line of shared prose — this slice exists
to remove those references, and the gate is what proves they stay removed.

Exit-code contract (matches `toc_gate.py` and `covers_gate.py`):
  0 → clean (no surviving reference to a sibling document)
  1 → finding (a sibling document is still named — prints a `reason:` line
      per finding, naming the file, line number, and matched text)
  2 → error / fail-closed (path missing or unreadable, empty content, or
      non-UTF-8 content)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GATE = REPO_ROOT / "plugins" / "craft" / "scripts" / "reference_depth_gate.py"


def run(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
    )


def doc(tmp_path: Path, body: str, name: str = "doc.md") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def sibling(tmp_path: Path, name: str, body: str = "# Sibling\n\nText.\n") -> Path:
    """Create an actual sibling `.md` file beside the document under test —
    the gate's sibling set is a directory listing, not a fixed name list."""
    return doc(tmp_path, body, name)


BASE = "# Title\n\nOrdinary prose with no references.\n"


class TestClean:
    def test_no_sibling_reference_exits_zero(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        assert run(doc(tmp_path, BASE)).returncode == 0

    def test_naming_its_own_filename_is_not_a_finding(self, tmp_path):
        """A document does not reference itself."""
        body = "# Title\n\nSee `doc.md` for context.\n"
        assert run(doc(tmp_path, body)).returncode == 0

    def test_non_sibling_filename_is_not_a_finding(self, tmp_path):
        """`README.md`, `SKILL.md`, and `plan.md` are not present as files
        beside the document, so mentioning them names no sibling."""
        sibling(tmp_path, "status-ownership.md")
        body = (
            "# Title\n\nSee `README.md`, `SKILL.md`, and `plan.md` for more.\n"
        )
        assert run(doc(tmp_path, body)).returncode == 0


class TestFinding:
    def test_one_live_reference_exits_one_and_names_file_and_line(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        path = doc(tmp_path, "# Title\n\nSee `_shared/status-ownership.md` for the contract.\n")
        result = run(path)
        assert result.returncode == 1
        assert str(path) in result.stderr
        assert "line 3" in result.stderr

    def test_two_different_sibling_references_names_both(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        sibling(tmp_path, "slice.md")
        path = doc(
            tmp_path,
            "# Title\n\nSee `_shared/status-ownership.md`.\n\nAlso `_shared/slice.md`.\n",
        )
        result = run(path)
        assert result.returncode == 1
        assert "status-ownership.md" in result.stderr
        assert "slice.md" in result.stderr

    def test_multiple_paths_names_only_the_dirty_one(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        clean = doc(tmp_path, BASE, "clean.md")
        dirty = doc(
            tmp_path, "# Title\n\nSee `_shared/status-ownership.md`.\n", "dirty.md"
        )
        result = run(clean, dirty)
        assert result.returncode == 1
        assert "dirty.md" in result.stderr
        assert "clean.md" not in result.stderr

    def test_reference_on_first_line_after_fence_closes_is_a_finding(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        body = (
            "# Title\n\n```sh\necho hi\n```\n"
            "See `_shared/status-ownership.md` right after the fence.\n"
        )
        result = run(doc(tmp_path, body))
        assert result.returncode == 1
        assert "status-ownership.md" in result.stderr


class TestRealWorldForms:
    """Each of the six real forms live prose uses for the same reference."""

    def test_parent_relative_form(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        body = "# Title\n\nSee `../_shared/status-ownership.md` for the contract.\n"
        assert run(doc(tmp_path, body)).returncode == 1

    def test_directory_relative_form(self, tmp_path):
        sibling(tmp_path, "slice.md")
        body = "# Title\n\nThe shapes `_shared/slice.md` fixes.\n"
        assert run(doc(tmp_path, body)).returncode == 1

    def test_parent_relative_form_second_sibling(self, tmp_path):
        sibling(tmp_path, "refine.md")
        body = "# Title\n\nRun `../_shared/refine.md`'s citation-resolution gate.\n"
        assert run(doc(tmp_path, body)).returncode == 1

    def test_directory_relative_form_second_sibling(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        body = "# Title\n\nSee `_shared/status-ownership.md` for the full contract.\n"
        assert run(doc(tmp_path, body)).returncode == 1

    def test_directory_relative_form_third_sibling(self, tmp_path):
        sibling(tmp_path, "council.md")
        body = "# Title\n\nThe `_shared/council.md` precedent.\n"
        assert run(doc(tmp_path, body)).returncode == 1

    def test_bare_filename_form_no_directory_at_all(self, tmp_path):
        sibling(tmp_path, "refine.md")
        body = "# Title\n\nA thin wrapper over it (refine.md's shape).\n"
        assert run(doc(tmp_path, body)).returncode == 1


class TestForgedStructure:
    """A gate that derives ground truth from a document format must not be
    steerable by text that only looks like a live reference."""

    def test_reference_inside_a_triple_backtick_fence_is_not_a_finding(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        body = BASE + "\n```markdown\nSee `_shared/status-ownership.md`.\n```\n"
        assert run(doc(tmp_path, body)).returncode == 0

    def test_reference_inside_a_tilde_fence_is_not_a_finding(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        body = BASE + "\n~~~markdown\nSee `_shared/status-ownership.md`.\n~~~\n"
        assert run(doc(tmp_path, body)).returncode == 0

    def test_reference_inside_a_longer_fence_wrapping_a_shorter_one(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        body = (
            BASE
            + "\n````markdown\n```\nSee `_shared/status-ownership.md`.\n```\n````\n"
        )
        assert run(doc(tmp_path, body)).returncode == 0

    def test_reference_inside_an_unterminated_fence_running_to_eof(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        body = BASE + "\n```sh\nSee _shared/status-ownership.md\n"
        assert run(doc(tmp_path, body)).returncode == 0

    def test_reference_inside_an_html_comment_is_not_a_finding(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        body = BASE + "\n<!-- see _shared/status-ownership.md -->\n"
        assert run(doc(tmp_path, body)).returncode == 0


class TestFailClosed:
    def test_missing_path_exits_two(self, tmp_path):
        assert run(tmp_path / "absent.md").returncode == 2

    def test_empty_file_exits_two(self, tmp_path):
        result = run(doc(tmp_path, ""))
        assert result.returncode == 2
        assert "empty" in result.stderr.lower()

    def test_non_utf8_content_exits_two(self, tmp_path):
        path = tmp_path / "bad.md"
        path.write_bytes(b"\xff\xfe\x00 not utf-8")
        result = run(path)
        assert result.returncode == 2

    def test_unreadable_path_exits_two(self, tmp_path):
        import os

        if os.name != "posix" or os.geteuid() == 0:
            import pytest

            pytest.skip("permission bits are not enforced for root or on non-POSIX")
        path = doc(tmp_path, BASE)
        path.chmod(0o000)
        try:
            result = run(path)
            assert result.returncode == 2
        finally:
            path.chmod(0o644)


class TestCommonMarkStructure:
    def test_crlf_input_is_read_the_same(self, tmp_path):
        sibling(tmp_path, "status-ownership.md")
        path = tmp_path / "crlf.md"
        body = "# Title\n\nSee `_shared/status-ownership.md`.\n"
        path.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))
        result = run(path)
        assert result.returncode == 1
        assert "line 3" in result.stderr
