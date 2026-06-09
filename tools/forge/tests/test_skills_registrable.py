"""Every shipped forge skill must be registrable by Claude Code.

A SKILL.md only registers as an invocable `/forge:<name>` command if it opens
with a YAML frontmatter block carrying at least a non-empty `name:` and
`description:`. This test locks the invariant so a skill can't silently fail to
register (the same failure mode that bit lore's first capture skills).

`skills/_shared/` is a reference doc, not a skill, and is exempt.
"""
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parent.parent / "plugins" / "forge" / "skills"


def _skill_files() -> list[Path]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(
        d / "SKILL.md"
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name != "_shared" and (d / "SKILL.md").exists()
    )


@pytest.mark.parametrize("skill_md", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_has_registrable_frontmatter(skill_md: Path):
    text = skill_md.read_text()
    assert text.startswith("---\n"), (
        f"{skill_md.parent.name}/SKILL.md must open with a `---` frontmatter "
        "block or Claude Code will not register it as a /forge: command"
    )
    end = text.find("\n---", 3)
    assert end > 0, f"{skill_md.parent.name}/SKILL.md frontmatter block is not closed"
    frontmatter = text[3:end]

    def _has(field: str) -> bool:
        return any(
            ln.strip().startswith(f"{field}:") and ln.split(":", 1)[1].strip()
            for ln in frontmatter.splitlines()
        )

    assert _has("name"), (
        f"{skill_md.parent.name}/SKILL.md frontmatter must carry a non-empty `name:`"
    )
    assert _has("description"), (
        f"{skill_md.parent.name}/SKILL.md frontmatter must carry a non-empty "
        "`description:` (it's what drives skill triggering)"
    )


AGENTS_DIR = Path(__file__).parent.parent / "plugins" / "forge" / "agents"

# Curated set of forge agents the subagent-driven-development controller
# dispatches by name. Kept explicit (not prose-parsed) to avoid false positives
# on ordinary words. The test cross-checks two invariants: (a) every name here
# actually appears in the skill text (so a controller-side rename that drops a
# dispatch can't leave a stale expectation), and (b) every name resolves to an
# installed forge agent file (so a dispatch can't silently dead-end).
_SDD_DISPATCHED_AGENTS: list[str] = [
    "sdd-assumption-prover",
    "sdd-implementer",
    "code-reviewer",
    "test-runner",
    "troubleshooter",
]


def test_sdd_dispatched_agents_resolve():
    """Every forge agent the SDD skill dispatches must resolve to a file.

    A future rename of a forge agent would otherwise silently dead-end a
    dispatch in subagent-driven-development. This locks the cross-reference:
    each dispatched agent name is both named in the skill AND installed as
    `plugins/forge/agents/<name>.md`.
    """
    skill_md = SKILLS_DIR / "subagent-driven-development" / "SKILL.md"
    assert skill_md.exists(), (
        f"Expected subagent-driven-development/SKILL.md in {SKILLS_DIR}."
    )
    text = skill_md.read_text()
    for agent in _SDD_DISPATCHED_AGENTS:
        assert agent in text, (
            f"subagent-driven-development/SKILL.md no longer dispatches "
            f"{agent!r} — update _SDD_DISPATCHED_AGENTS if the dispatch was "
            "intentionally removed, or restore the dispatch."
        )
        agent_file = AGENTS_DIR / f"{agent}.md"
        assert agent_file.exists(), (
            f"subagent-driven-development/SKILL.md dispatches {agent!r} but "
            f"{agent_file} does not exist. A dispatch must not dead-end — "
            "install the agent or rename the dispatch to an installed one."
        )


def test_handoff_and_pickup_skills_present():
    """Guard against a dev-ritual skill dir silently disappearing.

    handoff + pickup are the dev rituals forge owns (P3-B2). They are listed
    here from the start so the expected-set is a stable contract even before
    pickup lands.
    """
    names = {p.parent.name for p in _skill_files()}
    expected = {"handoff", "pickup"}
    missing = expected - names
    assert not missing, f"expected forge skills missing from the plugin: {sorted(missing)}"
