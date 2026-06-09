"""Slice 3 tests: CREATE new notes into YYYY-MM month buckets (lore toolchain).

New session notes (sessions.ensure_session_note) and new plan/spec notes
(`lore new plan|spec`) are born at ``<folder>/<YYYY-MM>/<filename>.md`` with the
month derived from the creation date and the month dir auto-created. Filenames
are unchanged. Resume/idempotency and `_unique_path` collision handling keep
working inside the bucket.

All fixtures are SYNTHETIC (invented widget/alpha/gadget vocabulary).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import CLI_PATH, load_script


def run_cli(args, env=None, cwd=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env,
        cwd=str(cwd) if cwd else None,
    )


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for d in ("specs", "plans", "sessions"):
        (vault / d).mkdir(parents=True)
    return vault


# ---------------------------------------------------------------------------
# vault.bucket_dir helper
# ---------------------------------------------------------------------------

class TestBucketDir:
    def test_appends_year_month(self):
        vault_mod = load_script("vault")
        folder = Path("/tmp/widgets/sessions")
        assert vault_mod.bucket_dir(folder, "2026-06-15T09:30:00Z") == folder / "2026-06"

    def test_accepts_bare_date(self):
        vault_mod = load_script("vault")
        folder = Path("/tmp/widgets/plans")
        assert vault_mod.bucket_dir(folder, "2026-11-03") == folder / "2026-11"


# ---------------------------------------------------------------------------
# sessions.ensure_session_note creation
# ---------------------------------------------------------------------------

class TestEnsureSessionNoteCreation:
    def _ensure(self, sessions, vault, *, worktree="alpha", now_iso="2026-06-15T09:30:00Z"):
        return sessions.ensure_session_note(
            vault=vault,
            worktree_name=worktree,
            branch="feature-branch",
            project="widget-project",
            now_iso=now_iso,
            now_human="June 15, 2026 09:30 UTC",
        )

    def test_creates_in_month_bucket(self, tmp_path):
        sessions = load_script("sessions")
        vault = _make_vault(tmp_path)
        note, created = self._ensure(sessions, vault, now_iso="2026-06-15T09:30:00Z")
        assert created is True
        assert note == vault / "sessions" / "2026-06" / "2026-06-15-0930-alpha.md"
        assert note.exists()

    def test_month_dir_auto_created(self, tmp_path):
        sessions = load_script("sessions")
        vault = _make_vault(tmp_path)
        assert not (vault / "sessions" / "2026-06").exists()
        self._ensure(sessions, vault, now_iso="2026-06-15T09:30:00Z")
        assert (vault / "sessions" / "2026-06").is_dir()

    def test_reuse_within_window_no_duplicate(self, tmp_path):
        sessions = load_script("sessions")
        vault = _make_vault(tmp_path)
        first, created1 = self._ensure(sessions, vault, now_iso="2026-06-15T09:30:00Z")
        second, created2 = self._ensure(sessions, vault, now_iso="2026-06-15T09:45:00Z")
        assert created1 is True
        assert created2 is False
        assert second == first
        all_notes = list((vault / "sessions").rglob("*.md"))
        assert all_notes == [first]


# ---------------------------------------------------------------------------
# Month rollover (injected now_iso, not wall clock)
# ---------------------------------------------------------------------------

class TestMonthRollover:
    def _ensure(self, sessions, vault, *, worktree, now_iso):
        return sessions.ensure_session_note(
            vault=vault,
            worktree_name=worktree,
            branch="feature-branch",
            project="widget-project",
            now_iso=now_iso,
            now_human="injected",
        )

    def test_end_of_month_lands_in_june(self, tmp_path):
        sessions = load_script("sessions")
        vault = _make_vault(tmp_path)
        note, _ = self._ensure(sessions, vault, worktree="rollover", now_iso="2026-06-30T23:59:00Z")
        assert note.parent == vault / "sessions" / "2026-06"

    def test_start_of_next_month_lands_in_july(self, tmp_path):
        sessions = load_script("sessions")
        vault = _make_vault(tmp_path)
        note, _ = self._ensure(sessions, vault, worktree="rollover", now_iso="2026-07-01T00:01:00Z")
        assert note.parent == vault / "sessions" / "2026-07"


# ---------------------------------------------------------------------------
# Bucket dir creation is race-safe
# ---------------------------------------------------------------------------

class TestBucketDirRaceSafe:
    def test_double_create_does_not_crash(self, tmp_path):
        vault_mod = load_script("vault")
        target = vault_mod.bucket_dir(tmp_path / "sessions", "2026-06-15T09:30:00Z")
        target.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
        assert target.is_dir()


# ---------------------------------------------------------------------------
# lore new plan|spec creation into bucket
# ---------------------------------------------------------------------------

class TestNewPlanSpecBucket:
    def test_plan_writes_into_month_bucket(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "plan", "--vault", str(vault),
             "--title", "Widget Rollout", "--project", "widget-project"],
            env={"LORE_TODAY": "2026-06-15"},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        bucket = vault / "plans" / "2026-06"
        notes = list(bucket.glob("*.md"))
        assert len(notes) == 1
        assert notes[0].name == "2026-06-15-widget-rollout.md"
        # Nothing left flat at the folder root.
        assert list((vault / "plans").glob("*.md")) == []

    def test_spec_writes_into_month_bucket(self, tmp_path):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", "spec", "--vault", str(vault),
             "--title", "Gadget Spec", "--project", "widget-project"],
            env={"LORE_TODAY": "2026-06-15"},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        notes = list((vault / "specs" / "2026-06").glob("*.md"))
        assert len(notes) == 1
        assert notes[0].name == "2026-06-15-gadget-spec.md"

    def test_unique_path_dedupes_inside_bucket(self, tmp_path):
        vault = _make_vault(tmp_path)
        for _ in range(2):
            r = run_cli(
                ["new", "plan", "--vault", str(vault),
                 "--title", "Same Slug", "--project", "widget-project"],
                env={"LORE_TODAY": "2026-06-15"},
            )
            assert r.returncode == 0, r.stderr + r.stdout
        bucket = vault / "plans" / "2026-06"
        names = sorted(p.name for p in bucket.glob("*.md"))
        assert names == ["2026-06-15-same-slug-2.md", "2026-06-15-same-slug.md"]

    def test_subsystem_stays_flat(self, tmp_path):
        """Out-of-scope note types (subsystem) must NOT bucket."""
        vault = _make_vault(tmp_path)
        (vault / "subsystems").mkdir(parents=True, exist_ok=True)
        r = run_cli(
            ["new", "subsystem", "--vault", str(vault), "--title", "Foo Widget"],
            env={"LORE_TODAY": "2026-06-15"},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert (vault / "subsystems" / "foo-widget.md").exists()
        assert not (vault / "subsystems" / "2026-06").exists()


# ---------------------------------------------------------------------------
# Slice 6: living folders (deferred/decision/radar/dead-end) bucket on create
# ---------------------------------------------------------------------------

class TestNewLivingFolderBucket:
    @pytest.mark.parametrize(
        "note_type,folder",
        [
            ("deferred", "deferred"),
            ("decision", "decisions"),
            ("radar", "radar"),
            ("dead-end", "dead-ends"),
        ],
    )
    def test_writes_into_month_bucket(self, tmp_path, note_type, folder):
        vault = _make_vault(tmp_path)
        r = run_cli(
            ["new", note_type, "--vault", str(vault),
             "--title", "Widget Thing", "--project", "widget-project"],
            env={"LORE_TODAY": "2026-06-15"},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        bucket = vault / folder / "2026-06"
        notes = list(bucket.glob("*.md"))
        assert len(notes) == 1, f"{note_type}: expected 1 note in bucket, got {notes}"
        assert notes[0].name == "2026-06-15-widget-thing.md"
        # Nothing left flat at the folder root.
        assert list((vault / folder).glob("*.md")) == []

    @pytest.mark.parametrize(
        "note_type,folder",
        [
            ("deferred", "deferred"),
            ("decision", "decisions"),
            ("radar", "radar"),
            ("dead-end", "dead-ends"),
        ],
    )
    def test_unique_path_dedupes_inside_bucket(self, tmp_path, note_type, folder):
        vault = _make_vault(tmp_path)
        for _ in range(2):
            r = run_cli(
                ["new", note_type, "--vault", str(vault),
                 "--title", "Same Slug", "--project", "widget-project"],
                env={"LORE_TODAY": "2026-06-15"},
            )
            assert r.returncode == 0, r.stderr + r.stdout
        bucket = vault / folder / "2026-06"
        names = sorted(p.name for p in bucket.glob("*.md"))
        assert names == ["2026-06-15-same-slug-2.md", "2026-06-15-same-slug.md"]

    def test_month_dir_auto_created(self, tmp_path):
        """The bucket dir does not need to pre-exist — mkdir creates it."""
        vault = _make_vault(tmp_path)
        assert not (vault / "deferred").exists()
        r = run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "No Dir Yet", "--project", "widget-project"],
            env={"LORE_TODAY": "2026-06-15"},
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert (vault / "deferred" / "2026-06").is_dir()

    def test_session_backlink_uses_bucketed_path(self, tmp_path):
        """The backlink written into the session note must reference the
        bucketed path (deferred/2026-06/<stem>), not the now-broken flat
        deferred/<stem> form — otherwise the wikilink won't resolve."""
        vault = _make_vault(tmp_path)
        worktree = tmp_path / "widget-wt"
        worktree.mkdir()
        # A session note whose stem encodes this worktree, so the backlink fires.
        session = vault / "sessions" / "2026-06" / "2026-06-15-0900-widget-wt.md"
        session.parent.mkdir(parents=True, exist_ok=True)
        session.write_text(
            "---\ntype: session\nworktree: widget-wt\nstatus: active\n---\n\n"
            "# Session\n\n## Deferred\n\n## Learned\n\n## Decided\n"
        )
        r = run_cli(
            ["new", "deferred", "--vault", str(vault),
             "--title", "Backlink Target", "--project", "widget-project"],
            env={"LORE_TODAY": "2026-06-15"},
            cwd=worktree,
        )
        assert r.returncode == 0, r.stderr + r.stdout
        content = session.read_text()
        assert "[[deferred/2026-06/2026-06-15-backlink-target]]" in content
        assert "[[deferred/2026-06-15-backlink-target]]" not in content
