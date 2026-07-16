"""Plugin-inventory loader for trailhead tool packages.

Contract
--------
Each tool package ships a ``capabilities.toml`` at its root.  This module parses
that file and discovers the tool's *selectable inventory* — the subagents and
skills a config can choose by name — plus the *always-on set* (``base`` dirs and
an optional ``hooks_json``) that every install wires regardless of selection.

Schema (config-driven onboarding)
---------------------------------
The capability-GROUP concept (``[capabilities.<name>]`` tables) is gone.  Install
selection is now per subagent / per skill by NAME (see ``install_config``), so a
manifest only needs to declare what always ships::

    [tool]
    name = "lore"                       # MUST equal the plugins/<name>/ dir
    base = ["skills/_shared"]           # always-on, NON-selectable dirs
    hooks_json = "hooks/hooks.json"     # optional; the whole containing dir ships
    cli_bin = "bin/lore"                # optional; path to a shippable CLI binary
    validate = true                     # optional, default true

Convention-based inventory
---------------------------
The selectable inventory is discovered on disk, never hand-listed:

* **subagents** — every ``agents/<name>.md`` file → ``{name: "agents/<name>.md"}``.
* **skills** — every ``skills/<name>/`` directory that contains a ``SKILL.md``
  → ``{name: "skills/<name>"}``, MINUS any dir named in ``base``.

A skill dir without a ``SKILL.md`` (e.g. ``skills/_shared`` holding a shared
include) is therefore never selectable; list it in ``base`` so it still ships.
"ALL" for a plugin = ``set(subagents) | set(skills)``.

Confinement guarantee (D-F)
---------------------------
``base``, ``hooks_json``, and ``cli_bin`` entries are confined to the tool's plugin root
(``<manifest_dir>/plugins/<tool.name>/``) BEFORE any stat/existence call, using::

    candidate = (plugin_root.resolve() / entry).resolve()
    assert candidate.is_relative_to(plugin_root.resolve())

This defeats ``../`` traversal AND absolute-path injection (``Path("/a") / "/b"``
silently drops the left operand; the ``resolve()``-then-``is_relative_to()`` check
catches that).  Discovered subagents/skills are inherently confined — they are
globbed from inside the plugin root.

Type conventions
----------------
* ``base`` entries must resolve to **directories** (when ``validate``).
* ``hooks_json`` must resolve to a **file** (when ``validate``).
* ``cli_bin`` must resolve to a **file** (when ``validate``).
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ManifestError(Exception):
    """Raised for structural, missing-field, or validation errors in a manifest."""


class ConfineError(Exception):
    """Raised when a manifest entry escapes the tool's plugin root.

    Attributes:
        tool:    Tool name (or None if tool name could not be determined).
        context: ``"base"``, ``"hooks_json"``, or ``"cli_bin"``.
        entry:   The raw string entry that failed confinement.
    """

    def __init__(self, tool: str | None, context: str | None, entry: str):
        self.tool = tool
        self.context = context
        self.entry = entry
        super().__init__(
            f"path {entry!r} escapes the plugin root for tool {tool!r} (context: {context!r})"
        )


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------


@dataclass
class Manifest:
    """Parsed plugin inventory — the successor to the capability-group manifest.

    Fields:
        tool_name:  Plugin name (equals the ``plugins/<name>/`` dir).
        plugin_root: ``<manifest_dir>/plugins/<tool_name>``.
        base:        Always-on, non-selectable relative dirs (e.g. ``skills/_shared``).
        hooks_json:  Optional relative path to the hooks JSON; the whole containing
                     dir is wired by the composer so sibling scripts ship too.
        cli_bin:     Optional relative path to a shippable CLI binary.
        validate:    Whether existence/type checks ran (default True).
        subagents:   ``{name: "agents/<name>.md"}`` — discovered, selectable.
        skills:      ``{name: "skills/<name>"}`` — discovered (SKILL.md dirs minus base).
    """

    tool_name: str
    plugin_root: Path
    base: list[str]
    hooks_json: str | None
    cli_bin: str | None
    validate: bool
    subagents: dict[str, str]
    skills: dict[str, str]


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
    # NOTE: Path(abs_root) / "/absolute/entry" silently drops abs_root in Python;
    # resolve() on the result exposes the escape.
    candidate = (plugin_root / entry).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ConfineError(tool, context, entry)
    return candidate


def _validate_path(
    candidate: Path,
    entry: str,
    tool: str,
    context: str | None,
    *,
    must_be_dir: bool = False,
    must_be_file: bool = False,
) -> None:
    """Assert candidate exists and is the expected type.

    Raises ManifestError with tool/context/path context on failure.
    """
    ctx = f"tool={tool!r}, context={context!r}, path={entry!r}"
    if not candidate.exists():
        raise ManifestError(f"missing path — {ctx}")
    if must_be_dir and not candidate.is_dir():
        raise ManifestError(f"expected a directory but found a file — {ctx}")
    if must_be_file and not candidate.is_file():
        raise ManifestError(f"expected a file but found a directory — {ctx}")


def _discover_subagents(plugin_root: Path) -> dict[str, str]:
    """Discover ``agents/<name>.md`` files → ``{name: "agents/<name>.md"}``."""
    agents_dir = plugin_root / "agents"
    out: dict[str, str] = {}
    if agents_dir.is_dir():
        for p in sorted(agents_dir.glob("*.md")):
            if p.is_file():
                out[p.stem] = f"agents/{p.name}"
    return out


def _discover_skills(plugin_root: Path, base: list[str]) -> dict[str, str]:
    """Discover ``skills/<name>/`` dirs with a SKILL.md, minus ``base`` entries."""
    skills_dir = plugin_root / "skills"
    base_set = set(base)
    out: dict[str, str] = {}
    if skills_dir.is_dir():
        for d in sorted(skills_dir.iterdir()):
            if not d.is_dir():
                continue
            if not (d / "SKILL.md").is_file():
                continue  # not a selectable skill (e.g. skills/_shared)
            rel = f"skills/{d.name}"
            if rel in base_set:
                continue  # explicitly always-on; not separately selectable
            out[d.name] = rel
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_manifest(manifest_path: Path) -> Manifest:
    """Parse and (optionally) validate a ``capabilities.toml`` inventory.

    Steps:
    1. Parse TOML; wrap ``TOMLDecodeError`` as ``ManifestError``.
    2. Validate required ``[tool]`` fields.
    3. Derive ``plugin_root = manifest_path.parent / "plugins" / tool_name``.
    4. Confine ``base`` + ``hooks_json`` + ``cli_bin`` (D-F) before any stat call.
    5. If ``validate``, assert ``base`` dirs, ``hooks_json``, and ``cli_bin``
       exist with the right type.
    6. Discover the selectable subagent + skill inventory by convention.

    Raises:
        ManifestError: Structural/missing-field/validation failure.
        ConfineError:  A ``base`` / ``hooks_json`` / ``cli_bin`` path escapes the plugin root.
    """
    try:
        with open(manifest_path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"malformed TOML in {manifest_path}: {exc}") from exc

    # ------------------------------------------------------------------
    # [tool] block validation
    # ------------------------------------------------------------------
    tool_data = data.get("tool")
    if tool_data is None:
        raise ManifestError(f"manifest {manifest_path} is missing the required [tool] section")

    tool_name = tool_data.get("name")
    if not tool_name:
        raise ManifestError(f"manifest {manifest_path}: [tool] is missing required field 'name'")

    base: list[str] = list(tool_data.get("base", []))
    hooks_json: str | None = tool_data.get("hooks_json")
    cli_bin: str | None = tool_data.get("cli_bin")
    should_validate: bool = tool_data.get("validate", True)

    # ------------------------------------------------------------------
    # Plugin root + confinement (D-F) — BEFORE any stat/exist call
    # ------------------------------------------------------------------
    plugin_root = manifest_path.parent / "plugins" / tool_name

    for entry in base:
        _confine(plugin_root, entry, tool_name, "base")
    if hooks_json is not None:
        _confine(plugin_root, hooks_json, tool_name, "hooks_json")
    if cli_bin is not None:
        _confine(plugin_root, cli_bin, tool_name, "cli_bin")

    # ------------------------------------------------------------------
    # Existence + type validation (only when validate=true)
    # ------------------------------------------------------------------
    if should_validate:
        for entry in base:
            candidate = _confine(plugin_root, entry, tool_name, "base")
            _validate_path(candidate, entry, tool_name, "base", must_be_dir=True)
        if hooks_json is not None:
            candidate = _confine(plugin_root, hooks_json, tool_name, "hooks_json")
            _validate_path(candidate, hooks_json, tool_name, "hooks_json", must_be_file=True)
        if cli_bin is not None:
            candidate = _confine(plugin_root, cli_bin, tool_name, "cli_bin")
            _validate_path(candidate, cli_bin, tool_name, "cli_bin", must_be_file=True)

    # ------------------------------------------------------------------
    # Convention-based selectable inventory
    # ------------------------------------------------------------------
    subagents = _discover_subagents(plugin_root)
    skills = _discover_skills(plugin_root, base)

    return Manifest(
        tool_name=tool_name,
        plugin_root=plugin_root,
        base=base,
        hooks_json=hooks_json,
        cli_bin=cli_bin,
        validate=should_validate,
        subagents=subagents,
        skills=skills,
    )


def cli_bearing_manifests(manifest_paths: dict[str, Path]) -> dict[str, Manifest]:
    """Load the manifests of every CLI-bearing tool in *manifest_paths*.

    A tool is *CLI-bearing* when its ``capabilities.toml`` declares ``cli_bin``.
    This is the single predicate defining that set — shared by install-config
    resolution, shim building, and doctor's PATH report — so "which tools ship a
    CLI" is decided in one place rather than reimplemented per caller.

    Returns ``{name: Manifest}`` preserving *manifest_paths* iteration order.
    """
    return {
        name: manifest
        for name, path in manifest_paths.items()
        if (manifest := load_manifest(path)).cli_bin is not None
    }
