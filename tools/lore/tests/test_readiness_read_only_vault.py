"""``lore status`` readiness for a ``read-only`` vault: its folder alone decides.

A read-only vault is read from a plain folder, so it is ``ok`` when that folder
is present and listable — no git repository, no remote, no forge probe — and
``missing`` otherwise. The declaration, not the folder's shape, makes the
difference: the same folder declared ``product`` is judged as a git vault.
"""

from __future__ import annotations

import argparse
import json
import os

import pytest

from conftest import write_vault_config

pytestmark = pytest.mark.usefixtures("hermetic_git_env")


def _readiness():
    from lore.cli import readiness
    return readiness


@pytest.fixture(autouse=True)
def signing_ok(monkeypatch):
    from lore.vault import signing
    monkeypatch.setattr(signing, "git_requires_signing", lambda: True)
    monkeypatch.setattr(
        signing, "describe_status", lambda env=None: (True, "this host signs unattended (key SHA256:abc)")
    )


@pytest.fixture
def no_probe(monkeypatch):
    """Record every forge probe so a test can see whether one ran."""
    calls = []

    def runner(argv, env, timeout):
        calls.append(argv)
        return 0, ""

    monkeypatch.setattr(_readiness(), "_run_probe", runner)
    return calls


def _setup(tmp_path, ro_path, scope="read-only"):
    default = tmp_path / "vaults" / "default"
    default.mkdir(parents=True)
    write_vault_config(
        tmp_path / "config", [("default", "default", default), ("ro", scope, ro_path)]
    )


def _status_json(capsys):
    from lore.cli import init
    rc = init.cmd_status(argparse.Namespace(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    return {i["id"]: i for i in payload["items"]}


def _folder(tmp_path):
    folder = tmp_path / "ro-folder"
    folder.mkdir()
    (folder / "note.md").write_text("x\n", encoding="utf-8")
    return folder


def test_read_only_non_git_folder_is_ok(tmp_path, no_probe, capsys):
    _setup(tmp_path, _folder(tmp_path))
    items = _status_json(capsys)
    assert items["vault:ro"]["state"] == "ok"


def test_read_only_ok_in_the_plain_report(tmp_path, no_probe, capsys):
    from lore.cli import init
    _setup(tmp_path, _folder(tmp_path))
    init.cmd_status(argparse.Namespace(json=False))
    out = capsys.readouterr()
    ro_lines = [l for l in (out.out + out.err).splitlines() if "vault ro" in l]
    assert ro_lines and not any("not a git repo" in l or "no remote" in l for l in ro_lines)


def test_read_only_emits_no_forge_item_and_runs_no_probe(tmp_path, no_probe, capsys):
    _setup(tmp_path, _folder(tmp_path))
    items = _status_json(capsys)
    assert "forge:ro" not in items
    assert not any("ro-folder" in " ".join(argv) for argv in no_probe)


def test_discriminator_same_folder_declared_product_is_missing(tmp_path, no_probe, capsys):
    _setup(tmp_path, _folder(tmp_path), scope="product")
    items = _status_json(capsys)
    assert items["vault:ro"]["state"] == "missing"


def test_read_only_folder_absent_is_missing(tmp_path, no_probe, capsys):
    _setup(tmp_path, tmp_path / "absent")
    assert _status_json(capsys)["vault:ro"]["state"] == "missing"


def test_read_only_path_is_a_regular_file_is_missing(tmp_path, no_probe, capsys):
    f = tmp_path / "afile"
    f.write_text("x\n", encoding="utf-8")
    _setup(tmp_path, f)
    assert _status_json(capsys)["vault:ro"]["state"] == "missing"


def test_read_only_dangling_symlink_is_missing(tmp_path, no_probe, capsys):
    link = tmp_path / "dangling"
    link.symlink_to(tmp_path / "nowhere")
    _setup(tmp_path, link)
    assert _status_json(capsys)["vault:ro"]["state"] == "missing"


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can list any directory")
def test_read_only_unlistable_folder_is_missing(tmp_path, no_probe, capsys):
    folder = _folder(tmp_path)
    folder.chmod(0o000)
    try:
        _setup(tmp_path, folder)
        assert _status_json(capsys)["vault:ro"]["state"] == "missing"
    finally:
        folder.chmod(0o755)


def test_read_only_folder_absent_stays_missing_after_a_refused_write(tmp_path, no_probe, capsys):
    from lore import locking

    absent = tmp_path / "absent"
    _setup(tmp_path, absent)
    with pytest.raises(locking.ReadOnlyVaultError):
        with locking.vault_write_lock(absent):
            pytest.fail("the lock must not be acquired")
    assert not absent.exists()
    assert _status_json(capsys)["vault:ro"]["state"] == "missing"


def test_a_writable_absent_folder_is_created_by_a_write_and_then_reads_ok(
    tmp_path, no_probe, capsys
):
    from lore import locking

    absent = tmp_path / "absent"
    _setup(tmp_path, absent, scope="repo")
    with locking.vault_write_lock(absent):
        pass
    assert absent.is_dir()
