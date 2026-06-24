"""Slice 6 — librarian output cap guards.

Asserts three changes to the Report structure section of librarian.md:

(a) ## Detail has an explicit bullet cap: the literal `8` appears near `bullet`
    (pinned phrase: "≤8 bullets").

(b) ## Related / adjacent is conditional: the phrase "omit if empty" appears
    in the section (pinned exactly — no hedge).

(c) ## Gaps is conditional: the phrase "omit if empty" appears in the section
    (pinned exactly — no hedge).

(d) ## Short answer and ## Detail headings are still present (structure intact).

Re-grep result: no existing test in tools/lore/tests/ asserts unconditional
presence of all four report sections. Survey confirmed before this test was
written.
"""

from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
AGENTS_DIR = PLUGIN_ROOT / "agents"

_LORE_LIBRARIAN = AGENTS_DIR / "librarian.md"


def _report_section(text: str) -> str:
    """Extract the text from '## Report structure' to the next top-level '##'.

    Handles code fences: '## ' lines inside a fenced block (``` ... ```) are
    part of the section, not section terminators.
    """
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.rstrip() == "## Report structure":
            start = i
            break
    assert start is not None, "## Report structure section not found in librarian.md"

    section_lines = []
    in_fence = False
    for line in lines[start + 1 :]:
        stripped = line.rstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
        if not in_fence and stripped.startswith("## "):
            break
        section_lines.append(line)
    return "".join(section_lines)


class TestDetailBulletCap:
    def test_detail_section_has_bullet_cap_of_8(self):
        """## Detail must state an explicit cap of 8 bullets (pin the literal '8'
        near 'bullet'). Fail-first: currently no cap stated."""
        text = _LORE_LIBRARIAN.read_text()
        section = _report_section(text)
        assert "8 bullet" in section or "8 bullets" in section or "≤8 bullet" in section, (
            "librarian.md Report structure ## Detail section must state an explicit "
            "cap of 8 bullets (e.g. '≤8 bullets'). Currently no cap is stated. "
            "Add the cap so librarian output tokens are bounded."
        )

    def test_detail_section_has_wikilink_over_quoting_instruction(self):
        """## Detail must instruct 'prefer a [[wikilink]] over quoting >2 lines'
        (the new cap-companion instruction). Fail-first: the existing text says
        'bulleted synthesis with [[wikilinks]]' but not the preference rule."""
        text = _LORE_LIBRARIAN.read_text()
        section = _report_section(text)
        assert "over quoting" in section, (
            "librarian.md Report structure ## Detail section must include the "
            "instruction to prefer a [[wikilink]] over quoting >2 lines. "
            "The current text has no such preference rule — add it as part of "
            "the Slice 6 output cap."
        )


class TestConditionalSections:
    def test_related_adjacent_is_conditional(self):
        """## Related / adjacent must be marked 'omit if empty' (pinned phrase).
        Fail-first: currently the section is unconditional."""
        text = _LORE_LIBRARIAN.read_text()
        section = _report_section(text)
        assert "omit if empty" in section, (
            "librarian.md Report structure must mark '## Related / adjacent' as "
            "conditional with the exact phrase 'omit if empty'. "
            "Currently the section is always emitted. "
            "Mark it conditional to reduce output tokens on focused queries."
        )

    def test_gaps_is_conditional(self):
        """## Gaps must be marked 'omit if empty' (pinned phrase).
        Fail-first: currently the section is unconditional."""
        text = _LORE_LIBRARIAN.read_text()
        section = _report_section(text)
        # Count occurrences — we expect at least one for Gaps (there may be one
        # for Related as well, both using the same phrase).
        gaps_idx = section.find("## Gaps")
        assert gaps_idx != -1, (
            "librarian.md Report structure must still contain '## Gaps' (as a "
            "conditional section). It was not found."
        )
        after_gaps = section[gaps_idx:]
        assert "omit if empty" in after_gaps, (
            "librarian.md Report structure must mark '## Gaps' as conditional with "
            "the exact phrase 'omit if empty' appearing after the '## Gaps' heading. "
            "Currently the section is always emitted."
        )


class TestStructureIntact:
    def test_short_answer_heading_present(self):
        """## Short answer must still be in the Report structure section."""
        text = _LORE_LIBRARIAN.read_text()
        section = _report_section(text)
        assert "## Short answer" in section, (
            "librarian.md Report structure must still contain '## Short answer'. "
            "Slice 6 only touches Detail/Related/Gaps — do not remove Short answer."
        )

    def test_detail_heading_present(self):
        """## Detail must still be in the Report structure section."""
        text = _LORE_LIBRARIAN.read_text()
        section = _report_section(text)
        assert "## Detail" in section, (
            "librarian.md Report structure must still contain '## Detail'. "
            "Slice 6 adds a cap to it — do not remove the heading."
        )
