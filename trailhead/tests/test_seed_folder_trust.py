"""``_seed_folder_trust``: concurrent launches against one ``.claude.json`` never corrupt it."""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from trailhead.harness.base import HarnessError
from trailhead.harness.claude_code import _seed_folder_trust


def test_concurrent_seeds_of_different_directories_leave_valid_json_with_every_entry(tmp_path):
    config = tmp_path / ".claude.json"
    config.write_text(json.dumps({"hasCompletedOnboarding": True, "projects": {}}))
    dirs = [tmp_path / f"hub{i}" for i in range(8)]
    errors: list[BaseException] = []

    def seed(directory: Path) -> None:
        try:
            for _ in range(40):
                _seed_folder_trust(config, directory)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=seed, args=(d,)) for d in dirs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    data = json.loads(config.read_text())
    assert data["hasCompletedOnboarding"] is True
    assert set(data["projects"]) <= {str(d) for d in dirs}
    assert all(e["hasTrustDialogAccepted"] is True for e in data["projects"].values())
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".claude.json")) == [
        ".claude.json"
    ]


@pytest.mark.parametrize("existing_mode", [0o640, 0o600])
def test_existing_mode_is_kept(tmp_path, existing_mode):
    config = tmp_path / ".claude.json"
    config.write_text("{}")
    config.chmod(existing_mode)
    _seed_folder_trust(config, tmp_path / "hub")
    assert stat.S_IMODE(config.stat().st_mode) == existing_mode


def test_new_file_is_created_0600(tmp_path):
    config = tmp_path / ".claude.json"
    _seed_folder_trust(config, tmp_path / "hub")
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert json.loads(config.read_text())["projects"][str(tmp_path / "hub")] == {
        "hasTrustDialogAccepted": True
    }


def test_failed_rename_leaves_the_original_and_no_temp_file(tmp_path, monkeypatch):
    config = tmp_path / ".claude.json"
    config.write_text('{"keep": 1}')

    def boom(src, dst):
        raise OSError("nope")

    # Patched by path: the failure under test is the rename after the temp file was
    # written while the original stays readable; no permission on the directory
    # fails the rename but not the temp write.
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(HarnessError, match="cannot write"):
        _seed_folder_trust(config, tmp_path / "hub")
    assert json.loads(config.read_text()) == {"keep": 1}
    assert [p.name for p in tmp_path.iterdir()] == [".claude.json"]


def test_seeds_of_two_directories_keep_both_trust_entries_and_other_keys(tmp_path):
    config = tmp_path / ".claude.json"
    config.write_text(json.dumps({"theme": "dark", "projects": {"/x": {"allowedTools": []}}}))
    _seed_folder_trust(config, tmp_path / "a")
    _seed_folder_trust(config, tmp_path / "b")
    data = json.loads(config.read_text())
    assert data["theme"] == "dark"
    assert data["projects"]["/x"] == {"allowedTools": []}
    assert data["projects"][str(tmp_path / "a")]["hasTrustDialogAccepted"] is True
    assert data["projects"][str(tmp_path / "b")]["hasTrustDialogAccepted"] is True
