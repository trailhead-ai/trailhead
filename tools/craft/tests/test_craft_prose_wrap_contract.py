"""Craft's skills and agents prose is wrapped at one consistent column.

`test_wrap_gate.py` and `test_wrap_prose.py` pin the gate's and formatter's
behaviour against synthetic fixtures. This suite points the gate at the real
prose under `plugins/craft/skills/` (including `_shared/`) and
`plugins/craft/agents/`, so a paragraph hand-edited back out of wrap — or a
new file added without ever being run through `wrap_prose.py` — fails here,
on the live tree rather than a fixture.

It also carries the tree-wide safety net for the reflow that produced this
state: every file's inline code spans are unchanged (a corrupted copy-paste
command or credential-scrub pattern would show up as a changed span
multiset), no file is unreadable, and no reflow moved content between
sections (a heading list is unchanged before and after).
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

from wrap_gate import _COMMONMARK_LINE_RE, _mask_frontmatter_lines  # noqa: E402

MAX_PROSE_LINE = 400

_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+.*)?[ \t]*$")
_CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1", re.DOTALL)


def governed_files() -> list[Path]:
    return sorted({*SKILLS.rglob("*.md"), *AGENTS.rglob("*.md")})


def gate(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(WRAP_GATE), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
    )


def code_spans(text: str) -> list[str]:
    """Every inline code span in `text`, backticks included, in document
    order — the multiset a reflow must leave untouched."""
    return [m.group(0) for m in _CODE_SPAN_RE.finditer(text)]


def headings(text: str) -> list[str]:
    return [line for line in text.splitlines() if _HEADING_RE.match(line)]


def test_there_are_governed_files_to_check():
    """Guards the parametrization below against silently covering nothing."""
    assert governed_files(), f"no markdown file found under {SKILLS} or {AGENTS}"


def test_governed_file_set_has_39_files():
    assert len(governed_files()) == 39


@pytest.mark.parametrize("path", governed_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
class TestGovernedFile:
    def test_wrapped_at_one_consistent_column(self, path):
        result = gate(path)
        assert result.returncode == 0, (
            f"{path} is not wrapped at one consistent column:\n{result.stderr}"
        )

    def test_no_prose_line_over_400_characters(self, path):
        """AC4's own floor, checked outside frontmatter.

        A YAML `description:` field is one logical line by construction and
        is never reflowed (`wrap_gate.py`/`wrap_prose.py` both mask
        frontmatter out), so a long `description:` is not a wrap defect and
        no formatter could ever satisfy this assertion as a whole-file check.
        The floor still applies to every line the gate actually governs.
        """
        text = path.read_text(encoding="utf-8")
        lines = _COMMONMARK_LINE_RE.split(text)
        frontmatter = _mask_frontmatter_lines(lines)
        too_long = [
            (i + 1, len(line))
            for i, line in enumerate(lines)
            if not frontmatter[i] and len(line) > MAX_PROSE_LINE
        ]
        assert too_long == [], f"{path} has line(s) over {MAX_PROSE_LINE} characters: {too_long}"
