#!/usr/bin/env python3
"""Fetch a PR's summarizer inputs through trailhead.vcs (portage thin CLI).

Usage: summarize_pr.py <repo-path> <pr-number>

Thin consumer of trailhead.vcs: delegates to ``get_provider().pr.summary_inputs(...)``.
Consolidates the three direct-``gh`` reads a PR summary used to make (``pr view`` /
``pr diff`` / inline-comments API) behind the VCS boundary, so the untrusted-content
marker wraps every free-text field (title/body/diff/comment bodies) before it reaches
the summarizer agent — no direct-``gh`` path that bypasses the marker.

Output JSON:
  {number, title, body, state, mergeable, statusCheckRollup, diff, comments[]}
  where title/body/diff and each comments[].body are wrapped in
  ``<untrusted-content>`` markers.
Exit 0 on success; exit 1 if the path is not a directory or gh fetch fails.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _bootstrap import ensure_trailhead_importable

ensure_trailhead_importable()

from trailhead.vcs import get_provider  # noqa: E402
from trailhead.vcs.github import InvalidInputError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_path")
    ap.add_argument("pr_number")
    args = ap.parse_args(argv)

    if not Path(args.repo_path).is_dir():
        print(json.dumps({"error": f"not a directory: {args.repo_path}"}))
        return 1

    try:
        result = get_provider().pr.summary_inputs(args.repo_path, args.pr_number)
    except (RuntimeError, InvalidInputError) as e:
        print(json.dumps({"error": str(e)}))
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
