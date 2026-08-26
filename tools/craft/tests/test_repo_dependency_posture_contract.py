"""This repository's own declared dependency posture — the target of the prior-art
survey block's first step (`brainstorm/SKILL.md` / `plan/SKILL.md`): "Read this
repository's declared dependency posture from its agent-instruction file (e.g.
`CLAUDE.md`) — never inferred from a manifest or lockfile."

Before this slice, trailhead's zero-dependency stance existed only as descriptive
install prose inside `## Commands` — not a statement a survey step would land on as
a posture declaration in its own right. This pins that the posture now has its own
named section, stated prescriptively and findable as a standalone statement, rather
than blended into the commands prose.

Content anchors on the shipped markdown, not a runtime harness — same contract-pin
style as `test_prior_art_survey_contract.py`.
"""

from __future__ import annotations

from pathlib import Path

CLAUDE_MD = Path(__file__).parent.parent.parent.parent / "CLAUDE.md"

POSTURE_HEADING = "## Dependency Posture"
COMMANDS_HEADING = "## Commands"


def _text() -> str:
    assert CLAUDE_MD.exists(), f"Expected CLAUDE.md at {CLAUDE_MD}"
    return CLAUDE_MD.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    assert heading in text, f"CLAUDE.md must carry the {heading!r} heading"
    start = text.index(heading)
    body_start = start + len(heading)
    next_heading = text.find("\n## ", body_start)
    end = next_heading if next_heading != -1 else len(text)
    return text[start:end]


def test_claude_md_carries_a_named_dependency_posture_section():
    """The posture must be a standalone section a survey step lands on directly —
    present as its own heading line, not merely as a substring anywhere in the
    file (e.g. inside a sentence quoting or describing the section)."""
    text = _text()
    assert any(line.strip() == POSTURE_HEADING for line in text.splitlines()), (
        f"{POSTURE_HEADING!r} must appear as its own heading line in CLAUDE.md"
    )


def test_dependency_posture_section_is_distinct_from_the_commands_section():
    """Slice 4's council review flagged restating the posture inside `## Commands`
    as blending two reading purposes — the posture gets its own home, and its
    prescriptive prose is not restated inside `## Commands`."""
    text = _text()
    posture_section = _section(text, POSTURE_HEADING)
    commands_section = _section(text, COMMANDS_HEADING)
    assert COMMANDS_HEADING not in posture_section
    assert POSTURE_HEADING not in commands_section
    assert "no new dependencies" not in commands_section, (
        "the posture's prescriptive declaration must not be restated inside "
        "`## Commands`"
    )


def test_dependency_posture_section_names_the_no_new_dependencies_posture():
    """Named consistently with the survey block's own vocabulary
    (`brainstorm/SKILL.md`: "Under a no-new-dependencies posture ...") so the
    declaration reads as the same posture the survey step is asking about."""
    section = _section(_text(), POSTURE_HEADING)
    assert "no-new-dependencies posture" in section or "no new dependencies" in section


def test_dependency_posture_section_scopes_itself_to_this_repository():
    section = _section(_text(), POSTURE_HEADING)
    assert "this repository only" in section.lower()
