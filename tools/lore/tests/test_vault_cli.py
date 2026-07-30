"""End-to-end tests for ``lore vault add|delete|ls|config`` + ``lore init``
config seeding.

Loads the CLI via ``CLI_PATH`` (subprocess) and isolates every path through
injected ``XDG_STATE_HOME`` (vaults root + index) and ``XDG_CONFIG_HOME``
(``config.json``) under ``tmp_path`` — so these tests NEVER touch the real
config, index, or vault (Axiom 6). Index-scope assertions open the index at the
``index_store`` boundary and query it directly.

Covers the test contract:
  - ``add`` writes the config entry AND scans an existing populated dir's records
    into the index, keyed + ``shared``-flagged correctly; missing ``--scope`` →
    non-zero; duplicate name → non-zero; ``--scope default --record blob`` →
    non-zero; unknown ``--record`` kind → non-zero.
  - ``delete`` removes the config entry + index rows, leaves the dir;
    ``--remove-from-disk`` without ``--yes`` aborts non-zero (preview only); with
    ``--yes`` removes the dir; a path outside the expected root (``--path /etc``)
    or a symlink is refused.
  - delete→re-add round-trips index row counts at the ``index_store`` boundary.
  - ``ls`` lists configured vaults + tolerates an absent config; ``init`` seeds a
    config with exactly one ``default`` vault.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

CONFTEST_DIR = Path(__file__).parent
sys.path.insert(0, str(CONFTEST_DIR))
from conftest import CLI_PATH, load_script  # noqa: E402


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _run(args, *, state, config, stdin_text=None, extra=None):
    """Run the lore CLI with isolated XDG state/config dirs."""
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
        input=stdin_text,
    )


def _dirs(tmp_path):
    """Return (state, config) dirs under tmp_path."""
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir()
    config.mkdir()
    return state, config


def _config_path(config):
    return config / "lore" / "config.json"


def _read_config(config):
    return json.loads(_config_path(config).read_text())


def _vaults_root(state):
    return state / "lore" / "vaults"


def _seed_default_config(config, state):
    """Write a minimal config.json with the single default vault."""
    cfg_path = _config_path(config)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "vaults": [
                    {"name": "default", "scope": "default"},
                ]
            }
        )
    )


def _write_record(vault_dir, kind, name, sidecar, body):
    kd = vault_dir / kind
    kd.mkdir(parents=True, exist_ok=True)
    (kd / f"{name}.json").write_text(json.dumps(sidecar))
    (kd / f"{name}.md").write_text(body)


def _sidecar(kind, name, title):
    return {
        "version": "v1",
        "kind": kind,
        "title": title,
        "status": "active",
        "created-at": "2026-06-17T10:00:00Z",
        "updated-at": "2026-06-17T10:00:00Z",
    }


def _open_index(state):
    index_store = load_script("lore.search.index")
    return index_store.open_index(env={"XDG_STATE_HOME": str(state)})


def _index_rows_for(state, vault_root):
    conn = _open_index(state)
    try:
        return conn.execute(
            "SELECT kind, name, shared FROM records WHERE vault=? ORDER BY name",
            (str(vault_root),),
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# init seeding (non-interactive installer)
# ---------------------------------------------------------------------------


def test_init_seeds_config_with_single_default_vault(tmp_path):
    """init seeds config.json with exactly one default-scope vault."""
    state, config = _dirs(tmp_path)
    # init installs the vault-guard into ~/.claude/settings.json (resolved via
    # Path.home()); isolate HOME so the guardrail lands in tmp, never the real file.
    res = _run(["init"], state=state, config=config, extra={"HOME": str(tmp_path / "home")})
    assert res.returncode == 0, res.stderr

    cfg = _read_config(config)
    vaults = cfg["vaults"]
    assert len(vaults) == 1
    assert vaults[0]["name"] == "default"
    assert vaults[0]["scope"] == "default"


def test_init_creates_default_vault_under_state_dir(tmp_path):
    """init creates vaults/default under XDG_STATE_HOME/lore/ (not a user path)."""
    state, config = _dirs(tmp_path)
    # init installs the vault-guard into ~/.claude/settings.json (resolved via
    # Path.home()); isolate HOME so the guardrail lands in tmp, never the real file.
    res = _run(["init"], state=state, config=config, extra={"HOME": str(tmp_path / "home")})
    assert res.returncode == 0, res.stderr
    assert _vaults_root(state).is_dir()
    assert (_vaults_root(state) / "default").is_dir()


# ---------------------------------------------------------------------------
# vault add — config entry + index scan
# ---------------------------------------------------------------------------


def test_add_writes_config_entry(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)

    res = _run(["vault", "add", "team-vault", "--scope", "team"], state=state, config=config)
    assert res.returncode == 0, res.stderr

    cfg = _read_config(config)
    names = {v["name"]: v for v in cfg["vaults"]}
    assert "team-vault" in names
    assert names["team-vault"]["scope"] == "team"


def test_add_creates_vault_directory_when_absent(tmp_path):
    """vault add creates the vault directory and git-inits it when it doesn't exist."""
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)

    vault_dir = _vaults_root(state) / "product-vault"
    assert not vault_dir.exists()

    res = _run(["vault", "add", "product-vault", "--scope", "product"], state=state, config=config)
    assert res.returncode == 0, res.stderr

    assert vault_dir.is_dir(), "vault directory was not created"
    assert (vault_dir / ".git").is_dir(), "vault was not git-initialized"


