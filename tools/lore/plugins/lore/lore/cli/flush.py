"""``lore flush`` — flip dirty sessions clean (current / all / KQL-scoped) + commit."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .common import (
    _add_session_selectors,
    _git,
    _resolve_all_vaults,
    _vault_drift,
    _vault_is_git_toplevel,
)
from .session import _open_session_index, _resolve_session_key


def _flush_commit(vault: Path, key: str, *, push: bool = True) -> int:
    """Stage the flushed session record's EXPLICIT path(s) + commit in ONE commit.

    Never `git add -A` — only `session/<key>.json` (the flipped sidecar) and its
    `.md` body are staged, so unrelated dirty vault files are never swept in.
    The staged-index gate (`git diff --cached --quiet`) makes an already-clean
    flush a no-op rather than a failed empty commit. Returns exit code.

    The per-session *commit* is the deliberate atomicity unit and always runs here.
    The *push* is NOT per-session: a batch flush passes ``push=False`` and pushes
    ONCE after the whole batch commits (:func:`_flush_push`), turning N network
    round-trips into one. The current-session path keeps the inline push
    (``push=True`` default) — there is nothing to batch. A skipped mid-batch push
    is harmless: the commits are durable locally and a re-run (or `lore sync`)
    pushes them, so atomicity is unchanged.
    """
    if not vault.exists() or not _vault_is_git_toplevel(vault):
        print("notice: vault is not its own git toplevel — skipping commit.", file=sys.stderr)
        return 0

    session_dir = vault / "session"
    paths = []
    for suffix in (".json", ".md"):
        p = session_dir / f"{key}{suffix}"
        if p.exists():
            paths.append(str(p))
    if not paths:
        print("Nothing to commit — no session record on disk.")
        return 0

    rc, _, stderr = _git(vault, "add", "--", *paths)
    if rc != 0:
        print(f"error: git add failed: {stderr}", file=sys.stderr)
        return 1

    rc, _, _ = _git(vault, "diff", "--cached", "--quiet")
    if rc == 0:
        print("Nothing to commit — index is clean.")
        return 0

    message = f"session: flush {key}"
    rc, _, stderr = _git(vault, "commit", "-m", message)
    if rc != 0:
        print(f"error: git commit failed: {stderr}", file=sys.stderr)
        return 1
    print(f"Committed: {message}")

    if push:
        return _flush_push(vault)
    return 0


def _flush_push(vault: Path) -> int:
    """Push committed flush(es) to origin once, if an origin remote exists.

    Split out of :func:`_flush_commit` so a batch flush probes the remote and
    pushes ONCE after every per-session commit lands, rather than N times inside
    the per-session loop (efficiency follow-up). A missing origin, or a push that
    fails (offline), is a soft outcome (exit 0): every flush is already committed
    locally and `lore sync` — or the next successful flush — re-pushes when online.
    Returns exit code (always 0; a push failure does not fail the flush).
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


def _report_unsynced_vaults() -> None:
    """Print a notice naming every configured vault left holding uncommitted work.

    `_flush_commit` stages the flushed session record's EXPLICIT paths and nothing
    else — deliberately, so unrelated dirty files are never swept into a session
    commit. The consequence is that the RECORDS a flush produces are not committed
    by the flush: the evaluation step creates them with `lore record create`, which
    routes by scope, so they can land in a product/repo vault while the session
    record commits in `default`. A flush that printed "Committed" and stopped
    therefore read as "everything is saved" while the records it existed to durably
    capture sat untracked.

    Reporting rather than committing keeps the explicit-paths guarantee intact: the
    flush still touches only what it staged, and the operator gets the one command
    that covers the rest. Silent when every vault is committed and pushed.
    """
    vaults, error = _resolve_all_vaults()
    if error is not None:
        print(f"notice: cannot check vault sync state — {error}", file=sys.stderr)
        return

    drifted = [(name, _vault_drift(Path(path))) for name, path in vaults]
    drifted = [(name, findings) for name, findings in drifted if findings]
    if not drifted:
        return

    print("notice: vault(s) still holding unsynced work — run `lore sync`:")
    for name, findings in drifted:
        print(f"  {name}: {'; '.join(findings)}")


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

    Every scope ends with `_report_unsynced_vaults`, which names the vaults the
    flush's own commit does not cover. It runs on the failure path too: a flush
    that exits non-zero is exactly when knowing what is still uncommitted matters,
    and the notice never changes the exit code.
    """
    scope = getattr(args, "scope", None)
    if scope == FLUSH_SCOPE_ALL:
        rc = _flush_batch(args, query=_FLUSH_ALL_QUERY, scope_label="all")
    elif scope:
        rc = _flush_batch(args, query=scope, scope_label=f"<search> {scope!r}")
    else:
        rc = _flush_current_session(args)

    _report_unsynced_vaults()
    return rc


def _flush_current_session(args) -> int:
    """Flush the CURRENT session record: dirty → clean + commit, else no-op.

    Reads `session/<key>.json` for the resolved current-session key:

      - no record → exit 0 with a "no session exists" notice (distinct from a
        clean session); writes nothing, no commit.
      - already `clean` → exit 0 with a "clean — nothing to flush" notice; an
        idempotent no-op, no commit.
      - `dirty` → flip `status` to `clean`, stamp `annotations[flushed-at]` (the
        pinned key/ISO-UTC format), reindex the one record, then commit the
        record's EXPLICIT paths only (the `.json` + `.md`; never `git add -A`).
    """
    from ..session import store as session_store_mod
    from ..vault import config as vault_config_mod
    from ..vault import vault as vault_mod

    vault = Path(vault_config_mod.resolve_active_vault())
    key, rc = _resolve_session_key(args)
    if key is None:
        return rc

    committer = vault_mod.resolve_committer_email() or vault_mod.resolve_user()
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
    rc = _flush_commit(vault, key)
    if rc != 0:
        # Same orphan-gap guarantee as the batch path: `flush_session` flips the
        # record `clean` on disk BEFORE the commit, so a commit failure would leave
        # a clean-on-disk-but-uncommitted record that `status:dirty` re-discovery
        # could never re-find. Revert the flip so a re-run can retry it
        # (the single path shares the batch's atomicity need).
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
    return rc


# Discovery must return EVERY dirty session, not the search facade's default page
# (`run_search` defaults to limit=20). The live vault holds dozens of session
# records, so an `all`/`<search>` flush capped at 20 would silently leave the rest
# dirty (a correctness bug). Use a high ceiling AND assert non-truncation
# so an overflow is a loud error, never a silent partial flush.
_FLUSH_DISCOVERY_LIMIT = 100_000


def _discover_dirty_session_keys(query: str) -> list[str]:
    """Run *query* through the search facade and return the matching DIRTY session keys.

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
    keys: list[str] = []
    for hit in payload.get("hits", []):
        if hit.get("kind") != "session" or hit.get("status") != "dirty":
            continue
        keys.append(hit["id"].rsplit("/", 1)[-1])
    return keys


