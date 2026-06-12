"""Bootstrap helper: make trailhead.paths importable without pip.

git-only distribution; no PyPI (name taken). The trailhead shared library
is reached by putting the repo root on sys.path — no editable install needed.

Four-tier fallback:
  1. Already importable (pytest run from repo root, or homebrew-installed future).
  2. Explicit override via $TRAILHEAD_ROOT env var (set by `trailhead install`
     or by the PATH shim, once wired).
  3. Walk upward from this file looking for a dir that contains trailhead/paths.py
     (the monorepo layout: this file lives at tools/landing/plugins/landing/scripts/).
  4. Hard error with a legible message (NOT a raw ModuleNotFoundError).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_trailhead_importable() -> None:
    """Ensure `import trailhead.paths` works, or exit with a legible message."""
    # Tier 1: already importable.
    try:
        import trailhead.paths  # noqa: F401
        return
    except ImportError:
        pass

    # Tier 2: explicit override via env var.
    root = os.environ.get("TRAILHEAD_ROOT")

    # Tier 3: walk upward from this file for the marker (trailhead/paths.py).
    if not root:
        here = Path(__file__).resolve()
        for p in (here, *here.parents):
            if (p / "trailhead" / "paths.py").exists():
                root = str(p)
                break

    if root and root not in sys.path:
        sys.path.insert(0, root)

    # Tier 4: confirm importable, else legible error.
    try:
        import trailhead.paths  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "landing: the trailhead shared library isn't importable. "
            "Run landing from a trailhead checkout, or set TRAILHEAD_ROOT "
            "/ run `trailhead install`.\n"
        )
        raise SystemExit(1)
