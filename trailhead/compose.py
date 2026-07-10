"""Install-composition seam for trailhead tool packages.

Contract
--------
A ``trailhead install`` composes a plugin's *selected subagents and skills* (plus
its always-on set) into a harness plugin path by copying the chosen sources into a
destination plugin dir.  Selection is by NAME (resolved against the plugin
inventory in :mod:`trailhead.capabilities`), with optional per-entry override
``file_path`` values.

Architecture: pure planner + separate applier
---------------------------------------------
* ``compose_plan`` — PURE (it stats override paths to decide file-vs-dir and to
  resolve paths, but writes nothing).  Given a parsed
  :class:`~trailhead.capabilities.Manifest`, the selected subagent / skill maps,
  and a destination, it resolves the union of sources to wire and returns a
  :class:`Plan`.
* ``apply_plan`` — the ONLY function that writes.  Executes the :class:`CopyOp`
  list (dir copies via ``copytree``; file copies via ``copy2``).

Always-on set
-------------
Every composed plugin automatically includes:

1. ``.claude-plugin/`` — plugin identity (the dest is a structurally valid plugin
   only when this dir, containing ``plugin.json``, is present).
2. Every ``base`` directory declared in the manifest.
3. The directory containing ``hooks_json`` (if declared) so the hooks' sibling
   scripts ship alongside the JSON.

Selection
---------
``subagents`` / ``skills`` map ``name -> override_path | None``:

* ``None`` — an in-repo entry; resolve via the inventory (``manifest.subagents`` /
  ``manifest.skills``).  A name not in the inventory raises
  :class:`UnknownSubagentError` / :class:`UnknownSkillError`.  Src is confined to
  the plugin root.
* an override path — copy that file/dir instead of the in-repo one.  Src
  confinement is **skipped** (the override deliberately points outside the repo);
  the override must exist.  A file override of a skill lands at
  ``skills/<name>/SKILL.md``; a dir override copies the whole tree to
  ``skills/<name>/``.  A subagent override (always a file) lands at
  ``agents/<name>.md``.

Dest confinement is ALWAYS enforced — a composed entry can never escape ``dest``.

De-dup vs collision
-------------------
* **Benign overlap** — same ``src`` → same ``dest`` → de-duplicated.
* **Genuine collision** — two *different* ``src`` paths → the *same* ``dest`` →
  :class:`CollisionError` in the pure planning phase, before any write.

Symlinks
--------
``apply_plan`` uses ``shutil.copytree(..., symlinks=False)`` so symlinks inside a
source tree are never preserved as escaping links — their targets' contents are
copied instead.
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from trailhead.capabilities import ConfineError, Manifest


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnknownSelectionError(Exception):
    """Base error for a selected name not present in the plugin inventory."""


class UnknownSubagentError(UnknownSelectionError):
    """Raised when a requested subagent name is not declared by the plugin."""

    def __init__(self, name: str, tool: str):
        self.name = name
        self.tool = tool
        super().__init__(f"unknown subagent {name!r} for tool {tool!r}")


class UnknownSkillError(UnknownSelectionError):
    """Raised when a requested skill name is not declared by the plugin."""

    def __init__(self, name: str, tool: str):
        self.name = name
        self.tool = tool
        super().__init__(f"unknown skill {name!r} for tool {tool!r}")


class OverrideError(Exception):
    """Raised when an override file_path does not exist."""

    def __init__(self, name: str, kind: str, path: Path):
        self.name = name
        self.kind = kind
        self.path = path
        super().__init__(f"override for {kind} {name!r} does not exist: {path}")


@dataclass
class CollisionError(Exception):
    """Raised when two different source paths map to the same dest path.

    Raised in the pure planning phase — before any write occurs.
    """

    dest: Path
    src_a: Path
    src_b: Path

    def __str__(self) -> str:
        return (
            f"collision: two different sources map to {self.dest!r}\n"
            f"  src_a={self.src_a!r}\n"
            f"  src_b={self.src_b!r}"
        )


class DestConfinementError(Exception):
    """Raised when a dest path would escape the target plugin dir."""

    def __init__(self, dest_root: Path, path: Path):
        self.dest_root = dest_root
        self.path = path
        super().__init__(f"dest path {path!r} escapes the target plugin dir {dest_root!r}")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CopyOp:
    """A single (src, dest) directory or file copy operation."""

    src: Path
    dest: Path


@dataclass
class Plan:
    """An ordered, de-duplicated list of copy operations."""

    ops: list[CopyOp] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _detect_collisions(ops: list[CopyOp]) -> None:
    """Raise CollisionError if two ops share a dest with different srcs."""
    seen: dict[Path, Path] = {}
    for op in ops:
        if op.dest in seen:
            if seen[op.dest] != op.src:
                raise CollisionError(dest=op.dest, src_a=seen[op.dest], src_b=op.src)
        else:
            seen[op.dest] = op.src


def _dedup_ops(ops: list[CopyOp]) -> list[CopyOp]:
    """Remove duplicate (src, dest) pairs, preserving order."""
    seen: set[tuple[Path, Path]] = set()
    result: list[CopyOp] = []
    for op in ops:
        key = (op.src, op.dest)
        if key not in seen:
            seen.add(key)
            result.append(op)
    return result


def _confine_dest(dest_root: Path, candidate: Path) -> None:
    """Assert candidate stays under dest_root.resolve()."""
    resolved_root = dest_root.resolve()
    resolved_candidate = candidate.resolve()
    if not resolved_candidate.is_relative_to(resolved_root):
        raise DestConfinementError(dest_root, candidate)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compose_plan(
    manifest: Manifest,
    subagents: dict[str, str | None] | None,
    skills: dict[str, str | None] | None,
    dest: Path,
) -> Plan:
    """Build a composition plan — pure (stats override paths, writes nothing).

    Args:
        manifest:  Parsed :class:`~trailhead.capabilities.Manifest`.
        subagents: ``{name: override_path | None}`` subagents to wire.
        skills:    ``{name: override_path | None}`` skills to wire.
        dest:      Target plugin directory (pure call does not create it).

    Returns:
        A :class:`Plan` with de-duplicated, collision-checked ops.

    Raises:
        UnknownSubagentError / UnknownSkillError: A non-override name is not in
            the inventory.
        OverrideError:        An override path does not exist.
        CollisionError:       Two different src paths map to the same dest.
        ConfineError:         An in-repo src path escapes the plugin root.
        DestConfinementError: A dest path would escape ``dest``.
    """
    subagents = subagents or {}
    skills = skills or {}

    plugin_root: Path = manifest.plugin_root.resolve()
    raw_ops: list[CopyOp] = []

    def _add_in_repo_dir(rel: str) -> None:
        src = (manifest.plugin_root / rel).resolve()
        if not src.is_relative_to(plugin_root):
            raise ConfineError(manifest.tool_name, "compose", rel)
        d = dest / rel
        _confine_dest(dest, d)
        raw_ops.append(CopyOp(src=src, dest=d))

    def _add_in_repo_file(rel: str) -> None:
        src = (manifest.plugin_root / rel).resolve()
        if not src.is_relative_to(plugin_root):
            raise ConfineError(manifest.tool_name, "compose", rel)
        d = dest / rel
        _confine_dest(dest, d)
        raw_ops.append(CopyOp(src=src, dest=d))

    def _add_override(src_path: Path, dest_rel: str, name: str, kind: str) -> None:
        # Override deliberately points outside the plugin root — skip src
        # confinement, but the override must exist (file or dir).
        src = src_path.resolve()
        if not src.exists():
            raise OverrideError(name, kind, src_path)
        d = dest / dest_rel
        _confine_dest(dest, d)
        raw_ops.append(CopyOp(src=src, dest=d))

    # ------------------------------------------------------------------
    # Always-on set
    # ------------------------------------------------------------------
    _add_in_repo_dir(".claude-plugin")
    for base_dir in manifest.base:
        _add_in_repo_dir(base_dir)
    if manifest.hooks_json is not None:
        # hooks.json shells out to sibling scripts via ${CLAUDE_PLUGIN_ROOT}/hooks/;
        # wire the whole containing dir so the scripts ship too (bare file fallback
        # when hooks_json sits at the plugin root with no dedicated dir).
        hooks_dir = str(Path(manifest.hooks_json).parent)
        if hooks_dir != ".":
            _add_in_repo_dir(hooks_dir)
        else:
            _add_in_repo_file(manifest.hooks_json)

    # ------------------------------------------------------------------
    # Selected subagents (always a single .md file)
    # ------------------------------------------------------------------
    for name, override in subagents.items():
        if override is not None:
            _add_override(Path(override), f"agents/{name}.md", name, "subagent")
        else:
            rel = manifest.subagents.get(name)
            if rel is None:
                raise UnknownSubagentError(name, manifest.tool_name)
            _add_in_repo_file(rel)

    # ------------------------------------------------------------------
    # Selected skills (in-repo = whole dir; override file = SKILL.md, dir = tree)
    # ------------------------------------------------------------------
    for name, override in skills.items():
        if override is not None:
            override_path = Path(override)
            resolved = override_path.resolve()
            if not resolved.exists():
                raise OverrideError(name, "skill", override_path)
            if resolved.is_dir():
                _add_override(override_path, f"skills/{name}", name, "skill")
            else:
                _add_override(override_path, f"skills/{name}/SKILL.md", name, "skill")
        else:
            rel = manifest.skills.get(name)
            if rel is None:
                raise UnknownSkillError(name, manifest.tool_name)
            _add_in_repo_dir(rel)

    deduped = _dedup_ops(raw_ops)
    _detect_collisions(deduped)
    return Plan(ops=deduped)


def apply_plan(plan: Plan) -> None:
    """Execute a :class:`Plan`, writing files to disk.

    This is the ONLY function in this module that writes to the filesystem.
    Always copies (``shutil.copytree`` / ``shutil.copy2`` with ``symlinks=False``)
    — composed trees never carry symlinks.

    Args:
        plan: The composition plan from :func:`compose_plan`.
    """
    for op in plan.ops:
        op.dest.parent.mkdir(parents=True, exist_ok=True)
        if op.src.is_dir():
            # Skip Python build cruft so a stray __pycache__/*.pyc never ships.
            shutil.copytree(
                op.src,
                op.dest,
                symlinks=False,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            shutil.copy2(op.src, op.dest)
