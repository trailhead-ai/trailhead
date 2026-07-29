"""Canonical lore record model + pure sidecar validator.

This module is the single, machine-checkable definition of *what a lore record
is*: the closed set of 9 kinds, the per-record JSON sidecar field schema (`v1`),
the per-kind status vocabularies and their initial/default value, the phases
taxonomy, and a **pure** `validate(sidecar, kind)` function.

This is the single source of truth for record status vocabularies (canonical
module-level data + pure predicate/accessor functions). It superseded an earlier
pre-commit `status_validator.py` that has since been retired. Nothing here reads
files or touches the search index — the validator operates on an already-parsed
dict and is shared verbatim by the `lore record` CLI and the migration.

It does, however, depend on `search.kql` for one thing: the set of queryable
field names. `RESERVED_LABEL_KEYS` is derived from `KINDS | kql.VALID_FIELDS` at
import, so the names an operator can *query by* and the names an operator may not
*label with* cannot drift apart. This module's own import of `kql` is a plain,
eager, module-level import — but `kql` derives its `related-<kind>` facet fields
from `KINDS`, and does so with a LAZY import back into this module (inside a
function, never at `kql`'s own module load) precisely to avoid a genuine cycle:
this module imports `kql` before `KINDS` exists in some load orders, so an eager
reach-back from `kql` into this module would find `KINDS` undefined. See
`search/kql.py`'s "Kind-derived facet fields" module docstring section for the
full reasoning.

Invariants:
- The kind set is closed: exactly the 9 kinds in ``KINDS``; any other ``kind`` is
  rejected.
- Each record carries ``version: v1``; the schema is keyed by ``(kind, version)``,
  but in ``v1`` all kinds share one global field schema (``FIELDS_V1``).
- ``status`` is drawn from the kind's ordered vocab; the **first** element is the
  initial/default value applied when ``status`` is omitted on create.
- ``depends-on``/``parent`` are gated to ``task`` records only (``KIND_GATED_FIELDS``);
  present on any other kind, they are rejected naming both the field and the kind.
- The validator checks **shape, not referential integrity**: ``related`` keys must
  be valid kinds and values ``list[str]``, but referenced names are *not* verified
  to exist (a dangling ``{"task": ["nope"]}`` validates clean — existence is
  enforced nowhere; the index materializes whatever edges exist).
- A ``labels`` key may not shadow a first-class record concept: exact matches
  against ``RESERVED_LABEL_KEYS`` and any ``related-`` prefixed key are refused.
  The refusal is classified (``_reserved_key_alternative``) so it names the one
  alternative that stores what the key meant — an edge, the field's own flag, or
  a free attribute — and always names ``--unset-label`` for a record that already
  carries the key. Reservation is exact, so ``hm/area`` and ``craft/subsystems``
  stay legal. ``annotations`` are exempt. This binds every caller of
  ``validate()`` — i.e. every
  ``lore record create``/``update`` — and nothing else: ``search.index``'s
  ``upsert_row`` (reached from the session store and ``rebuild``) writes label
  rows without consulting this module.
- ``created-by``/``updated-by`` are plaintext provenance PII (e.g. a git email),
  git-tracked, exactly as the legacy YAML frontmatter already stored. They are
  **never** an authz/authn signal — self-asserted and spoofable. Data
  classification/retention for them is owned by the CLI (which writes them); this
  module only fixes the keys' shape.
"""
# NOTE: deliberately no ``from __future__ import annotations``. Under string
# annotations, the stdlib ``@dataclass`` machinery on 3.12+ looks the defining
# module up in ``sys.modules`` to resolve field annotations and crashes when it
# is absent. Evaluating annotations eagerly (no future import) sidesteps that —
# every annotation here is a valid runtime expression on 3.11+ and there are no
# forward references.

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

from ..search import kql

# --- Canonical declarative model --------------------------------------------

#: The closed set of 9 record kinds. Any other ``kind`` value is rejected.
#: ``task`` unifies the former ``backlog``/``plan`` kinds — a single kind for
#: anything worth tracking to completion, ordered via ``depends-on``/``parent``.
#: ``adr`` is an immutable architecture decision record (convention-enforced,
#: not CLI-enforced); it uses no ``task``-only gated field.
KINDS: frozenset[str] = frozenset(
    {
        "adr",
        "area",
        "blob",
        "collaboration",
        "decision",
        "lesson",
        "session",
        "spec",
        "task",
    }
)

