"""Config resolution for ``trailhead install`` / ``trailhead uninstall``.

Both commands are driven under the covers by a resolved configuration: which
harnesses to target, which agent-plugins (and which subagents/skills, with
optional file_path overrides) to compose into each, and whether to install the
camp/lore CLIs onto PATH.  The config comes from a TOML file (default
``config/default.toml``, or ``--config``); CLI flags override it at runtime.

This module is PURE with respect to harness state — it reads the config TOML and
the plugin inventories (``capabilities.toml``) to resolve names, but it performs
no harness calls and writes nothing.  Harness detection is injected as a list of
names (``detected_harnesses``) so resolution stays deterministic and testable.

Config TOML schema
------------------
    install_camp_cli = true            # default true
    install_lore_cli = true            # default true
    plugins = ["camp", "lore", ...]    # top-level default plugin set (optional)

    [[harness]]                        # optional per-harness override
    name = "claude_code"
    plugins = ["camp", "lore", "craft", "portage"]

    [[harness]]
    name = "codex"
        [[harness.plugins]]            # map form — per-plugin subagent/skill subset
        name = "craft"
        subagents = ["advocate", "artist"]
        skills = ["execute"]
            # override form (file_path points at a custom md file OR a skill dir):
            # [[harness.plugins.subagents]]
            # name = "updater"
            # file_path = "/abs/path/custom.md"

Expansion rules
---------------
* A plugin given as a STRING expands to ALL of its selectable subagents + skills.
* A plugin MAP with a missing ``subagents`` / ``skills`` key means ALL of that
  kind.  A present key is a list whose items are either a bare name (no override)
  or a ``{name, file_path}`` map (override).
* You cannot mix string and map plugin forms in one ``plugins`` list (TOML), but
  this resolver tolerates either per item.

Resolution order
----------------
* Harness set: ``--harness`` → else config ``[[harness]]`` names → else detected.
* Per-harness plugin set: ``--plugin`` (REPLACES) → else that harness's
  ``[[harness]].plugins`` → else the top-level ``plugins`` default → else ALL.
* ``--no-camp`` / ``--no-lore`` force the corresponding CLI flag off.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from trailhead.capabilities import Manifest, load_manifest
from trailhead.compose import UnknownSkillError, UnknownSubagentError
from trailhead.harness import canonical_name, known_harness_names
from trailhead.wire import default_manifest_paths

# Canonical display/install order for the agent-plugins.
KNOWN_PLUGIN_ORDER = ["camp", "lore", "craft", "portage"]


class ConfigResolveError(Exception):
    """Raised for malformed config, unknown harness, or unknown plugin."""


# ---------------------------------------------------------------------------
# Resolved data structures
# ---------------------------------------------------------------------------


@dataclass
class ResolvedPlugin:
    """A plugin resolved to concrete subagent/skill selections (+ overrides)."""

    name: str
    subagents: dict[str, str | None] = field(default_factory=dict)
    skills: dict[str, str | None] = field(default_factory=dict)


@dataclass
class ResolvedHarness:
    """A harness resolved to its concrete plugin selections."""

    name: str
    plugins: list[ResolvedPlugin] = field(default_factory=list)

    def selection(self) -> dict[str, tuple[dict[str, str | None], dict[str, str | None]]]:
        """Return the ``wire()`` selection mapping for this harness."""
        return {p.name: (p.subagents, p.skills) for p in self.plugins}


@dataclass
class ResolvedConfig:
    """Fully resolved install/uninstall configuration."""

    install_camp_cli: bool
    install_lore_cli: bool
    harnesses: list[ResolvedHarness] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Config-path resolution
# ---------------------------------------------------------------------------


def resolve_config_path(config_arg: str | None, repo_root: Path) -> Path:
    """Resolve the --config argument.

    Absolute paths are respected; a relative path resolves under the repo's
    ``config/`` directory; ``None`` defaults to ``config/default.toml``.
    """
    config_dir = repo_root / "config"
    if config_arg is None:
        return config_dir / "default.toml"
    p = Path(config_arg)
    return p if p.is_absolute() else config_dir / config_arg


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_toml(config_path: Path) -> dict:
    if not config_path.exists():
        raise ConfigResolveError(f"config file not found: {config_path}")
    try:
        with open(config_path, "rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigResolveError(f"malformed TOML in {config_path}: {exc}") from exc


def _load_inventory(name: str, manifest_paths: dict[str, Path]) -> Manifest:
    path = manifest_paths.get(name)
    if path is None:
        raise ConfigResolveError(
            f"unknown plugin {name!r}; known plugins: {sorted(manifest_paths)}"
        )
    return load_manifest(path)


def _expand_selection(
    spec: dict,
    key: str,
    inventory: dict[str, str],
    plugin: str,
    *,
    kind: str,
) -> dict[str, str | None]:
    """Expand a subagents/skills selection. Missing key => ALL of that kind."""
    if key not in spec:
        return {n: None for n in inventory}

    out: dict[str, str | None] = {}
    for item in spec[key]:
        if isinstance(item, str):
            name, override = item, None
        elif isinstance(item, dict):
            name = item.get("name")
            override = item.get("file_path")
            if not name:
                raise ConfigResolveError(f"{plugin}: a {kind} entry is missing 'name'")
        else:
            raise ConfigResolveError(f"{plugin}: invalid {kind} entry {item!r}")
        if name not in inventory:
            if kind == "subagent":
                raise UnknownSubagentError(name, plugin)
            raise UnknownSkillError(name, plugin)
        out[name] = override
    return out


def _expand_plugin(spec, manifest_paths: dict[str, Path]) -> ResolvedPlugin:
    if isinstance(spec, str):
        inv = _load_inventory(spec, manifest_paths)
        return ResolvedPlugin(
            spec,
            {n: None for n in inv.subagents},
            {n: None for n in inv.skills},
        )
    if isinstance(spec, dict):
        name = spec.get("name")
        if not name:
            raise ConfigResolveError("a plugin entry is missing 'name'")
        inv = _load_inventory(name, manifest_paths)
        subagents = _expand_selection(spec, "subagents", inv.subagents, name, kind="subagent")
        skills = _expand_selection(spec, "skills", inv.skills, name, kind="skill")
        return ResolvedPlugin(name, subagents, skills)
    raise ConfigResolveError(f"invalid plugin entry {spec!r}")


def _dedupe(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _find_harness_block(blocks: list[dict], canonical: str) -> dict | None:
    for b in blocks:
        if canonical_name(b.get("name", "")) == canonical:
            return b
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_config(
    *,
    config_path: Path | None = None,
    cli_harnesses: list[str] | None = None,
    cli_plugins: list[str] | None = None,
    no_camp: bool = False,
    no_lore: bool = False,
    detected_harnesses: list[str] | None = None,
    manifest_paths: dict[str, Path] | None = None,
) -> ResolvedConfig:
    """Resolve the effective install/uninstall config.

    Args:
        config_path:        Path to a config TOML, or None for a config-less
                            (CLI/detection-only) resolution.
        cli_harnesses:      ``--harness`` values (override config + detection).
        cli_plugins:        ``--plugin`` values (REPLACE the per-harness plugin set).
        no_camp / no_lore:  Force the camp/lore CLI install flag off.
        detected_harnesses: Harness names detected on the machine (fallback when
                            neither CLI nor config name a harness).
        manifest_paths:     Plugin inventory paths (defaults to the repo manifests).

    Raises:
        ConfigResolveError:    Malformed config, unknown harness, or unknown plugin.
        UnknownSubagentError / UnknownSkillError: A named subagent/skill is not in
            the plugin's inventory.
    """
    _manifest_paths = manifest_paths or default_manifest_paths()
    data = _parse_toml(config_path) if config_path is not None else {}

    install_camp_cli = bool(data.get("install_camp_cli", True))
    install_lore_cli = bool(data.get("install_lore_cli", True))
    if no_camp:
        install_camp_cli = False
    if no_lore:
        install_lore_cli = False

    harness_blocks = data.get("harness", [])
    if not isinstance(harness_blocks, list):
        raise ConfigResolveError("[[harness]] must be an array of tables")

    # ------------------------------------------------------------------
    # Resolve the harness set
    # ------------------------------------------------------------------
    if cli_harnesses:
        names = [canonical_name(n) for n in cli_harnesses]
    elif harness_blocks:
        names = [canonical_name(b.get("name", "")) for b in harness_blocks]
    else:
        names = [canonical_name(n) for n in (detected_harnesses or [])]
    names = _dedupe(names)

    for n in names:
        if not n:
            raise ConfigResolveError("a [[harness]] entry is missing 'name'")
        if n not in known_harness_names():
            raise ConfigResolveError(
                f"unknown harness {n!r}; known harnesses: {known_harness_names()}"
            )

    # ------------------------------------------------------------------
    # Default plugin spec when none is specified anywhere
    # ------------------------------------------------------------------
    top_plugins = data.get("plugins")
    if top_plugins is None:
        default_plugins = [p for p in KNOWN_PLUGIN_ORDER if p in _manifest_paths]
    else:
        default_plugins = top_plugins

    # ------------------------------------------------------------------
    # Resolve plugins per harness
    # ------------------------------------------------------------------
    resolved_harnesses: list[ResolvedHarness] = []
    for hname in names:
        block = _find_harness_block(harness_blocks, hname)
        if cli_plugins:
            plugin_specs = list(cli_plugins)
        elif block is not None and "plugins" in block:
            plugin_specs = block["plugins"]
        else:
            plugin_specs = default_plugins

        plugins = [_expand_plugin(spec, _manifest_paths) for spec in plugin_specs]
        resolved_harnesses.append(ResolvedHarness(hname, plugins))

    return ResolvedConfig(
        install_camp_cli=install_camp_cli,
        install_lore_cli=install_lore_cli,
        harnesses=resolved_harnesses,
    )
