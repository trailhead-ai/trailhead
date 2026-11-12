"""Tests for trailhead/core_vault.py — the bundled core-vault copy sync."""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from trailhead.core_vault import MARKER_NAME, CoreVaultSyncError, core_copy_dir, install_core_vault, sync_core_copy


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


def _stamps(paths: list[Path]) -> dict[Path, tuple[int, int]]:
    return {p: (p.stat().st_ino, p.stat().st_mtime_ns) for p in paths}


def _siblings(dest: Path) -> list[str]:
    return sorted(p.name for p in dest.parent.iterdir() if p.name != dest.name)


def test_first_sync_creates_tree_and_reports_changed(source, dest):
    assert sync_core_copy(source, dest) is True
    assert _files(dest) == _files(source)


def test_unchanged_sync_reports_unchanged_and_touches_nothing(source, dest):
    sync_core_copy(source, dest)
    paths = [dest, *sorted(dest.rglob("*"))]
    before = _stamps(paths)
    parent_before = sorted(os.listdir(dest.parent))

    assert sync_core_copy(source, dest) is False

    assert _stamps(paths) == before
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


@pytest.mark.parametrize("prefix", ["staging", "old"])
@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_non_directory_leftover_sibling_is_removed_and_sync_succeeds(prefix, kind, source, dest, tmp_path):
    sync_core_copy(source, dest)
    (source / "records/a.md").write_bytes(b"alpha v2\n")
    victim = _write(tmp_path / "victim", {"v.md": b"v"})
    leftover = dest.parent / f".{dest.name}.{prefix}-evil"
    if kind == "symlink":
        leftover.symlink_to(victim)
    else:
        leftover.write_bytes(b"f")

    assert sync_core_copy(source, dest) is True

    assert _siblings(dest) == []
    assert _files(dest) == _files(source)
    assert _files(victim) == {"v.md": b"v"}


def test_non_directory_leftover_sibling_does_not_block_first_sync(source, dest):
    dest.parent.mkdir(parents=True)
    (dest.parent / f".{dest.name}.staging-file").write_bytes(b"f")

    assert sync_core_copy(source, dest) is True

    assert _siblings(dest) == []
    assert _files(dest) == _files(source)


def _tree(root: Path) -> dict[str, object]:
    """Every entry under root, dot-names included, symlinks reported not followed."""
    out: dict[str, object] = {}
    for current, dirs, names in os.walk(root, followlinks=False):
        for name in [*dirs, *names]:
            p = Path(current) / name
            rel = str(p.relative_to(root))
            if p.is_symlink():
                out[rel] = ("link", os.readlink(p))
            elif p.is_dir():
                out[rel] = "dir"
            else:
                out[rel] = p.read_bytes()
    return out


def _plant_dot_kind(dest, outside):
    _write(dest, {".kind/z.json": b"{}", ".kind/z.md": b"z\n"})


def _plant_dot_record(dest, outside):
    _write(dest, {"records/.h.json": b"{}", "records/.h.md": b"h\n"})


def _plant_dot_symlink(dest, outside):
    (dest / ".link").symlink_to(outside)


def _plant_extra_file(dest, outside):
    _write(dest, {"extra.md": b"extra\n"})


def _plant_empty_dir(dest, outside):
    (dest / "emptykind").mkdir()


@pytest.mark.parametrize(
    "plant",
    [_plant_dot_kind, _plant_dot_record, _plant_dot_symlink, _plant_extra_file, _plant_empty_dir],
    ids=lambda f: f.__name__,
)
def test_planted_dest_entry_makes_the_copy_differ_and_is_rebuilt(plant, source, dest, tmp_path):
    sync_core_copy(source, dest)
    outside = _write(tmp_path / "outside", {"o.md": b"outside\n"})
    plant(dest, outside)
    assert _tree(dest) != _tree(source)

    assert sync_core_copy(source, dest) is True

    assert _tree(dest) == _tree(source)
    assert _tree(outside) == {"o.md": b"outside\n"}
    assert _siblings(dest) == []


def test_dot_files_ignored_in_compare_and_copy(source, dest):
    sync_core_copy(source, dest)
    _write(source, {".DS_Store": b"junk", "records/.DS_Store": b"junk"})

    assert sync_core_copy(source, dest) is False
    (source / "records/a.md").write_bytes(b"alpha v2\n")
    assert sync_core_copy(source, dest) is True
    assert _files(dest) == {k: v for k, v in _files(source).items() if ".DS_Store" not in k}


