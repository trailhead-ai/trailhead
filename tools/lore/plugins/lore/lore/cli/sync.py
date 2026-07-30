"""``lore sync`` — stage, commit, pull, and push EVERY configured vault.

The no-argument form covers all configured vaults; ``--vault <name>`` narrows to
one. Syncing only the ``default``-scope vault is not an option the CLI offers,
because record writes route by scope: from a repo bound to a product-scope vault,
``lore record create`` writes there while a ``default``-only sync commits nothing
of what was just written — and still prints "Committed / Pushed to origin". Every
per-vault line of output is therefore labeled with the vault it describes, so a
mismatch between where records land and where they are committed is visible at
the call site rather than discovered when a disk fails. (The one run-level line —
the closing reindex report — is unlabeled, because the index spans all vaults.)

**Sync is bidirectional: commit → pull → push, in that order.** The same vault
lives on multiple devices, so a push-only sync leaves each device blind to
records captured on the others — and once histories diverge, every push is
rejected while the error text blames the network. Local changes are committed
FIRST so the pull rebases real commits (never stashes a dirty tree), then
remote commits are integrated, then the combined history is pushed.

**Partial failure is per-vault, never fatal to the run.** A vault that is missing,
is not its own git toplevel, fails to stage/commit, or hits a rebase conflict is
reported and *skipped*; the remaining vaults are still synced and the command
exits 1 at the end. One broken vault must not be able to strand the others
uncommitted — that is the same silent-data-loss shape this module exists to close.

**Network failures stay soft** (exit 0 with a notice): a failed fetch or push
leaves the commit durable locally, and a later ``lore sync`` retries both. A
**failed rebase is hard** (exit 1) — most commonly a genuine conflict, where only
manual resolution decides which text wins. The rebase is aborted before
reporting, and the abort is verified: if the vault somehow remains mid-rebase,
the notice says so instead of promising a clean state that does not exist.

**A pull that landed commits triggers a search-index rebuild** at the end of the
run: the index is a derived projection of the vault tree, and records written on
another device have never been projected on this one — without the rebuild,
``lore search`` would silently miss exactly the records sync just fetched.

**Only the tree-mutating half runs under the vault write lock.** ``git add -A`` →
``commit`` and the pull's ``rebase`` / ``reset --hard`` are held under
:func:`lore.locking.vault_write_lock`, because a concurrent ``move_record`` is a
copy → index-repoint → delete sequence and an unlocked ``git add -A`` can observe
the record at both endpoints. ``fetch`` and ``push`` are deliberately OUTSIDE it:
they never touch the working tree, and lore's lock is blocking with no timeout —
holding it across a network round-trip would let one hung remote starve every
local writer. Hold time is therefore bounded by local git work.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .. import locking
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


#: Outcomes of :func:`_pull_one`. ``PULL_OK`` covers both "nothing to pull" and a
#: clean integration; ``PULL_OFFLINE`` means the fetch never reached the remote
#: (soft — and the push is skipped, the network already failed once this run);
#: ``PULL_FAILED`` means the integration failed (hard — most commonly a rebase
#: conflict, which only manual resolution can settle).
PULL_OK = "ok"
PULL_OFFLINE = "offline"
PULL_FAILED = "failed"


def _vault_mid_rebase(vault: Path) -> bool:
    """Return ``True`` iff a rebase is in progress — state git itself tracks.

    Probed via ``rev-parse --git-path`` rather than a hand-built ``.git/...``
    path, because in a linked worktree ``.git`` is a file and the state dirs
    live elsewhere.
    """
    for state_dir in ("rebase-merge", "rebase-apply"):
        rc, out, _ = _git(vault, "rev-parse", "--git-path", state_dir)
        # `--git-path` output is relative to the vault when not absolute.
        if rc == 0 and out and (vault / out).exists():
            return True
    return False


def _pull_one(vault: Path, say, say_err) -> tuple[str, int]:
    """Fetch ``vault`` and integrate origin's commits. Returns ``(state, commits_pulled)``.

    Quiet no-ops: no origin remote (push reports it), detached HEAD (push
    reports it), a remote that does not have this branch yet (the first push
    creates it), and an already-up-to-date upstream.

    **A branch with no upstream still pulls against ``origin/<branch>`` when that
    ref exists.** Two devices can both be at their "first push": the loser's push
    is rejected non-fast-forward, and ``--set-upstream`` never records an upstream
    on a FAILED push — so a pull keyed on ``@{u}`` alone would skip forever while
    every push keeps failing. Rebasing onto the fetched ``origin/<branch>`` is
    what lets the next push converge.

    **An unborn branch adopts the remote branch outright.** A freshly ``git
    init``-ed vault wired to an existing remote has no commits to rebase, so
    without this it would print "Nothing to commit", pull nothing, and exit 0 —
    a silent no-op on exactly the new-device shape sync exists for. The caller
    commits BEFORE pulling, so an unborn HEAD here implies a clean tree and
    ``reset --hard origin/<branch>`` cannot discard local records. When the
    remote has no branch of the same name to adopt, that is reported, not
    skipped.

    **Integration is a rebase, never a merge**: sync commits are machine-made and
    content-independent, so replaying them keeps vault history linear instead of
    accumulating a merge bubble per device pair. On failure the rebase is
    ABORTED before reporting — a mid-rebase vault would break every subsequent
    record write, which is worse than the missed pull being reported — and the
    abort is verified via :func:`_vault_mid_rebase` so the remedy printed
    matches the state the vault is actually in.
    """
    rc_remote, remote_url, _ = _git(vault, "remote", "get-url", "origin")
    if rc_remote != 0 or not remote_url:
        return PULL_OK, 0

    rc_fetch, _, stderr_fetch = _git(vault, "fetch", "origin")
    if rc_fetch != 0:
        say_err("notice: fetch failed — skipping pull and push; records stay committed locally")
        say_err(f"  fetch error: {stderr_fetch}")
        say_err("  re-run `lore sync` when online")
        return PULL_OFFLINE, 0

    if _vault_has_upstream(vault):
        upstream = "@{u}"
    else:
        branch = _vault_head_branch(vault)
        if branch is None:
            # No commit under HEAD: an unborn branch (fresh `git init`) still
            # resolves a symbolic ref; a detached HEAD does not.
            rc_sym, unborn, _ = _git(vault, "symbolic-ref", "--quiet", "--short", "HEAD")
            if rc_sym != 0 or not unborn:
                return PULL_OK, 0
            rc_ref, _, _ = _git(
                vault, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{unborn}"
            )
            if rc_ref != 0:
                say_err(
                    f"notice: vault has no commits and origin has no {unborn!r} branch — "
                    "nothing to pull; check out the branch the remote uses, or clone the vault"
                )
                return PULL_OK, 0
            rc_n, n_out, _ = _git(vault, "rev-list", "--count", f"origin/{unborn}")
            # Tree mutation — locked (the fetch above was not).
            with locking.vault_write_lock(vault):
                rc_reset, _, stderr_reset = _git(
                    vault, "reset", "--hard", f"origin/{unborn}"
                )
            if rc_reset != 0:
                say_err(f"error: could not adopt origin/{unborn}: {stderr_reset}")
                return PULL_FAILED, 0
            adopted = int(n_out) if rc_n == 0 and n_out else 0
            say(f"Pulled {adopted} commit(s) from origin.")
            return PULL_OK, adopted
        rc_ref, _, _ = _git(
            vault, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"
        )
        if rc_ref != 0:
            return PULL_OK, 0
        upstream = f"origin/{branch}"

    rc_count, count_out, _ = _git(vault, "rev-list", "--count", f"HEAD..{upstream}")
    if rc_count != 0 or not count_out or count_out == "0":
        return PULL_OK, 0
    behind = int(count_out)

    # Tree mutation — locked, so a concurrent record write can never see a
    # half-replayed tree. The abort is part of the same critical section: the
    # vault must not be observable mid-rebase.
    with locking.vault_write_lock(vault):
        rc_rebase, stdout_rebase, stderr_rebase = _git(vault, "rebase", upstream)
        if rc_rebase != 0:
            # Abort unconditionally: whether the rebase stopped on a conflict or
            # never started, the vault must come back to its pre-pull state before
            # anything is reported. Verified below rather than trusted.
            _git(vault, "rebase", "--abort")
    if rc_rebase != 0:
        say_err("error: rebase onto origin failed — pull skipped")
        say_err(f"  rebase error: {stderr_rebase or stdout_rebase}")
        if _vault_mid_rebase(vault):
            say_err(
                f"  the vault is STILL mid-rebase; run: cd {vault} && git rebase --abort, "
                "then re-run `lore sync`"
            )
        else:
            say_err(
                f"  resolve manually: cd {vault} && git pull --rebase, "
                "fix the conflicts, then re-run `lore sync`"
            )
        return PULL_FAILED, 0

    say(f"Pulled {behind} commit(s) from origin.")
    return PULL_OK, behind


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


def _stage_and_commit_one(vault: Path, message: str, say, say_err) -> tuple[int, bool]:
    """Stage + commit one vault's whole tree under its write lock.

    Returns ``(exit_code, committed)``.

    Probed twice, deliberately. The lock file is a sidecar INSIDE the vault, so
    locking a clean vault would leave an untracked ``.lore.lock`` behind and no
    vault would ever read as clean again — which would also mean a freshly ``git
    init``-ed vault always "commits" instead of taking the unborn-adoption path.
    So a clean tree is answered outside the lock, creating nothing. The probe is
    then REPEATED under the lock, because staging what the first probe saw is only
    meaningful if nothing moved in between — and a cross-vault ``move_record``
    relocating a record out of this vault is exactly what would otherwise stage a
    copy that no longer exists.

    No network runs in here — see the module docstring.
    """
    # `status --porcelain` reports untracked files too, so nothing is missed.
    rc, status_out, stderr = _git(vault, "status", "--porcelain")
    if rc != 0:
        say_err(f"error: git status failed: {stderr} — skipped")
        return 1, False
    if not status_out.strip():
        say("Nothing to commit — vault is clean.")
        return 0, False

    with locking.vault_write_lock(vault):
        rc, status_out, stderr = _git(vault, "status", "--porcelain")
        if rc != 0:
            say_err(f"error: git status failed: {stderr} — skipped")
            return 1, False

        if not status_out.strip():
            say("Nothing to commit — vault is clean.")
            return 0, False

        rc, _, stderr = _git(vault, "add", "-A")
        if rc != 0:
            say_err(f"error: git add failed: {stderr} — skipped")
            return 1, False
        # Never pass -S or --no-gpg-sign; honor the adopter's commit.gpgsign.
        rc, _, stderr = _git(vault, "commit", "-m", message)
        if rc != 0:
            say_err(f"error: git commit failed: {stderr} — skipped")
            return 1, False

    say(f"Committed: {message}")
    return 0, True


def _sync_one(vault: Path, message: str, say, say_err) -> tuple[int, int]:
    """Stage, commit, pull, and push a single vault.

    Returns ``(exit_code, commits_pulled)`` — 1 on a hard failure, and the pull
    count so the caller knows whether the derived search index is now stale.

    A hard failure (1) is one that leaves the vault's records unsynced: the
    directory is absent, it is not its own git toplevel, git refused the
    stage/commit, or the pull failed to integrate (most commonly a rebase
    conflict). Network outcomes are soft — see :func:`_pull_one` and
    :func:`_push_one`.
    """
    if not vault.exists():
        say_err(f"error: vault not found: {vault} — skipped")
        return 1, 0

    if not _vault_is_git_toplevel(vault):
        say_err(
            f"error: not its own git toplevel: {vault} — skipped\n"
            "         (vault may be a subdirectory of a larger repo, or not a git repo)"
        )
        return 1, 0

    rc, committed = _stage_and_commit_one(vault, message, say, say_err)
    if rc != 0:
        return rc, 0

    pull_state, pulled = _pull_one(vault, say, say_err)
    if pull_state == PULL_FAILED:
        return 1, 0
    if pull_state == PULL_OFFLINE:
        return 0, 0

    return _push_one(vault, say, say_err, committed=committed), pulled


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

    When any vault pulled commits, the derived search index is rebuilt ONCE at
    the end (it is global across vaults, so per-vault rebuilds would be wasted
    work). A reindex failure is soft: the pulled text is already on disk and
    wins, and ``lore search`` reports its own staleness until `lore reindex`.
    """
    targets, rc = _select_targets(getattr(args, "vault", None))
    if rc != 0:
        return rc

    message = getattr(args, "message", None) or DEFAULT_SYNC_MSG
    width = max(len(name) for name, _ in targets) + 1  # + ':'

    failed: list[str] = []
    total_pulled = 0
    for name, vault in targets:
        say, say_err = _make_emitters(name, width)
        rc_one, pulled = _sync_one(Path(vault), message, say, say_err)
        total_pulled += pulled
        if rc_one != 0:
            failed.append(name)

    if total_pulled:
        from .areas import run_reindex

        count, error = run_reindex()
        if error is not None:
            print(
                f"notice: search reindex failed after pull — run `lore reindex` ({error})",
                file=sys.stderr,
            )
        else:
            print(f"  Reindexed {count} record(s) after pull.")

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
        "sync", help="Stage, commit, pull, and push every configured vault"
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
