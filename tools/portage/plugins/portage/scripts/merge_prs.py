#!/usr/bin/env python3
"""Merge PRs in dependency order with safety checks (portage thin CLI).

Usage: merge_prs.py --manifest <path> [--toml <path>] <path1:pr1[:name]> [...]

Thin consumer of trailhead.vcs: delegates to ``get_provider().pr.merge(...)``.
The provider owns the ordered-merge logic and the R-6 safety gate (>1 PR with no
merge_order declared → refuse). This script reproduces the craft CLI's argv,
JSON output, and exit codes, including surfacing the R-6 refusal.

Output JSON:
  {"merged": [...], "failed": {...}, "skipped": {...}}

Exit codes:
  0  all PRs merged; failed and skipped are empty
  1  at least one failed or skipped
  2  configuration / manifest / merge_order error (nothing merged)
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from _bootstrap import ensure_trailhead_importable

ensure_trailhead_importable()

from trailhead.vcs import get_provider
from trailhead.vcs.github import (
    InvalidInputError,
    ManifestReadError,
    MergeConfigError,
    MergeOrderRequiredError,
    PRPair,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Merge camp-group PRs in dependency order.")
    ap.add_argument("--manifest", required=True, help="Path to the camp central manifest.json")
    ap.add_argument("--toml", default=None, help="Path to the group TOML (for merge_order)")
    ap.add_argument(
        "pairs",
        nargs="*",
        metavar="path:pr_number",
        help="One or more repo-path:pr-number[:member_name] pairs.",
    )
    args = ap.parse_args(argv)

    pr_pairs: list[PRPair] = []
    for pair_str in args.pairs:
        parts = pair_str.split(":", 2)
        if len(parts) < 2:
            print(f"merge_prs: bad pair format '{pair_str}' (expected path:pr_number[:name])",
                  file=sys.stderr)
            return 2
        repo_path = parts[0]
        pr_number = parts[1]
        if not re.fullmatch(r"\d+", pr_number):
            print(f"merge_prs: pr_number must be all digits, got: {pr_number!r}", file=sys.stderr)
            return 2
        member_name = parts[2] if len(parts) > 2 else repo_path.rstrip("/").split("/")[-1]
        pr_pairs.append(PRPair(repo_path=repo_path, pr_number=pr_number, member_name=member_name))

    try:
        result = get_provider().pr.merge(
            pr_pairs,
            args.manifest,
            toml_path=args.toml,
        )
    except (
        InvalidInputError,
        ManifestReadError,
        MergeOrderRequiredError,
        MergeConfigError,
        RuntimeError,
    ) as e:
        print(str(e), file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2))
    if result.get("failed") or result.get("skipped"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
