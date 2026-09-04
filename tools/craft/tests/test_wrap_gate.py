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
        #
        # Line 4 ("{span} exactly") is not itself a single-unit line, so it
        # is separately over-budget under the narrower exemption scoping
        # (see `test_span_plus_breakable_word_is_over_budget_not_a_single_unit_exemption`
        # below) — an unrelated finding this test does not name. The
        # property this test pins is only about line 3, so it is asserted
        # directly rather than via the whole file's exit code.
        span = "`-----BEGIN [A-Z ]*PRIVATE KEY-----`"
        assert len(span) == 36
        body = f"# Title\n\nsee\n{span} exactly\n"
        findings = wrap_gate.check(doc(tmp_path, body), 30)
        assert not any(f.line == 3 and f.rule == "under-filled" for f in findings), findings

    def test_span_plus_breakable_word_is_over_budget_not_a_single_unit_exemption(self, tmp_path):
        # Line 4 above ("{span} exactly") is not a genuinely single-unit
        # line — `wrap_prose.py` isolates the span and wraps "exactly" onto
        # its own line — so it must be over-budget, not exempt.
        span = "`-----BEGIN [A-Z ]*PRIVATE KEY-----`"
        body = f"# Title\n\nsee\n{span} exactly\n"
        result = run(doc(tmp_path, body), column=30)
        assert result.returncode == 1, result.stderr
        assert "over-budget" in result.stderr

    def test_punctuation_glued_to_a_code_span_is_measured_with_it(self, tmp_path):
        # "`ab`," glued with no space is one 5-char unit, exactly as
        # wrap_prose.py's fill measures it. A tokenizer that treated the
        # span as its own token, resuming ordinary word-splitting right
        # after its closing backtick, would see the comma as a separate
        # 1-char token and measure "`ab`" (4 chars) as the successor's
        # first unit — 15 + 1 + 4 = 20, fitting the budget and wrongly
        # calling line 3 under-filled. Measured as the real 5-char glued
        # unit, 15 + 1 + 5 = 21 does not fit, so line 3 is correctly left
        # alone.
        line3 = "one two three a"
        assert len(line3) == 15
        body = f"# Title\n\n{line3}\n`ab`, more text\n"
        result = run(doc(tmp_path, body), column=20)
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
        body = f"# Title\n\n| h1 | h2 |\n|----|----|\n| {self._OVER_LONG_WORDS} | y |\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0


class TestTableVsPipeAsOrProse:
    """A line containing `|` is a table row only when it belongs to a real
    CommonMark table block (header row + delimiter row, plus contiguous
    rows after it) — not merely because it contains a pipe character.
    Craft's prose uses `|` as a plain "or" between short code-quoted
    alternatives, and those lines must still be wrapped like any other."""

    _OVER_LONG_WORDS = "aaaa bbbb cccc dddd eeee ffff gggg"

    def test_pipe_as_or_prose_is_wrapped_and_reported_when_over_budget(self, tmp_path):
        body = "# Title\n\naaaa `SHIP` | `FAIL` | `HOLD` bbbb cccc dddd\n"
        result = run(doc(tmp_path, body), column=COL)
        assert result.returncode == 1
        assert "over-budget" in result.stderr

    def test_genuine_table_row_over_budget_still_exempt(self, tmp_path):
        body = f"# Title\n\n| h1 | h2 |\n|----|----|\n| {self._OVER_LONG_WORDS} | y |\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_table_row_with_pipe_inside_a_code_span_cell_still_exempt(self, tmp_path):
        # No single token here is long enough to trip the separate
        # unbreakable-unit exemption — this row is only exempt because it is
        # still recognized as a table row despite the pipe inside `a|b`.
        body = "# Title\n\n| h1 | h2 |\n|----|----|\n| `a|b` cccc dddd eeee ffff gggg | y |\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_pipe_inside_a_code_span_does_not_make_a_prose_line_a_table_header(self, tmp_path):
        # This line's only "|" is inside a code span, so it carries no real
        # cell-delimiting pipe — it must not be mistaken for a header row
        # just because "----|----" happens to follow it.
        body = "# Title\n\naaaa `a|b` cccc dddd eeee\n----|----\n"
        result = wrap_gate.check(doc(tmp_path, body), COL)
        assert any(f.rule == "over-budget" and f.line == 3 for f in result)

    def test_prose_line_with_a_single_pipe_in_ordinary_text_is_wrapped(self, tmp_path):
        body = "# Title\n\naaaa bbbb | cccc dddd eeee\n"
        result = run(doc(tmp_path, body), column=COL)
        assert result.returncode == 1
        assert "over-budget" in result.stderr

    def test_escaped_pipe_before_a_dash_line_is_not_treated_as_a_table_header(self, tmp_path):
        # Without the escape, "----|----" would look like a delimiter row and
        # this line, its would-be header, would be wrongly exempted.
        body = "# Title\n\naaaa bbbb \\| cccc dddd eeee\n----|----\n"
        result = wrap_gate.check(doc(tmp_path, body), COL)
        assert any(f.rule == "over-budget" and f.line == 3 for f in result)


