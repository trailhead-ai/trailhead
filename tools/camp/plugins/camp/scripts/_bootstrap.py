"""Bootstrap helper: make trailhead.paths importable without pip.

git-only distribution; no PyPI (name taken). The trailhead shared library
is reached by putting the repo root on sys.path — no editable install needed.

Four-tier fallback (S-5 cold-start hardening: the `__file__` walk has
precedence over the env var):
  1. Already importable (pytest run from repo root, or homebrew-installed future).
  2. Walk upward from this file looking for a dir that contains trailhead/paths.py
     (the monorepo layout: this file lives at tools/camp/plugins/camp/scripts/).
     This anchor is co-located with the code already executing — the same trust
     domain — so it wins over the environment, which crosses subprocess / CI /
     settings-injection boundaries.
  3. Explicit override via $TRAILHEAD_ROOT env var — only as a fallback when the
     walk finds nothing (the plugin is installed outside any monorepo checkout,
     e.g. the camp PATH shim's front-door flow, which hardcodes TRAILHEAD_ROOT
     at write time). Trusted only after the marker is confirmed to exist there.
  4. Hard error with a legible message (NOT a raw ModuleNotFoundError).

S-5 — on a cold invocation (Tier 1 fails: trailhead not yet importable, the
normal state for a fresh thin-script process) a hostile $TRAILHEAD_ROOT must not
redirect the import to an attacker-planted trailhead/paths.py. The walk-first
ordering closes that: when the script runs from inside a checkout, the env var
is never consulted; it is reached only when there is no co-located checkout to
trust.
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

    root: str | None = None

    # Tier 2: walk upward from this file for the marker (trailhead/paths.py).
    # Co-located with the executing code, so it takes precedence over Tier 3
    # (the environment) — see the S-5 cold-start note above.
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "trailhead" / "paths.py").exists():
            root = str(p)
            break

    # Tier 3: explicit override via $TRAILHEAD_ROOT — only when the walk found
    # nothing. Validate the marker exists before trusting (and resolve symlinks)
    # so a stale or hostile env value can never poison sys.path.
    if root is None:
        env_root = os.environ.get("TRAILHEAD_ROOT")
        if env_root and (Path(env_root) / "trailhead" / "paths.py").exists():
            root = str(Path(env_root).resolve())

    if root and root not in sys.path:
        sys.path.insert(0, root)

    # Tier 4: confirm importable, else legible error.
    try:
        import trailhead.paths  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "camp: the trailhead shared library isn't importable. "
            "Run camp from a trailhead checkout, or set TRAILHEAD_ROOT "
            "/ run `trailhead install`.\n"
        )
        raise SystemExit(1)
