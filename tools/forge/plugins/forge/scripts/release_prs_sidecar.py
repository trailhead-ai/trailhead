#!/usr/bin/env python3
"""Forge-owned prs.json sidecar read/write helper (D-1, B-2).

The sidecar lives alongside the camp central manifest:
    <manifest_dir>/prs.json

Shape:
    {
        "schema_version": 1,
        "prs": [
            {"repo": str, "pr_number": str, "url": str, "branch": str},
            ...
        ],
        "external_tracker": null   # reserved; no connector built
    }

Write contract (B-2):
    - Atomic: temp file in parent dir + os.replace.
    - Mode 0o600 (replicates camp manifest.py:41-68 posture).
    - Raises SidecarError on any failure — never a raw exception.

Read contract (B-2):
    - Raises SidecarError on missing, malformed, or schema-invalid file.
    - Never propagates a raw KeyError, JSONDecodeError, or OSError.
    - Path always appears in the error message.

CLI usage:
    release_prs_sidecar.py write --sidecar <path> --pr <repo>:<pr_number>:<url>:<branch> [--pr ...]
    release_prs_sidecar.py read  --sidecar <path>

This module is stdlib-only (no camp import, no trailhead.paths — B-1 self-containment).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


class SidecarError(Exception):
    """Raised on a missing, malformed, or schema-invalid prs.json sidecar.

    The message always contains the file path.
    """


def write(path: Path | str, prs: list[dict[str, str]]) -> None:
    """Write prs[] to the sidecar atomically with mode 0o600.

    Creates the parent directory if it does not exist.

    Args:
        path:  Absolute path to the sidecar file (e.g. <manifest_dir>/prs.json).
        prs:   List of PR dicts with keys {repo, pr_number, url, branch}.

    Raises:
        SidecarError: On any I/O or serialisation failure.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "schema_version": 1,
        "prs": prs,
        "external_tracker": None,
    }

    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".prs-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, str(p))
        os.chmod(str(p), 0o600)
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise SidecarError(f"release_prs_sidecar: write failed at {p}: {e}") from e


def read(path: Path | str) -> dict[str, Any]:
    """Read and validate the prs.json sidecar.

    Args:
        path:  Absolute path to the sidecar file.

    Returns:
        Parsed dict with schema_version, prs[], and external_tracker keys.

    Raises:
        SidecarError: On missing, malformed, or schema-invalid file.
    """
    p = Path(path)

    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise SidecarError(
            f"release_prs_sidecar: cannot read sidecar at {p}: {e}"
        ) from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SidecarError(
            f"release_prs_sidecar: malformed JSON in sidecar at {p}: {e}"
        ) from e

    if not isinstance(data, dict):
        raise SidecarError(
            f"release_prs_sidecar: sidecar at {p} is not a JSON object"
        )

    if "prs" not in data:
        raise SidecarError(
            f"release_prs_sidecar: sidecar at {p} is missing required 'prs' field"
        )

    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_pr_token(token: str) -> dict[str, str]:
    """Parse a <repo>:<pr_number>:<url>:<branch> token into a PR dict.

    The url may itself contain colons (e.g. https://...), so we split off
    the first two fields and the last field by position, leaving the middle
    as the url.

    Raises:
        ValueError: On a malformed token (fewer than 4 colon-separated parts).
    """
    # Split off repo and pr_number from the left (first two colons)
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
    # The branch is the last colon-delimited segment; url is everything before it
    url, _, branch = url_and_branch.rpartition(":")
    if not url or not branch:
        raise ValueError(
            f"release_prs_sidecar: --pr must be <repo>:<pr_number>:<url>:<branch>, "
            f"got: {token!r}"
        )
    return {"repo": head, "pr_number": pr_number_part, "url": url, "branch": branch}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        0 on success, nonzero on error.
    """
    import argparse

    ap = argparse.ArgumentParser(
        description="Read or write the forge-owned prs.json sidecar."
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

    if args.command == "write":
        parsed_prs: list[dict[str, str]] = []
        for token in args.prs:
            try:
                parsed_prs.append(_parse_pr_token(token))
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return 2
        try:
            write(args.sidecar, parsed_prs)
        except SidecarError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    if args.command == "read":
        try:
            data = read(args.sidecar)
        except SidecarError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(json.dumps(data, indent=2))
        return 0

    return 0  # unreachable; argparse enforces required subcommand


if __name__ == "__main__":
    raise SystemExit(main())
