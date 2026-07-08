"""``lore record`` — create / update / delete / show vault records."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .common import _load_vault_config, _read_stdin_body, _resolve_groups_dir


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
    """Resolve the vault root that ``record delete`` should act on.

    Symmetric with ``record create``: when a ``config.json`` exists, the
    target vault is chosen by the SAME config resolution — the record's ``kind``
    (from ``record_id``) plus the command's routing flags (``--repo/--product/
    --suite/--team``) — so delete operates on the very vault create routed the
    record to (a no-scope record lands in, and is deleted from, the ``default``
    vault). When **no** config exists, fall back to the config-resolved active
    vault (``vault_config.resolve_active_vault()`` → the floor) — vanilla usage
    is unchanged (Axiom 3).

    NOTE: ``record update`` no longer uses this resolver. Its scope flags
    express the *destination*, not the current location, so update locates the record via
    ``_find_current_record_location`` (a config scan independent of the flag
    values) and re-resolves the destination via ``_resolve_destination_root``.

    NOTE: this reconciles the create-vs-update/delete location split that the
    config-routing work introduced. Vault resolution is now config-only across
    *every* lore command (and the hooks).
    """
    from ..vault import config as vault_config_mod
    from ..vault import resolve as vault_resolve_mod

    loaded = _load_vault_config()
    if loaded is None:
        return str(vault_config_mod.resolve_active_vault())
    _, vaults = loaded
    kind = record_id.split("/", 1)[0]
    participating_scopes = {
        flag: getattr(args, flag)
        for flag in ("repo", "product", "suite", "team")
        if getattr(args, flag, None)
    }
    chosen = vault_resolve_mod.resolve_vault(participating_scopes, kind, vaults)
    return str(chosen.path)


# Dedicated non-scope list fields: each maps a repeatable ``--<flag>`` /
# ``--unset-<flag> VALUE`` pair to its sidecar key. Scalars (--status, --title)
# and the --related map flag are handled inline by the applier. Scope fields
# (team/suite/product/repo) are NOT here — they remain routing flags handled by
# the record-write layer.
_LIST_FIELD_FLAGS: dict[str, str] = {
    "keyword": "keywords",
    "related_file": "related-files-or-folders",
    "related_url": "related-urls",
    "related_phase": "related-phases",
    "depends_on": "depends-on",
}


def _apply_record_fields(
    sidecar: dict,
    args,
) -> tuple[dict, list[str]]:
    """Apply the dedicated per-field flags to *sidecar*; return (updated, errors).

    Structural mirror of :func:`_apply_map_labels_annotations` (upsert/unset on a
    copy, errors surfaced to the caller), but with heterogeneous type dispatch
    of the per-field flags:

      - scalars: ``--status`` (always), ``--title`` (update-only setter; create
        builds it from the required positional) overwrite the scalar key.
      - repeatable list flags (``--keyword`` / ``--related-file`` /
        ``--related-url`` / ``--related-phase``) **append** to their list key;
        each ``--unset-<field> VALUE`` removes one matching item (a value not
        present is a tolerated no-op).
      - ``--related <kind>=<name>`` (repeatable) splits on the FIRST ``=`` and
        appends ``name`` to ``related[kind]``. An empty kind (``=foo``) or empty
        name (``task=``) is rejected HERE, before ``validate()`` ever sees it.

    All mutations still flow through ``validate()`` downstream: off-vocab
    ``--status`` and bad ``related`` kinds are caught there. This helper only
    guards the degenerate ``--related`` split that ``validate()`` could not name.

    Returns a mutated copy and a list of error strings (non-empty → nothing
    should be written).
    """
    errors: list[str] = []
    result = dict(sidecar)

    # --- scalars -----------------------------------------------------------
    status = getattr(args, "status", None)
    if status is not None:
        result["status"] = status
    # ``--title`` is an optional setter on update; on create it is the required
    # positional and already seeded into the sidecar before this runs.
    title = getattr(args, "title", None)
    if title is not None:
        result["title"] = title
    # ``--parent`` is the task graph's containment edge (a scalar). Set overwrites
    # the key; ``--unset-parent`` clears it. Both are ``task``-gated downstream by
    # ``validate()`` (present on a non-task kind → rejected there).
    parent = getattr(args, "parent", None)
    if parent is not None:
        result["parent"] = parent
    if getattr(args, "unset_parent", False):
        result.pop("parent", None)

    # --- repeatable list flags (append) ------------------------------------
    for dest, key in _LIST_FIELD_FLAGS.items():
        values = getattr(args, dest, None) or []
        if values:
            current = result.get(key, [])
            if not isinstance(current, list):
                current = []
            result[key] = current + list(values)

    # --- repeatable list-flag removals (remove one matching item) ----------
    for dest, key in _LIST_FIELD_FLAGS.items():
        removals = getattr(args, f"unset_{dest}", None) or []
        for value in removals:
            current = result.get(key, [])
            if isinstance(current, list) and value in current:
                result[key] = [v for v in current if v != value]

    # --- --related <kind>=<name> map flag (append name to that kind) -------
    related_pairs = getattr(args, "related_pairs", None) or []
    if related_pairs:
        related: dict = dict(result.get("related") or {})
        for pair in related_pairs:
            kind, sep, name = pair.partition("=")
            if not sep or not kind or not name:
                errors.append(
                    f"error: --related {pair!r} must be KIND=NAME with a "
                    f"non-empty kind and name"
                )
                continue
            related[kind] = list(related.get(kind, [])) + [name]
        if related:
            result["related"] = related

    return result, errors


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


def _apply_map_labels_annotations(
    sidecar: dict,
    label_pairs: list[str],
    annotation_pairs: list[str],
    unset_labels: list[str],
    unset_annotations: list[str],
) -> dict:
    """Apply --label/--annotation/--unset-label/--unset-annotation to *sidecar*.

    Map-field branch — intentionally separate from ``_apply_record_fields``'s
    scalar/list logic.

    Semantics:
      - set = upsert: ``--label k=v`` overwrites an existing key silently.
        Split is on the FIRST ``=`` only, so ``k=a=b`` stores value ``a=b``.
      - unset = remove one key from the map; when the map becomes empty the
        whole field is dropped (omit-when-empty — no ``{}`` left behind).
      - ``--unset-label`` on an absent key is a documented silent no-op (exit 0).

    This function mutates a copy and returns it; the caller passes the result
    through ``validate_and_write`` so bad keys are rejected there with a
    non-zero exit naming the offender.
    """
    result = dict(sidecar)

    def _upsert(field: str, pairs: list[str]) -> None:
        if not pairs:
            return
        current: dict = dict(result.get(field) or {})
        for pair in pairs:
            key, _, value = pair.partition("=")
            current[key] = value
        result[field] = current

    def _unset(field: str, keys: list[str]) -> None:
        if not keys:
            return
        current: dict = dict(result.get(field) or {})
        for key in keys:
            current.pop(key, None)
        if current:
            result[field] = current
        else:
            result.pop(field, None)

    _upsert("labels", label_pairs)
    _upsert("annotations", annotation_pairs)
    _unset("labels", unset_labels)
    _unset("annotations", unset_annotations)

    return result


# ---------------------------------------------------------------------------
# Task graph guards (task-only; a no-op for every other kind)
# ---------------------------------------------------------------------------

# A ``## Flow-out`` markdown heading (the completion-ritual section). Matched
# loosely — any heading level ≥ 2, case-insensitive — so the reminder fires only
# when the parent body genuinely lacks the knowledge-flow-out checklist.
_FLOW_OUT_RE = re.compile(r"(?im)^\s*#{2,}\s+flow-out\b")


def _body_has_flow_out(body: str) -> bool:
    """True iff *body* contains a ``## Flow-out`` section heading."""
    return bool(_FLOW_OUT_RE.search(body or ""))


