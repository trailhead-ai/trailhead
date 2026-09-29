#!/usr/bin/env python3
"""bin/_trailhead.py — Python shim that bin/trailhead execs on a qualifying interpreter.

Puts the repo root on sys.path so that `import trailhead` resolves
self-relatively (no pip install needed), then invokes trailhead.cli.main().
Not meant to be run directly: bin/trailhead picks an interpreter that meets the
declared requires-python floor and passes its arguments through to this file.

This file lives at <repo_root>/bin/_trailhead.py, so the repo root is one level
up from dirname(__file__). It is deliberately not named trailhead*.py, since its
directory is sys.path[0] and such a name would sit under the package's import name.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Put the repo root on sys.path so `import trailhead` resolves without pip.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from trailhead.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
