"""Craft's skills and agents prose is wrapped at one consistent column.

`test_wrap_gate.py` and `test_wrap_prose.py` pin the gate's and formatter's
behaviour against synthetic fixtures. This suite points the gate at the real
prose under `plugins/craft/skills/` (including `_shared/`) and
`plugins/craft/agents/`, so a paragraph hand-edited back out of wrap — or a
new file added without ever being run through `wrap_prose.py` — fails here,
on the live tree rather than a fixture.

It also pins that the tools have not regressed since: the committed tree is
`wrap_prose.py`'s own fixed point, so re-running the formatter over it and
comparing code spans and headings before and after can only ever detect a
change to the formatter or gate — a reflow that starts moving content
between sections, or losing or duplicating a code span, on input that is
already its own fixed point. What this does NOT pin: a hand-edit to the
prose content itself (a mangled credential-scrub pattern, a rewritten
sentence) is invisible to it, because both sides of every comparison here
are derived from the same already-committed text — there is no independent
reference to catch prose corruption that survives a reflow unchanged.
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

import wrap_prose  # noqa: E402
from wrap_gate import (  # noqa: E402
    _COMMONMARK_LINE_RE,
    _mask_frontmatter_lines,
    _scan_code_span,
    DEFAULT_COLUMN,
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

    def test_code_spans_are_unchanged_by_reflow(self, path):
        text = path.read_text(encoding="utf-8")
        before = code_spans(text)
        after = code_spans(wrap_prose.format_text(text, DEFAULT_COLUMN))
        assert sorted(after) == sorted(before), f"{path} lost or gained a code span on reflow"

    def test_heading_list_is_unchanged_by_reflow(self, path):
        text = path.read_text(encoding="utf-8")
        before = headings(text)
        after = headings(wrap_prose.format_text(text, DEFAULT_COLUMN))
        assert after == before, f"{path} had content move between sections on reflow"
