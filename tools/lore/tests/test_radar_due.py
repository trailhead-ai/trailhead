"""Behavioral tests for the radar-due selection helper.

The radar_due helper selects radar notes from a vault's ``radar/`` directory
that are due for polling, based on their status, source, check cadence, and
last-checked date. It also collects manual-source items separately.

Selection predicate:
  - Poll iff status == "active"
    AND (last-checked is empty → bootstrap, OR last-checked is older than cadence)
  - Skip status == "resolved" or "dropped"
  - List source == "manual" items separately as "needs human check"
  - Skip/flag unexpected legacy status (e.g. "snoozed") — do NOT crash

Cadence rules (daily / weekly):
  - "daily"  → stale if last-checked < today
  - "weekly" → stale if last-checked is older than 7 days

These tests use SYNTHETIC radar fixtures (public repo — invented vocabulary,
never real brain content or real repo slugs).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "lore" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from radar_due import RadarDueResult, radar_notes_due  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write_radar(path: Path, **fields) -> None:
    """Write a minimal synthetic radar note with the given frontmatter fields."""
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append("## What we're watching")
    lines.append("Synthetic fixture for testing.")
    path.write_text("\n".join(lines))


def _radar_dir(tmp_path: Path) -> Path:
    d = tmp_path / "radar"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Fixtures — synthetic vault with radar notes
# ---------------------------------------------------------------------------

@pytest.fixture()
def synthetic_vault(tmp_path: Path) -> Path:
    """A vault with 6 radar notes covering all predicate branches.

    (a) active + stale last-checked (daily; checked yesterday) → DUE
    (b) active + fresh last-checked (daily; checked today) → SKIP
    (c) active + empty last-checked (bootstrap) → DUE
    (d) resolved → SKIP
    (e) dropped → SKIP
    (f) source: manual, status: active → MANUAL (not polled)
    """
    radar = _radar_dir(tmp_path)

    # (a) active + stale: checked yesterday, daily cadence → due
    _write_radar(
        radar / "alpha.md",
        type="radar",
        project="acme",
        status="active",
        source="github-release",
        target="<owner/repo>",
        check="daily",
        added="2026-01-01",
        **{"last-checked": "2026-05-31", "last-state": "v1.0"},
    )
    # (b) active + fresh: checked today, daily cadence → not due
    _write_radar(
        radar / "beta.md",
        type="radar",
        project="acme",
        status="active",
        source="npm",
        target="some-pkg",
        check="daily",
        added="2026-01-01",
        **{"last-checked": "2026-06-01", "last-state": "1.2.3"},
    )
    # (c) active + empty last-checked (bootstrap) → due
    _write_radar(
        radar / "gamma.md",
        type="radar",
        project="acme",
        status="active",
        source="github-issue",
        target="<owner/repo> 42",
        check="weekly",
        added="2026-01-01",
        **{"last-checked": "", "last-state": ""},
    )
    # (d) resolved → always skip
    _write_radar(
        radar / "delta.md",
        type="radar",
        project="acme",
        status="resolved",
        source="github-pr",
        target="<owner/repo> 7",
        check="daily",
        added="2026-01-01",
        **{"last-checked": "2026-05-01", "last-state": "merged"},
    )
    # (e) dropped → always skip
    _write_radar(
        radar / "epsilon.md",
        type="radar",
        project="acme",
        status="dropped",
        source="url",
        target="https://example.invalid",
        check="weekly",
        added="2026-01-01",
        **{"last-checked": "2026-04-01", "last-state": ""},
    )
    # (f) source: manual, status: active → list as manual, do not poll
    _write_radar(
        radar / "zeta.md",
        type="radar",
        project="acme",
        status="active",
        source="manual",
        target="n/a",
        check="weekly",
        added="2026-01-01",
        **{"last-checked": "", "last-state": ""},
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Selection-predicate tests (the 6-case fixture)
# ---------------------------------------------------------------------------

def test_active_stale_is_selected(synthetic_vault: Path):
    """(a) active + stale last-checked → in due list."""
    result = radar_notes_due(synthetic_vault, today=date(2026, 6, 1))
    names = {p.name for p in result.due}
    assert "alpha.md" in names


def test_active_fresh_is_skipped(synthetic_vault: Path):
    """(b) active + fresh last-checked (checked today) → not in due list."""
    result = radar_notes_due(synthetic_vault, today=date(2026, 6, 1))
    names = {p.name for p in result.due}
    assert "beta.md" not in names


def test_active_empty_last_checked_is_selected(synthetic_vault: Path):
    """(c) active + empty last-checked (bootstrap) → in due list."""
    result = radar_notes_due(synthetic_vault, today=date(2026, 6, 1))
    names = {p.name for p in result.due}
    assert "gamma.md" in names


def test_resolved_is_skipped(synthetic_vault: Path):
    """(d) resolved → not in due list and not in manual list."""
    result = radar_notes_due(synthetic_vault, today=date(2026, 6, 1))
    all_names = {p.name for p in result.due} | {p.name for p in result.manual}
    assert "delta.md" not in all_names


def test_dropped_is_skipped(synthetic_vault: Path):
    """(e) dropped → not in due list and not in manual list."""
    result = radar_notes_due(synthetic_vault, today=date(2026, 6, 1))
    all_names = {p.name for p in result.due} | {p.name for p in result.manual}
    assert "epsilon.md" not in all_names


def test_manual_source_is_listed_separately(synthetic_vault: Path):
    """(f) source: manual → in manual list, not in due list."""
    result = radar_notes_due(synthetic_vault, today=date(2026, 6, 1))
    due_names = {p.name for p in result.due}
    manual_names = {p.name for p in result.manual}
    assert "zeta.md" not in due_names
    assert "zeta.md" in manual_names


def test_exact_due_set(synthetic_vault: Path):
    """Combined: only {a, c} are due; {f} is manual; {b, d, e} absent from both."""
    result = radar_notes_due(synthetic_vault, today=date(2026, 6, 1))
    due_names = {p.name for p in result.due}
    assert due_names == {"alpha.md", "gamma.md"}


# ---------------------------------------------------------------------------
# Cadence tests (weekly boundary)
# ---------------------------------------------------------------------------

def test_weekly_stale_7_days_ago_is_due(tmp_path: Path):
    """Weekly item checked exactly 7 days ago is stale (older than 7 days: strictly > 7)."""
    radar = _radar_dir(tmp_path)
    # last-checked 7 days ago: 2026-05-25, today: 2026-06-01 → 7 days → NOT due
    # (stale only if strictly more than 7 days old)
    _write_radar(
        radar / "watch.md",
        type="radar",
        project="acme",
        status="active",
        source="npm",
        target="some-pkg",
        check="weekly",
        added="2026-01-01",
        **{"last-checked": "2026-05-25", "last-state": "1.0"},
    )
    result = radar_notes_due(tmp_path, today=date(2026, 6, 1))
    # 2026-05-25 is 7 days before 2026-06-01: not stale yet
    due_names = {p.name for p in result.due}
    assert "watch.md" not in due_names


def test_weekly_stale_8_days_ago_is_due(tmp_path: Path):
    """Weekly item checked 8 days ago is stale."""
    radar = _radar_dir(tmp_path)
    _write_radar(
        radar / "watch.md",
        type="radar",
        project="acme",
        status="active",
        source="npm",
        target="some-pkg",
        check="weekly",
        added="2026-01-01",
        **{"last-checked": "2026-05-24", "last-state": "1.0"},
    )
    result = radar_notes_due(tmp_path, today=date(2026, 6, 1))
    due_names = {p.name for p in result.due}
    assert "watch.md" in due_names


def test_empty_radar_dir_returns_empty_result(tmp_path: Path):
    """A vault with no radar/ directory returns empty result gracefully."""
    result = radar_notes_due(tmp_path, today=date(2026, 6, 1))
    assert result.due == []
    assert result.manual == []
    assert result.skipped_legacy == []


# ---------------------------------------------------------------------------
# Status-preservation tests
# ---------------------------------------------------------------------------

def test_status_preserved_after_patch(tmp_path: Path):
    """After stamping last-checked/last-state, the note's status is unchanged."""
    from pathlib import Path as P
    import frontmatter as fm

    radar = _radar_dir(tmp_path)
    note = radar / "track.md"
    _write_radar(
        note,
        type="radar",
        project="acme",
        status="active",
        source="npm",
        target="some-pkg",
        check="daily",
        added="2026-01-01",
        **{"last-checked": "2026-05-30", "last-state": "1.0"},
    )

    # Patch last-checked and last-state (simulating what the skill does)
    text = note.read_text()
    text = text.replace("last-checked: 2026-05-30", "last-checked: 2026-06-01")
    text = text.replace("last-state: 1.0", "last-state: 1.1")
    note.write_text(text)

    # Validate: status must still be in the canonical set
    from status_validator import is_valid_status
    meta = fm.parse_frontmatter(note)
    assert meta["status"] == "active"
    assert is_valid_status("radar", meta["status"])