def test_add_scaffolds_the_lock_gitignore_in_a_new_vault(tmp_path):
    """`vault add` git-inits the vault, so it owes it the `*.lock` ignore too.

    Every vault gets a `.lore.lock` write-lock file at its root the first time
    anything writes to it; without the ignore, `lore sync`'s `git add -A` commits
    it.
    """
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)

    res = _run(["vault", "add", "product-vault", "--scope", "product"], state=state, config=config)
    assert res.returncode == 0, res.stderr

    gitignore = _vaults_root(state) / "product-vault" / ".gitignore"
    assert gitignore.is_file(), "vault add did not scaffold a .gitignore"
    assert "*.lock" in gitignore.read_text(encoding="utf-8").splitlines()


def test_add_scaffolds_the_lock_gitignore_in_an_existing_dir(tmp_path):
    """An already-populated dir registered by `vault add` gets the ignore too."""
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)

    vault_dir = _vaults_root(state) / "team-vault"
    _write_record(vault_dir, "spec", "spec-a", _sidecar("spec", "spec-a", "Spec A"), "body a")

    res = _run(["vault", "add", "team-vault", "--scope", "team"], state=state, config=config)
    assert res.returncode == 0, res.stderr

    gitignore = vault_dir / ".gitignore"
    assert gitignore.is_file(), "vault add did not scaffold a .gitignore"
    assert "*.lock" in gitignore.read_text(encoding="utf-8").splitlines()


def test_add_scans_populated_dir_into_index(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)

    vault_dir = _vaults_root(state) / "team-vault"
    _write_record(vault_dir, "spec", "spec-a", _sidecar("spec", "spec-a", "Spec A"), "body a")
    _write_record(vault_dir, "plan", "plan-x", _sidecar("plan", "plan-x", "Plan X"), "body x")

    res = _run(["vault", "add", "team-vault", "--scope", "team"], state=state, config=config)
    assert res.returncode == 0, res.stderr

    rows = _index_rows_for(state, vault_dir)
    assert {(r[0], r[1]) for r in rows} == {("spec", "spec-a"), ("plan", "plan-x")}
    # own vault (shared defaults False) → shared 0
    assert all(r[2] == 0 for r in rows)