def test_fd_level_non_regular_check_refuses_even_after_the_lstat_passed(source, dest):
    sync_core_copy(source, dest)
    (source / "records/a.md").write_bytes(b"alpha v2\n")
    before = _tree(dest)

    def fifo_fstat(fd):
        return os.stat_result((stat.S_IFIFO | 0o644, 0, 0, 1, 0, 0, 0, 0, 0, 0))

    with pytest.raises(CoreVaultSyncError, match="non-regular entry"):
        sync_core_copy(source, dest, fstat=fifo_fstat)

    assert _tree(dest) == before
    assert _siblings(dest) == []


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


# ---------------------------------------------------------------------------
# install_core_vault — driven through an injected lore runner, real disk
# ---------------------------------------------------------------------------

NOTE = (
    "Note: older lore releases refuse a config carrying this read-only entry; "
    "remove it with 'lore vault delete core' (or delete the entry from config.json by hand) to undo."
)
LORE_BIN = Path("/fake/bin/lore")


def _entry(name: str, path, *, scope="read-only", shared=False) -> dict:
    return {"name": name, "scope": scope, "shared": shared, "path": path, "records": 0}


class FakeLore:
    """Scripted lore CLI: records argv, answers `vault ls --json`, `vault add`, `reindex`."""

    def __init__(self, vaults=None, *, ls_rc=0, ls_out=None, add_rc=0, reindex_rc=0):
        self.vaults = list(vaults or [])
        self.ls_rc, self.ls_out = ls_rc, ls_out
        self.add_rc, self.reindex_rc = add_rc, reindex_rc
        self.calls: list[list[str]] = []

    def __call__(self, args, **kw):
        sub = [str(a) for a in args[1:]]
        self.calls.append(sub)

        def done(rc, out="", err=""):
            return subprocess.CompletedProcess(args, rc, stdout=out, stderr=err)

        if sub == ["vault", "ls", "--json"]:
            out = self.ls_out if self.ls_out is not None else json.dumps(self.vaults)
            return done(self.ls_rc, out, "lore: SECRET-LS-STDERR" if self.ls_rc else "")
        if sub[:2] == ["vault", "add"]:
            if self.add_rc:
                return done(self.add_rc, "", "lore: SECRET-ADD-STDERR")
            self.vaults.append(_entry(sub[2], sub[sub.index("--path") + 1]))
            return done(0, f"Added vault 'core' (read-only); indexed 1 record(s).\n{NOTE}\n")
        if sub == ["reindex"]:
            return done(self.reindex_rc, "indexed 1\n", "lore: SECRET-REINDEX-STDERR" if self.reindex_rc else "")
        raise AssertionError(f"unexpected lore call {sub}")

    @property
    def writes(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["vault", "add"] or c == ["reindex"]]


@pytest.fixture
def cenv(tmp_path):
    return _fenced_env(tmp_path)


@pytest.fixture
def copy(cenv):
    return core_copy_dir(cenv)


def _run(source, cenv, lore, capsys):
    out = install_core_vault(source=source, env=cenv, lore_bin=LORE_BIN, runner=lore)
    cap = capsys.readouterr()
    return out, cap.out + cap.err


def test_registers_when_no_core_vault(source, cenv, copy, capsys):
    lore = FakeLore([])
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "registered"
    assert _files(copy) == _files(source)
    assert ["vault", "add", "core", "--scope", "read-only", "--trusted", "--path", str(copy.resolve())] in lore.calls
    assert ["reindex"] not in lore.calls
    assert "registered" in text and str(copy) in text
    assert NOTE in text


def test_unchanged_rerun_is_up_to_date_and_touches_nothing(source, cenv, copy, capsys):
    lore = FakeLore([])
    _run(source, cenv, lore, capsys)
    paths = [copy, *sorted(copy.rglob("*"))]
    before = _stamps(paths)
    lore.calls.clear()
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "up_to_date"
    assert lore.writes == []
    assert _stamps(paths) == before
    assert "up to date" in text


def test_changed_source_refreshes_and_reindexes_once(source, cenv, copy, capsys):
    lore = FakeLore([])
    _run(source, cenv, lore, capsys)
    (source / "records" / "a.md").write_bytes(b"alpha edited\n")
    lore.calls.clear()
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "refreshed"
    assert (copy / "records" / "a.md").read_bytes() == b"alpha edited\n"
    assert lore.writes == [["reindex"]]
    assert not (copy.parent / MARKER_NAME).exists()
    assert "refreshed" in text


