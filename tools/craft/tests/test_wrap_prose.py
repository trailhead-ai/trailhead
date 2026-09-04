"""Tests for the greedy-fill prose formatter.

`wrap_prose.py`'s contract is defined against `wrap_gate.py` rather than
against a wording: for any input, the formatter's output exits the gate
clean at the same column, and running the formatter on its own output
changes nothing. The gate is imported and called directly — never
reimplemented here.
"""

from __future__ import annotations

import random
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS = REPO_ROOT / "plugins" / "craft" / "scripts"
FORMATTER = SCRIPTS / "wrap_prose.py"

sys.path.insert(0, str(SCRIPTS))
import wrap_gate  # noqa: E402
import wrap_prose  # noqa: E402


def doc(tmp_path: Path, body: str, name: str = "doc.md") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def findings(path: Path, column: int) -> list[wrap_gate.Finding]:
    return wrap_gate.check(path, column)


# Budget of 20 keeps most fixtures readable; wider columns are used where a
# fixture (a code span) is itself long.
COL = 20


class TestBasicReflow:
    def test_over_long_paragraph_reflows_clean_at_the_gates_column(self, tmp_path):
        body = "# Title\n\none two three four five six seven eight nine ten\n"
        formatted = wrap_prose.format_text(body, COL)
        out = doc(tmp_path, formatted)
        assert findings(out, COL) == []
        # sanity: the paragraph really was rewrapped across multiple lines
        assert formatted.count("\n") > body.count("\n")


class TestCodeSpansNeverSplit:
    PEM = "`-----BEGIN [A-Z ]*PRIVATE KEY-----`"
    LORE_CMD = (
        "`lore record update task/prose-formatter --vault trailhead --status done`"
    )

    @staticmethod
    def _assert_span_intact_and_clean(tmp_path, body, span, column):
        formatted = wrap_prose.format_text(body, column)
        assert span in formatted, f"span was split:\n{formatted!r}"
        out = doc(tmp_path, formatted)
        assert findings(out, column) == []

    def test_pem_span_survives_with_no_filler(self, tmp_path):
        body = f"# Title\n\nsee {self.PEM} pattern\n"
        self._assert_span_intact_and_clean(tmp_path, body, self.PEM, 45)

    def test_pem_span_survives_at_every_filler_offset(self, tmp_path):
        # Vary the amount of preceding filler so the greedy-fill boundary
        # falls at a different point relative to the span each time —
        # sometimes just before it, sometimes mid-run.
        for k in range(6):
            filler = " ".join(["word"] * k)
            body = f"# Title\n\n{filler} {self.PEM} tail words after it here\n"
            self._assert_span_intact_and_clean(tmp_path, body, self.PEM, 40)

    def test_long_lore_command_span_survives(self, tmp_path):
        body = f"# Title\n\nrun this {self.LORE_CMD} to record it\n"
        self._assert_span_intact_and_clean(tmp_path, body, self.LORE_CMD, 40)

    def test_span_longer_than_budget_emitted_alone_on_its_own_line(self, tmp_path):
        span = "`" + ("z" * 30) + "`"
        body = f"# Title\n\nsee {span} exactly\n"
        formatted = wrap_prose.format_text(body, COL)
        assert span in formatted
        lines = formatted.split("\n")
        assert any(line.strip() == span for line in lines)
        out = doc(tmp_path, formatted)
        assert findings(out, COL) == []

    def test_adjacent_spans_both_survive_intact(self, tmp_path):
        # Column chosen tight enough that a whitespace-naive fill (ignoring
        # span atomicity) would break inside one of the spans — proven
        # empirically: at column 12 a plain `str.split()`-based fill splits
        # "alpha" from "beta`".
        s1, s2 = "`alpha beta`", "`gamma delta`"
        column = 12
        body = f"# Title\n\nsee {s1} {s2} then more filler words follow after\n"
        formatted = wrap_prose.format_text(body, column)
        assert s1 in formatted
        assert s2 in formatted
        out = doc(tmp_path, formatted)
        assert findings(out, column) == []

    def test_double_backtick_delimited_span_survives(self, tmp_path):
        # Column tight enough (8) that an unprotected fill would break
        # inside the span's internal space.
        span = "``a ` b``"
        column = 8
        body = f"# Title\n\nsee {span} here and then some more filler words\n"
        formatted = wrap_prose.format_text(body, column)
        assert span in formatted
        out = doc(tmp_path, formatted)
        assert findings(out, column) == []

    def test_span_containing_a_backtick_survives(self, tmp_path):
        span = "`` a ` b ``"
        column = 8
        body = f"# Title\n\nsee {span} here and then some more filler words\n"
        formatted = wrap_prose.format_text(body, column)
        assert span in formatted
        out = doc(tmp_path, formatted)
        assert findings(out, column) == []


