"""Tests for the contents-block gate.

The gate certifies that a document's marker-fenced contents block still agrees
with the document's own headings. It answers one question: would a reader who
navigates by the block land on a section that exists?

It is not a prose assertion. Every heading in these fixtures may be reworded
freely and the gate stays green, so long as the block is reworded with it — the
gate binds two halves of a document to each other, never to a fixed phrase.

Exit-code contract (matches `leak_gate.py` and `covers_gate.py`):
  0 → clean (every heading has an entry, every entry has a heading, same order)
  1 → drift (a missing entry, an entry naming no heading, a wrong order, or a
      wrong nesting level — prints a `reason:` line per disagreement)
  2 → error / fail-closed (path missing or unreadable, no contents block, an
      unterminated contents block, or an unterminated code fence)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GATE = REPO_ROOT / "plugins" / "craft" / "scripts" / "toc_gate.py"


def run(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
    )


def doc(tmp_path: Path, body: str, name: str = "doc.md") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


IN_AGREEMENT = """\
# Title

Opening preamble.

<!-- toc:start -->
**Contents**

- Alpha
- Beta
  - Beta one
<!-- toc:end -->

## Alpha

Text.

## Beta

Text.

### Beta one

Text.
"""


class TestAgreement:
    def test_matching_block_exits_zero(self, tmp_path):
        assert run(doc(tmp_path, IN_AGREEMENT)).returncode == 0

    def test_rewording_both_halves_together_stays_clean(self, tmp_path):
        """The gate binds the block to the headings, never to a fixed phrase.

        Renaming every section and the matching entries is not drift — a gate
        that reddens here is a prose pin wearing a consistency check's clothes.
        """
        reworded = IN_AGREEMENT.replace("Alpha", "Gamma").replace("Beta", "Delta")
        assert run(doc(tmp_path, reworded)).returncode == 0


class TestDrift:
    def test_heading_with_no_entry_is_drift(self, tmp_path):
        body = IN_AGREEMENT.replace("## Beta\n\nText.\n\n", "## Beta\n\nText.\n\n## Omega\n\nText.\n\n")
        result = run(doc(tmp_path, body))
        assert result.returncode == 1
        assert "Omega" in result.stderr

    def test_entry_naming_no_heading_is_drift(self, tmp_path):
        body = IN_AGREEMENT.replace("- Beta\n", "- Beta\n- Phantom\n")
        result = run(doc(tmp_path, body))
        assert result.returncode == 1
        assert "Phantom" in result.stderr

    def test_renaming_a_heading_alone_is_drift(self, tmp_path):
        """The stale-block failure this gate exists for: the document moved on."""
        body = IN_AGREEMENT.replace("## Alpha", "## Alpha, revised")
        result = run(doc(tmp_path, body))
        assert result.returncode == 1
        assert "Alpha, revised" in result.stderr

    def test_wrong_order_is_drift(self, tmp_path):
        body = IN_AGREEMENT.replace("- Alpha\n- Beta\n", "- Beta\n- Alpha\n")
        result = run(doc(tmp_path, body))
        assert result.returncode == 1

    def test_wrong_nesting_level_is_drift(self, tmp_path):
        """A `###` section is a two-space-indented entry; flattening it is drift."""
        body = IN_AGREEMENT.replace("  - Beta one", "- Beta one")
        result = run(doc(tmp_path, body))
        assert result.returncode == 1
        assert "Beta one" in result.stderr


class TestFailClosed:
    def test_missing_path_exits_two(self, tmp_path):
        assert run(tmp_path / "absent.md").returncode == 2

    def test_no_contents_block_exits_two(self, tmp_path):
        body = "# Title\n\nPreamble.\n\n## Alpha\n\nText.\n"
        result = run(doc(tmp_path, body))
        assert result.returncode == 2
        assert "contents block" in result.stderr.lower()

    def test_unterminated_contents_block_exits_two(self, tmp_path):
        body = IN_AGREEMENT.replace("<!-- toc:end -->\n", "")
        assert run(doc(tmp_path, body)).returncode == 2

    def test_unterminated_code_fence_exits_two(self, tmp_path):
        """An unclosed fence makes every heading below it unclassifiable.

        Guessing would let a real drift pass as 'inside a fence'.
        """
        body = IN_AGREEMENT + "\n```sh\necho unterminated\n"
        assert run(doc(tmp_path, body)).returncode == 2

    def test_empty_contents_block_exits_two(self, tmp_path):
        body = IN_AGREEMENT.replace("- Alpha\n- Beta\n  - Beta one\n", "")
        result = run(doc(tmp_path, body))
        assert result.returncode == 2


class TestMultiplePaths:
    def test_every_failing_path_is_reported(self, tmp_path):
        clean = doc(tmp_path, IN_AGREEMENT, "clean.md")
        drifted = doc(tmp_path, IN_AGREEMENT.replace("- Alpha\n", ""), "drifted.md")
        other = doc(tmp_path, IN_AGREEMENT.replace("- Beta\n", ""), "other.md")
        result = run(clean, drifted, other)
        assert result.returncode == 1
        assert "drifted.md" in result.stderr
        assert "other.md" in result.stderr

    def test_all_clean_exits_zero(self, tmp_path):
        a = doc(tmp_path, IN_AGREEMENT, "a.md")
        b = doc(tmp_path, IN_AGREEMENT, "b.md")
        assert run(a, b).returncode == 0


class TestForgedStructure:
    """A gate that derives ground truth from a document format must not be
    steerable by text that only looks like that format."""

    def test_heading_inside_a_code_fence_is_not_a_section(self, tmp_path):
        """Template text showing `## Findings` is an output shape, not a section.

        Requiring it in the block would send a reader to prose that is not there.
        """
        body = IN_AGREEMENT + "\n```markdown\n## Findings\n\n- [Critical] ...\n```\n"
        assert run(doc(tmp_path, body)).returncode == 0

    def test_contents_markers_inside_a_code_fence_are_not_the_block(self, tmp_path):
        """A document documenting this convention shows the markers verbatim.

        Reading a fenced example as the document's own block would let an
        author replace real navigation with an illustration.
        """
        body = (
            "# Title\n\nPreamble.\n\n"
            "```markdown\n<!-- toc:start -->\n- Illustration\n<!-- toc:end -->\n```\n\n"
            "## Alpha\n\nText.\n"
        )
        result = run(doc(tmp_path, body))
        assert result.returncode == 2
        assert "contents block" in result.stderr.lower()

    def test_bullet_inside_a_nested_fence_is_not_an_entry(self, tmp_path):
        body = IN_AGREEMENT.replace(
            "  - Beta one\n",
            "  - Beta one\n\n```\n- Not an entry\n```\n",
        )
        assert run(doc(tmp_path, body)).returncode == 0

    def test_line_separator_cannot_forge_a_heading(self, tmp_path):
        """U+2028 ends a line for some renderers and not for `str.split`.

        A gate that treats it as a line break sees a heading the block cannot
        name, and reddens on a document that is actually consistent.
        """
        body = IN_AGREEMENT.replace("Opening preamble.", "Opening preamble. ## Forged")
        assert run(doc(tmp_path, body)).returncode == 0

    def test_html_comment_cannot_hide_a_real_heading(self, tmp_path):
        """A commented-out heading is not a section a reader can reach."""
        body = IN_AGREEMENT.replace("## Alpha", "<!-- ## Hidden -->\n\n## Alpha")
        assert run(doc(tmp_path, body)).returncode == 0

    def test_inline_marker_in_prose_does_not_open_the_block(self, tmp_path):
        """A document explaining the convention names the marker in a sentence.

        Matching the marker anywhere in a line rather than as the whole line
        would let that sentence open the block early and sweep unrelated
        preamble bullets in as entries.
        """
        body = IN_AGREEMENT.replace(
            "Opening preamble.",
            "Opening preamble. Write `<!-- toc:start -->` above the first section.\n\n"
            "- A preamble bullet that is not an entry",
        )
        assert run(doc(tmp_path, body)).returncode == 0
