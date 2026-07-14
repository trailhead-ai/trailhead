"""Every shipped agent/skill must be registrable by the harness — for every tool.

A subagent ``.md`` only registers as a dispatchable ``subagent_type`` if it opens
with a YAML frontmatter block carrying a non-empty ``name:`` and ``description:``;
a ``SKILL.md`` only registers as ``/<tool>:<name>`` under the same two fields. (A
``tools:`` line is optional — omitting it inherits all tools — so it is not part of
the registrability floor.) In both cases ``name:`` must equal the on-disk stem
(agent filename / skill dir) or the harness registers it under the wrong id.

This is the *content* contract on the frontmatter, complementary to
``test_capability_coverage`` (which proves the wiring *closure*: nothing on disk is
orphaned, nothing declared dangles). A malformed frontmatter block passes wiring
but silently fails to register — this test is the only guard against that.

Consolidated here, parametrized over every tool, so it replaces the per-tool
copy-paste that each tool carried. Tools that layer *extra* assertions
(craft's execute-dispatch resolution, lore's injection-defense scans, …) keep
their own files; this is the shared floor every tool stands on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS = ["lore", "camp", "craft", "portage"]


def _plugin_root(tool: str) -> Path:
    return _REPO_ROOT / "tools" / tool / "plugins" / tool


def _frontmatter(md: Path) -> str:
    text = md.read_text()
    assert text.startswith("---\n"), (
        f"{md} must open with a `---` frontmatter block or the harness will not "
        "register it"
    )
    end = text.find("\n---", 3)
    assert end > 0, f"{md} frontmatter block is not closed"
    return text[3:end]


def _field(frontmatter: str, field: str) -> str | None:
    for ln in frontmatter.splitlines():
        if ln.strip().startswith(f"{field}:"):
            return ln.split(":", 1)[1].strip()
    return None


def _agent_files(tool: str) -> list[Path]:
    agents = _plugin_root(tool) / "agents"
    return sorted(agents.glob("*.md")) if agents.is_dir() else []


def _skill_files(tool: str) -> list[Path]:
    skills = _plugin_root(tool) / "skills"
    if not skills.is_dir():
        return []
    return sorted(
        d / "SKILL.md"
        for d in skills.iterdir()
        if d.is_dir() and d.name != "_shared" and (d / "SKILL.md").is_file()
    )


# Flatten to (tool, path) pairs so each agent/skill is its own parametrized case
# with a legible id, across every tool, discovered on disk (never hand-listed).
_AGENT_CASES = [(t, p) for t in _TOOLS for p in _agent_files(t)]
_SKILL_CASES = [(t, p) for t in _TOOLS for p in _skill_files(t)]


@pytest.mark.parametrize(
    "tool,agent_md", _AGENT_CASES, ids=[f"{t}:{p.stem}" for t, p in _AGENT_CASES]
)
def test_agent_has_registrable_frontmatter(tool: str, agent_md: Path):
    fm = _frontmatter(agent_md)
    assert _field(fm, "name"), f"{tool}:{agent_md.name} frontmatter needs a non-empty `name:`"
    assert _field(fm, "description"), (
        f"{tool}:{agent_md.name} frontmatter needs a non-empty `description:`"
    )
    name = _field(fm, "name")
    assert name == agent_md.stem, (
        f"{tool}:{agent_md.name} frontmatter name={name!r} must equal the filename stem "
        f"{agent_md.stem!r} (registers as {tool}:{agent_md.stem})"
    )


@pytest.mark.parametrize(
    "tool,skill_md", _SKILL_CASES, ids=[f"{t}:{p.parent.name}" for t, p in _SKILL_CASES]
)
def test_skill_has_registrable_frontmatter(tool: str, skill_md: Path):
    fm = _frontmatter(skill_md)
    assert _field(fm, "name"), (
        f"{tool}:{skill_md.parent.name}/SKILL.md frontmatter needs a non-empty `name:`"
    )
    assert _field(fm, "description"), (
        f"{tool}:{skill_md.parent.name}/SKILL.md frontmatter needs a non-empty `description:`"
    )
    name = _field(fm, "name")
    assert name == skill_md.parent.name, (
        f"{tool}:{skill_md.parent.name}/SKILL.md frontmatter name={name!r} must equal the "
        f"skill dir name (registers as /{tool}:{skill_md.parent.name})"
    )
