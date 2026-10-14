"""``lore record criteria``: a spec's declared and removed criteria, read-only.

Every test runs the real CLI against a temp vault. Histories come from the real
``record amend`` verb; corrupt sidecars are written on disk the way a hand edit
or a bad sync would leave them.
"""

from __future__ import annotations

import json

import pytest
from conftest import make_vault as _make_vault, run_cli as _run
from test_record_cli_amend import _amend, _create, _revert, _set_history
from test_record_cli_amend_criteria import _ac, _make_spec, _spec


def _criteria(vault, state, rid, *extra, env_extra=None):
    return _run(["record", "criteria", rid, *extra], vault=vault, state_dir=state,
                env_extra=env_extra)


def _write_body(vault, rid, body):
    kind, name = rid.split("/", 1)
    (vault / kind / f"{name}.md").write_text(body, encoding="utf-8")


def _tree(vault):
    """Every path under the vault, dotfiles included, with its bytes and mtime."""
    return {
        str(p.relative_to(vault)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(vault.rglob("*")) if p.is_file()
    }


def _assert_clean_refusal(r):
    assert r.returncode != 0
    assert r.stderr.startswith("lore: "), r.stderr
    assert "Traceback" not in r.stderr
    assert r.stdout == ""


def test_prints_declared_in_document_order_and_removed_in_numeric_order(tmp_path):
    vault, state, rid = _make_spec(tmp_path, range(1, 13))
    r = _amend(vault, state, rid, _spec([3, 1, 2, 4, 5, 6, 7, 8, 9]))
    assert r.returncode == 0, r.stderr
    out = _criteria(vault, state, rid)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {
        "record": rid,
        "declared": ["AC3", "AC1", "AC2", "AC4", "AC5", "AC6", "AC7", "AC8", "AC9"],
        "removed": ["AC10", "AC11", "AC12"],
    }


def test_a_spec_never_amended_has_nothing_removed(tmp_path):
    vault, state, rid = _make_spec(tmp_path, range(1, 4))
    out = _criteria(vault, state, rid)
    assert json.loads(out.stdout) == {"record": rid, "declared": ["AC1", "AC2", "AC3"], "removed": []}


def test_stdout_is_exactly_one_json_object_and_nothing_is_written(tmp_path):
    vault, state, rid = _make_spec(tmp_path, range(1, 4))
    assert _amend(vault, state, rid, _spec([1, 3])).returncode == 0
    before = _tree(vault)
    out = _criteria(vault, state, rid)
    assert out.stdout.count("\n") == 1 and out.stdout.endswith("\n")
    assert set(json.loads(out.stdout)) == {"record", "declared", "removed"}
    assert _tree(vault) == before


def test_a_legacy_spec_with_a_heading_and_no_ids_prints_empty_lists(tmp_path):
    body = "# Spec\n\n## Acceptance Criteria\n\n- a plain bullet\n- another\n"
    vault, state, rid = _make_spec(tmp_path, body=body)
    out = _criteria(vault, state, rid)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"record": rid, "declared": [], "removed": []}


