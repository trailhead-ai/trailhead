"""Every shipped portage agent must be registrable by Claude Code.

An agent `.md` only registers as a dispatchable `subagent_type` if it opens with
a YAML frontmatter block carrying a non-empty `name:`, `description:`, and
`tools:`. This test locks that invariant so a portage agent can't silently fail
to register as `portage:<name>`. It also pins `name:` == filename stem.

Unique basename (test_portage_agents_registrable.py, not test_agents_registrable.py)
so it does not collide with craft's/lore's same-named test under a shared pytest run.
"""

from pathlib import Path

import pytest

AGENTS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "portage" / "agents"


def _agent_files() -> list[Path]:
    return sorted(AGENTS_DIR.glob("*.md"))


def _frontmatter(agent_md: Path) -> str:
    text = agent_md.read_text()
    assert text.startswith("---\n"), (
        f"{agent_md.name} must open with a `---` frontmatter block or Claude "
        "Code will not register it as a subagent_type"
    )
    end = text.find("\n---", 3)
    assert end > 0, f"{agent_md.name} frontmatter block is not closed"
    return text[3:end]


def _field(frontmatter: str, field: str) -> str | None:
    for ln in frontmatter.splitlines():
        if ln.strip().startswith(f"{field}:"):
            return ln.split(":", 1)[1].strip()
    return None


def test_at_least_three_agents_ship():
    """portage ships summarizer, updater, monitor."""
    names = {p.stem for p in _agent_files()}
    assert {"summarizer", "updater", "monitor"} <= names, (
        f"portage must ship summarizer/updater/monitor agents — found {sorted(names)}"
    )


@pytest.mark.parametrize("agent_md", _agent_files(), ids=lambda p: p.stem)
def test_agent_has_registrable_frontmatter(agent_md: Path):
    fm = _frontmatter(agent_md)
    assert _field(fm, "name"), f"{agent_md.name} frontmatter must carry a non-empty `name:`"
    assert _field(fm, "description"), (
        f"{agent_md.name} frontmatter must carry a non-empty `description:`"
    )
    assert _field(fm, "tools"), f"{agent_md.name} frontmatter must carry a non-empty `tools:`"


@pytest.mark.parametrize("agent_md", _agent_files(), ids=lambda p: p.stem)
def test_agent_name_matches_filename_stem(agent_md: Path):
    fm = _frontmatter(agent_md)
    name = _field(fm, "name")
    assert name == agent_md.stem, (
        f"{agent_md.name} frontmatter name={name!r} must equal the filename stem "
        f"{agent_md.stem!r} (Claude Code registers it as portage:{agent_md.stem})"
    )
