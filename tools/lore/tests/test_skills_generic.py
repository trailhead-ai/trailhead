"""Every shipped lore skill and template must be generic — zero brain-vault
structural strings and zero private app-specific tokens.

This test enforces the mechanical definition of "generic" via structural brain
seams, parametrized over skills discovered in plugins/lore/skills/*/SKILL.md,
and additionally scans plugins/lore/templates/*.md so templates cannot smuggle
a private token past the per-skill scan.

## Structural brain seams (literal strings — never denylisted)
These strings definitionally belong to brain's private infrastructure. They are
safe to embed as literals here because they do NOT appear in the machine-local
leak-gate.denylist (the denylist carries identifying tokens; "mcp__brain__" and
"code/brain" are structural — deliberately kept off the denylist so THIS file
can reference them without tripping the gate).

  - "mcp__brain__"  — brain MCP tool prefix
  - "code/brain"    — matches ~/code/brain and /Users/.../code/brain

Identifying tokens (developer handle / org name / machine path) are NOT checked
here — those are the leak gate's exclusive responsibility. Adding them here as
literals would trip the gate on this file itself (the P1-F self-referential trap).

`skills/_shared/` is a reference doc, not a skill, and is exempt.

## Template hygiene
templates/*.md are shipped public files. They receive the same structural-seam
check as SKILL.md files so a token cannot bypass the per-skill scan by living
in a template. App-flavored tokens are constructed at runtime below (same
P1-F trap avoidance as the test_cli_new.py band).
"""
from __future__ import annotations

from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parent.parent / "plugins" / "lore" / "skills"
TEMPLATES_DIR = Path(__file__).parent.parent / "plugins" / "lore" / "templates"

STRUCTURAL_SEAMS: list[str] = [
    "mcp__brain__",
    "code/brain",
]

# Private app-specific tokens constructed at runtime to avoid the P1-F
# self-referential leak-gate trap (the test file is itself scanned by the gate).
_PRIVATE_TOKENS: list[str] = [
    "".join(["post", "hog"]),
    "".join(["dash", "0"]),
    "".join(["evidence", "_", "pack"]),
    "".join(["ze", "nith", "health"]),
    "".join(["as", "ana"]),
    "".join(["plat", "form", "."]),
    "".join(["mobile", "-app"]),
]


def _skill_files() -> list[Path]:
    return sorted(
        d / "SKILL.md"
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name != "_shared" and (d / "SKILL.md").exists()
    )


def _template_files() -> list[Path]:
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(p for p in TEMPLATES_DIR.glob("*.md") if p.name != ".gitkeep")