def test_add_shared_vault_flags_rows_shared(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)

    vault_dir = _vaults_root(state) / "shared-team"
    _write_record(vault_dir, "spec", "s1", _sidecar("spec", "s1", "S1"), "b")

    res = _run(
        ["vault", "add", "shared-team", "--scope", "team", "--shared"], state=state, config=config
    )
    assert res.returncode == 0, res.stderr

    rows = _index_rows_for(state, vault_dir)
    assert len(rows) == 1
    assert rows[0][2] == 1


def test_add_repo_name_with_slash_normalized(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)

    res = _run(
        ["vault", "add", "trailhead-ai/trailhead", "--scope", "repo"], state=state, config=config
    )
    assert res.returncode == 0, res.stderr
    names = {v["name"] for v in _read_config(config)["vaults"]}
    assert "trailhead-ai_trailhead" in names


def test_add_missing_scope_is_error(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)
    res = _run(["vault", "add", "no-scope"], state=state, config=config)
    assert res.returncode != 0
    assert res.stderr.strip() != ""


def test_add_duplicate_name_is_error(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)
    first = _run(["vault", "add", "dup", "--scope", "team"], state=state, config=config)
    assert first.returncode == 0, first.stderr
    second = _run(["vault", "add", "dup", "--scope", "product"], state=state, config=config)
    assert second.returncode != 0
    assert "dup" in second.stderr


def test_add_duplicate_after_normalization_is_error(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)
    first = _run(["vault", "add", "a_b", "--scope", "team"], state=state, config=config)
    assert first.returncode == 0, first.stderr
    second = _run(["vault", "add", "a/b", "--scope", "product"], state=state, config=config)
    assert second.returncode != 0


def test_add_default_scope_with_record_is_error(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)
    res = _run(
        ["vault", "add", "another-default", "--scope", "default", "--record", "blob"],
        state=state,
        config=config,
    )
    assert res.returncode != 0
    assert res.stderr.strip() != ""


def test_add_unknown_record_kind_is_error(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)
    res = _run(
        ["vault", "add", "kindy", "--scope", "team", "--record", "notakind"],
        state=state,
        config=config,
    )
    assert res.returncode != 0
    assert "notakind" in res.stderr


def test_add_deep_validation_failure_leaves_config_unchanged(tmp_path):
    """A well-formed-but-semantically-invalid entry (passes the inline guards
    but fails load_config's deeper checks — here a name that is invalid after
    normalization) must be rejected WITHOUT mutating config.json."""
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)
    before = _config_path(config).read_bytes()

    # ".." passes the inline scope/duplicate/record guards but fails
    # validate_layer_name inside load_config (deep validation).
    res = _run(["vault", "add", "..", "--scope", "team"], state=state, config=config)
    assert res.returncode != 0, res.stdout
    # config.json must be byte-for-byte unchanged — never persisted then rejected.
    assert _config_path(config).read_bytes() == before


# ---------------------------------------------------------------------------
# vault delete — config entry + index rows; on-disk kept by default
# ---------------------------------------------------------------------------


def test_delete_removes_config_entry_and_index_rows_keeps_dir(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)

    vault_dir = _vaults_root(state) / "team-vault"
    _write_record(vault_dir, "spec", "spec-a", _sidecar("spec", "spec-a", "Spec A"), "body a")
    add = _run(["vault", "add", "team-vault", "--scope", "team"], state=state, config=config)
    assert add.returncode == 0, add.stderr
    assert len(_index_rows_for(state, vault_dir)) == 1

    res = _run(["vault", "delete", "team-vault"], state=state, config=config)
    assert res.returncode == 0, res.stderr

    names = {v["name"] for v in _read_config(config)["vaults"]}
    assert "team-vault" not in names
    assert _index_rows_for(state, vault_dir) == []
    # On-disk dir untouched.
    assert vault_dir.is_dir()
    assert (vault_dir / "spec" / "spec-a.json").is_file()


