"""``portage wait-for-actionable`` — block until a PR reaches an actionable state.

Thin consumer of ``trailhead.vcs``: delegates to ``get_provider().ci.wait(...)``.
The provider owns the poll loop (status → evaluate until actionable/timeout).

Output JSON: ``{"actionable": {...}, "waiting": {...}}`` — or
``{"timeout": true, "elapsed_seconds": N}``. Exit 0 on actionable; exit 1 on
timeout; exit 2 on a malformed ``repo:pr`` pair.
"""

from __future__ import annotations

import argparse
import json
import sys

from trailhead.vcs import get_provider

from ..pairs import PairFormatError, split_pair


def add_ci_subparser(sub) -> None:
    p = sub.add_parser(
        "wait-for-actionable",
        help="Block until at least one PR reaches an actionable state.",
        description="Block until at least one PR reaches an actionable state.",
    )
    p.add_argument("--since", default=None)
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--interval", type=int, default=30)
    p.add_argument("--review-bot-login", default=None)
    p.add_argument("pairs", nargs="+", metavar="repo:pr")
    p.set_defaults(func=cmd_wait_for_actionable)


def cmd_wait_for_actionable(args: argparse.Namespace) -> int:
    pr_pairs: list[tuple[str, str]] = []
    for pair in args.pairs:
        try:
            parts = split_pair(pair, max_parts=3)
        except PairFormatError as e:
            print(f"wait-for-actionable: {e}", file=sys.stderr)
            return 2
        repo, pr = parts[0], parts[1]
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