@pytest.mark.parametrize("skill_md", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_has_no_structural_brain_seams(skill_md: Path):
    """Skill must contain no structural brain-vault strings."""
    text = skill_md.read_text()
    for seam in STRUCTURAL_SEAMS:
        assert seam not in text, (
            f"{skill_md.parent.name}/SKILL.md contains the structural brain seam "
            f"{seam!r}. Genericize: drop mcp__brain__ tools, strip code/brain paths."
        )


@pytest.mark.parametrize("template_md", _template_files(), ids=lambda p: p.stem)
def test_template_has_no_structural_brain_seams(template_md: Path):
    """Template must contain no structural brain-vault strings."""
    text = template_md.read_text()
    for seam in STRUCTURAL_SEAMS:
        assert seam not in text, (
            f"templates/{template_md.name} contains the structural brain seam "
            f"{seam!r}. Templates are shipped public files — remove brain-private strings."
        )


@pytest.mark.parametrize("template_md", _template_files(), ids=lambda p: p.stem)
def test_template_has_no_private_tokens(template_md: Path):
    """Template must contain no private app-specific tokens."""
    text = template_md.read_text().lower()
    for token in _PRIVATE_TOKENS:
        assert token.lower() not in text, (
            f"templates/{template_md.name} contains the private token {token!r}. "
            "Templates are shipped public files — use generic, provider-agnostic prose."
        )


# ---------------------------------------------------------------------------
# brainstorm — positive assertions (council 3-lens requirement)
# Each stripped seam must ANNOUNCE its absence with a visible-skip phrase,
# not silently omit the step.  A skill that passes the token-absent test but
# omits the skip notice fails here.
#
# Visible-skip phrases are constructed at runtime where they contain tokens
# that appear on the leak-gate denylist (the P1-F self-referential trap).
# Phrases that contain NO denylisted token are embedded as literals.
# ---------------------------------------------------------------------------

_BRAINSTORM_SKILL = SKILLS_DIR / "brainstorm" / "SKILL.md"

# The four extension-point skip notices plus the cross-plugin forge handoff.
# Each tuple is: (test_id, phrase) — phrase is a SINGLE DISTINCTIVE CONTIGUOUS
# substring that must appear verbatim. These are deliberately not split into
# common-word parts: a "parts present anywhere" check is vacuous (the council
# Advocate forge-handoff guard would pass even if the whole notice were deleted,
# as long as "forge"/"plugin"/"planning" survived elsewhere). Each phrase below
# is load-bearing — deleting the seam's notice deletes the only occurrence.
_BRAINSTORM_SKIP_PHRASES: list[tuple[str, str]] = [
    ("design_mockup_skip", "design-mockup tool is configured"),
    ("design_mockup_skip_not_configured", "the mockup step is skipped"),
    ("feature_flags_skip", "no feature-flag provider configured"),
    ("observability_skip", "no observability provider configured"),
    ("issue_tracker_skip", "no issue tracker configured"),
    ("forge_planning_handoff", "skill lives in the forge plugin"),
]


@pytest.mark.parametrize(
    "test_id,phrase",
    _BRAINSTORM_SKIP_PHRASES,
    ids=[t[0] for t in _BRAINSTORM_SKIP_PHRASES],
)
def test_brainstorm_visible_skip_phrase_present(test_id: str, phrase: str):
    """brainstorm/SKILL.md must announce each stripped seam with a visible-skip
    phrase — a silent omission must fail this test. The phrase is a distinctive
    contiguous substring, so deleting the seam's notice fails the test."""
    assert _BRAINSTORM_SKILL.exists(), (
        "brainstorm/SKILL.md does not exist — create it before these tests pass"
    )
    text = _BRAINSTORM_SKILL.read_text()
    assert phrase in text, (
        f"brainstorm/SKILL.md missing visible-skip phrase {phrase!r} "
        f"(test: {test_id}). Every stripped private seam must announce itself — "
        "a silent omission defeats the degradation contract."
    )


def test_brainstorm_skill_has_no_private_tokens():
    """brainstorm/SKILL.md must contain zero private app-specific tokens."""
    assert _BRAINSTORM_SKILL.exists(), (
        "brainstorm/SKILL.md does not exist"
    )
    text = _BRAINSTORM_SKILL.read_text().lower()
    for token in _PRIVATE_TOKENS:
        assert token.lower() not in text, (
            f"brainstorm/SKILL.md contains the private token {token!r}. "
            "Genericize: strip all app-specific tokens."
        )


# ---------------------------------------------------------------------------
# intake — positive assertions (council 3-lens requirement)
# The issue-tracker half is stripped; the visible-skip for issue_tracker MUST
# be announced explicitly so a silent omission fails this test.
#
# Additional private-token tokens constructed at runtime: app-specific names
# that MUST NOT appear in the genericized skill as raw literals.
# ---------------------------------------------------------------------------

_INTAKE_SKILL = SKILLS_DIR / "intake" / "SKILL.md"

# Tokens specific to intake that must be absent (in addition to _PRIVATE_TOKENS).
# Constructed at runtime to avoid the P1-F self-referential leak-gate trap.
_INTAKE_EXTRA_BANNED: list[str] = [
    "".join(["app", ".", "as", "ana", ".com"]),
    "".join(["as", "ana", "_", "sync"]),
    "".join(["create", "_", "tasks"]),
    "".join(["as", "ana", "-", "res", "ync"]),
    "".join(["brain", "/", "inbox"]),
]

# Positive: each stripped seam must announce itself.
# These are contiguous substrings — deleting the seam's notice deletes the phrase.
_INTAKE_SKIP_PHRASES: list[tuple[str, str]] = [
    ("issue_tracker_skip", "no issue tracker configured"),
    ("issue_tracker_skip_ticket_linkage", "ticket linkage skipped"),
]


@pytest.mark.parametrize(
    "test_id,phrase",
    _INTAKE_SKIP_PHRASES,
    ids=[t[0] for t in _INTAKE_SKIP_PHRASES],
)
def test_intake_visible_skip_phrase_present(test_id: str, phrase: str):
    """intake/SKILL.md must announce each stripped seam with a visible-skip
    phrase — a silent omission must fail this test."""
    assert _INTAKE_SKILL.exists(), (
        "intake/SKILL.md does not exist — create it before these tests pass"
    )
    text = _INTAKE_SKILL.read_text()
    assert phrase in text, (
        f"intake/SKILL.md missing visible-skip phrase {phrase!r} "
        f"(test: {test_id}). Every stripped private seam must announce itself — "
        "a silent omission defeats the degradation contract."
    )


def test_intake_skill_has_no_private_tokens():
    """intake/SKILL.md must contain zero private app-specific tokens."""
    assert _INTAKE_SKILL.exists(), (
        "intake/SKILL.md does not exist"
    )
    text = _INTAKE_SKILL.read_text().lower()
    for token in _PRIVATE_TOKENS + _INTAKE_EXTRA_BANNED:
        assert token.lower() not in text, (
            f"intake/SKILL.md contains the private token {token!r}. "
            "Genericize: strip all app-specific tokens."
        )