def test_delete_remove_from_disk_without_yes_aborts(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)

    vault_dir = _vaults_root(state) / "team-vault"
    _write_record(vault_dir, "spec", "spec-a", _sidecar("spec", "spec-a", "Spec A"), "body a")
    _run(["vault", "add", "team-vault", "--scope", "team"], state=state, config=config)

    res = _run(["vault", "delete", "team-vault", "--remove-from-disk"], state=state, config=config)
    assert res.returncode != 0
    # Dir must survive — no destruction without --yes.
    assert vault_dir.is_dir()
    # Preview names the dir + a record count.
    assert str(vault_dir) in (res.stdout + res.stderr)


def test_delete_remove_from_disk_with_yes_removes_dir(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)

    vault_dir = _vaults_root(state) / "team-vault"
    _write_record(vault_dir, "spec", "spec-a", _sidecar("spec", "spec-a", "Spec A"), "body a")
    _run(["vault", "add", "team-vault", "--scope", "team"], state=state, config=config)
    assert vault_dir.is_dir()

    res = _run(
        ["vault", "delete", "team-vault", "--remove-from-disk", "--yes"], state=state, config=config
    )
    assert res.returncode == 0, res.stderr
    assert not vault_dir.exists()
    names = {v["name"] for v in _read_config(config)["vaults"]}
    assert "team-vault" not in names


def test_delete_remove_from_disk_refuses_path_outside_root(tmp_path):
    state, config = _dirs(tmp_path)
    # A config whose vault path is outside the vaults root (e.g. /etc-like).
    outside = tmp_path / "outside-target"
    outside.mkdir()
    _write_record(outside, "spec", "x", _sidecar("spec", "x", "X"), "b")
    cfg_path = _config_path(config)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "vaults": [
                    {"name": "default", "scope": "default"},
                    {"name": "exfil", "scope": "team", "path": str(outside)},
                ]
            }
        )
    )

    res = _run(
        ["vault", "delete", "exfil", "--remove-from-disk", "--yes"], state=state, config=config
    )
    assert res.returncode != 0
    # The outside dir must NOT be deleted.
    assert outside.is_dir()
    assert (outside / "spec" / "x.json").is_file()


def test_delete_remove_from_disk_refuses_symlinked_path(tmp_path):
    state, config = _dirs(tmp_path)
    real = tmp_path / "real-target"
    real.mkdir()
    (real / "keep.txt").write_text("keep")

    vaults_root = _vaults_root(state)
    vaults_root.mkdir(parents=True, exist_ok=True)
    link = vaults_root / "linky"
    link.symlink_to(real)

    cfg_path = _config_path(config)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "vaults": [
                    {"name": "default", "scope": "default"},
                    {"name": "linky", "scope": "team", "path": str(link)},
                ]
            }
        )
    )

    res = _run(
        ["vault", "delete", "linky", "--remove-from-disk", "--yes"], state=state, config=config
    )
    assert res.returncode != 0
    assert real.is_dir()
    assert (real / "keep.txt").is_file()


def test_delete_remove_from_disk_refuses_symlink_targeting_inside_root(tmp_path):
    """A symlink whose TARGET is inside the vaults root is still refused (the
    symlink guard, not just confinement)."""
    state, config = _dirs(tmp_path)
    vaults_root = _vaults_root(state)
    vaults_root.mkdir(parents=True, exist_ok=True)
    # Real target lives inside the vaults root, so confinement alone would pass.
    real = vaults_root / "real-inside"
    real.mkdir()
    (real / "keep.txt").write_text("keep")
    link = vaults_root / "linky"
    link.symlink_to(real)

    cfg_path = _config_path(config)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        json.dumps(
            {
                "vaults": [
                    {"name": "default", "scope": "default"},
                    {"name": "linky", "scope": "team", "path": str(link)},
                ]
            }
        )
    )

    res = _run(
        ["vault", "delete", "linky", "--remove-from-disk", "--yes"], state=state, config=config
    )
    assert res.returncode != 0
    assert real.is_dir()
    assert (real / "keep.txt").is_file()