class TestIdempotence:
    def test_formatting_an_already_formatted_document_is_a_byte_level_no_op(self, tmp_path):
        body = "# Title\n\none two three four five six seven eight nine ten\n"
        once = wrap_prose.format_text(body, COL)
        twice = wrap_prose.format_text(once, COL)
        assert twice == once


class TestPassthroughByteIdentity:
    _OVER_LONG = "aaaa bbbb cccc dddd eeee ffff gggg"

    def test_fenced_block_contents_byte_identical(self, tmp_path):
        body = f"# Title\n\n```\n{self._OVER_LONG}\n```\n"
        assert wrap_prose.format_text(body, COL) == body

    def test_html_comment_contents_byte_identical(self, tmp_path):
        body = f"# Title\n\n<!-- {self._OVER_LONG} -->\n"
        assert wrap_prose.format_text(body, COL) == body

    def test_table_row_byte_identical(self, tmp_path):
        body = f"# Title\n\n| {self._OVER_LONG} | y |\n"
        assert wrap_prose.format_text(body, COL) == body

    def test_heading_byte_identical(self, tmp_path):
        body = f"# {self._OVER_LONG}\n\nshort\n"
        assert wrap_prose.format_text(body, COL) == body


class TestFrontmatterMask:
    """A `---`-delimited YAML frontmatter block at the very top of a file is
    masked exactly as a fenced code block already is: the formatter never
    reflows it, byte-for-byte, including a block-scalar `description:` whose
    continuation lines are indented and under the column budget."""

    def test_frontmatter_block_round_trips_byte_for_byte(self, tmp_path):
        frontmatter = (
            "---\n"
            "name: slice\n"
            "description: >\n"
            "  Choose and materialize the next vertical slice from a spec, then write\n"
            "  it down.\n"
            "---\n"
        )
        body = frontmatter + "\none two three four five six seven eight nine ten\n"
        formatted = wrap_prose.format_text(body, COL)
        assert formatted.startswith(frontmatter)
        out = doc(tmp_path, formatted)
        assert findings(out, COL) == []

    def test_dashes_not_on_the_first_line_are_not_frontmatter_and_prose_still_reflows(
        self, tmp_path
    ):
        body = "# Title\n\none two three four five six seven eight nine ten\n\n---\n"
        formatted = wrap_prose.format_text(body, COL)
        # the paragraph reflowed — it was not exempted as if it were inside
        # a frontmatter block
        assert formatted != body
        out = doc(tmp_path, formatted)
        assert findings(out, COL) == []

    def test_unterminated_frontmatter_is_reflowed_not_masked_to_eof(self, tmp_path):
        # Opens with `---` but never closes. Masking to EOF would silently
        # exempt the whole file from the formatter; instead the opening
        # `---` line is ordinary prose and gets folded into the reflow.
        body = "---\nname: x\none two three four five six seven eight nine ten\n"
        formatted = wrap_prose.format_text(body, COL)
        assert formatted.split("\n", 1)[0] != "---"
        out = doc(tmp_path, formatted)
        assert findings(out, COL) == []


