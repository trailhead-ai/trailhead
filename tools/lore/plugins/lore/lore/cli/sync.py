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
**failed rebase is hard** (exit 1) — most commonly a genuine conflict, which
``lore resolve <vault>`` exists to settle and which the notice names as the
remedy. The rebase is aborted before reporting, and the abort is verified: if the
vault somehow remains mid-rebase, the notice says so instead of promising a clean
state that does not exist (``lore resolve`` picks the vault up from either state,
so the remedy is the same one).

**A pull that landed commits triggers a search-index rebuild** at the end of the
run: the index is a derived projection of the vault tree, and records written on
another device have never been projected on this one — without the rebuild,
``lore search`` would silently miss exactly the records sync just fetched.

**``--pull-only`` is the non-destructive half of all this.** It fetches and
integrates origin's commits and does nothing else: no staging, no commit, no
push. Integration is further gated on a CLEAN working tree — the full sync may
rebase a dirty vault only because it commits first, and a pull-only run has no
such commit to rebase onto. A dirty vault is therefore fetched (which touches no
file) and reported as "N commit(s) behind", never rebased. That is what makes it
safe to run implicitly, which is exactly what :func:`implicit_pull` does: every
lore write path calls it first, throttled to one fetch ATTEMPT per vault per
:data:`FRESHNESS_WINDOW_SECONDS`, reporting on stderr only, and unable to fail
the write it precedes. See :func:`implicit_pull` for the three properties every
caller depends on.

**Only the tree-mutating half runs under the vault write lock.** ``git add -A`` →
``commit`` and the pull's ``rebase`` / ``reset --hard`` are held under
:func:`lore.locking.vault_write_lock`, because a concurrent ``move_record`` is a
copy → index-repoint → delete sequence and an unlocked ``git add -A`` can observe
the record at both endpoints. ``fetch`` and ``push`` are deliberately OUTSIDE it:
they never touch the working tree, and lore's lock is blocking with no timeout —
holding it across a network round-trip would let one hung remote starve every
local writer. Hold time is therefore bounded by local git work.

**The commit phase locks every SUCCESSFULLY-LOCKED target vault TOGETHER, not
one at a time.** ``cmd_sync`` stages+commits every target of the run (every
configured vault, or just ``--vault <name>`` when given) with each target's
:func:`lore.locking.vault_write_lock` entered into one shared
``contextlib.ExitStack``, in the same sorted-path order
:func:`lore.locking.vault_write_locks` itself uses, before touching any pull or
push. A per-vault-only lock would let a cross-vault ``move_record`` run its
whole copy → repoint → delete sequence strictly BETWEEN two different targets'
visits — sync would then commit the destination vault's new copy while the
source vault's own commit (dropping the now-moved record) never happens this
run, leaving the record readable as committed in both vaults until a later
sync catches up. Locking every target up front closes that window: whichever
of sync or the move starts first, the other waits for it to finish in full
before touching any of the shared vaults. Acquiring one target at a time
(rather than delegating to ``vault_write_locks`` as one opaque call) also means
a lock that fails to acquire is attributed to that exact vault, with no need to
infer it from the failing exception — see ``cmd_sync``'s own docstring.

