"""KU-1 assumption probe: the four in-process migration APIs exist and are callable.

Proves KU-1 from the 2026-06-22 lore-existing-vault-migration plan:
  record_model.validate, record_store.place_record,
  record_store.validate_and_write, index_store.rebuild

EPHEMERAL — delete this file after KU-1 is closed.
"""

import inspect

from conftest import load_script


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_model():
    return load_script("record_model")


def _record_store():
    return load_script("record_store")


def _index_store():
    return load_script("index_store")


# ---------------------------------------------------------------------------
# KU-1a: record_model.validate exists, is callable, returns (sidecar, errors)
# ---------------------------------------------------------------------------

def test_record_model_validate_exists_and_is_callable():
    mod = _record_model()
    assert callable(mod.validate), "record_model.validate must be callable"


def test_record_model_validate_signature():
    """Signature: (sidecar: dict, kind: str | None = None) -> ValidationResult."""
    sig = inspect.signature(_record_model().validate)
    params = list(sig.parameters)
    assert params[0] == "sidecar"
    assert params[1] == "kind"
    # kind has a default (None)
    assert sig.parameters["kind"].default is None


def test_record_model_validate_returns_sidecar_and_errors():
    """Calling validate with a minimal valid sidecar returns (normalized_sidecar, errors).

    Required operator keys per FIELDS_V1: title + keywords (both non-auto_set required).
    status/version/provenance are defaulted/auto_set, so omitting them is fine.
    """
    mod = _record_model()
    result = mod.validate({"kind": "lesson", "title": "probe", "keywords": []})
    sidecar, errors = result         # unpack as (sidecar, errors)
    assert isinstance(sidecar, dict)
    assert isinstance(errors, list)
    assert errors == [], f"unexpected errors: {errors}"
    assert sidecar.get("kind") == "lesson"


def test_record_model_validate_rejects_bad_kind():
    """validate is pure and never raises; a bad kind produces errors, not an exception."""
    mod = _record_model()
    result = mod.validate({"kind": "nonexistent_kind", "title": "probe"})
    _, errors = result
    assert errors, "expected at least one error for an unknown kind"


# ---------------------------------------------------------------------------
# KU-1b: record_store.place_record exists, callable, returns RecordLocation
# ---------------------------------------------------------------------------

def test_record_store_place_record_exists_and_is_callable():
    mod = _record_store()
    assert callable(mod.place_record)


def test_record_store_place_record_signature():
    """Signature: (name, kind, scope, vault_root=None) -> RecordLocation."""
    sig = inspect.signature(_record_store().place_record)
    params = list(sig.parameters)
    assert params[0] == "name"
    assert params[1] == "kind"
    assert params[2] == "scope"
    assert params[3] == "vault_root"
    assert sig.parameters["vault_root"].default is None


def test_record_store_place_record_returns_record_location(tmp_path):
    """place_record with an explicit vault_root writes nothing and returns a RecordLocation."""
    mod = _record_store()
    vault_root = str(tmp_path)
    loc = mod.place_record("My Probe Lesson", "lesson", None, vault_root=vault_root)

    # RecordLocation fields per record_store.py:79-92
    assert hasattr(loc, "vault_root")
    assert hasattr(loc, "kind")
    assert hasattr(loc, "name")
    assert hasattr(loc, "record_id")
    assert hasattr(loc, "body_path")
    assert hasattr(loc, "sidecar_path")

    assert loc.kind == "lesson"
    assert loc.record_id.startswith("lesson/")
    # Nothing was written
    assert not loc.body_path.exists()
    assert not loc.sidecar_path.exists()


def test_record_store_place_record_session_kind_uses_name_verbatim(tmp_path):
    """Session records: stem == the passed name (GUID), never slugged."""
    mod = _record_store()
    guid = "abc12345-0000-1111-2222-333333333333"
    loc = mod.place_record(guid, "session", None, vault_root=str(tmp_path))
    assert loc.name == guid
    assert loc.record_id == f"session/{guid}"


# ---------------------------------------------------------------------------
# KU-1c: record_store.validate_and_write exists, callable, correct signature
# ---------------------------------------------------------------------------

def test_record_store_validate_and_write_exists_and_is_callable():
    mod = _record_store()
    assert callable(mod.validate_and_write)


def test_record_store_validate_and_write_signature():
    """Signature: (location, sidecar, body, conn, shared=0)."""
    sig = inspect.signature(_record_store().validate_and_write)
    params = list(sig.parameters)
    assert params[0] == "location"
    assert params[1] == "sidecar"
    assert params[2] == "body"
    assert params[3] == "conn"
    assert params[4] == "shared"
    assert sig.parameters["shared"].default == 0


# ---------------------------------------------------------------------------
# KU-1d: index_store.rebuild exists, callable, correct signature
# ---------------------------------------------------------------------------

def test_index_store_rebuild_exists_and_is_callable():
    mod = _index_store()
    assert callable(mod.rebuild)


def test_index_store_rebuild_signature():
    """Signature: (vaults, conn, *, owned_vault=None, shared_roots=None) -> int."""
    sig = inspect.signature(_index_store().rebuild)
    params = list(sig.parameters)
    assert params[0] == "vaults"
    assert params[1] == "conn"
    # keyword-only params
    assert "owned_vault" in params
    assert "shared_roots" in params
    assert sig.parameters["owned_vault"].default is None
    assert sig.parameters["shared_roots"].default is None
