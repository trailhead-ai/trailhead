#!/usr/bin/env python3
"""Merge PRs in dependency order with safety checks.

Usage: merge_prs.py --manifest <path> --toml <path> <path1:pr1> [path2:pr2 ...]

Reads the active camp central manifest to enumerate known members, reads the
optional [release] block from the group TOML for merge_order, and merges each
PR in the resolved order.

Safety gate (R-6/D-2): when >1 PR is queued and no merge_order is declared
in [release], refuses with a named error naming the fix (A-1).

Output JSON:
  {"merged": [...], "failed": {...}, "skipped": {...}}

Exit codes:
  0  all PRs in merged; failed and skipped are empty
  1  at least one failed or skipped (caller should inspect the JSON)
  2  configuration / manifest error (nothing merged)

All gh/git calls go through the injectable runner (R-1, S-4 shell=False).
No hardcoded repo names — merge order comes from group TOML [release] config.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import runner_protocol as rp
from manifest_read import ManifestReadError, load_manifest as _load_manifest


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


# ManifestReadError is imported from manifest_read (shared with detect_repos).


class MergeOrderRequiredError(Exception):
    """Raised when >1 PR is queued but no merge_order is declared (R-6/A-1)."""


class MergeConfigError(Exception):
    """Raised when merge_order names a member not in the manifest."""


class InvalidInputError(Exception):
    """Raised on option-injection attack vectors (pr_number / branch validation)."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class PRPair:
    repo_path: str
    pr_number: str
    member_name: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_merge_order(toml_path: str | None) -> list[str] | None:
    """Return merge_order from [release] block if declared, else None."""
    if not toml_path:
        return None
    p = Path(toml_path)
    if not p.is_file():
        return None
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None
    release = raw.get("release")
    if not isinstance(release, dict):
        return None
    order = release.get("merge_order")
    if isinstance(order, list) and all(isinstance(x, str) for x in order):
        return order
    return None


def _validate_pr_number(pr_number: str) -> None:
    """Reject non-numeric pr_number values (option-injection guard)."""
    if not re.fullmatch(r"\d+", pr_number):
        raise InvalidInputError(
            f"merge_prs: pr_number must be all digits, got: {pr_number!r}"
        )


def _resolve_author_email(runner: rp.Runner) -> str:
    r = rp.run(["git", "config", "user.email"], runner=runner)
    email = r.stdout.strip()
    if r.returncode != 0 or not email:
        raise RuntimeError(
            "git config user.email is unset — set it with: "
            "git config --global user.email you@example.com"
        )
    return email


def _get_pr_state(repo_path: str, pr_number: str, runner: rp.Runner) -> dict | None:
    """Fetch PR state JSON via gh. Returns dict or None on failure."""
    r = rp.run(
        ["gh", "pr", "view", pr_number, "--json",
         "mergeable,mergeStateStatus,isDraft,state,headRefName"],
        cwd=repo_path,
        runner=runner,
    )
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def _do_merge(repo_path: str, pr_number: str, author_email: str, runner: rp.Runner) -> tuple[bool, str]:
    r = rp.run(
        ["gh", "pr", "merge", pr_number, "--merge", "--author-email", author_email],
        cwd=repo_path,
        runner=runner,
    )
    if r.returncode != 0:
        return False, r.stderr.strip() or "gh pr merge failed"
    return True, ""


def _delete_remote_branch(repo_path: str, branch: str, runner: rp.Runner) -> None:
    if not branch or branch in ("main", "master"):
        return
    if branch.startswith("-"):
        return
    rp.run(
        ["git", "-C", repo_path, "push", "origin", "--delete", "--", branch],
        runner=runner,
    )


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------