# ---------------------------------------------------------------------------
# No-snoozed-explosion test
# ---------------------------------------------------------------------------

def test_snoozed_legacy_status_does_not_crash(tmp_path: Path):
    """A note with legacy status 'snoozed' (off lore vocab) does not crash and is not polled."""
    radar = _radar_dir(tmp_path)
    _write_radar(
        radar / "legacy.md",
        type="radar",
        project="acme",
        status="snoozed",
        source="github-issue",
        target="<owner/repo> 99",
        check="weekly",
        added="2025-01-01",
        **{"last-checked": "2025-01-01", "last-state": "open"},
    )
    # Must not raise; the note must not appear in due or manual
    result = radar_notes_due(tmp_path, today=date(2026, 6, 1))
    due_names = {p.name for p in result.due}
    manual_names = {p.name for p in result.manual}
    assert "legacy.md" not in due_names
    assert "legacy.md" not in manual_names
    # And it should be flagged in skipped_legacy
    legacy_names = {p.name for p in result.skipped_legacy}
    assert "legacy.md" in legacy_names


# ---------------------------------------------------------------------------
# Slice 6: radar is a date-bucketed living folder — selection recurses into
# YYYY-MM/ buckets while still finding flat notes.
# ---------------------------------------------------------------------------

def test_bucketed_radar_note_is_selected(tmp_path: Path):
    radar = _radar_dir(tmp_path)
    (radar / "2026-06").mkdir()
    _write_radar(
        radar / "2026-06" / "bucketed.md",
        type="radar",
        project="acme",
        status="active",
        source="github-issue",
        target="<owner/repo> 1",
        check="daily",
        added="2026-05-01",
        **{"last-checked": "", "last-state": "open"},
    )
    result = radar_notes_due(tmp_path, today=date(2026, 6, 1))
    assert "bucketed.md" in {p.name for p in result.due}


def test_flat_and_bucketed_radar_both_selected(tmp_path: Path):
    radar = _radar_dir(tmp_path)
    (radar / "2026-06").mkdir()
    for rel in ("flat.md", "2026-06/bucketed.md"):
        _write_radar(
            radar / rel,
            type="radar",
            project="acme",
            status="active",
            source="github-issue",
            target="<owner/repo> 1",
            check="daily",
            added="2026-05-01",
            **{"last-checked": "", "last-state": "open"},
        )
    result = radar_notes_due(tmp_path, today=date(2026, 6, 1))
    assert {p.name for p in result.due} == {"flat.md", "bucketed.md"}
