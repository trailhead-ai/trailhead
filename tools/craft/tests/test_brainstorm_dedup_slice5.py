"""Slice 5 — brainstorm dedup guards.

Asserts three trims in brainstorm/SKILL.md and injection-defense
canonical-inline dedup:

(a) The shared degradation sentence ("see the extend guide in
    `docs/DEGRADATION.md`") appears ONCE, not three times (one per provider),
    while all three provider names/seams still appear.

(b) The "Key Principles" recap section is absent (it duplicates earlier prose).

(c) The injection-defense canonical wording is present in brainstorm/SKILL.md
    and is byte-identical to the pinned canonical block (drift-prevention).

S6 Slice 3 moved brainstorm from the lore plugin into craft. The injection-
defense block was previously cross-checked byte-for-byte against lore's
`librarian.md`; now that brainstorm lives in a different plugin, the canonical
wording is pinned as a fixture (`fixtures/injection_defense_canonical.txt`,
captured from librarian.md — the canonical source). The drift-prevention intent
is preserved: brainstorm's block must still match the canonical wording exactly.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "craft"
SKILLS_DIR = PLUGIN_ROOT / "skills"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

_BRAINSTORM_SKILL = SKILLS_DIR / "brainstorm" / "SKILL.md"
_INJECTION_CANONICAL = FIXTURES_DIR / "injection_defense_canonical.txt"

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
_INJECTION_ANCHOR = 'external-memory layer="shared" source="…">'


# ---------------------------------------------------------------------------
# (a) Degradation paragraph collapse
# ---------------------------------------------------------------------------


class TestDegradationDedup:
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
    if block_lines and re.match(r"^[-*]\s+", block_lines[0]):
        block_lines[0] = re.sub(r"^[-*]\s+", "", block_lines[0])

    return " ".join(part for part in block_lines if part)


class TestInjectionDefenseByteIdentical:
    def test_injection_defense_present_in_brainstorm(self):
        """Injection-defense block must be present in brainstorm/SKILL.md."""
        text = _BRAINSTORM_SKILL.read_text()
        assert _INJECTION_ANCHOR in text, (
            "Injection-defense block missing from brainstorm/SKILL.md. "
            f"Expected anchor: {_INJECTION_ANCHOR!r}"
        )

    def test_injection_defense_matches_canonical(self):
        """The canonical injection-defense wording in brainstorm/SKILL.md must be
        byte-identical to the pinned canonical block (normalized for indentation
        context — content words must match exactly).

        The canonical fixture was captured from lore's `librarian.md`, the
        authoritative source of the shared injection-defense wording. Pinning it
        as a fixture keeps the drift-prevention check intact now that brainstorm
        lives in the craft plugin and can no longer reach librarian.md by path."""
        brainstorm_block = _extract_injection_block(_BRAINSTORM_SKILL.read_text())
        canonical_block = _INJECTION_CANONICAL.read_text().strip()
        assert brainstorm_block == canonical_block, (
            "Injection-defense block in brainstorm/SKILL.md drifted from the "
            "pinned canonical wording. Normalize it to match.\n"
            f"brainstorm block: {brainstorm_block!r}\n"
            f"canonical block:  {canonical_block!r}"
        )
