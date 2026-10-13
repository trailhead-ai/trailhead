"""Realpath confinement shared by every writer that must stay inside a root."""

from __future__ import annotations

import os
from pathlib import Path


def realpath_is_descendant(target: str | Path, root_real: str) -> bool:
    """True iff ``realpath(target)`` is ``root_real`` itself or strictly within it.

    The explicit ``+ os.sep`` guard stops a sibling dir sharing a name prefix
    (root ``/a/blob`` vs target ``/a/blob2/x``) from satisfying the check;
    resolving via realpath catches symlink escapes. ``root_real`` must already
    be a realpath.
    """
    target_real = os.path.realpath(target)
    return target_real == root_real or target_real.startswith(root_real + os.sep)
