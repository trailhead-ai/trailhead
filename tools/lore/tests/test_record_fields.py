"""Pure-unit tests for ``lore.record.fields`` — the sidecar field-merge helpers.

The module is a pure dict-transform layer: given a starting sidecar and the CLI
field flags, it produces the mutated sidecar (plus any degenerate-input errors)
that flows on to ``validate_and_write``. No vault, no index, no I/O.

  - ``apply_record_fields``: scalar overwrite (``--status`` / ``--title`` /
    ``--parent``), list append/remove (``--keyword`` / ``--related-*`` /
    ``--depends-on`` with per-item ``--unset-*``), and the ``--related
    KIND=NAME`` map builder with its degenerate-split validation.
  - ``apply_map_labels_annotations``: labels/annotations upsert plus
    remove-with-omit-when-empty.
"""

from __future__ import annotations

from types import SimpleNamespace

from conftest import load_script


def _fields():
    return load_script("lore.record.fields")


def _args(**kw):
    """A minimal argparse-like namespace; absent attrs fall back via getattr."""
    return SimpleNamespace(**kw)


# ---------------------------------------------------------------------------
# apply_record_fields — scalars
# ---------------------------------------------------------------------------


def test_status_scalar_overwrites():
    f = _fields()
    result, errors = f.apply_record_fields({"status": "open"}, _args(status="done"))
    assert errors == []
    assert result["status"] == "done"


def test_title_scalar_sets_when_present():
    f = _fields()
    result, errors = f.apply_record_fields({}, _args(title="New Title"))
    assert errors == []
    assert result["title"] == "New Title"


def test_absent_scalars_leave_sidecar_untouched():
    f = _fields()
    result, errors = f.apply_record_fields({"status": "open"}, _args())
    assert errors == []
    assert result == {"status": "open"}


def test_parent_set_and_unset():
    f = _fields()
    set_result, _ = f.apply_record_fields({}, _args(parent="epic"))
    assert set_result["parent"] == "epic"
    cleared, _ = f.apply_record_fields({"parent": "epic"}, _args(unset_parent=True))
    assert "parent" not in cleared


def test_returns_a_copy_not_the_original():
    f = _fields()
    original = {"status": "open"}
    result, _ = f.apply_record_fields(original, _args(status="done"))
    assert original == {"status": "open"}
    assert result is not original


# ---------------------------------------------------------------------------
# apply_record_fields — repeatable list flags
# ---------------------------------------------------------------------------


def test_keyword_appends_to_existing_list():
    f = _fields()
    result, errors = f.apply_record_fields(
        {"keywords": ["a"]}, _args(keyword=["b", "c"])
    )
    assert errors == []
    assert result["keywords"] == ["a", "b", "c"]


def test_unset_list_item_removes_one_matching_entry():
    f = _fields()
    result, errors = f.apply_record_fields(
        {"keywords": ["a", "b"]}, _args(unset_keyword=["a"])
    )
    assert errors == []
    assert result["keywords"] == ["b"]


def test_unset_absent_list_item_is_a_noop():
    f = _fields()
    result, errors = f.apply_record_fields(
        {"keywords": ["a"]}, _args(unset_keyword=["ghost"])
    )
    assert errors == []
    assert result["keywords"] == ["a"]


def test_depends_on_list_flag_appends():
    f = _fields()
    result, errors = f.apply_record_fields({}, _args(depends_on=["t1", "t2"]))
    assert errors == []
    assert result["depends-on"] == ["t1", "t2"]


# ---------------------------------------------------------------------------
# apply_record_fields — --related KIND=NAME map
# ---------------------------------------------------------------------------


def test_related_pair_appends_name_to_kind():
    f = _fields()
    result, errors = f.apply_record_fields(
        {}, _args(related_pairs=["task=alpha", "task=beta"])
    )
    assert errors == []
    assert result["related"] == {"task": ["alpha", "beta"]}


def test_related_pair_splits_on_first_equals():
    f = _fields()
    result, errors = f.apply_record_fields({}, _args(related_pairs=["spec=a=b"]))
    assert errors == []
    assert result["related"] == {"spec": ["a=b"]}


def test_related_pair_empty_kind_is_rejected():
    f = _fields()
    result, errors = f.apply_record_fields({}, _args(related_pairs=["=name"]))
    assert errors
    assert "related" not in result


def test_related_pair_empty_name_is_rejected():
    f = _fields()
    result, errors = f.apply_record_fields({}, _args(related_pairs=["task="]))
    assert errors
    assert "related" not in result