@pytest.mark.parametrize(
    "case",
    ["core-elsewhere", "core-writable", "core-shared", "alias-same-path", "alias-parent", "core-parent"],
)
def test_declines_without_writing_anything(case, source, cenv, copy, tmp_path, capsys):
    real = str(copy.resolve())
    clash = {
        "core-elsewhere": _entry("core", str(tmp_path / "other")),
        "core-writable": _entry("core", real, scope="team"),
        "core-shared": _entry("core", real, shared=True),
        "alias-same-path": _entry("mine", real),
        "alias-parent": _entry("mine", str(copy.resolve().parent)),
        "core-parent": _entry("core", str(copy.resolve().parent)),
    }[case]
    lore = FakeLore([clash])
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "declined"
    assert lore.writes == []
    assert not copy.exists() and not copy.parent.exists()
    assert clash["path"] in text
    assert "rename or remove" in text.lower()
    assert "not registered" in text


def test_decline_reason_differs_between_core_and_alias(source, cenv, copy, tmp_path, capsys):
    real = str(copy.resolve())
    a, _ = _run(source, cenv, FakeLore([_entry("core", str(tmp_path / "o"))]), capsys)
    b, _ = _run(source, cenv, FakeLore([_entry("mine", real)]), capsys)
    assert a.outcome == b.outcome == "declined"
    assert "named core" in a.reason and "mine" not in a.reason
    assert "mine" in b.reason


def test_symlinked_state_dir_does_not_falsely_decline(source, tmp_path, capsys):
    real_state = tmp_path / "real-state"
    real_state.mkdir()
    link = tmp_path / "link-state"
    link.symlink_to(real_state)
    env = _fenced_env(tmp_path, state="link-state")
    copy = core_copy_dir(env)
    lore = FakeLore([_entry("core", str(real_state / "trailhead" / "core-vault"))])
    out, _ = _run(source, env, lore, capsys)
    assert out.outcome == "refreshed"
    assert _files(copy) == _files(source)
    assert not any(c[:2] == ["vault", "add"] for c in lore.calls)


def test_core_registered_through_a_symlink_to_the_copy_is_owned(source, cenv, copy, tmp_path, capsys):
    lore = FakeLore([])
    _run(source, cenv, lore, capsys)
    alias = tmp_path / "alias-to-copy"
    alias.symlink_to(copy)
    lore = FakeLore([_entry("core", str(alias))])
    out, _ = _run(source, cenv, lore, capsys)
    assert out.outcome == "up_to_date"
    assert lore.writes == []


@pytest.mark.parametrize(
    "vaults",
    [
        [{"name": "core", "scope": "read-only", "shared": False}],
        [_entry("core", None)],
        [_entry("core", 7)],
        [_entry("core", "relative/path")],
        [_entry("other", None)],
        ["not-a-dict"],
    ],
)
def test_uncomparable_vault_path_fails_safe(vaults, source, cenv, copy, capsys):
    lore = FakeLore(vaults)
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "failed"
    assert lore.writes == []
    assert not copy.parent.exists()
    assert "vault ls" in text


def test_symlink_loop_vault_path_fails_safe(source, cenv, copy, tmp_path, capsys):
    (tmp_path / "loop-a").symlink_to(tmp_path / "loop-b")
    (tmp_path / "loop-b").symlink_to(tmp_path / "loop-a")
    lore = FakeLore([_entry("other", str(tmp_path / "loop-a"))])
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "failed"
    assert lore.writes == []
    assert not copy.parent.exists()
    assert "vault ls" in text and "install otherwise succeeded" in text
    assert "path could not be resolved" in out.reason
    assert "expected JSON" not in out.reason
    assert "SECRET-LS-STDERR" not in text


def test_symlink_loop_below_a_vault_path_fails_safe(source, cenv, copy, tmp_path, capsys):
    (tmp_path / "loop-a").symlink_to(tmp_path / "loop-b")
    (tmp_path / "loop-b").symlink_to(tmp_path / "loop-a")
    lore = FakeLore([_entry("other", str(tmp_path / "loop-a" / "deeper"))])
    out, _ = _run(source, cenv, lore, capsys)
    assert out.outcome == "failed"
    assert "path could not be resolved" in out.reason
    assert lore.writes == []
    assert not copy.parent.exists()


def test_nul_byte_vault_path_fails_safe(source, cenv, copy, capsys):
    lore = FakeLore([_entry("other", "/tmp/a\x00b")])
    out, _ = _run(source, cenv, lore, capsys)
    assert out.outcome == "failed"
    assert "path could not be resolved" in out.reason
    assert lore.writes == []
    assert not copy.parent.exists()