**Caveat: a git operation that PROMPTS is unbounded.** A commit whose signing key
needs a gpg pinentry passphrase (or any git helper that waits on a human) blocks
inside the lock, and because the lock has no timeout every other vault writer
queues behind that prompt until it is answered. Under the batched commit phase
this blast radius is the WHOLE phase, not just one vault: a prompt stalling
ONE target's commit blocks every other configured vault's lock acquisition
too, since they're all held together for the phase's duration. The lock
helper's own ``lore: waiting for the vault write lock`` stderr notice —
printed on any wait past ~2 seconds — is the diagnostic that distinguishes
this from a hang.
"""
from __future__ import annotations

import sys
import time
from contextlib import ExitStack
from pathlib import Path

from .. import locking
from .common import (
    _git,
    _resolve_all_vaults,
    _resolve_lore_state_dir,
    _vault_has_upstream,
    _vault_head_branch,
    _vault_is_git_toplevel,
    _vault_mid_rebase,
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


def _fetch_origin(vault: Path, say_err, *, pull_only: bool = False) -> bool:
    """Fetch ``origin``. Returns ``True`` on success, reporting on failure.

    A failed fetch is always SOFT — the network is not the vault. The notice
    differs only in what it promises about the rest of the run: the full sync
    also skips the push (it just proved the remote unreachable), while a
    pull-only run has no push to skip and reports the consequence that actually
    matters to its caller — the vault may be stale.
    """
    rc_fetch, _, stderr_fetch = _git(vault, "fetch", "origin")
    if rc_fetch == 0:
        return True
    if pull_only:
        say_err("notice: fetch failed — the vault may be stale; nothing was integrated")
    else:
        say_err("notice: fetch failed — skipping pull and push; records stay committed locally")
    say_err(f"  fetch error: {stderr_fetch}")
    say_err("  re-run `lore sync` when online")
    return False


def _vault_is_dirty(vault: Path) -> bool:
    """Return ``True`` iff ``vault``'s working tree has changes to commit.

    Excludes the write-lock sidecar for the same reason
    :func:`_stage_and_commit_one` does: merely taking the lock create-or-opens
    ``.lore.lock``, so counting it would make every locked vault read as dirty.
    """
    rc, out, _ = _git(
        vault, "status", "--porcelain", "--", ".", f":(exclude){locking.VAULT_LOCK_NAME}"
    )
    return rc != 0 or bool(out.strip())


def _upstream_ref(vault: Path) -> "str | None":
    """Return the ref ``vault``'s HEAD should be measured against, or ``None``.

    Mirrors :func:`_pull_one`'s own upstream resolution — ``@{u}`` when the
    branch tracks one, else ``origin/<branch>`` when the remote already has that
    branch — minus the unborn-branch adoption path, which is an integration
    decision rather than a measurement.
    """
    if _vault_has_upstream(vault):
        return "@{u}"
    branch = _vault_head_branch(vault)
    if branch is None:
        return None
    rc, _, _ = _git(vault, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}")
    return f"origin/{branch}" if rc == 0 else None


def _commits_behind(vault: Path) -> int:
    """Return how many commits ``vault``'s upstream has that HEAD does not."""
    ref = _upstream_ref(vault)
    if ref is None:
        return 0
    rc, out, _ = _git(vault, "rev-list", "--count", f"HEAD..{ref}")
    return int(out) if rc == 0 and out.isdigit() else 0


def _pull_only_one(vault: Path, say, say_err) -> tuple[str, int]:
    """Fetch ``vault`` and integrate ONLY if that costs the working tree nothing.

    The non-destructive half of :func:`_pull_and_push_one`: no staging, no
    commit, no push. Integration is delegated to :func:`_pull_one` (so the
    rebase, its abort, and the ``lore resolve`` remedy all have exactly one
    implementation) but is gated on a CLEAN tree.

    **A dirty tree is reported, never rebased.** The full sync can rebase a dirty
    vault because it commits first; a pull-only run has no such commit to rebase
    onto, and ``git rebase`` against uncommitted changes either refuses or
    (worse, for a caller that asked for nothing destructive) stashes them. So the
    fetch still runs — it touches no file — and the operator is told how far
    behind the vault is, which is the whole actionable content of the pull they
    did not get.
    """
    rc_remote, remote_url, _ = _git(vault, "remote", "get-url", "origin")
    if rc_remote != 0 or not remote_url:
        return PULL_OK, 0

    if not _fetch_origin(vault, say_err, pull_only=True):
        return PULL_OFFLINE, 0

    if _vault_is_dirty(vault):
        behind = _commits_behind(vault)
        if behind:
            say_err(
                f"notice: {behind} commit(s) behind origin — the working tree is not "
                "clean, so nothing was integrated; run `lore sync` to commit and pull"
            )
        return PULL_OK, 0

    return _pull_one(vault, say, say_err, already_fetched=True)


def _pull_one(vault: Path, say, say_err, *, already_fetched: bool = False) -> tuple[str, int]:
    """Fetch ``vault`` and integrate origin's commits. Returns ``(state, commits_pulled)``.

    ``already_fetched`` skips the fetch for a caller that has already run one this
    invocation (:func:`_pull_only_one`, which must fetch BEFORE it knows whether
    the tree is clean enough to integrate) — a second fetch would be a wasted
    network round-trip against a ref database that cannot have moved since.

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

    if not already_fetched and not _fetch_origin(vault, say_err):
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
        from . import resolve_state as resolve_state_mod

        remedy = resolve_state_mod.resolve_remedy(vault)
        if _vault_mid_rebase(vault):
            say_err(f"  the vault is STILL mid-rebase; to settle it, {remedy}")
        else:
            say_err(f"  to settle the conflict, {remedy}")
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

    Probed twice, deliberately, though :func:`cmd_sync` — this function's only
    caller — already holds every target's lock (lock file created) before
    calling in, so neither probe here ever finds a vault genuinely un-locked;
    the exclusion above is what keeps that pre-existing lock file from making
    an otherwise-clean vault read as dirty regardless. The REPEAT under
    ``with locking.vault_write_lock(vault)`` below (a reentrant no-op against
    the lock cmd_sync already holds) exists because staging what the first
    probe saw would only be safe if this vault's tree can't change between the
    two reads — true for a standalone caller taking the lock fresh here, and
    still asserted defensively even though cmd_sync's own batched acquisition
    already rules out a concurrent cross-vault ``move_record`` doing exactly
    that in between.

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

    Returns ``(exit_code, commits_pulled)``. Kept separate from
    :func:`_stage_and_commit_one` so :func:`cmd_sync` can run every target's
    stage+commit phase under ONE combined lock (see its docstring) before
    running each target's network-touching pull/push tail separately, one
    vault at a time.
    """
    pull_state, pulled = _pull_one(vault, say, say_err)
    if pull_state == PULL_FAILED:
        return 1, 0
    if pull_state == PULL_OFFLINE:
        return 0, 0

    return _push_one(vault, say, say_err, committed=committed), pulled


