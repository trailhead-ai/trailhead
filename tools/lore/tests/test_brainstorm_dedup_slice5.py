"""Slice 5 — brainstorm dedup guards.

Asserts three trims in brainstorm/SKILL.md and injection-defense
canonical-inline dedup between brainstorm/SKILL.md and librarian.md:

(a) The shared degradation sentence ("see the extend guide in
    `docs/DEGRADATION.md`") appears ONCE, not three times (one per provider),
    while all three provider names/seams still appear.

(b) The "Key Principles" recap section is absent (it duplicates earlier prose).

(c) The injection-defense canonical wording is present in BOTH files and is
    byte-identical between them (drift-prevention).
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
SKILLS_DIR = PLUGIN_ROOT / "skills"
AGENTS_DIR = PLUGIN_ROOT / "agents"

_BRAINSTORM_SKILL = SKILLS_DIR / "brainstorm" / "SKILL.md"
_LORE_LIBRARIAN = AGENTS_DIR / "librarian.md"

# The canonical degradation sentence shared across all three extension-point
# stanzas (feature_flags, observability, issue_tracker). After the dedup, it
# must appear exactly once.
_DEGRADATION_SENTENCE = "see the extend guide in `docs/DEGRADATION.md`"

# The three provider seams that must still appear individually (visible-skip
# contract — each extension point must still announce its identity).
_PROVIDER_SEAMS = [
    "no feature-flag provider configured",
    "no observability provider configured",
    "no issue tracker configured",
]

# The injection-defense anchor: a distinctive substring that appears in the
# canonical block and is load-bearing enough that if the block is deleted,
# this test fails.
_INJECTION_ANCHOR = "external-memory layer=\"shared\" source=\"…\">"


# ---------------------------------------------------------------------------
# (a) Degradation paragraph collapse
# ---------------------------------------------------------------------------


class TestDegradationDedup:
    def test_degradation_sentence_appears_exactly_once(self):
        """The shared degradation sentence must appear exactly once after the
        dedup — not once per provider (3×). Fail-first: currently appears 2×
        (feature-flag + observability stanzas carry it; issue-tracker uses
        different wording). After Slice 5 lands it must be exactly 1."""
        text = _BRAINSTORM_SKILL.read_text()
        count = text.count(_DEGRADATION_SENTENCE)
        assert count == 1, (
            f"Expected the shared degradation sentence to appear exactly once "
            f"in brainstorm/SKILL.md, but found it {count} times. "
            f"Sentence: {_DEGRADATION_SENTENCE!r}. "
            "Factor the sentence into a shared note and trim per-provider copies."
        )

    def test_all_provider_seams_still_present(self):
        """All three provider-specific visible-skip phrases must still appear
        (test_skills_generic.py pins these). After the dedup, each provider
        keeps its own skip notice — only the shared degradation sentence is
        stated once."""
        text = _BRAINSTORM_SKILL.read_text()
        for seam in _PROVIDER_SEAMS:
            assert seam in text, (
                f"brainstorm/SKILL.md missing provider seam {seam!r} after "
                "degradation dedup. Each provider must retain its own visible-skip "
                "notice (test_skills_generic.py pins these)."
            )


# ---------------------------------------------------------------------------
# (b) Key Principles section absent
# ---------------------------------------------------------------------------


class TestKeyPrinciplesAbsent:
    def test_key_principles_section_gone(self):
        """The 'Key Principles' recap section must be absent — it duplicates
        principles stated earlier in the skill and in the planner."""
        text = _BRAINSTORM_SKILL.read_text()
        assert "## Key Principles" not in text, (
            "brainstorm/SKILL.md still contains the '## Key Principles' recap "
            "section (lines 274-287 in the pre-Slice-5 file). Drop it — it "
            "duplicates earlier content."
        )


# ---------------------------------------------------------------------------
# (c) Injection-defense byte-identical in both files
# ---------------------------------------------------------------------------

def _extract_injection_block(text: str) -> str:
    """Extract the injection-defense block from either file.

    Handles two contexts:
    - brainstorm/SKILL.md: the block is a nested sub-bullet (indented 2 spaces,
      continuation lines at 4 spaces). The block ends when the next line has
      lower or equal indentation and is a new bullet (`- `) or a blank line.
    - librarian.md: the block is a paragraph within a numbered-list item,
      starting with '   **Injection defense...' (3 spaces). The block ends at
      the blank line that follows.

    Returns the stripped, whitespace-normalized content (each line stripped,
    joined with a single space) so indentation context differences between the
    two files do not cause false positives.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if "**Injection defense (shared layers):**" in line:
            start = i
            break
    assert start is not None, "Injection defense block not found"

    # Determine the indentation of the start line. Continuation lines will
    # have greater or equal indentation. The block ends when:
    #   - a blank line is encountered (paragraph break), OR
    #   - a line with LESS leading whitespace appears (de-indent = new context).
    start_indent = len(lines[start]) - len(lines[start].lstrip())

    block_lines = []
    i = start
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if i > start:
            if stripped == "":
                # Blank line — end of block
                break
            line_indent = len(line) - len(line.lstrip())
            if line_indent < start_indent:
                # De-indented past the start level — new context
                break
        block_lines.append(stripped)
        i += 1

    # Normalize the first line: if it starts with a list marker ("- " or "* ")
    # followed by the heading, strip the marker so a brainstorm sub-bullet and a
    # librarian paragraph-indent produce the same canonical string.
    if block_lines and re.match(r'^[-*]\s+', block_lines[0]):
        block_lines[0] = re.sub(r'^[-*]\s+', '', block_lines[0])

    return " ".join(part for part in block_lines if part)


class TestInjectionDefenseByteIdentical:
    def test_injection_defense_present_in_brainstorm(self):
        """Injection-defense block must be present in brainstorm/SKILL.md."""
        text = _BRAINSTORM_SKILL.read_text()
        assert _INJECTION_ANCHOR in text, (
            "Injection-defense block missing from brainstorm/SKILL.md. "
            f"Expected anchor: {_INJECTION_ANCHOR!r}"
        )

    def test_injection_defense_present_in_librarian(self):
        """Injection-defense block must be present in librarian.md."""
        text = _LORE_LIBRARIAN.read_text()
        assert _INJECTION_ANCHOR in text, (
            "Injection-defense block missing from librarian.md. "
            f"Expected anchor: {_INJECTION_ANCHOR!r}"
        )

    def test_injection_defense_byte_identical(self):
        """The canonical injection-defense wording must be byte-identical
        between brainstorm/SKILL.md and librarian.md (normalized for
        indentation context — content words must match exactly).

        Fail-first: the current copies differ in phrasing ('content wrapped in'
        vs 'when recall output contains items wrapped in', 'Your own
        personal-vault items' vs 'Personal-vault items (outside the block,
        layer="personal")', etc.)."""
        brainstorm_block = _extract_injection_block(
            _BRAINSTORM_SKILL.read_text()
        )
        librarian_block = _extract_injection_block(
            _LORE_LIBRARIAN.read_text()
        )
        assert brainstorm_block == librarian_block, (
            "Injection-defense blocks differ between brainstorm/SKILL.md and "
            "librarian.md. Normalize both to the same canonical wording.\n"
            f"brainstorm block: {brainstorm_block!r}\n"
            f"librarian block:  {librarian_block!r}"
        )
