"""Vault layer model and resolution for lore's multi-root vault support.

VaultLayer is the unit threaded through recall and capture — name, root, kind,
and trusted ride together through every call boundary so provenance is never lost.

This module provides:
  - VaultLayer dataclass (frozen, trusted defaults from kind)
  - resolve_layers() → personal layer plus any shared layers
  - layer_for_path() → maps a note path back to its owning layer
  - validate_layer_name() / assert_within_root() confinement helpers
  - _discover_shared_vaults() — lazy guarded import of camp's resolver
"""

import dataclasses
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LayerConfinementError(Exception):
    """Raised when a layer name or path fails the confinement check.

    Mirrors camp's GroupConfinementError contract for the layer segment
    lore appends to path constructions.
    """


# ---------------------------------------------------------------------------
# VaultLayer dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class VaultLayer:
    """A single vault root with provenance and trust metadata.

    Attributes:
        name:    Human-readable layer name (e.g. "personal", "team-vault").
        root:    Absolute path to the vault root directory.
        kind:    "personal" or "shared" — determines default trust and rendering.
        trusted: Whether content from this layer is trusted (self-authored).
                 Personal layers are True, shared layers are False.
    """

    name: str
    root: Path
    kind: str  # "personal" | "shared"
    trusted: bool


# ---------------------------------------------------------------------------
# layer-name confinement
# ---------------------------------------------------------------------------


def validate_layer_name(name: str) -> None:
    """Validate that `name` is safe to use as a single path segment.

    Rejects names containing:
      - path separators (/ or \\)
      - '..' components
      - null bytes or other control characters
      - empty string

    Raises:
        LayerConfinementError: with the bad name in the message.
    """
    if not name:
        raise LayerConfinementError("lore: layer name must not be empty")
    if "/" in name or "\\" in name or ".." in name or os.sep in name or "\x00" in name:
        raise LayerConfinementError(
            f"lore: layer name {name!r} must not contain path separators, "
            "backslashes, '..', or null bytes (confinement)"
        )


# ---------------------------------------------------------------------------
# path confinement with .resolve()
# ---------------------------------------------------------------------------


def assert_within_root(candidate: Path, root: Path) -> None:
    """Assert that candidate resolves to a path within root.

    Calls .resolve() on both candidate and root before comparing, so symlinks
    cannot bypass the check.

    Raises:
        LayerConfinementError: if candidate resolves outside root.
    """
    resolved_candidate = candidate.resolve()
    resolved_root = root.resolve()
    if not resolved_candidate.is_relative_to(resolved_root):
        raise LayerConfinementError(
            f"lore: path {candidate!r} resolves to {resolved_candidate!r} "
            f"which is outside the layer root {resolved_root!r} (confinement)"
        )


# ---------------------------------------------------------------------------
# layer_for_path() — maps a note path back to its owning layer
# ---------------------------------------------------------------------------


def layer_for_path(path: Path, layers: list[VaultLayer]) -> VaultLayer | None:
    """Return the first layer whose root contains path, or None.

    Uses .resolve() on both the path and each root before comparing,
    so symlinked paths resolving into a root still match.

    Args:
        path:   The note path to map.
        layers: Ordered list of VaultLayer instances to check.

    Returns:
        The first matching VaultLayer, or None if no layer contains path.
    """
    resolved_path = path.resolve()
    for layer in layers:
        resolved_root = layer.root.resolve()
        if resolved_path.is_relative_to(resolved_root):
            return layer
    return None


# ---------------------------------------------------------------------------
# Lazy guarded import of camp's resolver
# The camp plugin root is a sibling subtree; it must be on sys.path.
# ---------------------------------------------------------------------------

# Walk upward from this file to find the trailhead repo root (the directory
# that contains trailhead/paths.py), then derive the camp plugin root from it.
_TRAILHEAD_ROOT: Path | None = None
for _p in (Path(__file__).resolve(), *Path(__file__).resolve().parents):
    if (_p / "trailhead" / "paths.py").exists():
        _TRAILHEAD_ROOT = _p
        break

