"""``lore record`` — create / update / amend / supersede / delete / show vault records.

``update`` and ``amend`` share one read / resolve-body / apply / guard step
(:func:`_read_apply_and_guard`) so a body is resolved and guarded identically by
both. ``amend`` is the only write that may change a frozen record's authored
body (adr, spec, decision or task, per the guards' per-kind freeze): it hands
its own resolved new body to the guard's ``allowed_body`` seam and records the
change in the record's ``change-history``.

``supersede`` records a supersession in one verb: the successor's ``supersedes``
edge, then the predecessor's flip to ``superseded``, both under both vaults'
write locks with both records read inside them. Both writes go through the same
guard and write pipeline ``update`` uses (``evaluate_graph_guards`` →
``validate_and_write``); the pair itself is judged by
``guards.evaluate_supersede_pairing``. The verb is the only caller that passes
the guards' ``allow_superseded_status`` seam: ``create`` and ``update`` refuse
any write into ``superseded``.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

from .common import (
    StdinSilentError,
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
    and the ``--related``/``--unset-related <kind>=<name>`` map flag pair.
    ``--title`` is NOT added here —
    create declares it as a required argument and update as an optional setter.
    Scope flags (``--team`` etc.) are NOT added here either.
    """
    parser.add_argument(
        "--status", default=None,
        help="Set the record's status (must be in the kind's vocabulary).",
    )
    parser.add_argument(
        "--keyword", dest="keyword", action="append", default=[], metavar="VALUE",
        help="Append a keyword.",
    )
    parser.add_argument(
        "--unset-keyword", dest="unset_keyword", action="append", default=[], metavar="VALUE",
    )
    parser.add_argument(
        "--related-file", dest="related_file", action="append", default=[], metavar="VALUE",
        help="Append a related file/folder.",
    )
    parser.add_argument(
        "--unset-related-file", dest="unset_related_file", action="append", default=[],
        metavar="VALUE",
    )
    parser.add_argument(
        "--related-url", dest="related_url", action="append", default=[], metavar="VALUE",
        help="Append a related URL.",
    )
    parser.add_argument(
        "--unset-related-url", dest="unset_related_url", action="append", default=[],
        metavar="VALUE",
    )
    parser.add_argument(
        "--related-phase", dest="related_phase", action="append", default=[], metavar="VALUE",
        help="Append a related phase.",
    )
    parser.add_argument(
        "--unset-related-phase", dest="unset_related_phase", action="append", default=[],
        metavar="VALUE",
    )
    parser.add_argument(
        "--related", dest="related_pairs", action="append", default=[], metavar="KIND=NAME",
        help="Append NAME to related[KIND]; split on the first '=', both parts required "
             "and KIND must be a valid record kind.",
    )
    parser.add_argument(
        "--unset-related", dest="unset_related_pairs", action="append", default=[],
        metavar="KIND=NAME",
    )
    # depends-on: a bare TASK name on a task record, or a qualified
    # KIND/NAME[@STAGE] target (kind spec or adr) on a spec/adr record.
    # --parent stays task-only, rejected on every other kind by validate(), and
    # takes the same bare-name grammar --depends-on does.
    parser.add_argument(
        "--depends-on", dest="depends_on", action="append", default=[],
        metavar="TASK|KIND/NAME[@STAGE]",
        help="Append a dependency: a bare TASK on a task record, or a qualified "
             "KIND/NAME[@STAGE] on a spec/adr record.",
    )
    parser.add_argument(
        "--unset-depends-on", dest="unset_depends_on", action="append", default=[],
        metavar="TASK|KIND/NAME[@STAGE]",
    )
    # supersedes: a `<kind>/<name>` reference, valid on any kind (ungated,
    # unlike --depends-on/--parent). No stage tail, no cycle guard — only a
    # malformed reference or a self-edge is rejected at write time.
    parser.add_argument(
        "--supersedes", dest="supersedes", action="append", default=[],
        metavar="KIND/NAME",
        help="Raw edge edit: add KIND/NAME.",
    )
    parser.add_argument(
        "--unset-supersedes", dest="unset_supersedes", action="append", default=[],
        metavar="KIND/NAME",
    )
    parser.add_argument(
        "--parent", dest="parent", default=None, metavar="TASK",
        help="Set this task's parent: a bare TASK name (task-only).",
    )
    parser.add_argument(
        "--unset-parent", dest="unset_parent", action="store_true", default=False,
        help="Clear this task's parent.",
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
        help="Set a label (upsert); split on the first '=' so 'namespace/name=value' "
             "works unescaped.",
    )
    parser.add_argument(
        "--annotation", dest="annotation_pairs", action="append", default=[],
        metavar="KEY=VALUE",
        help="Set an annotation (upsert); split on the first '='.",
    )
    parser.add_argument(
        "--unset-label", dest="unset_labels", action="append", default=[],
        metavar="KEY",
    )
    parser.add_argument(
        "--unset-annotation", dest="unset_annotations", action="append", default=[],
        metavar="KEY",
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
    ``update``'s ``RecordNotFoundError``/``InvalidRecordIdError`` clause is NOT
    routed here — it is update-specific and caught before this handler.
    """
    from .. import locking
    from ..record import store as record_store_mod

    if isinstance(exc, record_store_mod.RecordValidationError):
        return _fail(exc.errors, prefix="error: ")
    if isinstance(exc, record_store_mod.ProvenanceError):
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if isinstance(exc, locking.VaultRecoveryError):
        print(exc, file=sys.stderr)
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


#: The record-URL line format, shared by ``record create`` and ``record
#: update``. A single constant rendered by one helper (below) — never two
#: independently-phrased ``print`` statements — so the two verbs cannot drift
#: apart on label text or position.
_RECORD_URL_LINE_FORMAT = "Reader: {url}"


def _print_record_url(vault_root, record_id: str) -> None:
    """Print *record_id*'s reader URL to stderr, in the shared line format.

    Joins the existing stderr trailer (routing confirmation, then
    :func:`_print_guard_notices`) rather than opening a second channel —
    called last, by both ``record create`` and ``record update``, once the
    record's final vault is known. On an update that relocates the record,
    *vault_name* is the DESTINATION vault, never the one it moved out of.

    Takes the record's vault ROOT and resolves the vault name itself, inside
    its own guard. Resolution runs in the same post-commit position as building
    the URL and carries the same obligation, so evaluating it at the call site
    would leave the trailer non-fatal only for the half of its work that
    happened to sit inside the guard.

    :func:`_vault_name_for_root` returns ``None`` when it cannot determine a
    trustworthy name (``config.json`` is loaded but no entry's path matches the
    record's root) — the URL line is omitted entirely in that case. A missing
    line is honest; a link naming the wrong vault is not.

    Construction (:func:`lore.record_url.build_record_url`) reads
    configuration only and performs no network I/O, so this prints the same
    line whether or not a reader daemon is actually running.

    A rejected configured base (see ``record_url.resolve_base``) is surfaced
    by that module as a :class:`RuntimeWarning` rather than a plain stderr
    line — no CLI-level stderr-notice convention existed yet at the module's
    layer. This function is that convention's layer, so the warning is
    caught here and re-printed as a plain ``lore: `` line matching every
    other rejection notice this CLI prints (e.g. :func:`_resolve_named_vault`),
    instead of leaking Python's multi-line ``file:lineno`` warning format
    into the trailer. Any other warning category raised during construction is
    re-emitted via :func:`warnings.warn` rather than discarded, so it still
    reaches its normal destination.
    """
    from .. import record_url as record_url_mod

    try:
        vault_name = _vault_name_for_root(vault_root)
        if vault_name is None:
            return
        _emit_record_url(record_url_mod, vault_name, record_id)
    except Exception:
        # The reader link is a convenience printed after the record is written
        # and committed. Nothing about constructing it may change the verb's
        # outcome, so an unforeseen failure costs the line and nothing else.
        pass


def _emit_record_url(record_url_mod, vault_name: str, record_id: str) -> None:
    """Build and print the reader-URL line. See :func:`_print_record_url`."""
    kind, _, slug = record_id.partition("/")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RuntimeWarning)
        url = record_url_mod.build_record_url(vault_name, kind, slug)
    for w in caught:
        if issubclass(w.category, RuntimeWarning):
            print(str(w.message), file=sys.stderr)
        else:
            warnings.warn(w.message, w.category)
    print(_RECORD_URL_LINE_FORMAT.format(url=url), file=sys.stderr)


def _vault_name_for_root(vault_root) -> str | None:
    """Return the configured vault name whose path resolves to *vault_root*.

    Falls back to ``"default"`` — the name ``lore init`` seeds for the sole
    default-scope vault — when no ``config.json`` is loaded, matching
    :func:`vault_config.resolve_active_vault`'s own floor-path fallback.

    Returns ``None`` when ``config.json`` IS loaded but no entry's path
    resolves to *vault_root* — the caller must not print a URL naming a vault
    the record is not actually in.

    ``config.json`` enforces unique vault *names*, not unique vault *paths*,
    so two entries may alias one directory. When more than one entry matches,
    this returns the first match in config order, deterministically — not
    because one alias is more "correct" than another, but because a
    deterministic pick beats an arbitrary one.
    """
    loaded = _load_vault_config()
    if loaded is None:
        return "default"
    _, vaults = loaded
    target = Path(vault_root).resolve()
    for v in vaults:
        if Path(v.path).resolve() == target:
            return v.name
    return None


def cmd_record(args) -> int:
    """Dispatch ``lore record <action>`` — create / update / amend / delete / show / history / criteria / rename.

    Thin shell over ``record_store``. Routes to the appropriate handler
    by ``record_action``; all handlers are argv→library adapters with no
    validation or I/O logic of their own.
    """
    action = getattr(args, "record_action", None)
    if action == "create":
        return _cmd_record_create(args)
    if action == "update":
        return _cmd_record_update(args)
    if action == "amend":
        return _cmd_record_amend(args)
    if action == "supersede":
        return _cmd_record_supersede(args)
    if action == "delete":
        return _cmd_record_delete(args)
    if action == "show":
        return _cmd_record_show(args)
    if action == "history":
        return _cmd_record_history(args)
    if action == "criteria":
        return _cmd_record_criteria(args)
    if action == "rename":
        return _cmd_record_rename(args)
    print(
        f"lore record: unknown action {action!r}. "
        f"Use 'lore record create', 'lore record update', "
        f"'lore record amend', 'lore record supersede', 'lore record rename', 'lore record delete', "
        f"'lore record history', 'lore record criteria', or 'lore record show'.",
        file=sys.stderr,
    )
    return 1


def _render_record(
    record_id: str,
    vault_root: str,
    as_json: bool,
    *,
    error_prefix: str = "error: ",
    history_note: bool = False,
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

    **The shared-layer trust fence.** A record read out of a ``shared: true``
    vault is content this operator did not author, on its way into an agent's
    context, and this is the command a bulk-processing caller must use to get a
    full body — so the marker ``lore search`` and the pipeline board apply to
    shared content applies here too, by the same two-mode contract. Human mode
    splices the body into the ``<external-memory layer="shared" source="…">``
    data channel, which entity-escapes what it wraps. ``--json`` carries a
    ``layer`` marker (``"shared"`` / ``"personal"``) for the consumer and
    entity-escapes the vault-authored halves of the payload itself — the body,
    and every string anywhere inside the sidecar, since a shared vault authors
    its keys as readily as its values. ``record_id``/``kind``/``name`` are not
    escaped: they are the caller's own id, confined to a safe charset before
    anything was opened.

    The write path's :func:`record.store.neutralize_fences` does not make this
    redundant. It guards what *this* lore stores; shared content arrives by
    ``lore sync``, authored elsewhere, having never passed through it.

    Trust comes from :func:`_vault_trust`, so a record stamped untrusted in the
    index is a record rendered untrusted here. Path-aliased config entries that
    disagree about it refuse to render rather than pick one — a wrong guess
    would hand shared content over as the operator's own.

    **The history note.** ``--json`` already carries ``change-history`` inside
    ``sidecar`` (entity-escaped in a shared vault with the rest of it). Plain
    mode keeps stdout exactly as it was; with ``history_note`` (``record show``
    sets it, ``session show`` does not) a record that has been amended also gets
    one stderr line, ``amended once; see `lore record history <id>``` (``amended N times; ...`` from two on), so a
    reader knows the body was corrected while a piped consumer sees no change.

    **The revision.** The SHA-256 hex of the stored body bytes (computed before
    any shared-vault escaping), the token ``record amend --base`` compares.
    ``--json`` carries it as top-level ``revision``; plain ``record show`` prints
    ``revision: <hex>`` on stderr (before the amended note), so stdout stays the
    body byte for byte.
    """
    from ..record import journal
    from ..record import store as record_store_mod
    from ..search import xml_escape

    journal.recover_if_pending(vault_root)
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

    try:
        vault_name, shared_flag = _vault_trust(vault_root)
    except _AliasedTrustConflict as exc:
        print(f"lore: {exc}", file=sys.stderr)
        return 1
    shared = shared_flag != 0

    body, revision = record_store_mod.read_stored_body(loc.body_path)

    if as_json:
        sidecar: dict[str, Any] = {}
        if loc.sidecar_path.exists():
            try:
                sidecar = json.loads(loc.sidecar_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                sidecar = {}
        if shared:
            body = xml_escape.xml_body_escape(body)
            sidecar = xml_escape.map_strings(sidecar, xml_escape.xml_body_escape)
        payload = {
            "record_id": record_id,
            "kind": loc.kind,
            "name": loc.name,
            "layer": (
                xml_escape.SHARED_LAYER if shared
                else xml_escape.PERSONAL_LAYER
            ),
            "sidecar": sidecar,
            "body": body,
            "revision": revision,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif shared:
        sys.stdout.write(
            "\n".join(xml_escape.wrap_shared(vault_name, body.split("\n"))) + "\n"
        )
    else:
        sys.stdout.write(body)
    if history_note and not as_json:
        print(f"revision: {revision}", file=sys.stderr)
        count = len(record_store_mod.read_change_history(loc.sidecar_path))
        if count:
            times = "once" if count == 1 else f"{count} times"
            print(
                f"amended {times}; see `lore record history {record_id}`",
                file=sys.stderr,
            )
    return 0


def _neutral(value: object) -> str:
    """*value* as text with any ``external-memory`` fence token made inert."""
    from ..record import store as record_store_mod

    return record_store_mod.neutralize_fences(str(value))


_STAMP_MAX = 120


def _printable(text: str, *, keep: str = "") -> str:
    """*text* with every non-printable character (ESC, BEL, ...) escaped to a backslash sequence.

    Characters in *keep* pass through, so a multi-line field can keep its newlines.
    """
    return "".join(
        ch if ch.isprintable() or ch in keep else ch.encode("unicode_escape").decode("ascii")
        for ch in text
    )


def _inert_stamp(value: object) -> str:
    """*value* as one capped printable line with any ``external-memory`` fence token made inert."""
    line = _printable(" ".join(str(value).split()))
    if len(line) > _STAMP_MAX:
        line = line[:_STAMP_MAX] + "…"
    return _neutral(line)


def _format_history(record_id: str, history: list) -> list[str]:
    """Lines of the newest-first listing: each entry's header, then its diff."""
    if not history:
        return [f"{record_id} has no change history."]
    lines: list[str] = []
    total = len(history)
    for number, entry in reversed(list(enumerate(history, start=1))):
        if not isinstance(entry, dict):
            continue
        if lines:
            lines.append("")
        lines.append(f"entry: {number} of {total}")
        lines.append(f"at: {_neutral(_printable(str(entry.get('at', ''))))}")
        lines.append(f"by: {_neutral(_printable(str(entry.get('by', ''))))}")
        lines.append(f"initiated-by: {_neutral(_printable(str(entry.get('initiated-by', ''))))}")
        lines.append(f"reason: {_neutral(_printable(str(entry.get('reason', '')), keep=chr(10)))}")
        criteria = entry.get("criteria")
        if isinstance(criteria, dict):
            from ..record.criteria import _canonical

            for key in ("removed", "reopened", "closed"):
                ids = criteria.get(key)
                if isinstance(ids, list) and ids:
                    shown = (i if _canonical(i) else "<non-canonical id>" for i in ids)
                    lines.append(f"{key} criteria: " + ", ".join(shown))
        lines.append("diff:")
        diff = _printable(str(entry.get("diff", "")), keep="\n\t")
        lines.extend(_neutral(diff).rstrip("\n").split("\n"))
    return _neutral("\n".join(lines)).split("\n")


def _parse_entry_number(raw: str, flag: str) -> "int | None":
    """*raw* as a history entry number, or ``None`` after printing the refusal."""
    try:
        return int(raw)
    except ValueError:
        print(f"lore: {flag} must be an integer, got {raw!r}", file=sys.stderr)
        return None


def _reconstruct_refusal(exc, location, flag: str) -> str:
    """The ``lore:`` message for a failed reconstruction, with the git fallback."""
    if exc.kind == "no-history":
        return f"lore: {location.record_id} has no change history: nothing to reconstruct"
    if exc.kind == "out-of-range":
        return f"lore: {flag} {exc}"
    return (
        f"lore: {location.record_id}: {exc}. Nothing was written. To see what "
        f"changed, run: git -C {location.vault_root} log -p -- "
        f"{location.kind}/{location.name}.md"
    )


def _reconstruct_from_stored(location, n: int, flag: str) -> "str | None":
    """The stored body rebuilt to before entry *n*, or ``None`` after printing why not.

    Works from the bytes on disk and the sidecar's own ``change-history`` —
    never from rendered or escaped text — so the result is exactly what an amend
    would write back.
    """
    from ..record import store as record_store_mod

    body = (
        location.body_path.read_text(encoding="utf-8")
        if location.body_path.exists()
        else ""
    )
    history = record_store_mod.read_stored_change_history(location.sidecar_path)
    try:
        return record_store_mod.reconstruct_body_before(body, history, n)
    except record_store_mod.ReconstructError as exc:
        print(_reconstruct_refusal(exc, location, flag), file=sys.stderr)
        return None


def _cmd_record_history(args) -> int:
    """``lore record history <kind>/<name> [--vault NAME]`` — a record's change history.

    Prints the ``change-history`` ``record amend`` appends, newest first: each
    entry's ``at``, ``by``, ``initiated-by`` and ``reason``, then its diff. A
    record with no history prints one line saying so and exits 0. A malformed id
    or missing record → non-zero + ``lore: <msg>`` on stderr, no traceback.
    ``--vault NAME`` is resolved by :func:`_resolve_named_vault` exactly as
    ``record show --vault`` does; omitting it scans like ``record show``.

    **Shared-layer fence.** In a shared vault the reason and the diff are text
    this operator did not author, so the whole listing travels through
    ``xml_escape.wrap_shared`` — the channel ``record show`` uses — which
    entity-escapes it and cannot be closed early by a ``</external-memory>``
    inside an entry. Trust comes from :func:`_vault_trust`.
    """
    from ..record import journal
    from ..record import store as record_store_mod
    from ..search import xml_escape

    record_id = _require_record_id(args)
    if record_id is None:
        return 1

    vault_name = getattr(args, "vault", None)
    if vault_name:
        named_vault = _resolve_named_vault(vault_name)
        if named_vault is None:
            return 1
        vault_root = str(named_vault.path)
    else:
        try:
            vault_root = _resolve_record_op_vault(record_id, args)
        except record_store_mod.InvalidRecordIdError as exc:
            print(f"lore: {exc}", file=sys.stderr)
            return 1

    journal.recover_if_pending(vault_root)
    try:
        loc = record_store_mod.locate_record(record_id, vault_root=vault_root)
    except (
        record_store_mod.RecordNotFoundError,
        record_store_mod.InvalidRecordIdError,
    ) as exc:
        print(f"lore: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"lore: record history failed: {exc}", file=sys.stderr)
        return 1

    try:
        trust_name, shared_flag = _vault_trust(vault_root)
    except _AliasedTrustConflict as exc:
        print(f"lore: {exc}", file=sys.stderr)
        return 1

    body_before = getattr(args, "body_before", None)
    if body_before is not None:
        n = _parse_entry_number(body_before, "--body-before")
        if n is None:
            return 1
        rebuilt = _reconstruct_from_stored(loc, n, "--body-before")
        if rebuilt is None:
            return 1
        safe = record_store_mod.neutralize_fences(rebuilt)
        if shared_flag != 0:
            safe = "\n".join(xml_escape.wrap_shared(trust_name, safe.split("\n"))) + "\n"
        sys.stdout.write(safe)
        return 0

    lines = _format_history(
        record_id, record_store_mod.read_change_history(loc.sidecar_path)
    )
    if shared_flag != 0:
        lines = xml_escape.wrap_shared(trust_name, lines)
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _cmd_record_criteria(args) -> int:
    """``lore record criteria <spec-id> [--vault NAME]`` — a spec's criteria state.

    Prints one JSON object, ``{"record": "spec/<name>", "declared": [...],
    "removed": [...]}``, computed by :func:`lore.record.criteria.criteria_state`
    from the stored body and the sidecar's ``change-history``: ``declared`` is the
    identifiers the body declares in document order, ``removed`` the identifiers a
    history entry recorded as removed in numeric order. A spec whose criteria
    section declares no identifiers prints two empty lists. ``reopened`` and
    ``closed`` (from :func:`~lore.record.criteria.criteria_state`) are printed only
    when non-empty, so a spec with no marks prints exactly the three keys above.

    Read-only: it takes no vault lock and writes no journal entry, so it follows
    ``record history``'s read path, including ``--vault`` resolution. A record
    that is not a spec, a missing record or malformed id, a missing or duplicated
    criteria heading, or a stored history that is not a list or whose
    ``criteria`` object is malformed → non-zero + ``lore: <msg>`` on stderr, no
    traceback, nothing on stdout. A stored sidecar is validated only on the write
    path, so this command fails closed on a hand-corrupted one.
    """
    from ..record import criteria as criteria_mod
    from ..record import journal
    from ..record import store as record_store_mod

    record_id = args.record_id

    vault_name = getattr(args, "vault", None)
    if vault_name:
        named_vault = _resolve_named_vault(vault_name)
        if named_vault is None:
            return 1
        vault_root = str(named_vault.path)
    else:
        try:
            vault_root = _resolve_record_op_vault(record_id, args)
        except record_store_mod.InvalidRecordIdError as exc:
            print(f"lore: {exc}", file=sys.stderr)
            return 1

    journal.recover_if_pending(vault_root)
    try:
        loc = record_store_mod.locate_record(record_id, vault_root=vault_root)
    except (
        record_store_mod.RecordNotFoundError,
        record_store_mod.InvalidRecordIdError,
    ) as exc:
        print(f"lore: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"lore: record criteria failed: {exc}", file=sys.stderr)
        return 1

    if loc.kind != "spec":
        print(
            f"lore: {loc.record_id} is a {loc.kind}, not a spec: only a spec "
            "declares acceptance criteria",
            file=sys.stderr,
        )
        return 1

    body = (
        loc.body_path.read_text(encoding="utf-8") if loc.body_path.exists() else ""
    )
    try:
        history = record_store_mod.read_stored_change_history_strict(loc.sidecar_path)
        state = criteria_mod.criteria_state(body, history)
    except record_store_mod.SidecarUnreadableError as exc:
        print(f"lore: {loc.record_id}: {exc}", file=sys.stderr)
        return 1
    except criteria_mod.CriteriaError as exc:
        print(f"lore: {loc.record_id}: {exc}", file=sys.stderr)
        return 1

    shown = {k: v for k, v in state.items() if v or k in ("declared", "removed")}
    sys.stdout.write(json.dumps({"record": loc.record_id, **shown}) + "\n")
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
    match may not be the vault the caller means (e.g. a queue
    classification and question extraction, which must read a specific vault's
    body, not whichever vault happens to sort first in config). An unknown
    ``--vault`` name, or a named vault that does not hold the record, errors
    ``lore: <msg>`` + nonzero — the same explicit-targeting convention
    ``update`` uses. Omitting ``--vault`` preserves
    :func:`_resolve_record_op_vault`'s scan exactly as before.
    """
    from ..record import store as record_store_mod

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
            record_id,
            str(named_vault.path),
            as_json,
            error_prefix="lore: ",
            history_note=True,
        )

    try:
        vault_root = _resolve_record_op_vault(record_id, args)
    except record_store_mod.InvalidRecordIdError as exc:
        print(f"lore: {exc}", file=sys.stderr)
        return 1
    return _render_record(record_id, vault_root, as_json, history_note=True)


def _cmd_record_delete(args) -> int:
    """``lore record delete RECORD_ID [--vault NAME]`` — thin shell over
    ``record_store``.

    Removes the body (``.md``), sidecar (``.json``), and index row for RECORD_ID
    in one operation. Uses :func:`record_store.delete_record`, which is the
    transactional delete primitive — this function does NOT
    re-implement removal logic.

    RECORD_ID must be in ``<kind>/<name>`` format and refer to an existing record.
    An invalid format or nonexistent record → non-zero + clear stderr.

    ``--vault NAME`` mirrors ``record show --vault`` exactly: resolved via
    :func:`_resolve_named_vault`, then a direct
    ``record_store.locate_record(vault_root=...)`` in exactly that vault — no
    :func:`_resolve_record_op_vault` scan fallback. This is the delete-side fix
    for the same collision ``show``/``update --vault`` address: a same-named
    record across more than one configured vault, where the cwd-blind scan's
    first match may not be the vault the caller means. An unknown ``--vault``
    name, or a named vault that does not hold the record, errors
    ``lore: <msg>`` + nonzero — never falling back to the scan. Omitting
    ``--vault`` preserves :func:`_resolve_record_op_vault`'s scan exactly as
    before.

    **The inbound-reference guard.** A delete leaves every reference to the
    record dangling, and nothing repairs them afterwards, so a delete is refused
    while any record still names this one — repoint first, delete second, is
    enforced here rather than left to a caller following it. What counts as a
    reference is :func:`record.rename.find_inbound_references`' answer, which is
    the rename sweep's own: a body wikilink or a ``related`` edge. Bare prose
    naming the record is not one, which is what lets a consolidation annotation
    survive the fold it records.

    Two references do not block. One in a ``shared: true`` vault is reported and
    stepped over: content the operator does not control does not get a veto over
    their own record. The record's link to itself is not a reason it cannot go.

    A referrer that could not be read *does* block, in its own words. A scan that
    failed to look must not be reported as a scan that found nothing.

    ``--force`` deletes anyway and names every reference it left dangling, which
    is the escape hatch for a deliberate delete and for a vault whose referrers
    cannot be read. It is not silence.
    """
    from .. import locking as locking_mod
    from ..record import guards as guards_mod
    from ..record import rename as rename_mod
    from ..record import store as record_store_mod
    from . import resolve_state as resolve_state_mod

    record_id = _require_record_id(args)
    if record_id is None:
        return 1

    vault_name = getattr(args, "vault", None)
    if vault_name:
        named_vault = _resolve_named_vault(vault_name)
        if named_vault is None:
            return 1
        try:
            record_store_mod.locate_record(record_id, vault_root=str(named_vault.path))
        except (
            record_store_mod.RecordNotFoundError,
            record_store_mod.InvalidRecordIdError,
        ) as exc:
            print(f"lore: {exc}", file=sys.stderr)
            return 1
        vault_root = str(named_vault.path)
    else:
        # Resolve the target vault: see _resolve_record_op_vault for the
        # two-path contract (config-driven scan when no scope flag is given,
        # explicit-flag routing otherwise). A record whose vault was removed
        # from config resolves to the default floor and surfaces a clean
        # RecordNotFoundError below rather than acting on an orphaned target.
        vault_root = _resolve_record_op_vault(record_id, args)

    # Same fence as ``record create`` / ``record update``: a vault stopped
    # mid-rebase is being resolved, and a delete landing in it would be replayed
    # over — or silently swept into — the resolution's own commit. Checked before
    # the lock so a refused delete never even creates a lock sidecar.
    if resolve_state_mod.vault_is_resolving(Path(vault_root)):
        print(resolve_state_mod.refusal_notice(vault_root, "record delete"), file=sys.stderr)
        return 1

    # Dependent-warning: deleting a task or design record that others depend-on
    # is allowed (delete is never blocked) but warns, listing the dependents.
    # Computed before the delete off the on-disk task/design graph; a no-op
    # for every kind that carries neither graph.
    kind, _, name = record_id.partition("/")

    # The inbound-reference guard: computed before the lock, so a refused delete
    # never even creates a lock sidecar, and off the vaults as they are on disk
    # rather than the index — body wikilinks are not indexed at all.
    inbound = rename_mod.find_inbound_references(
        rename_mod.sweep_vaults(), kind, name, source_root=vault_root
    )
    blocking = [ref for ref in inbound if not ref.shared]
    if blocking and not getattr(args, "force", False):
        print(
            f"lore: refusing to delete {record_id} — "
            f"{len(blocking)} record(s) still reference it:",
            file=sys.stderr,
        )
        for line in _inbound_lines(blocking):
            print(line, file=sys.stderr)
        print(
            "repoint these references first, or pass --force to delete anyway "
            "and leave them dangling",
            file=sys.stderr,
        )
        return 1

    _, guard_notices = guards_mod.evaluate_graph_guards(
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
    if inbound:
        print(
            f"notice: {len(inbound)} reference(s) to {record_id} now dangle:",
            file=sys.stderr,
        )
        for line in _inbound_lines(inbound):
            print(line, file=sys.stderr)

    return 0


def _inbound_lines(refs) -> list[str]:
    """One indented line per inbound reference, naming its vault and any reason.

    Shared by the refusal and the post-delete notice so a caller reads the same
    list whichever side of ``--force`` they are on.
    """
    lines = []
    for ref in refs:
        suffix = f" — {ref.error}" if ref.error else ""
        trust = " [shared vault]" if ref.shared else ""
        lines.append(f"  {ref.record_id} (vault {ref.vault!r}){trust}{suffix}")
    return lines


def _cmd_record_rename(args) -> int:
    """``lore record rename RECORD_ID --title "<new title>"`` — thin shell.

    Renames the record's stem + sidecar title within its own vault and rewrites
    every inbound reference to it across the configured vaults. All of the logic
    lives in ``record.rename``; this handler is an argv→library adapter that
    owns only the output contract:

      - **stdout**: the new RECORD_ID, and nothing else — the sole parseable
        line, matching ``record create``.
      - **stderr**: the ``renamed:`` line (naming the source vault) plus one
        line per referencing record the sweep touched, named individually with
        its vault, so a partial failure names exactly what did and did not
        land. References in ``shared: true`` vaults are listed as skipped
        unless ``--include-shared`` is passed; a record that references the old
        stem and could not be written is listed as ``failed:`` with its reason;
        a record the sweep could not READ is listed as ``unchecked:``.

    The sweep is fault-isolated, so one bad record no longer aborts a rename
    whose primary move has already landed. The full accumulated list is printed
    whether or not every record succeeded — a partial failure must be
    diagnosable from the output alone.

    **Exit status** tracks dangling references only. A ``failed:`` record is a
    known-dangling one, so it exits non-zero. An ``unchecked:`` record is not:
    its body could not be read, so nothing is known about its references, and an
    unrelated corrupt file elsewhere in a vault must not report a clean rename
    as broken. Those are surfaced as a ``warning:`` line naming the count.

    ``--vault NAME`` names the vault holding the record to rename, mirroring
    ``record show``/``record delete --vault``: with the same stem in more than
    one configured vault, config order alone may pick the wrong one. It narrows
    only the lookup of the record being renamed; the inbound-reference sweep
    still covers every configured vault.

    ``--dry-run`` writes nothing and prints the *predicted* new RECORD_ID —
    computed through the same ``_unique_stem`` path the real run uses, so the
    prediction (including a ``-2`` collision suffix) is what a subsequent real
    run produces.

    Re-running an identical rename is safe: the sweep re-runs and matches
    nothing, so a rename interrupted between the move and the rewrite is
    repaired by simply issuing it again.
    """
    from ..record import rename as rename_mod
    from ..record import store as record_store_mod

    record_id = _require_record_id(args)
    if record_id is None:
        return 1
    new_title = getattr(args, "title", None)
    if not new_title:
        print("error: --title is required for 'record rename'", file=sys.stderr)
        return 1

    dry_run = bool(getattr(args, "dry_run", False))
    include_shared = bool(getattr(args, "include_shared", False))

    vault_name = getattr(args, "vault", None)
    if vault_name:
        named_vault = _resolve_named_vault(vault_name)
        if named_vault is None:
            return 1
        vault_name = named_vault.name

    try:
        with record_store_mod.index_transaction() as conn:
            report = rename_mod.rename_record(
                record_id, new_title, conn,
                dry_run=dry_run, include_shared=include_shared,
                vault_name=vault_name,
            )
            if not dry_run:
                conn.commit()
    except rename_mod.RenameError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (
        record_store_mod.RecordNotFoundError,
        record_store_mod.InvalidRecordIdError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: record rename failed: {exc}", file=sys.stderr)
        return 1

    verb = "would rename" if dry_run else "renamed"
    print(
        f"{verb}: {report.old_id} -> {report.new_id} (vault: {report.vault})",
        file=sys.stderr,
    )
    failures = 0
    unchecked = 0
    for rw in report.rewrites:
        if rw.unchecked:
            unchecked += 1
            print(
                f"unchecked: {rw.vault}: {rw.record_id}: {rw.error}", file=sys.stderr
            )
        elif rw.error is not None:
            failures += 1
            print(f"failed: {rw.vault}: {rw.record_id}: {rw.error}", file=sys.stderr)
        elif rw.skipped:
            print(
                f"skipped (shared vault; pass --include-shared to rewrite): "
                f"{rw.vault}: {rw.record_id}",
                file=sys.stderr,
            )
        else:
            action = "would rewrite" if dry_run else "rewrote"
            print(f"{action}: {rw.vault}: {rw.record_id}", file=sys.stderr)

    print(report.new_id)
    if unchecked:
        print(
            f"warning: {unchecked} record(s) could not be read and were not "
            f"checked for references to {report.old_id}",
            file=sys.stderr,
        )
    if failures:
        print(
            f"error: {failures} record(s) still reference {report.old_id}; "
            f"rerun the same rename once the cause is fixed",
            file=sys.stderr,
        )
        return 1
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

    ``--kind adr`` is an ordinary kind here: the title lands verbatim in the
    sidecar and ``place_record`` derives its stem the same way it does for
    every other non-session kind (``_kebab(title)``, ``-2`` suffix on
    collision).

    ``--vault NAME`` names the destination vault directly via
    :func:`_resolve_named_vault`, bypassing :func:`vault_resolve.explain_resolution`'s
    scope-based routing computation entirely — explicit vault targeting wins
    over routing, the same precedence ``record show``/``record update --vault``
    already give explicit targeting over their scans. ``--repo``/``--product``/
    ``--suite``/``--team``, if also passed, keep stamping their own sidecar
    fields (:func:`record.fields.apply_record_fields` fields are unaffected);
    that effect is orthogonal to which vault the record lands in — the same
    "field-setter vs. routing" split ``record update`` already draws between
    scope flags and ``--vault``. The routing confirmation line still prints,
    naming the ``--vault``-selected vault. An unknown ``--vault`` name errors
    ``lore: <msg>`` + nonzero, nothing written. Omitting ``--vault`` preserves
    the existing scope-routing/vanilla-fallback behavior unchanged.
    """
    from .. import locking as locking_mod
    from ..record import fields as fields_mod
    from ..record import guards as guards_mod
    from ..record import store as record_store_mod
    from ..vault import config as vault_config_mod
    from ..vault import resolve as vault_resolve_mod
    from . import resolve_state as resolve_state_mod
    from . import sync as sync_mod

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
    try:
        body = _read_stdin_body()
    except StdinSilentError as exc:
        print(f"lore: {exc}", file=sys.stderr)
        return 1

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
            if kind == "session":
                # A session record lives only in the default vault, so a typed
                # scope flag on one is a contradiction rather than a preference:
                # honoring it would route to default anyway while stamping a
                # namesake sidecar field naming a vault the record is not in.
                # Refuse instead of silently rewriting what the operator asked for.
                print(
                    f"error: a session record is written only to the default vault; "
                    f"drop --{flag}",
                    file=sys.stderr,
                )
                return 1
            participating_scopes[flag] = val
            sidecar[flag] = val  # one input → both routing and field; field can never contradict vault

    shared_flag = 0
    routing_line: str | None = None
    seeded_scopes: set[str] = set()
    vault_name = getattr(args, "vault", None)
    if vault_name:
        # Explicit vault targeting wins over scope-based routing entirely —
        # scope flags above already stamped their sidecar fields; only the
        # destination-vault computation below is bypassed.
        named_vault = _resolve_named_vault(vault_name)
        if named_vault is None:
            return 1
        if kind == "session" and named_vault.scope != "default":
            # ``--vault`` bypasses routing entirely, so the kind pin in
            # ``vault_resolve`` never sees this path — it needs its own refusal.
            print(
                f"error: a session record is written only to the default vault; "
                f"--vault {named_vault.name} names a {named_vault.scope}-scope vault",
                file=sys.stderr,
            )
            return 1
        vault_root = Path(named_vault.path)
        shared_flag = vault_config_mod.shared_flag(named_vault)
        routing_line = f"Routed to vault: {named_vault.name} (--vault)"
    elif loaded is not None:
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
        # Seeding is skipped for a session record: it is pinned to the default
        # vault, so an inherited camp-group scope could only stamp a sidecar field
        # naming a vault the record does not live in.
        if cwd is not None and kind != "session":
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

    # A vault stopped mid-rebase is being resolved: writing into it would land
    # records in a tree `lore resolve` is about to rewrite. Checked before the
    # lock so a refused create never even creates a lock sidecar.
    if resolve_state_mod.vault_is_resolving(vault_root):
        print(resolve_state_mod.refusal_notice(vault_root, "record create"), file=sys.stderr)
        return 1

    # Converge on the other devices' records before writing — throttled to one
    # fetch attempt per vault per freshness window, reported on stderr only (this
    # command's stdout is exactly one RECORD_ID line), and unable to fail the
    # create. Runs BEFORE the lock and the index transaction below: it fetches
    # over the network and may reindex, neither of which belongs inside the
    # create's critical section.
    sync_mod.implicit_pull(vault_root)
    # Beside the freshness note: a stale automatic publish from a PRIOR write
    # is surfaced here too, before this write's own trigger below runs.
    from . import publish as publish_mod

    publish_mod.warn_stale_publish(vault_root)

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
            # Graph guards run against the resolved destination vault (the
            # in-flight record overlaid on the on-disk task/design graph)
            # before any write. Blocking errors → nothing written; notices are
            # held for the success path. A no-op for every kind that carries
            # neither graph.
            guard_errors, guard_notices = guards_mod.evaluate_graph_guards(
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
    # The reader URL for the vault the record actually landed in — last in the
    # stderr trailer (what happened, then warnings, then where to read it).
    _print_record_url(vault_root, record_id)

    # Schedule this vault's background publish AFTER the write has fully
    # succeeded — never able to change this command's exit code or stdout.
    publish_mod.request_publish(vault_root)

    # Print the vault-relative RECORD_ID on stdout.
    print(record_id)
    return 0


def _resolve_named_vault(name: str):
    """Resolve a ``--vault NAME`` argument to its configured :class:`vault_config.Vault`.

    The single *locate-by-vault* path, shared by every verb that takes a
    ``--vault NAME`` argument (``record show``'s, ``record delete``'s, and
    ``record update``'s current-location targeting, and ``lore task list``'s
    per-vault listing — see ``cli/task.py:_cmd_task_list``): loads ``config.json`` via
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


class _AliasedTrustConflict(Exception):
    """Path-aliased ``config.json`` entries disagree on one root's ``shared`` flag.

    Carries the operator-facing message; each caller reports it in its own
    convention (``record update`` aborts its write, ``record show`` refuses to
    render) rather than this module guessing which.
    """


def _vault_trust(vault_root: str) -> tuple[str, int]:
    """The ``(vault_name, shared_flag)`` ``config.json`` declares for *vault_root*.

    The one trust classification for an already-located record's vault, shared
    by ``record update``'s index stamp (:func:`_resolve_current_vault_shared`)
    and ``record show``'s output fence (:func:`_render_record`) — so what gets
    written as untrusted and what gets rendered as untrusted cannot drift apart.

    Matched on the resolved path, because ``config.json`` enforces unique vault
    *names* and not unique vault *paths*: a symlinked root or a relative path
    beside an absolute one is one vault, as it is everywhere else in this
    codebase that decides vault identity.

    A root no config declares — vanilla usage, or a path outside the configured
    set — is the operator's own: ``("", 0)``, the same trusted default
    ``record create`` stamps when there is no config to route by.

    **Path-aliased entries refuse rather than guess.** Two entries can name one
    directory carrying different ``shared`` flags, and that flag decides both
    whether a write is stamped untrusted and whether a read is fenced. Taking
    the config-order first match would let whichever alias happens to be listed
    first silently re-classify the record as trusted, so a disagreement raises
    :class:`_AliasedTrustConflict` naming the entries; aliases that agree are
    unambiguous and resolve normally.
    """
    from ..vault import config as vault_config_mod

    loaded = _load_vault_config()
    if loaded is None:
        return "", 0
    _, vaults = loaded
    current = Path(vault_root).resolve()
    matches = [v for v in vaults if Path(v.path).resolve() == current]
    if not matches:
        return "", 0
    flags = {vault_config_mod.shared_flag(v) for v in matches}
    if len(flags) > 1:
        names = ", ".join(repr(v.name) for v in matches)
        raise _AliasedTrustConflict(
            f"vault entries {names} all resolve to {current} but disagree "
            "on their 'shared' flag; fix config.json so path-aliased "
            "entries agree before reading or writing records there"
        )
    return matches[0].name, flags.pop()


def _resolve_current_vault_shared(location) -> tuple[str, int]:
    """Destination = the record's current vault, unchanged, with its trust flag.

    Used when no explicit ``--repo/--product/--suite/--team`` flag accompanied
    this ``record update`` call: there is nothing to re-route on, so the
    destination is simply where the record already lives — never re-derived
    from stale sidecar scope fields (see :func:`_resolve_destination_root`,
    which a stored ``repo``/``product``/``suite``/``team`` data field would
    otherwise feed into a fresh resolution and silently relocate the record).

    This also covers ``--vault NAME`` with no scope flag: the record was located
    in exactly the named vault, so its current vault IS that vault — pinning the
    destination there needs no separate branch.

    The trust flag is :func:`_vault_trust`'s, which also decides whether
    ``record show`` fences this record — one classification, so a write stamped
    untrusted is a read rendered untrusted. A path-aliased disagreement it
    cannot resolve aborts the update with a clean ``lore: `` error.
    """
    try:
        _, flag = _vault_trust(location.vault_root)
    except _AliasedTrustConflict as exc:
        raise _UpdateAborted(_fail([str(exc)], prefix="lore: ")) from exc
    return location.vault_root, flag


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


def _read_apply_and_guard(
    location,
    *,
    stdin_text: str,
    use_diff: bool,
    has_stdin: bool,
    notice_shown: set[str],
    apply_flags=None,
    status_set: str | None = None,
    supplied_depends_on: list[str] | None = None,
    parent_supplied: bool = False,
    allow_new_body: bool = False,
) -> tuple[dict, str, list[str], str]:
    """The read / resolve-body / apply / guard half of a record read-modify-write.

    Shared by ``record update`` and ``record amend`` so the two verbs cannot drift
    apart on how a body is resolved (full stdin, ``--diff``, or unchanged) or which
    guards judge the result. Returns ``(sidecar, new_body, notices, existing_body)``;
    raises :class:`_UpdateAborted` when a step has already reported its own error.

    MUST be called with the record's vault write lock held.

    *apply_flags*, when given, maps the in-memory sidecar through the caller's
    per-field mutations (``update``'s flags); ``amend`` has none. *allow_new_body*
    hands the resolved new body to the ADR body freeze as the one value it may
    become (``amend``); every other guard runs identically either way.
    """
    import json

    from ..record import guards as guards_mod
    from ..record import store as record_store_mod

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
            new_body, _ = record_store_mod.apply_unified_diff(existing_body, stdin_text)
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

    sidecar = dict(existing_sidecar)
    if apply_flags is not None:
        sidecar = apply_flags(sidecar)

    # Graph guards run against the record's CURRENT vault (where its
    # parent/depends-on relatives live), with the mutated record
    # overlaid. Blocking errors → nothing written; notices are held
    # for the success path. A no-op for every kind that carries
    # neither graph.
    #
    # ``--depends-on`` values are threaded in separately so the
    # task-edge form check judges only what THIS invocation adds:
    # an entry stored before that check existed must not block an
    # unrelated update (a ``--status`` flip) to the record holding
    # it, nor the ``--unset-depends-on`` that removes it.
    #
    # ``prior_status`` / ``prior_body`` are the on-disk values read
    # above, BEFORE this call's mutations — keyed on what the
    # record WAS, not what this write sets, so the freeze judges
    # "was this active adr's body left alone", never "is the
    # record still active after". A write into ``superseded`` is
    # refused separately by ``[superseded-status]``.
    guard_errors, notices = guards_mod.evaluate_graph_guards(
        kind=location.kind,
        name=location.name,
        sidecar=sidecar,
        body=new_body,
        vault_root=location.vault_root,
        status_set=status_set,
        supplied_depends_on=list(supplied_depends_on or []),
        parent_supplied=parent_supplied,
        prior_status=existing_sidecar.get("status"),
        prior_body=existing_body,
        allowed_body=new_body if allow_new_body else None,
    )
    if guard_errors:
        raise _UpdateAborted(_fail(guard_errors))

    return sidecar, new_body, notices, existing_body


def _pull_before_write(record_id: str, named_vault) -> None:
    """Converge on the other devices' records before an existing record is rewritten.

    The throttled, stderr-only, non-fatal implicit pull ``record create`` runs,
    shared by ``update`` and ``amend``. It has to happen BEFORE the index
    transaction and the write lock open: the pull fetches over the network and may
    reindex, and neither belongs inside the read-modify-write's critical section.
    That means locating the record twice — once here, unlocked, only to name the
    vault to pull, and again authoritatively under the transaction. A record this
    device has never seen simply gets no pull; the write then fails not-found as it
    always did.
    """
    from . import resolve_state as resolve_state_mod

    pull_target = Path(named_vault.path) if named_vault is not None else None
    if pull_target is None:
        try:
            pull_target = Path(_find_current_record_location(record_id).vault_root)
        except Exception:  # noqa: BLE001 — advisory: the real locate runs below
            pull_target = None
    if pull_target is not None and not resolve_state_mod.vault_is_resolving(pull_target):
        from . import publish as publish_mod
        from . import sync as sync_mod

        sync_mod.implicit_pull(pull_target)
        # Beside the freshness note: a stale automatic publish from a PRIOR
        # write is surfaced here too, before this write's own trigger below.
        publish_mod.warn_stale_publish(pull_target)


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

    **``--vault`` alone pins the destination too**, as a consequence of the rule
    below rather than a case of its own: with no explicit destination scope flag
    accompanying it, the destination is the record's current vault, which is the
    vault ``--vault`` just named. So a record whose sidecar carries no scope
    field at all (a legacy/unstamped record) is never silently moved to the
    default vault just because its empty scope resolves fresh there. An explicit
    destination scope flag alongside ``--vault`` still means "re-route" — the two
    concerns compose, they don't gate each other.

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

    A change to the authored body of a frozen adr, spec, decision or task is
    refused here (``[frozen-body]``), and an adr or spec cannot return to
    ``draft`` (``[frozen-status]``); the refusal names ``lore record amend`` as
    the in-place correction path.
    The read / body-resolve / guard step is :func:`_read_apply_and_guard`, shared
    with ``record amend``.
    """
    from contextlib import ExitStack

    from .. import locking as locking_mod
    from ..record import fields as fields_mod
    from ..record import guards as guards_mod
    from ..record import store as record_store_mod
    from . import resolve_state as resolve_state_mod

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
    try:
        stdin_text = _read_stdin_body()
    except StdinSilentError as exc:
        print(f"lore: {exc}", file=sys.stderr)
        return 1
    has_stdin = stdin_text != ""

    # Converge on the other devices' records before writing (see the helper).
    _pull_before_write(record_id, named_vault)

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
            # Same fence as ``record create``: a vault mid-rebase is being
            # resolved, and an update landing in it would be rewritten by the
            # resolution. Refused before the lock, so nothing is written.
            if resolve_state_mod.vault_is_resolving(location.vault_root):
                print(
                    resolve_state_mod.refusal_notice(location.vault_root, "record update"),
                    file=sys.stderr,
                )
                raise _UpdateAborted()

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

                def apply_flags(sidecar: dict) -> dict:
                    # (2) --- apply ALL field mutations to the sidecar IN MEMORY ----
                    # Scope flags (--team etc.) become field-setters that drive the
                    # merged scope; the non-scope per-field flags (--status /
                    # --title / --keyword / --related-*) reuse the shared applier.
                    # --title is optional here.
                    for flag in _SCOPE_FLAGS:
                        val = getattr(args, flag, None)
                        if val:
                            sidecar[flag] = val

                    sidecar, field_errors = fields_mod.apply_record_fields(sidecar, args)
                    if field_errors:
                        raise _UpdateAborted(_fail(field_errors))

                    # Apply --label / --annotation / --unset-label / --unset-annotation.
                    return fields_mod.apply_map_labels_annotations(
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

                sidecar, new_body, notices, _ = _read_apply_and_guard(
                    location,
                    stdin_text=stdin_text,
                    use_diff=use_diff,
                    has_stdin=has_stdin,
                    notice_shown=notice_shown,
                    apply_flags=apply_flags,
                    status_set=getattr(args, "status", None),
                    supplied_depends_on=list(getattr(args, "depends_on", None) or []),
                    parent_supplied=getattr(args, "parent", None) is not None,
                )
                return sidecar, new_body, notices

            sidecar, new_body, guard_notices = read_apply_and_guard()

            # (3) re-resolve the DESTINATION (root + shared trust), gated on an
            # explicit --repo/--product/--suite/--team flag being passed THIS call.
            #
            # No explicit scope flag this call means no re-resolution at all -- the
            # destination is just the record's current vault
            # (_resolve_current_vault_shared), never re-derived from merged_sidecar.
            # Two distinct silent relocations are closed by that one gate:
            #
            #   - A stored repo/product/suite/team field is ordinary data, not a
            #     standing routing request, so it must not feed the create-side
            #     resolver on its own (a metadata-only update would otherwise
            #     relocate a record off a stale/unconfigured stored scope value).
            #   - A record with NO scope field at all (e.g. a legacy/unstamped one)
            #     would resolve fresh off an EMPTY scope, which always lands on the
            #     default vault (vault/resolve.py's totality floor) -- moving it out
            #     of the vault the caller explicitly targeted with --vault. Since
            #     --vault locates the record in exactly that vault, "stay in the
            #     current vault" IS "stay in the named vault"; no separate case.
            #
            # An explicit scope flag still means "re-route", --vault or not.
            def resolve_destination(sidecar: dict) -> tuple[str, bool]:
                if not any(getattr(args, flag, None) for flag in _SCOPE_FLAGS):
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
                    # A destination id that already exists is overwritten by the
                    # move, so it is refused exactly as an in-place write would be:
                    # the freeze, and the rule that ``superseded`` is reached only
                    # through the verb, each judged on the destination's on-disk
                    # status and body against the moved record's resulting ones,
                    # read under the pair held above.
                    if dest_body_path.exists() or dest_sidecar_path.exists():
                        dest_prior = (
                            json.loads(dest_sidecar_path.read_text(encoding="utf-8"))
                            if dest_sidecar_path.exists()
                            else {}
                        )
                        dest_prior_body = (
                            dest_body_path.read_text(encoding="utf-8")
                            if dest_body_path.exists()
                            else ""
                        )
                        frozen_refusal = guards_mod.check_frozen_record(
                            kind=dest_kind,
                            name=dest_name,
                            prior_status=dest_prior.get("status"),
                            prior_body=dest_prior_body,
                            new_body=safe_body,
                            status_set=stamped.get("status"),
                        ) or guards_mod.check_superseded_status(
                            kind=dest_kind,
                            name=dest_name,
                            prior_status=dest_prior.get("status"),
                            status_set=None,
                            resulting_status=stamped.get("status"),
                        )
                        if frozen_refusal:
                            raise _UpdateAborted(_fail([frozen_refusal]))
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
    # The reader URL for the DESTINATION vault — the one the record now lives
    # in, which on a relocation is not the vault it started this call in.
    _print_record_url(dest_root, new_id)

    # Schedule a background publish for the vault the record now lives in —
    # covers both the in-place branch and the relocation branch, since
    # `dest_root` is the record's final vault either way.
    from . import publish as publish_mod

    publish_mod.request_publish(dest_root)

    # The relocation signal (no silent move) precedes the
    # RECORD_ID so the existing stdout contract for the no-move case is unchanged
    # (RECORD_ID stays the trailing parseable line); the moved case adds the signal.
    if moved_line is not None:
        print(moved_line)
    print(new_id)
    return 0


def _read_sidecar_and_body(location) -> tuple[dict, str]:
    sidecar = json.loads(location.sidecar_path.read_text(encoding="utf-8"))
    body = (
        location.body_path.read_text(encoding="utf-8")
        if location.body_path.exists()
        else ""
    )
    return sidecar, body


def _supersede_refusal(message: str) -> int:
    from ..record import graph as graph_mod

    print(graph_mod.format_guard_message("supersede-missing", message), file=sys.stderr)
    return 1


def _cmd_record_supersede(args) -> int:
    """``lore record supersede PREDECESSOR --by SUCCESSOR`` — record a supersession.

    Resolves both records, then — inside both vaults' write locks, with both
    read after the locks are held — appends PREDECESSOR to the successor's
    ``supersedes`` list and sets the predecessor's status to ``superseded``, in
    that order. The edge goes first so an interrupted run leaves a state the verb
    itself completes. ``superseded-by`` on the predecessor is derived at reindex.

    Idempotent: each half is skipped when already in place, so re-running a
    finished pair writes nothing, and re-running after a failed flip completes
    it. A predecessor that is already ``superseded`` gains the new successor's
    edge and keeps its status (a split names each child this way).

    Every check — the pairing guards, the graph guards, trust resolution and
    schema validation — runs for every step before the first write lands, so a
    refusal or a failed check writes nothing. Only a write itself can tear the
    pair: a failure exits nonzero with the exact re-run command on stderr and
    names the records that did land (the successor's edge, when the flip is the
    write that failed) or says nothing landed. Refusals are the ``supersede-...``
    guards of ``guards.evaluate_supersede_pairing`` plus ``[supersede-missing]``.
    The task-edge form check does not judge edges the verb did not supply. Neither
    body is ever changed.
    ``--vault NAME`` locates both records in that one vault.
    """
    import shlex
    from contextlib import ExitStack

    from .. import locking as locking_mod
    from ..record import guards as guards_mod
    from ..record import store as record_store_mod
    from . import publish as publish_mod
    from . import resolve_state as resolve_state_mod

    pred_id = _require_record_id(args)
    if pred_id is None:
        return 1
    succ_id = getattr(args, "by", None)
    if not succ_id or "/" not in succ_id:
        print(f"error: invalid --by {succ_id!r}; expected '<kind>/<name>'", file=sys.stderr)
        return 1
    if pred_id == succ_id:
        return _fail(
            guards_mod.evaluate_supersede_pairing(
                predecessor_id=pred_id, predecessor={}, successor_id=succ_id,
                successor={}, load_sidecar=lambda _id: None,
            )
        )

    named_vault = None
    if getattr(args, "vault", None):
        named_vault = _resolve_named_vault(args.vault)
        if named_vault is None:
            return 1

    for rid in (pred_id, succ_id):
        _pull_before_write(rid, named_vault)

    def locate(rid: str):
        if named_vault is not None:
            return record_store_mod.locate_record(rid, vault_root=str(named_vault.path))
        return _find_current_record_location(rid)

    def load_sidecar(rid: str) -> dict | None:
        try:
            return _read_sidecar_and_body(locate(rid))[0]
        except (record_store_mod.RecordNotFoundError, record_store_mod.InvalidRecordIdError,
                OSError, ValueError):
            return None

    rerun = f"lore record supersede {shlex.quote(pred_id)} --by {shlex.quote(succ_id)}"
    if named_vault is not None:
        rerun += f" --vault {shlex.quote(named_vault.name)}"

    written_roots: list[str] = []
    try:
        with record_store_mod.index_transaction() as conn, ExitStack() as stack:
            try:
                pred_loc, succ_loc = locate(pred_id), locate(succ_id)
            except (record_store_mod.RecordNotFoundError,
                    record_store_mod.InvalidRecordIdError) as exc:
                return _supersede_refusal(str(exc))
            for loc in (pred_loc, succ_loc):
                if resolve_state_mod.vault_is_resolving(loc.vault_root):
                    print(
                        resolve_state_mod.refusal_notice(loc.vault_root, "record supersede"),
                        file=sys.stderr,
                    )
                    return 1
            stack.enter_context(
                locking_mod.vault_write_locks(pred_loc.vault_root, succ_loc.vault_root)
            )

            # Both records are read only now, with the locks held.
            try:
                pred_sidecar, pred_body = _read_sidecar_and_body(pred_loc)
                succ_sidecar, succ_body = _read_sidecar_and_body(succ_loc)
            except (OSError, ValueError):
                return _supersede_refusal(
                    f"cannot read {pred_id} or {succ_id}: record is missing or unreadable"
                )
            refusal = guards_mod.evaluate_supersede_pairing(
                predecessor_id=pred_id, predecessor=pred_sidecar,
                successor_id=succ_id, successor=succ_sidecar, load_sidecar=load_sidecar,
            )
            if refusal:
                return _fail(refusal)

            edge_present = pred_id in (succ_sidecar.get("supersedes") or [])
            flip_needed = pred_sidecar.get("status") != "superseded"
            if edge_present and not flip_needed:
                outcome = "already recorded"
            elif flip_needed:
                outcome = "superseded"
            else:
                outcome = "edge added"

            steps = []
            notices: list[str] = []
            if not edge_present:
                new_succ = dict(succ_sidecar)
                new_succ["supersedes"] = [*(succ_sidecar.get("supersedes") or []), pred_id]
                steps.append((succ_loc, new_succ, succ_body, succ_sidecar.get("status"), None))
            if flip_needed:
                new_pred = dict(pred_sidecar)
                new_pred["status"] = "superseded"
                steps.append((pred_loc, new_pred, pred_body, pred_sidecar.get("status"), "superseded"))

            # Every write is judged before the first lands: the graph guards, the
            # trust resolution and the schema validation all run for every step
            # here, so only an actual write failure can tear the pair. Neither body
            # changes, so the body read under the lock is also the prior body the
            # freeze compares against. The verb supplies no depends-on or parent,
            # so the task-edge form check leaves stored edges alone, as it does for
            # any update that does not touch them.
            errors: list[str] = []
            for loc, sidecar, body, prior_status, status_set in steps:
                step_errors, step_notices = guards_mod.evaluate_graph_guards(
                    kind=loc.kind, name=loc.name, sidecar=sidecar, body=body,
                    vault_root=loc.vault_root, status_set=status_set,
                    supplied_depends_on=[], parent_supplied=False,
                    prior_status=prior_status, prior_body=body,
                    allow_superseded_status=True,
                )
                errors.extend(step_errors)
                notices.extend(step_notices)
            if errors:
                return _fail(errors)

            trust: dict[str, int] = {}
            for loc, sidecar, body, _prior, _set in steps:
                _, trust[loc.record_id] = _resolve_current_vault_shared(loc)
                record_store_mod.validate_stamp_neutralize(loc, sidecar, body)

            landed: list[str] = []
            for loc, sidecar, body, _prior, _set in steps:
                try:
                    record_store_mod.validate_and_write(
                        location=loc, sidecar=sidecar, body=body, conn=conn,
                        shared=trust[loc.record_id],
                    )
                except Exception as exc:  # noqa: BLE001 — reported below with the re-run command
                    conn.commit()
                    for root in dict.fromkeys(written_roots):
                        publish_mod.request_publish(root)
                    if landed:
                        outcome_line = (
                            f"error: supersession incomplete — writing {loc.record_id} failed"
                            f" after {', '.join(landed)} was written: {exc}\n"
                            "Run this to finish it:\n"
                        )
                    else:
                        outcome_line = (
                            f"error: supersession not recorded — writing {loc.record_id} failed,"
                            f" nothing landed: {exc}\nRun this to retry:\n"
                        )
                    print(outcome_line + rerun, file=sys.stderr)
                    return 1
                landed.append(loc.record_id)
                written_roots.append(loc.vault_root)
            conn.commit()
    except _UpdateAborted as aborted:
        return aborted.code
    except Exception as exc:
        return _handle_write_error(exc, "supersede")

    _print_guard_notices(notices)
    for root in dict.fromkeys(written_roots):
        publish_mod.request_publish(root)
    verb = {
        "superseded": f"superseded: {pred_id} is superseded by {succ_id}",
        "edge added": f"edge added: {succ_id} now supersedes {pred_id} (already superseded)",
        "already recorded": f"already recorded: {pred_id} is superseded by {succ_id}",
    }[outcome]
    print(verb)
    print(f"note: superseded-by on {pred_id} appears in search after 'lore reindex'")
    return 0


_SESSION_AMEND_REFUSAL = (
    "error: a session record cannot be amended: lore record update and "
    "lore flush write it under the session lock"
)


def _check_base_revision(location, base: str | None, *, required: bool) -> None:
    """Refuse a write composed against a body that is no longer the stored one.

    MUST be called with the record's vault write lock held, after the implicit
    pull, so the comparison is against the body the write would replace. *base*
    is the revision the caller read (``lore record show``); *required* is true
    when the new body is a full replacement, which would otherwise silently
    overwrite a change made after the read. Raises :class:`_UpdateAborted` after
    printing the refusal; returns when the write may proceed.
    """
    from ..record import store as record_store_mod

    show_hint = (
        f"read the current revision with `lore record show {location.record_id}` "
        "(`--json` carries it as `revision`; plain show prints `revision: <hex>` "
        "on stderr)"
    )
    if base is None:
        if required:
            print(
                "error: --base is required with a full-body amend: pass the "
                f"revision of the body you composed against. {show_hint}, then "
                "amend with --base <revision>. (A --diff amend may omit it: a "
                "diff whose context still matches overwrites nobody's change.)",
                file=sys.stderr,
            )
            raise _UpdateAborted()
        return

    current = record_store_mod.stored_body_revision(location.body_path)
    if base == current:
        return

    updated_by = updated_at = "unknown"
    has_history = False
    if location.sidecar_path.exists():
        try:
            side = json.loads(location.sidecar_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            side = {}
        if isinstance(side, dict):
            updated_by = _inert_stamp(side.get("updated-by", updated_by))
            updated_at = _inert_stamp(side.get("updated-at", updated_at))
            has_history = bool(side.get("change-history"))
    history_hint = (
        f" If that change was an amend, `lore record history {location.record_id}` "
        "lists it."
        if has_history else ""
    )
    try:
        shared = _vault_trust(str(location.vault_root))[1] != 0
    except _AliasedTrustConflict:
        shared = True
    if shared:
        last_changed = (
            "Who last changed it and when is withheld because this vault is shared."
        )
    else:
        last_changed = _neutral(f"It was last changed by {updated_by} at {updated_at}.")
    print(
        f"error: {location.record_id} changed after you read it: --base is "
        f"{base} but the stored body's revision is {current}. "
        f"{last_changed}{history_hint} Nothing was "
        f"written. To proceed: {show_hint}, reapply your correction to what "
        "you read, and amend again.",
        file=sys.stderr,
    )
    raise _UpdateAborted()


def _cmd_record_amend(args) -> int:
    """``lore record amend RECORD_ID --reason R --initiated-by operator|agent`` — thin shell.

    Rewrites an existing record's body and appends one ``change-history`` entry
    recording when (``at``), by whom (``by``, the committer identity every write is
    stamped with), who asked for it (``initiated-by``), why (``reason``) and what
    changed (``diff``, the unified diff between the stored — fence-neutralized —
    bodies, so ``apply_unified_diff(stored_before, diff)`` reproduces the stored
    body exactly). It is the one write that may change a frozen record's body: the
    freeze accepts amend's own resolved new body through the guard's
    ``allowed_body`` seam, and every other guard runs exactly as it does for
    ``record update``.

    The new body comes from stdin: a full replacement, or with ``--diff`` a unified
    diff applied to the stored body (the same resolution ``record update`` uses —
    :func:`_read_apply_and_guard`). Unlike ``update`` there is no metadata-only
    mode: empty stdin is a refusal, never a quiet no-op. Nothing but the body and
    the history changes — the record's id, kind, status, labels and links are
    kept, and no other record is created or touched.

    **Attested, not verified.** ``--initiated-by`` is recorded as stated:
    ``operator`` means a human instructed this edit in the current session,
    ``agent`` means the agent proposed it. Lore cannot check either claim.
    **Not a scrub path.** The reason and the diff are permanent; the removed text
    stays in the diff and in vault git history.

    **Refusals** (each prints an ``error: ...`` line naming the input to fix, exits
    non-zero, and writes nothing): a missing, empty or multi-line ``--reason``; a
    missing or invalid ``--initiated-by``; empty stdin; a ``--diff`` that does not
    apply; a body identical (after fence neutralization) to the stored one; a
    record that does not exist; a ``session`` record, which ``record update`` and
    flush write under the session lock.

    **Criterion numbering (spec records, any status).** A spec's ``**ACn.**``
    criteria keep their numbers for good. An amend may remove criteria, edit text
    in place, and add criteria from the next unused number upward; it is refused,
    writing nothing, if it skips a number, reuses a removed number, relabels a
    criterion's text under another number, or leaves the body without exactly one
    ``## Acceptance Criteria`` heading. Each removal is recorded in the entry as
    ``criteria: {"removed": [...]}`` and printed as ``removed criteria: ...`` so a
    removal you did not intend (a mangled ``**ACn.**`` marker reads as one) is
    visible at once; ``lore record amend --revert-to N`` restores it. ``--revert-to``
    skips the numbering checks (it restores a body that already existed) but still
    records what it removes. Named limits: renumber detection matches criterion
    text, so a relabel that also rewords escapes it; a criterion removed outside
    amend (for example while the spec was a draft) is not recorded, so its number
    is not protected; and an older lore build refuses writes to a spec once an
    entry carries ``criteria`` (reads are unaffected), the only recovery being to
    update lore.

    **Criterion marks (spec records only).** ``--reopen ACn`` and ``--close ACn``
    (each repeatable) record a human decision about a criterion in the entry's
    ``criteria`` as ``reopened`` / ``closed``. A reopen voids the coverage
    recorded before the amend: the entry stores ``ledger``, the task ids of the
    ``## Slices`` entries in the *stored* body, read under the write lock — a
    ledger line the new body adds is never part of the snapshot. A second reopen
    is allowed (it voids what was recorded since); a second close is refused as
    already closed; a close undoes a mistaken reopen. Reopening after a mistaken
    close voids all earlier coverage, so the prior partial state returns only
    when a slice records it again. With marks and no stdin the amend is
    marks-only: the body is unchanged, the entry's diff is empty and ``--base``
    is optional (checked if given). With marks and a body (full or ``--diff``)
    one entry carries both. Marks on a non-spec record, marks with
    ``--revert-to``, and every :func:`criteria.check_criteria_marks` refusal
    print ``error: ...`` naming the ID, the problem and the fix, then
    ``nothing was written``. Success prints ``reopened criteria: AC2 (voids
    coverage from task/a, task/b)`` (or ``(nothing to void)``), ``closed
    criteria: ...`` and the ``removed criteria:`` line. The reason is permanent
    and must never hold secrets.

    **The revision handshake.** ``--base REVISION`` (the SHA-256 of the body as
    ``record show`` printed it) is required with a full-body stdin and optional
    with ``--diff``. Under the write lock, after the implicit pull, it is compared
    with the stored body's revision by :func:`_check_base_revision`; a mismatch
    refuses the amend and writes nothing. A diff may omit it because its hunk
    context already refuses an overlapping concurrent edit, while a
    non-overlapping one is kept only when it shifts no lines above the hunk
    (hunks are located at their recorded line, with no offset search). The revision covers the body only, so a label or
    status change made after the read is neither overwritten nor a refusal.

    ``--vault NAME`` locates the record in exactly that configured vault, as it
    does for ``update``. The read-modify-write runs under the vault's write lock.
    """
    from contextlib import ExitStack

    from .. import locking as locking_mod
    from ..record import criteria as criteria_mod
    from ..record import model as record_model_mod
    from ..record import store as record_store_mod
    from . import resolve_state as resolve_state_mod

    record_id = _require_record_id(args)
    if record_id is None:
        return 1

    reason = getattr(args, "reason", None)
    if reason is None or not reason.strip():
        print(
            "error: --reason is required: give a non-empty one-line reason for the amendment",
            file=sys.stderr,
        )
        return 1
    problem = record_model_mod.change_history_reason_problem(reason)
    if problem:
        print(f"error: --reason {problem}", file=sys.stderr)
        return 1
    initiated_by = getattr(args, "initiated_by", None)
    if initiated_by is None:
        print(
            "error: --initiated-by is required: pass operator (a human instructed "
            "this edit) or agent (the agent proposed it)",
            file=sys.stderr,
        )
        return 1
    if initiated_by not in record_model_mod.CHANGE_HISTORY_INITIATORS:
        print(
            f"error: --initiated-by must be operator or agent, got {initiated_by!r}",
            file=sys.stderr,
        )
        return 1
    if record_id.split("/", 1)[0] == "session":
        print(_SESSION_AMEND_REFUSAL, file=sys.stderr)
        return 1

    named_vault = None
    vault_name = getattr(args, "vault", None)
    if vault_name:
        named_vault = _resolve_named_vault(vault_name)
        if named_vault is None:
            return 1

    use_diff = bool(getattr(args, "diff", False))
    revert_raw = getattr(args, "revert_to", None)
    revert_to = None
    reopen_ids = list(getattr(args, "reopen", None) or [])
    close_ids = list(getattr(args, "close", None) or [])
    has_marks = bool(reopen_ids or close_ids)
    if revert_raw is not None:
        if has_marks:
            print(
                "error: --reopen/--close cannot be combined with --revert-to: a "
                "revert restores an earlier body, so run the revert, then amend "
                "again with the marks\nnothing was written",
                file=sys.stderr,
            )
            return 1
        if use_diff:
            print(
                "error: --revert-to cannot be combined with --diff: it computes "
                "the body itself from the record's history",
                file=sys.stderr,
            )
            return 1
        revert_to = _parse_entry_number(revert_raw, "--revert-to")
        if revert_to is None:
            return 1
    try:
        stdin_text = _read_stdin_body()
    except StdinSilentError as exc:
        print(f"lore: {exc}", file=sys.stderr)
        return 1
    if revert_to is not None and stdin_text != "":
        print(
            "error: --revert-to takes no body on stdin: lore computes the body "
            "from the record's history, so nothing is read from stdin",
            file=sys.stderr,
        )
        return 1
    if stdin_text == "" and revert_to is None and not has_marks:
        print(
            "error: no body on stdin: pipe the new body, pass --diff with a "
            "unified diff, or pass --reopen/--close to mark criteria without "
            "changing the body\nnothing was written",
            file=sys.stderr,
        )
        return 1
    marks_only = has_marks and stdin_text == ""

    _pull_before_write(record_id, named_vault)

    guard_notices: list[str] = []
    try:
        with record_store_mod.index_transaction() as conn, ExitStack() as locks:
            if named_vault is not None:
                location = record_store_mod.locate_record(
                    record_id, vault_root=str(named_vault.path)
                )
            else:
                location = _find_current_record_location(record_id)

            if location.kind.casefold() == "session":
                print(_SESSION_AMEND_REFUSAL, file=sys.stderr)
                raise _UpdateAborted()

            if resolve_state_mod.vault_is_resolving(location.vault_root):
                print(
                    resolve_state_mod.refusal_notice(location.vault_root, "record amend"),
                    file=sys.stderr,
                )
                raise _UpdateAborted()

            if has_marks and location.kind.casefold() != "spec":
                print(
                    f"error: --reopen/--close apply only to spec records, and "
                    f"{location.record_id} is a {location.kind}: drop the flag\n"
                    "nothing was written",
                    file=sys.stderr,
                )
                raise _UpdateAborted()

            locks.enter_context(locking_mod.vault_write_lock(location.vault_root))

            _check_base_revision(
                location,
                getattr(args, "base", None),
                required=not use_diff and not marks_only,
            )

            if marks_only:
                use_diff = False
                stdin_text = (
                    location.body_path.read_text(encoding="utf-8")
                    if location.body_path.exists()
                    else ""
                )

            if revert_to is not None:
                stdin_text = _reconstruct_from_stored(location, revert_to, "--revert-to")
                if stdin_text is None:
                    raise _UpdateAborted()

            sidecar, new_body, guard_notices, existing_body = _read_apply_and_guard(
                location,
                stdin_text=stdin_text,
                use_diff=use_diff,
                has_stdin=True,
                notice_shown=set(),
                allow_new_body=True,
            )

            stored_before = record_store_mod.neutralize_fences(existing_body)
            stored_after = record_store_mod.neutralize_fences(new_body)
            if stored_after == stored_before and not has_marks:
                print(
                    "error: the new body is identical to the stored body: nothing to amend",
                    file=sys.stderr,
                )
                raise _UpdateAborted()

            by = record_store_mod.resolve_committer_email()
            if not by:
                raise record_store_mod.ProvenanceError(
                    "set git config user.email; *-by provenance is required "
                    "and cannot be defaulted"
                )
            entry = {
                "at": record_store_mod._now_utc_z(),
                "by": by,
                "initiated-by": initiated_by,
                "reason": reason,
                "diff": record_store_mod.make_unified_diff(stored_before, stored_after),
            }
            stored_history = sidecar.get("change-history", [])
            if not isinstance(stored_history, list):
                print(
                    f"error: {location.record_id} has a malformed change-history "
                    f"(stored as {type(stored_history).__name__}, not a list): "
                    "amending would reset the log. Restore the field from the "
                    "record's vault git history (git log -p -- "
                    f"{location.kind}/{location.name}.json), then amend again",
                    file=sys.stderr,
                )
                raise _UpdateAborted()

            removed_now: list[str] = []
            if location.kind.casefold() == "spec":
                try:
                    removed_now, refusals = criteria_mod.check_criteria_amend(
                        stored_before, stored_after, stored_history
                    )
                except criteria_mod.CriteriaError as exc:
                    print(
                        f"error: {location.record_id} has a malformed change-history "
                        f"criteria record ({exc}): amending would lose track of removed "
                        "criteria. Restore the field from the record's vault git history "
                        f"(git log -p -- {location.kind}/{location.name}.json), then "
                        "amend again",
                        file=sys.stderr,
                    )
                    raise _UpdateAborted()
                if refusals and revert_to is None:
                    for refusal in refusals:
                        print(f"error: {refusal}", file=sys.stderr)
                    print("nothing was written", file=sys.stderr)
                    raise _UpdateAborted()
            criteria_entry: dict = {}
            if removed_now:
                criteria_entry["removed"] = removed_now
            voided: dict[str, list[str]] = {}
            if has_marks:
                try:
                    voided, mark_refusals = criteria_mod.check_criteria_marks(
                        stored_before, stored_after, stored_history, reopen_ids, close_ids
                    )
                except criteria_mod.CriteriaError as exc:
                    print(
                        f"error: {location.record_id} has a malformed change-history "
                        f"criteria record ({exc}): marking criteria would lose track "
                        "of earlier marks. Restore the field from the record's vault "
                        f"git history (git log -p -- {location.kind}/{location.name}.json), "
                        "then amend again\nnothing was written",
                        file=sys.stderr,
                    )
                    raise _UpdateAborted()
                if mark_refusals:
                    for refusal in mark_refusals:
                        print(f"error: {refusal}", file=sys.stderr)
                    print("nothing was written", file=sys.stderr)
                    raise _UpdateAborted()
                if reopen_ids:
                    criteria_entry["reopened"] = reopen_ids
                    criteria_entry["ledger"] = list(dict.fromkeys(
                        task for task, _, _ in criteria_mod.ledger_entries(stored_before)
                    ))
                if close_ids:
                    criteria_entry["closed"] = close_ids
            if criteria_entry:
                entry["criteria"] = criteria_entry
            sidecar["change-history"] = [*stored_history, entry]

            dest_root, dest_shared = _resolve_current_vault_shared(location)
            new_id = record_store_mod.validate_and_write(
                location=location, sidecar=sidecar, body=new_body, conn=conn,
                shared=dest_shared,
            )
            conn.commit()
    except _UpdateAborted as aborted:
        return aborted.code
    except (
        record_store_mod.RecordNotFoundError,
        record_store_mod.InvalidRecordIdError,
    ) as exc:
        prefix = "lore: " if named_vault is not None else "error: "
        print(f"{prefix}{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        return _handle_write_error(exc, "amend")

    _print_guard_notices(guard_notices)
    _print_record_url(dest_root, new_id)

    from . import publish as publish_mod

    publish_mod.request_publish(dest_root)
    for ident in reopen_ids:
        tasks = voided.get(ident)
        effect = (
            "voids coverage from " + ", ".join(f"task/{t}" for t in tasks)
            if tasks
            else "nothing to void"
        )
        print(f"reopened criteria: {ident} ({effect})")
    if close_ids:
        print("closed criteria: " + ", ".join(close_ids))
    if removed_now:
        print("removed criteria: " + ", ".join(removed_now))
    print(new_id)
    return 0


_AMEND_MARKS_HELP = (
    'Spec criteria marks. --reopen ACn (repeatable) voids the coverage recorded before the amend: the '
    'snapshot is the ledger entries stored at the amend, never ones the new body adds. --close ACn '
    '(repeatable) closes a criterion without a slice covering it, and undoes a mistaken reopen. A second '
    'reopen is allowed (it voids what was recorded since); a second close is refused as already closed. '
    'Reopening after a mistaken close voids all earlier coverage, so the prior partial state returns only'
    ' when a slice records it again. With no stdin the amend is marks-only (body unchanged, --base '
    'optional but checked if given); with a body (full or --diff) one entry carries both. Marks apply to '
    'spec records only and cannot be combined with --revert-to. The reason is permanent: never put '
    'secrets in it.'
)


# Stated once per parser description instead of restated in every --unset-<field>
# help string: the removers are a mechanical family, and repeating the same
# sentence a dozen times is what made these two help outputs the most expensive
# in the CLI.
_UNSET_PAIRING_RULE = (
    "The --keyword/--related*/--depends-on/--label/--annotation setters repeat; "
    "each has an --unset-<field> remover taking the value shown beside it: it "
    "drops every matching entry; an absent value is a silent no-op. --unset-depends-on "
    "matches the stored value byte-for-byte, so an "
    "unqualified entry never matches a staged KIND/NAME@STAGE one. "
    "--supersedes/--unset-supersedes edit the raw edge; record a "
    "supersession with `lore record supersede`."
)

# Shared by both ``record create`` and ``record update`` --help epilogs: the two
# verbs accept the same --label/--related/--annotation flags, and an agent that
# reaches for ``update`` (the most common verb for writing labels) must see the
# same choosing rule ``create`` carries, not a partial or drifted copy of it.
_LABELS_FLAG_MECHANISM_RULE = (
    "Choosing a labels flag: a value naming another record — a task, a "
    "decision, an area — is a relation, not an attribute; use --related "
    "KIND=NAME instead of a label. A free attribute is a label; use "
    "--label KEY=VALUE. A labels key that shadows a record kind or a "
    "query field name (e.g. 'area', 'phase', 'status', 'kind') is refused "
    "at write time; the refusal names a runnable fix — --annotation "
    "KEY=VALUE for a free attribute whose natural name is taken, or a "
    "namespaced key (<ns>/<key>, e.g. craft/subsystems) to keep it "
    "queryable as a label."
)


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
        # The full argparse-generated usage banner listed all 27 options over a
        # dozen lines before the option table said the same thing again; the
        # compact form names the required arguments and defers the rest.
        usage="lore record create --kind KIND --title TITLE [OPTION ...]",
        description="Create a new vault record; the body is read from stdin. "
                    "--repo/--product/--suite/--team are routing scopes: each "
                    "picks the destination vault and stamps its namesake sidecar "
                    "field. " + _UNSET_PAIRING_RULE,
        epilog=_LABELS_FLAG_MECHANISM_RULE,
    )
    p_record_create.add_argument(
        "--kind", required=True,
        help="adr, area, blob, collaboration, decision, lesson, session, spec, "
             "or task.",
    )
    p_record_create.add_argument(
        "--title", required=True,
        help="Human-readable title (the record name slug is derived from it).",
    )
    # Routing flags: passed to place_record as scope. These influence vault
    # selection; their sidecar-write semantics are owned by the record-write layer.
    p_record_create.add_argument("--repo", default=None, help="Repo routing scope.")
    p_record_create.add_argument("--product", default=None, help="Product routing scope.")
    p_record_create.add_argument("--suite", default=None, help="Suite routing scope.")
    p_record_create.add_argument("--team", default=None, help="Team routing scope.")
    p_record_create.add_argument(
        "--vault", dest="vault", default=None, metavar="NAME",
        help="Write to exactly this configured vault, bypassing routing (the "
             "scope flags still stamp their sidecar fields).",
    )
    _add_record_field_flags(p_record_create)
    # Map flags (labels/annotations): dedicated branch.
    _add_map_field_flags(p_record_create)
    p_record_create.set_defaults(func=cmd_record)

    # ``lore record update RECORD_ID``.
    p_record_update = p_record_sub.add_parser(
        "update", help="Update an existing vault record (full-body / --diff / metadata-only)",
        usage="lore record update RECORD_ID [OPTION ...]",
        description="Update a vault record; a piped body replaces the existing one "
                    "and no stdin leaves it untouched. --repo/--product/--suite/"
                    "--team each set their namesake scope field, re-route the "
                    "record, and auto-move it when the destination vault changes. "
                    + _UNSET_PAIRING_RULE,
        epilog="Group-default scope routing applies to 'create' only; "
               "update (and delete) are unaffected and never seed scopes from a camp group. "
               + _LABELS_FLAG_MECHANISM_RULE,
    )
    p_record_update.add_argument(
        "record_id",
        metavar="RECORD_ID",
        help="The vault-relative record ID to update (<kind>/<name>).",
    )
    p_record_update.add_argument(
        "--diff", action="store_true",
        help="Treat piped stdin as a unified diff. Any non-applying hunk leaves "
             "the record unmodified and prints the rejects to stderr.",
    )
    p_record_update.add_argument(
        "--vault", dest="vault", default=None, metavar="NAME",
        help="Look for the record only in this configured vault, instead of "
             "scanning every vault in config order; it never re-routes.",
    )
    # Scope flags: dual field-setter +
    # routing on update too. Each writes its namesake sidecar field AND re-resolves
    # the destination vault from the merged scope; when the destination differs from
    # the record's current vault the record is auto-moved (no manual --move-to).
    p_record_update.add_argument("--repo", default=None, help="Repo scope.")
    p_record_update.add_argument("--product", default=None, help="Product scope.")
    p_record_update.add_argument("--suite", default=None, help="Suite scope.")
    p_record_update.add_argument("--team", default=None, help="Team scope.")
    # ``--title`` is an optional setter on update (it is the required positional
    # on create); the rest of the per-field flags are shared.
    p_record_update.add_argument(
        "--title", default=None,
        help="Overwrite the record's title.",
    )
    _add_record_field_flags(p_record_update)
    # Map flags (labels/annotations): dedicated branch.
    _add_map_field_flags(p_record_update)
    p_record_update.set_defaults(func=cmd_record)

    # ``lore record supersede PREDECESSOR --by SUCCESSOR``.
    p_record_supersede = p_record_sub.add_parser(
        "supersede",
        help="Record that one record supersedes another, in one step",
        usage="lore record supersede PREDECESSOR --by SUCCESSOR [--vault NAME]",
        description="Record a supersession: adds PREDECESSOR to SUCCESSOR's "
                    "supersedes list, then sets PREDECESSOR's status to "
                    "superseded. The edge lives on the successor (supersedes); "
                    "superseded-by on the predecessor is derived at reindex and "
                    "the edge is kind/name only, so the two may sit in different "
                    "vaults. An adr, spec or decision may only be superseded by a "
                    "record in a frozen status (an active adr, a ready spec — "
                    "not a draft); a task takes any live successor. Re-running a "
                    "finished pair changes nothing, and a predecessor that is "
                    "already superseded gains the extra successor's edge, which "
                    "is how a split names each child. Neither body changes. If "
                    "the second write fails, the exit is nonzero and the exact "
                    "command to finish it is printed.",
        epilog="To undo a supersession: lore record update PREDECESSOR --status "
               "<prior status>, then lore record update SUCCESSOR "
               "--unset-supersedes PREDECESSOR.",
    )
    p_record_supersede.add_argument(
        "record_id", metavar="PREDECESSOR",
        help="The record being superseded (<kind>/<name>).",
    )
    p_record_supersede.add_argument(
        "--by", required=True, metavar="SUCCESSOR",
        help="The record that replaces it (<kind>/<name>).",
    )
    p_record_supersede.add_argument(
        "--vault", dest="vault", default=None, metavar="NAME",
        help="Look for both records only in this configured vault, instead of "
             "scanning every vault in config order.",
    )
    p_record_supersede.set_defaults(func=cmd_record)

    # ``lore record amend RECORD_ID --reason R --initiated-by operator|agent``.
    # --reason and --initiated-by are validated by the handler (not argparse's
    # ``required``) so a missing or invalid value gets one message naming the fix.
    p_record_amend = p_record_sub.add_parser(
        "amend",
        help="Amend a record's body in place (even a frozen record), recording why",
        usage="lore record amend RECORD_ID --reason REASON --initiated-by "
              "{operator,agent} [--base REVISION] [--diff | --revert-to N] "
              "[--reopen ACn] [--close ACn] [--vault NAME]",
        description="Replace a record's body with the one piped on stdin (or, with "
                    "--diff, apply a unified diff to it) and append a "
                    "change-history entry: when, by whom, who asked, why, and the "
                    "diff. This is the way to correct a frozen record's body in "
                    "place; plain 'record update' refuses it. The record's id, "
                    "kind, status, labels and links are kept.",
        epilog="--base: the stale-write handshake. 'lore record show' gives the "
               "body's revision (SHA-256 of the stored body: 'revision' in "
               "--json, 'revision: <hex>' on stderr). A full-body amend must "
               "pass it as --base, and is refused, writing nothing, if the "
               "record's body changed after you read it (the message names who "
               "changed it and when; re-read, reapply, amend again). --diff "
               "may omit --base because a diff whose context still matches "
               "overwrites nobody's change, and a concurrent edit is kept only "
               "when it overlaps no hunk and shifts no lines above one; with "
               "--base it is refused when stale. A label or "
               "status change does not alter the revision. "
               "--initiated-by: 'operator' means a human instructed this edit in "
               "the current session; 'agent' means the agent proposed it. The "
               "value is recorded as stated, not verified: initiated-by and by "
               "(the unauthenticated git committer identity or LORE_EMAIL) are "
               "audit labels, not permission checks. The reason and the "
               "diff are permanent: amend never scrubs text, because the removed "
               "body stays in the diff and in vault git history. A record "
               "carrying change-history needs a lore new enough to know the "
               "field: an older lore refuses to write it (unsupported key "
               "'change-history'); pull trailhead to fix that. "
               "Spec criteria keep their numbers: an amend may remove criteria, "
               "edit them in place, and add the next unused number, and is "
               "refused (nothing written) if it skips a number, reuses a "
               "removed one or relabels a criterion; --revert-to skips those "
               "checks. Removals are recorded and printed as 'removed "
               "criteria: ...'. Limits: renumber detection matches criterion "
               "text, so a relabel that also rewords escapes it; a criterion "
               "removed outside amend (e.g. in a draft) is not recorded, so "
               "its number is not protected; an older lore refuses writes to a "
               "spec once an entry carries 'criteria' (reads still work), and "
               "only updating lore recovers it. " + _AMEND_MARKS_HELP,
    )
    p_record_amend.add_argument(
        "record_id",
        metavar="RECORD_ID",
        help="The vault-relative record ID to amend (<kind>/<name>).",
    )
    p_record_amend.add_argument(
        "--reason", default=None,
        help="One line saying why the body is being amended (kept permanently).",
    )
    p_record_amend.add_argument(
        "--initiated-by", dest="initiated_by", default=None,
        metavar="{operator,agent}",
        help="Who asked for the edit: 'operator' (a human instructed it in the "
             "current session) or 'agent' (the agent proposed it). Recorded as "
             "stated, not verified.",
    )
    p_record_amend.add_argument(
        "--diff", action="store_true",
        help="Treat piped stdin as a unified diff against the stored body "
             "instead of the full new body.",
    )
    p_record_amend.add_argument(
        "--base", dest="base", default=None, metavar="REVISION",
        help="The body revision you composed against (from 'lore record show': "
             "'revision' in --json, 'revision: <hex>' on stderr). Required with a "
             "full-body stdin; optional with --diff. The amend is refused, "
             "writing nothing, if the stored body's revision differs.",
    )
    p_record_amend.add_argument(
        "--reopen", dest="reopen", action="append", default=None, metavar="ACn",
        help="Reopen spec criterion ACn, voiding the coverage recorded before "
             "this amend. Repeatable; spec records only; not with --revert-to.",
    )
    p_record_amend.add_argument(
        "--close", dest="close", action="append", default=None, metavar="ACn",
        help="Close spec criterion ACn without a slice covering it (also undoes "
             "a mistaken reopen). Repeatable; refused if ACn is already closed "
             "or no slice has covered it; spec records only; not with --revert-to.",
    )
    p_record_amend.add_argument(
        "--revert-to", dest="revert_to", default=None, metavar="N",
        help="Revert to the body before change-history entry N (1 = oldest; see "
             "'lore record history ... --body-before N' to preview it). Lore "
             "computes the body from the stored body and history, and writes it "
             "as an ordinary amend with its own history entry. Needs --base; stdin "
             "must be empty and --diff is refused. A change made outside amend "
             "is kept when it overlaps no amend hunk and shifts no lines above "
             "one; otherwise that entry refuses, with the git command to inspect "
             "it. A body whose CRLF line endings were written outside lore is "
             "reconstructed with LF line endings.",
    )
    p_record_amend.add_argument(
        "--vault", dest="vault", default=None, metavar="NAME",
        help="Look for the record only in this configured vault, instead of "
             "scanning every vault in config order.",
    )
    p_record_amend.set_defaults(func=cmd_record)

    # ``lore record rename RECORD_ID --title "<new title>"``.
    p_record_rename = p_record_sub.add_parser(
        "rename",
        help="Rename a record's stem + title and rewrite inbound references",
    )
    p_record_rename.add_argument(
        "record_id",
        metavar="RECORD_ID",
        help="The vault-relative record ID to rename (<kind>/<name>)",
    )
    p_record_rename.add_argument(
        "--title", required=True,
        help="The record's new title; the new stem is derived from it "
             "(kebab-case, with a -2/-3 suffix on collision).",
    )
    p_record_rename.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=False,
        help="Print the would-be new RECORD_ID and the full would-be rewrite "
             "list without writing anything.",
    )
    p_record_rename.add_argument(
        "--vault", dest="vault", default=None, metavar="NAME",
        help="Name the configured vault holding the record to rename (use when "
             "the same stem exists in more than one vault). The inbound-reference "
             "sweep still covers every configured vault.",
    )
    p_record_rename.add_argument(
        "--include-shared", dest="include_shared", action="store_true", default=False,
        help="Also rewrite inbound references inside 'shared: true' vaults. "
             "By default those are skipped and only reported.",
    )
    p_record_rename.set_defaults(func=cmd_record)

    # ``lore record delete RECORD_ID``.
    p_record_delete = p_record_sub.add_parser(
        "delete", help="Delete a vault record (body + sidecar + index row)"
    )
    p_record_delete.add_argument(
        "record_id",
        metavar="RECORD_ID",
        help="The vault-relative record ID to delete (<kind>/<name>)",
    )
    p_record_delete.add_argument(
        "--vault", dest="vault", default=None, metavar="NAME",
        help="Delete the record from exactly this configured vault by name, "
             "instead of scanning every vault in config order.",
    )
    # Routing flags (symmetric with create/update): when a config exists they
    # select which vault the record lives in (a record routed to a scoped vault
    # on create is reached for delete with the same flags). Suppressed from help
    # like update's.
    p_record_delete.add_argument("--repo", default=None, help=argparse.SUPPRESS)
    p_record_delete.add_argument("--product", default=None, help=argparse.SUPPRESS)
    p_record_delete.add_argument("--suite", default=None, help=argparse.SUPPRESS)
    p_record_delete.add_argument("--team", default=None, help=argparse.SUPPRESS)
    p_record_delete.add_argument(
        "--force", dest="force", action="store_true", default=False,
        help="Delete even while other records still reference this one, leaving "
             "those references dangling. Without it a delete is refused and the "
             "referring records are listed.",
    )
    p_record_delete.set_defaults(func=cmd_record)

    p_record_history = p_record_sub.add_parser(
        "history",
        help="Print a record's change history, newest first",
        description="Print the change-history 'record amend' appends to a record, "
                    "newest first: when, by whom, who asked, why, and the diff.",
    )
    p_record_history.add_argument(
        "record_id",
        metavar="RECORD_ID",
        help="The record ID to read (<kind>/<name>)",
    )
    p_record_history.add_argument(
        "--vault", dest="vault", default=None, metavar="NAME",
        help="Read the record from exactly this configured vault by name, "
             "instead of scanning every vault in config order.",
    )
    p_record_history.add_argument(
        "--body-before", dest="body_before", default=None, metavar="N",
        help="Print, read-only, the body as it stood before change-history entry "
             "N (entries are numbered 1 = oldest, as the listing shows) instead of "
             "the listing: entries newest to N are reverse-applied to the current "
             "body. N=1 is the body before any amend. A change made outside amend "
             "is kept in the output when it overlaps no amend hunk and shifts no "
             "lines above one; otherwise that entry refuses, with the git command "
             "to inspect it. A body whose CRLF line endings were written outside "
             "lore is reconstructed with LF line endings. The "
             "preview is never written back; to revert, use 'lore record amend "
             "--revert-to N'.",
    )
    p_record_history.add_argument("--repo", default=None, help=argparse.SUPPRESS)
    p_record_history.add_argument("--product", default=None, help=argparse.SUPPRESS)
    p_record_history.add_argument("--suite", default=None, help=argparse.SUPPRESS)
    p_record_history.add_argument("--team", default=None, help=argparse.SUPPRESS)
    p_record_history.set_defaults(func=cmd_record)

    p_record_criteria = p_record_sub.add_parser(
        "criteria",
        help="Print a spec's declared and removed acceptance criteria as JSON",
        description="Print one JSON object, {\"record\", \"declared\", \"removed\"}: "
                    "the criterion identifiers a spec's body declares (document "
                    "order) and the ones its change-history recorded as removed "
                    "(numeric order). Two more keys appear only when non-empty: "
                    "\"reopened\" ({id: [task ids whose coverage the reopen "
                    "voided]}) and \"closed\" ([ids] closed without a slice). "
                    "Read-only.",
    )
    p_record_criteria.add_argument(
        "record_id",
        metavar="RECORD_ID",
        help="The spec ID to read (spec/<name>)",
    )
    p_record_criteria.add_argument(
        "--vault", dest="vault", default=None, metavar="NAME",
        help="Read the record from exactly this configured vault by name, "
             "instead of scanning every vault in config order.",
    )
    p_record_criteria.add_argument("--repo", default=None, help=argparse.SUPPRESS)
    p_record_criteria.add_argument("--product", default=None, help=argparse.SUPPRESS)
    p_record_criteria.add_argument("--suite", default=None, help=argparse.SUPPRESS)
    p_record_criteria.add_argument("--team", default=None, help=argparse.SUPPRESS)
    p_record_criteria.set_defaults(func=cmd_record)

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
