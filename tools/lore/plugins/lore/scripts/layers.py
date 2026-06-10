"""Vault layer model and resolution for lore's multi-root vault support.

VaultLayer is the unit threaded through recall and capture — name, root, kind,
and trusted ride together through every call boundary so provenance is never lost.

Slice 0 delivers:
  - VaultLayer dataclass (frozen, trusted defaults from kind)
  - resolve_layers() → personal-only layer list (shared discovery is Slice 1)
  - layer_for_path() → maps a note path back to its owning layer
  - validate_layer_name() / assert_within_root() confinement helpers (D-E pattern)
"""
import dataclasses
import os
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
    if (
        "/" in name
        or "\\" in name
        or ".." in name
        or os.sep in name
        or "\x00" in name
    ):
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
# resolve_layers() — personal-only (Slice 0); shared discovery in Slice 1
# ---------------------------------------------------------------------------


def resolve_layers() -> list[VaultLayer]:
    """Return the ordered list of vault layers for the current session.

    Slice 0: returns only the personal layer, derived from $LORE_VAULT
    (expanded + resolved) or ~/lore — mirroring resolve_vault() exactly.
    Shared-layer discovery from the group's camp config is Slice 1.

    Returns:
        A list containing exactly one VaultLayer (personal, trusted=True).
    """
    from vault import resolve_vault

    root_str = resolve_vault()
    return [
        VaultLayer(
            name="personal",
            root=Path(root_str),
            kind="personal",
            trusted=True,
        )
    ]
