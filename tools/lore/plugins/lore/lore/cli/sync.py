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

**The commit phase locks every target vault TOGETHER, not one at a time.**
``cmd_sync`` stages+commits every TARGET of the run (every configured vault,
or just ``--vault <name>`` when given) under one combined
:func:`lore.locking.vault_write_locks` acquisition before touching any pull or
push. A per-vault-only lock would let a cross-vault ``move_record`` run its
whole copy → repoint → delete sequence strictly BETWEEN two different targets'
visits — sync would then commit the destination vault's new copy while the
source vault's own commit (dropping the now-moved record) never happens this
run, leaving the record readable as committed in both vaults until a later
sync catches up. Locking every target up front closes that window: whichever
of sync or the move starts first, the other waits for it to finish in full
before touching any of the shared vaults.

**Caveat: a git operation that PROMPTS is unbounded.** A commit whose signing key
needs a gpg pinentry passphrase (or any git helper that waits on a human) blocks
inside the lock, and because the lock has no timeout every other vault writer
queues behind that prompt until it is answered. The lock helper's own
``lore: waiting for the vault write lock`` stderr notice — printed on any wait
past ~2 seconds — is the diagnostic that distinguishes this from a hang.
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
    multiple ``say`` calls against ONE emitter pair read as a single block::

        trailhead:    Committed: lore: sync vault
                      Pushed to origin.

    ``cmd_sync`` gets that block shape only WITHIN one phase: it calls
    ``_make_emitters`` once for the batched commit phase (shared across every
    target) and once more, freshly, per vault for the pull/push phase — a
    vault's commit line and its push line therefore come from two different
    emitter pairs, each independently labeling its own first line, rather than
    one continuous block spanning both phases.

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

    **The lock file itself is excluded from every ``status``/``add`` call here**,
    not just left to whatever ``.gitignore`` the vault happens to carry.
    ``config.installer``'s ``scaffold_gitignore`` covers real vaults, but this
    function must not depend on that: without the exclusion, the mere act of
    taking the write lock — which create-or-opens ``<vault_root>/.lore.lock`` —
    would make an otherwise-clean, ungitignored vault read as dirty and "commit"
    nothing but its own lock sidecar. That would also break a freshly ``git
    init``-ed vault's unborn-adoption path in :func:`_pull_one`, which assumes an
    unborn HEAD implies a clean tree. Excluding the lock file makes that true
    unconditionally, which is what lets :func:`cmd_sync` safely hold every
    configured vault's lock for its ENTIRE multi-target commit phase (see its
    docstring) instead of only locking vaults already known to be dirty.

    The ``status`` probes use a ``:(exclude)`` pathspec for this — safe there,
    since ``status`` never complains about ignored paths. ``add`` can't use the
    same trick: git treats an *explicitly named* pathspec — even an exclude-only
    one with no positive match — as an explicit request, and errors out ("The
    following paths are ignored…") the moment that name also happens to be
    gitignored (as ``.lore.lock`` is, in every scaffolded vault). So staging
    instead runs a bare ``git add -A`` (no pathspec — the one shape where git
    silently skips ignored paths instead of erroring on them) and then unstages
    the lock file explicitly with ``git reset``, which never errors whether or
    not the path was staged. Net effect is the same: the lock file is never part
    of the commit, whether or not the vault's own ``.gitignore`` already excludes it.

    Probed twice, deliberately. A clean tree is answered by the FIRST probe,
    before ``with locking.vault_write_lock(vault)`` below runs — skipping
    straight to "clean" without this call itself creating the lock file for a
    vault it ends up doing nothing to. (:func:`cmd_sync`'s batched multi-target
    phase is the one exception: it already holds every target's lock, lock
    file created, before calling this function at all — the exclusion above is
    what keeps that pre-existing lock file from making an otherwise-clean vault
    read as dirty regardless.) The probe is then REPEATED under the lock,
    because staging what the first probe saw is only meaningful if nothing
    moved in between — and a cross-vault ``move_record`` relocating a record
    out of this vault is exactly what would otherwise stage a copy that no
    longer exists.

    No network runs in here — see the module docstring.
    """
    lock_exclude = f":(exclude){locking.VAULT_LOCK_NAME}"

    # `status --porcelain` reports untracked files too, so nothing is missed —
    # except the lock sidecar itself, deliberately (see above).
    rc, status_out, stderr = _git(vault, "status", "--porcelain", "--", ".", lock_exclude)
    if rc != 0:
        say_err(f"error: git status failed: {stderr} — skipped")
        return 1, False
    if not status_out.strip():
        say("Nothing to commit — vault is clean.")
        return 0, False

    with locking.vault_write_lock(vault):
        rc, status_out, stderr = _git(vault, "status", "--porcelain", "--", ".", lock_exclude)
        if rc != 0:
            say_err(f"error: git status failed: {stderr} — skipped")
            return 1, False

        if not status_out.strip():
            say("Nothing to commit — vault is clean.")
            return 0, False

        # Bare `-A`, no pathspec — see the docstring for why an explicit
        # exclude pathspec errors here (unlike for `status`).
        rc, _, stderr = _git(vault, "add", "-A")
        if rc != 0:
            say_err(f"error: git add failed: {stderr} — skipped")
            return 1, False
        # Unstage the lock sidecar if it got swept up (i.e. wasn't already
        # gitignored). `reset` never errors on an ignored or never-staged path.
        rc, _, stderr = _git(vault, "reset", "-q", "--", locking.VAULT_LOCK_NAME)
        if rc != 0:
            say_err(f"error: git reset (unstaging lock file) failed: {stderr} — skipped")
            return 1, False
        # Never pass -S or --no-gpg-sign; honor the adopter's commit.gpgsign.
        rc, _, stderr = _git(vault, "commit", "-m", message)
        if rc != 0:
            say_err(f"error: git commit failed: {stderr} — skipped")
            return 1, False

    say(f"Committed: {message}")
    return 0, True


def _pull_and_push_one(
    vault: Path, say, say_err, *, committed: bool
) -> tuple[int, int]:
    """Pull then push one vault, given whether this run just committed to it.

    Returns ``(exit_code, commits_pulled)``. Split out of the old ``_sync_one``
    so :func:`cmd_sync` can run every target's stage+commit phase under ONE
    combined lock (see its docstring) before running each target's
    network-touching pull/push tail separately, one vault at a time.
    """
    pull_state, pulled = _pull_one(vault, say, say_err)
    if pull_state == PULL_FAILED:
        return 1, 0
    if pull_state == PULL_OFFLINE:
        return 0, 0

    return _push_one(vault, say, say_err, committed=committed), pulled


def _sync_one(vault: Path, message: str, say, say_err) -> tuple[int, int]:
    """Stage, commit, pull, and push a single vault.

    Returns ``(exit_code, commits_pulled)`` — 1 on a hard failure, and the pull
    count so the caller knows whether the derived search index is now stale.

    A hard failure (1) is one that leaves the vault's records unsynced: the
    directory is absent, it is not its own git toplevel, git refused the
    stage/commit, or the pull failed to integrate (most commonly a rebase
    conflict). Network outcomes are soft — see :func:`_pull_one` and
    :func:`_push_one`.

    Used directly by single-vault tests/callers; :func:`cmd_sync` no longer
    calls this for its multi-target run (see its docstring for why the
    stage+commit phase there is batched under one combined lock instead).
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

    return _pull_and_push_one(vault, say, say_err, committed=committed)


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

    **The stage+commit phase runs under every target's write lock held
    TOGETHER, not one target at a time.** A single ``lore sync`` run visits
    several vaults sequentially; locking each one independently (the original
    shape) only keeps a concurrent writer from interleaving WITHIN one
    vault's own stage/commit call. It does nothing to stop a cross-vault
    ``move_record`` (which holds source+destination together — see
    ``locking.vault_write_locks`` and ``record.store.move_record``) from
    running its ENTIRE copy -> repoint -> delete sequence strictly between
    two different targets' visits — e.g. sync sees the source vault clean
    before the move starts, the move completes, then sync commits the
    destination vault. That leaves the source vault's own commit (the one
    that would drop the now-deleted record) never taken this run, while the
    destination vault's commit for the SAME record already landed — the
    record then reads as committed in both vaults until a later sync happens
    to revisit the source. Acquiring every target's lock up front (sorted
    order, matching ``move_record``'s own discipline, so this can never
    deadlock against it) closes that: a move starting after this phase begins
    must wait for the WHOLE phase (every target) to finish, and a move
    already in flight blocks the phase from starting until it releases both
    its locks — either way, every target's post-move state is only ever
    visible together, never split across this run. Pull/push stay outside it
    and per-vault, one at a time, exactly as before — see
    :func:`_pull_and_push_one`.

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

    say_map = {name: _make_emitters(name, width) for name, _ in targets}
    commit_rc: dict[str, int] = {}
    committed_map: dict[str, bool] = {}
    valid_targets: list[tuple[str, Path]] = []

    for name, vault in targets:
        say, say_err = say_map[name]
        vault_path = Path(vault)
        if not vault_path.exists():
            say_err(f"error: vault not found: {vault_path} — skipped")
            commit_rc[name] = 1
            continue
        if not _vault_is_git_toplevel(vault_path):
            say_err(
                f"error: not its own git toplevel: {vault_path} — skipped\n"
                "         (vault may be a subdirectory of a larger repo, or not a git repo)"
            )
            commit_rc[name] = 1
            continue
        valid_targets.append((name, vault_path))

    # A lock acquisition failure (e.g. a read-only vault root, or the lock path
    # occupied by something other than a file) must not crash the whole run,
    # AND must not strand every OTHER target unattempted just because one
    # vault's lock is broken — both would violate this function's own
    # exit-code contract above ("one failure never strands the rest"). So a
    # failed acquisition is attributed to the one vault whose lock path it
    # names (``OSError.filename`` — set by the failing ``open``/``mkdir`` call
    # inside ``locking._flock``) and the batch is retried without it; a
    # failure that can't be attributed to a specific target fails the whole
    # remaining batch rather than looping forever.
    remaining = list(valid_targets)
    while remaining:
        try:
            with locking.vault_write_locks(*(str(v) for _, v in remaining)):
                for name, vault_path in remaining:
                    say, say_err = say_map[name]
                    rc_one, committed = _stage_and_commit_one(vault_path, message, say, say_err)
                    commit_rc[name] = rc_one
                    committed_map[name] = committed
            break
        except OSError as exc:
            # The for-loop body may already have run to completion and set
            # commit_rc/committed_map for every target in `remaining` before
            # this OSError fired on the way OUT of the `with` (e.g. releasing
            # one vault's lock raised after every commit had already landed).
            # Never clobber an outcome the loop body already recorded — only
            # a target still unrecorded genuinely failed to lock.
            unrecorded = [t for t in remaining if t[0] not in commit_rc]
            if not unrecorded:
                # Every target in this batch already has a recorded outcome —
                # the loop body ran to completion, so this OSError fired
                # releasing a lock AFTER the commit(s) it guarded already
                # landed. The work is safe; only the unlock itself is in
                # question, and that's not attributable to any one target's
                # commit outcome, so leave commit_rc alone and just surface it.
                print(
                    f"notice: error releasing a vault lock after sync: {exc}",
                    file=sys.stderr,
                )
                break
            bad_filename = str(getattr(exc, "filename", "") or "")
            bad = next(
                (t for t in unrecorded if bad_filename and str(t[1]) in bad_filename),
                None,
            )
            if bad is None:
                # Can't tell which target's lock broke — fail every
                # still-unrecorded vault rather than guessing (or retrying
                # forever on the same unattributed error).
                for name, _vault_path in unrecorded:
                    _say, say_err = say_map[name]
                    say_err(f"error: failed to acquire vault lock: {exc} — skipped")
                    commit_rc[name] = 1
                break
            name, _vault_path = bad
            _say, say_err = say_map[name]
            say_err(f"error: failed to acquire vault lock: {exc} — skipped")
            commit_rc[name] = 1
            remaining = [t for t in unrecorded if t[0] != name]

    failed: list[str] = []
    total_pulled = 0
    for name, vault in targets:
        if commit_rc.get(name, 1) != 0:
            failed.append(name)
            continue
        # Fresh emitters for the pull/push phase: the commit phase above may
        # already have printed this vault's labeled first line, and reusing
        # that closure's `state["first"]` here would print an unlabeled
        # continuation instead of a new labeled block (see `_make_emitters`).
        say, say_err = _make_emitters(name, width)
        rc_one, pulled = _pull_and_push_one(
            Path(vault), say, say_err, committed=committed_map.get(name, False)
        )
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