class TestRealSkillAndAgentFrontmatter:
    """The assertion this task exists for: every markdown file craft ships
    under skills/ and agents/, copied and reflowed at the tree-wide reflow's
    column, still parses as valid YAML frontmatter with `name` and
    `description` unchanged from the original — not one file, the whole
    set."""

    TARGET_DIRS = [
        REPO_ROOT / "plugins" / "craft" / "skills",
        REPO_ROOT / "plugins" / "craft" / "agents",
    ]

    @classmethod
    def _target_files(cls) -> list[Path]:
        files: list[Path] = []
        for d in cls.TARGET_DIRS:
            files.extend(sorted(d.rglob("*.md")))
        return files

    @staticmethod
    def _frontmatter_fields(text: str) -> dict[str, str] | None:
        """None if `text` doesn't open with a closed `---` frontmatter
        block; otherwise a mapping of each top-level key to its full raw
        value, continuation lines (block scalars) included verbatim."""
        if not text.startswith("---\n"):
            return None
        end = text.find("\n---", 3)
        if end <= 0:
            return None
        fields: dict[str, list[str]] = {}
        current_key: str | None = None
        for line in text[4:end].split("\n"):
            if line and not line[0].isspace() and ":" in line:
                key, _, value = line.partition(":")
                current_key = key.strip()
                fields[current_key] = [value.strip()]
            elif current_key is not None:
                fields[current_key].append(line)
        return {key: "\n".join(vals) for key, vals in fields.items()}

    def test_target_set_has_39_files(self):
        # find tools/craft/plugins/craft/skills tools/craft/plugins/craft/agents
        #   -name '*.md' | wc -l  ->  39
        assert len(self._target_files()) == 39

    def test_every_file_reflows_with_frontmatter_intact(self, tmp_path):
        for source in self._target_files():
            original = source.read_text(encoding="utf-8")
            before = self._frontmatter_fields(original)
            target = tmp_path / source.name
            target.write_text(original, encoding="utf-8")
            wrap_prose.format_path(target, wrap_gate.DEFAULT_COLUMN)
            reflowed = target.read_text(encoding="utf-8")
            after = self._frontmatter_fields(reflowed)
            assert (before is None) == (after is None), source
            if before is None:
                continue
            assert after.get("name") == before.get("name"), source
            assert after.get("description") == before.get("description"), source
            result = wrap_gate.check(target, wrap_gate.DEFAULT_COLUMN)
            assert result == [], (source, [f.message for f in result])

    def test_registrable_frontmatter_check_passes_after_reflow(self, tmp_path):
        import test_craft_skills_registrable as registrable

        for skill_md in registrable._skill_files():
            original = skill_md.read_text(encoding="utf-8")
            target = tmp_path / f"{skill_md.parent.name}.md"
            target.write_text(original, encoding="utf-8")
            wrap_prose.format_path(target, wrap_gate.DEFAULT_COLUMN)
            text = target.read_text(encoding="utf-8")
            assert text.startswith("---\n"), skill_md
            end = text.find("\n---", 3)
            assert end > 0, skill_md
            frontmatter = text[3:end]

            def _has(field: str) -> bool:
                return any(
                    ln.strip().startswith(f"{field}:") and ln.split(":", 1)[1].strip()
                    for ln in frontmatter.splitlines()
                )

            assert _has("name"), skill_md
            assert _has("description"), skill_md


class TestListItems:
    def test_wrapped_list_item_keeps_marker_and_fills_continuation_at_indented_width(
        self, tmp_path
    ):
        body = "# Title\n\n- one two three four five six seven eight nine ten\n"
        formatted = wrap_prose.format_text(body, COL)
        lines = formatted.split("\n")
        assert lines[2].startswith("- ")
        for line in lines[3:]:
            if line.strip():
                assert line.startswith("  ")
        out = doc(tmp_path, formatted)
        assert findings(out, COL) == []

    def test_nested_list_item_keeps_its_prefix_on_every_produced_line(self, tmp_path):
        body = "# Title\n\n  - one two three four five six seven eight nine\n"
        formatted = wrap_prose.format_text(body, COL)
        lines = [l for l in formatted.split("\n")[2:] if l.strip()]
        assert lines[0].startswith("  - ")
        for line in lines[1:]:
            assert line.startswith("    ")
        out = doc(tmp_path, formatted)
        assert findings(out, COL) == []

    def test_block_quote_keeps_its_prefix_on_every_produced_line(self, tmp_path):
        # Single physical line only: a multi-line block quote cannot be
        # made gate-clean by any formatter — see
        # TestKnownGateLimitationBlockquoteContinuation below for the proof.
        # This test pins the structural property that IS achievable: the
        # "> " prefix survives, on the one line produced.
        body = "# Title\n\n> one two three\n"
        formatted = wrap_prose.format_text(body, COL)
        lines = [l for l in formatted.split("\n")[2:] if l.strip()]
        for line in lines:
            assert line.startswith("> ")
        out = doc(tmp_path, formatted)
        assert findings(out, COL) == []


class TestMultilineBlockquoteIsGateClean:
    """`wrap_gate.py`'s under-fill rule now measures a block-quote
    continuation line's real first word, after stripping its "> " marker,
    and only compares two lines at all when they share the same quote
    depth. A correctly-wrapped multi-line quote is therefore gate-clean —
    real craft prose has multi-line block quotes (e.g.
    `skills/brainstorm/SKILL.md`)."""

    def test_correctly_wrapped_multiline_quote_exits_the_gate_clean(self, tmp_path):
        column = 20
        body = "# Title\n\n> one two three four five six seven eight nine ten eleven twelve\n"
        formatted = wrap_prose.format_text(body, column)
        out = doc(tmp_path, formatted)
        result = findings(out, column)
        assert result == [], f"unexpected finding(s): {result} for {formatted!r}"
        lines = [l for l in formatted.split("\n")[2:] if l.strip()]
        assert len(lines) > 1
        for line in lines:
            assert line.startswith("> ")


