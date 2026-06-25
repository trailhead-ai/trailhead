"""S6 Slice 5 — the new read/capture/dispatch skills (search / record / research)
plus the FINAL cross-skill lockstep grep gate.

This slice adds three skills on top of the rewired session skills (Slice 4):
  - `search`  — wraps `lore search` (KQL-subset read path; replaces old `recall`).
                Carries the `<external-memory>` injection-defense guard because
                search results land in the MAIN session and can include shared-
                layer vault content.
  - `record`  — thin GUIDE for a SINGLE deliberate capture NOW via `lore record`
                / `lore session …`. Its trigger must be scope-disjoint from
                `checkpoint` (which is a session *sweep*).
  - `research` — dispatches the lore `investigator` agent (deep) or `researcher`
                agent (lighter / `tracking`-backlog polling). Dispatch targets
                must resolve to real agent FILES (council Builder).

The FINAL LOCKSTEP GATE (spec AC) greps ALL retained lore skills for the removed
commands (`lore new`, `lore recall`, `lore patch`) and asserts zero matches.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent / "plugins" / "lore"
SKILLS_DIR = PLUGIN_ROOT / "skills"
AGENTS_DIR = PLUGIN_ROOT / "agents"

# The three new skills this slice introduces.
NEW_SKILLS = ("search", "record", "research")


def _skill_text(name: str) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text()


def _frontmatter(name: str) -> str:
    text = _skill_text(name)
    assert text.startswith("---\n"), f"{name}/SKILL.md must open with `---` frontmatter"
    end = text.find("\n---", 3)
    assert end > 0, f"{name}/SKILL.md frontmatter block is not closed"
    return text[3:end]


def _description(name: str) -> str:
    """The frontmatter `description:` value (may span multiple folded lines)."""
    fm = _frontmatter(name)
    lines = fm.splitlines()
    out: list[str] = []
    capturing = False
    for ln in lines:
        if ln.strip().startswith("description:"):
            capturing = True
            out.append(ln.split(":", 1)[1].strip())
            continue
        if capturing:
            # A new top-level YAML key ends the description block.
            if re.match(r"^[a-zA-Z_-]+:", ln):
                break
            out.append(ln.strip())
    return " ".join(p for p in out if p)


# ---------------------------------------------------------------------------
# Presence + registrable frontmatter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_new_skill_present_with_registrable_frontmatter(name: str):
    """Each new skill exists with a closed frontmatter block + non-empty
    description (the field Claude Code uses to register `/lore:<name>`)."""
    skill_md = SKILLS_DIR / name / "SKILL.md"
    assert skill_md.exists(), f"{name}/SKILL.md must exist (Slice 5)"
    assert _description(name), f"{name}/SKILL.md must carry a non-empty description:"


# ---------------------------------------------------------------------------
# research — dispatches the lore agents; targets resolve to real agent FILES
# ---------------------------------------------------------------------------


def test_research_dispatch_targets_resolve_to_real_agent_files():
    """The agents `research` dispatches to must exist as real files (council
    Builder — not merely name-resolution): if KU3 coordination slipped, this
    fails instead of dispatching to a nonexistent agent."""
    for agent in ("investigator", "researcher"):
        agent_file = AGENTS_DIR / f"{agent}.md"
        assert agent_file.exists(), (
            f"research dispatches to `{agent}` but {agent_file} does not exist"
        )
