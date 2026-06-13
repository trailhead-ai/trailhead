"""Install-composition seam for trailhead tool packages.

Contract
--------
A ``trailhead install`` composes a tool's selected capabilities into a
harness plugin path by *directory selection* — copying chosen capability
directories into a destination plugin dir.  This module pins that contract.

Architecture: pure planner + separate applier
---------------------------------------------
* ``compose_plan`` — PURE, touches NO filesystem.  Given a parsed
  :class:`~trailhead.capabilities.Manifest`, a set of selected capability
  names, and a destination :class:`~pathlib.Path`, it resolves the union of
  source directories to wire and returns a :class:`Plan`.

* ``apply_plan`` — the ONLY function that writes.  Executes the
  :class:`CopyOp` list from a :class:`Plan`.

Always-on set
-------------
Every composed plugin automatically includes:

1. ``.claude-plugin/`` — plugin identity.  A composed dest is a
   structurally valid plugin only when this directory (containing
   ``plugin.json``) is present.
2. Every ``base`` directory declared in the manifest.
3. The ``hooks_json`` file declared in the manifest (if any).

Union-of-selected rule
----------------------
For each name in ``selected``, the capability's ``skills`` entries are
added as directory CopyOps, and ``agents`` entries are added as file
CopyOps under the dest plugin's ``agents/`` directory.

De-dup vs collision
-------------------
* **Benign overlap** — same ``src`` → same ``dest`` (e.g. a base dir also
  listed by a capability) → de-duplicated to a single :class:`CopyOp`.
  No error.
* **Genuine collision** — two *different* ``src`` paths resolve to the
  *same* ``dest`` path → :class:`CollisionError` raised in the pure
  planning phase, before any write.

D-F dual-end confinement
------------------------
* Every ``src`` must stay under the tool's ``plugin_root.resolve()``.
  (Enforced upstream by the Slice 3 loader; compose confirms by reusing the
  same resolved root comparison.)
* Every ``dest`` must stay under ``dest.resolve()``.  A manifest entry must
  never write outside the destination plugin dir.

Symlinks
--------
``apply_plan(plan, mode="copy")`` uses ``shutil.copytree(..., symlinks=False)``
so that symlinks inside a source tree are *never* preserved as escaping
links — their targets' contents are copied instead.

What is NOT here (installer layer)
------------------------------------
* Preset → capability-name mapping (``--preset minimal``).
* Installer UX and ``trailhead config`` sub-command.
* Multi-tool orchestration and marketplace registration (``wire.py``, ``registry.py``).
* Live harness-launch validation (U3 proven structurally; live load deferred).

U3 resolution
-------------
Structural validity is proven by: ``dest/.claude-plugin/plugin.json`` exists
and parses as JSON after ``apply_plan``, and the selected skill dirs exist
with content.  Live harness-load validation is deferred to Step 5 where the
installer exists.
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from trailhead.capabilities import ConfineError, Manifest


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class UnknownCapabilityError(Exception):
    """Raised when a requested capability name is not declared in the manifest."""

    def __init__(self, name: str, tool: str):
        self.name = name
        self.tool = tool
        super().__init__(
            f"unknown capability {name!r} for tool {tool!r}"
        )


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
        super().__init__(
            f"dest path {path!r} escapes the target plugin dir {dest_root!r}"
        )


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
    """Detect genuine collisions in an op list.

    A genuine collision: two ops with the *same* dest but *different* srcs.
    Benign overlaps (same src AND same dest) are fine and ignored here.

    Raises:
        CollisionError: First detected collision (dest, src_a, src_b).
    """
    seen: dict[Path, Path] = {}
    for op in ops:
        if op.dest in seen:
            if seen[op.dest] != op.src:
                raise CollisionError(
                    dest=op.dest,
                    src_a=seen[op.dest],
                    src_b=op.src,
                )
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
    """Assert candidate stays under dest_root.resolve().

    Raises:
        DestConfinementError: If candidate escapes dest_root.
    """
    resolved_root = dest_root.resolve()
    resolved_candidate = candidate.resolve()
    if not resolved_candidate.is_relative_to(resolved_root):
        raise DestConfinementError(dest_root, candidate)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compose_plan(manifest: Manifest, selected: set[str] | list[str], dest: Path) -> Plan:
    """Build a composition plan — pure, touches NO filesystem.

    Resolves the union of source directories to wire and returns a
    :class:`Plan` of :class:`CopyOp` objects.  The always-on set
    (``.claude-plugin/``, base dirs, and the hooks directory containing
    ``hooks_json``) is included regardless of ``selected``.

    Args:
        manifest:  Parsed :class:`~trailhead.capabilities.Manifest`.
        selected:  Capability names to wire beyond the always-on set.
        dest:      Target plugin directory (must not yet exist; pure call
                   does not create it).

    Returns:
        A :class:`Plan` with de-duplicated, collision-checked ops.

    Raises:
        UnknownCapabilityError: A name in ``selected`` is not in manifest.
        CollisionError:         Two different src paths map to the same dest.
        ConfineError:           A src path escapes the plugin root (should
                                have been caught by loader, but re-asserted).
        DestConfinementError:   A dest path would escape ``dest``.
    """
    selected_names = set(selected)

    # Validate all requested capability names up-front
    for name in selected_names:
        if name not in manifest.capabilities:
            raise UnknownCapabilityError(name, manifest.tool_name)

    plugin_root: Path = manifest.plugin_root.resolve()
    dest_resolved = dest.resolve()

    raw_ops: list[CopyOp] = []

    def _add_dir(rel: str) -> None:
        """Add a src→dest CopyOp for a relative directory entry."""
        src = (manifest.plugin_root / rel).resolve()
        # Re-assert src confinement (loader already checked, but belt-and-suspenders)
        if not src.is_relative_to(plugin_root):
            raise ConfineError(manifest.tool_name, "compose", rel)
        d = dest / rel
        # Assert dest confinement
        _confine_dest(dest, d)
        raw_ops.append(CopyOp(src=src, dest=d))

    def _add_file(rel: str) -> None:
        """Add a src→dest CopyOp for a relative file entry."""
        src = (manifest.plugin_root / rel).resolve()
        if not src.is_relative_to(plugin_root):
            raise ConfineError(manifest.tool_name, "compose", rel)
        d = dest / rel
        _confine_dest(dest, d)
        raw_ops.append(CopyOp(src=src, dest=d))

    # Always-on: .claude-plugin/
    _add_dir(".claude-plugin")

    # Always-on: base dirs
    for base_dir in manifest.base:
        _add_dir(base_dir)

    # Always-on: hooks (if declared).
    # hooks.json shells out to sibling scripts (e.g. harvest-candidates.py) via
    # ${CLAUDE_PLUGIN_ROOT}/hooks/<script>.  Wiring only the JSON file would land
    # hooks.json but strip those scripts, so the hooks fail at runtime with
    # FileNotFoundError.  Wire the whole directory that contains hooks_json so its
    # scripts ship alongside it.  (Falls back to a bare file copy in the unusual
    # case where hooks_json sits at the plugin root with no dedicated dir.)
    if manifest.hooks_json is not None:
        hooks_dir = str(Path(manifest.hooks_json).parent)
        if hooks_dir != ".":  # Path.parent is "." when hooks_json sits at the plugin root
            _add_dir(hooks_dir)
        else:
            _add_file(manifest.hooks_json)

    # Selected capabilities: skills dirs + agent files
    for name in selected_names:
        cap = manifest.capabilities[name]
        for skill in cap["skills"]:
            _add_dir(skill)
        for agent in cap["agents"]:
            _add_file(agent)

    # De-duplicate benign overlaps first
    deduped = _dedup_ops(raw_ops)

    # Detect genuine collisions (raises before any write)
    _detect_collisions(deduped)

    return Plan(ops=deduped)


def apply_plan(plan: Plan, *, mode: str = "copy") -> None:
    """Execute a :class:`Plan`, writing files to disk.

    This is the ONLY function in this module that writes to the filesystem.

    Args:
        plan: The composition plan from :func:`compose_plan`.
        mode: ``"copy"`` (default) uses ``shutil.copytree`` / ``shutil.copy2``
              with ``symlinks=False`` — symlinks inside source trees are
              resolved to their target contents, never preserved as escaping
              links.  ``"symlink"`` creates directory symlinks instead.

    Raises:
        ValueError: Unknown mode.
    """
    if mode not in ("copy", "symlink"):
        raise ValueError(f"unknown apply_plan mode {mode!r}; expected 'copy' or 'symlink'")

    for op in plan.ops:
        op.dest.parent.mkdir(parents=True, exist_ok=True)
        if mode == "copy":
            if op.src.is_dir():
                # Skip Python build cruft — copytree copies the source tree verbatim,
                # so without this a stray __pycache__/*.pyc (e.g. beside the hooks
                # scripts) would ship into the user's install.
                shutil.copytree(
                    op.src,
                    op.dest,
                    symlinks=False,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
            else:
                shutil.copy2(op.src, op.dest)
        else:
            op.dest.symlink_to(op.src)