#: Stable display order for the kind set, reused in error messages (computed once).
_SORTED_KINDS: list[str] = sorted(KINDS)

#: The sidecar-schema version stamped on every record. The schema is keyed by
#: ``(kind, version)``; only ``v1`` exists today (the versioned registry is
#: deferred until a ``v2`` exists).
VERSION: str = "v1"

#: Per-kind status vocabulary as an **ordered tuple** — the first element is the
#: kind's initial/default status (applied when ``status`` is omitted on create).
#: Ordered (not a ``frozenset``) so "first == initial" is well-defined.
STATUS_VOCAB: dict[str, tuple[str, ...]] = {
    "adr": ("draft", "active", "superseded", "dropped"),
    "area": ("active",),
    "blob": ("active",),
    "collaboration": ("active",),
    "decision": ("active", "superseded", "dropped"),
    "lesson": ("active", "conditional"),
    "session": ("dirty", "clean"),
    "spec": ("draft", "ready", "planned", "complete", "superseded", "dropped"),
    "task": (
        "open",
        "ready",
        "in-progress",
        "blocked",
        "done",
        "dropped",
        "superseded",
    ),
}

#: The closed, ordered phase taxonomy. ``related-phases`` is a subset of these;
#: an empty ``related-phases`` means the record applies to *all* phases.
PHASES: tuple[str, ...] = ("orient", "frame", "build", "review", "ship", "close")


@dataclass(frozen=True)
class FieldSpec:
    """Schema for a single sidecar key.

    ``required`` — the key must be present on a validated record, **except** when
    it is ``auto_set`` (filled in by the CLI on create/update, so the pure
    validator tolerates its absence) or defaulted (``status``/``version``).
    ``type_tag`` — one of ``str``/``list[str]``/``datetime``/``related-map``.
    ``auto_set`` — populated by the CLI, not operator-supplied.
    """

    required: bool
    type_tag: str
    auto_set: bool = False


# Type tags used by FIELDS_V1 / the validator.
_STR = "str"
_LIST_STR = "list[str]"
_DATETIME = "datetime"
_RELATED_MAP = "related-map"
_MAP_STR_STR = "map[str,str]"

#: Keys that are ``required`` in the field table but never operator-supplied:
#: ``status`` and ``version`` are defaulted by the validator when omitted.
_DEFAULTED: frozenset[str] = frozenset({"status", "version"})

#: The global ``v1`` sidecar field schema. In ``v1`` every kind shares this one
#: schema (the ``(kind, version)`` keying matters only once a ``v2`` exists).
#: Insertion order mirrors the spec's field table for readability.
FIELDS_V1: dict[str, FieldSpec] = {
    "version": FieldSpec(required=True, type_tag=_STR),
    "kind": FieldSpec(required=True, type_tag=_STR),
    "title": FieldSpec(required=True, type_tag=_STR),
    "keywords": FieldSpec(required=False, type_tag=_LIST_STR),
    "status": FieldSpec(required=True, type_tag=_STR),
    "team": FieldSpec(required=False, type_tag=_STR),
    "suite": FieldSpec(required=False, type_tag=_STR),
    "product": FieldSpec(required=False, type_tag=_STR),
    "repo": FieldSpec(required=False, type_tag=_STR),
    "created-at": FieldSpec(required=True, type_tag=_DATETIME, auto_set=True),
    "created-by": FieldSpec(required=True, type_tag=_STR, auto_set=True),
    "updated-at": FieldSpec(required=True, type_tag=_DATETIME, auto_set=True),
    "updated-by": FieldSpec(required=True, type_tag=_STR, auto_set=True),
    "last-referenced-at": FieldSpec(required=False, type_tag=_DATETIME),
    "related-files-or-folders": FieldSpec(required=False, type_tag=_LIST_STR),
    "related": FieldSpec(required=False, type_tag=_RELATED_MAP),
    "related-urls": FieldSpec(required=False, type_tag=_LIST_STR),
    "related-phases": FieldSpec(required=False, type_tag=_LIST_STR),
    "labels": FieldSpec(required=False, type_tag=_MAP_STR_STR),
    "annotations": FieldSpec(required=False, type_tag=_MAP_STR_STR),
    "depends-on": FieldSpec(required=False, type_tag=_LIST_STR),
    "parent": FieldSpec(required=False, type_tag=_STR),
}

