"""Sidecar field-merge helpers — how a record's fields evolve on write.

The pure dict-transform layer between the CLI's per-field flags and
``validate_and_write``: given a starting sidecar and the parsed field flags,
produce the mutated sidecar (and any degenerate-input errors) to be validated and
written. No vault, no index, no I/O — every result flows on through ``validate()``
downstream, so vocabulary/kind checks are NOT re-implemented here; these helpers
only guard the degenerate splits (``--related``/``--label``) that ``validate()``
could not name.

Two intentionally separate branches:

  - :func:`apply_record_fields` — scalars (``--status``/``--title``/``--parent``),
    repeatable list flags (``--keyword``/``--related-*``/``--depends-on`` with
    per-item ``--unset-*`` removers), and the ``--related KIND=NAME`` map builder.
  - :func:`apply_map_labels_annotations` — the ``--label``/``--annotation`` maps
    with upsert-or-remove-with-omit-when-empty semantics.
"""

from __future__ import annotations

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


def apply_record_fields(
    sidecar: dict,
    args,
) -> tuple[dict, list[str]]:
    """Apply the dedicated per-field flags to *sidecar*; return (updated, errors).

    Structural mirror of :func:`apply_map_labels_annotations` (upsert/unset on a
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


def apply_map_labels_annotations(
    sidecar: dict,
    label_pairs: list[str],
    annotation_pairs: list[str],
    unset_labels: list[str],
    unset_annotations: list[str],
) -> dict:
    """Apply --label/--annotation/--unset-label/--unset-annotation to *sidecar*.

    Map-field branch — intentionally separate from ``apply_record_fields``'s
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
