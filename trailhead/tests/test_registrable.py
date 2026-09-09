"""Every dispatchable name trailhead wires must be the name the harness registers.

Two registries decide what an operator can actually invoke, and they are keyed
differently:

* **trailhead's**, built by ``load_manifest`` → ``compose_plan`` → ``apply_plan``.
  ``_discover_subagents`` keys a subagent by its **filename stem** and
  ``_discover_skills`` keys a skill by its **directory name**; those keys are what
  ``trailhead install --subagent <name>`` selects on and what lands in the composed
  tree.
* **the harness's**, which registers a composed agent as ``subagent_type: <name>``
  and a composed skill as ``/<tool>:<name>`` reading the ``name:`` field out of the
  file's own YAML frontmatter.

When the two disagree, ``trailhead install`` wires a capability the operator then
cannot invoke under the name they selected it by — the install reports success and
the dispatch fails. This suite runs the trailhead half for real (compose the full
inventory of every tool into a tmp dest, then read back what compose actually
wrote) and asserts the composed file's declared identity equals the key compose
filed it under.

**Scope.** The harness half cannot be executed here, so this pins agreement, not
registration. ``claude plugin validate`` is not an oracle for it either: an agent
file with no frontmatter at all, and one whose ``name:`` differs from its stem,
both pass that command clean. What it does check — a skill missing a
``description:`` earns a warning — is a lint on prose quality rather than a
dispatch contract, and is left to that command rather than restated here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest
from trailhead.compose import apply_plan, compose_plan

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS = ["lore", "camp", "craft", "portage", "outpost", "trailhead"]


def _declared_name(md: Path) -> str | None:
    """The ``name:`` a YAML frontmatter block declares, or None if there is no
    closed frontmatter block or no ``name:`` in it.

    Deliberately returns None rather than raising on a malformed block: a file
    the harness cannot read a name out of is unregistrable for the same reason
    a mismatched name is, and the caller reports both as one failure.
    """
    text = md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    for line in text[3:end].splitlines():
        if line.strip().startswith("name:"):
            return line.split(":", 1)[1].strip() or None
    return None


@pytest.fixture(scope="module")
def composed(tmp_path_factory) -> dict[str, Path]:
    """Compose every tool's FULL inventory once, and hand back each dest.

    Composing rather than reading ``tools/<tool>/plugins/`` directly is the point:
    the files under test are the ones the installer produced, and the name each is
    keyed by is the one ``compose_plan`` filed it under.
    """
    root = tmp_path_factory.mktemp("composed")
    dests: dict[str, Path] = {}
    for tool in _TOOLS:
        manifest = load_manifest(_REPO_ROOT / "tools" / tool / "capabilities.toml")
        dest = root / tool
        apply_plan(
            compose_plan(
                manifest,
                {name: None for name in manifest.subagents},
                {name: None for name in manifest.skills},
                dest,
            )
        )
        dests[tool] = dest
    return dests


def _cases() -> list[tuple[str, str, str]]:
    """(tool, kind, name) for every selectable capability of every tool.

    Read off the real manifests at collection time so a capability added on disk
    is covered without being hand-listed here.
    """
    out: list[tuple[str, str, str]] = []
    for tool in _TOOLS:
        manifest = load_manifest(_REPO_ROOT / "tools" / tool / "capabilities.toml")
        out += [(tool, "agent", name) for name in manifest.subagents]
        out += [(tool, "skill", name) for name in manifest.skills]
    return out


_CASES = _cases()


def test_the_inventory_is_not_empty():
    """Anti-vacuity: a manifest-loading change that silently discovered nothing
    would leave the parametrization below empty and green.
    """
    agents = [c for c in _CASES if c[1] == "agent"]
    skills = [c for c in _CASES if c[1] == "skill"]
    assert len(agents) >= 20, f"expected the full agent inventory, found {len(agents)}"
    assert len(skills) >= 20, f"expected the full skill inventory, found {len(skills)}"


@pytest.mark.parametrize(
    "tool,kind,name", _CASES, ids=[f"{t}:{k}:{n}" for t, k, n in _CASES]
)
def test_composed_capability_declares_the_name_it_was_wired_under(
    tool: str, kind: str, name: str, composed: dict[str, Path]
):
    md = (
        composed[tool] / "agents" / f"{name}.md"
        if kind == "agent"
        else composed[tool] / "skills" / name / "SKILL.md"
    )
    assert md.is_file(), (
        f"{tool}: compose wired {kind} {name!r} but produced no {md.name} for it at "
        f"{md.relative_to(composed[tool])}"
    )
    declared = _declared_name(md)
    assert declared == name, (
        f"{tool}: `trailhead install` wires this {kind} under {name!r}, but the "
        f"composed file declares name={declared!r} — the harness registers it under "
        f"the declared name, so selecting it by {name!r} installs a capability that "
        f"cannot be dispatched by that name"
    )
