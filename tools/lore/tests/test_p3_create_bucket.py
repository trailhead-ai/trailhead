"""Slice 3 tests: CREATE new session notes into YYYY-MM month buckets.

New session notes (sessions.ensure_session_note) are born at
``<folder>/<YYYY-MM>/<filename>.md`` with the month derived from the creation
date and the month dir auto-created. Filenames are unchanged. Resume/idempotency
keeps working inside the bucket.

The `lore new plan|spec` date-bucketing and living-folder tests that lived here
were removed in Slice 4 alongside the `lore new` template-renderer: the surviving
record surface (`lore record create`) is body-agnostic and writes flat under
``<kind>/`` with no date prefix or session backlink, so that bucketing/backlink
behavior no longer exists to assert.

All fixtures are SYNTHETIC (invented widget/alpha/gadget vocabulary).
"""

from __future__ import annotations

from pathlib import Path

from conftest import load_script


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