class TestUnbreakableWords:
    def test_line_with_single_unbreakable_word_longer_than_budget_exits_zero(self, tmp_path):
        # The legitimate half of the exemption: a line that IS the one
        # over-budget unit, with nothing breakable beside it, is one the
        # formatter genuinely cannot help — still exempt.
        body = "# Title\n\nhttps://example.com/" + ("a" * 30) + "\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_breakable_text_beside_an_unbreakable_unit_is_over_budget(self, tmp_path):
        # Not a genuinely single-unit line: `wrap_prose.py` isolates the
        # unbreakable unit onto its own line and wraps the rest, so a gate
        # that stayed exempt here would certify a line the formatter would
        # still change.
        url = "https://example.com/" + ("a" * 30)
        body = f"# Title\n\n{url} and then some more breakable words after it\n"
        result = run(doc(tmp_path, body), column=COL)
        assert result.returncode == 1, result.stderr
        assert "over-budget" in result.stderr


class TestHardLineBreak:
    def test_line_ending_in_hard_break_is_exempt_from_under_fill(self, tmp_path):
        # Two trailing spaces mark a hard line break; the successor's word
        # would otherwise trigger the under-fill rule.
        body = "# Title\n\none two  \nthree\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_line_before_a_hard_break_successor_is_not_under_filled(self, tmp_path):
        # `wrap_prose.py`'s `_segment_block` always flushes its fill segment
        # before a hard-break line, so the line preceding a hard break is
        # never re-filled together with it — the formatter is a no-op here.
        # The gate must agree, or it reports a finding `wrap_prose.py` cannot
        # fix (contradicting this module's own "run wrap_prose.py" remedy).
        body = "# T\n\none two\nthree four  \nfive\n"
        result = run(doc(tmp_path, body), column=20)
        assert result.returncode == 0, result.stderr


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


class TestSiblingBlocksAreNotOneBlock:
    """A line's successor only counts for the under-fill rule when it
    continues the *same* block. A new list-item marker, a block quote at a
    different nesting depth, and a fresh paragraph opener each terminate the
    preceding block exactly as a blank line already does."""

    def test_first_of_two_short_sibling_list_items_is_not_under_filled(self, tmp_path):
        # "- one two" (9 chars) could absorb "-" (the next item's own
        # marker) by length alone, but the two are separate list items —
        # joining them would merge them into one.
        body = "# Title\n\n- one two\n- three four\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_wrapped_items_last_continuation_line_not_under_filled_against_next_marker(
        self, tmp_path
    ):
        # "  five" (6 chars) is the last continuation line of the first
        # item; the next item's marker line follows with no blank line
        # between. Joining them would merge the continuation into the next
        # item's own text.
        body = "# Title\n\n- one two three four\n  five\n- six seven\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_wrapped_items_last_continuation_line_not_under_filled_against_a_nested_next_marker(
        self, tmp_path
    ):
        body = "# Title\n\n- one two three four\n  five\n  - six seven\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_inner_list_item_is_not_a_continuation_of_its_outer_item(self, tmp_path):
        # The outer item's own line, not a continuation line, is followed
        # directly by a nested item — still not a continuation.
        body = "# Title\n\n- one two three\n  - four five\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_paragraph_immediately_followed_by_a_list_item_is_not_under_filled(self, tmp_path):
        body = "# Title\n\none two three\n- four five\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_block_quote_line_not_under_filled_against_a_line_at_different_quote_depth(
        self, tmp_path
    ):
        body = "# Title\n\n> one two\n> > three four\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0

    def test_block_quote_continuation_at_the_same_depth_is_still_reported_when_under_filled(
        self, tmp_path
    ):
        body = "# Title\n\n> one two\n> three four\n"
        result = run(doc(tmp_path, body), column=COL)
        assert result.returncode == 1
        assert "under-filled" in result.stderr

    def test_block_quote_continuation_at_the_same_depth_measured_by_its_real_word_not_its_marker(
        self, tmp_path
    ):
        # A raw tokenizer would see the next line's "> " marker as "the
        # next word" (1 char, always fits) and wrongly flag this as
        # under-filled. The real next word, "seven" (5 chars), does not fit
        # — 15 + 1 + 5 = 21 > 20 — so this line is correctly left alone.
        body = "# Title\n\n> one two three\n> seven eight\n"
        assert run(doc(tmp_path, body), column=COL).returncode == 0


