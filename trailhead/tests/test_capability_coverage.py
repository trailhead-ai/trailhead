"""Coverage guard: every on-disk skill/agent must be base or selectable.

The bug this prevents
---------------------
``trailhead install`` only wires a plugin's ``base`` (always-on) set plus the
subagents/skills selected by name from the discovered inventory.  A skill dir or
agent file that lands on disk but is neither in ``base`` nor discoverable as a
selectable entry is invisible to the installer — even a full "install everything"
config skips it.  That is exactly how ``skills/_shared`` (craft's ``council.md``)
went un-wired while the ``plan`` skill read it at runtime; it is now ``base``.

This test asserts the closure: for every tool, every ``skills/<dir>`` and every
``agents/<x>.md`` on disk is either listed in ``base`` or appears in the
discovered selectable inventory.  A skill dir with no ``SKILL.md`` that is not in
``base`` fails RED (add a ``SKILL.md`` to make it selectable, or list it in
``base`` to always ship it).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest
from trailhead.compose import apply_plan, compose_plan

# Matches a ${CLAUDE_PLUGIN_ROOT}/hooks/<script> reference inside a hooks.json
# command string (script name stops at the closing quote / whitespace).
_HOOK_SCRIPT_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/(hooks/[^\"\s\\]+)")

_REPO_ROOT = Path(__file__).parent.parent.parent
_TOOLS = ["lore", "camp", "craft", "portage"]


def _manifest_path(tool: str) -> Path:
    return _REPO_ROOT / "tools" / tool / "capabilities.toml"


def _plugin_root(tool: str) -> Path:
    return _REPO_ROOT / "tools" / tool / "plugins" / tool


def _wired(tool: str) -> set[str]:
    """All skill/agent rel-paths the installer can wire: base + selectable inventory."""
    m = load_manifest(_manifest_path(tool))
    refs: set[str] = set(m.base)
    refs.update(m.subagents.values())
    refs.update(m.skills.values())
    return refs


def _on_disk(tool: str) -> set[str]:
    """Every shippable skill dir (skills/<x>) and agent file (agents/<x>.md) on disk."""
    root = _plugin_root(tool)
    found: set[str] = set()
    skills_dir = root / "skills"
    if skills_dir.is_dir():
        for child in skills_dir.iterdir():
            if child.is_dir():
                found.add(f"skills/{child.name}")
    agents_dir = root / "agents"
    if agents_dir.is_dir():
        for child in agents_dir.iterdir():
            if child.suffix == ".md" and child.is_file():
                found.add(f"agents/{child.name}")
    return found


@pytest.mark.parametrize("tool", _TOOLS)
def test_every_on_disk_skill_and_agent_is_wired(tool: str):
    """No orphans: every skill dir / agent file on disk is base or selectable."""
    orphans = sorted(_on_disk(tool) - _wired(tool))
    assert not orphans, (
        f"{tool}: these skills/agents exist on disk but are neither in `base` nor "
        f"discoverable as selectable, so `trailhead install` will never wire them: "
        f"{orphans}. Add a SKILL.md (to make a skill selectable) or list it in "
        f"`base` in tools/{tool}/capabilities.toml."
    )


@pytest.mark.parametrize("tool", _TOOLS)
def test_manifest_validates_against_disk(tool: str):
    """load_manifest(validate=True) proves base/hooks paths exist."""
    load_manifest(_manifest_path(tool))


@pytest.mark.parametrize("tool", _TOOLS)
def test_every_declared_capability_resolves_to_existing_src(tool: str, tmp_path: Path):
    """Oracle: compose_plan for a tool's FULL inventory must produce only
    CopyOps whose src exists on disk — no manifest entry or discovered selectable
    may dangle.

    The inverse of ``test_every_on_disk_skill_and_agent_is_wired`` (which proves
    nothing on disk is orphaned): this proves nothing *declared* points at a
    missing src. Preserved from the retired ``test_renames_guard.py`` and
    generalized from lore+craft to every tool.
    """
    m = load_manifest(_manifest_path(tool))
    plan = compose_plan(
        m,
        {n: None for n in m.subagents},
        {n: None for n in m.skills},
        tmp_path / "all",
    )
    missing = [str(op.src) for op in plan.ops if not op.src.exists()]
    assert not missing, (
        f"{tool}: compose_plan produced CopyOps with missing src:\n" + "\n".join(missing)
    )


@pytest.mark.parametrize("tool", _TOOLS)
def test_hooks_scripts_referenced_by_hooks_json_get_wired(tool: str, tmp_path: Path):
    """Every script a hooks.json shells out to must land in the composed install.

    Regression guard: compose once wired only hooks.json (the file), stripping the
    sibling scripts it invokes via ${CLAUDE_PLUGIN_ROOT}/hooks/<script>. Tools that
    declare no hooks_json are a vacuous pass.
    """
    manifest = load_manifest(_manifest_path(tool))
    if manifest.hooks_json is None:
        pytest.skip(f"{tool} declares no hooks_json")

    hooks_json_src = manifest.plugin_root / manifest.hooks_json
    referenced = sorted(set(_HOOK_SCRIPT_RE.findall(hooks_json_src.read_text())))
    assert referenced, (
        f"{tool}: hooks.json references no ${{CLAUDE_PLUGIN_ROOT}}/hooks/ scripts — "
        "either the regex drifted or hooks.json changed shape"
    )

    dest = tmp_path / tool
    plan = compose_plan(manifest, {}, {}, dest)  # always-on set only
    apply_plan(plan)

    missing = [rel for rel in referenced if not (dest / rel).exists()]
    assert not missing, (
        f"{tool}: hooks.json invokes these scripts but compose did not wire them "
        f"into the install: {missing}. Ensure the hooks directory ships, not just "
        f"hooks.json."
    )
