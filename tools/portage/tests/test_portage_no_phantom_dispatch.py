"""Every subagent portage's docs dispatch resolves to one a tool actually ships.

A dispatch instruction naming an agent nobody installs reads fine and resolves
to nothing at runtime — a silent failure. The names are extracted from the docs
and looked up through ``load_manifest``, the same discovery the harness uses, so
the check is against what is really installed rather than a denylist of the
phantoms someone happened to notice.

Portage's docs legitimately dispatch craft's agents (green-driver hands CI
triage to ``code-reviewer`` / ``log-sifter``), so the allowlist spans every
tool's manifest, not just portage's — which is the same sibling-plugin lookup
``monitor.md`` tells the agent to perform before dispatching.
"""

from __future__ import annotations

import re
from pathlib import Path

from trailhead.capabilities import load_manifest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOLS_DIR = _REPO_ROOT / "tools"
_PORTAGE_PLUGIN_ROOT = _REPO_ROOT / "tools" / "portage" / "plugins" / "portage"

# The two ways a portage doc names a dispatch target: the structured
# `subagent_type:` field of an Agent() call, and prose ("dispatch `updater`").
_DISPATCH_PATTERNS = (
    re.compile(r'subagent_type:\s*"([A-Za-z0-9_-]+)"'),
    re.compile(r"[Dd]ispatch(?:es|ing|ed)?\s+`([a-z][a-z0-9-]*)`"),
)


def _installed_subagents() -> dict[str, str]:
    """Every subagent every tool declares, name → the tool that ships it."""
    installed: dict[str, str] = {}
    for manifest in sorted(_TOOLS_DIR.glob("*/capabilities.toml")):
        for name in load_manifest(manifest).subagents:
            installed[name] = manifest.parent.name
    return installed


def _dispatched_names() -> dict[str, set[str]]:
    """Every agent name portage's shipped docs dispatch, name → doc filenames."""
    docs = [
        *(_PORTAGE_PLUGIN_ROOT / "agents").rglob("*.md"),
        *(_PORTAGE_PLUGIN_ROOT / "skills").rglob("*.md"),
    ]
    dispatched: dict[str, set[str]] = {}
    for doc in docs:
        text = doc.read_text()
        for pattern in _DISPATCH_PATTERNS:
            for name in pattern.findall(text):
                dispatched.setdefault(name, set()).add(doc.name)
    return dispatched


def test_every_dispatched_agent_resolves_to_an_installed_subagent():
    installed = _installed_subagents()
    dispatched = _dispatched_names()
    phantom = {
        name: sorted(docs)
        for name, docs in dispatched.items()
        if name not in installed
    }
    assert phantom == {}, (
        f"these portage docs dispatch agents no tool installs: {phantom} "
        f"(installed: {sorted(installed)})"
    )