class TestRealContentBrainstormSkill:
    """The assertion that would have caught the original defect: real
    prose, not a synthetic fixture. Before the boundary fix, reflowing this
    file at column 100 and gate-checking it produced ~40 findings, nearly
    all sibling list items wrongly treated as one block."""

    SKILL_SOURCE = REPO_ROOT / "plugins" / "craft" / "skills" / "brainstorm" / "SKILL.md"

    def test_reflowed_brainstorm_skill_exits_the_gate_clean(self, tmp_path):
        import wrap_prose

        target = tmp_path / "SKILL.md"
        target.write_text(self.SKILL_SOURCE.read_text(encoding="utf-8"), encoding="utf-8")
        wrap_prose.format_path(target, wrap_gate.DEFAULT_COLUMN)
        result = wrap_gate.check(target, wrap_gate.DEFAULT_COLUMN)
        assert result == [], f"{len(result)} finding(s):\n" + "\n".join(
            f.message for f in result
        )


def _line_code_spans(text: str) -> list[str]:
    """Every inline code span the tool itself recognizes — matched
    per physical line via the same `_scan_code_span` the gate and
    formatter use, never across a line break. A DOTALL regex over the
    whole file would also match two literal, unrelated backticks that
    happen to straddle a line break in already-hand-wrapped prose; that
    is not a span either tool ever treats as atomic, so counting it
    would flag ordinary whitespace normalization as span corruption."""
    spans = []
    for line in text.splitlines():
        i, n = 0, len(line)
        while i < n:
            if line[i] == "`":
                end = wrap_gate._scan_code_span(line, i)
                if end is not None:
                    spans.append(line[i:end])
                    i = end
                    continue
            i += 1
    return spans


