"""The `_shared` documents stay one hop from each other.

`test_reference_depth_gate.py` pins the gate's behaviour against synthetic
documents. This suite points the same gate at the real prose, so a filename a
future edit reintroduces — naming a sibling `_shared` document instead of
stating the point it needs without a path — fails here, on the live tree
rather than a fixture.

It asserts no phrase and names no site. Every document may be reworded
freely; only a line that names a sibling document's filename fails.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
GATE = REPO_ROOT / "plugins" / "craft" / "scripts" / "reference_depth_gate.py"
SHARED = REPO_ROOT / "plugins" / "craft" / "skills" / "_shared"


def shared_documents() -> list[Path]:
    return sorted(SHARED.glob("*.md"))


def gate(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
    )


def test_there_are_shared_documents_to_check():
    """Guards the parametrization below against silently covering nothing."""
    assert shared_documents(), f"no document found in {SHARED}"


@pytest.mark.parametrize("path", shared_documents(), ids=lambda p: p.name)
class TestSharedDocument:
    def test_stays_one_hop_from_its_siblings(self, path):
        result = gate(path)
        assert result.returncode == 0, (
            f"{path.name} still references a sibling _shared document:\n{result.stderr}"
        )

    def test_a_new_reference_to_a_sibling_is_caught(self, path, tmp_path):
        """The gate binds this document's real prose, not only fixtures.

        Adding a mention of a sibling document's filename and leaving the
        rest of the file alone is the drift a one-hop tree cannot survive;
        it must fail against the document as it ships. The gate's sibling
        set is directory-relative, so the fixture copies the whole `_shared`
        directory rather than the one file being mutated — a lone copy with
        no siblings beside it would never be flagged regardless.
        """
        sibling = next((p for p in shared_documents() if p != path), None)
        if sibling is None:
            pytest.skip(f"{path.name} has no sibling in {SHARED}")
        mirror = tmp_path / "shared"
        shutil.copytree(SHARED, mirror)
        target = mirror / path.name
        target.write_text(
            target.read_text(encoding="utf-8") + f"\nSee `{sibling.name}` for more.\n",
            encoding="utf-8",
        )
        assert gate(target).returncode == 1