def _load_task_sidecars(vault_root: str) -> dict[str, dict]:
    """Read every task sidecar under ``<vault_root>/task/`` → ``{name: sidecar}``.

    The source-of-truth read for the graph guards — sidecars, never the index.
    A malformed or unreadable sidecar is skipped (best-effort: the guard degrades
    to not seeing that node rather than failing the whole write).
    """
    task_dir = Path(vault_root) / "task"
    graph: dict[str, dict] = {}
    if not task_dir.is_dir():
        return graph
    for sidecar_path in task_dir.glob("*.json"):
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            graph[sidecar_path.stem] = data
    return graph


def _confine_edge_reference(value: str, vault_root: str) -> str | None:
    """Return a guard-error string if *value* is an unsafe task reference, else None.

    ``--parent``/``--depends-on`` values are record names, so they flow through the
    SAME name-resolution/confinement guard every RECORD_ID-bearing op uses
    (:func:`record_store._confine_record_id`): a ``..`` segment, absolute
    component, NUL byte, or empty/degenerate segment is rejected before the value
    is ever written. Existence is deliberately NOT checked — referential integrity
    is not enforced (a dangling edge is valid, per the record model's shape-only
    contract).
    """
    from ..record import graph as graph_mod
    from ..record import store as record_store_mod

    if not value:
        return graph_mod.format_guard_message("edge-reference", "empty task reference")
    try:
        record_store_mod._confine_record_id(f"task/{value}", vault_root)
    except record_store_mod.InvalidRecordIdError as exc:
        return graph_mod.format_guard_message(
            "edge-reference", f"unsafe task reference {value!r}: {exc}"
        )
    return None


