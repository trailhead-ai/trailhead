"""Spec S1 tests: the canonical lore record model + pure sidecar validator.

Slice 1 pins the declarative model (kinds, per-kind status vocab, phases, the
v1 field schema) and the pure accessors. Slice 2 pins the pure ``validate``
contract. Loaded via ``conftest.load_script("record_model")`` like every other
lore script.
"""

from conftest import load_script


def rm():
    return load_script("record_model")


# --- Slice 1: declarative model + accessors ---------------------------------


def test_kinds_are_exactly_the_nine():
    assert set(rm().KINDS) == {
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


def test_version_is_v1():
    assert rm().VERSION == "v1"


def test_status_vocab_matches_spec_per_kind():
    vocab = rm().STATUS_VOCAB
    assert set(vocab["area"]) == {"active"}
    assert set(vocab["backlog"]) == {"open", "tracking", "dropped"}
    assert set(vocab["blob"]) == {"active"}
    assert set(vocab["collaboration"]) == {"active"}
    assert set(vocab["decision"]) == {"active", "superseded", "dropped"}
    assert set(vocab["lesson"]) == {"active", "conditional"}
    assert set(vocab["plan"]) == {
        "draft",
        "ready",
        "in-progress",
        "complete",
        "superseded",
        "dropped",
    }
    assert set(vocab["session"]) == {"active", "complete"}
    assert set(vocab["spec"]) == {
        "draft",
        "ready",
        "planned",
        "complete",
        "superseded",
        "dropped",
    }


def test_status_vocab_is_ordered_tuple_not_frozenset():
    """First element == initial; ordering must be preserved (tuple, not set)."""
    vocab = rm().STATUS_VOCAB
    assert all(isinstance(v, tuple) for v in vocab.values())
    assert vocab["plan"][0] == "draft"
    assert vocab["backlog"][0] == "open"


def test_initial_status_per_kind():
    m = rm()
    assert m.initial_status("area") == "active"
    assert m.initial_status("backlog") == "open"
    assert m.initial_status("blob") == "active"
    assert m.initial_status("collaboration") == "active"
    assert m.initial_status("decision") == "active"
    assert m.initial_status("lesson") == "active"
    assert m.initial_status("plan") == "draft"
    assert m.initial_status("session") == "active"
    assert m.initial_status("spec") == "draft"


def test_phases_are_ordered_closed_set():
    assert rm().PHASES == ("orient", "frame", "build", "review", "ship", "close")


def test_is_valid_phase():
    m = rm()
    assert m.is_valid_phase("frame") is True
    assert m.is_valid_phase("nope") is False
    assert m.phases() == ("orient", "frame", "build", "review", "ship", "close")


def test_fields_v1_required_optional_and_autoset_flags():
    fields = rm().FIELDS_V1
    for key in ("version", "kind", "title", "keywords", "status"):
        assert fields[key].required is True
        assert fields[key].auto_set is False
    for key in ("created-at", "created-by", "updated-at", "updated-by"):
        assert fields[key].auto_set is True
        assert fields[key].required is True
    for key in (
        "team",
        "suite",
        "product",
        "repo",
        "last-referenced-at",
        "related-files-or-folders",
        "related",
        "related-urls",
        "related-phases",
    ):
        assert fields[key].required is False


def test_fields_v1_type_tags():
    fields = rm().FIELDS_V1
    assert fields["keywords"].type_tag == "list[str]"
    assert fields["related-files-or-folders"].type_tag == "list[str]"
    assert fields["related-urls"].type_tag == "list[str]"
    assert fields["related-phases"].type_tag == "list[str]"
    assert fields["related"].type_tag == "related-map"
    assert fields["created-at"].type_tag == "datetime"
    assert fields["updated-at"].type_tag == "datetime"
    assert fields["last-referenced-at"].type_tag == "datetime"
    assert fields["title"].type_tag == "str"


def test_field_spec_accessor_returns_v1():
    m = rm()
    assert m.field_spec("v1") is m.FIELDS_V1
    assert m.field_spec() is m.FIELDS_V1


def test_accessor_contract_unknown_kind_returns_none_never_raises():
    """Council Critical: unknown kind returns None, does not raise."""
    m = rm()
    assert m.is_valid_kind("foo") is False
    assert m.permitted_statuses("foo") is None
    assert m.initial_status("foo") is None


def test_auto_set_keys_exact():
    assert rm().auto_set_keys() == {
        "created-at",
        "created-by",
        "updated-at",
        "updated-by",
    }


def test_required_operator_keys_exact_equality():
    """Exact equality, not superset (status/version defaulted; auto-set excluded)."""
    assert rm().required_operator_keys() == {"kind", "title", "keywords"}


# --- Slice 2: KU1 — datetime Z-suffix parsing -------------------------------


def test_datetime_z_suffix_parses():
    """KU1: ``datetime.fromisoformat`` accepts the ``Z`` suffix on this runtime.

    Resolved as a test (not a module-level assert that would crash import on an
    unexpected build): the validator type-checks datetimes via try/except, so a
    bad value yields a validation error, never an import-time crash.
    """
    from datetime import datetime

    assert datetime.fromisoformat("2026-06-17T14:32:00Z") is not None


# --- Slice 2: the pure validator --------------------------------------------


def _worked_example_spec_sidecar():
    """The spec's worked-example ``spec`` sidecar."""
    return {
        "version": "v1",
        "kind": "spec",
        "title": "lore search query language (KQL-subset facade)",
        "status": "draft",
        "keywords": ["query language", "kql", "search"],
        "created-at": "2026-06-17T14:32:00Z",
        "created-by": "tom.duffield@gmail.com",
        "updated-at": "2026-06-17T15:10:00Z",
        "updated-by": "tom.duffield@gmail.com",
        "team": "trailhead",
        "related": {"plan": ["kql-search-rollout"], "decision": ["why-kql-subset"]},
        "related-phases": ["frame"],
    }


def test_worked_example_validates_clean():
    result = rm().validate(_worked_example_spec_sidecar())
    assert result.errors == []


def test_unknown_key_rejected_naming_it():
    m = rm()
    sidecar = _worked_example_spec_sidecar()
    sidecar["group"] = "trailhead"
    sidecar["foo"] = "bar"
    result = m.validate(sidecar)
    joined = " ".join(result.errors)
    assert "group" in joined
    assert "foo" in joined


def test_invalid_kind_rejected():
    sidecar = _worked_example_spec_sidecar()
    sidecar["kind"] = "widget"
    result = rm().validate(sidecar)
    assert any("widget" in e for e in result.errors)


def test_status_outside_vocab_rejected_naming_value_and_allowed_set():
    sidecar = _worked_example_spec_sidecar()
    sidecar["kind"] = "lesson"
    sidecar["status"] = "complete"  # not in lesson vocab {active, conditional}
    result = rm().validate(sidecar)
    offending = [e for e in result.errors if "complete" in e]
    assert offending
    assert any("active" in e and "conditional" in e for e in offending)


def test_status_omitted_defaults_to_initial_no_error():
    sidecar = _worked_example_spec_sidecar()
    del sidecar["status"]
    result = rm().validate(sidecar)
    assert result.errors == []
    assert result.sidecar["status"] == "draft"  # spec initial


def test_missing_operator_required_keys_error():
    m = rm()
    sidecar = _worked_example_spec_sidecar()
    del sidecar["title"]
    del sidecar["keywords"]
    result = m.validate(sidecar)
    assert any("title" in e for e in result.errors)
    assert any("keywords" in e for e in result.errors)


def test_missing_auto_set_keys_tolerated():
    sidecar = _worked_example_spec_sidecar()
    for key in ("created-at", "created-by", "updated-at", "updated-by"):
        del sidecar[key]
    result = rm().validate(sidecar)
    assert result.errors == []


def test_keywords_as_string_rejected_naming_key():
    sidecar = _worked_example_spec_sidecar()
    sidecar["keywords"] = "not-a-list"
    result = rm().validate(sidecar)
    assert any("keywords" in e for e in result.errors)


def test_related_not_a_map_rejected():
    sidecar = _worked_example_spec_sidecar()
    sidecar["related"] = ["plan", "decision"]
    result = rm().validate(sidecar)
    assert any("related" in e for e in result.errors)


def test_related_non_kind_key_names_path():
    sidecar = _worked_example_spec_sidecar()
    sidecar["related"] = {"widget": ["x"]}
    result = rm().validate(sidecar)
    assert any("related.widget" in e for e in result.errors)


def test_related_value_not_list_of_str_names_path():
    sidecar = _worked_example_spec_sidecar()
    sidecar["related"] = {"plan": "not-a-list"}
    result = rm().validate(sidecar)
    assert any("related.plan" in e for e in result.errors)


def test_datetime_not_iso_rejected_naming_key():
    sidecar = _worked_example_spec_sidecar()
    sidecar["created-at"] = "not-a-date"
    result = rm().validate(sidecar)
    assert any("created-at" in e for e in result.errors)


def test_datetime_explicit_utc_offset_accepted():
    """A zero UTC offset (`+00:00`) is the same instant as `Z` — accepted."""
    sidecar = _worked_example_spec_sidecar()
    sidecar["created-at"] = "2026-06-17T14:32:00+00:00"
    result = rm().validate(sidecar)
    assert result.errors == []


def test_datetime_naive_rejected():
    """A naive timestamp carries no UTC guarantee — rejected (contract is UTC)."""
    sidecar = _worked_example_spec_sidecar()
    sidecar["created-at"] = "2026-06-17T14:32:00"
    result = rm().validate(sidecar)
    assert any("created-at" in e for e in result.errors)


def test_datetime_non_utc_offset_rejected():
    """A non-zero offset is not UTC — rejected, matching the 'UTC' error text."""
    sidecar = _worked_example_spec_sidecar()
    sidecar["created-at"] = "2026-06-17T14:32:00+05:00"
    result = rm().validate(sidecar)
    assert any("created-at" in e for e in result.errors)


def test_non_str_kind_does_not_raise():
    """Purity: a malformed sidecar with a non-str (unhashable) kind yields an
    error, never a TypeError from the vocab lookup."""
    m = rm()
    sidecar = _worked_example_spec_sidecar()
    sidecar["kind"] = {"not": "a-string"}
    result = m.validate(sidecar)  # must not raise
    assert any("kind" in e for e in result.errors)
    # The accessor is likewise total for non-str input.
    assert m.permitted_statuses({"x": 1}) is None
    assert m.initial_status(["list"]) is None


def test_version_absent_defaults_to_v1_no_error():
    sidecar = _worked_example_spec_sidecar()
    del sidecar["version"]
    result = rm().validate(sidecar)
    assert result.errors == []
    assert result.sidecar["version"] == "v1"


def test_version_non_v1_rejected_with_named_error():
    sidecar = _worked_example_spec_sidecar()
    sidecar["version"] = "v2"
    result = rm().validate(sidecar)
    assert any("v2" in e for e in result.errors)


def test_related_empty_list_boundary_clean():
    sidecar = _worked_example_spec_sidecar()
    sidecar["related"] = {"plan": []}
    result = rm().validate(sidecar)
    assert result.errors == []


def test_related_phases_invalid_phase_rejected():
    sidecar = _worked_example_spec_sidecar()
    sidecar["related-phases"] = ["frame", "bogus"]
    result = rm().validate(sidecar)
    assert any("bogus" in e for e in result.errors)


def test_related_phases_empty_ok():
    sidecar = _worked_example_spec_sidecar()
    sidecar["related-phases"] = []
    result = rm().validate(sidecar)
    assert result.errors == []


def test_lesson_conditional_clean():
    sidecar = _worked_example_spec_sidecar()
    sidecar["kind"] = "lesson"
    sidecar["status"] = "conditional"
    sidecar.pop("related", None)
    sidecar.pop("related-phases", None)
    result = rm().validate(sidecar)
    assert result.errors == []


def test_backlog_tracking_clean():
    sidecar = _worked_example_spec_sidecar()
    sidecar["kind"] = "backlog"
    sidecar["status"] = "tracking"
    sidecar.pop("related", None)
    sidecar.pop("related-phases", None)
    result = rm().validate(sidecar)
    assert result.errors == []


def test_validate_kind_arg_overrides_sidecar_kind():
    """Kind passed explicitly is used when sidecar omits it."""
    sidecar = _worked_example_spec_sidecar()
    del sidecar["kind"]
    # kind no longer in sidecar -> missing required 'kind' is reported, but the
    # passed kind still drives status-vocab validation.
    result = rm().validate(sidecar, kind="spec")
    assert any("kind" in e for e in result.errors)  # operator-required 'kind'
    # status 'draft' is valid for spec, so no status error.
    assert not any("status" in e for e in result.errors)


# --- Slice 1 (plan 2026-06-20): labels and annotations map fields -----------


def _base_sidecar_with(**extra):
    """Minimal valid spec sidecar with optional extras merged in."""
    s = {
        "version": "v1",
        "kind": "spec",
        "title": "Test Spec",
        "keywords": ["test"],
        "status": "draft",
        "created-at": "2026-06-17T14:32:00Z",
        "created-by": "tester@example.com",
        "updated-at": "2026-06-17T15:00:00Z",
        "updated-by": "tester@example.com",
    }
    s.update(extra)
    return s


def test_labels_and_annotations_in_fields_v1():
    """Both fields appear in FIELDS_V1 as optional map[str,str]."""
    fields = rm().FIELDS_V1
    assert "labels" in fields
    assert "annotations" in fields
    assert fields["labels"].required is False
    assert fields["labels"].type_tag == "map[str,str]"
    assert fields["annotations"].required is False
    assert fields["annotations"].type_tag == "map[str,str]"


def test_labels_bare_and_namespaced_key_validates():
    """labels={bare key, namespaced key} both valid."""
    sidecar = _base_sidecar_with(labels={"worktree": "s5", "claude-code/model": "x"})
    result = rm().validate(sidecar)
    assert result.errors == []


def test_annotations_bare_and_namespaced_key_validates():
    """annotations works identically to labels for shape validation."""
    sidecar = _base_sidecar_with(annotations={"worktree": "s5", "claude-code/model": "x"})
    result = rm().validate(sidecar)
    assert result.errors == []


def test_spec_worked_example_with_labels_and_annotations_validates():
    """The spec worked example (both fields populated) validates clean."""
    sidecar = _worked_example_spec_sidecar()
    sidecar["labels"] = {"worktree": "s5", "claude-code/model": "sonnet-4"}
    sidecar["annotations"] = {"note": "generated by executor slice-1"}
    result = rm().validate(sidecar)
    assert result.errors == []


def test_labels_non_map_rejected_naming_key():
    """A non-dict labels value is rejected, naming 'labels'."""
    sidecar = _base_sidecar_with(labels="not-a-map")
    result = rm().validate(sidecar)
    assert any("labels" in e for e in result.errors)


def test_annotations_non_map_rejected_naming_key():
    """A non-dict annotations value is rejected, naming 'annotations'."""
    sidecar = _base_sidecar_with(annotations=["a", "b"])
    result = rm().validate(sidecar)
    assert any("annotations" in e for e in result.errors)


def test_labels_non_string_value_int_rejected_naming_key():
    """A map value that is an int is rejected, naming the key."""
    sidecar = _base_sidecar_with(labels={"worktree": 1})
    result = rm().validate(sidecar)
    joined = " ".join(result.errors)
    assert "worktree" in joined


def test_labels_non_string_value_bool_rejected():
    """A map value that is True is rejected."""
    sidecar = _base_sidecar_with(labels={"flag": True})
    result = rm().validate(sidecar)
    assert any("flag" in e for e in result.errors)


def test_labels_non_string_value_dict_rejected():
    """A map value that is a dict is rejected."""
    sidecar = _base_sidecar_with(labels={"nested": {"a": "b"}})
    result = rm().validate(sidecar)
    assert any("nested" in e for e in result.errors)


def test_labels_non_string_value_list_rejected():
    """A map value that is a list is rejected."""
    sidecar = _base_sidecar_with(labels={"items": ["a", "b"]})
    result = rm().validate(sidecar)
    assert any("items" in e for e in result.errors)


def test_labels_empty_string_key_rejected():
    """An empty string key is rejected."""
    sidecar = _base_sidecar_with(labels={"": "val"})
    result = rm().validate(sidecar)
    assert result.errors  # some error emitted for the bad key


def test_labels_uppercase_key_rejected_naming_key():
    """An uppercase key is rejected, naming the offender."""
    sidecar = _base_sidecar_with(labels={"MyKey": "val"})
    result = rm().validate(sidecar)
    assert any("MyKey" in e for e in result.errors)


def test_labels_leading_dash_key_rejected_naming_key():
    """A key beginning with '-' is rejected."""
    sidecar = _base_sidecar_with(labels={"-bad": "val"})
    result = rm().validate(sidecar)
    assert any("-bad" in e for e in result.errors)


def test_labels_trailing_dash_key_rejected_naming_key():
    """A key ending with '-' is rejected."""
    sidecar = _base_sidecar_with(labels={"bad-": "val"})
    result = rm().validate(sidecar)
    assert any("bad-" in e for e in result.errors)


def test_labels_illegal_char_key_rejected_naming_key():
    """A key containing an illegal char (e.g. '_') is rejected."""
    sidecar = _base_sidecar_with(labels={"bad_key": "val"})
    result = rm().validate(sidecar)
    assert any("bad_key" in e for e in result.errors)


def test_labels_multi_segment_namespace_rejected_naming_key():
    """a/b/c (two slashes) is rejected — at most one namespace segment."""
    sidecar = _base_sidecar_with(labels={"a/b/c": "val"})
    result = rm().validate(sidecar)
    assert any("a/b/c" in e for e in result.errors)


def test_labels_empty_namespace_segment_rejected_naming_key():
    """/x (empty namespace before slash) is rejected."""
    sidecar = _base_sidecar_with(labels={"/x": "val"})
    result = rm().validate(sidecar)
    assert any("/x" in e for e in result.errors)


def test_labels_absent_omitted_from_schema_not_unsupported():
    """Absent labels/annotations are simply absent (not flagged unsupported)."""
    sidecar = _base_sidecar_with()
    result = rm().validate(sidecar)
    assert result.errors == []


def test_annotations_same_key_rules_as_labels():
    """annotations uses the same key validation rules as labels."""
    for bad_key, desc in [
        ("MyKey", "uppercase"),
        ("-bad", "leading dash"),
        ("bad-", "trailing dash"),
        ("bad_key", "illegal char"),
        ("a/b/c", "multi-segment"),
        ("/x", "empty namespace"),
    ]:
        sidecar = _base_sidecar_with(annotations={bad_key: "val"})
        result = rm().validate(sidecar)
        assert any(bad_key in e for e in result.errors), f"expected error for {desc} key {bad_key!r}"
