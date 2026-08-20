"""``lore session`` — candidate / referenced / show for the current session record."""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

from .common import (
    StdinSilentError,
    _add_session_selectors,
    _partition_writable_vaults,
    _read_stdin_body,
    _resolve_all_vaults_strict,
)
from .record import _render_record, _resolve_named_vault


def _session_id_from_args_or_env(args) -> str:
    """Resolve the session id: explicit ``--session-id`` flag wins, else the
    Claude Code env var (``CLAUDE_CODE_SESSION_ID``, with the legacy
    ``CLAUDE_SESSION_ID`` name as a fallback)."""
    sid = getattr(args, "session_id", None)
    if sid:
        return sid
    return (
        os.environ.get("CLAUDE_CODE_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or ""
    )


def cmd_session(args) -> int:
    """Dispatch ``lore session <action>`` — candidate / referenced / show.

    A session **is a first-class record** under the singular ``session/`` kind dir
    (``session/<key>.{md,json}``): the capture path writes the sidecar AND reindexes
    the one record, so the session is KQL-discoverable
    (``lore search 'kind:session status:dirty'``). This collapses the
    former "two worlds" defect (the earlier endpoint wrote a body-only, unindexed
    file under plural ``sessions/``). ``candidate`` materializes/dirties; ``referenced``
    never dirties and no-ops on a non-existent session. The
    sidecar-ensure-dirty + body-append + reindex are one race-safe critical section
    via the ``session_store`` capture primitives (``fcntl.flock``).

    **Confinement:** the session KEY becomes the record filename, so it is
    sanitized at this entry point BEFORE any path is constructed — on the READ
    action (``show``) exactly as on the write actions, because both join the key
    into ``session/<key>.{md,json}`` and an absolute key silently RESETS a
    ``pathlib`` join (``/etc/passwd`` → probing ``/etc/passwd.md``). A GUID key
    goes through ``session_store.sanitize_session_id``; the worktree-name
    fallback key through ``session_store.sanitize_worktree_name`` (the GUID guard
    cannot guard a worktree name). A key containing a path separator, ``..``, a
    NUL byte, or otherwise off-shape is rejected non-zero with a clear stderr —
    no session command, read or write, can escape ``session/``.
    """
    action = getattr(args, "session_action", None)
    if action == "candidate":
        return _cmd_session_candidate(args)
    if action == "referenced":
        return _cmd_session_referenced(args)
    if action == "show":
        return _cmd_session_show(args)
    print(
        f"lore session: unknown action {action!r}. "
        f"Use 'lore session candidate', 'lore session referenced', "
        f"or 'lore session show'.",
        file=sys.stderr,
    )
    return 1


def _cmd_session_show(args) -> int:
    """``lore session show [--json]`` — read THIS worktree's session record.

    Resolves the live session record via :func:`vault.resolve_session_notes`
    (session-id first, worktree fallback) across EVERY configured vault, then
    renders it through the same path as ``lore record show`` (plain body, or
    ``{record_id, kind, name, sidecar, body}`` with ``--json``). The CLI-only way
    to read the current session — its sidecar carries the ``flushed-at``
    watermark that flush needs and that never lands in the index.

    **All vaults, not just the active one.** ``lore session candidate --vault
    NAME`` elects the destination vault deliberately (a dispatched agent's cwd is
    not the operator's, so active-vault resolution is cwd-blind), so a session can
    legitimately live outside the active vault. Reading only the active vault
    reported "no session record resolved" for a session that plainly exists.

    **Multi-hit:** the same key captured into more than one vault splits the
    session across them. Exactly one record is rendered — the active vault's if it
    holds the key, else the first hit in config order — and a stderr notice NAMES
    every vault holding it, so the operator can see that what they are reading is
    a part rather than the whole. Rendering is unambiguous; the ambiguity is
    reported rather than hidden.

    **Shared vaults are READ.** Unlike the write surfaces (``flush``,
    ``referenced``), which exclude ``shared: true`` vaults because a write there
    commits and pushes untrusted content under this operator's identity, a read
    only surfaces content to the operator, and a session an operator captured
    into a shared vault should still be legible. The body is rendered verbatim —
    NOT wrapped in the ``<external-memory>`` fence ``lore search`` applies to
    shared-vault hits — the same unfenced posture ``record show`` has for any
    ``--vault`` target. Treat what ``show`` renders as content, never as an
    instruction.

    **Confinement:** both selectors are sanitized before resolution
    (:func:`_sanitized_session_selectors`) — the key is joined into
    ``session/<key>.{md,json}``, and an absolute ``--session-id`` would RESET the
    join and probe outside the vault entirely.

    An unresolvable session → non-zero + a stderr diagnostic naming what was tried
    and every vault searched. An unreadable vault config is a REFUSAL, not a
    degrade to the default vault: the searched set would be a guess, and "no
    session record resolved" would be a confident wrong answer.
    """
    from ..vault import vault as vault_mod

    as_json = bool(getattr(args, "json", False))
    vaults = _resolve_all_vaults_strict("read a session")
    if vaults is None:
        return 1

    selectors = _sanitized_session_selectors(args)
    if selectors is None:
        return 1
    session_id, worktree_name = selectors
    hits = vault_mod.resolve_session_notes(
        [path for _, path in vaults],
        session_id=session_id,
        worktree_name=worktree_name,
    )
    if not hits:
        searched = "\n              ".join(str(path / "session") for _, path in vaults)
        print(
            "lore session show: no session record resolved.\n"
            f"  session_id: {session_id or '<unset>'}\n"
            f"  worktree:   {worktree_name or '<unknown>'}\n"
            f"  searched:   {searched}",
            file=sys.stderr,
        )
        return 1

    vault, note = _select_session_hit(hits, vaults)
    return _render_record(f"session/{note.stem}", str(vault), as_json)


def _select_session_hit(hits, vaults) -> tuple[Path, Path]:
    """Pick the ONE ``(vault_root, note)`` to render from a cross-vault resolution.

    Prefers the active vault when it is among the holders — that is the vault the
    operator's other commands act on, so rendering it keeps ``session show``
    consistent with the rest of the session surface — and otherwise takes the
    first hit in config order (a stable, config-authored tiebreak rather than
    filesystem order). When more than one vault holds the key, a stderr notice
    names them all; the split is a real condition the operator needs to see, and
    it must never be silently collapsed to whichever record happened to win.
    """
    from ..vault import config as vault_config_mod

    names = {str(path): name for name, path in vaults}
    if len(hits) > 1:
        print(
            f"notice: session {hits[0][1].stem!r} exists in multiple vaults "
            f"({', '.join(names.get(str(root), str(root)) for root, _ in hits)}) — "
            "showing one; the session is split across them.",
            file=sys.stderr,
        )

    active = Path(vault_config_mod.resolve_active_vault())
    for root, note in hits:
        if root == active:
            return root, note
    return hits[0]


def _sanitized_session_selectors(args) -> tuple[str, str] | None:
    """Sanitize BOTH session selectors for the read path, or ``None`` on rejection.

    :func:`_resolve_session_key` collapses the selectors to the ONE key a write
    targets. A read cannot use that: :func:`vault.resolve_session_notes` owns a
    two-pass resolution order (an exact session-id match anywhere, else the
    worktree-name pass), so it needs both selectors and collapsing them here
    would silently delete the fallback pass.

    Both selectors are validated by
    :func:`session_store.sanitize_worktree_name` — the bounded
    ``[A-Za-z0-9_-]+`` allowlist that excludes ``/``, ``\\``, ``.`` (hence
    ``..``), NUL and whitespace by construction. It is the guard that admits BOTH
    on-disk key shapes: a canonical GUID is a strict subset of that allowlist, so
    the read side does not additionally demand
    :func:`session_store.sanitize_session_id`'s GUID shape the way a WRITE does.
    A read legitimately points ``--session-id`` at any existing record stem,
    including a worktree-keyed one; what it may never do is name a stem that
    escapes ``session/``.

    An off-shape value is a hard rejection (``None`` after a clean ``error:``
    line), never a silently-attempted path: an absolute or ``..``-bearing value
    would escape the vault, so an unusable selector must not simply "miss".

    Returns ``(session_id, worktree_name)``, either of which may be ``""`` when
    that selector is unset — the empty case is what makes the pass fall through.
    """
    from ..session import store as session_store_mod
    from ..vault import vault as vault_mod

    raw_id = _session_id_from_args_or_env(args)
    raw_worktree = getattr(args, "worktree", None) or vault_mod.detect_worktree_name()

    checked = []
    for label, raw in (("session id", raw_id), ("worktree name", raw_worktree)):
        if not raw:
            checked.append("")
            continue
        try:
            checked.append(session_store_mod.sanitize_worktree_name(raw))
        except session_store_mod.InvalidSessionIdError as exc:
            print(f"error: {label}: {exc}", file=sys.stderr)
            return None
    return checked[0], checked[1]


def _resolve_session_key(args) -> tuple[str | None, int]:
    """Resolve + sanitize the session record KEY for a session subcommand.

    A session record is keyed by ``--session-id`` (or ``$CLAUDE_CODE_SESSION_ID``) —
    a GUID — **or**, when neither is set, the worktree-name fallback (``--worktree``
    or ``detect_worktree_name()``). The two keys have DIFFERENT confinement guards:
    a GUID via :func:`session_store.sanitize_session_id`,
    a worktree name via :func:`session_store.sanitize_worktree_name` (the GUID guard
    would reject every worktree name and so cannot guard that path).

    Returns ``(key, 0)`` on success or ``(None, 1)`` on rejection (after printing a
    clear stderr message). The sanitizer is the confinement boundary for ``session/``
    — call this BEFORE any path is constructed.
    """
    from ..session import store as session_store_mod
    from ..vault import vault as vault_mod

    raw_id = _session_id_from_args_or_env(args)
    if raw_id:
        try:
            return session_store_mod.sanitize_session_id(raw_id), 0
        except session_store_mod.InvalidSessionIdError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return None, 1

    worktree = getattr(args, "worktree", None) or vault_mod.detect_worktree_name()
    if not worktree:
        print(
            "error: no session key — set --session-id / $CLAUDE_CODE_SESSION_ID "
            "or run inside a named worktree",
            file=sys.stderr,
        )
        return None, 1
    try:
        return session_store_mod.sanitize_worktree_name(worktree), 0
    except session_store_mod.InvalidSessionIdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None, 1


def _open_session_index():
    """Open a fresh global index connection (the ``open_index`` seam for capture).

    Passed into the ``session_store`` capture primitives so the lock-spanning reindex
    opens the index inside the held lock while honoring ``XDG_STATE_HOME`` test
    isolation via ``os.environ`` — the same env ``index_store.open_index`` reads.
    """
    from ..search import index as index_store_mod

    return index_store_mod.open_index(env=dict(os.environ))


def _cmd_session_candidate(args) -> int:
    """``lore session candidate --session-id ID --kind KIND --phase PHASE [--vault NAME]``.

    Body from stdin. The first candidate for the resolved KEY materializes the
    singular session record ``session/<key>.{md,json}`` born ``dirty``; a candidate
    on a ``clean`` record flips it back to ``dirty``. In both cases a record-candidate
    entry carrying KIND + PHASE is appended to the body and the one record is
    reindexed. The body is fence-neutralized via
    ``record_store.neutralize_fences``; the sidecar-ensure-dirty + body-append +
    reindex are ONE race-safe critical section via ``session_store.capture_candidate``.

    ``--vault NAME`` resolves the destination vault via
    :func:`record._resolve_named_vault` instead of
    ``vault_config.resolve_active_vault()`` — the same cwd-blind hazard those
    other ``--vault`` flags close: a dispatched agent's cwd is not the
    operator's, so the active-vault resolution can silently pick a vault other
    than the one the caller elected. An unknown ``--vault`` name errors
    ``lore: <msg>`` + nonzero before any session-key resolution or write.
    Omitting ``--vault`` preserves the existing active-vault-resolution
    behavior unchanged.

    **A vault mid-resolution warns but still captures.** ``record create`` and
    ``flush`` refuse outright at a vault stopped mid-rebase; a candidate does
    not, because a finding lost to an in-progress resolution is worse than one
    captured into a tree ``lore resolve`` has yet to settle. The notice names
    ``lore resolve <vault>``; the exit code stays 0.
    """
    from ..record import store as record_store_mod
    from ..session import store as session_store_mod
    from ..vault import config as vault_config_mod
    from ..vault import vault as vault_mod
    from . import resolve_state as resolve_state_mod

    vault_name = getattr(args, "vault", None)
    if vault_name:
        named_vault = _resolve_named_vault(vault_name)
        if named_vault is None:
            return 1
        vault_root = str(named_vault.path)
    else:
        vault_root = str(vault_config_mod.resolve_active_vault())

    # A candidate capture is NOT fenced off a mid-resolution vault the way
    # ``record create`` is: losing a finding is worse than capturing it into a
    # tree ``lore resolve`` is still settling. The operator is told, and the
    # write goes ahead.
    if resolve_state_mod.vault_is_resolving(vault_root):
        print(
            resolve_state_mod.warning_notice(vault_root, "session candidate"),
            file=sys.stderr,
        )
    else:
        # Converge on the other devices' records before writing — throttled,
        # stderr-only, and unable to fail the capture. Skipped outright on a
        # mid-resolution vault: its tree is already the subject of a rebase
        # ``lore resolve`` owns, so there is nothing a pull could safely add.
        from . import sync as sync_mod

        sync_mod.implicit_pull(vault_root)

    key, rc = _resolve_session_key(args)
    if key is None:
        return rc

    kind = getattr(args, "kind", None)
    if not kind:
        print("error: --kind is required", file=sys.stderr)
        return 1
    phase = getattr(args, "phase", None)
    if not phase:
        print("error: --phase is required", file=sys.stderr)
        return 1

    try:
        raw_body = _read_stdin_body()
    except StdinSilentError as exc:
        print(f"lore: {exc}", file=sys.stderr)
        return 1
    safe_body = record_store_mod.neutralize_fences(raw_body)

    now = dt.datetime.now(dt.timezone.utc).strftime(session_store_mod.FLUSHED_AT_FORMAT)
    # A single record-candidate entry: a one-line header carrying KIND + PHASE +
    # timestamp, then the (possibly multi-line) neutralized body indented as a
    # block so the append stays one logical entry.
    entry_lines = [f"- candidate {now} kind={kind} phase={phase}"]
    for line in safe_body.splitlines():
        entry_lines.append(f"  {line}")
    entry = "\n".join(entry_lines)

    committer = vault_mod.resolve_committer_email() or vault_mod.resolve_user()
    try:
        session_store_mod.capture_candidate(
            key, entry,
            vault_root=vault_root,
            committer=committer,
            open_index=_open_session_index,
        )
    except Exception as exc:
        print(f"error: session candidate write failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_session_referenced(args) -> int:
    """``lore session referenced RECORD_ID --session-id ID``.

    Logs that RECORD_ID was used this session. On a **non-existent**
    session this is a **no-op — it creates NOTHING**; on an **existing** session it
    appends the reference line and bumps ``last-referenced-at`` in the sidecar
    ``annotations`` map, but **never flips status**. The append + bump + reindex are
    one race-safe critical section via ``session_store.capture_referenced``.

    **Resolved across EVERY configured vault**, exactly as ``show`` and ``flush``
    are: ``session candidate --vault NAME`` elects where the session record lives,
    so pinning ``referenced`` to the active vault meant a ``--vault``-captured
    session had no record where this looked — and the no-op-on-non-existent
    contract then swallowed the write silently, everywhere. The reference is
    appended to the existing record in EVERY vault holding the key, consistent
    with flush flushing every dirty instance of a split session; a key no vault
    holds is still the inert no-op (exit 0, nothing created).

    ``shared: true`` vaults are excluded from the fan-out and named in a notice:
    this is a WRITE into a record body, and a shared vault is untrusted
    multi-user content that no local command may modify by default. An unreadable
    vault config is a refusal, not a degrade to the default vault.
    """
    from ..record import store as record_store_mod
    from ..session import store as session_store_mod
    from ..vault import vault as vault_mod

    key, rc = _resolve_session_key(args)
    if key is None:
        return rc

    record_id = getattr(args, "record_id", None)
    if not record_id:
        print("error: RECORD_ID is required", file=sys.stderr)
        return 1

    now = dt.datetime.now(dt.timezone.utc).strftime(session_store_mod.FLUSHED_AT_FORMAT)
    # Neutralize fences in the entry: RECORD_ID is a free-form arg, so
    # a `<external-memory>` token in it must not land live in the session record —
    # the referenced boundary neutralizes uniformly like candidate/create/blob.
    entry = record_store_mod.neutralize_fences(f"- referenced {now} {record_id}")

    vaults = _resolve_all_vaults_strict("log a session reference")
    if vaults is None:
        return 1
    vaults, shared = _partition_writable_vaults(vaults)
    # Named only when a shared vault ACTUALLY holds the key — otherwise every
    # `referenced` call in an install that merely has a shared vault would carry
    # a notice about a vault it was never going to touch.
    skipped = [
        name for name, path in shared
        if session_store_mod.session_exists(str(path), key)
    ]
    if skipped:
        print(
            f"notice: not logging the reference into shared vault(s) "
            f"({', '.join(skipped)}) — shared vaults are untrusted and are "
            "never written to.",
            file=sys.stderr,
        )

    committer = vault_mod.resolve_committer_email() or vault_mod.resolve_user()
    # `capture_referenced` is itself the no-op-on-non-existent guard, so every
    # vault is offered the entry and only the holders take it. A per-vault failure
    # does not abort the rest — the remaining holders are still logged and the
    # command exits non-zero.
    worst = 0
    for _, vault in vaults:
        try:
            session_store_mod.capture_referenced(
                key, entry,
                vault_root=str(vault),
                committer=committer,
                open_index=_open_session_index,
            )
        except Exception as exc:
            print(
                f"error: session referenced write failed in {vault}: {exc}",
                file=sys.stderr,
            )
            worst = 1
    return worst


def add_session_subparser(sub) -> None:
    """Register the ``session`` command parser and its candidate/referenced/show actions."""
    # session subcommand: ``lore session candidate|referenced``.
    # A SEPARATE endpoint from ``lore record`` — it does NOT route through the
    # record write path (endpoint isolation). Registered with EXPLICIT
    # subcommand names; the action is required.
    p_session = sub.add_parser(
        "session",
        help="Log record candidates / references for a session (race-safe)",
    )
    p_session_sub = p_session.add_subparsers(dest="session_action", required=True)

    # ``lore session candidate --session-id ID --kind KIND --phase PHASE``.
    p_session_candidate = p_session_sub.add_parser(
        "candidate",
        help="Log a record-candidate (lazy-creates the session note, race-safe)",
    )
    p_session_candidate.add_argument(
        "--session-id", dest="session_id", default=None,
        help="Session id (GUID) to log under (default: $CLAUDE_CODE_SESSION_ID). "
             "Sanitized before any path use — separators/'..'/NUL/non-GUID rejected.",
    )
    p_session_candidate.add_argument(
        "--worktree", dest="worktree", default=None,
        help="Worktree-name key when no --session-id/env is set (default: detected). "
             "Sanitized to [A-Za-z0-9_-]+ before any path use.",
    )
    p_session_candidate.add_argument(
        "--kind", required=True,
        help="The kind of record being proposed (e.g. spec, decision, lesson).",
    )
    p_session_candidate.add_argument(
        "--phase", required=True,
        help="The session phase the candidate was proposed in (e.g. Plan, Build).",
    )
    p_session_candidate.add_argument(
        "--vault", dest="vault", default=None, metavar="NAME",
        help="Write the candidate into exactly this configured vault by name, "
             "instead of the cwd-blind active-vault resolution.",
    )
    p_session_candidate.set_defaults(func=cmd_session)

    # ``lore session referenced RECORD_ID --session-id ID``.
    p_session_referenced = p_session_sub.add_parser(
        "referenced",
        help="Log that a RECORD_ID was used this session (feeds last-referenced-at)",
    )
    p_session_referenced.add_argument(
        "record_id",
        metavar="RECORD_ID",
        help="The vault-relative record ID referenced this session (<kind>/<name>).",
    )
    p_session_referenced.add_argument(
        "--session-id", dest="session_id", default=None,
        help="Session id (GUID) to log under (default: $CLAUDE_CODE_SESSION_ID). "
             "Sanitized before any path use — separators/'..'/NUL/non-GUID rejected.",
    )
    p_session_referenced.add_argument(
        "--worktree", dest="worktree", default=None,
        help="Worktree-name key when no --session-id/env is set (default: detected). "
             "Sanitized to [A-Za-z0-9_-]+ before any path use.",
    )
    p_session_referenced.set_defaults(func=cmd_session)

    # ``lore session show [--json]`` — read THIS worktree's session record.
    p_session_show = p_session_sub.add_parser(
        "show",
        help="Read this worktree's session record (body, or sidecar with --json)",
    )
    p_session_show.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit {record_id, kind, name, sidecar, body} as JSON",
    )
    _add_session_selectors(p_session_show)
    p_session_show.set_defaults(func=cmd_session)
