"""Every shipped agent must be registrable by Claude Code.

forge is agent-centric (its reason to exist is hosting general-dev agents). An
agent `.md` only registers as a dispatchable `subagent_type` if it opens with a
YAML frontmatter block carrying a non-empty `name:` and `description:`. This
test locks that invariant so an agent can't silently fail to register.

Live proof of the mechanism (KU1): the lore plugin's `lore-librarian` agent
appears in the running session's registry as the namespaced subagent_type
`lore:lore-librarian`. forge's agents register the same way as `forge:<name>`.
"""
from pathlib import Path

import pytest

AGENTS_DIR = Path(__file__).parent.parent / "plugins" / "forge" / "agents"


def _agent_files() -> list[Path]:
    return sorted(AGENTS_DIR.glob("*.md"))


def test_at_least_one_agent_ships():
    """forge's whole point is hosting agents — guard against an empty dir."""
    assert _agent_files(), "forge must ship at least one agent (the proof agent)"


@pytest.mark.parametrize("agent_md", _agent_files(), ids=lambda p: p.stem)
def test_agent_has_registrable_frontmatter(agent_md: Path):
    text = agent_md.read_text()
    assert text.startswith("---\n"), (
        f"{agent_md.name} must open with a `---` frontmatter block or Claude "
        "Code will not register it as a subagent_type"
    )
    end = text.find("\n---", 3)
    assert end > 0, f"{agent_md.name} frontmatter block is not closed"
    frontmatter = text[3:end]

    def _has(field: str) -> bool:
        return any(
            ln.strip().startswith(f"{field}:") and ln.split(":", 1)[1].strip()
            for ln in frontmatter.splitlines()
        )

    assert _has("name"), f"{agent_md.name} frontmatter must carry a non-empty `name:`"
    assert _has("description"), (
        f"{agent_md.name} frontmatter must carry a non-empty `description:` "
        "(it's what drives agent dispatch)"
    )


def _frontmatter(agent_md: Path) -> str:
    text = agent_md.read_text()
    end = text.find("\n---", 3)
    return text[3:end]


def _tools_line(frontmatter: str) -> str | None:
    for ln in frontmatter.splitlines():
        if ln.strip().startswith("tools:"):
            return ln.split(":", 1)[1].strip()
    return None


def test_planner_tools_line_is_generic():
    """The planner agent dropped its `mcp__brain__*` tools during genericization.
    Its `tools:` line must be exactly the generic resolvable list — `Write` is
    required so the agent can still emit specs/plans — and must carry NO
    `mcp__brain__*` residue (council Builder Important)."""
    planner_md = AGENTS_DIR / "planner.md"
    assert planner_md.exists(), f"Expected planner.md in {AGENTS_DIR}"
    tools = _tools_line(_frontmatter(planner_md))
    assert tools is not None, "planner.md frontmatter must carry a `tools:` line"
    assert "mcp__brain__" not in tools, (
        f"planner.md `tools:` still references a brain MCP tool: {tools!r}. "
        "Replace the entire tools line with the generic list."
    )
    declared = [t.strip() for t in tools.split(",") if t.strip()]
    assert declared == ["Read", "Grep", "Glob", "Write", "WebFetch", "WebSearch", "Bash"], (
        f"planner.md `tools:` must be exactly the generic list "
        "'Read, Grep, Glob, Write, WebFetch, WebSearch, Bash' "
        f"(Write required to emit specs/plans), got {tools!r}"
    )
