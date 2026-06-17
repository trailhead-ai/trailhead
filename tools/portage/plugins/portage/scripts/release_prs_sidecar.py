#!/usr/bin/env python3
"""prs.json sidecar read/write helper (portage thin CLI).

The sidecar lives alongside the camp central manifest: <manifest_dir>/prs.json

Thin consumer of trailhead.vcs: ``write`` delegates to ``get_provider().pr.open``
(atomic, 0o600); ``read`` delegates to ``get_provider().pr.read_sidecar`` (schema-
validated). Reproduces the craft CLI's argv + JSON output + exit codes.

CLI usage:
    release_prs_sidecar.py write --sidecar <path> --pr <repo>:<pr_number>:<url>:<branch> [--pr ...]
    release_prs_sidecar.py read  --sidecar <path>
"""
from __future__ import annotations

import argparse
import json
import sys

from _bootstrap import ensure_trailhead_importable

ensure_trailhead_importable()

from trailhead.vcs import get_provider  # noqa: E402
from trailhead.vcs.github import SidecarError  # noqa: E402


def _parse_pr_token(token: str) -> dict[str, str]:
    """Parse a <repo>:<pr_number>:<url>:<branch> token into a PR dict.

    The url may itself contain colons (e.g. https://...), so we split off the
    first two fields and the last field by position, leaving the middle as url.
    """
    head, _, rest = token.partition(":")
    if not rest:
        raise ValueError(
            f"release_prs_sidecar: --pr must be <repo>:<pr_number>:<url>:<branch>, "
            f"got: {token!r}"
        )
    pr_number_part, _, url_and_branch = rest.partition(":")
    if not url_and_branch or ":" not in url_and_branch:
        raise ValueError(
            f"release_prs_sidecar: --pr must be <repo>:<pr_number>:<url>:<branch>, "
            f"got: {token!r}"
        )
    url, _, branch = url_and_branch.rpartition(":")
    if not url or not branch:
        raise ValueError(
            f"release_prs_sidecar: --pr must be <repo>:<pr_number>:<url>:<branch>, "
            f"got: {token!r}"
        )
    return {"repo": head, "pr_number": pr_number_part, "url": url, "branch": branch}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Read or write the portage-owned prs.json sidecar."
    )
    sub = ap.add_subparsers(dest="command", required=True)

    wp = sub.add_parser("write", help="Write PR entries to the sidecar.")
    wp.add_argument("--sidecar", required=True, metavar="PATH",
                    help="Absolute path to the sidecar file (e.g. <manifest_dir>/prs.json).")
    wp.add_argument("--pr", dest="prs", action="append", default=[], metavar="REPO:PR:URL:BRANCH",
                    help="PR entry in <repo>:<pr_number>:<url>:<branch> form. Repeatable.")

    rp = sub.add_parser("read", help="Print the sidecar JSON to stdout.")
    rp.add_argument("--sidecar", required=True, metavar="PATH",
                    help="Absolute path to the sidecar file.")

    args = ap.parse_args(argv)
    provider = get_provider()

    if args.command == "write":
        parsed_prs: list[dict[str, str]] = []
        for token in args.prs:
            try:
                parsed_prs.append(_parse_pr_token(token))
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return 2
        try:
            provider.pr.open(args.sidecar, parsed_prs)
        except SidecarError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    if args.command == "read":
        try:
            data = provider.pr.read_sidecar(args.sidecar)
        except SidecarError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(json.dumps(data, indent=2))
        return 0

    return 0  # unreachable; argparse enforces required subcommand


if __name__ == "__main__":
    raise SystemExit(main())
