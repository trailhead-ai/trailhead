"""Tests for the ``vault.bucket_dir`` YYYY-MM month-bucket helper.

The session-note month-bucket CREATE tests that lived here (via
``sessions.ensure_session_note``) were retired when the frontmatter
session-note lifecycle was removed — capture is now the singular indexed
``session/`` record, covered by test_session_records.py. The
``bucket_dir`` helper itself is still live (it computes the YYYY-MM archive
subdir for the date-bucketed folders) and stays tested here.

All fixtures are SYNTHETIC (invented widget/alpha/gadget vocabulary).
"""
from __future__ import annotations

from pathlib import Path

from conftest import load_script


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
# (sessions.ensure_session_note month-bucket creation tests were retired in
# Slice 2 — the frontmatter-note CREATE lifecycle is gone; capture is now the
# singular ``session/`` record, covered by test_session_records.py.)
# ---------------------------------------------------------------------------


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