# ---------------------------------------------------------------------------
# The implicit pull — freshness-window-throttled, stderr-only, never fatal
# ---------------------------------------------------------------------------

#: How long a vault stays "fresh enough" after a fetch ATTEMPT. Every write path
#: pulls implicitly, and a write is not a network operation the operator asked
#: for — five minutes keeps a multi-device vault current within a working
#: rhythm while capping the cost of a burst of writes at one fetch per vault.
FRESHNESS_WINDOW_SECONDS = 300

#: Freshness-stamp directory under ``state_dir("lore")``.
FETCH_STAMP_DIRNAME = "fetch"


def fetch_stamp_root() -> Path:
    """Return ``state_dir("lore")/fetch`` — the freshness-stamp directory."""
    return _resolve_lore_state_dir() / FETCH_STAMP_DIRNAME


def fetch_stamp_path(vault_root: str | Path) -> Path:
    """Return the freshness stamp for *vault_root*, confined to the stamp root.

    Keyed on ``Path(vault_root).name`` and confined with
    ``layers.assert_within_root`` — the same shape ``cli.resolve_state.marker_path``
    uses for its own machine-local per-vault file, so a symlink planted at the
    stamp's name cannot redirect the write outside the stamp root.

    Raises:
        layers.LayerConfinementError: if the stamp path escapes the stamp root.
    """
    from ..vault import layers as layers_mod

    root = fetch_stamp_root()
    candidate = root / Path(vault_root).name
    layers_mod.assert_within_root(candidate, root)
    return candidate


def _fetch_is_fresh(vault_root: str | Path) -> bool:
    """Return ``True`` iff a fetch was ATTEMPTED against *vault_root* recently.

    Attempted, not succeeded: an offline session must pay one network timeout
    per window, not one per write. The stamp is therefore written before the
    fetch runs and never rolled back on failure.
    """
    try:
        age = time.time() - fetch_stamp_path(vault_root).stat().st_mtime
    except OSError:
        return False
    return 0 <= age < FRESHNESS_WINDOW_SECONDS


