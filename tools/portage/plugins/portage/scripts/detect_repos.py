#!/usr/bin/env python3
"""Detect active repos in a camp group worktree (portage thin CLI).

Usage: detect_repos.py --manifest <path>

Thin consumer of trailhead.vcs: bootstraps the shared library, then delegates to
``get_provider().repos.detect(manifest_path)``. The provider owns the git logic
and the injectable runner seam; this script just reproduces the forge CLI's argv
and JSON output shape.

Output: a JSON array of {repo, path, branch, ahead, dirty} for active members.
Exit 0 on success; exit 2 on a missing/malformed manifest.
"""
from __future__ import annotations

import argparse
import json
import sys

from _bootstrap import ensure_trailhead_importable

ensure_trailhead_importable()

from trailhead.vcs import get_provider
from trailhead.vcs.github import ManifestReadError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Detect active repos in a camp group worktree.")
    ap.add_argument("--manifest", required=True, help="Path to the camp central manifest.json")
    args = ap.parse_args(argv)

    try:
        result = get_provider().repos.detect(args.manifest)
    except ManifestReadError as e:
        print(str(e), file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