class TestBlankLineSeparation:
    def test_blank_line_between_paragraphs_preserved_and_blocks_not_merged(self, tmp_path):
        body = (
            "# Title\n\n"
            "one two three four five six seven\n\n"
            "eight nine ten eleven twelve thirteen\n"
        )
        formatted = wrap_prose.format_text(body, COL)
        # exactly one blank line still separates the two paragraphs
        assert "\n\n\n" not in formatted
        parts = formatted.split("\n\n")
        assert len(parts) == 3  # title block, paragraph one, paragraph two
        out = doc(tmp_path, formatted)
        assert findings(out, COL) == []


class TestHardLineBreak:
    def test_hard_break_line_ends_its_run_and_is_left_untouched(self, tmp_path):
        body = "# Title\n\none two  \nthree\n"
        formatted = wrap_prose.format_text(body, COL)
        lines = formatted.split("\n")
        assert lines[2] == "one two  "
        out = doc(tmp_path, formatted)
        assert findings(out, COL) == []


class TestTrailingNewline:
    def test_file_with_trailing_newline_round_trips_the_property(self, tmp_path):
        body = "# Title\n\none two three four five six seven eight\n"
        assert body.endswith("\n")
        formatted = wrap_prose.format_text(body, COL)
        assert formatted.endswith("\n")

    def test_file_without_trailing_newline_round_trips_the_property(self, tmp_path):
        body = "# Title\n\none two three four five six seven eight"
        assert not body.endswith("\n")
        formatted = wrap_prose.format_text(body, COL)
        assert not formatted.endswith("\n")


class TestCli:
    def test_cli_rewrites_the_file_in_place(self, tmp_path):
        body = "# Title\n\none two three four five six seven eight nine ten\n"
        path = doc(tmp_path, body)
        result = subprocess.run(
            [sys.executable, str(FORMATTER), "--column", str(COL), str(path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        rewritten = path.read_text(encoding="utf-8")
        assert rewritten != body
        assert findings(path, COL) == []


def _code_span_multiset(text: str) -> list[str]:
    lines = wrap_gate._COMMONMARK_LINE_RE.split(text)
    spans: list[str] = []
    for line in lines:
        for token in wrap_gate._tokenize(line):
            if token.startswith("`"):
                spans.append(token)
    return sorted(spans)


def _random_document(rng: random.Random) -> str:
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]

    def paragraph() -> str:
        n = rng.randint(3, 14)
        tokens = []
        for _ in range(n):
            if rng.random() < 0.15:
                span_words = rng.randint(1, 3)
                tokens.append("`" + " ".join(rng.choices(words, k=span_words)) + "`")
            else:
                tokens.append(rng.choice(words))
        return " ".join(tokens)

    def list_item() -> str:
        marker = rng.choice(["- ", "* ", "1. "])
        return marker + paragraph()

    def blockquote() -> str:
        # Capped to a couple of short words so it always fits on one
        # physical line at the corpus's test column: a multi-line block
        # quote cannot be made gate-clean by any formatter (see
        # TestKnownGateLimitationBlockquoteContinuation) — that is a
        # documented gate limitation, not a property this corpus exercises.
        return "> " + " ".join(rng.choices(words, k=2))

    blocks = []
    blocks.append("# Title")
    for _ in range(rng.randint(2, 5)):
        kind = rng.choice(["paragraph", "list", "quote", "fence", "heading"])
        if kind == "paragraph":
            blocks.append(paragraph())
        elif kind == "list":
            blocks.append(list_item())
        elif kind == "quote":
            blocks.append(blockquote())
        elif kind == "fence":
            blocks.append("```\n" + paragraph() + "\n```")
        else:
            blocks.append("## " + paragraph())
    return "\n\n".join(blocks) + "\n"


class TestPropertyCorpus:
    SEED = 20260904

    def test_generated_corpus_satisfies_the_formatters_contract(self, tmp_path):
        rng = random.Random(self.SEED)
        column = 40
        for trial in range(50):
            body = _random_document(rng)
            try:
                once = wrap_prose.format_text(body, column)
                out = doc(tmp_path, once, name=f"doc{trial}.md")
                gate_findings = findings(out, column)
                assert gate_findings == [], (
                    f"seed={self.SEED} trial={trial} gate findings={gate_findings}\n"
                    f"input={body!r}\noutput={once!r}"
                )
                assert _code_span_multiset(once) == _code_span_multiset(body), (
                    f"seed={self.SEED} trial={trial} span multiset changed\n"
                    f"input={body!r}\noutput={once!r}"
                )
                twice = wrap_prose.format_text(once, column)
                assert twice == once, (
                    f"seed={self.SEED} trial={trial} second pass not identical\n"
                    f"once={once!r}\ntwice={twice!r}"
                )
            except AssertionError:
                print(f"property test failed at seed={self.SEED} trial={trial}: {body!r}")
                raise
