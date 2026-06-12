"""Preset → capability mapping for trailhead.

Preset table (spec §832-838):

  minimal  = lore{capture, recall, sessions}
  standard = minimal + camp{} (base only) + forge{planning, execute, review, helpers}
  full     = every capability declared in each tool's capabilities.toml (computed at
             runtime from load_manifest — cannot drift from the manifests, D-2)

An empty capability set (set()) means "wire the tool's base dirs only, no named caps."
"""

from pathlib import Path

from trailhead.capabilities import load_manifest

_REPO_ROOT = Path(__file__).parent.parent

_MINIMAL: dict[str, set[str]] = {
    "lore": {"capture", "recall", "sessions"},
}

_STANDARD: dict[str, set[str]] = {
    "lore": {"capture", "recall", "sessions"},
    "camp": set(),
    "forge": {"planning", "execute", "review", "helpers"},
}

_STATIC_PRESETS = {
    "minimal": _MINIMAL,
    "standard": _STANDARD,
}


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class PresetError(Exception):
    """Raised when an unknown preset name is requested.

    The message lists the valid preset names.
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve(name: str) -> dict[str, set[str]]:
    """Map a preset name to {tool: set[capability]}.

    For "full", the mapping is computed at runtime from the loaded manifests
    so it can't drift as tools add capabilities (D-2).

    Args:
        name: Preset name ("minimal", "standard", or "full").

    Returns:
        Dict mapping tool name → set of capability names to wire.
        An empty set means "base only, no named capabilities."

    Raises:
        PresetError: Unknown preset name. Message lists valid presets.
    """
    if name in _STATIC_PRESETS:
        return {tool: set(caps) for tool, caps in _STATIC_PRESETS[name].items()}
    if name == "full":
        return _compute_full()
    valid = ", ".join(sorted(_STATIC_PRESETS.keys()) + ["full"])
    raise PresetError(
        f"unknown preset {name!r}; valid presets are: {valid}"
    )


def _compute_full() -> dict[str, set[str]]:
    """Compute the full preset by loading all five tool manifests."""
    tools = {
        "lore": _REPO_ROOT / "tools" / "lore" / "capabilities.toml",
        "camp": _REPO_ROOT / "tools" / "camp" / "capabilities.toml",
        "forge": _REPO_ROOT / "tools" / "forge" / "capabilities.toml",
        "portage": _REPO_ROOT / "tools" / "portage" / "capabilities.toml",
        "landing": _REPO_ROOT / "tools" / "landing" / "capabilities.toml",
    }
    result: dict[str, set[str]] = {}
    for tool_name, manifest_path in tools.items():
        manifest = load_manifest(manifest_path)
        result[tool_name] = set(manifest.capabilities.keys())
    return result
