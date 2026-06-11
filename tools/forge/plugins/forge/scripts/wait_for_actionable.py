#!/usr/bin/env python3
"""Block until at least one PR reaches an actionable state.

Usage: wait_for_actionable.py [--since <iso>] [--timeout <secs>]
       [--interval <secs>] [--review-bot-login <login>]
       <repo1:pr1> [repo2:pr2 ...]

Polls pr_evaluate_status every <interval> seconds. Exits when any PR has an
action other than "wait", or when the timeout is reached.

Output JSON:
  {
    "actionable": {"<repo:pr>": {...}},
    "waiting": {"<repo:pr>": {...}}
  }
  -- or on timeout: {"timeout": true, "elapsed_seconds": N}

All evaluate calls go through the injectable runner (R-1, S-4 shell=False).
No hardcoded review-bot login — caller passes --review-bot-login explicitly.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any

import runner_protocol as rp
import pr_evaluate_status as pev
import check_pr_status as cps


def wait(
    pr_pairs: list[tuple[str, str]],
    *,
    since: str | None = None,
    timeout: int = 1800,
    interval: int = 30,
    review_bot_login: str | None = None,
    runner: rp.Runner | None = None,
) -> dict[str, Any]:
    """Poll until actionable or timeout.

    Args:
        pr_pairs:         List of (repo_path, pr_number) tuples.
        since:            ISO timestamp filter for reviews.
        timeout:          Max seconds to wait before returning timeout result.
        interval:         Seconds between polls.
        review_bot_login: Optional bot login for review detection.
        runner:           Injectable runner for tests.

    Returns:
        {"actionable": {...}, "waiting": {...}} or {"timeout": True, ...}
    """
    elapsed = 0

    while elapsed < timeout:
        actionable: dict[str, Any] = {}
        waiting: dict[str, Any] = {}

        for repo, pr in pr_pairs:
            key = f"{repo}:{pr}"
            try:
                result = pev.evaluate_with_check(
                    repo,
                    pr,
                    since=since,
                    review_bot_login=review_bot_login,
                    runner=runner,
                )
            except Exception as e:
                result = {"action": "error", "reason": str(e)}

            if result.get("action") == "wait":
                waiting[key] = result
            else:
                actionable[key] = result

        if actionable:
            return {"actionable": actionable, "waiting": waiting}

        time.sleep(interval)
        elapsed += interval

    return {"timeout": True, "elapsed_seconds": elapsed}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--review-bot-login", default=None)
    ap.add_argument("pairs", nargs="+", metavar="repo:pr")
    args = ap.parse_args(argv)

    pr_pairs = []
    for pair in args.pairs:
        repo, pr = pair.split(":", 1)
        pr_pairs.append((repo, pr))

    result = wait(
        pr_pairs=pr_pairs,
        since=args.since,
        timeout=args.timeout,
        interval=args.interval,
        review_bot_login=args.review_bot_login,
    )
    print(json.dumps(result, indent=2))
    if result.get("timeout"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
