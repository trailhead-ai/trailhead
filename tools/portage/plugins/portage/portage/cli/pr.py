"""``portage`` PR subcommands: check-status, evaluate-status, merge, approvals,
summarize, and the ``sidecar`` read/write pair.

All are thin consumers of ``trailhead.vcs``: each parses argv, calls the matching
``get_provider().pr`` method, and reproduces the JSON output shape + exit codes.
The provider owns the ordered-merge logic and the safety gates (the merge_order
refusal and the fail-closed ``auto_merge`` gate); the ``merge`` handler surfaces
those refusals as a clean exit 2.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from trailhead.vcs import get_provider
from trailhead.vcs.github import (
    AutoMergeDisabledError,
    InvalidInputError,
    ManifestReadError,
    MergeConfigError,
    MergeOrderRequiredError,
    PRPair,
    SidecarError,
)

from ..pairs import PairFormatError, split_pair
from ..sidecar import parse_pr_token


def add_pr_subparsers(sub) -> None:
    _add_check_status(sub)
    _add_evaluate_status(sub)
    _add_merge(sub)
    _add_approvals(sub)
    _add_summarize(sub)
    _add_sidecar(sub)


# ---------------------------------------------------------------------------
# check-status
# ---------------------------------------------------------------------------


def _add_check_status(sub) -> None:
    p = sub.add_parser(
        "check-status",
        help="Poll the status of one PR.",
        description="Poll the status of one PR.",
    )
    p.add_argument("repo_path")
    p.add_argument("pr_number")
    p.add_argument("--since", default=None)
    p.add_argument("--review-bot-login", default=None)
    p.set_defaults(func=cmd_check_status)


def cmd_check_status(args: argparse.Namespace) -> int:
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
    except (RuntimeError, InvalidInputError) as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


# ---------------------------------------------------------------------------
# evaluate-status
# ---------------------------------------------------------------------------


def _add_evaluate_status(sub) -> None:
    p = sub.add_parser(
        "evaluate-status",
        help="Evaluate PR status and return a recommended action.",
        description="Evaluate PR status and return a recommended action.",
    )
    p.add_argument("repo_path")
    p.add_argument("pr_number")
    p.add_argument("--since", default=None)
    p.add_argument("--fail-count", type=int, default=0)
    p.add_argument("--review-bot-login", default=None)
    p.set_defaults(func=cmd_evaluate_status)


def cmd_evaluate_status(args: argparse.Namespace) -> int:
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


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def _add_merge(sub) -> None:
    p = sub.add_parser(
        "merge",
        help="Merge camp-group PRs in dependency order.",
        description="Merge camp-group PRs in dependency order with safety checks.",
    )
    p.add_argument("--manifest", required=True, help="Path to the camp central manifest.json")
    p.add_argument("--toml", default=None, help="Path to the group TOML (for merge_order)")
    p.add_argument(
        "pairs",
        nargs="*",
        metavar="path:pr_number:member_name",
        help="One or more repo-path:pr-number:member_name pairs.",
    )
    p.set_defaults(func=cmd_merge)


def cmd_merge(args: argparse.Namespace) -> int:
    pr_pairs: list[PRPair] = []
    for pair_str in args.pairs:
        try:
            parts = split_pair(pair_str, max_parts=3)
        except PairFormatError as e:
            print(f"merge: {e}", file=sys.stderr)
            return 2
        if len(parts) < 3:
            print(
                f"merge: pair {pair_str!r} is missing member_name — expected "
                "path:pr_number:member_name (2-field basename back-fill has been removed "
                "because it silently corrupts merge_order keying)",
                file=sys.stderr,
            )
            return 2
        repo_path, pr_number, member_name = parts[0], parts[1], parts[2]
        pr_pairs.append(PRPair(repo_path=repo_path, pr_number=pr_number, member_name=member_name))

    try:
        result = get_provider().pr.merge(pr_pairs, args.manifest, toml_path=args.toml)
    except (
        AutoMergeDisabledError,
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


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------


def _add_approvals(sub) -> None:
    p = sub.add_parser(
        "approvals",
        help="Check whether a PR carries a human-authored approval signal.",
        description=(
            "Answers whether a PR carries a human-authored approval signal — an "
            "approving review by a User (non-bot) reviewer, or the human-approved "
            "label applied by a User actor. This is the merge gate monitor checks "
            "before calling `portage merge`; it never applies the signal itself. "
            "Exits 0 approved, 1 not approved, 2 on a usage or IO error."
        ),
    )
    p.add_argument("repo_path")
    p.add_argument("pr_number")
    p.set_defaults(func=cmd_approvals)


def cmd_approvals(args: argparse.Namespace) -> int:
    # Exit codes are a three-way answer, not a boolean: 0 approved, 1 asked
    # and not approved, 2 never asked (usage or IO error). A caller gating a
    # merge on this verb must not read "could not ask" as "not approved" —
    # or, worse, collapse a bad path into the same code as a real refusal.
    if not Path(args.repo_path).is_dir():
        print(json.dumps({"error": f"not a directory: {args.repo_path}"}))
        return 2
    try:
        result = get_provider().pr.approval(args.repo_path, args.pr_number)
    except (RuntimeError, InvalidInputError) as e:
        print(json.dumps({"error": str(e)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result.get("approved") else 1


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------


def _add_summarize(sub) -> None:
    p = sub.add_parser(
        "summarize",
        help="Fetch a PR's summarizer inputs through the VCS boundary.",
        description=(
            "Fetch a PR's summarizer inputs (metadata/diff/comments) through "
            "trailhead.vcs, with untrusted free-text fields marker-wrapped."
        ),
    )
    p.add_argument("repo_path")
    p.add_argument("pr_number")
    p.set_defaults(func=cmd_summarize)


def cmd_summarize(args: argparse.Namespace) -> int:
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


# ---------------------------------------------------------------------------
# sidecar (read / write)
# ---------------------------------------------------------------------------


def _add_sidecar(sub) -> None:
    p = sub.add_parser(
        "sidecar",
        help="Read or write the portage-owned prs.json sidecar.",
        description="Read or write the portage-owned prs.json sidecar.",
    )
    ssub = p.add_subparsers(dest="sidecar_command", required=True)

    wp = ssub.add_parser("write", help="Write PR entries to the sidecar.")
    wp.add_argument(
        "--sidecar",
        required=True,
        metavar="PATH",
        help="Absolute path to the sidecar file (e.g. <manifest_dir>/prs.json).",
    )
    wp.add_argument(
        "--pr",
        dest="prs",
        action="append",
        default=[],
        metavar="REPO:PR:URL:BRANCH",
        help="PR entry in <repo>:<pr_number>:<url>:<branch> form. Repeatable.",
    )
    wp.set_defaults(func=cmd_sidecar_write)

    rp = ssub.add_parser("read", help="Print the sidecar JSON to stdout.")
    rp.add_argument(
        "--sidecar", required=True, metavar="PATH", help="Absolute path to the sidecar file."
    )
    rp.set_defaults(func=cmd_sidecar_read)


def cmd_sidecar_write(args: argparse.Namespace) -> int:
    parsed_prs: list[dict[str, str]] = []
    for token in args.prs:
        try:
            parsed_prs.append(parse_pr_token(token))
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
    try:
        get_provider().pr.open(args.sidecar, parsed_prs)
    except SidecarError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def cmd_sidecar_read(args: argparse.Namespace) -> int:
    try:
        data = get_provider().pr.read_sidecar(args.sidecar)
    except SidecarError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(data, indent=2))
    return 0
