"""Tests for the ``lore record delete`` CLI.

Covers the test contract:

  delete:
    - delete removes all three artifacts (md + json + index row).
    - invalid / nonexistent RECORD_ID → non-zero, nothing side-effected.

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern). Never writes
to the real vault: the CLI resolves the test vault from a seeded config.json
(isolated XDG_CONFIG_HOME) and XDG_STATE_HOME is fenced too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import (  # noqa: F401
    load_script,
    make_vault as _make_vault,
    run_cli as _run,
    write_default_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_record(vault, state, *, kind="spec", title="Test Record", body="body text\n") -> str:
    """Create a record via the CLI and return its RECORD_ID."""
    r = _run(
        ["record", "create", "--kind", kind, "--title", title, "--keyword", "test"],
        vault=vault,
        state_dir=state,
        stdin_text=body,
    )
    assert r.returncode == 0, f"create failed: {r.stderr}"
    return r.stdout.strip()


def _open_index(state_dir):
    """Load index_store and open the index for test assertions."""
    mod = load_script("lore.search.index")
    return mod.open_index(env={"XDG_STATE_HOME": str(state_dir)})


# ---------------------------------------------------------------------------
# delete: removes all three artifacts
# ---------------------------------------------------------------------------


def test_delete_removes_body(tmp_path):
    """delete removes the .md body file."""
    vault, state = _make_vault(tmp_path)
    record_id = _create_record(vault, state)
    kind, name = record_id.split("/", 1)
    body_path = vault / kind / f"{name}.md"
    assert body_path.exists()

    r = _run(["record", "delete", record_id], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert not body_path.exists()


def test_delete_removes_sidecar(tmp_path):
    """delete removes the .json sidecar file."""
    vault, state = _make_vault(tmp_path)
    record_id = _create_record(vault, state)
    kind, name = record_id.split("/", 1)
    sidecar_path = vault / kind / f"{name}.json"
    assert sidecar_path.exists()

    r = _run(["record", "delete", record_id], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert not sidecar_path.exists()


def test_delete_removes_index_row(tmp_path):
    """delete removes the index row."""
    vault, state = _make_vault(tmp_path)
    record_id = _create_record(vault, state)
    kind, name = record_id.split("/", 1)

    r = _run(["record", "delete", record_id], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr

    conn = _open_index(state)
    try:
        rows = conn.execute(
            "SELECT name FROM records WHERE vault=? AND kind=? AND name=?",
            (str(vault), kind, name),
        ).fetchall()
    finally:
        conn.close()
    assert rows == []


def test_delete_nonexistent_id_exits_nonzero(tmp_path):
    """delete with a nonexistent RECORD_ID → non-zero."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "delete", "spec/does-not-exist"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    assert "not found" in r.stderr.lower() or "error" in r.stderr.lower()


