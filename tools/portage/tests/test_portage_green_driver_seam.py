"""The green-driver agent portage's config seam defaults to is really installed.

`monitor` dispatches whatever `[release].green_driver_agent` names, defaulting to
portage's own `green-driver`. That default is only usable if the agent is
discovered by the `agents/<name>.md` convention `trailhead.capabilities` reads,
so the manifest loader is what decides it — run it and ask.
"""

from __future__ import annotations

from pathlib import Path

from trailhead.capabilities import load_manifest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PORTAGE_MANIFEST = _REPO_ROOT / "tools" / "portage" / "capabilities.toml"


def test_green_driver_discovered_as_portage_subagent():
    manifest = load_manifest(_PORTAGE_MANIFEST)
    assert "green-driver" in manifest.subagents, (
        f"green-driver not discovered among portage subagents: {sorted(manifest.subagents)}"
    )
    assert manifest.subagents["green-driver"] == "agents/green-driver.md"