#: Schema registry keyed by version (only ``v1`` exists today).
_SCHEMAS: dict[str, dict[str, FieldSpec]] = {"v1": FIELDS_V1}

#: Fields gated to a subset of kinds — present here means "the field is a valid
#: sidecar key everywhere, but only ``validate()``-clean on the listed kinds".
#: ``depends-on``/``parent`` are ``task``-only graph edges; naming a field here
#: is the entire mechanism for rejecting it on every other surviving kind
#: (naming both the field and the offending kind in the error). A field absent
#: from this table is ungated — valid on any kind, as before this table existed.
KIND_GATED_FIELDS: dict[str, frozenset[str]] = {
    "depends-on": frozenset({"task"}),
    "parent": frozenset({"task"}),
}

# Derived key sets are constant per version, so compute them once at import
# rather than rebuilding a frozenset on every accessor call (the migration may
# validate thousands of records).
_AUTO_SET_KEYS: dict[str, frozenset[str]] = {
    version: frozenset(k for k, spec in fields.items() if spec.auto_set)
    for version, fields in _SCHEMAS.items()
}
_REQUIRED_OPERATOR_KEYS: dict[str, frozenset[str]] = {
    version: frozenset(
        k
        for k, spec in fields.items()
        if spec.required and not spec.auto_set and k not in _DEFAULTED
    )
    for version, fields in _SCHEMAS.items()
}


# --- Accessors --------------------------------------------------------------


def is_valid_kind(kind: str | None) -> bool:
    """Return True iff ``kind`` is one of the 8 closed kinds."""
    return kind in KINDS


def permitted_statuses(kind: str | None) -> tuple[str, ...] | None:
    """Return the ordered status vocab for ``kind``, or ``None`` if unknown.

    Returns ``None`` (never raises) for an unknown kind so ``validate()`` can
    branch on ``None`` rather than guarding an exception.
    """
    # Guard against a non-str ``kind`` (e.g. a malformed sidecar carrying a
    # dict/list) so the accessor — and thus ``validate`` — never raises
    # ``TypeError`` on an unhashable lookup key.
    if not isinstance(kind, str):
        return None
    return STATUS_VOCAB.get(kind)


def initial_status(kind: str | None) -> str | None:
    """Return the kind's initial/default status, or ``None`` if the kind is unknown."""
    vocab = permitted_statuses(kind)
    return vocab[0] if vocab else None


def phases() -> tuple[str, ...]:
    """Return the ordered phase taxonomy."""
    return PHASES


def is_valid_phase(phase: str | None) -> bool:
    """Return True iff ``phase`` is one of the closed phases."""
    return phase in PHASES


def field_spec(version: str = "v1") -> dict[str, FieldSpec]:
    """Return the sidecar field schema for ``version`` (only ``v1`` today).

    Raises ``KeyError`` for an unknown version; ``validate()`` rejects a bad
    ``version`` before ever calling this.
    """
    return _SCHEMAS[version]


def auto_set_keys(version: str = "v1") -> frozenset[str]:
    """Return the CLI-auto-set keys (tolerated-absent by the pure validator)."""
    return _AUTO_SET_KEYS[version]


def required_operator_keys(version: str = "v1") -> frozenset[str]:
    """Return the keys an operator must supply.

    Required keys minus the auto-set keys (filled by the CLI) and the defaulted
    keys (``status``/``version``). For ``v1`` this is exactly ``{kind, title}``
    (``keywords`` is optional).
    """
    return _REQUIRED_OPERATOR_KEYS[version]


# --- Pure validator ---------------------------------------------------------


class ValidationResult(NamedTuple):
    """Outcome of ``validate``: a normalized sidecar + ordered error strings.

    ``sidecar`` is a shallow copy of the input with ``status`` and ``version``
    defaulted when omitted. ``errors`` is an ordered list of human-readable
    messages, each naming the offending key/path; empty means valid.
    """

    sidecar: dict
    errors: list[str]


