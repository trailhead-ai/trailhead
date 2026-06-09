"""Session-filter behavioral tests for the reflect skill.

The reflect skill gathers sessions within a date window, selecting only those
with status `complete` (lore's canonical finalized status) or `finalized`
(accepted for back-compat, P1-B). Sessions with status `active` are excluded
— they will be picked up by a future reflection.

These tests use a SYNTHETIC fixture vault (no real brain content) to prove the
filter helper works correctly. The helper lives in
`plugins/lore/scripts/reflect_sessions.py` and is importable standalone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add the scripts dir to sys.path so we can import the helper directly.
SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "lore" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from reflect_sessions import sessions_in_window  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — synthetic vault with session notes at known dates
# ---------------------------------------------------------------------------

def _write_session(path: Path, status: str, ended: str) -> None:
    """Write a minimal synthetic session note with the given status and ended date."""
    path.write_text(
        f"---\ntype: session\nstatus: {status}\nended: {ended}\n---\n\n# Session\n"
    )


@pytest.fixture()
def synthetic_vault(tmp_path: Path) -> Path:
    sessions = tmp_path / "sessions"
    sessions.mkdir()

    # In-window complete session (canonical status)
    _write_session(sessions / "2026-05-10-1000-alpha.md", "complete", "2026-05-10T10:00:00Z")
    # In-window finalized session (back-compat status)
    _write_session(sessions / "2026-05-15-1400-beta.md", "finalized", "2026-05-15T14:00:00Z")
    # In-window active session — MUST be excluded
    _write_session(sessions / "2026-05-20-0900-gamma.md", "active", "")
    # Out-of-window complete session (April — before the window)
    _write_session(sessions / "2026-04-30-0800-delta.md", "complete", "2026-04-30T08:00:00Z")
    # Out-of-window complete session (June — after the window)
    _write_session(sessions / "2026-06-01-1200-epsilon.md", "complete", "2026-06-01T12:00:00Z")

    return tmp_path


# ---------------------------------------------------------------------------
# Behavioral tests
# ---------------------------------------------------------------------------

def test_complete_sessions_selected(synthetic_vault: Path):
    """status: complete sessions within the window are selected."""
    selected = sessions_in_window(synthetic_vault, "2026-05", "2026-05-01", "2026-05-31")
    names = {p.name for p in selected}
    assert "2026-05-10-1000-alpha.md" in names


def test_finalized_sessions_selected_for_back_compat(synthetic_vault: Path):
    """status: finalized is accepted as back-compat for complete (P1-B)."""
    selected = sessions_in_window(synthetic_vault, "2026-05", "2026-05-01", "2026-05-31")
    names = {p.name for p in selected}
    assert "2026-05-15-1400-beta.md" in names


def test_active_sessions_excluded(synthetic_vault: Path):
    """status: active sessions are never included in a reflection window."""
    selected = sessions_in_window(synthetic_vault, "2026-05", "2026-05-01", "2026-05-31")
    names = {p.name for p in selected}
    assert "2026-05-20-0900-gamma.md" not in names


def test_out_of_window_sessions_excluded(synthetic_vault: Path):
    """Sessions whose ended date falls outside the window are excluded."""
    selected = sessions_in_window(synthetic_vault, "2026-05", "2026-05-01", "2026-05-31")
    names = {p.name for p in selected}
    assert "2026-04-30-0800-delta.md" not in names
    assert "2026-06-01-1200-epsilon.md" not in names


def test_empty_vault_returns_empty_list(tmp_path: Path):
    """A vault with no sessions/ directory returns an empty list gracefully."""
    result = sessions_in_window(tmp_path, "2026-05", "2026-05-01", "2026-05-31")
    assert result == []


def test_both_complete_and_finalized_are_selected_together(synthetic_vault: Path):
    """complete and finalized are both returned in the same call."""
    selected = sessions_in_window(synthetic_vault, "2026-05", "2026-05-01", "2026-05-31")
    names = {p.name for p in selected}
    assert "2026-05-10-1000-alpha.md" in names
    assert "2026-05-15-1400-beta.md" in names
    assert len([n for n in names if n in {"2026-05-10-1000-alpha.md", "2026-05-15-1400-beta.md"}]) == 2
