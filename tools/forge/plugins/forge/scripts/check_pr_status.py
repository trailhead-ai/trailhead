#!/usr/bin/env python3
"""Poll the status of one PR via gh.

Usage: check_pr_status.py <repo-path> <pr-number> [--since <iso>]
       [--review-bot-login <login>]

Output JSON:
  {
    "mergeable": "MERGEABLE" | "CONFLICTING" | "UNKNOWN",
    "mergeStateStatus": "CLEAN" | "BEHIND" | "BLOCKED" | "DIRTY" | ...,
    "isDraft": bool,
    "failingChecks": [...],
    "botReviews": [...]    (only if --review-bot-login is set; else absent)
  }

All gh calls go through the injectable runner (R-1, S-4 shell=False).
No hardcoded review-bot login — caller passes --review-bot-login explicitly.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import runner_protocol as rp


def _gh(args: list[str], cwd: str, runner: rp.Runner) -> Any | None:
    r = rp.run(["gh"] + args, cwd=cwd, runner=runner)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _get_owner_repo(cwd: str, runner: rp.Runner) -> str | None:
    r = rp.run(
        ["git", "remote", "get-url", "origin"],
        cwd=cwd,
        runner=runner,
    )
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def _fetch_annotations(
    check: dict,
    owner_repo: str | None,
    cwd: str,
    runner: rp.Runner,
    max_annotations: int = 10,
) -> list[dict]:
    link = check.get("link", "")
    m = re.search(r"/actions/runs/(\d+)/job/(\d+)", link)
    if not m or not owner_repo:
        return []
    job_id = m.group(2)
    raw = _gh(
        ["api", f"repos/{owner_repo}/check-runs/{job_id}/annotations",
         "--paginate", "-q",
         '[.[] | select(.annotation_level=="failure") | {path, start_line, message: .message}]'],
        cwd=cwd,
        runner=runner,
    )
    if not raw:
        return []
    annotations = raw[:max_annotations]
    if len(raw) > max_annotations:
        annotations.append({"truncated": True, "total": len(raw)})
    return annotations


def check_status(
    repo_path: str,
    pr_number: str,
    *,
    since: str | None = None,
    review_bot_login: str | None = None,
    runner: rp.Runner | None = None,
) -> dict[str, Any]:
    """Fetch PR status. Returns dict; raises RuntimeError on gh failure.

    Args:
        repo_path:         Local path to the git repo / worktree.
        pr_number:         PR number (string).
        since:             ISO timestamp — filter reviews to only those after this.
        review_bot_login:  If set, include matching bot reviews in 'botReviews'.
                           If None, 'botReviews' is absent from output (CI-only).
        runner:            Injectable runner for tests.
    """
    effective = runner if runner is not None else rp._default_runner

    pr = _gh(
        ["pr", "view", pr_number, "--json",
         "mergeable,mergeStateStatus,isDraft,reviews"],
        cwd=repo_path,
        runner=effective,
    )
    if not pr:
        raise RuntimeError(f"check_pr_status: could not fetch PR #{pr_number} in {repo_path}")

    checks_raw = _gh(
        ["pr", "checks", pr_number, "--json", "name,state,link"],
        cwd=repo_path,
        runner=effective,
    ) or []

    failing = [
        c for c in checks_raw
        if c.get("state") not in (
            "SUCCESS", "SKIPPED", "NEUTRAL", "PENDING", "IN_PROGRESS", "QUEUED"
        )
    ]

    owner_repo = _get_owner_repo(repo_path, effective)
    for check in failing:
        check["annotations"] = _fetch_annotations(check, owner_repo, repo_path, effective)

    result: dict[str, Any] = {
        "mergeable": pr.get("mergeable"),
        "mergeStateStatus": pr.get("mergeStateStatus"),
        "isDraft": pr.get("isDraft", False),
        "failingChecks": failing,
    }

    if review_bot_login:
        all_reviews = pr.get("reviews", [])
        bot_reviews = [
            r for r in all_reviews
            if r.get("author", {}).get("login") == review_bot_login
            and (not since or r.get("submittedAt", "") > since)
        ]
        result["botReviews"] = bot_reviews

    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
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
        result = check_status(
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
