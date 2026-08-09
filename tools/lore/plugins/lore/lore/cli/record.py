"""``lore record`` — create / update / delete / show vault records."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .common import (
    _load_vault_config,
    _read_stdin_body,
    _resolve_config_path,
    _resolve_groups_dir,
)

# The scope flags a record can carry, in resolution-precedence order. Does not
# include "default" -- that's a vault-routing fallback (see
# vault/resolve.py's _PRECEDENCE), not a scope a record's --repo/--product/
# --suite/--team flags can set directly.
_SCOPE_FLAGS = ("repo", "product", "suite", "team")


def _resolve_group_scopes(
    *,
    cwd: "Path",
    groups_dir: "Path | None",
    camp_state_dir: "Path | None" = None,
) -> "dict[str, str]":
    """Return the active camp group's default scope routing, or ``{}``.

    Resolves ``cwd`` to a camp group (via the shared
    :func:`layers.resolve_active_group_config`) and returns that group's declared
    ``[[lore_scopes]]`` binding as a ``{scope: vault_name}`` map. The map seeds
    write routing so a record created inside a bound workspace lands in the
    group's vault without an explicit ``--repo/--product/--suite/--team`` flag.

    Names are returned verbatim — exactly as an explicit routing flag would
    store them — so flag-origin and group-default-origin sidecar fields agree;
    vault election (``explain_resolution``) normalizes them at lookup. Returns
    ``{}`` on every failure path; the shared resolver owns the degradation
    contract (trailhead/camp unavailable, no group, overlap, malformed/unreadable
    config). ``camp_state_dir`` is forwarded so resolution stays isolated in tests.
    """
    from ..vault import layers as layers_mod

    cfg = layers_mod.resolve_active_group_config(
        groups_dir, cwd, camp_state_dir=camp_state_dir, degrade_target="default routing"
    )
    if cfg is None:
        return {}
    return {entry["scope"]: entry["name"] for entry in cfg.get("lore_scopes", [])}


def _resolve_record_op_vault(record_id: str, args) -> str:
    """Resolve the vault root that ``record show``/``record delete`` should act on.

    No scope flags (the common case) → :func:`_find_current_record_location`'s
    config-driven scan, passing the config this function already loaded so the
    scan doesn't re-read/re-parse ``config.json``. See that function's docstring
    for why the scan (not a routing computation) is what "locate an EXISTING
    record" means here. Falls through to the routing resolution below when the
    scan finds nothing, so a genuinely nonexistent record still gets a clean
    not-found rather than a scan failure.

    Explicit scope flag(s) (``--repo/--product/--suite/--team``) skip the scan
    and resolve via ``vault_resolve.resolve_vault`` instead — the same routing
    ``create`` uses. Kept as an escape hatch for the rare case where the same
    ``<kind>/<name>`` collides across more than one configured vault and the
    scan's first-match would be ambiguous.

    When **no** config exists, fall back to the config-resolved active vault
    (``vault_config.resolve_active_vault()`` → the floor) — vanilla usage is
    unchanged (Axiom 3).
    """
    from ..record import store as record_store_mod
    from ..vault import config as vault_config_mod
    from ..vault import resolve as vault_resolve_mod

    loaded = _load_vault_config()
    if loaded is None:
        return str(vault_config_mod.resolve_active_vault())
    _, vaults = loaded
    kind = record_id.split("/", 1)[0]
    participating_scopes = {
        flag: getattr(args, flag)
        for flag in _SCOPE_FLAGS
        if getattr(args, flag, None)
    }
    if not participating_scopes:
        try:
            return _find_current_record_location(record_id, loaded=loaded).vault_root
        except record_store_mod.RecordNotFoundError:
            pass
    chosen = vault_resolve_mod.resolve_vault(participating_scopes, kind, vaults)
    return str(chosen.path)


def _add_record_field_flags(parser) -> None:
    """Register the shared dedicated per-field flags on a record subparser.

    Adds the non-scope field flags common to ``create`` and ``update``:
    the ``--status`` scalar, the
    repeatable list flags with their per-item ``--unset-<field> VALUE`` removers,
    and the ``--related <kind>=<name>`` map flag. ``--title`` is NOT added here —
    create declares it as a required argument and update as an optional setter.
    Scope flags (``--team`` etc.) are NOT added here either.
    """
    parser.add_argument(
        "--status", default=None,
        help="Set the record's status (must be in the kind's vocabulary).",
    )
    parser.add_argument(
        "--keyword", dest="keyword", action="append", default=[], metavar="VALUE",
        help="Append a keyword (repeatable). Use --unset-keyword VALUE to remove one entry.",
    )
    parser.add_argument(
        "--unset-keyword", dest="unset_keyword", action="append", default=[], metavar="VALUE",
        help="Remove one keyword entry (repeatable). Absent value is a silent no-op.",
    )
    parser.add_argument(
        "--related-file", dest="related_file", action="append", default=[], metavar="VALUE",
        help="Append a related file/folder (repeatable). Use --unset-related-file VALUE "
             "to remove one entry.",
    )
    parser.add_argument(
        "--unset-related-file", dest="unset_related_file", action="append", default=[],
        metavar="VALUE",
        help="Remove one related file/folder entry (repeatable). Absent value is a no-op.",
    )
    parser.add_argument(
        "--related-url", dest="related_url", action="append", default=[], metavar="VALUE",
        help="Append a related URL (repeatable). Use --unset-related-url VALUE to remove "
             "one entry.",
    )
    parser.add_argument(
        "--unset-related-url", dest="unset_related_url", action="append", default=[],
        metavar="VALUE",
        help="Remove one related URL entry (repeatable). Absent value is a silent no-op.",
    )
    parser.add_argument(
        "--related-phase", dest="related_phase", action="append", default=[], metavar="VALUE",
        help="Append a related phase (repeatable). Use --unset-related-phase VALUE to "
             "remove one entry.",
    )
    parser.add_argument(
        "--unset-related-phase", dest="unset_related_phase", action="append", default=[],
        metavar="VALUE",
        help="Remove one related phase entry (repeatable). Absent value is a no-op.",
    )
    parser.add_argument(
        "--related", dest="related_pairs", action="append", default=[], metavar="KIND=NAME",
        help="Append NAME to the related[KIND] list (repeatable). Split on the first '='; "
             "KIND must be a valid record kind and both KIND and NAME must be non-empty.",
    )
    # Task graph edges (task-only; rejected on other kinds by validate()).
    parser.add_argument(
        "--depends-on", dest="depends_on", action="append", default=[], metavar="TASK",
        help="Append a task this task depends on (task-only, repeatable). Use "
             "--unset-depends-on TASK to remove one entry.",
    )
    parser.add_argument(
        "--unset-depends-on", dest="unset_depends_on", action="append", default=[],
        metavar="TASK",
        help="Remove one depends-on entry (repeatable). Absent value is a silent no-op.",
    )
    parser.add_argument(
        "--parent", dest="parent", default=None, metavar="TASK",
        help="Set this task's parent task (task-only). Use --unset-parent to clear it.",
    )
    parser.add_argument(
        "--unset-parent", dest="unset_parent", action="store_true", default=False,
        help="Clear this task's parent field.",
    )


def _add_map_field_flags(parser) -> None:
    """Register the shared ``--label``/``--annotation`` map flags on a record subparser.

    Sibling of :func:`_add_record_field_flags` for the map-field branch
    (:func:`record.fields.apply_map_labels_annotations`) — the ``--label``/``--annotation``/
    ``--unset-label``/``--unset-annotation`` quartet is identical on ``create``
    and ``update``, so both subparser blocks call this instead of repeating the
    four ``add_argument`` calls verbatim.
    """
    parser.add_argument(
        "--label", dest="label_pairs", action="append", default=[],
        metavar="KEY=VALUE",
        help="Set a label (repeatable, upsert). Split on first '=' so "
             "'namespace/name=value' works unescaped.",
    )
    parser.add_argument(
        "--annotation", dest="annotation_pairs", action="append", default=[],
        metavar="KEY=VALUE",
        help="Set an annotation (repeatable, upsert). Split on first '='.",
    )
    parser.add_argument(
        "--unset-label", dest="unset_labels", action="append", default=[],
        metavar="KEY",
        help="Remove a label key (repeatable). Absent key is a silent no-op.",
    )
    parser.add_argument(
        "--unset-annotation", dest="unset_annotations", action="append", default=[],
        metavar="KEY",
        help="Remove an annotation key (repeatable). Absent key is a silent no-op.",
    )


# ---------------------------------------------------------------------------
# Shared CLI-handler helpers (create/update/delete/show)
# ---------------------------------------------------------------------------


def _fail(errors: list[str], prefix: str = "") -> int:
    """Print each of *errors* to stderr (optionally *prefix*-ed) and return 1.

    The shared "surface every failure, block the write" tail shared by
    field-flag errors, task-graph guard errors, and
    :class:`record_store.RecordValidationError` messages across create/update.
    Field-flag and guard-error strings already carry their own ``error:``/
    ``graph-guard [...]:`` framing, so they pass through with the default empty
    *prefix*; ``RecordValidationError.errors`` are bare messages, so its two
    call sites pass ``prefix="error: "``.
    """
    for msg in errors:
        print(f"{prefix}{msg}", file=sys.stderr)
    return 1


def _require_record_id(args) -> str | None:
    """Validate ``args.record_id`` is ``<kind>/<name>``; else print + return None.

    Shared by show/delete/update — the same "missing or malformed RECORD_ID"
    check and error text, so the three handlers stay byte-for-byte consistent.
    """
    record_id = getattr(args, "record_id", None)
    if not record_id or "/" not in record_id:
        print(
            f"error: invalid RECORD_ID {record_id!r}; expected '<kind>/<name>'",
            file=sys.stderr,
        )
        return None
    return record_id


def _handle_write_error(exc: Exception, op: str) -> int:
    """Map a create/update write exception to a stderr line + exit code 1.

    The failure clauses shared by ``record create`` and ``record update``:
    a :class:`record_store.RecordValidationError` surfaces every message (prefixed
    ``error: ``); a :class:`record_store.ProvenanceError` and any other unexpected
    error each print one framed line, the catch-all naming *op*.
    :class:`record_store.RecordAlreadyExistsError` (create's sequence-number
    collision refusal — today only reachable via ``--kind adr``) gets the
    ``lore: `` prefix instead of ``error: ``, matching the CLI's explicit
    clean-refusal convention elsewhere (``_resolve_named_vault`` etc.).
    ``update``'s ``RecordNotFoundError``/``InvalidRecordIdError`` clause is NOT
    routed here — it is update-specific and caught before this handler.
    """
    from ..record import store as record_store_mod

    if isinstance(exc, record_store_mod.RecordValidationError):
        return _fail(exc.errors, prefix="error: ")
    if isinstance(exc, record_store_mod.ProvenanceError):
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if isinstance(exc, record_store_mod.RecordAlreadyExistsError):
        print(f"lore: {exc}", file=sys.stderr)
        return 1
    print(f"error: record {op} failed: {exc}", file=sys.stderr)
    return 1


def _print_guard_notices(notices: list[str]) -> None:
    """Print each non-blocking task-graph notice to stderr (success-path only).

    Shared trailer for create/update/delete — the stdout contract (RECORD_ID /
    moved line) stays the sole parseable stdout output, so these dependent /
    flow-out reminders go to stderr.
    """
    for msg in notices:
        print(msg, file=sys.stderr)


def cmd_record(args) -> int:
    """Dispatch ``lore record <action>`` — create / update / delete.

    Thin shell over ``record_store``. Routes to the appropriate handler
    by ``record_action``; all handlers are argv→library adapters with no
    validation or I/O logic of their own.
    """
    action = getattr(args, "record_action", None)
    if action == "create":
        return _cmd_record_create(args)
    if action == "update":
        return _cmd_record_update(args)
    if action == "delete":
        return _cmd_record_delete(args)
    if action == "show":
        return _cmd_record_show(args)
    print(
        f"lore record: unknown action {action!r}. "
        f"Use 'lore record create', 'lore record update', "
        f"'lore record delete', or 'lore record show'.",
        file=sys.stderr,
    )
    return 1


def _render_record(
    record_id: str, vault_root: str, as_json: bool, *, error_prefix: str = "error: "
) -> int:
    """Locate ``record_id`` in ``vault_root`` and print it; shared by the readers.

    Plain: writes the body (``.md``) to stdout. ``--json``: emits
    ``{record_id, kind, name, sidecar, body}`` — the ``sidecar`` dict is how
    callers read the un-indexed annotations (e.g. flush's ``flushed-at``
    watermark, which is sidecar-only and never lands in the index). A nonexistent
    or malformed record → non-zero + stderr (``error_prefix``-ed — ``record
    show --vault`` passes ``"lore: "`` to match its explicit-targeting error
    convention, mirroring ``record update --vault``; every other caller keeps
    the default ``"error: "``). Backs both ``lore record show`` (caller-supplied
    ``<kind>/<name>``) and ``lore session show`` (the resolved session record
    id), so the output shape is identical for both.
    """
    from ..record import store as record_store_mod

    try:
        loc = record_store_mod.locate_record(record_id, vault_root=vault_root)
    except (
        record_store_mod.RecordNotFoundError,
        record_store_mod.InvalidRecordIdError,
    ) as exc:
        print(f"{error_prefix}{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: record show failed: {exc}", file=sys.stderr)
        return 1

    body = (
        loc.body_path.read_text(encoding="utf-8")
        if loc.body_path.exists()
        else ""
    )

    if as_json:
        sidecar: dict[str, Any] = {}
        if loc.sidecar_path.exists():
            try:
                sidecar = json.loads(loc.sidecar_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                sidecar = {}
        payload = {
            "record_id": record_id,
            "kind": loc.kind,
            "name": loc.name,
            "sidecar": sidecar,
            "body": body,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(body)
    return 0


def _cmd_record_show(args) -> int:
    """``lore record show <kind>/<name> [--json] [--vault NAME]`` — the canonical
    record reader.

    The CLI-only way to read a record so agents and skills never poke at vault
    files directly. RECORD_ID must be ``<kind>/<name>``; a malformed ID or a
    nonexistent record → non-zero + stderr. To read THIS worktree's live session
    record (resolved by session-id / worktree, not a fixed name), use the
    dedicated ``lore session show``.

    ``--vault NAME`` mirrors ``record update --vault`` exactly: resolved via
    :func:`_resolve_named_vault`, then a direct
    ``record_store.locate_record(vault_root=...)`` in exactly that vault — no
    :func:`_resolve_record_op_vault` scan fallback. This is the read-side fix
    for the same collision ``update --vault`` addresses: a same-named record
    across more than one configured vault, where the cwd-blind scan's first
    match may not be the vault the caller means (e.g. ranger's queue
    classification and question extraction, which must read a specific vault's
    body, not whichever vault happens to sort first in config). An unknown
    ``--vault`` name, or a named vault that does not hold the record, errors
    ``lore: <msg>`` + nonzero — the same explicit-targeting convention
    ``update`` uses. Omitting ``--vault`` preserves
    :func:`_resolve_record_op_vault`'s scan exactly as before.
    """
    record_id = _require_record_id(args)
    if record_id is None:
        return 1
    as_json = bool(getattr(args, "json", False))

    vault_name = getattr(args, "vault", None)
    if vault_name:
        named_vault = _resolve_named_vault(vault_name)
        if named_vault is None:
            return 1
        return _render_record(
            record_id, str(named_vault.path), as_json, error_prefix="lore: "
        )

    vault_root = _resolve_record_op_vault(record_id, args)
    return _render_record(record_id, vault_root, as_json)


def _cmd_record_delete(args) -> int:
    """``lore record delete RECORD_ID`` — thin shell over ``record_store``.

    Removes the body (``.md``), sidecar (``.json``), and index row for RECORD_ID
    in one operation. Uses :func:`record_store.delete_record`, which is the
    transactional delete primitive — this function does NOT
    re-implement removal logic.

    RECORD_ID must be in ``<kind>/<name>`` format and refer to an existing record.
    An invalid format or nonexistent record → non-zero + clear stderr.
    """
    from .. import locking as locking_mod
    from ..record import guards as guards_mod
    from ..record import store as record_store_mod

    record_id = _require_record_id(args)
    if record_id is None:
        return 1

    # Resolve the target vault: see _resolve_record_op_vault for the two-path
    # contract (config-driven scan when no scope flag is given, explicit-flag
    # routing otherwise). A record whose vault was removed from config resolves
    # to the default floor and surfaces a clean RecordNotFoundError below rather
    # than acting on an orphaned target.
    vault_root = _resolve_record_op_vault(record_id, args)

    # Dependent-warning: deleting a task that others depend-on is allowed (delete
    # is never blocked) but warns, listing the dependents. Computed before the
    # delete off the on-disk task graph; a no-op for every non-task kind.
    kind, _, name = record_id.partition("/")
    _, guard_notices = guards_mod.evaluate_task_guards(
        kind=kind,
        name=name,
        sidecar={},
        body="",
        vault_root=vault_root,
        status_set=None,
        deleting=True,
    )

    try:
        # The lock is held across the commit, symmetric with the create path: a
        # commit after the release publishes the row drop to a writer that already
        # holds the lock and has therefore already decided the record exists.
        # ``delete_record``'s own acquisition is a reentrant depth bump.
        with locking_mod.vault_write_lock(vault_root), \
                record_store_mod.index_transaction() as conn:
            record_store_mod.delete_record(record_id, conn, vault_root=vault_root)
            conn.commit()
    except (
        record_store_mod.RecordNotFoundError,
        record_store_mod.InvalidRecordIdError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: record delete failed: {exc}", file=sys.stderr)
        return 1

    _print_guard_notices(guard_notices)

    return 0


def _cmd_record_create(args) -> int:
    """``lore record create`` — thin shell over ``record_store``.

    Builds the sidecar dict, reads the body from stdin (empty string when stdin is
    not piped), calls ``place_record`` + ``validate_and_write``, and prints the
    vault-relative RECORD_ID on stdout.

    Body is read verbatim from stdin — a leading ``---`` block is **never**
    parsed as frontmatter; it is stored as body text.

    Routing flags (--repo / --product / --suite / --team) are dual-purpose:
    they both select the destination vault
    via ``place_record``'s ``scope`` argument AND write the namesake sidecar field
    with the same value.  One input drives both effects, so the field value and the
    routing vault always agree — a record cannot carry ``team: beta`` while living
    in alpha's vault.  Non-scope sidecar fields are set through the dedicated
    per-field flags (``--status``, ``--keyword``, ``--related-*``, ``--related``)
    applied by :func:`record.fields.apply_record_fields`.

    ``--kind adr`` assigns its own per-vault sequence number: the title is
    rewritten to ``"ADR-NNN: <title>"`` (:func:`record_store.format_adr_title`,
    scanning the DESTINATION vault's ``adr/`` directory via
    :func:`record_store.next_adr_number`) before ``place_record`` derives the
    slug from it, and the write goes through
    ``validate_and_write(require_new=True)``, which holds a lock on the number
    itself for the write's duration. The scan only picks a candidate: two
    concurrent creates read the same highest number, so it is that write-time
    lock that makes exactly one of them win and the other refuse cleanly —
    never a silent suffix, and never a clobber.
    """
    from .. import locking as locking_mod
    from ..record import fields as fields_mod
    from ..record import guards as guards_mod
    from ..record import store as record_store_mod
    from ..vault import config as vault_config_mod
    from ..vault import resolve as vault_resolve_mod

    # --kind is required.
    kind = getattr(args, "kind", None)
    if not kind:
        print("error: --kind is required", file=sys.stderr)
        return 1

    # --title is required (operator-required by the record model).
    title = getattr(args, "title", None)
    if not title:
        print("error: --title is required", file=sys.stderr)
        return 1

    # Seed the sidecar with the required operator fields. ``keywords`` is optional
    # (optional) — it is populated only when --keyword is supplied.
    sidecar: dict = {
        "kind": kind,
        "title": title,
    }

    # Apply the dedicated per-field flags (--status / --keyword / --related-* /
    # --related). On create --title is the
    # required positional already seeded above, so the applier leaves it alone.
    sidecar, field_errors = fields_mod.apply_record_fields(sidecar, args)
    if field_errors:
        return _fail(field_errors)

    # Apply --label / --annotation / --unset-label / --unset-annotation.
    sidecar = fields_mod.apply_map_labels_annotations(
        sidecar,
        label_pairs=list(getattr(args, "label_pairs", None) or []),
        annotation_pairs=list(getattr(args, "annotation_pairs", None) or []),
        unset_labels=list(getattr(args, "unset_labels", None) or []),
        unset_annotations=list(getattr(args, "unset_annotations", None) or []),
    )

    # Body from stdin. No stdin → empty body.  The leading ``---``
    # check is intentionally absent — we read verbatim.
    body = _read_stdin_body()

    # --- vault routing -----------------------------------------------------
    # ``participating_scopes`` is built from the routing FLAGS
    # (--repo/--product/--suite/--team), then — only when a config.json exists and
    # multi-vault routing is therefore active — augmented with the active camp
    # group's [[lore_scopes]] binding. When a config loads, resolution picks the
    # destination vault and a confirmation line names the elected vault + scope
    # (+ any fall-through reason). With NO config we keep vanilla behavior: the
    # active vault, shared=0, and NO group-default seeding — there is no vault to
    # route to, so we never stamp implicit scope fields the user did not type.
    loaded = _load_vault_config()
    participating_scopes: dict = {}
    for flag in _SCOPE_FLAGS:
        val = getattr(args, flag, None)
        if val:
            participating_scopes[flag] = val
            sidecar[flag] = val  # one input → both routing and field; field can never contradict vault

    shared_flag = 0
    routing_line: str | None = None
    seeded_scopes: set[str] = set()
    if loaded is not None:
        # Group-default seeding: a record created inside a camp workspace whose
        # group declares a [[lore_scopes]] binding inherits that scope when no
        # explicit flag selects it. ``setdefault`` on BOTH ``participating_scopes``
        # and the namesake sidecar field (never plain assignment) keeps an
        # explicit flag authoritative. ``seeded_scopes`` records which scopes were
        # newly inserted from the binding; the confirmation line is annotated
        # ``(via group default)`` only when the *elected* scope is one of these,
        # so a higher-precedence typed flag carries no provenance suffix even when
        # a lower scope was seeded. ``Path.cwd()`` is resolved defensively: a
        # deleted cwd (e.g. a removed worktree) degrades to no seeding rather than
        # crashing the capture.
        try:
            cwd = Path.cwd()
        except OSError:
            cwd = None
        if cwd is not None:
            for seed_scope, seed_name in _resolve_group_scopes(
                cwd=cwd, groups_dir=_resolve_groups_dir()
            ).items():
                if seed_scope not in participating_scopes:
                    participating_scopes[seed_scope] = seed_name
                    seeded_scopes.add(seed_scope)
                sidecar.setdefault(seed_scope, seed_name)

        _, vaults = loaded
        resolution = vault_resolve_mod.explain_resolution(
            participating_scopes, kind, vaults
        )
        chosen = resolution.chosen
        vault_root = Path(chosen.path)
        shared_flag = vault_config_mod.shared_flag(chosen)
        routing_line = f"Routed to vault: {chosen.name} ({chosen.scope})"
        if chosen.scope in seeded_scopes:
            routing_line += " (via group default)"
        if resolution.skipped is not None:
            routing_line += (
                f"\n{resolution.skipped.name} excluded: {resolution.skipped_reason}"
            )
    else:
        vault_root = Path(vault_config_mod.resolve_active_vault())

    # The scope string passed to place_record reflects the FINAL participating
    # scopes — typed flags plus any group-default seeds — so the routing hint
    # never disagrees with the vault the record actually lands in.
    scope = (
        ",".join(
            f"{flag}:{participating_scopes[flag]}"
            for flag in _SCOPE_FLAGS
            if flag in participating_scopes
        )
        or None
    )

    # ADR per-vault sequence numbering: assigned here (not inside
    # place_record) because the numbered title must land in the sidecar
    # ("title") AND drive the slug place_record derives from ``name`` — two
    # effects from one computed value, same one-input-drives-both-effects
    # shape as the scope flags above. Scanning the DESTINATION vault's adr/
    # directory (not the active vault) matters when routing sends this record
    # somewhere other than the default vault. The write-time collision guard
    # (validate_and_write's ``require_new``, below) is what actually makes the
    # refusal atomic — this scan only picks a candidate number.
    if kind == "adr":
        adr_kind_dir = vault_root / "adr"
        adr_number = record_store_mod.next_adr_number(adr_kind_dir)
        title = record_store_mod.format_adr_title(adr_number, title)
        sidecar["title"] = title

    guard_notices: list[str] = []
    try:
        # The create critical section starts at ``place_record``, not at the write:
        # stem collision resolution is a check-then-act, so two concurrent creates
        # of the same title would otherwise both claim the same stem and one record
        # would be lost. Held through ``conn.commit()`` so the index row for the
        # claimed stem is durable before the next writer picks a stem.
        with locking_mod.vault_write_lock(vault_root), \
                record_store_mod.index_transaction() as conn:
            location = record_store_mod.place_record(
                name=title,
                kind=kind,
                scope=scope,
                vault_root=str(vault_root),
            )
            # Task graph guards run against the resolved destination vault (the
            # in-flight record overlaid on the on-disk task graph) before any
            # write. Blocking errors → nothing written; notices are held for the
            # success path. A no-op for every non-task kind.
            guard_errors, guard_notices = guards_mod.evaluate_task_guards(
                kind=kind,
                name=location.name,
                sidecar=sidecar,
                body=body,
                vault_root=str(vault_root),
                status_set=getattr(args, "status", None),
            )
            if guard_errors:
                return _fail(guard_errors)
            record_id = record_store_mod.validate_and_write(
                location=location,
                sidecar=sidecar,
                body=body,
                conn=conn,
                shared=shared_flag,
                require_new=(kind == "adr"),
            )
            conn.commit()
    except Exception as exc:
        return _handle_write_error(exc, "create")

    # Routing confirmation goes to STDERR so the RECORD_ID stays
    # the sole parseable stdout line (the create contract — test_record_cli_create).
    if routing_line is not None:
        print(routing_line, file=sys.stderr)
    # Non-blocking graph notices (dependent-warning, flow-out reminder) — stderr,
    # so stdout stays the sole parseable RECORD_ID line.
    _print_guard_notices(guard_notices)

    # Print the vault-relative RECORD_ID on stdout.
    print(record_id)
    return 0


def _resolve_named_vault(name: str):
    """Resolve a ``--vault NAME`` argument to its configured :class:`vault_config.Vault`.

    The single *locate-by-vault* path, shared by every verb that takes a
    ``--vault NAME`` argument (``record update``'s current-location targeting
    and ``lore task list``'s per-vault listing — see
    ``cli/task.py:_cmd_task_list``): loads ``config.json`` via
    :func:`vault_config.load_config` and matches on the normalized name. An
    unreadable config or a name absent from it prints ``lore: <msg>`` to stderr
    and returns ``None`` — callers treat ``None`` as "stop, nothing located,
    nothing read or written" (never a silent fall-through to some other vault,
    nor to ``_find_current_record_location``'s scan).

    This is deliberately NOT the same lookup ``_resolve_destination_root`` uses
    (``explain_resolution``'s scope+precedence routing) — ``--vault`` names a
    vault directly, with no scope/precedence involved, because it answers
    "which vault is meant RIGHT NOW", not "where should a record be routed".
    """
    from ..vault import config as vault_config_mod

    config_path = _resolve_config_path()
    try:
        vaults = vault_config_mod.load_config(str(config_path))
    except (OSError, ValueError) as exc:
        print(f"lore: cannot read config: {exc}", file=sys.stderr)
        return None
    except vault_config_mod.VaultConfigError as exc:
        print(str(exc), file=sys.stderr)
        return None

    normalized = vault_config_mod.normalize_vault_name(name)
    vault = next((v for v in vaults if v.name == normalized), None)
    if vault is None:
        print(f"lore: vault {normalized!r} is not configured", file=sys.stderr)
        return None
    return vault


def _find_current_record_location(record_id: str, loaded=None):
    """Locate the record's CURRENT vault, independent of the new scope-flag values.

    The scope flags (``--team`` etc.) now mean **destination**, so the
    current location can no longer be derived from them (deriving it from them
    would produce a spurious ``RecordNotFoundError``). Strategy: scan every
    configured vault path with :func:`record_store.locate_record` until the record
    is found — config-driven, index-independent, and with no circular dependence on
    the sidecar's own scope fields. Falls back to the active vault when no config
    exists (vanilla, Axiom 3).

    ``loaded`` accepts an already-resolved :func:`_load_vault_config` result so a
    caller that loaded config for its own purposes doesn't trigger a second
    read+parse of ``config.json``; omit it to load fresh.

    Shared by ``record update`` (always — its scope flags are destination-only)
    and, via :func:`_resolve_record_op_vault`, ``record show``/``record delete``
    when no scope flag is supplied — so all three locate an EXISTING record the
    same way, and a record ``create`` routed to a non-default vault (an explicit
    flag or a camp group default) stays reachable everywhere without re-supplying
    that routing.

    Raises :class:`record_store.RecordNotFoundError` when no configured vault holds
    the record.
    """
    from ..record import store as record_store_mod
    from ..vault import config as vault_config_mod

    if loaded is None:
        loaded = _load_vault_config()
    if loaded is None:
        root = str(vault_config_mod.resolve_active_vault())
        return record_store_mod.locate_record(record_id, vault_root=root)
    _, vaults = loaded
    for v in vaults:
        try:
            return record_store_mod.locate_record(record_id, vault_root=str(v.path))
        except record_store_mod.RecordNotFoundError:
            continue
    raise record_store_mod.RecordNotFoundError(f"record not found: {record_id}")


def _resolve_destination_root(merged_sidecar: dict, kind: str) -> tuple[str, int]:
    """Resolve the DESTINATION ``(vault_root, shared_flag)`` from the merged scope.

    Merged scope = the existing sidecar scope fields overlaid with the new flag
    values (the merge is already baked into ``merged_sidecar`` by the caller).
    Resolved via the **same create-side resolver** (:func:`vault_resolve.explain_resolution`)
    so a zero-prior-scope record + ``--team beta`` resolves exactly like a fresh
    create. Falls back to the active vault when no config exists (vanilla).

    Also returns the destination vault's ``shared`` trust flag (0 = own/trusted,
    1 = ``shared: true``) so the caller stamps the index row with the trust of the
    vault the record actually lands in — symmetric with create (which passes
    ``vault_config.shared_flag(chosen)``). Vanilla (no config) is always 0.
    """
    from ..vault import config as vault_config_mod
    from ..vault import resolve as vault_resolve_mod

    loaded = _load_vault_config()
    if loaded is None:
        return str(vault_config_mod.resolve_active_vault()), 0
    _, vaults = loaded
    participating_scopes = {
        flag: merged_sidecar[flag]
        for flag in _SCOPE_FLAGS
        if merged_sidecar.get(flag)
    }
    resolution = vault_resolve_mod.explain_resolution(participating_scopes, kind, vaults)
    return str(resolution.chosen.path), vault_config_mod.shared_flag(resolution.chosen)


def _resolve_current_vault_shared(location) -> tuple[str, int]:
    """Destination = the record's current vault, unchanged, with its trust flag.

    Used when no explicit ``--repo/--product/--suite/--team`` flag accompanied
    this ``record update`` call: there is nothing to re-route on, so the
    destination is simply where the record already lives — never re-derived
    from stale sidecar scope fields (see :func:`_resolve_destination_root`,
    which a stored ``repo``/``product``/``suite``/``team`` data field would
    otherwise feed into a fresh resolution and silently relocate the record).
    """
    from ..vault import config as vault_config_mod

    loaded = _load_vault_config()
    if loaded is None:
        return location.vault_root, 0
    _, vaults = loaded
    current = Path(location.vault_root).resolve()
    for v in vaults:
        if Path(v.path).resolve() == current:
            return location.vault_root, vault_config_mod.shared_flag(v)
    return location.vault_root, 0


class _UpdateAborted(Exception):
    """An update step already reported its own error; carries the exit code.

    ``record update``'s read-modify-apply step is a re-runnable inner call (the
    relocation path runs it twice, under two lock spans), so it cannot ``return``
    the handler's exit code directly. It raises this instead, and the handler
    returns the carried code unchanged.
    """

    def __init__(self, code: int = 1) -> None:
        super().__init__(code)
        self.code = code


def _cmd_record_update(args) -> int:
    """``lore record update RECORD_ID`` (with auto-move) — thin shell.

    Body sources (mutually exclusive on stdin):
      - **Full-body** (default, piped stdin): stdin replaces the whole body.
      - **``--diff``**: stdin is a unified diff applied to the existing body via
        ``record_store.apply_unified_diff``. The post-hunk body flows through
        the shared validate/stamp/neutralize step before the atomic rename, so a
        hunk injecting an ``<external-memory>`` token cannot land a live fence
        (the diff path is not a neutralization bypass). Any hunk
        failing → non-zero, record byte-for-byte unmodified, no index update;
        rejected hunks print to stderr one-line-per-hunk for agent retry.
      - **Metadata-only** (no stdin): the body is unchanged and only the
        dedicated per-field flags apply. A ``note: no stdin — body
        unchanged, metadata-only update`` is printed to **stderr** (exit stays 0)
        so an agent that *forgot* to pipe a body can detect the mode.

    **``--vault NAME`` — explicit current-location targeting.** Locates the
    record in exactly the named configured vault (:func:`_resolve_named_vault`)
    instead of :func:`_find_current_record_location`'s config-order first-match
    scan — the fix for a same-named record colliding across more than one
    configured vault, where the scan's first match may not be the vault the
    caller means. An unknown ``--vault`` name, or a named vault that does not
    hold the record, errors plainly (``lore: <msg>``, nonzero) and never falls
    back to the scan. Orthogonal to the destination re-routing flags below:
    omitting ``--vault`` preserves the scan exactly as before, and ``--vault``
    composes with ``--repo/--product/--suite/--team`` (current location vs.
    re-routed destination are independent concerns).

    **``--vault`` alone pins the destination too.** When ``--vault`` is given
    and NO explicit destination scope flag (``--repo/--product/--suite/--team``)
    accompanies it, the destination is the named vault itself — the merged-scope
    re-resolution below is skipped entirely, so a record whose sidecar carries
    no scope field at all (a legacy/unstamped record) is never silently moved to
    the default vault just because its empty scope resolves fresh there. An
    explicit destination scope flag alongside ``--vault`` still means "re-route"
    exactly as before — the two concerns compose, they don't gate each other.

    **Scope flags drive automatic relocation — and ONLY an explicit scope flag
    on THIS call does.** ``--team/--suite/--product/--repo`` are field-setters
    that, when passed on this invocation, also re-resolve the destination vault
    from the **merged scope** (existing sidecar scope fields overlaid with the
    new flag values). When none of those four flags is passed, the destination
    re-resolution is skipped entirely and the destination is simply the
    record's current vault (:func:`_resolve_current_vault_shared`) — the merged
    sidecar is NEVER fed back into the create-side resolver on its own, so a
    stored ``repo``/``product``/``suite``/``team`` data field (ordinary content,
    not a routing request) can never silently relocate the record on a
    metadata-only update. The flow: (1) locate the record's CURRENT vault via a
    config scan (decoupled from the now-destination-meaning scope flags —
    :func:`_find_current_record_location`); (2) apply ALL field mutations in
    memory; (3) re-resolve the destination via the create-side resolver
    (:func:`_resolve_destination_root`) only if a scope flag was passed this
    call, else keep the current vault; (4) validate + stamp + neutralize the
    mutated record, then write it ONCE at its final location:

      - destination == current vault (compared on ``Path.resolve()``-normalized
        roots, so a trailing-slash / symlink / ``~`` mismatch never triggers a
        spurious self-move): a normal in-place ``validate_and_write``, no move.
      - destination != current vault: a single durable write AT the destination via
        ``move_record``'s in-memory overrides (the mutated sidecar is NEVER written
        at the old location and then moved), old artifacts
        removed last (copy→repoint→delete). A structured
        ``moved: <old id> → <new id>`` line is printed to **stdout** in addition to
        the RECORD_ID so a tool can detect the relocation (no silent move).

    **Locking.** The whole read-modify-write runs under the source vault's write
    lock. Relocation needs both vaults, and holding one of the pair while
    ``move_record`` acquires the sorted pair would break that total order — so the
    single lock is dropped, the pair is acquired through the sorted helper, and the
    read-modify-apply is re-run under it (steps 2–3 above), which is what keeps a
    write that lands in the swap window from being silently overwritten.

    ``updated-at``/``updated-by`` are re-stamped every update; ``created-*`` are
    preserved. The relocation is automatic; there is no manual ``--move-to`` flag —
    removing it also closed an unconfined-destination write path (the dest is now
    only ever a config-declared vault root, and ``move_record`` confines it too).

    Invalid/nonexistent RECORD_ID → non-zero.
    """
    import json
    from contextlib import ExitStack

    from .. import locking as locking_mod
    from ..record import fields as fields_mod
    from ..record import guards as guards_mod
    from ..record import store as record_store_mod
    from ..vault import config as vault_config_mod

    record_id = _require_record_id(args)
    if record_id is None:
        return 1

    # --vault NAME: resolve up front (no I/O against the record itself yet) so
    # an unknown name or an unreadable config fails before the index
    # transaction opens below — see _resolve_named_vault's docstring for why
    # this is a distinct locate-by-vault path from the scan.
    vault_name = getattr(args, "vault", None)
    named_vault = None
    if vault_name:
        named_vault = _resolve_named_vault(vault_name)
        if named_vault is None:
            return 1

    use_diff = bool(getattr(args, "diff", False))

    # stdin detection: a TTY is never "piped"; otherwise read what's there. An
    # empty read (no pipe / /dev/null / closed stdin) is the metadata-only signal
    # — distinct from a non-empty body replace. An intentional
    # empty-body replace is degenerate and out of scope; clear the body via a
    # ``--diff`` deleting every line instead.
    stdin_text = _read_stdin_body()
    has_stdin = stdin_text != ""

    guard_notices: list[str] = []
    notice_shown: set[str] = set()
    try:
        with record_store_mod.index_transaction() as conn, ExitStack() as locks:
            # (1) Resolve the CURRENT location, decoupled from the scope flags
            # (which now mean destination); a missing record → RecordNotFoundError.
            # --vault skips the scan entirely and locates ONLY in the named
            # vault — a record absent there is a RecordNotFoundError too, never
            # a fall-through to the other configured vaults.
            if named_vault is not None:
                location = record_store_mod.locate_record(
                    record_id, vault_root=str(named_vault.path)
                )
            else:
                location = _find_current_record_location(record_id)

            # An update is a read-modify-write, so the WHOLE of it — the reads
            # below, the in-memory mutation, and the write+index upsert — is one
            # critical section. Locking only the write loses updates silently: two
            # concurrent `--keyword` updates would both read the same pre-state and
            # the second write would drop the first one's field. Taken here, the
            # instant the vault is known (the locate above is the only unlocked
            # read); the write path's own acquisition is a reentrant depth bump, and
            # the lock is released after ``conn.commit()`` below because the
            # ExitStack is entered inside the transaction.
            locks.enter_context(locking_mod.vault_write_lock(location.vault_root))

            def read_apply_and_guard() -> tuple[dict, str, list[str]]:
                """Read the record from disk, apply this call's mutations, guard it.

                Returns ``(sidecar, body, notices)``; raises :class:`_UpdateAborted`
                when a step has already reported its own error.

                MUST be called with the record's vault write lock held — this is
                the read half of the read-modify-write. It is deliberately
                re-runnable: the relocation path calls it a SECOND time, under the
                source+destination lock pair, so a write that landed while it was
                between locks is picked up instead of overwritten.
                """
                # Load the existing sidecar so the field flags mutate the live record.
                existing_sidecar: dict = {}
                if location.sidecar_path.exists():
                    try:
                        existing_sidecar = json.loads(
                            location.sidecar_path.read_text(encoding="utf-8")
                        )
                    except (OSError, ValueError):
                        existing_sidecar = {}
                if not isinstance(existing_sidecar, dict):
                    existing_sidecar = {}

                existing_body = (
                    location.body_path.read_text(encoding="utf-8")
                    if location.body_path.exists()
                    else ""
                )

                # --- resolve the new body -------------------------------------
                if use_diff:
                    # stdin is a unified diff applied to the existing body.
                    try:
                        new_body, _ = record_store_mod.apply_unified_diff(
                            existing_body, stdin_text
                        )
                    except record_store_mod.DiffRejectError as exc:
                        # Atomic reject: body byte-for-byte unchanged, no
                        # index update. Parseable one-line-per-hunk stderr for retry.
                        for header, reason in exc.rejected:
                            print(f"rejected hunk {header}: {reason}", file=sys.stderr)
                        raise _UpdateAborted() from None
                    except record_store_mod.DiffFormatError as exc:
                        print(f"error: unparseable diff: {exc}", file=sys.stderr)
                        raise _UpdateAborted() from None
                elif has_stdin:
                    # Full-body replace.
                    new_body = stdin_text
                else:
                    # Metadata-only: body unchanged. Emit the notice to
                    # stderr — exit stays 0. Printed once even when this runs
                    # twice (the relocation path's re-read).
                    new_body = existing_body
                    if "metadata-only" not in notice_shown:
                        notice_shown.add("metadata-only")
                        print(
                            "note: no stdin — body unchanged, metadata-only update",
                            file=sys.stderr,
                        )

                # (2) --- apply ALL field mutations to the sidecar IN MEMORY ----
                # Scope flags (--team etc.) become field-setters that drive the merged
                # scope; the non-scope per-field flags (--status / --title / --keyword
                # / --related-*) reuse the shared applier. --title is optional here.
                sidecar = dict(existing_sidecar)
                for flag in _SCOPE_FLAGS:
                    val = getattr(args, flag, None)
                    if val:
                        sidecar[flag] = val

                sidecar, field_errors = fields_mod.apply_record_fields(sidecar, args)
                if field_errors:
                    raise _UpdateAborted(_fail(field_errors))

                # Apply --label / --annotation / --unset-label / --unset-annotation.
                sidecar = fields_mod.apply_map_labels_annotations(
                    sidecar,
                    label_pairs=list(getattr(args, "label_pairs", None) or []),
                    annotation_pairs=list(
                        getattr(args, "annotation_pairs", None) or []
                    ),
                    unset_labels=list(getattr(args, "unset_labels", None) or []),
                    unset_annotations=list(
                        getattr(args, "unset_annotations", None) or []
                    ),
                )

                # Task graph guards run against the record's CURRENT vault (where its
                # parent/depends-on relatives live), with the mutated record overlaid.
                # Blocking errors → nothing written; notices are held for the success
                # path. A no-op for every non-task kind.
                guard_errors, notices = guards_mod.evaluate_task_guards(
                    kind=location.kind,
                    name=location.name,
                    sidecar=sidecar,
                    body=new_body,
                    vault_root=location.vault_root,
                    status_set=getattr(args, "status", None),
                )
                if guard_errors:
                    raise _UpdateAborted(_fail(guard_errors))

                return sidecar, new_body, notices

            sidecar, new_body, guard_notices = read_apply_and_guard()

            # (3) re-resolve the DESTINATION (root + shared trust), gated on an
            # explicit --repo/--product/--suite/--team flag being passed THIS call.
            #
            # When --vault named the current location AND no explicit destination
            # scope flag was given on this call, the destination IS that named vault
            # -- skip the merged-scope re-resolution entirely. Without this, a record
            # whose sidecar carries no repo/product/suite/team scope field (e.g. a
            # legacy/unstamped record) would resolve fresh off an EMPTY scope, which
            # always lands on the default vault (vault/resolve.py's totality floor),
            # silently moving it OUT of the vault the caller explicitly targeted with
            # --vault, even though nothing asked for a re-route.
            #
            # Otherwise, with no --vault in play: no explicit scope flag this call
            # means no re-resolution at all -- the destination is just the record's
            # current vault (_resolve_current_vault_shared), never re-derived from
            # merged_sidecar. A stored repo/product/suite/team field is ordinary data,
            # not a standing routing request, so it must not feed the create-side
            # resolver on its own (that was the bug: a metadata-only update silently
            # relocated a record off a stale/unconfigured stored scope value). An
            # explicit scope flag still means "re-route", --vault or not.
            def resolve_destination(sidecar: dict) -> tuple[str, bool]:
                explicit_destination_flag = any(
                    getattr(args, flag, None) for flag in _SCOPE_FLAGS
                )
                if named_vault is not None and not explicit_destination_flag:
                    return str(named_vault.path), vault_config_mod.shared_flag(
                        named_vault
                    )
                if not explicit_destination_flag:
                    return _resolve_current_vault_shared(location)
                return _resolve_destination_root(sidecar, location.kind)

            dest_root, dest_shared = resolve_destination(sidecar)
            same_vault = (
                Path(dest_root).resolve() == Path(location.vault_root).resolve()
            )

            moved_line: str | None = None
            if same_vault:
                # (4a) no move — normal in-place write (validates + stamps). Pass the
                # resolved vault's shared trust so an in-place update of a record in a
                # shared vault does not silently un-fence its index row (reset to 0).
                new_id = record_store_mod.validate_and_write(
                    location=location, sidecar=sidecar, body=new_body, conn=conn,
                    shared=dest_shared,
                )
                conn.commit()
            else:
                # (4b) Relocation. Two lock spans, deliberately:
                #
                # The single source lock is released FIRST because ``move_record``
                # acquires source+destination as a SORTED set, which is what keeps
                # two opposed cross-vault moves from deadlocking — and holding one
                # of the pair across that call is exactly what breaks the total
                # order: this update would hold A and want B while its opposite
                # holds B and wants A, and the lock has no timeout.
                #
                # Releasing alone would open a lost-write window: a concurrent
                # same-record update can commit inside it, and re-using the
                # pre-release snapshot would silently overwrite that write. So the
                # record is RE-READ and the mutations RE-APPLIED under the pair,
                # which is acquired here (through the same sorted helper, so
                # ``move_record``'s own acquisition is a reentrant bump and the
                # total order still holds) and held across ``conn.commit()``.
                locks.close()
                with locking_mod.vault_write_locks(location.vault_root, dest_root):
                    sidecar, new_body, guard_notices = read_apply_and_guard()
                    # The re-read can only change the destination if a concurrent
                    # writer re-scoped the record; the pair already held is then
                    # the wrong pair, so this call bails instead of moving the
                    # record to a vault resolved from a superseded scope.
                    recheck_root, dest_shared = resolve_destination(sidecar)
                    if Path(recheck_root).resolve() != Path(dest_root).resolve():
                        print(
                            "error: the record's destination vault changed "
                            "concurrently — nothing written, re-run the update",
                            file=sys.stderr,
                        )
                        return 1

                    # validate + stamp + neutralize the mutated record IN MEMORY,
                    # then write it ONCE at the destination via move_record
                    # overrides — the mutated sidecar is NEVER written at the old
                    # location and then moved (single durable write at destination).
                    stamped, safe_body = record_store_mod.validate_stamp_neutralize(
                        location, sidecar, new_body
                    )
                    # Destination paths are confined via the shared
                    # ``confine_record_id`` seam (the same guard every
                    # RECORD_ID-bearing op uses) rather than hand-rolled — so a
                    # destination vault whose ``kind`` dir is symlinked outside its
                    # root is rejected here, not merely relied upon downstream.
                    dest_kind, dest_name, dest_body_path, dest_sidecar_path = (
                        record_store_mod.confine_record_id(location.record_id, dest_root)
                    )
                    dest_location = record_store_mod.RecordLocation(
                        vault_root=dest_root,
                        kind=dest_kind,
                        name=dest_name,
                        record_id=location.record_id,  # ID is vault-root-agnostic
                        body_path=dest_body_path,
                        sidecar_path=dest_sidecar_path,
                    )
                    new_id = record_store_mod.move_record(
                        old_id=location.record_id,
                        new_location=dest_location,
                        conn=conn,
                        old_vault_root=location.vault_root,
                        new_sidecar=stamped,
                        new_body=safe_body,
                        shared=dest_shared,
                    )
                    # Inside the pair, mirroring the delete path: a commit after
                    # the release would publish the repointed rows to a writer
                    # that already holds the lock.
                    conn.commit()
                moved_line = f"moved: {location.record_id} → {new_id}"
    except _UpdateAborted as aborted:
        return aborted.code
    except (
        record_store_mod.RecordNotFoundError,
        record_store_mod.InvalidRecordIdError,
    ) as exc:
        # Update-specific: only update locates an existing record, so this
        # not-found/invalid-id clause is NOT part of the shared write-error
        # handler and is caught before it. A --vault miss gets the "lore: "
        # prefix (the explicit-targeting error convention — see
        # _resolve_named_vault) rather than the scan's plain "error: ".
        prefix = "lore: " if named_vault is not None else "error: "
        print(f"{prefix}{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        return _handle_write_error(exc, "update")

    # Non-blocking graph notices (dependent-warning, flow-out reminder) — stderr,
    # so the stdout RECORD_ID/moved contract is unchanged.
    _print_guard_notices(guard_notices)

    # The relocation signal (no silent move) precedes the
    # RECORD_ID so the existing stdout contract for the no-move case is unchanged
    # (RECORD_ID stays the trailing parseable line); the moved case adds the signal.
    if moved_line is not None:
        print(moved_line)
    print(new_id)
    return 0


def add_record_subparser(sub) -> None:
    """Register the ``record`` command parser and its create/update/delete/show actions."""
    # record subcommand: ``lore record create``.
    # ``--team`` etc. are the *routing* flags (scope → place_record); their
    # sidecar-write semantics are owned by the record-write layer.
    p_record = sub.add_parser(
        "record",
        help="Create, update, delete, or manage vault records",
    )
    p_record_sub = p_record.add_subparsers(dest="record_action", required=True)

    p_record_create = p_record_sub.add_parser(
        "create", help="Create a new vault record",
        epilog="Choosing a labels flag: a value naming another record — a task, a "
               "decision, an area — is a relation, not an attribute; use --related "
               "KIND=NAME instead of a label. A free attribute is a label; use "
               "--label KEY=VALUE. A labels key that shadows a record kind or a "
               "query field name (e.g. 'area', 'phase', 'status', 'kind') is refused "
               "at write time; the refusal names a runnable fix — --annotation "
               "KEY=VALUE for a free attribute whose natural name is taken, or a "
               "namespaced key (<ns>/<key>, e.g. craft/subsystems) to keep it "
               "queryable as a label.",
    )
    p_record_create.add_argument(
        "--kind", required=True,
        help="Record kind (one of: adr, area, blob, collaboration, decision, "
             "lesson, session, spec, task)",
    )
    p_record_create.add_argument(
        "--title", required=True,
        help="Human-readable title (used to derive the record name slug)",
    )
    # Routing flags: passed to place_record as scope. These influence vault
    # selection; their sidecar-write semantics are owned by the record-write layer.
    p_record_create.add_argument(
        "--repo", default=None,
        help="Routing scope: restrict to this repo's vault",
    )
    p_record_create.add_argument(
        "--product", default=None,
        help="Routing scope: restrict to this product's vault",
    )
    p_record_create.add_argument(
        "--suite", default=None,
        help="Routing scope: restrict to this suite's vault",
    )
    p_record_create.add_argument(
        "--team", default=None,
        help="Routing scope: restrict to this team's vault",
    )
    _add_record_field_flags(p_record_create)
    # Map flags (labels/annotations): dedicated branch.
    _add_map_field_flags(p_record_create)
    p_record_create.set_defaults(func=cmd_record)

    # ``lore record update RECORD_ID``.
    p_record_update = p_record_sub.add_parser(
        "update", help="Update an existing vault record (full-body / --diff / metadata-only)",
        epilog="Group-default scope routing applies to 'create' only; "
               "update (and delete) are unaffected and never seed scopes from a camp group.",
    )
    p_record_update.add_argument(
        "record_id",
        metavar="RECORD_ID",
        help="The vault-relative record ID to update (<kind>/<name>)",
    )
    p_record_update.add_argument(
        "--diff", action="store_true",
        help="Treat piped stdin as a unified diff applied to the existing body. "
             "On any non-applying hunk the record is left byte-for-byte "
             "unmodified and the rejected hunks print to stderr.",
    )
    p_record_update.add_argument(
        "--vault", dest="vault", default=None, metavar="NAME",
        help="Locate the record in exactly this configured vault by name, "
             "instead of scanning every vault in config order. Current-location "
             "targeting only — combine with --repo/--product/--suite/--team to "
             "also re-route the record's destination.",
    )
    # Scope flags: dual field-setter +
    # routing on update too. Each writes its namesake sidecar field AND re-resolves
    # the destination vault from the merged scope; when the destination differs from
    # the record's current vault the record is auto-moved (no manual --move-to).
    p_record_update.add_argument(
        "--repo", default=None,
        help="Set the repo scope; re-routes + auto-moves the record on a change.",
    )
    p_record_update.add_argument(
        "--product", default=None,
        help="Set the product scope; re-routes + auto-moves the record on a change.",
    )
    p_record_update.add_argument(
        "--suite", default=None,
        help="Set the suite scope; re-routes + auto-moves the record on a change.",
    )
    p_record_update.add_argument(
        "--team", default=None,
        help="Set the team scope; re-routes + auto-moves the record on a change.",
    )
    # ``--title`` is an optional setter on update (it is the required positional
    # on create); the rest of the per-field flags are shared.
    p_record_update.add_argument(
        "--title", default=None,
        help="Overwrite the record's title (optional on update).",
    )
    _add_record_field_flags(p_record_update)
    # Map flags (labels/annotations): dedicated branch.
    _add_map_field_flags(p_record_update)
    p_record_update.set_defaults(func=cmd_record)

    # ``lore record delete RECORD_ID``.
    p_record_delete = p_record_sub.add_parser(
        "delete", help="Delete a vault record (body + sidecar + index row)"
    )
    p_record_delete.add_argument(
        "record_id",
        metavar="RECORD_ID",
        help="The vault-relative record ID to delete (<kind>/<name>)",
    )
    # Routing flags (symmetric with create/update): when a config exists they
    # select which vault the record lives in (a record routed to a scoped vault
    # on create is reached for delete with the same flags). Suppressed from help
    # like update's.
    p_record_delete.add_argument("--repo", default=None, help=argparse.SUPPRESS)
    p_record_delete.add_argument("--product", default=None, help=argparse.SUPPRESS)
    p_record_delete.add_argument("--suite", default=None, help=argparse.SUPPRESS)
    p_record_delete.add_argument("--team", default=None, help=argparse.SUPPRESS)
    p_record_delete.set_defaults(func=cmd_record)

    p_record_show = p_record_sub.add_parser(
        "show",
        help="Read a record's body (and sidecar with --json)",
    )
    p_record_show.add_argument(
        "record_id",
        metavar="RECORD_ID",
        help="The record ID to read (<kind>/<name>)",
    )
    p_record_show.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit {record_id, kind, name, sidecar, body} as JSON",
    )
    p_record_show.add_argument(
        "--vault", dest="vault", default=None, metavar="NAME",
        help="Read the record from exactly this configured vault by name, "
             "instead of scanning every vault in config order.",
    )
    # Routing flags (symmetric with delete): select which vault a scoped record
    # lives in. Suppressed from help.
    p_record_show.add_argument("--repo", default=None, help=argparse.SUPPRESS)
    p_record_show.add_argument("--product", default=None, help=argparse.SUPPRESS)
    p_record_show.add_argument("--suite", default=None, help=argparse.SUPPRESS)
    p_record_show.add_argument("--team", default=None, help=argparse.SUPPRESS)
    p_record_show.set_defaults(func=cmd_record)