def merge_prs(
    pr_pairs: list[PRPair],
    manifest_path: str,
    toml_path: str | None = None,
    runner: rp.Runner | None = None,
) -> dict[str, Any]:
    """Merge PRs in resolved order with safety checks.

    Args:
        pr_pairs:       List of PRPair(repo_path, pr_number, member_name).
        manifest_path:  Path to the camp central manifest.json.
        toml_path:      Path to the group TOML (optional; for merge_order).
        runner:         Injectable runner for tests (default: real subprocess).

    Returns:
        {"merged": [...], "failed": {...}, "skipped": {...}}

    Raises:
        ManifestReadError: On a missing or malformed manifest.
        MergeOrderRequiredError: On >1 PR + no merge_order (R-6).
        MergeConfigError: On merge_order naming a non-existent member.
    """
    for pair in pr_pairs:
        _validate_pr_number(pair.pr_number)

    effective_runner = runner if runner is not None else rp._default_runner

    manifest_data = _load_manifest(manifest_path)
    member_names = {m["name"] for m in manifest_data.get("members", [])}

    merge_order = _load_merge_order(toml_path)

    # R-6 safety gate
    if len(pr_pairs) > 1 and not merge_order:
        n = len(pr_pairs)
        raise MergeOrderRequiredError(
            f"refusing to merge {n} PRs with no merge_order declared — "
            f"add merge_order = [...] to the [release] block of your group TOML"
        )

    # Validate merge_order entries exist in manifest
    if merge_order:
        for entry in merge_order:
            if entry not in member_names:
                raise MergeConfigError(
                    f"merge_prs: merge_order entry '{entry}' not in manifest members "
                    f"(known: {sorted(member_names)})"
                )

    # Build ordered list of pr_pairs
    if merge_order:
        pair_by_name = {p.member_name: p for p in pr_pairs}
        ordered: list[PRPair] = []
        for name in merge_order:
            if name in pair_by_name:
                ordered.append(pair_by_name[name])
        # Any pr_pairs not covered by merge_order go last
        covered = {p.member_name for p in ordered}
        for pair in pr_pairs:
            if pair.member_name not in covered:
                ordered.append(pair)
    else:
        # Single PR (or no merge_order) — use provided order
        ordered = list(pr_pairs)

    author_email = _resolve_author_email(effective_runner)

    merged: list[str] = []
    failed: dict[str, str] = {}
    skipped: dict[str, str] = {}

    for pair in ordered:
        key = f"{pair.repo_path}:{pair.pr_number}"

        state = _get_pr_state(pair.repo_path, pair.pr_number, effective_runner)
        if state is None:
            failed[key] = "gh pr view failed"
            _skip_remaining(ordered, merged, failed, skipped, pair)
            break

        if state.get("state") == "MERGED":
            skipped[key] = "already merged"
            continue

        if state.get("isDraft"):
            failed[key] = "draft PR — not ready to merge"
            _skip_remaining(ordered, merged, failed, skipped, pair)
            break

        mergeable = state.get("mergeable")
        merge_state = state.get("mergeStateStatus")
        if mergeable != "MERGEABLE" or merge_state != "CLEAN":
            failed[key] = f"not ready: mergeable={mergeable}, mergeState={merge_state}"
            _skip_remaining(ordered, merged, failed, skipped, pair)
            break

        ok, err = _do_merge(pair.repo_path, pair.pr_number, author_email, effective_runner)
        if not ok:
            failed[key] = err
            _skip_remaining(ordered, merged, failed, skipped, pair)
            break

        merged.append(key)
        branch = state.get("headRefName", "")
        _delete_remote_branch(pair.repo_path, branch, effective_runner)

    return {"merged": merged, "failed": failed, "skipped": skipped}


def _skip_remaining(
    ordered: list[PRPair],
    merged: list[str],
    failed: dict[str, str],
    skipped: dict[str, str],
    failed_pair: PRPair,
) -> None:
    failed_key = f"{failed_pair.repo_path}:{failed_pair.pr_number}"
    for pair in ordered:
        key = f"{pair.repo_path}:{pair.pr_number}"
        if key not in merged and key not in failed and key not in skipped:
            skipped[key] = f"blocked by {failed_key}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Merge camp-group PRs in dependency order.")
    ap.add_argument("--manifest", required=True, help="Path to the camp central manifest.json")
    ap.add_argument("--toml", default=None, help="Path to the group TOML (for merge_order)")
    ap.add_argument(
        "pairs",
        nargs="*",
        metavar="path:pr_number",
        help="One or more repo-path:pr-number pairs. "
             "Each pair is <worktree_path>:<pr_number>:<member_name>",
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
        result = merge_prs(
            pr_pairs=pr_pairs,
            manifest_path=args.manifest,
            toml_path=args.toml,
        )
    except InvalidInputError as e:
        print(str(e), file=sys.stderr)
        return 2
    except ManifestReadError as e:
        print(str(e), file=sys.stderr)
        return 2
    except MergeOrderRequiredError as e:
        print(str(e), file=sys.stderr)
        return 2
    except MergeConfigError as e:
        print(str(e), file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2))
    if result.get("failed") or result.get("skipped"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
