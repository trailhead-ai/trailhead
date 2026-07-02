"""Shared helpers for the ``camp`` CLI command-group modules.

The per-command-group modules (``dispatch``, ``group``, ``status``, …) each own
their own handlers; this module holds the small set of helpers used across more
than one group so they have a single home instead of being recomputed per-file.
"""
from __future__ import annotations

from pathlib import Path


def _groups_dir() -> Path:
    """Return ``config_dir("camp")/"groups"`` — the camp group-config directory.

    Callers run after ``main()``'s bootstrap has made ``trailhead.paths``
    importable, so this imports it directly rather than lazy-falling-back
    (unlike lore's standalone-CLI equivalent, which must tolerate a missing
    trailhead install).
    """
    import trailhead.paths as _paths

    return _paths.config_dir("camp") / "groups"