# ---------------------------------------------------------------------------
# apply_record_fields — --unset-related KIND=NAME map removal
# ---------------------------------------------------------------------------


def test_unset_related_pair_removes_one_name_from_kind():
    f = _fields()
    result, errors = f.apply_record_fields(
        {"related": {"decision": ["stale-id", "good-id"]}},
        _args(unset_related_pairs=["decision=stale-id"]),
    )
    assert errors == []
    assert result["related"] == {"decision": ["good-id"]}


def test_unset_related_pair_drops_kind_key_when_list_empties():
    f = _fields()
    result, errors = f.apply_record_fields(
        {"related": {"decision": ["only-id"], "task": ["other"]}},
        _args(unset_related_pairs=["decision=only-id"]),
    )
    assert errors == []
    assert result["related"] == {"task": ["other"]}


def test_unset_related_pair_omits_related_field_when_map_empties():
    f = _fields()
    result, errors = f.apply_record_fields(
        {"related": {"decision": ["only-id"]}},
        _args(unset_related_pairs=["decision=only-id"]),
    )
    assert errors == []
    assert "related" not in result


def test_unset_related_pair_absent_is_a_silent_noop():
    f = _fields()
    result, errors = f.apply_record_fields(
        {"related": {"decision": ["good-id"]}},
        _args(unset_related_pairs=["decision=ghost"]),
    )
    assert errors == []
    assert result["related"] == {"decision": ["good-id"]}


def test_unset_related_pair_absent_kind_is_a_silent_noop():
    f = _fields()
    result, errors = f.apply_record_fields(
        {"related": {"decision": ["good-id"]}},
        _args(unset_related_pairs=["task=ghost"]),
    )
    assert errors == []
    assert result["related"] == {"decision": ["good-id"]}


def test_unset_related_pair_empty_kind_is_rejected():
    f = _fields()
    result, errors = f.apply_record_fields(
        {"related": {"decision": ["good-id"]}},
        _args(unset_related_pairs=["=name"]),
    )
    assert errors
    assert result["related"] == {"decision": ["good-id"]}


def test_unset_related_pair_empty_name_is_rejected():
    f = _fields()
    result, errors = f.apply_record_fields(
        {"related": {"decision": ["good-id"]}},
        _args(unset_related_pairs=["decision="]),
    )
    assert errors
    assert result["related"] == {"decision": ["good-id"]}


def test_unset_related_pair_malformed_no_equals_is_rejected():
    f = _fields()
    result, errors = f.apply_record_fields(
        {"related": {"decision": ["good-id"]}},
        _args(unset_related_pairs=["decision-good-id"]),
    )
    assert errors
    assert result["related"] == {"decision": ["good-id"]}


# ---------------------------------------------------------------------------
# apply_map_labels_annotations
# ---------------------------------------------------------------------------


def test_labels_upsert_overwrites_existing_key():
    f = _fields()
    result = f.apply_map_labels_annotations(
        {"labels": {"a": "1"}},
        label_pairs=["a=2", "b=3"],
        annotation_pairs=[],
        unset_labels=[],
        unset_annotations=[],
    )
    assert result["labels"] == {"a": "2", "b": "3"}


def test_label_splits_on_first_equals():
    f = _fields()
    result = f.apply_map_labels_annotations(
        {},
        label_pairs=["ns/name=v=w"],
        annotation_pairs=[],
        unset_labels=[],
        unset_annotations=[],
    )
    assert result["labels"] == {"ns/name": "v=w"}


def test_unset_last_label_drops_the_whole_field():
    f = _fields()
    result = f.apply_map_labels_annotations(
        {"labels": {"a": "1"}},
        label_pairs=[],
        annotation_pairs=[],
        unset_labels=["a"],
        unset_annotations=[],
    )
    assert "labels" not in result


def test_unset_absent_label_key_is_a_noop():
    f = _fields()
    result = f.apply_map_labels_annotations(
        {"labels": {"a": "1"}},
        label_pairs=[],
        annotation_pairs=[],
        unset_labels=["ghost"],
        unset_annotations=[],
    )
    assert result["labels"] == {"a": "1"}


def test_annotations_independent_of_labels():
    f = _fields()
    result = f.apply_map_labels_annotations(
        {},
        label_pairs=[],
        annotation_pairs=["k=v"],
        unset_labels=[],
        unset_annotations=[],
    )
    assert result["annotations"] == {"k": "v"}
    assert "labels" not in result
