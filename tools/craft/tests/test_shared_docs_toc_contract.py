"""The long `_shared` documents carry an accurate contents block.

`test_toc_gate.py` pins the gate's behaviour against synthetic documents. This
suite points the same gate at the real prose, so a heading added, renamed, or
removed without updating the document's contents block fails here — the drift
the block cannot survive and a reader cannot see.

It asserts no phrase and names no section. Every heading in these documents may
be reworded freely; only a block that stops matching them fails.
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
        if len(p.read_text(encoding="utf-8").split("\n")) > LONG_DOCUMENT_LINES
    )


def gate(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
    )


def test_there_are_long_shared_documents_to_check():
    """Guards the parametrization below against silently covering nothing."""
    assert long_documents(), f"no document over {LONG_DOCUMENT_LINES} lines found in {SHARED}"


@pytest.mark.parametrize("path", long_documents(), ids=lambda p: p.name)
class TestLongSharedDocument:
    def test_contents_block_matches_its_headings(self, path):
        result = gate(path)
        assert result.returncode == 0, (
            f"{path.name}'s contents block no longer matches its headings:\n{result.stderr}"
        )

    def test_a_new_section_without_an_entry_is_caught(self, path, tmp_path):
        """The gate binds this document's real prose, not only fixtures.

        Adding a section and leaving the block alone is the drift that makes a
        contents block lie; it must fail against the document as it ships.
        """
        copy = tmp_path / path.name
        copy.write_text(
            path.read_text(encoding="utf-8") + "\n## A section the block does not name\n",
            encoding="utf-8",
        )
        assert gate(copy).returncode == 1

    def test_a_dropped_entry_is_caught(self, path, tmp_path):
        text = path.read_text(encoding="utf-8")
        start = text.index("<!-- toc:start -->")
        end = text.index("<!-- toc:end -->")
        block = text[start:end]
        first_entry = next(line for line in block.split("\n") if line.startswith("- "))
        copy = tmp_path / path.name
        copy.write_text(text.replace(block, block.replace(first_entry + "\n", "", 1)), encoding="utf-8")
        assert gate(copy).returncode == 1