class TestWholeSetAfterReflow:
    """The acceptance gate for the whole slice: with all 39 governed files
    reflowed at column 100 into a scratch copy, no line exceeds 400
    characters *and* the gate exits 0 on every one. Either alone is
    satisfiable by the very defect this task fixes (a line the classifier
    exempts from the gate can still be long, and the reflow it never
    receives leaves it that way) — both must hold together."""

    SKILLS = REPO_ROOT / "plugins" / "craft" / "skills"
    AGENTS = REPO_ROOT / "plugins" / "craft" / "agents"
    MAX_LINE = 400

    def _governed_files(self) -> list[Path]:
        return sorted({*self.SKILLS.rglob("*.md"), *self.AGENTS.rglob("*.md")})

    def _reflow_into_scratch(self, tmp_path: Path) -> list[Path]:
        import wrap_prose

        scratch_skills = tmp_path / "skills"
        scratch_agents = tmp_path / "agents"
        targets = []
        for source in self._governed_files():
            if self.SKILLS in source.parents or source.parent == self.SKILLS:
                rel = source.relative_to(self.SKILLS)
                target = scratch_skills / rel
            else:
                rel = source.relative_to(self.AGENTS)
                target = scratch_agents / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            wrap_prose.format_path(target, wrap_gate.DEFAULT_COLUMN)
            targets.append(target)
        return targets

    def test_governed_set_has_39_files(self):
        assert len(self._governed_files()) == 39

    def test_no_reflowed_line_over_400_characters_and_gate_exits_zero(self, tmp_path):
        # The 400-character ceiling is scoped to lines the gate actually
        # governs — a masked line (fenced code, HTML comment, or YAML
        # frontmatter) is never reflowed by design, so a long line inside one
        # is not evidence of the table/pipe classifier this task fixes, or
        # of anything a reflow could address. Nine agent files' frontmatter
        # `description:` fields already exceed 400 characters on the
        # unreflowed tree today (confirmed via
        # test_craft_prose_wrap_contract.py::test_no_prose_line_over_400_characters,
        # e.g. agents/code-reviewer.md, agents/simplifier.md) — a real,
        # pre-existing, unrelated defect, out of this task's Files and fix
        # surface, so it is excluded here rather than folded in silently.
        targets = self._reflow_into_scratch(tmp_path)
        too_long = []
        gate_findings = []
        for target in targets:
            text = target.read_text(encoding="utf-8")
            lines = wrap_gate._COMMONMARK_LINE_RE.split(text)
            fenced = wrap_gate._mask_fenced_lines(lines)
            frontmatter = wrap_gate._mask_frontmatter_lines(lines)
            too_long.extend(
                (str(target), i + 1, len(line))
                for i, line in enumerate(lines)
                if not (fenced[i] or frontmatter[i]) and len(line) > self.MAX_LINE
            )
            gate_findings.extend(wrap_gate.check(target, wrap_gate.DEFAULT_COLUMN))
        assert too_long == []
        assert gate_findings == [], "\n".join(f.message for f in gate_findings)

    # The files this classifier fix newly un-exempts (see the module docstring's
    # discriminator): idempotence and span fidelity were previously proven only
    # over lines the old rule already admitted as prose, so these are what needs
    # re-checking now that these files' pipe-as-or lines are reflowed for the
    # first time. Scoped to these six rather than all 39: a whole-corpus run also
    # walks unrelated, pre-existing content this task does not touch — e.g.
    # `agents/executor.md` already carries a code span hand-wrapped across two
    # physical lines, which this task's `_scan_code_span` (single-line, unchanged
    # here) never recognized as one span either before or after a reflow. That is
    # real, but it is a distinct defect from the one this task fixes, and it is
    # unaffected by this change — asserting over it here would launder an
    # unrelated finding into this task's contract.
    # (file, 1-based source line number) for each line the old any-pipe rule
    # wrongly exempted as a table row.
    NEWLY_UNEXEMPTED_SITES = [
        (AGENTS / "consistency-auditor.md", 96),
        (AGENTS / "divergence-prober.md", 70),
        (AGENTS / "premise-attacker.md", 74),
        (SKILLS / "_shared" / "execute.md", 414),
        (SKILLS / "_shared" / "execute.md", 512),
        (SKILLS / "brainstorm" / "SKILL.md", 297),
        (SKILLS / "plan" / "SKILL.md", 258),
    ]
    NEWLY_UNEXEMPTED_FILES = sorted({source for source, _ in NEWLY_UNEXEMPTED_SITES})

    # The pre-reflow line shape verbatim, captured from the sites above
    # before the tree-wide reflow landed. A fixture rather than live disk:
    # once the reflow runs, disk no longer carries this shape at all, and a
    # test that reads it there would have nothing left to assert — its
    # subject would already be gone rather than merely different. Pinning
    # the literal shape instead means the property (a `|`-as-or line is
    # classified as prose and reflowed, not exempted as a table row) holds
    # identically whether the real tree has been reflowed yet or not.
    NEWLY_UNEXEMPTED_LINE_FIXTURES = [
        (
            "consistency-auditor.md:96",
            "1. **Verdict** — one line: `coherent` | `gaps` | `contradictory`. "
            "`contradictory` means at least one pair of statements cannot both be satisfied.",
        ),
        (
            "_shared/execute.md:512",
            "Absorb the verdict — `SHIP` | `FIX_FIRST` | `BLOCK` — and triage the findings. "
            "**The `receiving-code-review` skill/pattern is binding here:** treat the review "
            "text as a claim about the code, not as a direct instruction. Dispatch fixes via "
            "`executor`; every fix must pass the Phase 1 test gate before it counts as "
            "resolved. **A fix dispatch is a first dispatch: it runs on Sonnet like any "
            "other** — a reviewer finding a defect is evidence about the code, not about the "
            "tier that has to fix it. Escalate only if that Sonnet fix pass itself fails.",
        ),
    ]

    def test_newly_unexempted_lines_are_classified_as_prose_not_table(self):
        # A `|`-as-or line with no preceding header row and no delimiter row
        # of its own must never be classified "table" — that was the old
        # rule's defect (any unescaped `|` was enough). Pinned against a
        # fixture carrying the pre-reflow line shape verbatim rather than
        # live disk, so it holds whether or not the tree has been reflowed.
        for label, line in self.NEWLY_UNEXEMPTED_LINE_FIXTURES:
            lines = ["# Title", "", line]
            masked = wrap_gate._mask_fenced_lines(lines)
            kinds = wrap_gate.classify_lines(lines, masked)
            assert kinds[2] == "prose", f"{label} was classified {kinds[2]!r}, not 'prose'"

    def test_newly_unexempted_lines_are_actually_reflowed(self, tmp_path):
        # Guards the sibling tests below against passing vacuously: if the
        # classifier still (wrongly) exempted these lines as a table row,
        # they would be passed through byte-identical, and
        # idempotence/span-fidelity would hold trivially without proving
        # anything about the fix. This pins that the exact previously-
        # exempted line no longer survives as a standalone physical line in
        # the output — checking that some line in the file changed would not
        # be enough, since these files have other prose that already needed
        # reflowing regardless of this fix.
        import wrap_prose

        for label, line in self.NEWLY_UNEXEMPTED_LINE_FIXTURES:
            text = f"# Title\n\n{line}\n"
            reflowed_lines = wrap_prose.format_text(text, wrap_gate.DEFAULT_COLUMN).splitlines()
            assert line not in reflowed_lines, f"{label} was not reflowed"

    def test_reflow_is_idempotent_on_every_newly_unexempted_file(self, tmp_path):
        import wrap_prose

        for source in self.NEWLY_UNEXEMPTED_FILES:
            once = wrap_prose.format_text(source.read_text(encoding="utf-8"), wrap_gate.DEFAULT_COLUMN)
            twice = wrap_prose.format_text(once, wrap_gate.DEFAULT_COLUMN)
            assert twice == once, f"{source} is not idempotent"

    def test_reflow_preserves_code_span_multiset_on_every_newly_unexempted_file(self, tmp_path):
        import wrap_prose

        for source in self.NEWLY_UNEXEMPTED_FILES:
            text = source.read_text(encoding="utf-8")
            before = _line_code_spans(text)
            after = _line_code_spans(wrap_prose.format_text(text, wrap_gate.DEFAULT_COLUMN))
            assert sorted(after) == sorted(before), f"{source} lost or gained a code span"


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


