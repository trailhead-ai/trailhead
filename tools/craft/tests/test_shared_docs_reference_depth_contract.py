"""The `_shared` documents stay one hop from each other.

`test_reference_depth_gate.py` pins the gate's behaviour against synthetic
documents. This suite points the same gate at the real prose, so a filename a
future edit reintroduces — naming a sibling `_shared` document instead of
stating the point it needs without a path — fails here, on the live tree
rather than a fixture.

It asserts no phrase and names no site. Every document may be reworded
freely; only a line that names a sibling document's filename fails.

One test per document, because there is one seam per document. The gate's own
behaviour on a seeded sibling reference is `test_reference_depth_gate.py`'s
subject, where the input actually varies.
"""

from __future__ import annotations

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


@pytest.mark.parametrize("path", shared_documents(), ids=lambda p: p.name)
class TestSharedDocument:
    def test_stays_one_hop_from_its_siblings(self, path):
        result = gate(path)
        assert result.returncode == 0, (
            f"{path.name} still references a sibling _shared document:\n{result.stderr}"
        )
