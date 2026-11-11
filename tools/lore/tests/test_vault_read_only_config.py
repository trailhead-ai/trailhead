"""``read-only`` vault scope: config validation, ``lore vault add`` / ``ls``.

Everything runs against real files under ``tmp_path`` with injected
``XDG_STATE_HOME`` / ``XDG_CONFIG_HOME`` — never the live install.
"""

import json
import os
import subprocess
import sys

import pytest

from conftest import CLI_PATH, load_script
from test_read_only_write_guard import _snapshot


def _load(tmp_path, monkeypatch, vaults):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"vaults": [{"name": "default", "scope": "default"}, *vaults]}))
    vc = load_script("lore.vault.config")
    return {v.name: v for v in vc.load_config(str(p))}


def _run(args, *, tmp_path, extra=None):
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["LORE_EMAIL"] = "tester@example.com"
    env.pop("LORE_VAULT", None)
    if extra:
        env.update(extra)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args], capture_output=True, text=True, env=env
    )


def _config_file(tmp_path):
    return tmp_path / "config" / "lore" / "config.json"


def _seed_default(tmp_path):
    cfg = _config_file(tmp_path)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"vaults": [{"name": "default", "scope": "default"}]}))


def _foreign_vault(tmp_path):
    d = tmp_path / "foreign"
    (d / "blob").mkdir(parents=True)
    (d / "blob" / "zebra-notes.json").write_text(
        json.dumps(
            {
                "version": "v1",
                "kind": "blob",
                "title": "Zebra notes",
                "status": "active",
                "created-at": "2026-06-17T10:00:00Z",
                "updated-at": "2026-06-17T10:00:00Z",
            }
        )
    )
    (d / "blob" / "zebra-notes.md").write_text("striped quadruped\n")
    return d


# --- config ---------------------------------------------------------------


@pytest.mark.parametrize("shared", [False, True])
def test_read_only_keeps_declared_trust(tmp_path, monkeypatch, shared):
    vaults = _load(
        tmp_path, monkeypatch, [{"name": "ro", "scope": "read-only", "shared": shared}]
    )
    assert vaults["ro"].read_only is True
    assert vaults["ro"].shared is shared


def test_read_only_without_shared_key_loads_shared(tmp_path, monkeypatch):
    vaults = _load(tmp_path, monkeypatch, [{"name": "ro", "scope": "read-only"}])
    assert vaults["ro"].shared is True


