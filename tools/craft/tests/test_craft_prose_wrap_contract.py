"""Craft's skills and agents prose is wrapped at one consistent column.

`test_wrap_gate.py` and `test_wrap_prose.py` pin the gate's and formatter's
behaviour against synthetic fixtures, varying the input across the branch that
decides. This suite is the seam smoke for the pair: it points the real gate at
the real prose under `plugins/craft/skills/` (including `_shared/`) and
`plugins/craft/agents/`, so a paragraph hand-edited back out of wrap — or a new
file added without ever being run through `wrap_prose.py` — fails here, on the
live tree rather than a fixture.

One test, because there is one seam. Comparing the formatter's output against
the committed tree adds nothing: the tree is already `wrap_prose.py`'s own fixed
point, so both sides of such a comparison derive from the same text and no prose
corruption that survives a reflow could ever show up in it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS = REPO_ROOT / "plugins" / "craft" / "scripts"
WRAP_GATE = SCRIPTS / "wrap_gate.py"
SKILLS = REPO_ROOT / "plugins" / "craft" / "skills"
AGENTS = REPO_ROOT / "plugins" / "craft" / "agents"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wrap_gate import (  # noqa: E402
    _scan_code_span,
)

MAX_PROSE_LINE = 400

_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+.*)?[ \t]*$")


def governed_files() -> list[Path]:
    return sorted({*SKILLS.rglob("*.md"), *AGENTS.rglob("*.md")})


def gate(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(WRAP_GATE), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
    )


def code_spans(text: str) -> list[str]:
    """Every inline code span in `text`, whitespace-collapsed, in document
    order — the multiset a reflow must leave untouched. Matched per
    physical line via `wrap_gate._scan_code_span`, the same walk the gate
    and formatter both use, never a DOTALL regex over the whole file — that
    would mispair two unrelated backtick runs straddling a block-quote's
    repeated "> " prefix and report a false difference on this tree."""
    spans = []
    for line in text.splitlines():
        i, n = 0, len(line)
        while i < n:
            if line[i] == "`":
                end = _scan_code_span(line, i)
                if end is not None:
                    spans.append(" ".join(line[i:end].split()))
                    i = end
                    continue
            i += 1
    return spans


def headings(text: str) -> list[str]:
    return [line for line in text.splitlines() if _HEADING_RE.match(line)]


@pytest.mark.parametrize("path", governed_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
class TestGovernedFile:
    def test_wrapped_at_one_consistent_column(self, path):
        result = gate(path)
        assert result.returncode == 0, (
            f"{path} is not wrapped at one consistent column:\n{result.stderr}"
        )
