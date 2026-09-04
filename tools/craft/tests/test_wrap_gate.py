"""Tests for the wrap gate.

The gate certifies that a document's prose is wrapped at one consistent
column: no prose line runs over the budget, and no prose line stops short
while its successor's first word (or code span) would still have fit. A bare
"no line over N" check cannot tell a document wrapped at 100 from one wrapped
at 70 — both pass — so the under-fill rule is what actually pins the column.

Exit-code contract (matches `toc_gate.py` and `reference_depth_gate.py`):
  0 → clean (every prose line is within budget and fully filled)
  1 → finding (an over-budget line or an under-filled line — prints a
      `reason:` line per finding, naming the file, line number, which rule
      fired, and the `wrap_prose.py` remedy)
  2 → error / fail-closed (path missing or unreadable, or non-UTF-8 content)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GATE = REPO_ROOT / "plugins" / "craft" / "scripts" / "wrap_gate.py"

sys.path.insert(0, str(GATE.parent))
import wrap_gate  # noqa: E402


def run(*paths: Path, column: int | None = None) -> subprocess.CompletedProcess:
    args = [sys.executable, str(GATE)]
    if column is not None:
        args += ["--column", str(column)]
    args += [str(p) for p in paths]
    return subprocess.run(args, capture_output=True, text=True)


def doc(tmp_path: Path, body: str, name: str = "doc.md") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# Budget of 20 keeps fixtures readable; the default-100 behaviour is pinned
# separately below.
COL = 20


class TestOverBudget:
    def test_one_character_over_budget_exits_one_and_names_the_line(self, tmp_path):
        # "aaaa bbbb cccc ddd" is 18 chars; add one more short word to push
        # one character past a budget of 18.
        line = "aaaa bbbb cccc dde"  # 18 chars exactly
        body = f"# Title\n\n{line}f\n"  # 19 chars, budget 18 -> over by 1
        result = run(doc(tmp_path, body), column=18)
        assert result.returncode == 1
        assert "line 3" in result.stderr
        assert "over-budget" in result.stderr

    def test_line_filled_exactly_to_budget_exits_zero(self, tmp_path):
        line = "aaaa bbbb cccc dde"  # exactly 18 characters
        assert len(line) == 18
        body = f"# Title\n\n{line}\n"
        assert run(doc(tmp_path, body), column=18).returncode == 0


class TestUnderFilled:
    def test_short_line_whose_successors_word_would_fit_exits_one(self, tmp_path):
        # "one two" (7 chars) + " three" (6 chars) = 13, well under 20.
        body = "# Title\n\none two\nthree four five six seven\n"
        result = run(doc(tmp_path, body), column=COL)
        assert result.returncode == 1
        assert "under-filled" in result.stderr
        assert "line 3" in result.stderr

    def test_under_filled_is_distinguishable_from_over_budget(self, tmp_path):
        body = "# Title\n\none two\nthree four five\n"
        result = run(doc(tmp_path, body), column=COL)
        assert "under-filled" in result.stderr
        assert "over-budget" not in result.stderr

    def test_last_line_of_paragraph_is_never_under_filled(self, tmp_path):
        # No trailing newline: "short" is the true last element after the
        # line split, with no successor at all — not merely a blank one.
        body = "# Title\n\nshort"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_properly_filled_paragraph_exits_zero(self, tmp_path):
        # Each line packed so the next word would not fit within budget 20.
        body = "# Title\n\none two three four\nfive six seven\n"
        result = run(doc(tmp_path, body), column=COL)
        assert result.returncode == 0


class TestRemedyNamed:
    def test_over_budget_finding_names_wrap_prose(self, tmp_path):
        body = "# Title\n\naaaa bbbb cccc dddd eeee\n"
        result = run(doc(tmp_path, body), column=COL)
        assert result.returncode == 1
        assert "wrap_prose.py" in result.stderr

    def test_under_filled_finding_names_wrap_prose(self, tmp_path):
        body = "# Title\n\none two\nthree four five six seven\n"
        result = run(doc(tmp_path, body), column=COL)
        assert result.returncode == 1
        assert "wrap_prose.py" in result.stderr


class TestCodeSpans:
    def test_short_line_whose_successor_code_span_would_not_fit_exits_zero(self, tmp_path):
        body = "# Title\n\nsee\n`this is a long code span here`\n"
        result = run(doc(tmp_path, body), column=COL)
        assert result.returncode == 0

    def test_short_line_whose_successor_code_span_would_fit_exits_one(self, tmp_path):
        body = "# Title\n\nsee\n`ab` more text\n"
        result = run(doc(tmp_path, body), column=COL)
        assert result.returncode == 1
        assert "under-filled" in result.stderr

    def test_single_code_span_longer_than_budget_exits_zero(self, tmp_path):
        body = "# Title\n\n`" + ("z" * 30) + "`\n"
        result = run(doc(tmp_path, body), column=COL)
        assert result.returncode == 0

    def test_pem_pattern_is_measured_as_one_unit(self, tmp_path):
        # The real fixture: a multi-space backtick span from execute.md's
        # credential scrub, copied verbatim. A tokenizer that splits it on
        # its internal whitespace would see a short trailing fragment
        # (`` `-----BEGIN `` is 11 chars) as the successor's "first unit"
        # and wrongly call line 3 under-filled; measured as one 36-char
        # unit, it does not fit and the line is correctly left alone.
        span = "`-----BEGIN [A-Z ]*PRIVATE KEY-----`"
        assert len(span) == 36
        body = f"# Title\n\nsee\n{span} exactly\n"
        result = run(doc(tmp_path, body), column=30)
        assert result.returncode == 0


class TestFencedAndCommentedExemptions:
    # Made of several short words rather than one long token — an
    # over-budget line whose exemption, if it fired, could only be the
    # masking exemption itself, never the unrelated unbreakable-word one.
    _OVER_LONG_PROSE = "aaaa bbbb cccc dddd eeee ffff gggg"

    def test_over_long_line_inside_triple_backtick_fence_exits_zero(self, tmp_path):
        body = f"# Title\n\n```\n{self._OVER_LONG_PROSE}\n```\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_over_long_line_inside_tilde_fence_exits_zero(self, tmp_path):
        body = f"# Title\n\n~~~\n{self._OVER_LONG_PROSE}\n~~~\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_over_long_line_inside_a_longer_fence_wrapping_a_shorter_one(self, tmp_path):
        body = f"# Title\n\n````\n```\n{self._OVER_LONG_PROSE}\n```\n````\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_over_long_line_inside_html_comment_exits_zero(self, tmp_path):
        body = f"# Title\n\n<!-- {self._OVER_LONG_PROSE} -->\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_over_long_line_inside_unterminated_comment_running_to_eof_exits_zero(self, tmp_path):
        body = f"# Title\n\n<!-- unterminated\n{self._OVER_LONG_PROSE}\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0


class TestHeadingsAndTables:
    # Multiple short words, not one long token, so the only exemption that
    # could apply is the heading/table one, never the unbreakable-word one.
    _OVER_LONG_WORDS = "aaaa bbbb cccc dddd eeee ffff gggg"

    def test_over_long_atx_heading_exits_zero(self, tmp_path):
        body = f"# {self._OVER_LONG_WORDS}\n\nshort\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_over_long_table_row_exits_zero(self, tmp_path):
        body = f"# Title\n\n| {self._OVER_LONG_WORDS} | y |\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0


class TestUnbreakableWords:
    def test_line_with_single_unbreakable_word_longer_than_budget_exits_zero(self, tmp_path):
        body = "# Title\n\nhttps://example.com/" + ("a" * 30) + "\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_same_line_with_breakable_text_appended_past_budget_still_exits_zero(self, tmp_path):
        url = "https://example.com/" + ("a" * 30)
        body = f"# Title\n\n{url} and then some more breakable words after it\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0


class TestHardLineBreak:
    def test_line_ending_in_hard_break_is_exempt_from_under_fill(self, tmp_path):
        # Two trailing spaces mark a hard line break; the successor's word
        # would otherwise trigger the under-fill rule.
        body = "# Title\n\none two  \nthree\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0


class TestListContinuationIndent:
    def test_correctly_filled_continuation_line_exits_zero(self, tmp_path):
        # The 2-space hanging indent is part of each continuation line's
        # measured width: "  three four five" (17 chars, indent included)
        # plus " six" (4) is 21 — over budget, so correctly not under-filled.
        # A check that stripped the indent first would measure 15 + 4 = 19,
        # fit inside 20, and wrongly call it under-filled.
        body = (
            "# Title\n\n"
            "- one two three four\n"
            "  three four five\n"
            "  six\n"
        )
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_under_filled_continuation_line_exits_one(self, tmp_path):
        body = (
            "# Title\n\n"
            "- one two three four\n"
            "  three\n"
            "  four five\n"
        )
        result = run(doc(tmp_path, body), column=COL)
        assert result.returncode == 1
        assert "under-filled" in result.stderr


class TestColumnOverride:
    def test_column_flag_changes_the_budget_for_over_budget(self, tmp_path):
        body = "# Title\n\naaa bbb ccc ddd\n"
        assert run(doc(tmp_path, body), column=20).returncode == 0
        assert run(doc(tmp_path, body), column=10).returncode == 1

    def test_column_flag_changes_the_budget_for_under_fill(self, tmp_path):
        body = "# Title\n\none two\nthree\n"
        # At a tiny budget, "one two" plus " three" would not fit, so it's
        # fine; at a huge budget it obviously would.
        assert run(doc(tmp_path, body), column=8).returncode == 0
        assert run(doc(tmp_path, body), column=100).returncode == 1


class TestFailClosed:
    def test_missing_path_exits_two(self, tmp_path):
        assert run(tmp_path / "absent.md").returncode == 2

    def test_non_utf8_content_exits_two(self, tmp_path):
        path = tmp_path / "bad.md"
        path.write_bytes(b"\xff\xfe\x00 not utf-8")
        result = run(path)
        assert result.returncode == 2


class TestLibraryInterface:
    """The gate is importable as a library, not only a CLI — the formatter
    task uses this to check its own output without shelling out."""

    def test_check_returns_findings_for_over_budget_line(self, tmp_path):
        body = "# Title\n\naaaa bbbb cccc dddd eeee\n"
        findings = wrap_gate.check(doc(tmp_path, body), COL)
        assert any(f.rule == "over-budget" for f in findings)

    def test_check_returns_no_findings_for_clean_document(self, tmp_path):
        body = "# Title\n\nshort\n"
        assert wrap_gate.check(doc(tmp_path, body), COL) == []