@pytest.mark.parametrize("bad", [None, 0, 1, "", "true", [], {}])
def test_read_only_with_a_non_bool_shared_is_refused_naming_the_vault(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    vc = load_script("lore.vault.config")
    data = {
        "vaults": [
            {"name": "default", "scope": "default"},
            {"name": "ro", "scope": "read-only", "shared": bad},
        ]
    }
    with pytest.raises(vc.VaultConfigError) as exc:
        vc.validate_config(data)
    assert "'ro'" in str(exc.value) and "shared" in str(exc.value)


def test_non_read_only_entry_keeps_its_lenient_shared_coercion(tmp_path, monkeypatch):
    vaults = _load(tmp_path, monkeypatch, [{"name": "p", "scope": "product", "shared": 0}])
    assert vaults["p"].shared is False


def test_product_without_shared_key_loads_trusted_and_not_read_only(tmp_path, monkeypatch):
    vaults = _load(tmp_path, monkeypatch, [{"name": "p", "scope": "product"}])
    assert vaults["p"].shared is False
    assert vaults["p"].read_only is False
    assert vaults["default"].read_only is False


# --- old-release rejection -----------------------------------------------


def test_pre_change_validator_rejects_shipped_declaration(tmp_path, monkeypatch):
    _seed_default(tmp_path)
    ro = _foreign_vault(tmp_path)
    r = _run(
        ["vault", "add", "ro", "--scope", "read-only", "--path", str(ro), "--shared"],
        tmp_path=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    written = json.loads(_config_file(tmp_path).read_text())
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    vc = load_script("lore.vault.config")
    monkeypatch.setattr(vc, "VALID_SCOPES", frozenset({"repo", "product", "suite", "team", "default"}))
    with pytest.raises(vc.VaultConfigError) as exc:
        vc.validate_config(written)
    assert "read-only" in str(exc.value) and "invalid scope" in str(exc.value)
    without = {"vaults": [e for e in written["vaults"] if e["scope"] != "read-only"]}
    assert len(vc.validate_config(without)) == 1


# --- vault add ------------------------------------------------------------


def test_add_read_only_indexes_and_leaves_folder_untouched(tmp_path):
    _seed_default(tmp_path)
    ro = _foreign_vault(tmp_path)
    before = _snapshot(ro)
    r = _run(
        ["vault", "add", "ro", "--scope", "read-only", "--path", str(ro), "--shared"],
        tmp_path=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    assert _snapshot(ro) == before
    assert "older lore releases" in r.stdout
    assert "lore vault delete ro" in r.stdout and "config.json" in r.stdout
    s = _run(["search", "zebra"], tmp_path=tmp_path)
    assert "zebra-notes" in s.stdout


@pytest.mark.parametrize("flag,expected", [("--shared", True), ("--trusted", False)])
def test_add_read_only_records_declared_trust(tmp_path, flag, expected):
    _seed_default(tmp_path)
    ro = _foreign_vault(tmp_path)
    r = _run(["vault", "add", "ro", "--scope", "read-only", "--path", str(ro), flag], tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    entry = next(e for e in json.loads(_config_file(tmp_path).read_text())["vaults"] if e["name"] == "ro")
    assert entry["shared"] is expected


def test_add_read_only_missing_folder_refused(tmp_path):
    _seed_default(tmp_path)
    before = _config_file(tmp_path).read_bytes()
    missing = tmp_path / "nope"
    r = _run(["vault", "add", "ro", "--scope", "read-only", "--path", str(missing), "--shared"], tmp_path=tmp_path)
    assert r.returncode != 0
    assert str(missing) in r.stderr and "never creates" in r.stderr
    assert not missing.exists()
    assert _config_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize("extra", [[], ["--shared", "--trusted"]])
def test_add_read_only_needs_exactly_one_trust_flag(tmp_path, extra):
    _seed_default(tmp_path)
    before = _config_file(tmp_path).read_bytes()
    ro = _foreign_vault(tmp_path)
    r = _run(["vault", "add", "ro", "--scope", "read-only", "--path", str(ro), *extra], tmp_path=tmp_path)
    assert r.returncode != 0
    assert _config_file(tmp_path).read_bytes() == before


def test_add_read_only_without_path_refused(tmp_path):
    _seed_default(tmp_path)
    before = _config_file(tmp_path).read_bytes()
    r = _run(["vault", "add", "ro", "--scope", "read-only", "--shared"], tmp_path=tmp_path)
    assert r.returncode != 0
    assert _config_file(tmp_path).read_bytes() == before


def test_trusted_refused_for_other_scopes(tmp_path):
    _seed_default(tmp_path)
    before = _config_file(tmp_path).read_bytes()
    r = _run(["vault", "add", "p", "--scope", "product", "--trusted"], tmp_path=tmp_path)
    assert r.returncode != 0
    assert "--trusted" in r.stderr
    assert _config_file(tmp_path).read_bytes() == before


def test_add_read_only_twice_second_refused(tmp_path):
    _seed_default(tmp_path)
    ro = _foreign_vault(tmp_path)
    args = ["vault", "add", "ro", "--scope", "read-only", "--path", str(ro), "--shared"]
    assert _run(args, tmp_path=tmp_path).returncode == 0
    before = _config_file(tmp_path).read_bytes()
    r = _run(args, tmp_path=tmp_path)
    assert r.returncode != 0
    assert _config_file(tmp_path).read_bytes() == before


def test_vault_ls_shows_read_only_scope(tmp_path):
    _seed_default(tmp_path)
    ro = _foreign_vault(tmp_path)
    _run(["vault", "add", "ro", "--scope", "read-only", "--path", str(ro), "--trusted"], tmp_path=tmp_path)
    out = _run(["vault", "ls"], tmp_path=tmp_path).stdout
    line = next(l for l in out.splitlines() if l.startswith("ro\t"))
    assert line.split("\t")[1] == "read-only"
    _run(["vault", "delete", "ro"], tmp_path=tmp_path)
    _run(["vault", "add", "ro", "--scope", "read-only", "--path", str(ro), "--shared"], tmp_path=tmp_path)
    out = _run(["vault", "ls"], tmp_path=tmp_path).stdout
    line = next(l for l in out.splitlines() if l.startswith("ro\t"))
    assert line.split("\t")[1] == "read-only shared"


# --- existing behaviour unchanged ----------------------------------------


def test_create_and_update_route_to_product_beside_read_only(tmp_path):
    _seed_default(tmp_path)
    ro = _foreign_vault(tmp_path)
    assert _run(["vault", "add", "ro", "--scope", "read-only", "--path", str(ro), "--shared"], tmp_path=tmp_path).returncode == 0
    assert _run(["vault", "add", "prod", "--scope", "product"], tmp_path=tmp_path).returncode == 0
    r = _run(
        ["record", "create", "--kind", "blob", "--title", "Apple", "--keyword", "foo", "--product", "prod"],
        tmp_path=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    rid = r.stdout.strip()
    prod_dir = tmp_path / "state" / "lore" / "vaults" / "prod"
    assert list((prod_dir / "blob").glob("*.md"))
    u = _run(["record", "update", rid, "--keyword", "bar", "--product", "prod"], tmp_path=tmp_path)
    assert u.returncode == 0, u.stderr
    assert "bar" in json.loads(next((prod_dir / "blob").glob("*.json")).read_text())["keywords"]