def test_delete_remove_from_disk_succeeds_with_symlinked_ancestor(tmp_path):
    """A symlinked ANCESTOR of the vault dir (e.g. macOS /var, a symlinked
    $HOME) must NOT trip the symlink guard — only the vault dir's own leaf
    being a symlink is refused. The vault dir itself is a real directory."""
    # Real state tree; the XDG_STATE_HOME we hand the CLI is a symlink to it,
    # so every ancestor of the vault dir resolves through a symlink.
    real_state = tmp_path / "real-state"
    real_state.mkdir()
    state = tmp_path / "state-link"
    state.symlink_to(real_state)
    config = tmp_path / "config"
    config.mkdir()

    # Legitimate vault dir: a real directory beneath the (symlinked) state root.
    vault_dir = _vaults_root(state) / "team-vault"
    _write_record(vault_dir, "spec", "spec-a", _sidecar("spec", "spec-a", "Spec A"), "body a")

    _seed_default_config(config, state)
    add = _run(["vault", "add", "team-vault", "--scope", "team"], state=state, config=config)
    assert add.returncode == 0, add.stderr

    res = _run(
        ["vault", "delete", "team-vault", "--remove-from-disk", "--yes"], state=state, config=config
    )
    assert res.returncode == 0, res.stderr
    # The vault dir (a real dir, just reached through a symlinked ancestor)
    # must actually be deleted.
    assert not vault_dir.exists()


# ---------------------------------------------------------------------------
# delete → re-add round-trip (index row counts at the index_store boundary)
# ---------------------------------------------------------------------------


def test_delete_then_readd_roundtrips_index_rows(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)

    vault_dir = _vaults_root(state) / "team-vault"
    _write_record(vault_dir, "spec", "spec-a", _sidecar("spec", "spec-a", "Spec A"), "body a")
    _write_record(vault_dir, "plan", "plan-x", _sidecar("plan", "plan-x", "Plan X"), "body x")

    add1 = _run(["vault", "add", "team-vault", "--scope", "team"], state=state, config=config)
    assert add1.returncode == 0, add1.stderr
    assert len(_index_rows_for(state, vault_dir)) == 2

    dele = _run(["vault", "delete", "team-vault"], state=state, config=config)
    assert dele.returncode == 0, dele.stderr
    assert _index_rows_for(state, vault_dir) == []

    # Re-add reattaches the still-present dir and re-scans rows back.
    add2 = _run(["vault", "add", "team-vault", "--scope", "team"], state=state, config=config)
    assert add2.returncode == 0, add2.stderr
    assert len(_index_rows_for(state, vault_dir)) == 2


def test_readd_is_idempotent_no_duplicate_rows(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)

    vault_dir = _vaults_root(state) / "team-vault"
    _write_record(vault_dir, "spec", "spec-a", _sidecar("spec", "spec-a", "Spec A"), "body a")

    add1 = _run(["vault", "add", "team-vault", "--scope", "team"], state=state, config=config)
    assert add1.returncode == 0, add1.stderr
    _run(["vault", "delete", "team-vault"], state=state, config=config)
    add2 = _run(["vault", "add", "team-vault", "--scope", "team"], state=state, config=config)
    assert add2.returncode == 0, add2.stderr

    rows = _index_rows_for(state, vault_dir)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# vault ls
# ---------------------------------------------------------------------------


def test_ls_lists_configured_vaults(tmp_path):
    state, config = _dirs(tmp_path)
    _seed_default_config(config, state)
    _run(["vault", "add", "team-vault", "--scope", "team"], state=state, config=config)

    res = _run(["vault", "ls"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    assert "default" in res.stdout
    assert "team-vault" in res.stdout


def test_ls_tolerates_absent_config(tmp_path):
    state, config = _dirs(tmp_path)
    # No config.json written (pre-init).
    res = _run(["vault", "ls"], state=state, config=config)
    assert res.returncode == 0, res.stderr
    # No traceback.
    assert "Traceback" not in res.stderr