def test_malformed_vault_ls_json_keeps_its_own_reason(source, cenv, copy, capsys):
    lore = FakeLore(ls_out="{not json")
    out, _ = _run(source, cenv, lore, capsys)
    assert out.outcome == "failed"
    assert "did not print the expected JSON" in out.reason
    assert "could not be resolved" not in out.reason


@pytest.mark.parametrize("rel", ["not-yet/core", "not-yet/deeper/still/core"])
def test_core_vault_at_a_missing_valid_path_still_declines(rel, source, cenv, copy, tmp_path, capsys):
    missing = tmp_path / rel
    lore = FakeLore([_entry("core", str(missing))])
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "declined"
    assert "already exists and is not trailhead's copy" in out.reason
    assert str(missing) in text
    assert lore.writes == []
    assert not copy.parent.exists()


def test_unresolvable_state_dir_fails_safe_naming_the_copy(source, tmp_path, capsys):
    env = {**_fenced_env(tmp_path), "TRAILHEAD_STATE_DIR": "relative/state"}
    lore = FakeLore([])
    out, text = _run(source, env, lore, capsys)
    assert out.outcome == "failed"
    assert out.reason.startswith("copy ")
    assert lore.calls == []
    assert "install otherwise succeeded" in text


def test_symlink_loop_in_the_copy_location_fails_safe(source, tmp_path, capsys):
    env = _fenced_env(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "trailhead").symlink_to(tmp_path / "state" / "trailhead")
    lore = FakeLore([])
    out, text = _run(source, env, lore, capsys)
    assert out.outcome == "failed"
    assert lore.calls == []
    assert out.reason.startswith("copy ")
    assert "install otherwise succeeded" in text


@pytest.mark.parametrize("name", ["mine", "core"])
def test_vault_nested_inside_the_copy_is_declined(name, source, cenv, copy, capsys):
    nested = copy.resolve() / "records"
    lore = FakeLore([_entry(name, str(nested))])
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "declined"
    assert lore.writes == []
    assert not copy.exists() and not copy.parent.exists()
    assert str(nested) in text
    assert "inside trailhead's copy" in out.reason
    assert "rename or remove" in text.lower()


def test_vault_beside_the_copy_with_a_shared_name_prefix_is_not_declined(source, cenv, copy, capsys):
    beside = copy.resolve().parent / "core-vault-extra"
    lore = FakeLore([_entry("mine", str(beside))])
    out, _ = _run(source, cenv, lore, capsys)
    assert out.outcome == "registered"


class _Boom(FakeLore):
    def __init__(self, *args, fail_on, **kw):
        super().__init__(*args, **kw)
        self.fail_on = fail_on

    def __call__(self, args, **kw):
        if [str(a) for a in args[1:]][: len(self.fail_on)] == self.fail_on:
            raise FileNotFoundError(2, "No such file or directory", "/fake/bin/lore")
        return super().__call__(args, **kw)


@pytest.mark.parametrize(
    ("fail_on", "step", "setup"),
    [(["vault", "ls"], "vault ls", "none"), (["vault", "add"], "vault add", "none"), (["reindex"], "reindex", "owned")],
)
def test_lore_binary_oserror_fails_safe(fail_on, step, setup, source, cenv, copy, capsys):
    lore = _Boom([], fail_on=fail_on)
    if setup == "owned":
        _run(source, cenv, lore.__class__([], fail_on=["never"]), capsys)
        lore.vaults = [_entry("core", str(copy.resolve()))]
        (source / "records" / "a.md").write_bytes(b"alpha changed\n")
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "failed"
    assert step in out.reason and "FileNotFoundError" in out.reason
    assert "install otherwise succeeded" in text


@pytest.mark.parametrize("path", ["registered", "refreshed"])
def test_unlinkable_marker_fails_safe(path, source, cenv, copy, capsys):
    marker = copy.parent / MARKER_NAME
    if path == "registered":
        lore = FakeLore([])
    else:
        lore = FakeLore([])
        _run(source, cenv, lore, capsys)
    marker.mkdir(parents=True, exist_ok=True)
    (marker / "keep").write_text("x")
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "failed"
    assert "marker" in out.reason
    assert "install otherwise succeeded" in text


