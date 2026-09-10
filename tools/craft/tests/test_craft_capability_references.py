"""Every craft capability a craft document names resolves through the real manifest loader.

craft's skills and agents refer to each other constantly: `_shared/execute.md`
dispatches eight subagents by name across its build loop, and the README
advertises the whole inventory as `/craft:<skill>` and `craft:<agent>`. A name
that no longer resolves reads perfectly well and dead-ends at the harness — the
dispatch finds no such subagent, the advertised skill cannot be invoked.

The allowlist is derived from :func:`trailhead.capabilities.load_manifest` —
the same loader the installer and the harness composition use — so a capability
stops being valid the moment the manifest stops registering it. There is no
hand-maintained roster of dispatched agents to keep in step with the prose, and
no directory glob standing in for what the loader actually registers.

The registrability floor (frontmatter that parses, `name:` matching the on-disk
stem) is not repeated here: `trailhead/tests/test_registrable.py` already
parametrizes it over every tool, craft included.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CRAFT_ROOT = Path(__file__).parent.parent
_MANIFEST = _CRAFT_ROOT / "capabilities.toml"
_PLUGIN = _CRAFT_ROOT / "plugins" / "craft"
_README = _CRAFT_ROOT / "README.md"

_MANIFEST_OBJ = load_manifest(_MANIFEST)
_SKILLS = frozenset(_MANIFEST_OBJ.skills)


def _installed_subagents() -> dict[str, str]:
    """Every subagent every tool declares, name -> the tool that ships it.

    craft's `drive` skill hands a finished change to portage's `updater` and
    `monitor`, which is the point of the skill — so a craft-only allowlist would
    report those correct dispatches as dead ends. The resolvable set is the union
    across the monorepo, and each dispatch is reported with the tool that owns it.
    """
    installed: dict[str, str] = {}
    for manifest in sorted((_REPO_ROOT / "tools").glob("*/capabilities.toml")):
        for name in load_manifest(manifest).subagents:
            installed[name] = manifest.parent.name
    return installed


_SUBAGENTS = _installed_subagents()
_CRAFT_SUBAGENTS = frozenset(_MANIFEST_OBJ.subagents)

# The two shapes craft prose uses to name a subagent it hands work to. Both are
# anchored on a backticked token so an ordinary sentence mentioning, say, the word
# "executor" is not swept in.
_DISPATCH_PATTERNS = (
    re.compile(r'subagent_type:\s*"([a-z][a-z0-9-]*)"'),
    re.compile(r"[Dd]ispatch(?:es|ing|ed)?\s+(?:a\s+second\s+)?`([a-z][a-z0-9-]*)`"),
)


def _prose_documents() -> list[Path]:
    """Every skill and agent document craft ships."""
    return sorted(
        [*(_PLUGIN / "skills").rglob("*.md"), *(_PLUGIN / "agents").glob("*.md")]
    )


def _dispatches() -> dict[str, set[str]]:
    """subagent name -> the craft documents that hand work to it."""
    found: dict[str, set[str]] = {}
    for document in _prose_documents():
        text = document.read_text(encoding="utf-8")
        for pattern in _DISPATCH_PATTERNS:
            for name in pattern.findall(text):
                found.setdefault(name, set()).add(str(document.relative_to(_PLUGIN)))
    return found


@pytest.mark.parametrize("name", sorted(_dispatches()), ids=lambda n: n)
def test_every_dispatched_subagent_resolves_to_an_installed_capability(name: str):
    """A dispatch naming a subagent the manifest does not register dead-ends at the
    harness: the agent asks for a `subagent_type` that resolves to nothing."""
    assert name in _SUBAGENTS, (
        f"{sorted(_dispatches()[name])} dispatch `{name}`, which no tool's manifest registers "
        f"as a subagent. Installed: {sorted(_SUBAGENTS)}"
    )


# ---------------------------------------------------------------------------
# The README advertises the inventory; both directions must resolve
# ---------------------------------------------------------------------------

_SKILL_TOKEN = re.compile(r"/craft:([a-z][a-z0-9-]*)")
_AGENT_TOKEN = re.compile(r"(?<!/)\bcraft:([a-z][a-z0-9-]*)")


def _readme() -> str:
    return _README.read_text(encoding="utf-8")


def test_every_capability_the_readme_advertises_is_one_the_manifest_registers():
    """The other direction: a README that outlives a removed skill or agent sends a
    reader to a capability the loader will not produce."""
    text = _readme()
    phantom_skills = sorted(set(_SKILL_TOKEN.findall(text)) - _SKILLS)
    phantom_agents = sorted(set(_AGENT_TOKEN.findall(text)) - _CRAFT_SUBAGENTS)
    assert not phantom_skills, f"README advertises unregistered skills: {phantom_skills}"
    assert not phantom_agents, f"README advertises unregistered subagents: {phantom_agents}"
