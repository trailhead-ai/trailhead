"""``lore sync`` — stage, commit, and push EVERY configured vault.

The no-argument form covers all configured vaults; ``--vault <name>`` narrows to
one. Syncing only the ``default``-scope vault is not an option the CLI offers,
because record writes route by scope: from a repo bound to a product-scope vault,
``lore record create`` writes there while a ``default``-only sync commits nothing
of what was just written — and still prints "Committed / Pushed to origin". Every
line of output is therefore labeled with the vault it describes, so a mismatch
between where records land and where they are committed is visible at the call
site rather than discovered when a disk fails.

**Partial failure is per-vault, never fatal to the run.** A vault that is missing,
is not its own git toplevel, or fails to stage/commit is reported and *skipped*;
the remaining vaults are still synced and the command exits 1 at the end. One
broken vault must not be able to strand the others uncommitted — that is the same
silent-data-loss shape this module exists to close.

**Push failures stay soft** (exit 0 with a notice): the commit is already durable
locally and a later ``lore sync`` re-pushes it.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .common import (
    _git,
    _resolve_all_vaults,
    _vault_has_upstream,
    _vault_head_branch,
    _vault_is_git_toplevel,
    _vault_unpushed,
)

DEFAULT_SYNC_MSG = "lore: sync vault"


def _make_emitters(name: str, width: int):
    """Return ``(say, say_err)`` writers that label output with the vault name.

    ``say`` labels its first line and indents continuations to the same column, so
    one vault's multi-line outcome reads as a block::

        trailhead:    Committed: lore: sync vault
                      Pushed to origin.

    ``say_err`` always repeats the full label and writes to stderr — an error line
    must identify its vault even when stderr is captured or read on its own, where
    the continuation indent would leave it anonymous.
    """
    label = f"{name}:".ljust(width)
    state = {"first": True}

    def say(text: str) -> None:
        prefix = label if state["first"] else " " * width
        state["first"] = False
        print(f"  {prefix} {text}")

    def say_err(text: str) -> None:
        print(f"  {label} {text}", file=sys.stderr)

    return say, say_err


def _push_one(vault: Path, say, say_err, *, committed: bool) -> int:
    """Push ``vault`` to origin when there is anything to push. Always returns 0.

    Skipped silently when the vault is clean AND already in sync with its
    upstream — the common case across a multi-vault sync, where an unconditional
    push would spend one network round-trip per vault to say "Everything
    up-to-date". ``_vault_unpushed`` answers that from the local ref database.

    **A branch with no upstream is pushed with ``--set-upstream``.** A bare
    ``git push origin`` refuses outright in that state ("The current branch has no
    upstream branch", exit 128) and, crucially, never sets one either — so
    without this the vault would fail identically on every future sync while the
    error text blamed the network. Setting upstream on the first push is what
    makes the condition converge.

    A missing origin is reported only when this run committed something, so the
    per-vault line names the vault whose new commit is now unbacked; a clean
    remote-less vault stays quiet rather than re-reporting a standing condition on
    every sync (``lore status`` is the surface that reports it standing).
    """
    rc_remote, remote_url, _ = _git(vault, "remote", "get-url", "origin")
    if rc_remote != 0 or not remote_url:
        if committed:
            say("No origin remote — skipping push.")
        return 0

    if not committed and not _vault_unpushed(vault):
        return 0

    push_args = ["push", "origin"]
    if not _vault_has_upstream(vault):
        branch = _vault_head_branch(vault)
        if branch is None:
            # Detached HEAD: there is no branch to track, and guessing a refspec
            # would push to a name the operator never chose. Report, don't guess.
            say_err("notice: detached HEAD — skipping push; check out a branch and re-run")
            return 0
        push_args = ["push", "--set-upstream", "origin", branch]

    rc_push, _, stderr_push = _git(vault, *push_args)
    if rc_push != 0:
        say_err("notice: committed locally; push failed — re-run `lore sync` when online")
        say_err(f"  push error: {stderr_push}")
        return 0
    say("Pushed to origin.")
    return 0


def _sync_one(vault: Path, message: str, say, say_err) -> int:
    """Stage, commit, and push a single vault. Returns 0, or 1 on a hard failure.

    A hard failure (1) is one that leaves the vault's records uncommitted: the
    directory is absent, it is not its own git toplevel, or git refused the
    stage/commit. Push outcomes are soft — see :func:`_push_one`.
    """
    if not vault.exists():
        say_err(f"error: vault not found: {vault} — skipped")
        return 1

    if not _vault_is_git_toplevel(vault):
        say_err(
            f"error: not its own git toplevel: {vault} — skipped\n"
            "         (vault may be a subdirectory of a larger repo, or not a git repo)"
        )
        return 1

    # Probe BEFORE staging: a clean vault needs no index write, and `status
    # --porcelain` already reports untracked files, so nothing is missed.
    rc, status_out, stderr = _git(vault, "status", "--porcelain")
    if rc != 0:
        say_err(f"error: git status failed: {stderr} — skipped")
        return 1

    committed = False
    if status_out.strip():
        rc, _, stderr = _git(vault, "add", "-A")
        if rc != 0:
            say_err(f"error: git add failed: {stderr} — skipped")
            return 1
        # Never pass -S or --no-gpg-sign; honor the adopter's commit.gpgsign.
        rc, _, stderr = _git(vault, "commit", "-m", message)
        if rc != 0:
            say_err(f"error: git commit failed: {stderr} — skipped")
            return 1
        say(f"Committed: {message}")
        committed = True
    else:
        say("Nothing to commit — vault is clean.")

    return _push_one(vault, say, say_err, committed=committed)


def _select_targets(vault_filter: str | None) -> tuple[list, int]:
    """Resolve the vaults to sync. Returns ``(targets, exit_code)``.

    A non-empty ``targets`` always pairs with exit code 0. An unreadable config or
    an unknown ``--vault`` name yields ``([], 1)`` with the diagnostic already
    printed — both are refusals, never a silent fallback to syncing ``default``
    alone, which would recreate the very gap this command closes.
    """
    from ..vault import config as vault_config_mod

    targets, error = _resolve_all_vaults()
    if error is not None:
        print(f"error: {error}", file=sys.stderr)
        print("  Aborting — refusing to sync a partial vault set.", file=sys.stderr)
        return [], 1

    if vault_filter is None:
        return targets, 0

    wanted = vault_config_mod.normalize_vault_name(vault_filter)
    selected = [(n, p) for n, p in targets if n == wanted]
    if not selected:
        known = ", ".join(n for n, _ in targets) or "(none)"
        print(f"error: unknown vault: {vault_filter!r}", file=sys.stderr)
        print(f"  configured vaults: {known}", file=sys.stderr)
        return [], 1
    return selected, 0


def cmd_sync(args) -> int:
    """Sync every configured vault, or just ``--vault <name>``.

    Exit code is 1 if ANY vault hit a hard failure, 0 otherwise — the run always
    attempts every selected vault first, so one failure never strands the rest.
    """
    targets, rc = _select_targets(getattr(args, "vault", None))
    if rc != 0:
        return rc

    message = getattr(args, "message", None) or DEFAULT_SYNC_MSG
    width = max(len(name) for name, _ in targets) + 1  # + ':'

    failed: list[str] = []
    for name, vault in targets:
        say, say_err = _make_emitters(name, width)
        if _sync_one(Path(vault), message, say, say_err) != 0:
            failed.append(name)

    if failed:
        print(
            f"error: {len(failed)} of {len(targets)} vault(s) failed to sync: "
            f"{', '.join(failed)}",
            file=sys.stderr,
        )
        return 1
    return 0


def add_sync_subparser(sub) -> None:
    """Register the ``sync`` command parser."""
    p_sync = sub.add_parser(
        "sync", help="Stage, commit, and push every configured vault"
    )
    p_sync.add_argument(
        "--message", "-m", default=None,
        help=f"Commit message (default: {DEFAULT_SYNC_MSG!r})",
    )
    p_sync.add_argument(
        "--vault", default=None,
        help="Sync only this vault (default: every configured vault)",
    )
    p_sync.set_defaults(func=cmd_sync)
