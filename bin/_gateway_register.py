#!/usr/bin/env python3
"""bin/_gateway_register.py — the script the pinned ``trailhead-gateway-register`` shim runs.

Puts the repo root on sys.path so that `import trailhead` resolves
self-relatively (no pip install needed), then runs the session-registration step,
trailhead.gateway.register.run(). The Outpost plugin's SessionStart hook runs it,
outside the harness sandbox, so `trailhead install` pins it to the installing
interpreter, isolated, rather than routing it through bin/trailhead.

This file lives at <repo_root>/bin/_gateway_register.py, so the repo root is one level
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

from trailhead.gateway.register import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
