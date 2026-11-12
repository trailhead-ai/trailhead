"""Tests for trailhead/core_vault.py — the bundled core-vault copy sync."""

import os
from pathlib import Path

import pytest

from trailhead.core_vault import CoreVaultSyncError, core_copy_dir, sync_core_copy


def _write(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()
    }


def _fenced_env(tmp_path: Path, state: str = "state") -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_STATE_HOME": str(tmp_path / state),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "source",
        {"README.md": b"readme\n", "records/a.md": b"alpha\n", "records/b.md": b"beta\n"},
    )


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    return tmp_path / "state" / "core-vault"


def _siblings(dest: Path) -> list[str]:
    return sorted(p.name for p in dest.parent.iterdir() if p.name != dest.name)


def test_first_sync_creates_tree_and_reports_changed(source, dest):
    assert sync_core_copy(source, dest) is True
    assert _files(dest) == _files(source)


def test_unchanged_sync_reports_unchanged_and_touches_nothing(source, dest):
    sync_core_copy(source, dest)
    paths = [dest, *sorted(dest.rglob("*"))]
    before = {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in paths}
    parent_before = sorted(os.listdir(dest.parent))

    assert sync_core_copy(source, dest) is False

    after = {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in paths}
    assert after == before
    assert sorted(os.listdir(dest.parent)) == parent_before


def test_edit_is_invisible_until_sync_then_lands(source, dest):
    sync_core_copy(source, dest)
    (source / "records/a.md").write_bytes(b"alpha v2\n")
    assert (dest / "records/a.md").read_bytes() == b"alpha\n"

    assert sync_core_copy(source, dest) is True
    assert (dest / "records/a.md").read_bytes() == b"alpha v2\n"


def test_removed_source_file_mirrors_into_copy(source, dest):
    sync_core_copy(source, dest)
    (source / "records/b.md").unlink()

    assert sync_core_copy(source, dest) is True
    assert set(_files(dest)) == set(_files(source))


def test_added_source_file_appears_in_copy(source, dest):
    sync_core_copy(source, dest)
    _write(source, {"new/c.md": b"gamma\n"})

    assert sync_core_copy(source, dest) is True
    assert (dest / "new/c.md").read_bytes() == b"gamma\n"
    assert _files(dest) == _files(source)


def test_symlink_in_source_is_refused_and_dest_untouched(source, dest):
    sync_core_copy(source, dest)
    (source / "link.md").symlink_to(source / "README.md")
    (source / "records/a.md").write_bytes(b"changed\n")
    before = _files(dest)

    with pytest.raises(CoreVaultSyncError, match="contains a symlink"):
        sync_core_copy(source, dest)

    assert _files(dest) == before
    assert _siblings(dest) == []


def test_fifo_in_source_is_refused(source, dest):
    os.mkfifo(source / "pipe")

    with pytest.raises(CoreVaultSyncError, match="regular"):
        sync_core_copy(source, dest)

    assert not dest.parent.exists()


def test_missing_source_is_refused_and_nothing_created(tmp_path, dest):
    with pytest.raises(CoreVaultSyncError, match="source"):
        sync_core_copy(tmp_path / "nope", dest)

    assert not dest.exists()
    assert not dest.parent.exists()


def test_no_staging_or_backup_left_after_changed_sync(source, dest):
    sync_core_copy(source, dest)
    (source / "records/a.md").write_bytes(b"alpha v2\n")
    sync_core_copy(source, dest)

    assert _siblings(dest) == []


def test_core_copy_dir_follows_injected_env(tmp_path):
    a = core_copy_dir(_fenced_env(tmp_path, "state-a"))
    b = core_copy_dir(_fenced_env(tmp_path, "state-b"))

    assert a != b
    assert a == tmp_path / "state-a" / "trailhead" / "core-vault"
    assert b.name == "core-vault"


def test_missing_parent_is_created(source, tmp_path):
    dest = tmp_path / "deep" / "er" / "core-vault"

    assert sync_core_copy(source, dest) is True
    assert _files(dest) == _files(source)


def test_symlink_dest_is_refused_and_target_untouched(source, tmp_path):
    target = _write(tmp_path / "target", {"keep.md": b"keep\n"})
    dest = tmp_path / "state" / "core-vault"
    dest.parent.mkdir()
    dest.symlink_to(target)

    with pytest.raises(CoreVaultSyncError, match="dest"):
        sync_core_copy(source, dest)

    assert _files(target) == {"keep.md": b"keep\n"}
    assert dest.is_symlink()


def test_file_dest_is_refused(source, dest):
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"not a dir")

    with pytest.raises(CoreVaultSyncError, match="dest"):
        sync_core_copy(source, dest)

    assert dest.read_bytes() == b"not a dir"


def test_leftover_staging_and_backup_swept_on_next_sync(source, dest):
    sync_core_copy(source, dest)
    (source / "records/a.md").write_bytes(b"alpha v2\n")
    _write(dest.parent, {f".{dest.name}.staging-crashed/x.md": b"x"})
    _write(dest.parent, {f".{dest.name}.old-crashed/y.md": b"y"})

    assert sync_core_copy(source, dest) is True

    assert _siblings(dest) == []
    assert _files(dest) == _files(source)


def test_preexisting_staging_symlink_is_refused_before_writing(source, dest, tmp_path):
    sync_core_copy(source, dest)
    (source / "records/a.md").write_bytes(b"alpha v2\n")
    victim = _write(tmp_path / "victim", {"v.md": b"v"})
    (dest.parent / f".{dest.name}.staging-evil").symlink_to(victim)
    before = _files(dest)

    with pytest.raises(CoreVaultSyncError, match="staging"):
        sync_core_copy(source, dest)

    assert _files(dest) == before
    assert _files(victim) == {"v.md": b"v"}


def test_preexisting_staging_file_is_refused(source, dest):
    dest.parent.mkdir(parents=True)
    (dest.parent / f".{dest.name}.staging-file").write_bytes(b"f")

    with pytest.raises(CoreVaultSyncError, match="staging"):
        sync_core_copy(source, dest)

    assert not dest.exists()


def test_dot_files_ignored_in_compare_and_copy(source, dest):
    sync_core_copy(source, dest)
    _write(source, {".DS_Store": b"junk", "records/.DS_Store": b"junk"})

    assert sync_core_copy(source, dest) is False
    (source / "records/a.md").write_bytes(b"alpha v2\n")
    assert sync_core_copy(source, dest) is True
    assert _files(dest) == {k: v for k, v in _files(source).items() if ".DS_Store" not in k}


def test_before_swap_runs_once_on_change_with_old_bytes_visible(source, dest):
    sync_core_copy(source, dest)
    (source / "records/a.md").write_bytes(b"alpha v2\n")
    seen = []

    def hook():
        seen.append((dest / "records/a.md").read_bytes())

    assert sync_core_copy(source, dest, before_swap=hook) is True
    assert seen == [b"alpha\n"]


def test_before_swap_not_called_on_noop(source, dest):
    sync_core_copy(source, dest)
    calls = []

    assert sync_core_copy(source, dest, before_swap=lambda: calls.append(1)) is False
    assert calls == []


def test_before_swap_not_called_when_refused(source, dest):
    (source / "link.md").symlink_to(source / "README.md")
    calls = []

    with pytest.raises(CoreVaultSyncError):
        sync_core_copy(source, dest, before_swap=lambda: calls.append(1))
    assert calls == []