@pytest.mark.parametrize(
    "kwargs",
    [{"ls_rc": 2}, {"ls_out": "not json at all"}, {"ls_out": '{"a": 1}'}],
    ids=["nonzero", "non-json", "not-a-list"],
)
def test_vault_ls_problems_warn_and_write_nothing(kwargs, source, cenv, copy, capsys):
    lore = FakeLore([], **kwargs)
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "failed"
    assert lore.writes == []
    assert not copy.parent.exists()
    assert text.startswith("trailhead:")
    assert "vault ls" in text
    assert "install otherwise succeeded" in text
    assert "SECRET-LS-STDERR" not in text


def test_vault_add_failure_warns_then_next_run_registers(source, cenv, copy, capsys):
    lore = FakeLore([], add_rc=1)
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "failed"
    assert _files(copy) == _files(source)
    assert "vault add" in text and "install otherwise succeeded" in text
    assert "SECRET-ADD-STDERR" not in text
    lore.add_rc = 0
    out, _ = _run(source, cenv, lore, capsys)
    assert out.outcome == "registered"
    assert any(c[:2] == ["vault", "add"] for c in lore.calls)


def _stale_marker(copy) -> Path:
    marker = copy.parent / MARKER_NAME
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("reindex pending\n")
    return marker


def test_stale_marker_is_cleared_by_a_fresh_registration(source, cenv, copy, capsys):
    marker = _stale_marker(copy)
    lore = FakeLore([])
    out, _ = _run(source, cenv, lore, capsys)
    assert out.outcome == "registered"
    assert any(c[:2] == ["vault", "add"] for c in lore.calls)
    assert ["reindex"] not in lore.calls
    assert not marker.exists()


def test_stale_marker_survives_a_failed_registration(source, cenv, copy, capsys):
    marker = _stale_marker(copy)
    lore = FakeLore([], add_rc=1)
    out, _ = _run(source, cenv, lore, capsys)
    assert out.outcome == "failed"
    assert marker.read_text() == "reindex pending\n"


def test_reindex_failure_keeps_marker_and_next_run_retries(source, cenv, copy, capsys):
    lore = FakeLore([])
    _run(source, cenv, lore, capsys)
    (source / "records" / "a.md").write_bytes(b"alpha v2\n")
    lore.reindex_rc = 1
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "failed"
    assert (copy.parent / MARKER_NAME).exists()
    assert "reindex" in text and "install otherwise succeeded" in text
    assert "SECRET-REINDEX-STDERR" not in text
    lore.reindex_rc = 0
    lore.calls.clear()
    out, _ = _run(source, cenv, lore, capsys)
    assert lore.writes == [["reindex"]]
    assert out.outcome == "refreshed"
    assert not (copy.parent / MARKER_NAME).exists()


def test_marker_is_written_before_the_swap(source, cenv, copy, capsys):
    lore = FakeLore([])
    _run(source, cenv, lore, capsys)
    (source / "records" / "a.md").write_bytes(b"alpha v3\n")

    def failing_rename(src, dst):
        raise OSError("swap refused")

    lore.calls.clear()
    out = install_core_vault(source=source, env=cenv, lore_bin=LORE_BIN, runner=lore, rename=failing_rename)
    capsys.readouterr()
    assert out.outcome == "failed"
    assert (copy / "records" / "a.md").read_bytes() != b"alpha v3\n"
    assert (copy.parent / MARKER_NAME).exists()
    assert lore.writes == []


def test_symlinked_marker_is_replaced_and_its_target_left_alone(source, cenv, copy, tmp_path, capsys):
    lore = FakeLore([])
    _run(source, cenv, lore, capsys)
    target = tmp_path / "victim.txt"
    target.write_bytes(b"precious\n")
    marker = copy.parent / MARKER_NAME
    marker.symlink_to(target)
    (source / "records" / "a.md").write_bytes(b"alpha v4\n")
    lore.reindex_rc = 1

    out, _ = _run(source, cenv, lore, capsys)

    assert out.outcome == "failed" and "reindex" in out.reason
    assert target.read_bytes() == b"precious\n"
    assert not marker.is_symlink()
    assert marker.read_text() == "reindex pending\n"


def test_sync_error_warns_and_registers_nothing(tmp_path, cenv, capsys):
    lore = FakeLore([])
    out, text = _run(tmp_path / "missing-source", cenv, lore, capsys)
    assert out.outcome == "failed"
    assert lore.writes == []
    assert "copy" in text and "install otherwise succeeded" in text