_CAMP_PLUGIN_ROOT: Path | None = (
    _TRAILHEAD_ROOT / "tools" / "camp" / "plugins" / "camp" if _TRAILHEAD_ROOT else None
)


def resolve_active_group_config(
    groups_dir: "Path | None",
    cwd: Path,
    *,
    camp_state_dir: "Path | None" = None,
    degrade_target: str = "personal-only recall",
) -> "dict | None":
    """Resolve ``cwd`` to the active camp group's config dict, or ``None``.

    Shared by the read path (shared-vault discovery) and the write path
    (group-default scope routing): performs the lazy, guarded camp import and
    the cwd->group resolution once, returning the matching group's raw config
    dict (as produced by ``load_group``, including the ``_toml_path`` key) or
    ``None`` so callers can project their own slice.

    Returns ``None`` on every degradation: ``groups_dir`` absent, trailhead or
    camp unimportable, the group config unreadable/malformed, an invalid
    configured group name, an overlap, or no group matching ``cwd``. A clean
    no-match is silent; an overlap, an unreadable/malformed config, or an
    invalid group name prints a warning naming ``degrade_target`` first.

    ``ModuleNotFoundError`` from the lazy ``import trailhead.paths`` inside
    ``resolve_from_cwd`` is deliberately NOT caught: it can occur only if the
    bootstrap guard below failed to run, which is a programming error to fix,
    not a runtime condition to swallow (swallowing it would silently disable all
    group resolution). The guard runs BEFORE the camp import because camp
    internally does ``import trailhead.paths``. ``camp_state_dir`` is forwarded
    to ``resolve_from_cwd`` so resolution stays isolated in tests.
    """
    if groups_dir is None:
        return None

    if _CAMP_PLUGIN_ROOT is not None and str(_CAMP_PLUGIN_ROOT) not in sys.path:
        sys.path.insert(0, str(_CAMP_PLUGIN_ROOT))

    try:
        from _bootstrap import ensure_trailhead_importable

        ensure_trailhead_importable()
    except (ImportError, SystemExit):
        return None  # trailhead unavailable

    try:
        import camp.group.config as _gc
        import camp.group.resolve as _gr
        from camp.group.config import GroupConfigError
        from camp.group.resolve import (
            GroupConfinementError,
            GroupResolutionError,
        )
    except ImportError:
        return None  # camp absent

    try:
        group_configs = _gc.load_all_groups(groups_dir)  # [] if dir absent
    except GroupConfigError as exc:
        print(
            f"lore: camp group config error; degrading to {degrade_target}: {exc}",
            file=sys.stderr,
        )
        return None  # malformed config
    except (OSError, UnicodeDecodeError) as exc:
        # load_group only wraps TOMLDecodeError; an unreadable (permission) or
        # non-UTF-8 group TOML would otherwise crash the caller. Degrade instead.
        print(
            f"lore: cannot read camp group config; degrading to {degrade_target}: {exc}",
            file=sys.stderr,
        )
        return None

    if not group_configs:
        return None

    try:
        group_name, _slug = _gr.resolve_from_cwd(
            cwd, group_configs, camp_state_dir=camp_state_dir
        )
    except GroupResolutionError as exc:
        # cwd not in any group → silent (normal, not a warning)
        # overlap (one repo in multiple groups) → emit a named warning
        exc_msg = str(exc)
        if "multiple groups" in exc_msg:
            print(
                f"lore: {exc_msg}; degrading to {degrade_target}",
                file=sys.stderr,
            )
        return None
    except GroupConfinementError as exc:
        # A configured group whose name is not a safe path segment.
        print(
            f"lore: invalid camp group name; degrading to {degrade_target}: {exc}",
            file=sys.stderr,
        )
        return None

    return next(
        (cfg for cfg in group_configs if cfg["group"]["name"] == group_name),
        None,
    )


