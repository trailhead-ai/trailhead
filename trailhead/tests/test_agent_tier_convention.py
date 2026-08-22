"""Every shipped agent must declare its cost settings deliberately.

`model:` and `effort:` are the two largest per-dispatch cost multipliers under
trailhead's control. An agent that omits either inherits a value nobody chose,
and the inherited value is invisible at review time — the frontmatter looks
fine because the field simply is not there.

These tests make the omission loud. They assert declaration, not any particular
value: choosing `haiku`/`low` for a mechanical agent and `opus`/`xhigh` for a
synthesis agent are both correct outcomes, and the convention in
`trailhead/docs/agent-cost-convention.md` says how to pick. What is not correct is
leaving the choice to inheritance.
"""

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_TOOLS = _REPO_ROOT / "tools"

_VALID_MODELS = {"haiku", "sonnet", "opus"}
_VALID_EFFORTS = {"low", "medium", "high", "xhigh"}


def _agent_files() -> list[Path]:
    return sorted(_TOOLS.glob("*/plugins/*/agents/*.md"))


def _frontmatter(text: str) -> str:
    assert text.startswith("---\n"), "agent file must open with a frontmatter block"
    end = text.find("\n---", 3)
    assert end > 0, "agent frontmatter block is not closed"
    return text[3:end]


def _field(frontmatter: str, field: str) -> str | None:
    for ln in frontmatter.splitlines():
        if ln.strip().startswith(f"{field}:"):
            value = ln.split(":", 1)[1].strip()
            return value or None
    return None


def _agent_id(path: Path) -> str:
    return f"{path.parts[-3]}:{path.stem}"


def test_agents_are_discovered():
    """Guard the glob itself — a silent zero would make every test below vacuous."""
    assert len(_agent_files()) >= 20, (
        f"expected the full composed agent set, found {len(_agent_files())}"
    )


@pytest.mark.parametrize("agent_md", _agent_files(), ids=_agent_id)
def test_agent_declares_model(agent_md: Path):
    model = _field(_frontmatter(agent_md.read_text()), "model")
    assert model is not None, (
        f"{_agent_id(agent_md)} declares no `model:` — it would silently inherit "
        "the dispatching session's tier. Choose one deliberately; see "
        "trailhead/docs/agent-cost-convention.md"
    )
    assert model in _VALID_MODELS, (
        f"{_agent_id(agent_md)} declares model `{model}`, not one of "
        f"{sorted(_VALID_MODELS)}"
    )


@pytest.mark.parametrize("agent_md", _agent_files(), ids=_agent_id)
def test_agent_declares_effort(agent_md: Path):
    effort = _field(_frontmatter(agent_md.read_text()), "effort")
    assert effort is not None, (
        f"{_agent_id(agent_md)} declares no `effort:` — it would silently inherit "
        "a deliberation level nobody chose. Choose one deliberately; see "
        "trailhead/docs/agent-cost-convention.md"
    )
    assert effort in _VALID_EFFORTS, (
        f"{_agent_id(agent_md)} declares effort `{effort}`, not one of "
        f"{sorted(_VALID_EFFORTS)}"
    )
