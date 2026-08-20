"""``lore flush`` — flip dirty sessions clean (current / all / KQL-scoped) + commit."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .. import locking
from .common import (
    DRIFT_SYNC_FIXABLE,
    _add_session_selectors,
    _git,
    _partition_writable_vaults,
    _resolve_all_vaults,
    _resolve_all_vaults_strict,
    _vault_drift,
    _vault_has_upstream,
    _vault_is_git_toplevel,
    _vault_mid_rebase,
)
from .resolve_state import refusal_notice, resolve_remedy, vault_is_resolving
from .session import _open_session_index, _resolve_session_key


def _flush_commit(vault: Path, key: str, *, push: bool = True) -> int:
    """Stage the flushed session record's EXPLICIT path(s) + commit in ONE commit.

    Never `git add -A` — only `session/<key>.json` (the flipped sidecar) and its
    `.md` body are staged, so unrelated dirty vault files are never swept into
    *this* commit. The staged-index gate (`git diff --cached --quiet`) makes an
    already-clean flush a no-op rather than a failed empty commit. Returns exit
    code.

    **Scope of that guarantee: this commit, not the command.** A default `lore
    flush` ends with :func:`_flush_sync_tail`, a full `lore sync` that DOES
    `git add -A` every writable vault — so the unrelated dirty files this commit
    refuses to sweep are committed by the tail, in their own separate sync
    commit. What survives is the property the guarantee exists for: the session
    commit is a clean, reviewable unit holding exactly the flushed record.
    `lore flush --no-sync` runs no tail, and is the form under which the flush
    command as a whole still touches nothing but the session record.

    The per-session *commit* is the deliberate atomicity unit and always runs here,
    under the session-key lock (see :func:`_stage_and_commit_session`). The *push*
    is not: both flush paths pass ``push=False`` and push themselves once the lock
    is released, because a push is a network round-trip and lore's lock is blocking
    with no timeout. ``push=True`` therefore stays available for a caller that owns
    no lock span of its own and wants commit-then-push in one call; it pushes only
    after the lock is released either way. A skipped push is harmless: the commit is
    durable locally and a re-run (or `lore sync`) pushes it.
    """
    rc, committed = _stage_and_commit_session(vault, key)
    if rc == 0 and committed and push:
        return _flush_push(vault)
    return rc


def _stage_and_commit_session(vault: Path, key: str) -> tuple[int, bool]:
    """Stage + commit ``session/<key>.{json,md}`` under TWO locks.

    Returns ``(exit_code, committed)``.

    :func:`lore.locking.session_write_lock` — the (vault, session-key)
    granularity every session write already uses — is the outer one. It is
    reentrant per thread, so the callers that hold it across ``flush_session`` →
    this commit (making the flip and the commit one unit, so a concurrent
    ``session candidate`` cannot leave a ``dirty`` sidecar staged inside the flush
    commit) are not blocked by themselves.

    :func:`lore.locking.vault_write_lock` is taken **inside** it, for the staging
    span only. Staging is the git index, which is vault-wide state, not
    session-scoped: ``lore sync`` holds the vault lock across its own ``git add
    -A`` → ``commit``, and without this lock that ``add -A`` could land between
    this function's ``add`` and its ``commit`` and sweep the whole dirty tree into
    the flush commit — which is the session commit's own scope guarantee (see
    :func:`_flush_commit`), not the command's: flush's sync tail commits the rest
    deliberately, in a commit of its own, never inside this one. The
    acquisition order is fixed **session-key → vault**; no lore path takes them
    the other way round, so the pair cannot deadlock.

    **Accepted residual:** ``capture_candidate`` writes a session's body and
    sidecar under the session-key lock ONLY, never the vault lock. A ``lore sync``
    running concurrently can therefore take the vault lock and ``git add -A``
    between that pair's two writes, staging a half-updated session pair. This is
    pre-existing and out of this lock's scope — closing it means putting session
    writes under the vault lock too, which would serialize every session capture
    against every vault writer. The next capture or flush rewrites both files, so
    the effect is a momentarily-stale commit, not lost data.

    The push is deliberately left to the caller / :func:`_flush_commit`, outside
    both locks — a network round-trip must never run under a no-timeout flock.
    """
    if not vault.exists() or not _vault_is_git_toplevel(vault):
        print("notice: vault is not its own git toplevel — skipping commit.", file=sys.stderr)
        return 0, False

    with locking.session_write_lock(vault, key), locking.vault_write_lock(vault):
        session_dir = vault / "session"
        paths = []
        for suffix in (".json", ".md"):
            p = session_dir / f"{key}{suffix}"
            if p.exists():
                paths.append(str(p))
        if not paths:
            print("Nothing to commit — no session record on disk.")
            return 0, False

        rc, _, stderr = _git(vault, "add", "--", *paths)
        if rc != 0:
            print(f"error: git add failed: {stderr}", file=sys.stderr)
            return 1, False

        rc, _, _ = _git(vault, "diff", "--cached", "--quiet")
        if rc == 0:
            print("Nothing to commit — index is clean.")
            return 0, False

        message = f"session: flush {key}"
        rc, _, stderr = _git(vault, "commit", "-m", message)
        if rc != 0:
            print(f"error: git commit failed: {stderr}", file=sys.stderr)
            return 1, False

    print(f"Committed: {message}")
    return 0, True


def _flush_push(vault: Path) -> int:
    """Push committed flush(es) to origin once, if an origin remote exists.

    Split out of :func:`_flush_commit` so a batch flush probes the remote and
    pushes ONCE after every per-session commit lands, rather than N times inside
    the per-session loop (efficiency follow-up). A missing origin, or a push that
    fails (offline), is a soft outcome (exit 0): every flush is already committed
    locally and `lore sync` — or the next successful flush — re-pushes when online.
    Returns exit code (always 0; a push failure does not fail the flush).

    **Only the `--no-sync` path calls this.** A default flush ends with
    :func:`_flush_sync_tail`, whose per-vault `lore sync` already pushes; running
    both would be two round-trips for one outcome. Both flush paths therefore
    take their `push` flag from the tail's own opt-out, so exactly one of the two
    pushes on any given run.
    """
    rc_remote, remote_url, _ = _git(vault, "remote", "get-url", "origin")
    if rc_remote != 0 or not remote_url:
        print("No origin remote — skipping push.")
        return 0
    rc_push, _, stderr_push = _git(vault, "push", "origin")
    if rc_push != 0:
        print("notice: committed locally; push failed — re-run `lore sync` when online", file=sys.stderr)
        print(f"  push error: {stderr_push}", file=sys.stderr)
        return 0
    print("Pushed to origin.")
    return 0


def _report_unsynced_vaults(
    vaults: "list[tuple[str, Path]] | None" = None, *, file=None
) -> None:
    """Print a notice naming every vault in *vaults* left holding uncommitted work.

    **Two callers, two scopes, both preserved exactly.** `--no-sync` calls this
    with no arguments: *vaults* resolves to EVERY configured vault (the old,
    unpartitioned shape, still printed to stdout) — the full report that
    substitutes for the sync tail it opted out of.

    A default flush ends with :func:`_flush_sync_tail`, which SYNCS every
    writable vault instead of naming it — leaving nothing for that half of the
    report. But the tail structurally never touches a `shared: true` vault (the
    shared-vault write gate — see :func:`_flush_sync_tail`), so a shared vault
    left holding unsynced work is never mentioned by anything on the default
    path either, unless this notice runs a second time, scoped to shared vaults
    ONLY, after the tail (`cmd_flush` passes `file=sys.stderr` there, matching
    every other post-tail notice). The two scopes never overlap — the tail's
    writable partition and this call's shared partition are exactly
    complementary — so calling both is not double-reporting the same vault.

    `_flush_commit` stages the flushed session record's EXPLICIT paths and nothing
    else — deliberately, so unrelated dirty files are never swept into a session
    commit. The consequence is that the RECORDS a flush produces are not committed
    by the flush: the evaluation step creates them with `lore record create`, which
    routes by scope, so they can land in a product/repo vault while the session
    record commits in `default`. A flush that printed "Committed" and stopped
    therefore read as "everything is saved" while the records it existed to durably
    capture sat untracked.

    Reporting rather than committing is what `--no-sync` MEANS: that flush still
    touches only what it staged, and the operator gets the one command that covers
    the rest. (A default flush covers its writable vaults instead of naming them —
    :func:`_flush_sync_tail` — and can only ever NAME its shared ones, since it
    must never write to them.)

    **Only ``DRIFT_SYNC_FIXABLE`` findings are reported**, because the notice's
    whole payload is "run `lore sync`". A standing condition sync cannot fix — a
    vault with no origin remote, which is a legitimate deliberate configuration —
    would otherwise attach that remedy to every flush forever, and a notice that
    fires unconditionally with a no-op remedy is one the operator learns to skip.
    ``lore status`` is the surface that reports standing conditions, with the
    remedy that actually applies. Silent when nothing is sync-fixable.
    """
    if vaults is None:
        vaults, error = _resolve_all_vaults()
        if error is not None:
            print(f"notice: cannot check vault sync state — {error}", file=sys.stderr)
            return

    drifted = []
    for name, path in vaults:
        actionable = [
            desc for code, desc in _vault_drift(Path(path)) if code in DRIFT_SYNC_FIXABLE
        ]
        if actionable:
            drifted.append((name, actionable))
    if not drifted:
        return

    out = file or sys.stdout
    print("notice: vault(s) still holding unsynced work — run `lore sync`:", file=out)
    for name, descriptions in drifted:
        print(f"  {name}: {'; '.join(descriptions)}", file=out)


def _vault_diverged(vault: Path) -> bool:
    """Return ``True`` iff *vault* holds local commits AND is behind its upstream.

    Read purely from the local ref database, and only ever consulted right after
    a sync attempt on *vault* has already fetched: at that moment "still ahead
    and still behind" means the rebase did not integrate, which is the rebase
    conflict :func:`_sync_tail_notice` needs to distinguish from every other way
    a sync can fail. A commit failure, a missing directory, or a broken lock
    leaves the vault un-fetched and un-diverged, so none of them answer ``True``
    here and none of them are offered the ``lore resolve`` remedy.
    """
    if not _vault_has_upstream(vault):
        return False
    rc_a, ahead, _ = _git(vault, "rev-list", "--count", "@{u}..HEAD")
    rc_b, behind, _ = _git(vault, "rev-list", "--count", "HEAD..@{u}")
    if rc_a != 0 or rc_b != 0:
        return False
    return ahead not in ("", "0") and behind not in ("", "0")


def _sync_tail_notice(name: str, vault: Path) -> None:
    """Report a vault the sync tail could not finish — soft, and never fatal.

    The flush itself already succeeded and its commits are durable locally, so
    the tail's exit code is deliberately dropped (see :func:`_flush_sync_tail`);
    what must not be dropped is WHICH remedy applies. A vault left mid-rebase, or
    left diverged by a rebase that aborted, is settled by ``lore resolve`` and by
    nothing else — prescribing ``lore sync`` there would send the operator back
    into the command that just refused. Everything else the tail can fail on
    (a missing vault directory, a lock it could not take, a commit that failed)
    is retried by a later ``lore sync``.
    """
    if _vault_mid_rebase(vault) or _vault_diverged(vault):
        remedy = resolve_remedy(vault)
    else:
        remedy = "re-run `lore sync`"
    print(
        f"notice: the flush is committed locally, but syncing vault {name!r} did "
        f"not complete — to settle it, {remedy}.",
        file=sys.stderr,
    )


def _flush_sync_tail() -> None:
    """Close the flush with the FULL sync flow — commit, pull, push — per vault.

    Flush's own commit covers the session record alone, which is the right scope
    for that commit and the wrong scope for the command: the records a flush
    exists to durably capture are written by `lore record create`, route by
    scope, and therefore land in vaults the session commit never touches. Naming
    them (the pre-tail behavior, kept for `--no-sync`) told the operator what was
    still unsaved; the tail saves it, and pulls the other devices' work down in
    the same pass — the convergence half `implicit_pull` cannot deliver, because
    a flush runs against a vault made dirty by the session note it is about to
    commit and pull-only never integrates a dirty tree.

    **One ``cmd_sync`` call per WRITABLE vault, not one whole-install run.** A
    bare `lore sync` covers every configured vault including `shared: true`
    ones, and a shared vault must never be committed or pushed under this
    operator's git identity by an agent-actuated write — the same gate that
    excludes it from the flush itself (`_partition_writable_vaults`). Iterating
    the writable vaults and passing each as ``--vault`` is what keeps that gate
    intact while still reusing ``cmd_sync`` unchanged as the flow.

    **The tail holds no lock.** Both flush paths release the session-key lock
    (and the vault lock nested in it) before returning to :func:`cmd_flush`, so
    by the time this runs the thread owns nothing and each ``cmd_sync`` is an
    ordinary second sync invocation, taking its own locks in sync's own order.
    Moving this call inside any lock span would put a network round-trip under a
    blocking, timeout-less flock — do not.

    **Failure is soft, always.** The flush succeeded and is committed; a tail
    that cannot sync reports the applicable remedy (:func:`_sync_tail_notice`)
    and leaves the exit code alone. A vault already mid-resolution is skipped
    outright rather than synced: ``lore sync`` would abort the rebase `lore
    resolve` is mid-way through and throw that resolution away.
    """
    from types import SimpleNamespace

    from . import sync as sync_mod

    vaults, error = _resolve_all_vaults()
    if error is not None:
        print(f"notice: cannot sync after flush — {error}", file=sys.stderr)
        return

    writable, _shared = _partition_writable_vaults(vaults)
    for name, path in writable:
        vault = Path(path)
        if vault_is_resolving(vault):
            print(
                f"notice: vault {name!r} is mid-resolution — not synced after the "
                f"flush; to finish the resolution, {resolve_remedy(vault)}.",
                file=sys.stderr,
            )
            continue
        rc = sync_mod.cmd_sync(
            SimpleNamespace(vault=name, message=None, pull_only=False)
        )
        if rc != 0:
            _sync_tail_notice(name, vault)


# The literal reserved scope token. It is unambiguous against a KQL
# query because real KQL queries are field-qualified (e.g. `status:dirty`) — a bare
# `all` is never a valid scoping query, so it is reclaimed as the all-sessions verb.
FLUSH_SCOPE_ALL = "all"

# The all-scope discovery query — every dirty session, via the search facade.
_FLUSH_ALL_QUERY = "kind:session status:dirty"


def cmd_flush(args) -> int:
    """Flush sessions — current (no arg), `all`, or a `<search>` KQL query.

    Three positional scopes:

      - **no positional** → the CURRENT session: resolve by `--session-id`
        / `$CLAUDE_CODE_SESSION_ID` GUID, else the sanitized worktree-name fallback.
      - **the reserved token `all`** → discover every `dirty` session via
        the search facade (`kind:session status:dirty`) and flush each.
      - **any other positional** → treat it as a KQL query, run it through
        the same facade, INTERSECT with `dirty`, and flush each match. An empty /
        non-matching / only-clean set is a clean no-op.

    `all` vs `<search>` is disambiguated by the reserved-token check alone: `all` is
    never a field-qualified KQL query, so it can be reclaimed as the all-scope verb.

    Per-session atomicity: each session is its own flip + commit unit
    (`flush_session` + `_flush_commit`). A mid-batch failure leaves
    already-flushed sessions `clean`, NAMES the failed one, states a re-run safely
    retries (re-run is idempotent by design), and exits non-zero.

    No code path writes `status: complete` / `active` — that vocab was retired;
    a session status is only ever `dirty` / `clean`.

    Every scope ends with the SYNC TAIL (:func:`_flush_sync_tail`): the full
    commit → pull → push flow over every WRITABLE vault, which is what makes a
    flush leave the whole install saved rather than only the session record.
    `--no-sync` opts out, and that path ends instead with `_report_unsynced_vaults`
    over every configured vault — the notice that NAMES what such a flush leaves
    uncommitted. Exactly one of the two governs the writable vaults.

    The tail structurally never touches a `shared: true` vault (the shared-vault
    write gate), so a default flush follows the tail with `_report_unsynced_vaults`
    a second time, scoped to shared vaults ONLY, on stderr — the one thing nothing
    else on the default path would otherwise say. This does not compete with the
    tail: the tail's writable partition and this call's shared partition are
    exactly complementary, so a vault is only ever covered by one of the two.

    Either ending runs on the failure path too: a flush that exits non-zero is
    exactly when the sessions that DID commit most need pushing and the operator
    most needs to know what is still uncommitted. Neither changes the exit code.

    A flush that syncs must not also push on its own (`_flush_push`), so the
    per-session/per-batch push is enabled only when the tail is opted out of.
    """
    scope = getattr(args, "scope", None)
    sync_tail = not getattr(args, "no_sync", False)
    push = not sync_tail
    if scope == FLUSH_SCOPE_ALL:
        rc = _flush_batch(args, query=_FLUSH_ALL_QUERY, scope_label="all", push=push)
    elif scope:
        rc = _flush_batch(
            args, query=scope, scope_label=f"<search> {scope!r}", push=push
        )
    else:
        rc = _flush_current_session(args, push=push)

    if sync_tail:
        _flush_sync_tail()
        vaults, error = _resolve_all_vaults()
        if error is None:
            _, shared = _partition_writable_vaults(vaults)
            if shared:
                _report_unsynced_vaults(shared, file=sys.stderr)
    else:
        _report_unsynced_vaults()
    return rc


def _flush_current_session(args, *, push: bool) -> int:
    """Flush the CURRENT session record: dirty → clean + commit, else no-op.

    Reads `session/<key>.json` for the resolved current-session key, in EVERY
    configured vault that holds it:

      - no record in any vault → exit 0 with a "no session exists" notice
        (distinct from a clean session); writes nothing, no commit.
      - already `clean` → exit 0 with a "clean — nothing to flush" notice; an
        idempotent no-op, no commit.
      - `dirty` → flip `status` to `clean`, stamp `annotations[flushed-at]` (the
        pinned key/ISO-UTC format), reindex the one record, then commit the
        record's EXPLICIT paths only (the `.json` + `.md`; never `git add -A`).
        Anything else dirty in the vault is committed by the sync tail
        afterwards, in its own commit — see :func:`_flush_sync_tail`.

    **All vaults, not just the active one.** `lore session candidate --vault NAME`
    writes the session record into the ELECTED vault, so pinning the flush to the
    active vault left such a session permanently un-flushable — reported as "no
    session exists" while sitting `dirty` on disk with an empty watermark. The
    session KEY itself is vault-independent (a session id or the worktree name),
    so resolution is simply "which vaults hold `session/<key>`".

    A key held by more than one vault is a session split across them: EVERY dirty
    instance is flushed, each as its own flip + commit in its own vault. Flushing
    only one would leave the other half dirty and re-trigger the same dead end.
    A per-vault failure does not abort the rest — the remaining vaults are still
    flushed and the command exits non-zero.

    **`shared: true` vaults are excluded.** A shared vault is untrusted,
    multi-user content, and a flush is a WRITE that also commits and pushes under
    this operator's git identity — so a dirty session record planted there must
    never actuate one. The skip is announced by name (`_writable_vaults`),
    never silent.

    An unreadable vault config REFUSES (non-zero, nothing flipped) rather than
    degrading to the default vault: with the vault set unknown, "no session
    exists — nothing to flush" is a false success over a session that may be
    sitting `dirty` in a vault the broken config never named.
    """
    from ..session import store as session_store_mod
    from ..vault import vault as vault_mod

    key, rc = _resolve_session_key(args)
    if key is None:
        return rc

    vaults = _resolve_all_vaults_strict("flush")
    if vaults is None:
        return 1

    committer = vault_mod.resolve_committer_email() or vault_mod.resolve_user()

    all_holders = [
        (name, path) for name, path in vaults
        if session_store_mod.session_exists(str(path), key)
    ]
    holding_pairs, shared_holders = _partition_writable_vaults(all_holders)
    if shared_holders:
        print(
            f"notice: session {key!r} also exists in shared vault(s) "
            f"({', '.join(name for name, _ in shared_holders)}) — not flushed; "
            "a flush writes, commits and pushes, and shared vaults are untrusted.",
            file=sys.stderr,
        )
    holders = [path for _, path in holding_pairs]
    if not holders:
        if not shared_holders:
            print(f"notice: no session exists for {key!r} — nothing to flush.")
        return 0

    worst = 0
    for vault in holders:
        rc = _flush_one_session(vault, key, committer, push=push)
        if rc != 0:
            worst = rc
    return worst


def _implicit_pull(vault: Path) -> None:
    """Run ``sync``'s throttled, stderr-only implicit pull for *vault*.

    A one-line wrapper so both flush paths — single-session and batch — reach the
    same call site. ``cli.sync`` is imported at call time, matching how the other
    write paths reach it, so neither module has to sit at the other's import top.
    """
    from . import sync as sync_mod

    sync_mod.implicit_pull(vault)


def _flush_one_session(vault: Path, key: str, committer: str, *, push: bool) -> int:
    """Flush `session/<key>` in ONE vault: flip + commit as a unit, then push.

    *push* is ``False`` whenever the sync tail will run — the tail's own
    `lore sync` pushes this vault, and pushing here as well would spend a second
    network round-trip for the same commits.

    The per-vault primitive behind the current-session path — extracted so a key
    held by several vaults is flushed once per vault, each with its own atomic
    flip + commit and its own rollback on commit failure, rather than N flips
    against one vault. Returns the exit code for this vault alone.

    The caller has ALREADY established that this vault holds the key (that is how
    it picked the vaults to iterate), so no pre-lock existence probe runs here —
    a vault without the session never reaches this function and so never creates
    even a lock sidecar. The record vanishing between that probe and the lock is
    covered by `flush_session`'s own check under the lock, whose
    ``FLUSH_NO_SESSION`` verdict is handled below.
    """
    from ..session import store as session_store_mod

    # A vault mid-rebase is being resolved: a flush would flip the sidecar and
    # commit into a tree `lore resolve` is still settling. Refused before the
    # lock, so the session stays dirty and nothing is written.
    if vault_is_resolving(vault):
        print(refusal_notice(vault, "flush"), file=sys.stderr)
        return 1

    # Converge on the other devices' records before the flip + commit —
    # throttled, stderr-only, and unable to fail the flush. Runs before the
    # session-key lock below: the pull fetches over the network and may reindex.
    _implicit_pull(vault)

    # The flip and the commit are ONE unit under the session-key lock: a
    # `session candidate` that landed between them would flip the sidecar back to
    # `dirty` and be staged into the flush commit. The lock is reentrant, so
    # `flush_session`'s own acquisition is a depth bump. The push stays outside.
    with locking.session_write_lock(vault, key):
        try:
            verdict = session_store_mod.flush_session(
                key,
                vault_root=str(vault),
                committer=committer,
                open_index=_open_session_index,
            )
        except Exception as exc:
            print(f"error: session flush failed: {exc}", file=sys.stderr)
            return 1

        if verdict == session_store_mod.FLUSH_NO_SESSION:
            print(f"notice: no session exists for {key!r} — nothing to flush.")
            return 0
        if verdict == session_store_mod.FLUSH_ALREADY_CLEAN:
            print(f"notice: session {key!r} is clean — nothing to flush.")
            return 0

        print(f"Flushed: session/{key} (dirty -> clean)")
        rc = _flush_commit(vault, key, push=False)
    if rc == 0:
        # Push OUTSIDE the lock (network never runs under a no-timeout flock).
        # Only when the vault is a git toplevel — otherwise the commit was
        # skipped with a notice and there is nothing to push.
        if push and _vault_is_git_toplevel(vault):
            _flush_push(vault)
    else:
        _revert_flip(vault, key, committer)
    return rc


def _revert_flip(vault: Path, key: str, committer: str) -> None:
    """Roll a flushed session record back to `dirty` after its commit failed.

    `flush_session` flips the record `clean` on disk BEFORE the commit, so a
    commit failure would otherwise leave a clean-on-disk-but-uncommitted record
    that a re-run's `status:dirty` discovery could never re-find. Reverting the
    flip keeps the failed session retry-discoverable — the orphan gap both flush
    paths have to close.

    Shared by BOTH paths (single-session and the batch loop) deliberately: the
    guarantee is identical, and a second copy would be an unexercised second
    implementation of it that could silently drift.

    A failed rollback re-creates the very orphan this exists to prevent, so it is
    never swallowed: the warning names the record and states what condition it may
    be left in. Never raises — the caller is already on a failure path and owns
    the exit code.
    """
    from ..session import store as session_store_mod

    try:
        session_store_mod.revert_flush(
            key,
            vault_root=str(vault),
            committer=committer,
            open_index=_open_session_index,
        )
    except Exception as exc:
        print(
            f"warning: flush commit failed AND rollback failed for session/{key}: "
            f"{exc}\n  the record may be clean-on-disk but uncommitted — inspect "
            "manually before re-running.",
            file=sys.stderr,
        )


# Discovery must return EVERY dirty session, not the search facade's default page
# (`run_search` defaults to limit=20). The live vault holds dozens of session
# records, so an `all`/`<search>` flush capped at 20 would silently leave the rest
# dirty (a correctness bug). Use a high ceiling AND assert non-truncation
# so an overflow is a loud error, never a silent partial flush.
_FLUSH_DISCOVERY_LIMIT = 100_000


def _discover_dirty_session_keys(query: str) -> list[tuple[Path, str]]:
    """Run *query* through the search facade and return the matching DIRTY sessions.

    REUSES the KQL search facade (`search.run_search`) — never a second query engine.
    The facade is the injection boundary: it compiles *query*
    via `kql_compile`, which BINDS every value as a `?` param (never interpolated),
    so a malicious `<search>` string cannot inject SQL. A facade parse/compile error
    raises so the caller can report it and exit non-zero.

    Discovery passes `limit=_FLUSH_DISCOVERY_LIMIT` (well above any realistic dirty
    count) and treats a `truncated` result as a hard error — a flush must never
    silently skip dirty sessions beyond the facade's default page.

    Intersects with `dirty` here (not only in the query) so a `<search>` that matches
    clean sessions still only flushes the dirty ones: a hit is kept iff its `kind` is
    `session` AND its `status` is `dirty`. The session KEY is the last path segment of
    the index `id` (`<vault>/session/<key>`); session keys never contain `/`.

    Returns `(vault_root, key)` pairs, NOT bare keys. The index spans every vault,
    so discovery legitimately returns sessions from several — and the caller must
    flush each in the vault that actually holds it. Dropping the vault and running
    every key against the active one both misses non-default-vault sessions and
    reports success for a flush that never happened. The vault comes from the hit's
    own `vault` column (the vault ROOT path the record was indexed under), falling
    back to the `id` prefix, which encodes the same value.
    """
    from ..search import engine as search_mod

    text, code = search_mod.run_search(
        query, env=dict(os.environ), as_json=True, limit=_FLUSH_DISCOVERY_LIMIT
    )
    if code != 0:
        raise ValueError(text)
    payload = json.loads(text)
    if payload.get("truncated"):
        raise ValueError(
            f"dirty-session discovery exceeded {_FLUSH_DISCOVERY_LIMIT} results — "
            "refusing to flush a partial set; narrow the <search> scope"
        )
    hits: list[tuple[Path, str]] = []
    for hit in payload.get("hits", []):
        if hit.get("kind") != "session" or hit.get("status") != "dirty":
            continue
        record_id = hit["id"]
        vault_root = hit.get("vault") or record_id.rsplit("/", 2)[0]
        hits.append((Path(vault_root), record_id.rsplit("/", 1)[-1]))
    return hits


def _keep_writable_hits(
    discovered: list[tuple[Path, str]],
    writable: set[str],
    shared_names: dict[str, str],
) -> list[tuple[Path, str]]:
    """Keep only the discovery hits whose vault is a live, writable vault.

    *writable* holds the resolved paths of the currently configured non-shared
    vaults; *shared_names* maps the resolved path of each configured
    ``shared: true`` vault to its name. A hit in neither is a STALE index row —
    the vault it names is not part of this install any more (or never was) — and
    a hit in *shared_names* is untrusted content that must not actuate a commit.

    Both are dropped WITH a notice naming the vault: an unflushed dirty session
    the operator cannot see is exactly how the original defect (a permanently
    un-flushable session reported as absent) manifested.
    """
    kept: list[tuple[Path, str]] = []
    for vault, key in discovered:
        resolved = str(Path(vault).resolve())
        if resolved in writable:
            kept.append((vault, key))
        elif resolved in shared_names:
            print(
                f"notice: skipping session/{key} in shared vault "
                f"{shared_names[resolved]!r} — a flush writes, commits and pushes, "
                "and shared vaults are untrusted.",
                file=sys.stderr,
            )
        else:
            print(
                f"notice: skipping session/{key} — its indexed vault is not a "
                f"configured vault (stale index row): {vault}",
                file=sys.stderr,
            )
    return kept


def _flush_batch(args, *, query: str, scope_label: str, push: bool) -> int:
    """Flush every DIRTY session matching *query*, one atomic unit at a time.

    Discovers keys via :func:`_discover_dirty_session_keys` (the facade), then for
    each key runs the SAME per-session primitive the current-session path uses
    (`flush_session` + `_flush_commit`) so every session is record-flip + commit as
    its own unit. The git *push*, however, is hoisted OUT of the loop: each commit
    runs with `push=False` and the batch pushes ONCE at the end (`_flush_push`),
    replacing N network round-trips with one — and not at all when the sync tail
    is going to push the same vaults itself (*push* is ``False``). A mid-batch failure stops the batch
    (and never reaches the final push), prints the per-session summary so far, NAMES
    the failed session, states already-flushed sessions are `clean` and that a re-run
    safely retries (idempotent by design — the committed-but-unpushed sessions are
    pushed by the next successful run or `lore sync`), and exits non-zero. An empty
    match set is a clean no-op (exit 0). A roll-up of the flushed count closes a
    successful batch.

    **Each session is flushed in the vault that HOLDS it.** The index spans every
    vault, so discovery returns `(vault_root, key)` pairs that may name several;
    the flip, the commit, and the session-key lock all follow the hit's own vault.
    Running the batch against the active vault instead skipped every session a
    `--vault` capture had routed elsewhere while still reporting them flushed. The
    push stays hoisted out of the loop, now ONCE PER TOUCHED VAULT — a commit is
    only pushable by the repo that carries it.

    **The hit's own `vault` column is never trusted as a write destination.**
    It is an index row — index state outlives the config that produced it, so a
    stale (or planted) row can name a path this install no longer governs, and
    acting on it verbatim would steer a flip + commit at an arbitrary location.
    Every hit is intersected with the LIVE configured vault set, minus the
    `shared: true` vaults a flush must never write/commit/push into
    (`_partition_writable_vaults`). Each dropped hit is NAMED, so a session that
    really is sitting dirty somewhere unreachable is visible rather than silently
    passed over.
    """
    from ..session import store as session_store_mod
    from ..vault import vault as vault_mod

    committer = vault_mod.resolve_committer_email() or vault_mod.resolve_user()

    vaults = _resolve_all_vaults_strict(f"flush {scope_label}")
    if vaults is None:
        return 1
    writable_pairs, shared_pairs = _partition_writable_vaults(vaults)
    writable = {str(Path(path).resolve()) for _, path in writable_pairs}
    shared_names = {str(Path(path).resolve()): name for name, path in shared_pairs}

    try:
        discovered = _discover_dirty_session_keys(query)
    except ValueError as exc:
        print(f"error: flush {scope_label} — search failed: {exc}", file=sys.stderr)
        return 1

    discovered = _keep_writable_hits(discovered, writable, shared_names)

    if not discovered:
        print(f"notice: no dirty sessions match {scope_label} — nothing to flush.")
        return 0

    # Distinct vaults in discovery order, keyed by resolved path so a vault
    # holding several matching sessions is fenced and pulled exactly once.
    batch_vaults = list({str(v): v for v, _ in discovered}.values())

    # The same mid-resolution fence as the single-session path, applied to the
    # whole batch BEFORE the first flip: refusing partway through would leave
    # earlier sessions flushed, which is exactly the split state a resolution
    # in progress must not acquire.
    for batch_vault in batch_vaults:
        if vault_is_resolving(batch_vault):
            print(refusal_notice(batch_vault, "flush"), file=sys.stderr)
            return 1

    # Same implicit pull as the single-session path, and only once the fence
    # above has cleared EVERY vault — a refused batch must not have pulled.
    # Before the first flip, too: a batch must not start converging halfway
    # through.
    for batch_vault in batch_vaults:
        _implicit_pull(batch_vault)

    flushed: list[str] = []
    # Insertion-ordered so the end-of-batch pushes run in discovery order; keyed by
    # the resolved path string so one vault is pushed once however many of its
    # sessions the batch flushed.
    touched_vaults: dict[str, Path] = {}
    for vault, key in discovered:
        flipped = False
        try:
            # Flip + commit as ONE unit under the session-key lock, so a
            # concurrent `session candidate` for this key cannot re-dirty the
            # sidecar between them and be staged into the flush commit. Keyed,
            # so sibling sessions in the batch never contend.
            with locking.session_write_lock(vault, key):
                verdict = session_store_mod.flush_session(
                    key,
                    vault_root=str(vault),
                    committer=committer,
                    open_index=_open_session_index,
                )
                # A session that raced clean since discovery is a benign skip.
                if verdict != session_store_mod.FLUSH_FLUSHED:
                    continue
                flipped = True
                # Commit per-session (the atomicity unit) but DON'T push here —
                # the push is hoisted to one round-trip after the batch (below),
                # which also keeps the network out of the lock.
                rc = _flush_commit(vault, key, push=False)
                if rc != 0:
                    raise RuntimeError(f"commit failed (rc={rc})")
        except Exception as exc:
            # Per-session atomicity: the flip happened before the commit, so on a
            # commit failure roll the flip BACK to `dirty` (shared with the
            # single-session path — see :func:`_revert_flip`).
            if flipped:
                _revert_flip(vault, key, committer)
            print(f"Flushed: {len(flushed)} session(s) before the failure:")
            for done in flushed:
                print(f"  - session/{done} (dirty -> clean)")
            print(
                f"error: flush {scope_label} failed on session/{key}: {exc}\n"
                f"  Already-flushed sessions are clean; re-run `lore flush` "
                f"({scope_label}) to safely retry the rest (re-run is idempotent).",
                file=sys.stderr,
            )
            return 1
        flushed.append(key)
        touched_vaults[str(vault)] = vault
        print(f"Flushed: session/{key} (dirty -> clean)")

    print(f"Flushed {len(flushed)} session(s) [{scope_label}].")
    # One push per TOUCHED VAULT after the batch: every session was committed
    # locally above; push them together rather than once per session. A vault the
    # batch never committed to is never probed — an all-raced-clean batch (every
    # verdict != FLUSH_FLUSHED) touches nothing and pushes nothing.
    if push:
        for vault in touched_vaults.values():
            _flush_push(vault)
    return 0


def add_flush_subparser(sub) -> None:
    """Register the ``flush`` command parser."""
    p_flush = sub.add_parser(
        "flush",
        help="Flush sessions: current (no arg) / `all` / a `<search>` KQL query",
    )
    p_flush.add_argument(
        "scope",
        nargs="?",
        default=None,
        help=(
            "Flush scope: omit = current session; the reserved token "
            "`all` = every dirty session; any other value = a KQL query "
            "(intersected with dirty)"
        ),
    )
    p_flush.add_argument(
        "--no-sync",
        action="store_true",
        help=(
            "Skip the closing `lore sync` of every writable vault: commit the "
            "session record(s) only and name what is left uncommitted"
        ),
    )
    _add_session_selectors(p_flush)
    p_flush.set_defaults(func=cmd_flush)
