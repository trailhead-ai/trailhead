#!/usr/bin/env python3
"""Block until at least one PR reaches an actionable state (portage thin CLI).

Usage: wait_for_actionable.py [--since <iso>] [--timeout <secs>]
       [--interval <secs>] [--review-bot-login <login>]
       <repo1:pr1> [repo2:pr2 ...]

Thin consumer of trailhead.vcs: delegates to ``get_provider().ci.wait(...)``.
The provider owns the poll loop (status → evaluate until actionable/timeout).
Reproduces the forge CLI's argv + JSON output + exit codes.

Output JSON:
  {"actionable": {...}, "waiting": {...}}  -- or {"timeout": true, "elapsed_seconds": N}
Exit 0 on actionable; exit 1 on timeout.
"""
from __future__ import annotations

import argparse
import json

from _bootstrap import ensure_trailhead_importable

ensure_trailhead_importable()

from trailhead.vcs import get_provider


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--review-bot-login", default=None)
    ap.add_argument("pairs", nargs="+", metavar="repo:pr")
    args = ap.parse_args(argv)

    pr_pairs: list[tuple[str, str]] = []
    for pair in args.pairs:
        repo, pr = pair.split(":", 1)
        pr_pairs.append((repo, pr))

    result = get_provider().ci.wait(
        pr_pairs,
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
