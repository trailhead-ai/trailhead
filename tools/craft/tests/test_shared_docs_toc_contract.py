"""The long `_shared` documents carry an accurate contents block.

`test_toc_gate.py` pins the gate's behaviour against synthetic documents. This
suite points the same gate at the real prose, so a heading added, renamed, or
removed without updating the document's contents block fails here — the drift
the block cannot survive and a reader cannot see.

It asserts no phrase and names no section. Every heading in these documents may
be reworded freely; only a block that stops matching them fails.

One test per document, because there is one seam per document. The gate's own
behaviour on a mutated block — a section added, an entry dropped — is
`test_toc_gate.py`'s subject, where the input actually varies.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
GATE = REPO_ROOT / "plugins" / "craft" / "scripts" / "toc_gate.py"
SHARED = REPO_ROOT / "plugins" / "craft" / "skills" / "_shared"

# A document long enough that a reader cannot hold its shape in view at once
# needs a map. Below this, the headings are the map.
LONG_DOCUMENT_LINES = 100


def long_documents() -> list[Path]:
    return sorted(
        p
        for p in SHARED.glob("*.md")
        if len(p.read_text(encoding="utf-8").splitlines()) > LONG_DOCUMENT_LINES
    )


def gate(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("path", long_documents(), ids=lambda p: p.name)
class TestLongSharedDocument:
    def test_contents_block_matches_its_headings(self, path):
        result = gate(path)
        assert result.returncode == 0, (
            f"{path.name}'s contents block no longer matches its headings:\n{result.stderr}"
        )