def test_source_with_symlink_does_not_register(source, cenv, capsys):
    (source / "link").symlink_to(source / "README.md")
    lore = FakeLore([])
    out, _ = _run(source, cenv, lore, capsys)
    assert out.outcome == "failed"
    assert lore.writes == []


# ---------------------------------------------------------------------------
# install_core_vault decision table — every input state, ordering included
# ---------------------------------------------------------------------------

_CORE_ELSEWHERE = "a vault named core already exists and is not trailhead's copy"
_CORE_NOT_RO = "a vault named core is at the copy but is not read-only and unshared"
_INSIDE = "is inside trailhead's copy"
_ALIAS = "is or contains trailhead's copy"


def _states(copy: Path, tmp_path: Path) -> dict[str, tuple[list[dict], str, str | None, Path | None]]:
    c, o, o2 = copy.resolve(), tmp_path / "o", tmp_path / "o2"
    p, i = c.parent, c / "inner"
    return {
        "empty": ([], "registered", None, None),
        "core-owned": ([_entry("core", str(c))], "refreshed", None, None),
        "core-owned-plus-unrelated": ([_entry("core", str(c)), _entry("x", str(o2))], "refreshed", None, None),
        "core-elsewhere": ([_entry("core", str(o))], "declined", _CORE_ELSEWHERE, o),
        "core-writable": ([_entry("core", str(c), scope="team")], "declined", _CORE_NOT_RO, c),
        "core-shared": ([_entry("core", str(c), shared=True)], "declined", _CORE_NOT_RO, c),
        "alias-at-copy": ([_entry("mine", str(c))], "declined", _ALIAS, c),
        "alias-parent": ([_entry("mine", str(p))], "declined", _ALIAS, p),
        "core-parent": ([_entry("core", str(p))], "declined", _CORE_ELSEWHERE, p),
        "alias-inside": ([_entry("mine", str(i))], "declined", _INSIDE, i),
        "core-inside": ([_entry("core", str(i))], "declined", _INSIDE, i),
        "core-elsewhere-then-inside": ([_entry("core", str(o)), _entry("mine", str(i))], "declined", _INSIDE, i),
        "core-elsewhere-then-alias": ([_entry("core", str(o)), _entry("mine", str(c))], "declined", _ALIAS, c),
        "owned-then-core-elsewhere": ([_entry("core", str(c)), _entry("core", str(o))], "declined", _CORE_ELSEWHERE, o),
        "core-elsewhere-then-owned": ([_entry("core", str(o)), _entry("core", str(c))], "declined", _CORE_ELSEWHERE, o),
        "inside-then-alias": ([_entry("mine", str(i)), _entry("mine", str(c))], "declined", _INSIDE, i),
        "alias-then-inside": ([_entry("mine", str(c)), _entry("mine", str(i))], "declined", _ALIAS, c),
        "owned-then-alias": ([_entry("core", str(c)), _entry("mine", str(c))], "declined", _ALIAS, c),
        "alias-then-writable-core": (
            [_entry("mine", str(c)), _entry("core", str(c), scope="team")],
            "declined",
            _ALIAS,
            c,
        ),
        "two-elsewhere-first-named": (
            [_entry("core", str(o)), _entry("core", str(o2))],
            "declined",
            _CORE_ELSEWHERE,
            o,
        ),
    }


@pytest.mark.parametrize("state", list(_states(Path("/x/core-vault"), Path("/y"))))
def test_install_decision_table(state, source, cenv, copy, tmp_path, capsys):
    vaults, outcome, reason, path = _states(copy, tmp_path)[state]
    lore = FakeLore(vaults)
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == outcome
    if reason is None:
        return
    assert reason in out.reason and f"(path: {path})" in out.reason
    assert reason in text and f"(path: {path})" in text and "Rename or remove that vault" in text
    assert lore.writes == []


def test_two_vaults_named_core_both_at_the_copy_are_declined(source, cenv, copy, capsys):
    real = str(copy.resolve())
    lore = FakeLore([_entry("core", real), _entry("core", real)])
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "declined"
    assert "more than one vault named core" in out.reason
    assert lore.writes == [] and not copy.exists()


def test_duplicate_core_decline_remedy_names_the_extra_registration_not_a_rename(source, cenv, copy, capsys):
    real = str(copy.resolve())
    lore = FakeLore([_entry("core", real), _entry("core", real)])
    out, text = _run(source, cenv, lore, capsys)
    assert out.outcome == "declined"
    assert "lore vault delete core" in text and "re-run `trailhead install`" in text
    assert "rename or remove" not in text.lower()
