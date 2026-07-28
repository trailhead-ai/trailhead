"""Tests for the canonical lore record model + pure sidecar validator.

Pins the declarative model (kinds, per-kind status vocab, phases, the
v1 field schema) and the pure accessors, plus the pure ``validate``
contract. Loaded via ``conftest.load_script("lore.record.model")``.
"""

from conftest import load_script


def rm():
    return load_script("lore.record.model")


# --- declarative model + accessors ---------------------------------


def test_kinds_are_exactly_the_eight():
    assert set(rm().KINDS) == {
        "area",
        "blob",
        "collaboration",
        "decision",
        "lesson",
        "session",
        "spec",
        "task",
    }


def test_version_is_v1():
    assert rm().VERSION == "v1"


def test_status_vocab_matches_spec_per_kind():
    vocab = rm().STATUS_VOCAB
    assert set(vocab["area"]) == {"active"}
    assert set(vocab["blob"]) == {"active"}
    assert set(vocab["collaboration"]) == {"active"}
    assert set(vocab["decision"]) == {"active", "superseded", "dropped"}
    assert set(vocab["lesson"]) == {"active", "conditional"}
    assert set(vocab["session"]) == {"dirty", "clean"}
    assert set(vocab["spec"]) == {
        "draft",
        "ready",
        "planned",
        "complete",
        "superseded",
        "dropped",
    }
    assert set(vocab["task"]) == {
        "open",
        "ready",
        "in-progress",
        "blocked",
        "done",
        "dropped",
        "superseded",
    }


def test_status_vocab_is_ordered_tuple_not_frozenset():
    """First element == initial; ordering must be preserved (tuple, not set)."""
    vocab = rm().STATUS_VOCAB
    assert all(isinstance(v, tuple) for v in vocab.values())
    assert vocab["spec"][0] == "draft"
    assert vocab["task"][0] == "open"


def test_initial_status_per_kind():
    m = rm()
    assert m.initial_status("area") == "active"
    assert m.initial_status("blob") == "active"
    assert m.initial_status("collaboration") == "active"
    assert m.initial_status("decision") == "active"
    assert m.initial_status("lesson") == "active"
    assert m.initial_status("session") == "dirty"
    assert m.initial_status("spec") == "draft"
    assert m.initial_status("task") == "open"


def test_phases_are_ordered_closed_set():
    assert rm().PHASES == ("orient", "frame", "build", "review", "ship", "close")


def test_is_valid_phase():
    m = rm()
    assert m.is_valid_phase("frame") is True
    assert m.is_valid_phase("nope") is False
    assert m.phases() == ("orient", "frame", "build", "review", "ship", "close")


