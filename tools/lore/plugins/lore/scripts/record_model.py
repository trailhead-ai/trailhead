"""Canonical lore record model + pure sidecar validator (Spec S1).

This module is the single, machine-checkable definition of *what a lore record
is*: the closed set of 9 kinds, the per-record JSON sidecar field schema (`v1`),
the per-kind status vocabularies and their initial/default value, the phases
taxonomy, and a **pure** `validate(sidecar, kind)` function.

It mirrors the shape of the legacy `status_validator.py` (canonical module-level
data + pure predicate/accessor functions) but is a **new** module: the legacy
validator keeps guarding old-vocabulary notes until the S7 migration cuts over.
Nothing here reads files or touches the search index — the validator operates on
an already-parsed dict and is shared verbatim by S2 (the `lore record` CLI) and
S7 (the migration). The file/dedicated-per-field-flag wrapper is S2; the index is S3.

Invariants (Spec S1):
- The kind set is closed: exactly the 9 kinds in ``KINDS``; any other ``kind`` is
  rejected.
- Each record carries ``version: v1``; the schema is keyed by ``(kind, version)``,
  but in ``v1`` all kinds share one global field schema (``FIELDS_V1``).
- ``status`` is drawn from the kind's ordered vocab; the **first** element is the
  initial/default value applied when ``status`` is omitted on create.
- The validator checks **shape, not referential integrity**: ``related`` keys must
  be valid kinds and values ``list[str]``, but referenced names are *not* verified
  to exist (a dangling ``{"plan": ["nope"]}`` validates clean — existence is
  enforced nowhere; S3 materializes whatever edges exist).
- ``created-by``/``updated-by`` are plaintext provenance PII (e.g. a git email),
  git-tracked, exactly as the legacy YAML frontmatter already stored. They are
  **never** an authz/authn signal — self-asserted and spoofable. Data
  classification/retention for them is owned by S2 (which writes them); S1 only
  fixes the keys' shape.
"""
# NOTE: deliberately no ``from __future__ import annotations``. The lore test
# harness loads scripts via ``conftest.load_script`` (importlib without
# registering in ``sys.modules``); under string annotations the stdlib
# ``@dataclass`` machinery on 3.12+ looks the module up in ``sys.modules`` to
# resolve field annotations and crashes when it is absent. Evaluating
# annotations eagerly (no future import) sidesteps that — every annotation here
# is a valid runtime expression on 3.11+ and there are no forward references.

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

# --- Canonical declarative model (Slice 1) ----------------------------------

#: The closed set of 9 record kinds. Any other ``kind`` value is rejected.
KINDS: frozenset[str] = frozenset(
    {
        "area",
        "backlog",
        "blob",
        "collaboration",
        "decision",
        "lesson",
        "plan",
        "session",
        "spec",
    }
)

#: Stable display order for the kind set, reused in error messages (computed once).
_SORTED_KINDS: list[str] = sorted(KINDS)

#: The sidecar-schema version stamped on every record. The schema is keyed by
#: ``(kind, version)``; only ``v1`` exists today (umbrella decision 3 defers the
#: versioned registry until a ``v2`` exists).
VERSION: str = "v1"

#: Per-kind status vocabulary as an **ordered tuple** — the first element is the
#: kind's initial/default status (applied when ``status`` is omitted on create).
#: Ordered (not a ``frozenset``) so "first == initial" is well-defined.
STATUS_VOCAB: dict[str, tuple[str, ...]] = {
    "area": ("active",),
    "backlog": ("open", "tracking", "dropped"),
    "blob": ("active",),
    "collaboration": ("active",),
    "decision": ("active", "superseded", "dropped"),
    "lesson": ("active", "conditional"),
    "plan": ("draft", "ready", "in-progress", "complete", "superseded", "dropped"),
    "session": ("active", "complete"),
    "spec": ("draft", "ready", "planned", "complete", "superseded", "dropped"),
}

#: The closed, ordered phase taxonomy. ``related-phases`` is a subset of these;
#: an empty ``related-phases`` means the record applies to *all* phases.
PHASES: tuple[str, ...] = ("orient", "frame", "build", "review", "ship", "close")


@dataclass(frozen=True)
class FieldSpec:
    """Schema for a single sidecar key.

    ``required`` — the key must be present on a validated record, **except** when
    it is ``auto_set`` (filled in by S2's CLI on create/update, so the pure
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
}

#: Schema registry keyed by version (only ``v1`` exists today).
_SCHEMAS: dict[str, dict[str, FieldSpec]] = {"v1": FIELDS_V1}

# Derived key sets are constant per version, so compute them once at import
# rather than rebuilding a frozenset on every accessor call (S7 may validate
# thousands of records).
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


# --- Accessors (Slice 1) ----------------------------------------------------


def is_valid_kind(kind: str | None) -> bool:
    """Return True iff ``kind`` is one of the 9 closed kinds."""
    return kind in KINDS


def permitted_statuses(kind: str | None) -> tuple[str, ...] | None:
    """Return the ordered status vocab for ``kind``, or ``None`` if unknown.

    Returns ``None`` (never raises) for an unknown kind, matching
    ``status_validator.permitted_statuses`` — so ``validate()`` can branch on
    ``None`` rather than guarding an exception.
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
    (``keywords`` is optional — Slice 1, dedicated-field-flags plan).
    """
    return _REQUIRED_OPERATOR_KEYS[version]


# --- Pure validator (Slice 2) -----------------------------------------------


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

    The spec locks datetimes to ISO-8601 UTC (e.g. ``2026-06-17T14:32:00Z``) and
    S2/S7 trust that guarantee, so this enforces what the error message claims:
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


def _check_map_str_str(key: str, value: object) -> list[str]:
    """Validate a ``map[str,str]`` sidecar field (``labels`` or ``annotations``).

    Enforces: value is a dict; every map value is a ``str``; every map key matches
    ``[namespace/]name`` — a lowercase kebab segment (``[a-z0-9-]``, begins and
    ends with alphanumeric) with an optional single namespace prefix + ``/``.

    SECURITY NOTE: map values are untrusted free-form strings supplied by callers.
    Any future read path that echoes them into a prompt or a fenced channel MUST
    escape them before output. No such path exists in v1; this note is the standing
    guard for when one is added.
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

    # --- kind: must be one of the 9; otherwise we cannot check status vocab. ---
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