def _stamp_fetch_attempt(vault_root: str | Path) -> None:
    """Record that a fetch is being attempted against *vault_root*, now."""
    path = fetch_stamp_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def implicit_pull(vault_root: str | Path) -> None:
    """Run a throttled, stderr-only ``--pull-only`` for a vault about to be written.

    The cadence half of sync: every write path calls this so a multi-device vault
    converges on write instead of only when someone remembers to sync, WITHOUT a
    write ever becoming a network operation the caller has to reason about.
    Three properties make that safe, and every caller depends on all three:

    - **Advisory.** Nothing here can fail the write. A conflict, an unreachable
      remote, a broken git state — all are reported and stepped over; the pull is
      an optimization, and the write's own success is unrelated to it.
    - **Silent on stdout.** All output goes to stderr, because ``record create``'s
      stdout is exactly one ``RECORD_ID`` line that callers parse.
    - **Throttled on ATTEMPT.** See :func:`_fetch_is_fresh` — an offline session
      pays one timeout per window per vault, not one per write.

    MUST be called before the caller takes any vault write lock or opens an index
    transaction: this fetches (network, unbounded) and may reindex (which takes
    every configured vault's lock), neither of which belongs inside a write's own
    critical section.
    """
    vault = Path(vault_root)
    name = vault.name

    def say(text: str) -> None:
        print(f"  lore: {name}: {text}", file=sys.stderr)

    try:
        if _fetch_is_fresh(vault):
            return
        _stamp_fetch_attempt(vault)
        state, pulled = _pull_only_one(vault, say, say)
    except Exception as exc:  # noqa: BLE001 — advisory: a write must not fail on this
        print(f"  lore: {name}: notice: implicit pull skipped ({exc})", file=sys.stderr)
        return

    if state != PULL_OK or not pulled:
        return

    from .areas import run_reindex

    count, error = run_reindex()
    if error is not None:
        print(
            f"  lore: {name}: notice: search reindex failed after pull — "
            f"run `lore reindex` ({error})",
            file=sys.stderr,
        )
    else:
        print(f"  lore: {name}: Reindexed {count} record(s) after pull.", file=sys.stderr)


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

    Locks are acquired ONE TARGET AT A TIME into a single ``ExitStack`` (sorted
    order, same as ``vault_write_locks``), not via that helper's one opaque
    all-or-nothing call — a target whose lock fails to acquire is skipped
    (attributed exactly, no exception-message guessing needed) while every
    OTHER target still gets locked together and committed; it never strands
    the rest of the batch just because one vault's lock is broken.

    When any vault pulled commits, the derived search index is rebuilt ONCE at
    the end (it is global across vaults, so per-vault rebuilds would be wasted
    work). A reindex failure is soft: the pulled text is already on disk and
    wins, and ``lore search`` reports its own staleness until `lore reindex`.

    **This is also the flow ``lore flush``'s sync tail reuses.** Every input is
    read off *args* with ``getattr`` — ``vault``, ``message``, ``pull_only`` —
    and nothing here touches the parser, so the tail calls this function directly
    with a small namespace object, once per writable vault (``cli.flush``'s
    ``_flush_sync_tail``; per-vault, because a bare run would cover ``shared:
    true`` vaults too and a shared vault must never be committed or pushed by an
    agent-actuated write). Keep it that way: coupling this function to argparse,
    or making any of those three attributes mandatory, breaks that caller.
    """
    targets, rc = _select_targets(getattr(args, "vault", None))
    if rc != 0:
        return rc

    message = getattr(args, "message", None) or DEFAULT_SYNC_MSG
    pull_only = bool(getattr(args, "pull_only", False))
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

    # A lock acquisition failure (e.g. a read-only vault root, or the lock
    # path occupied by something other than a file) must not crash the whole
    # run, AND must not strand every OTHER target unattempted just because
    # one vault's lock is broken. Acquiring each target's lock ONE AT A TIME
    # into a single shared ExitStack (rather than delegating to
    # ``locking.vault_write_locks`` as one opaque all-or-nothing call) gives
    # exact attribution for free — the vault whose lock just failed to
    # acquire is whichever one this loop iteration is on, no need to infer it
    # from ``OSError.filename`` — while still holding every SUCCESSFULLY
    # locked target together for the whole commit phase, in the same sorted
    # order ``vault_write_locks`` itself uses, so this still can't deadlock
    # against a cross-vault ``move_record``.
    sorted_targets = sorted(valid_targets, key=lambda t: locking.vault_lock_sort_key(t[1]))
    if pull_only:
        # Nothing to stage, nothing to commit — so nothing here needs a lock.
        # ``_pull_only_one`` still takes the write lock around its own rebase.
        for name, _vault_path in sorted_targets:
            commit_rc[name] = 0
        sorted_targets = []
    try:
        with ExitStack() as stack:
            locked: list[tuple[str, Path]] = []
            for name, vault_path in sorted_targets:
                try:
                    stack.enter_context(locking.vault_write_lock(vault_path))
                except OSError as exc:
                    say_err = say_map[name][1]
                    say_err(f"error: failed to acquire vault lock: {exc} — skipped")
                    commit_rc[name] = 1
                    continue
                locked.append((name, vault_path))

            for name, vault_path in locked:
                say, say_err = say_map[name]
                rc_one, committed = _stage_and_commit_one(vault_path, message, say, say_err)
                commit_rc[name] = rc_one
                committed_map[name] = committed
    except OSError as exc:
        # Every target that reached the point of being locked or committed
        # above already has a recorded commit_rc entry (set as the loops run,
        # not just at the end) — this can only fire releasing a lock during
        # the ExitStack's own unwind, after all guarded work already landed.
        # The work is safe; only the unlock itself is in question, and that's
        # not attributable to any one target's commit outcome, so leave
        # commit_rc alone and just surface it.
        print(f"notice: error releasing a vault lock after sync: {exc}", file=sys.stderr)

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
        if pull_only:
            state, pulled = _pull_only_one(Path(vault), say, say_err)
            rc_one = 1 if state == PULL_FAILED else 0
        else:
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
    p_sync.add_argument(
        "--pull-only", action="store_true",
        help="Fetch and integrate origin only — never stage, commit, or push",
    )
    p_sync.set_defaults(func=cmd_sync)
