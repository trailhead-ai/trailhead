#!/usr/bin/env python3
"""Evaluate PR status and return a recommended action (portage thin CLI).

Usage: pr_evaluate_status.py <repo-path> <pr-number>
       [--since <iso>] [--fail-count <n>] [--review-bot-login <login>]

Thin consumer of trailhead.vcs: fetches status via ``pr.status`` then classifies
via ``pr.evaluate``. Reproduces the craft CLI's argv + JSON output shape.

Output JSON:
  {action, reason, details}
Exit 0 on success; exit 1 if the path is not a directory or gh fetch fails.
"""

from __future__ import annotations

import argparse
import json
import os

from _bootstrap import ensure_trailhead_importable

ensure_trailhead_importable()

from trailhead.vcs import get_provider  # noqa: E402
from trailhead.vcs.github import InvalidInputError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_path")
    ap.add_argument("pr_number")
    ap.add_argument("--since", default=None)
    ap.add_argument("--fail-count", type=int, default=0)
    ap.add_argument("--review-bot-login", default=None)
    args = ap.parse_args(argv)

    if not os.path.isdir(args.repo_path):
        print(json.dumps({"action": "error", "reason": f"not a directory: {args.repo_path}"}))
        return 1

    provider = get_provider()
    try:
        status = provider.pr.status(
            args.repo_path,
            args.pr_number,
            since=args.since,
            review_bot_login=args.review_bot_login,
        )
    except (RuntimeError, InvalidInputError) as e:
        print(json.dumps({"action": "error", "reason": str(e)}))
        return 1

    result = provider.pr.evaluate(
        status,
        review_bot_login=args.review_bot_login,
        fail_count=args.fail_count,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
