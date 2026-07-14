"""``portage detect-repos`` — active-repo detection for a camp group worktree.

Thin consumer of ``trailhead.vcs``: delegates to
``get_provider().repos.detect(manifest)``. The provider owns the git logic and
the injectable runner seam; this handler reproduces the JSON output shape and
exit codes.

Output: a JSON array of ``{repo, path, branch, ahead, dirty}`` for active
members. Exit 0 on success; exit 2 on a missing/malformed manifest.
"""

from __future__ import annotations

import argparse
import json
import sys

from trailhead.vcs import get_provider
from trailhead.vcs.github import ManifestReadError


def add_repos_subparser(sub) -> None:
    p = sub.add_parser(
        "detect-repos",
        help="Detect active repos in a camp group worktree.",
        description="Detect active repos in a camp group worktree.",
    )
    p.add_argument("--manifest", required=True, help="Path to the camp central manifest.json")
    p.set_defaults(func=cmd_detect_repos)


def cmd_detect_repos(args: argparse.Namespace) -> int:
    try:
        result = get_provider().repos.detect(args.manifest)
    except ManifestReadError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0