def test_delete_invalid_id_format_exits_nonzero(tmp_path):
    """delete with an invalid RECORD_ID (no slash) → non-zero."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "delete", "no-slash-here"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0


# ---------------------------------------------------------------------------
# CRITICAL security regression: RECORD_ID confinement on
# delete + update. A crafted ID with '..' / absolute segments must NOT delete
# or overwrite .md/.json files outside the active vault.
# ---------------------------------------------------------------------------


def _make_outside_victim(tmp_path: Path) -> Path:
    """Create a sibling 'other-vault' with a victim spec record outside the vault."""
    other = tmp_path / "other-vault" / "spec"
    other.mkdir(parents=True, exist_ok=True)
    (other / "victim.md").write_text("important other-vault content\n", encoding="utf-8")
    (other / "victim.json").write_text('{"kind": "spec"}\n', encoding="utf-8")
    return other


@pytest.mark.parametrize(
    "evil_id",
    [
        "../other-vault/spec/victim",  # climb out of the vault root
        "spec/../../other-vault/spec/victim",  # climb out via the name half
        "/etc/passwd",  # absolute (no slash-split escape)
        "spec/../../../etc/hosts",  # deep traversal
    ],
)
def test_delete_rejects_record_id_escaping_vault(tmp_path, evil_id):
    """A traversal RECORD_ID on delete → non-zero, victim files untouched."""
    vault, state = _make_vault(tmp_path)
    victim = _make_outside_victim(tmp_path)

    r = _run(["record", "delete", evil_id], vault=vault, state_dir=state)
    assert r.returncode != 0, f"escape ID {evil_id!r} was not rejected: {r.stdout!r}"
    # The outside victim must still be on disk — nothing deleted outside the vault.
    assert (victim / "victim.md").exists()
    assert (victim / "victim.json").exists()


@pytest.mark.parametrize(
    "evil_id",
    [
        "../other-vault/spec/victim",
        "spec/../../other-vault/spec/victim",
        "/etc/passwd",
    ],
)
def test_update_rejects_record_id_escaping_vault(tmp_path, evil_id):
    """A traversal RECORD_ID on update → non-zero, victim files unmodified."""
    vault, state = _make_vault(tmp_path)
    victim = _make_outside_victim(tmp_path)
    before = (victim / "victim.md").read_text(encoding="utf-8")

    r = _run(
        ["record", "update", evil_id],
        vault=vault,
        state_dir=state,
        stdin_text="HIJACKED BODY\n",
    )
    assert r.returncode != 0, f"escape ID {evil_id!r} was not rejected: {r.stdout!r}"
    # The outside victim body must be byte-for-byte unchanged.
    assert (victim / "victim.md").read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Group-default scope routing is create-only: delete must still locate and
# remove a record when run from inside a bound workspace — the group binding
# must not interfere with record resolution on delete.
# ---------------------------------------------------------------------------


def test_delete_inside_group_still_locates_and_removes_record(tmp_path):
    """delete run from inside a bound workspace removes a default-vault record
    normally — group-default seeding is create-only and never re-routes the
    delete's record lookup.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    groups_dir = tmp_path / "groups"
    member_repo = tmp_path / "repo"
    member_repo.mkdir()
    trailhead_vault = tmp_path / "trailhead_vault"
    trailhead_vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    (lore_cfg / "config.json").write_text(
        json.dumps(
            {
                "vaults": [
                    {"name": "default", "scope": "default", "path": str(vault)},
                    {"name": "trailhead", "scope": "product", "path": str(trailhead_vault)},
                ]
            }
        ),
        encoding="utf-8",
    )
    groups_dir.mkdir(parents=True, exist_ok=True)
    (groups_dir / "trailhead.toml").write_text(
        '[group]\nname = "trailhead"\n\n'
        f'[[members]]\nname = "repo"\nrepo_root = "{member_repo}"\n\n'
        '[[lore_scopes]]\nscope = "product"\nname = "trailhead"\n',
        encoding="utf-8",
    )
    env = {"XDG_CONFIG_HOME": str(config_home), "LORE_GROUPS_DIR": str(groups_dir)}

    # Create from outside any group → record lands in the default vault.
    c = _run(
        ["record", "create", "--kind", "spec", "--title", "T", "--keyword", "foo"],
        vault=vault,
        state_dir=state,
        env_extra=env,
        cwd=outside,
        stdin_text="body\n",
    )
    assert c.returncode == 0, c.stderr
    record_id = c.stdout.strip()
    kind, name = record_id.split("/", 1)
    assert (vault / kind / f"{name}.md").exists()

    # Delete from inside the bound workspace still finds and removes the record.
    d = _run(
        ["record", "delete", record_id],
        vault=vault,
        state_dir=state,
        env_extra=env,
        cwd=member_repo,
    )
    assert d.returncode == 0, d.stderr
    assert not (vault / kind / f"{name}.md").exists()
    assert not (vault / kind / f"{name}.json").exists()


# ---------------------------------------------------------------------------
# --vault: explicit current-location targeting (mirrors ``record show
# --vault``) -- delete must locate the record in exactly the named configured
# vault, never falling back to the cwd-blind config-order scan.
# ---------------------------------------------------------------------------


def _write_config(config_home: Path, vaults: list) -> Path:
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    cfg_path = lore_cfg / "config.json"
    cfg_path.write_text(json.dumps({"vaults": vaults}, indent=2), encoding="utf-8")
    return cfg_path