def test_a_non_spec_record_is_refused(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create(vault, state, "adr", "Pick A Thing", body=_spec([1]))
    r = _criteria(vault, state, rid)
    _assert_clean_refusal(r)
    assert "spec" in r.stderr and rid in r.stderr


def test_a_missing_record_is_refused(tmp_path):
    vault, state = _make_vault(tmp_path)
    _assert_clean_refusal(_criteria(vault, state, "spec/does-not-exist"))


@pytest.mark.parametrize("rid", ["", "spec", "spec/", "/name", "Spec/../x"])
def test_a_malformed_id_is_refused(tmp_path, rid):
    vault, state = _make_vault(tmp_path)
    _assert_clean_refusal(_criteria(vault, state, rid))


def test_a_missing_heading_is_refused(tmp_path):
    vault, state, rid = _make_spec(tmp_path)
    _write_body(vault, rid, "# Spec\n\n- **AC1.** orphan\n")
    r = _criteria(vault, state, rid)
    _assert_clean_refusal(r)
    assert "no '## Acceptance Criteria' heading" in r.stderr


def test_a_duplicate_heading_is_refused(tmp_path):
    vault, state, rid = _make_spec(tmp_path)
    _write_body(vault, rid, _spec([1]) + "\n## Acceptance Criteria\n\n" + _ac(2) + "\n")
    r = _criteria(vault, state, rid)
    _assert_clean_refusal(r)
    assert "more than one" in r.stderr


@pytest.mark.parametrize("history", [{}, "x", 3], ids=["dict", "string", "int"])
def test_a_stored_history_that_is_not_a_list_is_refused(tmp_path, history):
    vault, state, rid = _make_spec(tmp_path)
    _set_history(vault, rid, history)
    r = _criteria(vault, state, rid)
    _assert_clean_refusal(r)
    assert "not a list" in r.stderr


@pytest.mark.parametrize(
    "criteria",
    [{"removed": "AC1"}, {"removed": []}, {}, {"removed": ["ac1"]}, {"removed": ["AC1", "AC1"]}],
    ids=["string", "empty", "empty-object", "non-canonical", "repeated"],
)
def test_a_malformed_removed_record_is_refused(tmp_path, criteria):
    vault, state, rid = _make_spec(tmp_path)
    entry = {"at": "2026-10-01T00:00:00Z", "by": "x@example.com", "initiated-by": "agent",
             "reason": "r", "diff": "", "criteria": criteria}
    _set_history(vault, rid, [entry])
    _assert_clean_refusal(_criteria(vault, state, rid))


def test_an_unknown_vault_is_a_clean_error(tmp_path):
    vault, state, rid = _make_spec(tmp_path)
    r = _criteria(vault, state, rid, "--vault", "nope")
    _assert_clean_refusal(r)
    assert "vault 'nope' is not configured" in r.stderr


def test_vault_picks_the_record_in_the_named_vault_when_two_hold_the_same_name(tmp_path):
    default_vault, state = _make_vault(tmp_path)
    other = tmp_path / "vault_other"
    other.mkdir()
    config_home = tmp_path / "config"
    (config_home / "lore").mkdir(parents=True)
    (config_home / "lore" / "config.json").write_text(json.dumps({"vaults": [
        {"name": "default", "scope": "default", "path": str(default_vault)},
        {"name": "other", "scope": "team", "records": ["spec"], "path": str(other)},
    ]}), encoding="utf-8")
    env = {"XDG_CONFIG_HOME": str(config_home)}
    rid = _create(default_vault, state, "spec", "A Spec", status="ready", body=_spec([1, 2]))
    kind, name = rid.split("/", 1)
    for sub in ("md", "json"):
        (other / kind).mkdir(exist_ok=True)
        (other / kind / f"{name}.{sub}").write_text(
            (default_vault / kind / f"{name}.{sub}").read_text(encoding="utf-8"), encoding="utf-8")
    _write_body(other, rid, _spec([7, 8, 9]))
    in_other = _criteria(default_vault, state, rid, "--vault", "other", env_extra=env)
    in_default = _criteria(default_vault, state, rid, "--vault", "default", env_extra=env)
    assert json.loads(in_other.stdout)["declared"] == ["AC7", "AC8", "AC9"]
    assert json.loads(in_default.stdout)["declared"] == ["AC1", "AC2"]


def test_a_revert_that_restores_a_removed_criterion_empties_removed(tmp_path):
    vault, state, rid = _make_spec(tmp_path, range(1, 4))
    assert _amend(vault, state, rid, _spec([1, 3])).returncode == 0
    assert json.loads(_criteria(vault, state, rid).stdout) == {
        "record": rid, "declared": ["AC1", "AC3"], "removed": ["AC2"]}
    r = _revert(vault, state, rid, 1)
    assert r.returncode == 0, r.stderr
    assert json.loads(_criteria(vault, state, rid).stdout) == {
        "record": rid, "declared": ["AC1", "AC2", "AC3"], "removed": []}


def test_criteria_help_is_reachable_from_the_parser(tmp_path):  # inert-gate: allow seam smoke: the subcommand is wired into the real parser
    vault, state = _make_vault(tmp_path)
    r = _run(["record", "criteria", "--help"], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert "record criteria" in r.stdout.split("\n\n")[0]


@pytest.mark.parametrize(
    "content",
    ['<<<<<<< HEAD\n{"kind": "spec"}\n=======\n{"kind": "spec"}\n>>>>>>> other\n', "[1, 2]"],
    ids=["conflict-markers", "not-an-object"],
)
def test_an_unreadable_sidecar_is_refused_instead_of_printing_empty_removed(tmp_path, content):
    vault, state, rid = _make_spec(tmp_path, range(1, 4))
    kind, name = rid.split("/", 1)
    (vault / kind / f"{name}.json").write_text(content, encoding="utf-8")
    _assert_clean_refusal(_criteria(vault, state, rid))
