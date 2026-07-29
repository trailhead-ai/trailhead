"""Tests for ``lore vault resolve --kind <kind> [--json]`` — the group-aware
vault-resolution query ranger's sweep shells out to.

Covers the test contract:

  - Bound camp group + a configured vault whose allowlist accepts the kind →
    full object with vault/path/scope/source all populated.
  - Unbound (no camp group at cwd, or a group with no ``[[lore_scopes]]``
    binding at all) → ``scope: "default"``, ``vault: null``, exit 0.
  - A binding IS present but the elected vault's ``records`` allowlist
    excludes the kind → default-floor report (the same unbound signal), with
    ``skipped``/``skipped_reason`` naming the allowlist exclusion.
  - A binding names a vault absent from ``config.json`` → default floor, and
    that binding appears in ``unmatched_scopes`` (pins the today-silent
    fall-through at ``vault/resolve.py``).
  - The key set is exactly the eight contract keys, always all present.
  - A bad ``--kind`` or an unreadable config → ``lore: <msg>`` on stderr,
    nonzero exit.
  - ``resolve`` is registered in ``vault --help``; existing
    ``add``/``delete``/``ls``/``config`` behavior is unchanged.

Tests run the CLI as a subprocess via ``CLI_PATH`` (conftest pattern), fencing
``XDG_STATE_HOME``/``XDG_CONFIG_HOME`` under ``tmp_path`` and, for camp-group
tests, ``LORE_GROUPS_DIR`` + a subprocess ``cwd`` inside the bound member repo
— never touching the real vault, state, config, or camp groups (Axiom 6).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

CONFTEST_DIR = Path(__file__).parent
sys.path.insert(0, str(CONFTEST_DIR))
from conftest import CLI_PATH  # noqa: E402


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _run(args, *, state, config, cwd=None, extra=None):
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state)
    env["XDG_CONFIG_HOME"] = str(config)
    env["LORE_EMAIL"] = "tester@example.com"
    if extra:
        env.update(extra)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
    )


def _dirs(tmp_path):
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir()
    config.mkdir()
    return state, config


def _config_path(config):
    return config / "lore" / "config.json"


def _write_config(config, vaults):
    """``vaults`` is a list of raw vault-entry dicts, written verbatim."""
    cfg_path = _config_path(config)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"vaults": vaults}), encoding="utf-8")


def _write_group_binding(groups_dir, *, member_repo, group_name="grp", lore_scopes=None):
    """Write a camp group TOML binding ``member_repo`` to a ``[[lore_scopes]]`` map.

    Same shape as the sibling helper in ``test_record_cli_create.py``:
    ``member_repo`` is the group's sole member ``repo_root``, so a subprocess
    run with ``cwd=member_repo`` resolves to this group via camp's
    canonical-member-repo walk-up (no ``camp_state_dir`` needed).
    """
    groups_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f'[group]\nname = "{group_name}"\n',
        f'\n[[members]]\nname = "repo"\nrepo_root = "{member_repo}"\n',
    ]
    for ls in lore_scopes or []:
        lines.append(f'\n[[lore_scopes]]\nscope = "{ls["scope"]}"\nname = "{ls["name"]}"\n')
    (groups_dir / f"{group_name}.toml").write_text("".join(lines), encoding="utf-8")


def _routing_env(groups_dir):
    return {"LORE_GROUPS_DIR": str(groups_dir)}


_CONTRACT_KEYS = {
    "kind", "vault", "path", "scope", "source",
    "skipped", "skipped_reason", "unmatched_scopes",
}


# ---------------------------------------------------------------------------
# Bound group + configured vault → full object populated
# ---------------------------------------------------------------------------


def test_bound_group_resolves_to_configured_vault(tmp_path):
    state, config = _dirs(tmp_path)
    groups_dir = tmp_path / "groups"
    member_repo = tmp_path / "repo"
    member_repo.mkdir()
    team_vault = tmp_path / "team_vault"
    team_vault.mkdir()

    _write_config(
        config,
        [
            {"name": "default", "scope": "default", "path": str(tmp_path / "default_vault")},
            {"name": "beta", "scope": "team", "path": str(team_vault)},
        ],
    )
    _write_group_binding(groups_dir, member_repo=member_repo, lore_scopes=[{"scope": "team", "name": "beta"}])

    res = _run(
        ["vault", "resolve", "--kind", "task", "--json"],
        state=state,
        config=config,
        cwd=member_repo,
        extra=_routing_env(groups_dir),
    )
    assert res.returncode == 0, res.stderr
    obj = json.loads(res.stdout)
    assert set(obj.keys()) == _CONTRACT_KEYS
    assert obj["kind"] == "task"
    assert obj["vault"] == "beta"
    assert obj["path"] == str(team_vault)
    assert obj["scope"] == "team"
    assert obj["source"] == {"team": "beta"}
    assert obj["skipped"] is None
    assert obj["skipped_reason"] is None
    assert obj["unmatched_scopes"] == []


# ---------------------------------------------------------------------------
# Unbound group ({} scopes) → default floor, vault: null
# ---------------------------------------------------------------------------


def test_unbound_group_resolves_to_default_floor(tmp_path):
    state, config = _dirs(tmp_path)
    groups_dir = tmp_path / "groups"
    member_repo = tmp_path / "repo"
    member_repo.mkdir()

    _write_config(
        config,
        [{"name": "default", "scope": "default", "path": str(tmp_path / "default_vault")}],
    )
    # No group binding at all — not even a group TOML file — so
    # ``_resolve_group_scopes`` returns {} (no camp group at this cwd).

    res = _run(
        ["vault", "resolve", "--kind", "task", "--json"],
        state=state,
        config=config,
        cwd=member_repo,
        extra=_routing_env(groups_dir),
    )
    assert res.returncode == 0, res.stderr
    obj = json.loads(res.stdout)
    assert obj["scope"] == "default"
    assert obj["vault"] is None
    assert obj["source"] == {}


# ---------------------------------------------------------------------------
# Binding present but allowlist excludes the kind → default floor + skipped
# ---------------------------------------------------------------------------


def test_allowlist_exclusion_falls_through_to_default_with_skipped(tmp_path):
    state, config = _dirs(tmp_path)
    groups_dir = tmp_path / "groups"
    member_repo = tmp_path / "repo"
    member_repo.mkdir()
    team_vault = tmp_path / "team_vault"
    team_vault.mkdir()

    _write_config(
        config,
        [
            {"name": "default", "scope": "default", "path": str(tmp_path / "default_vault")},
            {
                "name": "beta", "scope": "team", "path": str(team_vault),
                "records": ["spec"],  # excludes "task"
            },
        ],
    )
    _write_group_binding(groups_dir, member_repo=member_repo, lore_scopes=[{"scope": "team", "name": "beta"}])

    res = _run(
        ["vault", "resolve", "--kind", "task", "--json"],
        state=state,
        config=config,
        cwd=member_repo,
        extra=_routing_env(groups_dir),
    )
    assert res.returncode == 0, res.stderr
    obj = json.loads(res.stdout)
    assert obj["scope"] == "default"
    assert obj["vault"] is None
    assert obj["source"] == {"team": "beta"}
    assert obj["skipped"] == "beta"
    assert obj["skipped_reason"] == "kind not in allowlist"
    assert obj["unmatched_scopes"] == []


# ---------------------------------------------------------------------------
# Binding names a vault absent from config → default floor + unmatched_scopes
# ---------------------------------------------------------------------------


def test_binding_names_vault_absent_from_config_is_unmatched(tmp_path):
    state, config = _dirs(tmp_path)
    groups_dir = tmp_path / "groups"
    member_repo = tmp_path / "repo"
    member_repo.mkdir()

    _write_config(
        config,
        [{"name": "default", "scope": "default", "path": str(tmp_path / "default_vault")}],
    )
    _write_group_binding(
        groups_dir, member_repo=member_repo, lore_scopes=[{"scope": "team", "name": "ghost"}]
    )

    res = _run(
        ["vault", "resolve", "--kind", "task", "--json"],
        state=state,
        config=config,
        cwd=member_repo,
        extra=_routing_env(groups_dir),
    )
    assert res.returncode == 0, res.stderr
    obj = json.loads(res.stdout)
    assert obj["scope"] == "default"
    assert obj["vault"] is None
    assert obj["source"] == {"team": "ghost"}
    assert obj["skipped"] is None
    assert obj["skipped_reason"] is None
    assert obj["unmatched_scopes"] == ["team:ghost"]


# ---------------------------------------------------------------------------
# Key set is exactly the eight contract keys, always all present
# ---------------------------------------------------------------------------


def test_key_set_is_exactly_eight_contract_keys_even_with_no_config(tmp_path):
    state, config = _dirs(tmp_path)
    # No config.json at all (vanilla usage, pre-init).
    res = _run(["vault", "resolve", "--kind", "task", "--json"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    obj = json.loads(res.stdout)
    assert set(obj.keys()) == _CONTRACT_KEYS
    assert obj["scope"] == "default"
    assert obj["vault"] is None


# ---------------------------------------------------------------------------
# Bad --kind / unreadable config → "lore: <msg>" on stderr, nonzero exit
# ---------------------------------------------------------------------------


def test_bad_kind_is_rejected(tmp_path):
    state, config = _dirs(tmp_path)
    _write_config(config, [{"name": "default", "scope": "default"}])

    res = _run(["vault", "resolve", "--kind", "notakind", "--json"], state=state, config=config)
    assert res.returncode != 0
    assert res.stderr.startswith("lore: ")
    assert "notakind" in res.stderr


def test_unreadable_config_is_rejected(tmp_path):
    state, config = _dirs(tmp_path)
    cfg_path = _config_path(config)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("not valid json {{{", encoding="utf-8")

    res = _run(["vault", "resolve", "--kind", "task", "--json"], state=state, config=config)
    assert res.returncode != 0
    assert res.stderr.startswith("lore: ")


# ---------------------------------------------------------------------------
# Registered in vault --help; existing add/delete/ls/config unaffected
# ---------------------------------------------------------------------------


def test_resolve_registered_in_vault_help(tmp_path):
    state, config = _dirs(tmp_path)
    res = _run(["vault", "--help"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    assert "resolve" in res.stdout


def test_existing_vault_ls_still_works(tmp_path):
    state, config = _dirs(tmp_path)
    _write_config(config, [{"name": "default", "scope": "default"}])
    res = _run(["vault", "ls"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    assert "default" in res.stdout


# ---------------------------------------------------------------------------
# Human-readable (non-JSON) rendering carries the same fields, one line
# ---------------------------------------------------------------------------


def test_human_rendering_without_json_is_one_line(tmp_path):
    state, config = _dirs(tmp_path)
    _write_config(config, [{"name": "default", "scope": "default"}])

    res = _run(["vault", "resolve", "--kind", "task"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "scope=default" in lines[0]
    assert "vault=" in lines[0]
