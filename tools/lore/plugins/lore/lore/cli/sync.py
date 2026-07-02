"""``lore sync`` — stage, commit, and push the active vault."""
from __future__ import annotations

import sys
from pathlib import Path

from ..vault import config as vault_config_mod
from .common import _git, _vault_is_git_toplevel

DEFAULT_SYNC_MSG = "lore: sync vault"


def cmd_sync(args) -> int:
    vault = Path(vault_config_mod.resolve_active_vault())

    if not vault.exists():
        print(f"error: vault not found: {vault}", file=sys.stderr)
        return 1

    if not _vault_is_git_toplevel(vault):
        print(
            f"error: vault is not its own git toplevel: {vault}\n"
            "  (vault may be a subdirectory of a larger repo, or not a git repo)\n"
            "  Aborting — refusing to operate on the wrong tree.",
            file=sys.stderr,
        )
        return 1

    message = getattr(args, "message", None) or DEFAULT_SYNC_MSG

    # Stage everything.
    rc, _, stderr = _git(vault, "add", "-A")
    if rc != 0:
        print(f"error: git add failed: {stderr}", file=sys.stderr)
        return 1

    # Check if there's anything to commit.
    rc, status_out, _ = _git(vault, "status", "--porcelain")
    if rc != 0 or not status_out.strip():
        print("Nothing to commit — vault is clean.")
        return 0

    # Commit — never pass -S or --no-gpg-sign; honor adopter's commit.gpgsign.
    rc, _, stderr = _git(vault, "commit", "-m", message)
    if rc != 0:
        print(f"error: git commit failed: {stderr}", file=sys.stderr)
        return 1
    print(f"Committed: {message}")

    # Push to origin if it exists.
    rc_remote, remote_url, _ = _git(vault, "remote", "get-url", "origin")
    if rc_remote != 0 or not remote_url:
        print("No origin remote — skipping push.")
        return 0

    rc_push, _, stderr_push = _git(vault, "push", "origin")
    if rc_push != 0:
        print(
            f"notice: committed locally; push failed — re-run `lore sync` when online",
            file=sys.stderr,
        )
        print(f"  push error: {stderr_push}", file=sys.stderr)
        return 0
    print("Pushed to origin.")
    return 0


def add_sync_subparser(sub) -> None:
    """Register the ``sync`` command parser."""
    p_sync = sub.add_parser("sync", help="Stage, commit, and push the vault")
    p_sync.add_argument(
        "--message", "-m", default=None,
        help=f"Commit message (default: {DEFAULT_SYNC_MSG!r})",
    )
    p_sync.set_defaults(func=cmd_sync)
