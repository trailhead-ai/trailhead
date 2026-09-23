"""Tests for launch/lasttouched.py — "last touched" resolution for `camp list`/`ls`.

Test contract:
- A worktree's gitdir resolves through a `.git` FILE (`gitdir: <path>`,
  relative or absolute) exactly like a linked worktree's, and through a
  `.git` DIRECTORY exactly like an ordinary repo's.
- `worktree_activity_mtime` is the max of the gitdir's `index` and
  `logs/HEAD` mtimes; either missing is skipped silently; both missing (or
  no resolvable gitdir at all) is `None`.
- `workspace_last_touched` is the max of tmux activity, every member's
  worktree activity mtime, and the manifest's own mtime; all three absent
  is `None`. No git subprocess is invoked — resolution is by reading files
  under the gitdir only.
- `to_iso_utc` / `from_iso_utc` round-trip an epoch value; both are `None`
  for `None`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _import():
    from camp.launch import lasttouched

    return lasttouched


def test_gitdir_file_form_resolves_relative_gitdir(tmp_path):
    lasttouched = _import()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    real_gitdir = tmp_path / "real-gitdir"
    real_gitdir.mkdir()
    (worktree / ".git").write_text("gitdir: ../real-gitdir\n")

    resolved = lasttouched.resolve_worktree_gitdir(worktree)

    assert resolved == real_gitdir


def test_gitdir_directory_form_resolves_directly(tmp_path):
    lasttouched = _import()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").mkdir()

    resolved = lasttouched.resolve_worktree_gitdir(worktree)

    assert resolved == worktree / ".git"


def test_activity_mtime_is_max_of_index_and_logs_head():
    lasttouched = _import()

    def fake_resolve(path):
        return Path("/fake/gitdir")

    calls = {}

    def fake_stat_mtime(path):
        calls[str(path)] = True
        if path == Path("/fake/gitdir/index"):
            return 100.0
        if path == Path("/fake/gitdir/logs/HEAD"):
            return 200.0
        raise OSError("missing")

    result = lasttouched.worktree_activity_mtime(
        Path("/irrelevant"), resolve_gitdir=fake_resolve, stat_mtime=fake_stat_mtime
    )

    assert result == 200.0


def test_activity_mtime_skips_missing_file_silently():
    lasttouched = _import()

    def fake_resolve(path):
        return Path("/fake/gitdir")

    def fake_stat_mtime(path):
        if path == Path("/fake/gitdir/index"):
            return 100.0
        raise OSError("no logs/HEAD")

    result = lasttouched.worktree_activity_mtime(
        Path("/irrelevant"), resolve_gitdir=fake_resolve, stat_mtime=fake_stat_mtime
    )

    assert result == 100.0


def test_activity_mtime_is_none_when_gitdir_unresolvable():
    lasttouched = _import()
    result = lasttouched.worktree_activity_mtime(
        Path("/irrelevant"), resolve_gitdir=lambda p: None
    )
    assert result is None


def test_workspace_last_touched_is_max_across_all_three_sources(tmp_path):
    lasttouched = _import()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    os.utime(manifest, (1000, 1000))

    result = lasttouched.workspace_last_touched(
        tmux_activity=500.0,
        member_worktree_paths=[],
        manifest_path=manifest,
    )

    assert result == 1000.0


def test_workspace_last_touched_none_when_nothing_observable(tmp_path):
    lasttouched = _import()
    missing_manifest = tmp_path / "does-not-exist.json"

    result = lasttouched.workspace_last_touched(
        tmux_activity=None,
        member_worktree_paths=[],
        manifest_path=missing_manifest,
    )

    assert result is None


def test_iso_round_trip_and_none_passthrough():
    lasttouched = _import()
    epoch = 1_700_000_000.0

    iso = lasttouched.to_iso_utc(epoch)
    assert isinstance(iso, str)
    assert lasttouched.from_iso_utc(iso) == epoch

    assert lasttouched.to_iso_utc(None) is None
    assert lasttouched.from_iso_utc(None) is None
