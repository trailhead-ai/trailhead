"""Capability-manifest loader for trailhead tool packages.

Contract
--------
Each tool package ships a ``capabilities.toml`` at its root.  This module
parses that file, validates structural rules, and — when ``validate`` is
true (the default) — confirms that every referenced path exists on disk
with the expected type.

Confinement guarantee (D-F)
---------------------------
Every path referenced in a manifest (``base`` entries, capability
``skills`` / ``agents`` entries, and ``hooks_json``) is confined to the
tool's plugin root::

    <manifest_dir>/plugins/<tool.name>/

The check is performed BEFORE any stat/existence call, for every entry,
using::

    candidate = (plugin_root.resolve() / entry).resolve()
    assert candidate.is_relative_to(plugin_root.resolve())

This defeats both ``../`` traversal AND absolute-path injection.  Python's
``Path("/a") / "/b"`` silently drops the left operand; the
``resolve()``-then-``is_relative_to()`` check catches that case.

Empty vs missing keys
---------------------
A capability MUST declare both ``skills`` and ``agents`` keys.  An explicit
``[]`` is valid (used for future/not-yet-built capabilities).  A capability
whose ``skills`` or ``agents`` key is absent entirely is flagged as a
``ManifestError`` — the loader must not silently accept a capability that
contributes nothing by accident.

Type conventions
----------------
* ``skills/<x>`` entries must resolve to **directories**.
* ``agents/<x>.md`` entries must resolve to **files**.
* ``hooks_json`` must resolve to a **file**.

Duplicate tables
----------------
``tomllib`` raises ``TOMLDecodeError`` on a duplicated
``[capabilities.<name>]`` table.  The loader catches this and re-raises as
``ManifestError`` citing the file path.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ManifestError(Exception):
    """Raised for structural, missing-field, or validation errors in a manifest."""


class ConfineError(Exception):
    """Raised when a manifest entry escapes the tool's plugin root.

    Attributes:
        tool:       Tool name (or None if tool name could not be determined).
        capability: Capability name, ``"base"``, or ``"hooks_json"``.
        entry:      The raw string entry that failed confinement.
    """

    def __init__(self, tool: str | None, capability: str | None, entry: str):
        self.tool = tool
        self.capability = capability
        self.entry = entry
        super().__init__(
            f"path {entry!r} escapes the plugin root for tool {tool!r} "
            f"(context: {capability!r})"
        )


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------


@dataclass
class Manifest:
    """Parsed and validated representation of a ``capabilities.toml``."""

    tool_name: str
    base: list[str]
    hooks_json: str | None
    validate: bool
    capabilities: dict[str, dict]  # name → {description, skills, agents}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _confine(
    plugin_root: Path,
    entry: str,
    tool: str | None,
    context: str,
) -> Path:
    """Resolve *entry* relative to *plugin_root* and assert it stays inside.

    Raises ConfineError if the resolved path escapes plugin_root.
    """
    resolved_root = plugin_root.resolve()
    # NOTE: Path(abs_root) / "/absolute/entry" silently drops abs_root in
    # Python.  resolved() on the result will expose the escape.
    candidate = (plugin_root / entry).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ConfineError(tool, context, entry)
    return candidate


def _validate_path(
    candidate: Path,
    entry: str,
    tool: str,
    capability: str | None,
    *,
    must_be_dir: bool = False,
    must_be_file: bool = False,
) -> None:
    """Assert candidate exists and is the expected type.

    Raises ManifestError with tool/capability/path context on failure.
    """
    ctx = f"tool={tool!r}, capability={capability!r}, path={entry!r}"
    if not candidate.exists():
        raise ManifestError(f"missing path — {ctx}")
    if must_be_dir and not candidate.is_dir():
        raise ManifestError(
            f"expected a directory but found a file — {ctx}"
        )
    if must_be_file and not candidate.is_file():
        raise ManifestError(
            f"expected a file but found a directory — {ctx}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_manifest(manifest_path: Path) -> Manifest:
    """Parse and (optionally) validate a ``capabilities.toml`` manifest.

    Steps:
    1. Parse TOML; wrap ``TOMLDecodeError`` as ``ManifestError``.
    2. Validate required ``[tool]`` fields.
    3. Derive ``plugin_root = manifest_path.parent / "plugins" / tool_name``.
    4. Confine every referenced path to plugin_root (D-F).
    5. If ``validate`` is true, assert existence + correct type for all paths.

    Args:
        manifest_path: Absolute (or resolvable) path to the ``capabilities.toml``.

    Returns:
        A :class:`Manifest` instance.

    Raises:
        ManifestError: Structural/missing-field/validation failure.
        ConfineError:  A referenced path escapes the plugin root.
    """
    try:
        with open(manifest_path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(
            f"malformed TOML in {manifest_path}: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # [tool] block validation
    # ------------------------------------------------------------------
    tool_data = data.get("tool")
    if tool_data is None:
        raise ManifestError(
            f"manifest {manifest_path} is missing the required [tool] section"
        )

    tool_name = tool_data.get("name")
    if not tool_name:
        raise ManifestError(
            f"manifest {manifest_path}: [tool] is missing required field 'name'"
        )

    base: list[str] = tool_data.get("base", [])
    hooks_json: str | None = tool_data.get("hooks_json")
    should_validate: bool = tool_data.get("validate", True)

    # ------------------------------------------------------------------
    # [capabilities.*] block validation (structure only, before path checks)
    # ------------------------------------------------------------------
    raw_caps: dict = data.get("capabilities", {})
    capabilities: dict[str, dict] = {}

    for cap_name, cap_data in raw_caps.items():
        if not isinstance(cap_data, dict):
            raise ManifestError(
                f"manifest {manifest_path}: capability {cap_name!r} must be a table"
            )
        description = cap_data.get("description")
        if not description:
            raise ManifestError(
                f"manifest {manifest_path}: capability {cap_name!r} is missing "
                "required field 'description'"
            )
        if "skills" not in cap_data:
            raise ManifestError(
                f"manifest {manifest_path}: capability {cap_name!r} is missing "
                "the 'skills' key — use skills = [] for a future/not-yet-built capability"
            )
        if "agents" not in cap_data:
            raise ManifestError(
                f"manifest {manifest_path}: capability {cap_name!r} is missing "
                "the 'agents' key — use agents = [] for a future/not-yet-built capability"
            )
        capabilities[cap_name] = {
            "description": description,
            "skills": list(cap_data["skills"]),
            "agents": list(cap_data["agents"]),
        }

    # ------------------------------------------------------------------
    # Plugin root + confinement (D-F) — BEFORE any stat/exist call
    # ------------------------------------------------------------------
    plugin_root = manifest_path.parent / "plugins" / tool_name

    # Confine base entries
    for entry in base:
        _confine(plugin_root, entry, tool_name, "base")

    # Confine hooks_json
    if hooks_json is not None:
        _confine(plugin_root, hooks_json, tool_name, "hooks_json")

    # Confine capability skills and agents
    for cap_name, cap in capabilities.items():
        for skill in cap["skills"]:
            _confine(plugin_root, skill, tool_name, cap_name)
        for agent in cap["agents"]:
            _confine(plugin_root, agent, tool_name, cap_name)

    # ------------------------------------------------------------------
    # Path existence + type validation (only when validate=true)
    # ------------------------------------------------------------------
    if should_validate:
        # Validate base dirs
        for entry in base:
            candidate = _confine(plugin_root, entry, tool_name, "base")
            _validate_path(
                candidate, entry, tool_name, "base", must_be_dir=True
            )

        # Validate hooks_json
        if hooks_json is not None:
            candidate = _confine(plugin_root, hooks_json, tool_name, "hooks_json")
            _validate_path(
                candidate, hooks_json, tool_name, "hooks_json", must_be_file=True
            )

        # Validate capability paths
        for cap_name, cap in capabilities.items():
            for skill in cap["skills"]:
                candidate = _confine(plugin_root, skill, tool_name, cap_name)
                _validate_path(
                    candidate, skill, tool_name, cap_name, must_be_dir=True
                )
            for agent in cap["agents"]:
                candidate = _confine(plugin_root, agent, tool_name, cap_name)
                _validate_path(
                    candidate, agent, tool_name, cap_name, must_be_file=True
                )

    return Manifest(
        tool_name=tool_name,
        base=base,
        hooks_json=hooks_json,
        validate=should_validate,
        capabilities=capabilities,
    )
