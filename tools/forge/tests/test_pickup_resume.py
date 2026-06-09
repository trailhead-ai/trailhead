"""Unit + integration tests for the pickup-resume helper.

The deterministic logic behind `/forge:pickup` lives in
`plugins/forge/scripts/pickup_resume.py` so it can be tested without driving a
live Claude Code session. `/forge:pickup` resumes work shelved by
`/forge:handoff` — it surfaces the recorded git state + pickup hints and flips
the shelved note back to active. Coverage:

  - most-recent forge handoff read (symmetric with handoff's degraded write):
    picks the newest file in ~/.forge/handoffs/, returns its path + content,
    None when the dir is empty/missing.
  - pickup-hints parse: extracts the `## Pickup hints` section body from a
    handoff file (or session note), graceful when the section is absent.
  - cross-repo integration: shelve a synthetic vault note via the REAL
    `lore handoff --pickup-hints-file`, then drive pickup's actual sequence —
    `lore shelved` lists it, `lore resume` flips it back to active.

All fixtures are synthetic — no real branch names, repo slugs, or machine paths.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "plugins" / "forge" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pickup_resume as pr  # noqa: E402


# ---------------------------------------------------------------------------
# most-recent forge handoff read (symmetric degraded read)
# ---------------------------------------------------------------------------

def test_most_recent_returns_newest_file(tmp_path: Path):
    handoff_dir = tmp_path / ".forge" / "handoffs"
    handoff_dir.mkdir(parents=True)
    older = handoff_dir / "alpha-widget.md"
    older.write_text("# old handoff\n")
    time.sleep(0.01)
    newer = handoff_dir / "beta-gadget.md"
    newer.write_text("# new handoff\n## Pickup hints\n\nNext: do the gadget\n")
    # Make mtimes explicit so the test is not timing-fragile.
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    found = pr.most_recent_handoff(handoff_dir)
    assert found is not None
    assert found.path == newer
    assert "Next: do the gadget" in found.content


def test_most_recent_none_when_dir_missing(tmp_path: Path):
    handoff_dir = tmp_path / ".forge" / "handoffs"
    assert not handoff_dir.exists()
    assert pr.most_recent_handoff(handoff_dir) is None


def test_most_recent_none_when_dir_empty(tmp_path: Path):
    handoff_dir = tmp_path / ".forge" / "handoffs"
    handoff_dir.mkdir(parents=True)
    assert pr.most_recent_handoff(handoff_dir) is None


def test_most_recent_by_slug(tmp_path: Path):
    """A slug arg targets a specific handoff file rather than the newest."""
    handoff_dir = tmp_path / ".forge" / "handoffs"
    handoff_dir.mkdir(parents=True)
    (handoff_dir / "alpha-widget.md").write_text("# alpha\nhint A\n")
    (handoff_dir / "beta-gadget.md").write_text("# beta\nhint B\n")

    found = pr.most_recent_handoff(handoff_dir, slug="alpha-widget")
    assert found is not None
    assert found.path.name == "alpha-widget.md"


def test_most_recent_by_slug_missing_returns_none(tmp_path: Path):
    handoff_dir = tmp_path / ".forge" / "handoffs"
    handoff_dir.mkdir(parents=True)
    (handoff_dir / "beta-gadget.md").write_text("# beta\nhint B\n")
    assert pr.most_recent_handoff(handoff_dir, slug="no-such-slug") is None


# ---------------------------------------------------------------------------
# pickup-hints parse
# ---------------------------------------------------------------------------

def test_parse_pickup_hints_extracts_section():
    text = (
        "# Forge handoff — alpha\n\n"
        "## Pickup hints\n\n"
        "Next: finish the parser\n"
        "Blocker: waiting on review\n\n"
        "## Captured git state\n\n"
        "- Branch: `feature-thing`\n"
    )
    hints = pr.parse_pickup_hints(text)
    assert "Next: finish the parser" in hints
    assert "Blocker: waiting on review" in hints
    # The next heading's content is not included.
    assert "feature-thing" not in hints


def test_parse_pickup_hints_graceful_when_absent():
    text = "# Forge handoff — alpha\n\nNo hints section here.\n"
    hints = pr.parse_pickup_hints(text)
    assert hints == ""


def test_parse_pickup_hints_runs_to_eof_when_last_section():
    text = "# title\n\n## Pickup hints\n\nNext: the only section\n"
    hints = pr.parse_pickup_hints(text)
    assert "Next: the only section" in hints


# ---------------------------------------------------------------------------
# cross-repo integration: drive pickup's REAL sequence against a fixture vault
# ---------------------------------------------------------------------------

def _resolve_lore_cli() -> list[str] | None:
    """Locate a runnable lore CLI without hardcoding a machine path.

    Order: `lore` on PATH (preferred), else the local lore plugin's bin under
    $HOME. Returns the argv prefix, or None when neither is available.
    """
    import shutil as _shutil

    on_path = _shutil.which("lore")
    if on_path:
        return [on_path]
    candidate = Path(os.environ["HOME"]) / "code" / "lore" / "plugins" / "lore" / "bin" / "lore"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return [str(candidate)]
    return None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def fixture_vault(tmp_path: Path) -> Path:
    """A minimal git-backed lore vault with one active session note."""
    vault = tmp_path / "synthetic-vault"
    (vault / "sessions").mkdir(parents=True)
    note = vault / "sessions" / "2026-01-02-0900-alpha-widget.md"
    note.write_text(
        "---\n"
        "type: session\n"
        "status: active\n"
        "started: 2026-01-02T09:00:00Z\n"
        "ended:\n"
        "---\n\n"
        "# Session\n\n"
        "## Pickup hints\n\n"
    )
    _git(vault, "init", "-q")
    _git(vault, "config", "user.email", "t@example.test")
    _git(vault, "config", "user.name", "Test")
    _git(vault, "config", "commit.gpgsign", "false")
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", "seed vault")
    return vault


def _run_lore(cli, args, vault, cwd):
    env = dict(os.environ)
    env["LORE_VAULT"] = str(vault)
    return subprocess.run(
        [*cli, *args], cwd=str(cwd), env=env, capture_output=True, text=True
    )


def test_real_pickup_sequence_shelved_then_resume(fixture_vault: Path, tmp_path: Path):
    """Integration: drive pickup's ACTUAL working-backend sequence.

    1. Shelve via the real `lore handoff --pickup-hints-file` (what handoff does).
    2. `lore shelved` lists the note + surfaces the hints fragment.
    3. `lore resume <slug>` flips the note back to active.

    This drives the real lore CLI sequence the skill orchestrates — not stubs.
    """
    cli = _resolve_lore_cli()
    if cli is None:
        pytest.skip("lore CLI not available (not on PATH, no local lore bin)")

    note = fixture_vault / "sessions" / "2026-01-02-0900-alpha-widget.md"
    cwd = fixture_vault / "work" / "alpha-widget"
    cwd.mkdir(parents=True)

    # --- handoff shelves the note with hints ---
    hints_file = tmp_path / "pickup_hints.md"
    hints_file.write_text("Next: resume widget implementation\nBlocker: dependency upgrade\n")
    shelve = _run_lore(cli, ["handoff", "--pickup-hints-file", str(hints_file)], fixture_vault, cwd)
    assert shelve.returncode == 0, shelve.stderr
    assert "status: shelved" in note.read_text()

    # --- pickup step 1: `lore shelved` lists it + surfaces the hints fragment ---
    listed = _run_lore(cli, ["shelved"], fixture_vault, cwd)
    assert listed.returncode == 0, listed.stderr
    assert "alpha-widget" in listed.stdout
    assert "Next: resume widget implementation" in listed.stdout

    # --- pickup step 2: `lore resume <slug>` flips it back to active ---
    resumed = _run_lore(cli, ["resume", "alpha-widget"], fixture_vault, cwd)
    assert resumed.returncode == 0, resumed.stderr
    assert "status: active" in note.read_text()


def test_real_pickup_resume_by_path(fixture_vault: Path, tmp_path: Path):
    """`lore resume <note-path>` also flips a shelved note to active."""
    cli = _resolve_lore_cli()
    if cli is None:
        pytest.skip("lore CLI not available (not on PATH, no local lore bin)")

    note = fixture_vault / "sessions" / "2026-01-02-0900-alpha-widget.md"
    cwd = fixture_vault / "work" / "alpha-widget"
    cwd.mkdir(parents=True)

    hints_file = tmp_path / "h.md"
    hints_file.write_text("Next: thing\n")
    _run_lore(cli, ["handoff", "--pickup-hints-file", str(hints_file)], fixture_vault, cwd)
    assert "status: shelved" in note.read_text()

    resumed = _run_lore(cli, ["resume", str(note)], fixture_vault, cwd)
    assert resumed.returncode == 0, resumed.stderr
    assert "status: active" in note.read_text()
