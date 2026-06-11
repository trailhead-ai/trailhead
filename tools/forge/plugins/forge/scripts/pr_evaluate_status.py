#!/usr/bin/env python3
"""Evaluate PR status and return recommended action.

Usage: pr_evaluate_status.py <repo-path> <pr-number>
       [--since <iso>] [--fail-count <n>]
       [--review-bot-login <login>]
       [--scripts-dir <path>]

Output JSON:
  {
    "action": "done" | "rebase" | "fix_ci" | "rerun_ci" | "review" | "wait",
    "reason": "...",
    "details": {...}
  }

D-3: review_bot_login is OPTIONAL — default None → CI-only (no bot review path).
All gh calls go through the injectable runner (R-1, S-4 shell=False).
B-3: check_pr_status called via injectable runner (no hardcoded path).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import runner_protocol as rp
import check_pr_status as cps


def evaluate(
    status: dict[str, Any],
    *,
    review_bot_login: str | None = None,
    fail_count: int = 0,
) -> dict[str, Any]:
    """Classify a PR status dict into a recommended action.

    Args:
        status:            Output of check_pr_status.check_status().
        review_bot_login:  If set, bot reviews in 'botReviews' → 'review' action.
                           Default None → CI-only (bot reviews ignored).
        fail_count:        How many times this check has already failed (for
                           flake tolerance: >= 2 → treat as real failure).

    Returns:
        {"action": str, "reason": str, "details": {...}}
    """
    mergeable = status.get("mergeable", "")
    merge_state = status.get("mergeStateStatus", "")
    is_draft = status.get("isDraft", False)
    failing = status.get("failingChecks", [])

    if is_draft:
        return {"action": "wait", "reason": "PR is a draft", "details": {}}

    if mergeable == "MERGEABLE" and merge_state == "CLEAN":
        # Check bot reviews before declaring done
        if review_bot_login:
            bot_reviews = status.get("botReviews", [])
            changes = [r for r in bot_reviews if r.get("state") == "CHANGES_REQUESTED"]
            comments = [r for r in bot_reviews if r.get("state") == "COMMENTED"]
            if changes or comments:
                return {
                    "action": "review",
                    "reason": f"{len(changes)} changes requested, {len(comments)} comments from {review_bot_login}",
                    "details": {"reviews": bot_reviews},
                }
        return {"action": "done", "reason": "PR is mergeable and clean", "details": status}

    if mergeable == "CONFLICTING":
        return {
            "action": "rebase",
            "reason": "PR has merge conflicts — rebase and resolve",
            "details": {},
        }

    if failing:
        has_code_annotations = any(
            ann.get("path") and not ann.get("truncated")
            for check in failing
            for ann in check.get("annotations", [])
        )

        if fail_count >= 2:
            return {
                "action": "fix_ci",
                "reason": f"CI failing {fail_count + 1} times — treat as real failure",
                "details": {"checks": failing},
            }

        if has_code_annotations:
            return {
                "action": "fix_ci",
                "reason": "CI failure with code annotations — fix the code",
                "details": {"checks": failing},
            }

        run_ids: set[str] = set()
        for check in failing:
            link = check.get("link", "")
            m = re.search(r"/actions/runs/(\d+)", link)
            if m:
                run_ids.add(m.group(1))

        return {
            "action": "rerun_ci",
            "reason": "CI failure without clear code annotations — rerun failed jobs",
            "details": {
                "checks": failing,
                "commands": [f"gh run rerun {rid} --failed" for rid in sorted(run_ids)] if run_ids else [],
            },
        }

    if review_bot_login:
        bot_reviews = status.get("botReviews", [])
        changes = [r for r in bot_reviews if r.get("state") == "CHANGES_REQUESTED"]
        comments = [r for r in bot_reviews if r.get("state") == "COMMENTED"]
        if changes or comments:
            return {
                "action": "review",
                "reason": f"{len(changes)} changes requested, {len(comments)} comments from {review_bot_login}",
                "details": {"reviews": bot_reviews},
            }

    return {"action": "wait", "reason": "CI still running or no actionable state yet", "details": status}


def evaluate_with_check(
    repo_path: str,
    pr_number: str,
    *,
    since: str | None = None,
    fail_count: int = 0,
    review_bot_login: str | None = None,
    runner: rp.Runner | None = None,
) -> dict[str, Any]:
    """Run check_pr_status then evaluate the result.

    B-3: check_pr_status is called via the injectable runner, not a hardcoded path.
    """
    effective = runner if runner is not None else rp._default_runner
    status = cps.check_status(
        repo_path,
        pr_number,
        since=since,
        review_bot_login=review_bot_login,
        runner=effective,
    )
    return evaluate(status, review_bot_login=review_bot_login, fail_count=fail_count)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_path")
    ap.add_argument("pr_number")
    ap.add_argument("--since", default=None)
    ap.add_argument("--fail-count", type=int, default=0)
    ap.add_argument("--review-bot-login", default=None)
    args = ap.parse_args(argv)

    import os
    if not os.path.isdir(args.repo_path):
        print(json.dumps({"action": "error", "reason": f"not a directory: {args.repo_path}"}))
        return 1

    try:
        result = evaluate_with_check(
            args.repo_path,
            args.pr_number,
            since=args.since,
            fail_count=args.fail_count,
            review_bot_login=args.review_bot_login,
        )
    except RuntimeError as e:
        print(json.dumps({"action": "error", "reason": str(e)}))
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
