"""Tests for the ``lore record show`` CLI — the canonical record reader.

``record show`` is the CLI-only way to read any record's body and sidecar, so
agents and skills never poke at vault files directly. It reads a record by its
``<kind>/<name>`` id only — reading THIS worktree's live session record is the
separate ``lore session show`` subcommand (see test_session_cli.py), so the
``<kind>/<name>`` grammar here has no special-cased exceptions. Test contract:

  plain:
    - ``record show <kind>/<name>`` prints the body to stdout.
  --json:
    - ``record show <kind>/<name> --json`` emits {record_id, kind, name,
      sidecar (dict), body (str)} — the sidecar is how callers read the
      un-indexed annotations (e.g. flush's ``flushed-at`` watermark).
  errors:
    - a malformed RECORD_ID (no '/') → non-zero + stderr.
    - a bare ``session`` (no '/') is NOT special-cased — same malformed error.
    - a nonexistent record → non-zero + stderr.
  --vault NAME:
    - locates the record in exactly the named configured vault (mirrors
      ``record update --vault``), instead of the cwd-blind config-order scan —
      the read-side fix for a same-named record colliding across vaults.
    - an unknown vault name, or a named vault lacking the record, errors
      ``lore: <msg>`` + nonzero, never falling back to the scan.
    - composes with ``--json``; omitting the flag preserves scan behavior
      byte-for-byte.

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern). Never writes
to the real vault: the CLI resolves the test vault from a seeded config.json
(isolated XDG_CONFIG_HOME) and XDG_STATE_HOME is fenced too.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import make_vault as _make_vault, run_cli as _run, write_default_config  # noqa: F401


def _create_record(vault, state, *, kind="spec", title="Test Record",
                   body="body text\n") -> str:
    r = _run(
        ["record", "create", "--kind", kind, "--title", title, "--keyword", "test"],
        vault=vault, state_dir=state, stdin_text=body,
    )
    assert r.returncode == 0, f"create failed: {r.stderr}"
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# plain: prints the body
# ---------------------------------------------------------------------------

def test_show_prints_body(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state, body="the body content\n")
    r = _run(["record", "show", rid], vault=vault, state_dir=state)
    assert r.returncode == 0, f"show failed: {r.stderr}"
    assert "the body content" in r.stdout


# ---------------------------------------------------------------------------
# --json: emits sidecar + body
# ---------------------------------------------------------------------------

def test_show_json_emits_sidecar_and_body(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state, kind="decision", body="decided\n")
    r = _run(["record", "show", rid, "--json"], vault=vault, state_dir=state)
    assert r.returncode == 0, f"show --json failed: {r.stderr}"
    payload = json.loads(r.stdout)
    assert payload["record_id"] == rid
    assert payload["kind"] == "decision"
    assert payload["name"] == rid.split("/", 1)[1]
    assert "decided" in payload["body"]
    # The sidecar is a dict carrying at least the provenance the writer stamps.
    assert isinstance(payload["sidecar"], dict)
    assert payload["sidecar"], "sidecar should not be empty"


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

def test_show_invalid_record_id(tmp_path):
    vault, state = _make_vault(tmp_path)
    r = _run(["record", "show", "notaslug"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert r.stderr.strip()


def test_show_bare_session_is_not_special_cased(tmp_path):
    # Reading the live session is `lore session show`, NOT a `record show`
    # special case — a bare `session` has no '/', so it fails the <kind>/<name>
    # grammar like any other malformed id (guards against re-introducing a
    # bare-kind exception in `record show`).
    vault, state = _make_vault(tmp_path)
    r = _run(["record", "show", "session"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert "<kind>/<name>" in r.stderr


def test_show_nonexistent_record(tmp_path):
    vault, state = _make_vault(tmp_path)
    r = _run(["record", "show", "spec/does-not-exist"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert r.stderr.strip()


# ---------------------------------------------------------------------------
# --vault: explicit current-location targeting (read-side collision hazard)
# ---------------------------------------------------------------------------
#
# Without a vault-targeting flag, ``record show`` locates a record via
# ``_find_current_record_location``'s cwd-blind config-order scan -- on a
# same-named record colliding across more than one configured vault, the scan
# returns the first configured match, which may not be the vault the caller
# means. ``--vault NAME`` mirrors ``record update --vault``'s semantics
# exactly: resolved via ``_resolve_named_vault``, located directly in that
# vault only, ``lore: <msg>`` + nonzero on an unknown vault name or a record
# absent there, and never a fallback to the scan.


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
    for team, vault, body in (("alpha", alpha_vault, "alpha body\n"), ("beta", beta_vault, "beta body\n")):
        r = _run_cfg(
            ["record", "create", "--kind", "task", "--title", title, "--team", team],
            vault=default_vault, state=state, config_home=config_home, stdin_text=body,
        )
        assert r.returncode == 0, r.stderr
    return default_vault, alpha_vault, beta_vault, state, config_home


def test_show_vault_flag_targets_named_vault_on_collision(tmp_path):
    """``show --vault beta`` returns beta's body, not alpha's (config-order-first)."""
    default_vault, alpha_vault, beta_vault, state, config_home = (
        _duplicate_named_task_two_vaults(tmp_path)
    )
    record_id = "task/dup-task"

    r = _run_cfg(
        ["record", "show", record_id, "--vault", "beta"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "beta body\n"


def test_show_vault_flag_record_absent_in_named_vault_errors_without_scan_fallback(tmp_path):
    """``--vault`` naming a vault that lacks the record errors plainly -- it
    never falls back to scanning the other configured vaults."""
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
        ["record", "show", record_id, "--vault", "beta"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode != 0
    assert r.stderr.startswith("lore: ")


def test_show_vault_flag_unknown_name_errors(tmp_path):
    """An unconfigured ``--vault`` name errors with ``lore: <msg>`` -- nonzero."""
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state, body="body\n")

    r = _run(["record", "show", rid, "--vault", "nope"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert r.stderr.startswith("lore: ")
    assert "nope" in r.stderr


def test_show_vault_flag_works_with_json(tmp_path):
    """``--vault`` composes with ``--json``, reading the named vault's copy."""
    default_vault, alpha_vault, beta_vault, state, config_home = (
        _duplicate_named_task_two_vaults(tmp_path)
    )
    record_id = "task/dup-task"

    r = _run_cfg(
        ["record", "show", record_id, "--vault", "beta", "--json"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["record_id"] == record_id
    assert "beta body" in payload["body"]


def test_show_vault_flag_omitted_preserves_scan_behavior(tmp_path):
    """Omitting ``--vault`` still scans config order and returns the first
    match -- unchanged from pre-existing behavior."""
    default_vault, alpha_vault, beta_vault, state, config_home = (
        _duplicate_named_task_two_vaults(tmp_path)
    )
    record_id = "task/dup-task"

    r = _run_cfg(
        ["record", "show", record_id],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "alpha body\n"