def _flush_batch(args, *, query: str, scope_label: str) -> int:
    """Flush every DIRTY session matching *query*, one atomic unit at a time.

    Discovers keys via :func:`_discover_dirty_session_keys` (the facade), then for
    each key runs the SAME per-session primitive the current-session path uses
    (`flush_session` + `_flush_commit`) so every session is record-flip + commit as
    its own unit. The git *push*, however, is hoisted OUT of the loop: each commit
    runs with `push=False` and the batch pushes ONCE at the end (`_flush_push`),
    replacing N network round-trips with one. A mid-batch failure stops the batch
    (and never reaches the final push), prints the per-session summary so far, NAMES
    the failed session, states already-flushed sessions are `clean` and that a re-run
    safely retries (idempotent by design — the committed-but-unpushed sessions are
    pushed by the next successful run or `lore sync`), and exits non-zero. An empty
    match set is a clean no-op (exit 0). A roll-up of the flushed count closes a
    successful batch.
    """
    from ..session import store as session_store_mod
    from ..vault import config as vault_config_mod
    from ..vault import vault as vault_mod

    vault = Path(vault_config_mod.resolve_active_vault())
    committer = vault_mod.resolve_committer_email() or vault_mod.resolve_user()

    try:
        keys = _discover_dirty_session_keys(query)
    except ValueError as exc:
        print(f"error: flush {scope_label} — search failed: {exc}", file=sys.stderr)
        return 1

    if not keys:
        print(f"notice: no dirty sessions match {scope_label} — nothing to flush.")
        return 0

    flushed: list[str] = []
    for key in keys:
        flipped = False
        try:
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
            # Commit per-session (the atomicity unit) but DON'T push here — the
            # push is hoisted to one round-trip after the batch (see below).
            rc = _flush_commit(vault, key, push=False)
            if rc != 0:
                raise RuntimeError(f"commit failed (rc={rc})")
        except Exception as exc:
            # Per-session atomicity: the flip happened before the commit, so on a
            # commit failure roll the flip BACK to `dirty` — otherwise the session
            # is clean-on-disk-but-uncommitted and a re-run's discovery query
            # (`status:dirty`) could never re-find it. Reverting keeps the failed
            # session retry-discoverable.
            if flipped:
                try:
                    session_store_mod.revert_flush(
                        key,
                        vault_root=str(vault),
                        committer=committer,
                        open_index=_open_session_index,
                    )
                except Exception as revert_exc:
                    # A failed rollback re-creates the very orphan revert exists to
                    # prevent — never swallow it silently; surface it so the operator
                    # knows this session may be clean-on-disk but uncommitted.
                    print(
                        f"warning: rollback failed for session/{key}: {revert_exc}\n"
                        f"  the record may be clean-on-disk but uncommitted — inspect "
                        "manually.",
                        file=sys.stderr,
                    )
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
        print(f"Flushed: session/{key} (dirty -> clean)")

    print(f"Flushed {len(flushed)} session(s) [{scope_label}].")
    # One push for the whole batch (efficiency follow-up): every session was
    # committed locally above; push them together rather than once per session.
    # Only probe/push when something actually committed — an all-raced-clean batch
    # (every verdict != FLUSH_FLUSHED) leaves nothing to push.
    if flushed:
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
    _add_session_selectors(p_flush)
    p_flush.set_defaults(func=cmd_flush)
