"""Vault layer model and resolution for lore's multi-root vault support.

VaultLayer is the unit threaded through recall and capture — name, root, kind,
and trusted ride together through every call boundary so provenance is never lost.

Slice 0 delivers:
  - VaultLayer dataclass (frozen, trusted defaults from kind)
  - resolve_layers() → personal-only layer list (Slice 0) + shared layers (Slice 1)
  - layer_for_path() → maps a note path back to its owning layer
  - validate_layer_name() / assert_within_root() confinement helpers (D-E pattern)

Slice 1 adds:
  - _discover_shared_vaults() — lazy guarded import of camp's resolver (B-1)
  - resolve_layers(cwd, groups_dir) — appends shared layers (A-4, C-3, C-4, D20)
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
# D-E: layer-name confinement
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
# A-4: path confinement with .resolve()
# ---------------------------------------------------------------------------


def assert_within_root(candidate: Path, root: Path) -> None:
    """Assert that candidate resolves to a path within root.

    Calls .resolve() on both candidate and root before comparing, so symlinks
    cannot bypass the check (A-4).

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

    Uses .resolve() on both the path and each root before comparing (A-4),
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
# Slice 1: lazy guarded import of camp's resolver (B-1)
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
    _TRAILHEAD_ROOT / "tools" / "camp" / "plugins" if _TRAILHEAD_ROOT else None
)


def _discover_shared_vaults(groups_dir: Path, cwd: Path) -> list[dict]:
    """Discover shared vaults from the active group's camp config.

    Performs a lazy, guarded import of camp's resolver (B-1). Returns an empty
    list on any failure, so every degradation path preserves the personal layer.

    The returned dicts carry the raw [[shared_vaults]] entries plus a
    "_toml_path" key (from load_group) for relative-root resolution (A-4).

    Args:
        groups_dir: The camp groups directory (trailhead.paths.config_dir("camp")/groups).
        cwd:        The current working directory for group resolution.

    Returns:
        List of raw shared_vault dicts from the active group's config,
        each with {"name": str, "root": str, "_toml_path": str}.
        Returns [] on any error (camp absent, malformed config, no group, overlap).
    """
    if _CAMP_PLUGIN_ROOT is not None and str(_CAMP_PLUGIN_ROOT) not in sys.path:
        sys.path.insert(0, str(_CAMP_PLUGIN_ROOT))

    # camp.group_resolve.resolve_from_cwd lazily does `import trailhead.paths`,
    # assuming the entry-point bootstrap guard already ran. When lore reaches into
    # camp as a *library* (not via camp's own CLI) that guarantee doesn't hold, so
    # we run the guard here. Without it, the lazy import raises ModuleNotFoundError
    # which escapes the GroupResolutionError catch below and degrades recall to
    # personal-only — silently dropping every shared layer (B-1/D20).
    try:
        from _bootstrap import ensure_trailhead_importable

        ensure_trailhead_importable()
    except (ImportError, SystemExit):
        return []  # trailhead unavailable → personal-only (B-1/D20)

    try:
        import camp.scripts.group_config as _gc
        import camp.scripts.group_resolve as _gr
        from camp.scripts.group_config import GroupConfigError
        from camp.scripts.group_resolve import GroupResolutionError
    except ImportError:
        return []  # camp absent → personal-only (B-1/D20)

    try:
        group_configs = _gc.load_all_groups(groups_dir)  # [] if dir absent
    except GroupConfigError as exc:
        print(
            f"lore: camp group config error; shared vaults unavailable: {exc}",
            file=sys.stderr,
        )
        return []  # malformed config → personal-only (C-3)

    if not group_configs:
        return []

    try:
        group_name, _slug = _gr.resolve_from_cwd(cwd, group_configs)
    except GroupResolutionError as exc:
        # cwd not in any group → silent personal-only (normal, not a warning)
        # overlap → emit a named warning (C-4)
        exc_msg = str(exc)
        if "multiple groups" in exc_msg or "overlap" in exc_msg.lower():
            print(
                f"lore: {exc_msg}; recall degrading to personal-only",
                file=sys.stderr,
            )
        return []  # personal-only for recall (C-4)

    for cfg in group_configs:
        if cfg["group"]["name"] == group_name:
            shared_vaults = cfg.get("shared_vaults", [])
            toml_path = cfg.get("_toml_path", "")
            return [
                {"name": sv["name"], "root": sv["root"], "_toml_path": toml_path}
                for sv in shared_vaults
            ]
    return []


# ---------------------------------------------------------------------------
# resolve_layers() — personal layer + shared layers (Slice 1)
# ---------------------------------------------------------------------------


def resolve_layers(
    *,
    cwd: Path | None = None,
    groups_dir: Path | None = None,
) -> list[VaultLayer]:
    """Return the ordered list of vault layers for the current session.

    Personal is always layer 0 (trusted). For each declared, existing,
    confinement-valid shared vault from the active group's camp config,
    appends a VaultLayer(kind="shared", trusted=False) in declared order (A-4).

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
    from vault import resolve_vault

    root_str = resolve_vault()
    personal_root = Path(root_str)
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

        # A-4: validate layer name
        try:
            validate_layer_name(sv_name)
        except LayerConfinementError as exc:
            print(
                f"lore: shared vault {sv_name!r} has invalid name; skipped ({exc})",
                file=sys.stderr,
            )
            continue

        # A-4: resolve root — relative paths resolve relative to the TOML file location
        sv_root_path = Path(sv_root_str)
        if not sv_root_path.is_absolute() and toml_path:
            sv_root_path = Path(toml_path).parent / sv_root_path
        resolved_root = sv_root_path.resolve()

        # A-4: reject same-path collision
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