class TestFrontmatterMask:
    """A `---`-delimited YAML frontmatter block at the very top of a file is
    masked exactly as a fenced code block already is: the gate never reports
    a finding inside it, but stays fully alert to the prose that follows."""

    def test_no_finding_inside_frontmatter_block(self, tmp_path):
        body = (
            "---\n"
            "name: x\n"
            "description: an extremely long line that blows straight past the tiny budget\n"
            "---\n"
            "\n"
            "short and clean\n"
        )
        result = wrap_gate.check(doc(tmp_path, body), COL)
        assert result == []

    def test_over_budget_and_under_filled_prose_after_the_block_still_reported(self, tmp_path):
        body = (
            "---\n"
            "name: x\n"
            "description: y\n"
            "---\n"
            "\n"
            "one two\n"
            "three\n"
            "\n"
            "aaaa bbbb cccc dddd eeee\n"
        )
        result = wrap_gate.check(doc(tmp_path, body), COL)
        assert any(f.rule == "under-filled" and f.line == 6 for f in result)
        assert any(f.rule == "over-budget" and f.line == 9 for f in result)
        # nothing inside the frontmatter block (lines 1-4) is reported
        assert not any(f.line <= 4 for f in result)

    def test_dashes_not_on_the_first_line_are_an_ordinary_thematic_break(self, tmp_path):
        # A `---` that doesn't open the file is not a frontmatter delimiter —
        # it's an ordinary thematic break, and the short line right before it
        # is still under-filled prose.
        body = "# Title\n\none two\nthree\n\n---\n"
        result = wrap_gate.check(doc(tmp_path, body), COL)
        assert any(f.rule == "under-filled" and f.line == 3 for f in result)

    def test_unterminated_frontmatter_block_fails_closed_not_masked_to_eof(self, tmp_path):
        # Opens with `---` but never closes. A mask running to EOF would
        # silently exempt the whole file from the gate; instead this line
        # (well past the budget) must still be reported.
        body = "---\nname: x\naaaa bbbb cccc dddd eeee\n"
        result = wrap_gate.check(doc(tmp_path, body), COL)
        assert any(f.rule == "over-budget" for f in result)


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
