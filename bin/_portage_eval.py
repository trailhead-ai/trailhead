#!/usr/bin/env python3
"""bin/_portage_eval.py — the script the pinned ``portage-eval`` shim runs.

Accepts only ``start`` and ``wait`` and runs them as ``portage eval <verb> ...``, so the
command the harness excludes from its sandbox cannot be turned into any other portage command.
Puts the repo root and the portage plugin root on sys.path so the ``trailhead`` and ``portage``
packages resolve self-relatively (no pip install needed).

This file lives at <repo_root>/bin/_portage_eval.py, so the repo root is one level up from
dirname(__file__). It is deliberately not named trailhead*.py, since its directory is sys.path[0]
and such a name would sit under the package's import name.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PLUGIN_ROOT = _REPO_ROOT / "tools" / "portage" / "plugins" / "portage"
for _path in (str(_PLUGIN_ROOT), str(_REPO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

VERBS = ("start", "wait")


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in VERBS:
        print(f"portage-eval: expected one of {' | '.join(VERBS)} as the first argument", file=sys.stderr)
        return 2
    from portage.cli import dispatch

    return dispatch.main(["eval", *argv])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
