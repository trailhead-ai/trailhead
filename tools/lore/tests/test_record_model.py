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
