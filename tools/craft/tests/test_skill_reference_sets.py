"""A skill that reads `_shared/execute.md` end to end must name every
`_shared` document that procedure's rules depend on, in the SKILL.md itself.

`execute.md` states three of its rules — task-status ownership, the
standalone refine procedure, and design-doc state coverage — at the point of
use without naming `status-ownership.md`, `refine.md`, or `slice.md` by path
(see `reference_depth_gate.py`). A reader who opens only the SKILL.md must
still reach all three in one hop: they are promoted into the reference set of
every SKILL.md that reads `execute.md` end to end, so no rule the agent is
held to sits behind a reference a partial read can drop.

This asserts structurally that each filename is named — not that it is read
in any particular order, or with any particular wording around it.
"""

from __future__ import annotations

from pathlib import Path

SKILLS = Path(__file__).parent.parent / "plugins" / "craft" / "skills"

# The three sibling `_shared` documents `execute.md`'s rules draw on without
# naming any of them by path.
EXECUTE_MD_DEPENDENCIES = ("status-ownership.md", "refine.md", "slice.md")


def _text(skill_name: str) -> str:
    return (SKILLS / skill_name / "SKILL.md").read_text(encoding="utf-8")


# The anchor drive/SKILL.md's build-phase read directive opens with — the
# sentence that actually tells the reader what to read *now*, as opposed to a
# trailing aside about what was supposedly read earlier.
_DRIVE_READ_DIRECTIVE_ANCHOR = "Read `../_shared/execute.md` now"


def _unconditional_read_clause(text: str) -> str:
    """The portion of the build-phase read directive up to its first em
    dash — the list of documents the sentence actually puts the reader onto
    reading now. A name that appears only after the em dash, in a
    parenthetical about a document supposedly "already read above", is a
    passing mention, not part of the unconditional read: a reader who stops
    at the main clause never learns to open that file at all.
    """
    start = text.index(_DRIVE_READ_DIRECTIVE_ANCHOR)
    end = text.index("—", start)
    return text[start:end]


def test_execute_skill_names_every_shared_document_execute_md_needs():
    text = _text("execute")
    missing = [name for name in EXECUTE_MD_DEPENDENCIES if name not in text]
    assert not missing, f"skills/execute/SKILL.md never names {missing}"


def test_drive_skill_names_every_shared_document_in_its_unconditional_read_directive():
    """`drive/SKILL.md` reads `_shared/execute.md` end to end at its build
    phase, so it needs the same reference set `execute/SKILL.md` needs —
    and each name must sit in the directive's own unconditional read
    clause, not in a conditional aside claiming it was read elsewhere."""
    clause = _unconditional_read_clause(_text("drive"))
    missing = [name for name in EXECUTE_MD_DEPENDENCIES if name not in clause]
    assert not missing, (
        "skills/drive/SKILL.md's build-phase read directive (the clause "
        f"before its first em dash) never names {missing} as something to "
        "read now — a name appearing only after the em dash, in a "
        "conditional or 'already read above' aside, is a passing mention, "
        "not an unconditional read"
    )