def _discover_shared_vaults(groups_dir: Path, cwd: Path) -> list[dict]:
    """Discover shared vaults from the active group's camp config.

    Returns the raw [[shared_vaults]] entries from the active group, each with a
    "_toml_path" key (from load_group) for relative-root resolution, or [] on any
    degradation — so every failure path preserves the personal layer. Delegates
    the cwd->group resolution to :func:`resolve_active_group_config`.

    Args:
        groups_dir: The camp groups directory (trailhead.paths.config_dir("camp")/groups).
        cwd:        The current working directory for group resolution.

    Returns:
        List of raw shared_vault dicts, each {"name": str, "root": str, "_toml_path": str}.
    """
    cfg = resolve_active_group_config(groups_dir, cwd)
    if cfg is None:
        return []
    toml_path = cfg.get("_toml_path", "")
    return [
        {"name": sv["name"], "root": sv["root"], "_toml_path": toml_path}
        for sv in cfg.get("shared_vaults", [])
    ]


# ---------------------------------------------------------------------------
# resolve_layers() — personal layer + shared layers
# ---------------------------------------------------------------------------


def resolve_layers(
    *,
    cwd: Path | None = None,
    groups_dir: Path | None = None,
) -> list[VaultLayer]:
    """Return the ordered list of vault layers for the current session.

    Personal is always layer 0 (trusted). For each declared, existing,
    confinement-valid shared vault from the active group's camp config,
    appends a VaultLayer(kind="shared", trusted=False) in declared order.

    Every failure degrades gracefully: no group / camp absent / malformed config
    / missing root → personal-only or drops-the-bad-layer, never crashes.

    Args:
        cwd:        Current working directory for group resolution.
                    Defaults to Path.cwd() if None.
        groups_dir: Camp groups directory.
                    Defaults to trailhead.paths.config_dir("camp")/"groups" if None.

    Returns:
        Ordered list of VaultLayer, personal first.
    """
    # Function-local import keeps this module free of the vault ↔ vault_config
    # module-load cycle (config.py imports layers + record_model; Axiom 6).
    from .config import resolve_active_vault

    personal_root = resolve_active_vault()
    personal_layer = VaultLayer(
        name="personal",
        root=personal_root,
        kind="personal",
        trusted=True,
    )
    layers: list[VaultLayer] = [personal_layer]

    # Resolve the groups_dir default via trailhead.paths (only on this path).
    if groups_dir is None:
        try:
            from _bootstrap import ensure_trailhead_importable

            ensure_trailhead_importable()
            import trailhead.paths as _paths

            groups_dir = _paths.config_dir("camp") / "groups"
        except (ImportError, SystemExit):
            return layers  # trailhead unavailable → personal-only

    if cwd is None:
        cwd = Path.cwd()

    raw_shared = _discover_shared_vaults(groups_dir, cwd)

    for sv in raw_shared:
        sv_name = sv["name"]
        sv_root_str = sv["root"]
        toml_path = sv.get("_toml_path", "")

        # validate layer name
        try:
            validate_layer_name(sv_name)
        except LayerConfinementError as exc:
            print(
                f"lore: shared vault {sv_name!r} has invalid name; skipped ({exc})",
                file=sys.stderr,
            )
            continue

        # resolve root — relative paths resolve relative to the TOML file location
        sv_root_path = Path(sv_root_str)
        if not sv_root_path.is_absolute() and toml_path:
            sv_root_path = Path(toml_path).parent / sv_root_path
        resolved_root = sv_root_path.resolve()

        # reject same-path collision
        if resolved_root == personal_root.resolve():
            print(
                f"lore: shared vault {sv_name!r} resolves to the same path as the "
                f"personal vault; skipped (same-path collision)",
                file=sys.stderr,
            )
            continue

        # Check existence on disk
        if not resolved_root.is_dir():
            print(
                f"lore: shared vault {sv_name!r} not found at {resolved_root}; skipped",
                file=sys.stderr,
            )
            continue

        layers.append(
            VaultLayer(
                name=sv_name,
                root=resolved_root,
                kind="shared",
                trusted=False,
            )
        )

    return layers