def _evaluate_task_guards(
    *,
    kind: str,
    name: str,
    sidecar: dict,
    body: str,
    vault_root: str,
    status_set: str | None,
    deleting: bool = False,
) -> tuple[list[str], list[str]]:
    """Evaluate the task graph guards for a create/update/delete.

    Returns ``(errors, notices)``:

      - ``errors`` block the operation (nothing is written): a ``depends-on``
        cycle, a ``parent`` ancestor loop, an unsafe edge reference, or a
        parent-completion violation (``--status done`` with non-terminal
        children). Each is a machine-parseable ``graph-guard [...]`` stderr line.
      - ``notices`` are non-blocking, printed only on a successful op: the
        dependent-warning (a depended-on task going ``dropped``/``superseded`` or
        being deleted) and the flow-out reminder (a parent completed without a
        ``## Flow-out`` section).

    A no-op — ``([], [])`` — for every non-``task`` kind, so no other kind is
    touched by any of these guards.
    """
    from ..record import graph as graph_mod

    if kind != "task":
        return [], []

    # Delete only warns about dependents; it is never blocked.
    if deleting:
        graph = _load_task_sidecars(vault_root)
        deps = graph_mod.dependents(graph, name)
        notices: list[str] = []
        if deps:
            notices.append(
                graph_mod.format_guard_message(
                    "dependents",
                    f"task {name!r} deleted but still depended on",
                    offenders=deps,
                )
            )
        return [], notices

    errors: list[str] = []

    # Edge references are confined before the graph is built — a malformed value
    # must never be written, and a traversal-shaped name must never reach disk.
    references: list[str] = []
    parent = sidecar.get("parent")
    if isinstance(parent, str):
        references.append(parent)
    deps_field = sidecar.get("depends-on")
    if isinstance(deps_field, list):
        references.extend(d for d in deps_field if isinstance(d, str))
    for ref in references:
        msg = _confine_edge_reference(ref, vault_root)
        if msg:
            errors.append(msg)
    if errors:
        return errors, []

    # Overlay the in-flight record onto the on-disk task graph.
    graph = _load_task_sidecars(vault_root)
    graph[name] = sidecar

    cycle = graph_mod.find_dependency_cycle(graph, start=name)
    if cycle:
        errors.append(
            graph_mod.format_guard_message(
                "depends-on-cycle",
                f"task {name!r} would create a dependency cycle: " + " -> ".join(cycle),
            )
        )
    loop = graph_mod.find_ancestor_loop(graph, name)
    if loop:
        errors.append(
            graph_mod.format_guard_message(
                "parent-loop",
                f"task {name!r} would create a parent ancestor loop: " + " -> ".join(loop),
            )
        )

    if status_set == "done":
        open_children = graph_mod.non_terminal_children(graph, name)
        if open_children:
            errors.append(
                graph_mod.format_guard_message(
                    "parent-completion",
                    f"cannot set task {name!r} to done — non-terminal children remain",
                    offenders=open_children,
                )
            )

    if errors:
        return errors, []

    notices = []
    if status_set in ("dropped", "superseded"):
        deps = graph_mod.dependents(graph, name)
        if deps:
            notices.append(
                graph_mod.format_guard_message(
                    "dependents",
                    f"task {name!r} set to {status_set} but still depended on",
                    offenders=deps,
                )
            )
    if status_set == "done" and not _body_has_flow_out(body):
        notices.append(
            graph_mod.format_guard_message(
                "flow-out",
                f"task {name!r} completed without a '## Flow-out' section — "
                f"capture the knowledge flow-out",
            )
        )
    return errors, notices


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