def _run_cfg(args, *, vault, state, config_home, stdin_text=None):
    return _run(
        args, vault=vault, state_dir=state, stdin_text=stdin_text,
        env_extra={"XDG_CONFIG_HOME": str(config_home)},
    )


def _duplicate_named_task_two_vaults(tmp_path, *, title="Dup Task"):
    """Two team vaults (config order alpha, beta), each holding an
    independently-created task record of the same name -- the collision case
    the cwd-blind scan cannot disambiguate."""
    default_vault, state = _make_vault(tmp_path)
    alpha_vault = tmp_path / "vault_alpha"
    beta_vault = tmp_path / "vault_beta"
    alpha_vault.mkdir(parents=True)
    beta_vault.mkdir(parents=True)
    config_home = tmp_path / "config"
    _write_config(
        config_home,
        [
            {"name": "default", "scope": "default", "path": str(default_vault)},
            {"name": "alpha", "scope": "team", "path": str(alpha_vault)},
            {"name": "beta", "scope": "team", "path": str(beta_vault)},
        ],
    )
    for team, vault in (("alpha", alpha_vault), ("beta", beta_vault)):
        r = _run_cfg(
            ["record", "create", "--kind", "task", "--title", title, "--team", team],
            vault=default_vault, state=state, config_home=config_home, stdin_text="body\n",
        )
        assert r.returncode == 0, r.stderr
    return default_vault, alpha_vault, beta_vault, state, config_home


def test_delete_vault_flag_targets_named_vault_on_collision(tmp_path):
    """``delete --vault beta`` removes beta's copy and leaves the
    first-declared (alpha) vault's copy untouched -- proving it does not fall
    back to config-order first-match."""
    default_vault, alpha_vault, beta_vault, state, config_home = (
        _duplicate_named_task_two_vaults(tmp_path)
    )
    record_id = "task/dup-task"

    r = _run_cfg(
        ["record", "delete", record_id, "--vault", "beta"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode == 0, r.stderr
    assert not (beta_vault / "task" / "dup-task.md").exists()
    assert (alpha_vault / "task" / "dup-task.md").exists()


def test_delete_vault_flag_record_absent_in_named_vault_errors_without_scan_fallback(tmp_path):
    """``--vault`` naming a vault that lacks the record errors plainly --
    neither vault's copy is deleted."""
    default_vault, state = _make_vault(tmp_path)
    alpha_vault = tmp_path / "vault_alpha"
    beta_vault = tmp_path / "vault_beta"
    alpha_vault.mkdir(parents=True)
    beta_vault.mkdir(parents=True)
    config_home = tmp_path / "config"
    _write_config(
        config_home,
        [
            {"name": "default", "scope": "default", "path": str(default_vault)},
            {"name": "alpha", "scope": "team", "path": str(alpha_vault)},
            {"name": "beta", "scope": "team", "path": str(beta_vault)},
        ],
    )
    r = _run_cfg(
        ["record", "create", "--kind", "task", "--title", "Solo", "--team", "alpha"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="solo body\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = "task/solo"

    r = _run_cfg(
        ["record", "delete", record_id, "--vault", "beta"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode != 0
    assert r.stderr.startswith("lore: ")
    assert (alpha_vault / "task" / "solo.md").exists()
    assert not (beta_vault / "task" / "solo.md").exists()


def test_delete_vault_flag_unknown_name_errors(tmp_path):
    """An unconfigured ``--vault`` name errors with ``lore: <msg>`` --
    nonzero."""
    vault, state = _make_vault(tmp_path)
    record_id = _create_record(vault, state)

    r = _run(["record", "delete", record_id, "--vault", "nope"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert r.stderr.startswith("lore: ")
    assert "nope" in r.stderr


def test_delete_vault_flag_omitted_preserves_scan_behavior(tmp_path):
    """Omitting ``--vault`` still deletes via the existing config-order
    scan -- unchanged regression guard."""
    vault, state = _make_vault(tmp_path)
    record_id = _create_record(vault, state)
    kind, name = record_id.split("/", 1)
    body_path = vault / kind / f"{name}.md"
    assert body_path.exists()

    r = _run(["record", "delete", record_id], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert not body_path.exists()