def _is_list_of_str(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_iso8601_utc(value: object) -> bool:
    """True iff ``value`` is an ISO-8601 **UTC** datetime string.

    Datetimes are locked to ISO-8601 UTC (e.g. ``2026-06-17T14:32:00Z``) and
    the CLI and migration trust that guarantee, so this enforces what the error message claims:
    the value must parse via ``datetime.fromisoformat`` (which accepts the ``Z``
    suffix on 3.11+) **and** carry a zero UTC offset. Naive (no tzinfo),
    date-only, and non-UTC-offset values are rejected.
    """
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    return parsed.utcoffset() == timedelta(0)


# Per-type checkers share a uniform `(key, value) -> list[str]` signature (empty
# == ok), so the validator dispatches by table lookup instead of an if/elif
# ladder, and the path-qualified ``related`` checker is just another entry rather
# than a special case. Adding a sidecar type = one new checker + one table row.


def _check_str(key: str, value: object) -> list[str]:
    return [] if isinstance(value, str) else [f"key {key!r} must be a string"]


def _check_list_str(key: str, value: object) -> list[str]:
    return [] if _is_list_of_str(value) else [f"key {key!r} must be a list of strings"]


def _check_datetime(key: str, value: object) -> list[str]:
    if _is_iso8601_utc(value):
        return []
    return [f"key {key!r} must be an ISO-8601 UTC datetime string"]


def _check_related(key: str, value: object) -> list[str]:
    """Validate the nested ``related`` map (``kind -> [names]``); shape only."""
    if not isinstance(value, dict):
        return ["key 'related' must be a map of kind -> [names]"]
    errors: list[str] = []
    for rel_kind, names in value.items():
        if not is_valid_kind(rel_kind):
            errors.append(f"related.{rel_kind}: {rel_kind!r} is not a valid kind")
            continue
        if not _is_list_of_str(names):
            errors.append(f"related.{rel_kind}: must be a list of strings")
    return errors


# A kebab segment: starts and ends with [a-z0-9]; interior may contain hyphens.
# Allows single-char names like "x". No uppercase, no underscore, no digit-only ban.
_KEBAB_SEGMENT = r"[a-z0-9]([a-z0-9-]*[a-z0-9])?"
# A map key is one segment (bare) or two segments separated by exactly one slash
# (namespace/name). More than one slash is rejected. Anchored with \Z (not $) so a
# trailing newline cannot sneak past — $ matches just before a final \n, which would
# admit keys like "foo\n" into the index.
_MAP_KEY_RE = re.compile(rf"^{_KEBAB_SEGMENT}(/{_KEBAB_SEGMENT})?\Z")

#: Every ``related-*`` sidecar field is an edge/list concept with its own setter,
#: so a bare label key under this prefix reads as one of them while being stored
#: where none of them is read from. Reserved as a prefix, not by enumeration:
#: ``related-subsystems`` names no real field yet still misleads.
_RELATED_KEY_PREFIX = "related-"


def _derive_reserved_label_keys() -> frozenset[str]:
    """Union the two exact-match reservation sources.

    Kept callable so what a test exercises when a source grows is the derivation
    itself rather than a hand-maintained copy of its result.
    """
    return KINDS | frozenset(kql.VALID_FIELDS)


#: Bare ``labels`` keys that shadow a first-class record concept: every record
#: kind and every queryable KQL field name (23 keys today). Derived at import from
#: the two authoritative sets — adding a kind or a query field reserves its name
#: with no change here. Exact match only: ``hm/area`` is fine, ``area`` is not.
RESERVED_LABEL_KEYS: frozenset[str] = _derive_reserved_label_keys()


def _is_reserved_label_key(map_key: str) -> bool:
    """Whether a ``labels`` key shadows a first-class record concept.

    The union of both reservation sources — the ``related-`` prefix and the
    derived exact-match set — as a single predicate, so the validator and any
    other reader of the rule (e.g. a doc audit) test the same condition rather
    than each restating it.
    """
    return map_key.startswith(_RELATED_KEY_PREFIX) or map_key in RESERVED_LABEL_KEYS

#: ``related-<suffix>`` keys whose suffix names no record kind but does have a
#: dedicated repeatable setter. Source of truth: the flag→field map in
#: ``record/fields.py`` (``_LIST_FIELD_FLAGS``). Mirrored rather than imported —
#: this module is pure and must not reach into the applier layer — so a field
#: renamed there has to be renamed here too. Each field appears under its real
#: sidecar suffix (what ``lore record show --json`` displays) *and* its flag
#: spelling (what an operator reasoning from ``--help`` writes).
_RELATED_SUFFIX_FLAGS = {
    "phases": "--related-phase",
    "phase": "--related-phase",
    "files-or-folders": "--related-file",
    "file": "--related-file",
    "urls": "--related-url",
    "url": "--related-url",
}

#: Reserved keys naming a field an operator can actually set, and the flag that
#: sets it. Not 1:1 with the field name: ``keywords`` and its ``keyword`` query
#: alias share ``--keyword``, and ``phase`` is set by ``--related-phase``.
#: Deliberately partial — the rest of ``RESERVED_LABEL_KEYS`` is either
#: system-managed (``kind`` and the timestamps have no setter) or a scope flag
#: (``repo``/``product``/``suite``/``team``), whose flags **relocate the record to
#: a different vault** and so must never be offered as the fix for a label.
_SETTABLE_FIELD_FLAGS = {
    "status": "--status",
    "keywords": "--keyword",
    "keyword": "--keyword",
    "phase": "--related-phase",
}


def _reserved_key_alternative(map_key: str) -> str:
    """Name the one-step alternative that stores what a reserved key meant.

    Classified in this order, because ``related-area`` and ``related-phases``
    belong to *both* reservation sources and the prefix reading is the one that
    names a flag an operator can run:

    1. ``related-<suffix>`` — an edge (``--related``), a dedicated list flag, or
       (no such field exists) a free attribute.
    2. a record kind — an edge.
    3. a settable field — the flag that sets it.
    4. anything else — a free attribute, with no flag named.
    """
    free_attribute = (
        f"use `--annotation {map_key}=<value>` for a free attribute, or a"
        f" namespaced key (`<ns>/{map_key}`)"
    )
    if map_key.startswith(_RELATED_KEY_PREFIX):
        suffix = map_key[len(_RELATED_KEY_PREFIX):]
        if is_valid_kind(suffix):
            return f"`{map_key}` names a relation — use `--related {suffix}=<name>`."
        flag = _RELATED_SUFFIX_FLAGS.get(suffix)
        if flag is not None:
            return f"`{map_key}` names a relation — use `{flag} <value>`."
        return (
            f"`{map_key}` reads as a relation but names no field — {free_attribute}."
        )
    if is_valid_kind(map_key):
        return f"`{map_key}` is a record kind — use `--related {map_key}=<name>`."
    flag = _SETTABLE_FIELD_FLAGS.get(map_key)
    if flag is not None:
        return f"`{map_key}` is a settable record field — use `{flag} <value>`."
    return f"`{map_key}` is a reserved field name — {free_attribute}."


def _reserved_label_key_error(field: str, map_key: str) -> str:
    """The full refusal for one reserved ``labels`` key.

    ``--unset-label`` rides along on every branch: ``validate()`` is pure and
    cannot tell a key being added from one the record already carries, and a
    stored reserved key fails the record's *next* write whatever that write
    changes — so the message has to carry its own repair.
    """
    return (
        f"{field}: {_reserved_key_alternative(map_key)} Already storing it?"
        f" `--unset-label {map_key}` clears the key."
    )


def _check_map_str_str(key: str, value: object) -> list[str]:
    """Validate a ``map[str,str]`` sidecar field (``labels`` or ``annotations``).

    Enforces: value is a dict; every map value is a ``str``; every map key matches
    ``[namespace/]name`` — a lowercase kebab segment (``[a-z0-9-]``, begins and
    ends with alphanumeric) with an optional single namespace prefix + ``/``.

    ``labels`` additionally refuse a **reserved** key — one in
    ``RESERVED_LABEL_KEYS`` or carrying the ``related-`` prefix — with the
    classified message :func:`_reserved_label_key_error` builds. The check runs
    after the charset match in the same loop, so a malformed key reports as
    malformed and anything echoed into the refusal is already charset-clean.
    ``annotations`` are exempt by field name, not by a separate validator: they
    are the sanctioned carrier for a free attribute whose natural name is taken.

    SECURITY NOTE: map values are untrusted free-form strings supplied by callers.
    Any future read path that echoes them into a prompt or a fenced channel MUST
    escape them before output. No such path exists in v1; this note is the standing
    guard for when one is added. The reserved-key refusal echoes the map *key*
    only, and only once it has matched ``_MAP_KEY_RE``.
    """
    if not isinstance(value, dict):
        return [f"key {key!r} must be a map of string keys to string values"]
    errors: list[str] = []
    for map_key, map_val in value.items():
        if not isinstance(map_val, str):
            errors.append(
                f"{key}: value for map key {map_key!r} must be a string"
                f" (got {type(map_val).__name__})"
            )
        if not isinstance(map_key, str) or not _MAP_KEY_RE.match(map_key):
            errors.append(
                f"{key}: invalid map key {map_key!r} — must match"
                " [namespace/]name (lowercase kebab, begins/ends alphanumeric,"
                " at most one namespace segment)"
            )
            continue
        if key == "labels" and _is_reserved_label_key(map_key):
            errors.append(_reserved_label_key_error(key, map_key))
    return errors


_TYPE_CHECKS = {
    _STR: _check_str,
    _LIST_STR: _check_list_str,
    _DATETIME: _check_datetime,
    _RELATED_MAP: _check_related,
    _MAP_STR_STR: _check_map_str_str,
}


def validate(sidecar: dict, kind: str | None = None) -> ValidationResult:
    """Validate a parsed sidecar dict against the record model. Pure; never raises.

    ``kind`` is read from ``sidecar["kind"]`` when not passed. Implements the
    spec's validator contract: rejects unsupported keys, enforces the kind set
    and per-kind status vocab, defaults ``status``/``version`` when omitted,
    type-checks every key (naming the offending key/path on mismatch), and
    rejects any non-``v1`` ``version``. Returns the normalized sidecar plus an
    ordered list of error strings (empty == valid).

    Shape only — referential integrity of ``related`` target names is NOT checked
    (a dangling target validates clean).
    """
    errors: list[str] = []
    normalized = dict(sidecar)

    # --- version: default when absent; reject any non-v1 (no fall-through). ---
    version = sidecar.get("version")
    if version is None:
        normalized["version"] = VERSION
        version = VERSION
    elif version != VERSION:
        errors.append(f"unsupported version {version!r}: only {VERSION!r} is supported")
        return ValidationResult(normalized, errors)

    schema = field_spec(version)

    # --- kind: must be one of the 8; otherwise we cannot check status vocab. ---
    if kind is None:
        kind = sidecar.get("kind")
    vocab = permitted_statuses(kind)
    if vocab is None:
        errors.append(f"invalid kind {kind!r}: not one of {_SORTED_KINDS}")
        return ValidationResult(normalized, errors)

    # --- unsupported keys (kind-independent in v1). --------------------------
    for key in sidecar:
        if key not in schema:
            errors.append(f"unsupported key {key!r}")

    # --- required operator keys present. -------------------------------------
    for key in required_operator_keys(version):
        if key not in sidecar:
            errors.append(f"missing required key {key!r}")

    # --- kind-gated fields: present only on the kinds that permit them. ------
    for key, permitted_kinds in KIND_GATED_FIELDS.items():
        if key in sidecar and kind not in permitted_kinds:
            errors.append(
                f"key {key!r} is not valid on kind {kind!r}: only permitted on"
                f" {sorted(permitted_kinds)}"
            )

    # --- status: default when omitted; else enforce the kind's vocab. --------
    status = sidecar.get("status")
    if status is None:
        normalized["status"] = initial_status(kind)
    elif status not in vocab:
        errors.append(f"invalid status {status!r} for kind {kind!r}: allowed {list(vocab)}")

    # --- per-key type checks (only for keys present and in the schema). ------
    for key, value in sidecar.items():
        spec = schema.get(key)
        if spec is None:
            continue  # already reported as unsupported
        errors.extend(_TYPE_CHECKS[spec.type_tag](key, value))

    # --- related-phases: every entry must be a valid phase (empty == all). ---
    related_phases = sidecar.get("related-phases")
    if _is_list_of_str(related_phases):
        for phase in related_phases:
            if not is_valid_phase(phase):
                errors.append(f"related-phases: {phase!r} is not a valid phase")

    return ValidationResult(normalized, errors)