def _render_record(record_id: str, vault_root: str, as_json: bool) -> int:
    """Locate ``record_id`` in ``vault_root`` and print it; shared by the readers.

    Plain: writes the body (``.md``) to stdout. ``--json``: emits
    ``{record_id, kind, name, sidecar, body}`` — the ``sidecar`` dict is how
    callers read the un-indexed annotations (e.g. flush's ``flushed-at``
    watermark, which is sidecar-only and never lands in the index). A nonexistent
    or malformed record → non-zero + stderr. Backs both ``lore record show``
    (caller-supplied ``<kind>/<name>``) and ``lore session show`` (the resolved
    session record id), so the output shape is identical for both.
    """
    from ..record import store as record_store_mod

    try:
        loc = record_store_mod.locate_record(record_id, vault_root=vault_root)
    except (
        record_store_mod.RecordNotFoundError,
        record_store_mod.InvalidRecordIdError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
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
    """``lore record show <kind>/<name> [--json]`` — the canonical record reader.

    The CLI-only way to read a record so agents and skills never poke at vault
    files directly. RECORD_ID must be ``<kind>/<name>``; a malformed ID or a
    nonexistent record → non-zero + stderr. To read THIS worktree's live session
    record (resolved by session-id / worktree, not a fixed name), use the
    dedicated ``lore session show``.
    """
    record_id = getattr(args, "record_id", None)
    as_json = bool(getattr(args, "json", False))
    if not record_id or "/" not in record_id:
        print(
            f"error: invalid RECORD_ID {record_id!r}; expected '<kind>/<name>'",
            file=sys.stderr,
        )
        return 1
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
    from ..record import store as record_store_mod
    from ..search import index as index_store_mod

    record_id = getattr(args, "record_id", None)
    if not record_id or "/" not in record_id:
        print(
            f"error: invalid RECORD_ID {record_id!r}; expected '<kind>/<name>'",
            file=sys.stderr,
        )
        return 1

    # Resolve the target vault via config (symmetric with `record create`) when a
    # config exists, else the active vault (vanilla). A record whose vault was
    # removed from config resolves to the default floor and surfaces a clean
    # RecordNotFoundError below rather than acting on an orphaned target.
    vault_root = _resolve_record_op_vault(record_id, args)

    # Dependent-warning: deleting a task that others depend-on is allowed (delete
    # is never blocked) but warns, listing the dependents. Computed before the
    # delete off the on-disk task graph; a no-op for every non-task kind.
    kind, _, name = record_id.partition("/")
    _, guard_notices = _evaluate_task_guards(
        kind=kind,
        name=name,
        sidecar={},
        body="",
        vault_root=vault_root,
        status_set=None,
        deleting=True,
    )

    try:
        conn = index_store_mod.open_index()
        try:
            record_store_mod.delete_record(record_id, conn, vault_root=vault_root)
            conn.commit()
        finally:
            conn.close()
    except (
        record_store_mod.RecordNotFoundError,
        record_store_mod.InvalidRecordIdError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: record delete failed: {exc}", file=sys.stderr)
        return 1

    for msg in guard_notices:
        print(msg, file=sys.stderr)

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
    applied by :func:`_apply_record_fields`.
    """
    from ..record import store as record_store_mod
    from ..search import index as index_store_mod
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
    sidecar, field_errors = _apply_record_fields(sidecar, args)
    if field_errors:
        for msg in field_errors:
            print(msg, file=sys.stderr)
        return 1

    # Apply --label / --annotation / --unset-label / --unset-annotation.
    sidecar = _apply_map_labels_annotations(
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
    for flag in ("repo", "product", "suite", "team"):
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
            for flag in ("repo", "product", "suite", "team")
            if flag in participating_scopes
        )
        or None
    )

    guard_notices: list[str] = []
    try:
        conn = index_store_mod.open_index()
        try:
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
            guard_errors, guard_notices = _evaluate_task_guards(
                kind=kind,
                name=location.name,
                sidecar=sidecar,
                body=body,
                vault_root=str(vault_root),
                status_set=getattr(args, "status", None),
            )
            if guard_errors:
                for msg in guard_errors:
                    print(msg, file=sys.stderr)
                return 1
            record_id = record_store_mod.validate_and_write(
                location=location,
                sidecar=sidecar,
                body=body,
                conn=conn,
                shared=shared_flag,
            )
            conn.commit()
        finally:
            conn.close()
    except record_store_mod.RecordValidationError as exc:
        for msg in exc.errors:
            print(f"error: {msg}", file=sys.stderr)
        return 1
    except record_store_mod.ProvenanceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: record create failed: {exc}", file=sys.stderr)
        return 1

    # Routing confirmation goes to STDERR so the RECORD_ID stays
    # the sole parseable stdout line (the create contract — test_record_cli_create).
    if routing_line is not None:
        print(routing_line, file=sys.stderr)
    # Non-blocking graph notices (dependent-warning, flow-out reminder) — stderr,
    # so stdout stays the sole parseable RECORD_ID line.
    for msg in guard_notices:
        print(msg, file=sys.stderr)

    # Print the vault-relative RECORD_ID on stdout.
    print(record_id)
    return 0


def _find_current_record_location(record_id: str):
    """Locate the record's CURRENT vault, independent of the new scope-flag values.

    The scope flags (``--team`` etc.) now mean **destination**, so the
    current location can no longer be derived from them (deriving it from them
    would produce a spurious ``RecordNotFoundError``). Strategy: scan every
    configured vault path with :func:`record_store.locate_record` until the record
    is found — config-driven, index-independent, and with no circular dependence on
    the sidecar's own scope fields. Falls back to the active vault when no config
    exists (vanilla, Axiom 3).

    Raises :class:`record_store.RecordNotFoundError` when no configured vault holds
    the record.
    """
    from ..record import store as record_store_mod
    from ..vault import config as vault_config_mod

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
        for flag in ("repo", "product", "suite", "team")
        if merged_sidecar.get(flag)
    }
    resolution = vault_resolve_mod.explain_resolution(participating_scopes, kind, vaults)
    return str(resolution.chosen.path), vault_config_mod.shared_flag(resolution.chosen)


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

    **Scope flags drive automatic relocation.**
    ``--team/--suite/--product/--repo`` are field-setters that also re-resolve the
    destination vault from the **merged scope** (existing sidecar scope fields
    overlaid with the new flag values). The flow: (1) locate the record's CURRENT
    vault via a config scan (decoupled from the now-destination-meaning scope flags
    — :func:`_find_current_record_location`); (2) apply ALL field mutations in
    memory; (3) re-resolve the destination via the create-side resolver
    (:func:`_resolve_destination_root`); (4) validate + stamp + neutralize the
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

    ``updated-at``/``updated-by`` are re-stamped every update; ``created-*`` are
    preserved. The relocation is automatic; there is no manual ``--move-to`` flag —
    removing it also closed an unconfined-destination write path (the dest is now
    only ever a config-declared vault root, and ``move_record`` confines it too).

    Invalid/nonexistent RECORD_ID → non-zero.
    """
    import json

    from ..record import store as record_store_mod
    from ..search import index as index_store_mod

    record_id = getattr(args, "record_id", None)
    if not record_id or "/" not in record_id:
        print(
            f"error: invalid RECORD_ID {record_id!r}; expected '<kind>/<name>'",
            file=sys.stderr,
        )
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
    try:
        conn = index_store_mod.open_index()
        try:
            # (1) Resolve the CURRENT location, decoupled from the scope flags
            # (which now mean destination); a missing record → RecordNotFoundError.
            location = _find_current_record_location(record_id)

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

            # --- resolve the new body -----------------------------------------
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
                    return 1
                except record_store_mod.DiffFormatError as exc:
                    print(f"error: unparseable diff: {exc}", file=sys.stderr)
                    return 1
            elif has_stdin:
                # Full-body replace.
                new_body = stdin_text
            else:
                # Metadata-only: body unchanged. Emit the notice to
                # stderr — exit stays 0.
                new_body = existing_body
                print(
                    "note: no stdin — body unchanged, metadata-only update",
                    file=sys.stderr,
                )

            # (2) --- apply ALL field mutations to the sidecar IN MEMORY --------
            # Scope flags (--team etc.) become field-setters that drive the merged
            # scope; the non-scope per-field flags (--status / --title / --keyword
            # / --related-*) reuse the shared applier. --title is optional here.
            sidecar = dict(existing_sidecar)
            for flag in ("repo", "product", "suite", "team"):
                val = getattr(args, flag, None)
                if val:
                    sidecar[flag] = val

            sidecar, field_errors = _apply_record_fields(sidecar, args)
            if field_errors:
                for msg in field_errors:
                    print(msg, file=sys.stderr)
                return 1

            # Apply --label / --annotation / --unset-label / --unset-annotation.
            sidecar = _apply_map_labels_annotations(
                sidecar,
                label_pairs=list(getattr(args, "label_pairs", None) or []),
                annotation_pairs=list(getattr(args, "annotation_pairs", None) or []),
                unset_labels=list(getattr(args, "unset_labels", None) or []),
                unset_annotations=list(getattr(args, "unset_annotations", None) or []),
            )

            # Task graph guards run against the record's CURRENT vault (where its
            # parent/depends-on relatives live), with the mutated record overlaid.
            # Blocking errors → nothing written; notices are held for the success
            # path. A no-op for every non-task kind.
            guard_errors, guard_notices = _evaluate_task_guards(
                kind=location.kind,
                name=location.name,
                sidecar=sidecar,
                body=new_body,
                vault_root=location.vault_root,
                status_set=getattr(args, "status", None),
            )
            if guard_errors:
                for msg in guard_errors:
                    print(msg, file=sys.stderr)
                return 1

            # (3) re-resolve the DESTINATION (root + shared trust) from the merged scope.
            dest_root, dest_shared = _resolve_destination_root(sidecar, location.kind)
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
            else:
                # (4b) validate + stamp + neutralize the mutated record IN MEMORY,
                # then write it ONCE at the destination via move_record overrides —
                # the mutated sidecar is NEVER written at the old location and then
                # moved (single durable write at destination).
                stamped, safe_body = record_store_mod.validate_stamp_neutralize(
                    location, sidecar, new_body
                )
                dest_location = record_store_mod.RecordLocation(
                    vault_root=dest_root,
                    kind=location.kind,
                    name=location.name,
                    record_id=location.record_id,  # ID is vault-root-agnostic
                    body_path=Path(dest_root) / location.kind / f"{location.name}.md",
                    sidecar_path=Path(dest_root) / location.kind / f"{location.name}.json",
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
                moved_line = f"moved: {location.record_id} → {new_id}"

            conn.commit()
        finally:
            conn.close()
    except (
        record_store_mod.RecordNotFoundError,
        record_store_mod.InvalidRecordIdError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except record_store_mod.RecordValidationError as exc:
        for msg in exc.errors:
            print(f"error: {msg}", file=sys.stderr)
        return 1
    except record_store_mod.ProvenanceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: record update failed: {exc}", file=sys.stderr)
        return 1

    # Non-blocking graph notices (dependent-warning, flow-out reminder) — stderr,
    # so the stdout RECORD_ID/moved contract is unchanged.
    for msg in guard_notices:
        print(msg, file=sys.stderr)

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

    p_record_create = p_record_sub.add_parser("create", help="Create a new vault record")
    p_record_create.add_argument(
        "--kind", required=True,
        help="Record kind (one of: area, blob, collaboration, decision, lesson, "
             "session, spec, task)",
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
    p_record_create.add_argument(
        "--label", dest="label_pairs", action="append", default=[],
        metavar="KEY=VALUE",
        help="Set a label (repeatable, upsert). Split on first '=' so "
             "'namespace/name=value' works unescaped.",
    )
    p_record_create.add_argument(
        "--annotation", dest="annotation_pairs", action="append", default=[],
        metavar="KEY=VALUE",
        help="Set an annotation (repeatable, upsert). Split on first '='.",
    )
    p_record_create.add_argument(
        "--unset-label", dest="unset_labels", action="append", default=[],
        metavar="KEY",
        help="Remove a label key (repeatable). Absent key is a silent no-op.",
    )
    p_record_create.add_argument(
        "--unset-annotation", dest="unset_annotations", action="append", default=[],
        metavar="KEY",
        help="Remove an annotation key (repeatable). Absent key is a silent no-op.",
    )
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
    p_record_update.add_argument(
        "--label", dest="label_pairs", action="append", default=[],
        metavar="KEY=VALUE",
        help="Set a label (repeatable, upsert). Split on first '=' so "
             "'namespace/name=value' works unescaped.",
    )
    p_record_update.add_argument(
        "--annotation", dest="annotation_pairs", action="append", default=[],
        metavar="KEY=VALUE",
        help="Set an annotation (repeatable, upsert). Split on first '='.",
    )
    p_record_update.add_argument(
        "--unset-label", dest="unset_labels", action="append", default=[],
        metavar="KEY",
        help="Remove a label key (repeatable). Absent key is a silent no-op.",
    )
    p_record_update.add_argument(
        "--unset-annotation", dest="unset_annotations", action="append", default=[],
        metavar="KEY",
        help="Remove an annotation key (repeatable). Absent key is a silent no-op.",
    )
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
    # Routing flags (symmetric with delete): select which vault a scoped record
    # lives in. Suppressed from help.
    p_record_show.add_argument("--repo", default=None, help=argparse.SUPPRESS)
    p_record_show.add_argument("--product", default=None, help=argparse.SUPPRESS)
    p_record_show.add_argument("--suite", default=None, help=argparse.SUPPRESS)
    p_record_show.add_argument("--team", default=None, help=argparse.SUPPRESS)
    p_record_show.set_defaults(func=cmd_record)