def test_fields_v1_required_optional_and_autoset_flags():
    fields = rm().FIELDS_V1
    # ``keywords`` is relaxed to optional.
    for key in ("version", "kind", "title", "status"):
        assert fields[key].required is True
        assert fields[key].auto_set is False
    for key in ("created-at", "created-by", "updated-at", "updated-by"):
        assert fields[key].auto_set is True
        assert fields[key].required is True
    for key in (
        "keywords",
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
    """Unknown kind returns None, does not raise."""
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
    """Exact equality, not superset (status/version defaulted; auto-set excluded).

    ``keywords`` is relaxed to optional, so
    the required operator key set is exactly ``{kind, title}``.
    """
    assert rm().required_operator_keys() == {"kind", "title"}


# --- datetime Z-suffix parsing -------------------------------


def test_datetime_z_suffix_parses():
    """``datetime.fromisoformat`` accepts the ``Z`` suffix on this runtime.

    Resolved as a test (not a module-level assert that would crash import on an
    unexpected build): the validator type-checks datetimes via try/except, so a
    bad value yields a validation error, never an import-time crash.
    """
    from datetime import datetime

    assert datetime.fromisoformat("2026-06-17T14:32:00Z") is not None


# --- the pure validator --------------------------------------------


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
        "related": {"task": ["kql-search-rollout"], "decision": ["why-kql-subset"]},
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


def test_backlog_kind_rejected_naming_it():
    """``backlog`` was retired in favor of ``task`` — rejected, naming it."""
    sidecar = _worked_example_spec_sidecar()
    sidecar["kind"] = "backlog"
    result = rm().validate(sidecar)
    assert any("backlog" in e for e in result.errors)


def test_plan_kind_rejected_naming_it():
    """``plan`` was retired in favor of ``task`` — rejected, naming it."""
    sidecar = _worked_example_spec_sidecar()
    sidecar["kind"] = "plan"
    result = rm().validate(sidecar)
    assert any("plan" in e for e in result.errors)


def test_backlog_rejected_as_related_map_key_naming_it():
    sidecar = _worked_example_spec_sidecar()
    sidecar["related"] = {"backlog": ["x"]}
    result = rm().validate(sidecar)
    assert any("related.backlog" in e for e in result.errors)


def test_plan_rejected_as_related_map_key_naming_it():
    sidecar = _worked_example_spec_sidecar()
    sidecar["related"] = {"plan": ["x"]}
    result = rm().validate(sidecar)
    assert any("related.plan" in e for e in result.errors)


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
    result = m.validate(sidecar)
    assert any("title" in e for e in result.errors)


def test_missing_keywords_validates_clean():
    """``keywords`` is optional: a sidecar without it validates clean."""
    sidecar = _worked_example_spec_sidecar()
    del sidecar["keywords"]
    result = rm().validate(sidecar)
    assert result.errors == []


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
    sidecar["related"] = ["task", "decision"]
    result = rm().validate(sidecar)
    assert any("related" in e for e in result.errors)


def test_related_non_kind_key_names_path():
    sidecar = _worked_example_spec_sidecar()
    sidecar["related"] = {"widget": ["x"]}
    result = rm().validate(sidecar)
    assert any("related.widget" in e for e in result.errors)


def test_related_value_not_list_of_str_names_path():
    sidecar = _worked_example_spec_sidecar()
    sidecar["related"] = {"task": "not-a-list"}
    result = rm().validate(sidecar)
    assert any("related.task" in e for e in result.errors)


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
    sidecar["related"] = {"task": []}
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


def test_task_blocked_clean():
    sidecar = _worked_example_spec_sidecar()
    sidecar["kind"] = "task"
    sidecar["status"] = "blocked"
    sidecar.pop("related", None)
    sidecar.pop("related-phases", None)
    result = rm().validate(sidecar)
    assert result.errors == []


def test_validate_kind_arg_overrides_sidecar_kind():
    """Kind passed explicitly is used when sidecar omits it."""
    sidecar = _worked_example_spec_sidecar()
    del sidecar["kind"]
    # kind absent from sidecar -> missing required 'kind' is reported, but the
    # passed kind still drives status-vocab validation.
    result = rm().validate(sidecar, kind="spec")
    assert any("kind" in e for e in result.errors)  # operator-required 'kind'
    # status 'draft' is valid for spec, so no status error.
    assert not any("status" in e for e in result.errors)


# --- labels and annotations map fields -----------


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


def test_labels_trailing_newline_key_rejected_naming_key():
    """A key with a trailing newline is rejected — the regex anchors with \\Z, not
    $, so 'foo\\n' cannot sneak past validation and into the index."""
    sidecar = _base_sidecar_with(labels={"foo\n": "val"})
    result = rm().validate(sidecar)
    assert any("foo" in e for e in result.errors)


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
        msg = f"expected error for {desc} key {bad_key!r}"
        assert any(bad_key in e for e in result.errors), msg


# --- reserved label keys ------------------------------------------------
#
# A ``labels`` key that shadows a first-class record concept — a record kind, a
# KQL query field, or a ``related-`` edge field — is refused, because such a
# label reads like the concept it names while being stored somewhere the concept
# is never read from. ``annotations`` are exempt by field name: they are the
# sanctioned carrier for a free attribute whose natural name is reserved.


def _kql_module():
    """The KQL parser module — the second source of the reserved key set.

    Reached through the model's own binding rather than reloaded independently,
    so a test observes the exact module object the derivation reads.
    """
    return rm().kql


def test_reserved_label_keys_is_a_frozenset_union_of_both_sources():
    """The reserved set is exactly ``KINDS | VALID_FIELDS`` — derived, not listed."""
    mod = rm()
    assert isinstance(mod.RESERVED_LABEL_KEYS, frozenset)
    assert mod.RESERVED_LABEL_KEYS == mod.KINDS | frozenset(_kql_module().VALID_FIELDS)


def test_every_record_kind_is_reserved_as_a_label_key():
    """No record kind can be used as a bare label key; the error names the key."""
    mod = rm()
    for kind in sorted(mod.KINDS):
        result = mod.validate(_base_sidecar_with(labels={kind: "x"}))
        assert any(kind in e for e in result.errors), f"kind {kind!r} not reserved"


def test_every_kql_field_is_reserved_as_a_label_key():
    """No queryable field name can be used as a bare label key."""
    mod = rm()
    for field in _kql_module().VALID_FIELDS:
        result = mod.validate(_base_sidecar_with(labels={field: "x"}))
        assert any(field in e for e in result.errors), f"field {field!r} not reserved"


def test_related_prefixed_label_key_rejected():
    """``related-<suffix>`` is refused — the edge fields are their own concept."""
    result = rm().validate(_base_sidecar_with(labels={"related-subsystems": "x"}))
    assert any("related-subsystems" in e for e in result.errors)


def test_related_prefix_reserves_beyond_the_derived_set():
    """``related-subsystems`` is in neither source, proving the prefix reserves alone."""
    mod = rm()
    assert "related-subsystems" not in mod.KINDS
    assert "related-subsystems" not in _kql_module().VALID_FIELDS
    assert "related-subsystems" not in mod.RESERVED_LABEL_KEYS
    result = mod.validate(_base_sidecar_with(labels={"related-subsystems": "x"}))
    assert any("related-subsystems" in e for e in result.errors)


def test_annotations_accept_the_identical_reserved_key():
    """The exemption is by sidecar field name — annotations keep charset-only rules."""
    sidecar = _base_sidecar_with(
        annotations={"area": "home-manager", "related-subsystems": "pr-dashboard"}
    )
    assert rm().validate(sidecar).errors == []


def test_namespaced_label_key_shadowing_a_reserved_name_validates():
    """Reservation is exact-match — a namespace prefix is the documented escape."""
    sidecar = _base_sidecar_with(
        labels={"hm/area": "home-manager", "craft/subsystems": "pr-dashboard"}
    )
    assert rm().validate(sidecar).errors == []


def test_charset_invalid_reserved_label_key_emits_only_the_charset_error():
    """Shape is enforced before reservation, so 'Area' reads as a malformed key."""
    result = rm().validate(_base_sidecar_with(labels={"Area": "x"}))
    assert any("invalid map key" in e and "Area" in e for e in result.errors)
    assert not any("reserved" in e for e in result.errors)


def test_charset_invalid_related_prefixed_label_key_emits_only_the_charset_error():
    """The prefix source is also gated behind the charset check, not ahead of it."""
    result = rm().validate(_base_sidecar_with(labels={"related-Foo": "x"}))
    assert any("invalid map key" in e for e in result.errors)
    assert not any("reserved" in e for e in result.errors)


def test_reserved_label_key_check_reads_no_files():
    """The guard is set membership — ``validate`` stays pure."""
    import builtins

    real_open = builtins.open

    def _explode(*args, **kwargs):
        raise AssertionError("validate() must not open files")

    builtins.open = _explode
    try:
        result = rm().validate(_base_sidecar_with(labels={"decision": "x"}))
    finally:
        builtins.open = real_open
    assert any("decision" in e for e in result.errors)


def test_a_new_record_kind_is_reserved_without_touching_the_guard(monkeypatch):
    """Adding a kind reserves its name — the guard re-derives, it does not enumerate."""
    mod = rm()
    monkeypatch.setattr(mod, "KINDS", mod.KINDS | {"synthetic-kind"})
    monkeypatch.setattr(mod, "RESERVED_LABEL_KEYS", mod._derive_reserved_label_keys())
    result = mod.validate(_base_sidecar_with(labels={"synthetic-kind": "x"}))
    assert any("synthetic-kind" in e for e in result.errors)


def test_a_new_kql_field_is_reserved_without_touching_the_guard():
    """Adding a queryable field reserves its name, via the same derivation."""
    kql = _kql_module()
    original = kql.VALID_FIELDS
    kql.VALID_FIELDS = original + ("synthetic-field",)
    try:
        # Re-import so the import-time derivation reruns against the patched source.
        mod = rm()
        assert "synthetic-field" in mod.RESERVED_LABEL_KEYS
        result = mod.validate(_base_sidecar_with(labels={"synthetic-field": "x"}))
        assert any("synthetic-field" in e for e in result.errors)
    finally:
        kql.VALID_FIELDS = original
        rm()


# --- reserved label key refusal messages ---------------------------------
#
# A refusal an agent cannot act on is a dead end, so each reserved key is
# classified into the alternative that actually stores what the author meant:
# an edge (``--related``), a dedicated list flag, a first-class setter, or —
# when the name is system-managed or would relocate the record — a free
# attribute. Every message also names ``--unset-label`` so a record that
# already carries a reserved key can be repaired from the message alone.


def _label_key_error(map_key: str) -> str:
    """The single refusal ``validate`` emits for one reserved ``labels`` key."""
    result = rm().validate(_base_sidecar_with(labels={map_key: "x"}))
    assert len(result.errors) == 1, result.errors
    return result.errors[0]


#: Alternatives an agent can run in one step. A refusal naming none of these is
#: a dead end regardless of how well it explains the problem.
_EXECUTABLE_ALTERNATIVES = (
    "--related ",
    "--related-phase ",
    "--related-file ",
    "--related-url ",
    "--status ",
    "--keyword ",
    "--annotation ",
)


def _every_refusable_label_key() -> list[str]:
    """Every key the guard refuses: the derived set plus ``related-`` samples.

    The derived set is enumerated (not sampled) so a future record kind or query
    field cannot land without a message that names an alternative.
    """
    extra = {"related-subsystems", "related-file", "related-url", "related-task"}
    return sorted(rm().RESERVED_LABEL_KEYS | extra)


def test_no_refusal_is_a_dead_end():
    """Every refusable key's message names at least one runnable alternative."""
    for key in _every_refusable_label_key():
        msg = _label_key_error(key)
        assert any(alt in msg for alt in _EXECUTABLE_ALTERNATIVES), (
            f"refusal for {key!r} names no runnable alternative: {msg}"
        )


def test_every_refusal_names_the_unset_escape():
    """``validate`` cannot tell a new key from a stored one, so every message
    carries the removal that unblocks a record already holding the key."""
    for key in _every_refusable_label_key():
        assert f"--unset-label {key}" in _label_key_error(key)


def test_related_prefixed_kind_suffix_points_at_the_related_flag():
    """``related-<kind>`` names the edge map, whose setter is ``--related``."""
    assert _label_key_error("related-area") == (
        "labels: `related-area` names a relation — use `--related area=<name>`."
        " Already storing it? `--unset-label related-area` clears the key."
    )


def test_related_prefixed_list_field_names_its_own_flag():
    """``related-`` keys with a dedicated setter point at that setter — routing
    them to a free attribute would store a relation where nothing reads it."""
    for key, flag in (
        ("related-phases", "--related-phase"),
        ("related-file", "--related-file"),
        ("related-url", "--related-url"),
    ):
        msg = _label_key_error(key)
        assert msg == (
            f"labels: `{key}` names a relation — use `{flag} <value>`."
            f" Already storing it? `--unset-label {key}` clears the key."
        )
        assert "--annotation" not in msg


def test_related_prefixed_key_with_no_field_falls_through_to_annotation():
    """``related-subsystems`` names no real field, so a free attribute is the fix."""
    assert _label_key_error("related-subsystems") == (
        "labels: `related-subsystems` reads as a relation but names no field —"
        " use `--annotation related-subsystems=<value>` for a free attribute, or"
        " a namespaced key (`<ns>/related-subsystems`)."
        " Already storing it? `--unset-label related-subsystems` clears the key."
    )


def test_record_kind_label_key_points_at_the_related_flag():
    """A bare kind name is an edge the author meant to draw."""
    assert _label_key_error("area") == (
        "labels: `area` is a record kind — use `--related area=<name>`."
        " Already storing it? `--unset-label area` clears the key."
    )


def test_every_record_kind_message_names_the_related_flag():
    """The kind branch covers all eight kinds, not just the overlapping one."""
    for kind in sorted(rm().KINDS):
        assert f"--related {kind}=<name>" in _label_key_error(kind)


def test_settable_field_label_key_names_the_flag_that_sets_it():
    """Field-to-flag is not 1:1 — ``keywords`` and its ``keyword`` query alias
    share ``--keyword``, and ``phase`` is set by ``--related-phase``."""
    for key, flag in (
        ("status", "--status"),
        ("keywords", "--keyword"),
        ("keyword", "--keyword"),
        ("phase", "--related-phase"),
    ):
        assert _label_key_error(key) == (
            f"labels: `{key}` is a settable record field — use `{flag} <value>`."
            f" Already storing it? `--unset-label {key}` clears the key."
        )


def test_scope_routing_label_key_does_not_name_its_flag():
    """``--repo``/``--product``/``--suite``/``--team`` relocate a record to a
    different vault scope, so naming them would be worse advice than none."""
    for key in ("repo", "product", "suite", "team"):
        msg = _label_key_error(key)
        assert msg == (
            f"labels: `{key}` is a reserved field name — use"
            f" `--annotation {key}=<value>` for a free attribute, or a namespaced"
            f" key (`<ns>/{key}`)."
            f" Already storing it? `--unset-label {key}` clears the key."
        )
        assert f"--{key}" not in msg


def test_system_managed_field_label_key_has_no_flag_to_name():
    """``kind`` and the three timestamps have no setter at all."""
    for key in ("kind", "created-at", "updated-at", "last-referenced-at"):
        assert _label_key_error(key) == (
            f"labels: `{key}` is a reserved field name — use"
            f" `--annotation {key}=<value>` for a free attribute, or a namespaced"
            f" key (`<ns>/{key}`)."
            f" Already storing it? `--unset-label {key}` clears the key."
        )


# --- kind-gated fields: depends-on / parent (task-only graph edges) --------


def _base_task_sidecar_with(**extra):
    """Minimal valid task sidecar with optional extras merged in."""
    s = {
        "version": "v1",
        "kind": "task",
        "title": "Test Task",
        "status": "open",
        "created-at": "2026-06-17T14:32:00Z",
        "created-by": "tester@example.com",
        "updated-at": "2026-06-17T15:00:00Z",
        "updated-by": "tester@example.com",
    }
    s.update(extra)
    return s


def test_kind_gated_fields_table_exact():
    """``depends-on``/``parent`` are gated to ``task`` only, and nothing else is gated."""
    assert rm().KIND_GATED_FIELDS == {
        "depends-on": frozenset({"task"}),
        "parent": frozenset({"task"}),
    }


def test_depends_on_in_fields_v1_as_list_str():
    fields = rm().FIELDS_V1
    assert fields["depends-on"].type_tag == "list[str]"
    assert fields["depends-on"].required is False


def test_parent_in_fields_v1_as_str():
    fields = rm().FIELDS_V1
    assert fields["parent"].type_tag == "str"
    assert fields["parent"].required is False


def test_depends_on_accepted_on_task():
    sidecar = _base_task_sidecar_with(**{"depends-on": ["other-task"]})
    result = rm().validate(sidecar)
    assert result.errors == []


def test_parent_accepted_on_task():
    sidecar = _base_task_sidecar_with(parent="parent-task")
    result = rm().validate(sidecar)
    assert result.errors == []


_NON_TASK_KINDS = sorted(rm().KINDS - {"task"})


def test_non_task_kinds_are_the_expected_seven():
    """Guards the parametrized rejection tests below against a silent kind-set drift."""
    assert _NON_TASK_KINDS == [
        "area",
        "blob",
        "collaboration",
        "decision",
        "lesson",
        "session",
        "spec",
    ]


def test_depends_on_rejected_on_every_non_task_kind_naming_field_and_kind():
    for kind in _NON_TASK_KINDS:
        sidecar = _base_sidecar_with(kind=kind, **{"depends-on": ["other"]})
        result = rm().validate(sidecar)
        joined = " ".join(result.errors)
        assert "depends-on" in joined, f"kind={kind!r}: {result.errors}"
        assert kind in joined, f"kind={kind!r}: {result.errors}"


def test_parent_rejected_on_every_non_task_kind_naming_field_and_kind():
    for kind in _NON_TASK_KINDS:
        sidecar = _base_sidecar_with(kind=kind, parent="some-parent")
        result = rm().validate(sidecar)
        joined = " ".join(result.errors)
        assert "parent" in joined, f"kind={kind!r}: {result.errors}"
        assert kind in joined, f"kind={kind!r}: {result.errors}"


# --- session status vocab → {dirty, clean} ---------------------------------


def test_session_status_vocab_is_dirty_clean():
    """Session vocab is exactly {dirty, clean} — not active/complete."""
    vocab = rm().STATUS_VOCAB
    assert set(vocab["session"]) == {"dirty", "clean"}


def test_session_initial_status_is_dirty():
    """Sessions are born dirty; first element of ordered tuple is 'dirty'."""
    assert rm().initial_status("session") == "dirty"


def test_session_status_vocab_order_dirty_first():
    """dirty must be the first element so initial_status('session') == 'dirty'."""
    assert rm().STATUS_VOCAB["session"][0] == "dirty"


def test_session_clean_is_valid_status():
    """clean is a valid session status."""
    m = rm()
    sidecar = _base_sidecar_with(kind="session", status="clean")
    result = m.validate(sidecar)
    assert result.errors == []


def test_session_dirty_is_valid_status():
    """dirty is a valid session status."""
    m = rm()
    sidecar = _base_sidecar_with(kind="session", status="dirty")
    result = m.validate(sidecar)
    assert result.errors == []


def test_session_active_is_rejected():
    """active is not a valid session status."""
    m = rm()
    sidecar = _base_sidecar_with(kind="session", status="active")
    result = m.validate(sidecar)
    assert result.errors, "active must be rejected for session kind"
    assert any("active" in e for e in result.errors)


def test_session_complete_is_rejected():
    """complete is not a valid session status."""
    m = rm()
    sidecar = _base_sidecar_with(kind="session", status="complete")
    result = m.validate(sidecar)
    assert result.errors, "complete must be rejected for session kind"
    assert any("complete" in e for e in result.errors)


def test_session_shelved_is_rejected():
    """shelved was never valid; still rejected."""
    m = rm()
    sidecar = _base_sidecar_with(kind="session", status="shelved")
    result = m.validate(sidecar)
    assert result.errors, "shelved must be rejected for session kind"


def test_session_handoff_is_rejected():
    """handoff is not in the session vocab; rejected."""
    m = rm()
    sidecar = _base_sidecar_with(kind="session", status="handoff")
    result = m.validate(sidecar)
    assert result.errors, "handoff must be rejected for session kind"


def test_session_finalized_is_rejected():
    """finalized is not in the session vocab; rejected."""
    m = rm()
    sidecar = _base_sidecar_with(kind="session", status="finalized")
    result = m.validate(sidecar)
    assert result.errors, "finalized must be rejected for session kind"
