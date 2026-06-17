#!/usr/bin/env python3
"""Poll the status of one PR (portage thin CLI).

Usage: check_pr_status.py <repo-path> <pr-number> [--since <iso>]
       [--review-bot-login <login>]

Thin consumer of trailhead.vcs: delegates to ``get_provider().pr.status(...)``.
Reproduces the craft CLI's argv + JSON output shape.

Output JSON:
  {mergeable, mergeStateStatus, isDraft, failingChecks[, botReviews]}
Exit 0 on success; exit 1 if the path is not a directory or gh fetch fails.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from _bootstrap import ensure_trailhead_importable

ensure_trailhead_importable()

from trailhead.vcs import get_provider  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_path")
    ap.add_argument("pr_number")
    ap.add_argument("--since", default=None)
    ap.add_argument("--review-bot-login", default=None)
    args = ap.parse_args(argv)

    if not Path(args.repo_path).is_dir():
        print(json.dumps({"error": f"not a directory: {args.repo_path}"}))
        return 1

    try:
        result = get_provider().pr.status(
            args.repo_path,
            args.pr_number,
            since=args.since,
            review_bot_login=args.review_bot_login,
        )
    except RuntimeError as e:
        print(json.dumps({"error": str(e)}))
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
