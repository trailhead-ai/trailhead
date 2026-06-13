"""Coverage guard: every on-disk skill/agent must be wired by SOME manifest entry.

The bug this prevents
---------------------
`trailhead install` only wires what a tool's ``capabilities.toml`` references —
``base`` dirs plus the ``skills`` / ``agents`` of selected capabilities. A skill
dir or agent file that lands on disk but is named by NO capability (and is not in
``base``) is invisible to the installer: even ``--preset full`` skips it. That is
exactly how ``skills/_shared`` (craft's ``council.md``) went un-wired while the
``plan`` skill read it at runtime.

This test asserts the closure: for every tool, every ``skills/<dir>`` and every
``agents/<x>.md`` on disk is referenced by ``base`` or by at least one capability.
If you add a new skill/agent, you must also wire it — or this guard fails RED.

The inverse direction (referenced-but-missing) is already enforced by
``load_manifest(validate=True)``; we call it here too so a typo'd path fails loudly.
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
_TOOLS = ["lore", "camp", "craft", "portage", "landing"]


def _manifest_path(tool: str) -> Path:
    return _REPO_ROOT / "tools" / tool / "capabilities.toml"


def _plugin_root(tool: str) -> Path:
    return _REPO_ROOT / "tools" / tool / "plugins" / tool


def _referenced(tool: str) -> set[str]:
    """All skill/agent paths a manifest wires: base + every capability's skills/agents."""
    manifest = load_manifest(_manifest_path(tool))
    refs: set[str] = set(manifest.base)
    for cap in manifest.capabilities.values():
        refs.update(cap["skills"])
        refs.update(cap["agents"])
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
    """No orphans: every skill dir / agent file on disk is referenced by the manifest."""
    orphans = sorted(_on_disk(tool) - _referenced(tool))
    assert not orphans, (
        f"{tool}: these skills/agents exist on disk but no capability or base "
        f"references them, so `trailhead install` will never wire them: {orphans}. "
        f"Add each to base or a capability in tools/{tool}/capabilities.toml."
    )


@pytest.mark.parametrize("tool", _TOOLS)
def test_manifest_paths_all_exist_on_disk(tool: str):
    """No dangling references: load_manifest(validate=True) proves every wired path exists."""
    # Raises ManifestError if any referenced skill/agent/base/hooks path is missing.
    load_manifest(_manifest_path(tool))


@pytest.mark.parametrize("tool", _TOOLS)
def test_hooks_scripts_referenced_by_hooks_json_get_wired(tool: str, tmp_path: Path):
    """Every script a hooks.json shells out to must land in the composed install.

    Regression guard: compose once wired only hooks.json (the file), stripping the
    sibling scripts it invokes via ${CLAUDE_PLUGIN_ROOT}/hooks/<script>. The hooks
    then failed at runtime with FileNotFoundError. This composes the tool's
    always-on set and asserts each referenced script exists in the wired tree.

    Tools that declare no hooks_json are a vacuous pass.
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
    plan = compose_plan(manifest, set(), dest)  # always-on set only
    apply_plan(plan, mode="copy")

    missing = [rel for rel in referenced if not (dest / rel).exists()]
    assert not missing, (
        f"{tool}: hooks.json invokes these scripts but compose did not wire them "
        f"into the install: {missing}. Ensure the hooks directory ships, not just "
        f"hooks.json."
    )
