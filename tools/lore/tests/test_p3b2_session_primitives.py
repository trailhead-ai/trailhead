"""P3B2-2 / P3B2-3 fix tests: session-note lifecycle primitives — shelve/resume.

TDD: tests written before implementation. All fixtures are SYNTHETIC
(invented vocabulary, no real vault/session names).

Covers:
- finalize_note with status= param: sets shelved + stamps ended:
- finalize_note default: still sets complete (backward-compat)
- finalize_note on a note with NO status: key (append-fallback path)
- finalize_note idempotency: returns False when already terminal
- find_shelved_notes: vault-wide finder for {shelved, handoff}
  - excludes active / complete
  - sorted most-recent-first by frontmatter timestamp (ended: fallback started:)
  - a note missing ended: sorts gracefully (no comparator crash)
  - slug filter narrows (including duplicate-slug case → predictable order)
  - empty when none shelved
- resume_note: flips shelved → active; flips handoff → active
- lore handoff CLI: active → shelved + commits; already-shelved → idempotent notice, no-op
- lore finish on already-shelved note: prints "already" notice, returns 0, no "Finalized:"
- shelved notes pass status_validator for type=session
- lore handoff --pickup-hints-file: writes hints into ## Pickup hints AND shelves (P3B2-3 fix)
  - hints written before shelving (note still flips to shelved)
  - plain lore handoff (no flag) still just shelves (regression)
  - already-shelved with hints file: idempotent no-op (does NOT double-write hints)
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


def load_script(name: str):
    """Load a module from plugins/lore/scripts/ freshly (no cache)."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    for cached in (name, "vault", "frontmatter", "status_validator", "sessions", "config"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_cli(args, env=None, cwd=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, cwd=cwd,
    )


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "testvault"
    (vault / "sessions").mkdir(parents=True)
    return vault


def _git_vault(tmp_path: Path) -> Path:
    """A vault that is its own git repo (toplevel == vault), no GPG signing."""
    vault = _make_vault(tmp_path)
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    for key, val in [
        ("user.email", "bot@fixture.test"),
        ("user.name", "Fixture Bot"),
        ("commit.gpgsign", "false"),
    ]:
        subprocess.run(
            ["git", "-C", str(vault), "config", key, val],
            check=True, capture_output=True,
        )
    return vault


def _write_session_note(
    vault: Path,
    filename: str,
    status: str = "active",
    started: str = "2026-01-01T10:00:00Z",
    ended: str | None = None,
    include_status_key: bool = True,
) -> Path:
    """Write a minimal synthetic session note."""
    sessions_dir = vault / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    note = sessions_dir / filename
    ended_line = f"ended: {ended}" if ended else "ended:"
    status_line = f"status: {status}\n" if include_status_key else ""
    note.write_text(
        f"---\n"
        f"type: session\n"
        f"project: test-project\n"
        f"worktree: alpha-worktree\n"
        f"branch: feature-branch\n"
        f"started: {started}\n"
        f"{ended_line}\n"
        f"subsystems: []\n"
        f"phase: Orient\n"
        f"session_id: sid-fixture\n"
        f"{status_line}"
        f"---\n\n"
        f"# Session: alpha-worktree\n\n"
        f"## What we did\n\n"
        f"## Decided\n\n"
        f"## Deferred\n\n"
        f"## Learned\n\n"
        f"## Open questions\n"
    )
    return note


# ===========================================================================
# finalize_note: status= parameter
# ===========================================================================

class TestFinalizeNoteStatusParam:
    """finalize_note(note, iso, status=...) parameterizes BOTH write sites."""

    def test_default_sets_complete(self, tmp_path):
        """finalize_note with no status arg still writes complete (backward-compat)."""
        vault = _make_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md")
        sessions = load_script("sessions")
        result = sessions.finalize_note(note, "2026-01-01T11:00:00Z")
        assert result is True
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "complete"
        assert fm["ended"] == "2026-01-01T11:00:00Z"

    def test_status_shelved_sets_shelved(self, tmp_path):
        """finalize_note(note, iso, status='shelved') writes shelved, not complete."""
        vault = _make_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md")
        sessions = load_script("sessions")
        result = sessions.finalize_note(note, "2026-01-01T11:00:00Z", status="shelved")
        assert result is True
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "shelved"
        assert fm["ended"] == "2026-01-01T11:00:00Z"

    def test_shelved_stamps_ended(self, tmp_path):
        """A shelved note must have ended: stamped (fully-formed)."""
        vault = _make_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md")
        sessions = load_script("sessions")
        sessions.finalize_note(note, "2026-01-02T09:30:00Z", status="shelved")
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm.get("ended") == "2026-01-02T09:30:00Z"

    def test_status_handoff_sets_handoff(self, tmp_path):
        """finalize_note(note, iso, status='handoff') writes handoff."""
        vault = _make_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md")
        sessions = load_script("sessions")
        result = sessions.finalize_note(note, "2026-01-01T11:00:00Z", status="handoff")
        assert result is True
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "handoff"


class TestFinalizeNoteNoStatusKey:
    """finalize_note on a note with NO status: key exercises the append-fallback."""

    def test_append_fallback_with_default_complete(self, tmp_path):
        """Note missing status: key gets status: complete appended (not replaced)."""
        vault = _make_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md", include_status_key=False
        )
        # Confirm fixture has no status: key
        raw = note.read_text()
        assert "status:" not in raw
        sessions = load_script("sessions")
        result = sessions.finalize_note(note, "2026-01-01T11:00:00Z")
        assert result is True
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "complete"

    def test_append_fallback_with_shelved(self, tmp_path):
        """Note missing status: key gets status: shelved appended via fallback path."""
        vault = _make_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md", include_status_key=False
        )
        sessions = load_script("sessions")
        result = sessions.finalize_note(note, "2026-01-01T11:00:00Z", status="shelved")
        assert result is True
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "shelved"
        assert fm["ended"] == "2026-01-01T11:00:00Z"


class TestFinalizeNoteIdempotency:
    """finalize_note returns False when the note is already terminal."""

    def test_returns_false_when_already_complete(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md", status="complete")
        sessions = load_script("sessions")
        result = sessions.finalize_note(note, "2026-01-01T12:00:00Z")
        assert result is False

    def test_returns_false_when_already_shelved(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md", status="shelved")
        sessions = load_script("sessions")
        result = sessions.finalize_note(note, "2026-01-01T12:00:00Z")
        assert result is False

    def test_returns_false_when_already_finalized(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md", status="finalized")
        sessions = load_script("sessions")
        result = sessions.finalize_note(note, "2026-01-01T12:00:00Z")
        assert result is False


class TestFinalizeNoteStatusValidator:
    """shelved notes pass status_validator for type=session."""

    def test_shelved_is_valid_session_status(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md")
        sessions = load_script("sessions")
        sessions.finalize_note(note, "2026-01-01T11:00:00Z", status="shelved")
        fm = load_script("frontmatter").parse_frontmatter(note)
        sv = load_script("status_validator")
        assert sv.is_valid_status(fm["type"], fm["status"])


# ===========================================================================
# find_shelved_notes: vault-wide finder
# ===========================================================================

class TestFindShelvedNotes:
    """find_shelved_notes returns notes with status in {shelved, handoff}."""

    def test_finds_shelved_notes(self, tmp_path):
        vault = _make_vault(tmp_path)
        shelved = _write_session_note(
            vault, "2026-01-02-0900-beta-worktree.md",
            status="shelved", ended="2026-01-02T09:00:00Z",
        )
        _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md", status="active")
        sessions = load_script("sessions")
        result = sessions.find_shelved_notes(vault)
        assert len(result) == 1
        assert result[0] == shelved

    def test_finds_handoff_notes(self, tmp_path):
        vault = _make_vault(tmp_path)
        handoff = _write_session_note(
            vault, "2026-01-02-0900-beta-worktree.md",
            status="handoff", ended="2026-01-02T09:00:00Z",
        )
        sessions = load_script("sessions")
        result = sessions.find_shelved_notes(vault)
        assert len(result) == 1
        assert result[0] == handoff

    def test_excludes_active_notes(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md", status="active")
        sessions = load_script("sessions")
        result = sessions.find_shelved_notes(vault)
        assert result == []

    def test_excludes_complete_notes(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="complete", ended="2026-01-01T11:00:00Z",
        )
        sessions = load_script("sessions")
        result = sessions.find_shelved_notes(vault)
        assert result == []

    def test_empty_when_none_shelved(self, tmp_path):
        vault = _make_vault(tmp_path)
        sessions = load_script("sessions")
        result = sessions.find_shelved_notes(vault)
        assert result == []

    def test_empty_when_sessions_dir_missing(self, tmp_path):
        vault = tmp_path / "novault"
        vault.mkdir()
        sessions = load_script("sessions")
        result = sessions.find_shelved_notes(vault)
        assert result == []

    def test_sorted_most_recent_first_by_ended(self, tmp_path):
        """Most-recent ended: timestamp appears first."""
        vault = _make_vault(tmp_path)
        older = _write_session_note(
            vault, "2026-01-01-0900-gamma-worktree.md",
            status="shelved", ended="2026-01-01T09:00:00Z",
        )
        newer = _write_session_note(
            vault, "2026-01-02-0900-delta-worktree.md",
            status="shelved", ended="2026-01-02T09:00:00Z",
        )
        sessions = load_script("sessions")
        result = sessions.find_shelved_notes(vault)
        assert result == [newer, older]

    def test_missing_ended_sorts_gracefully_no_crash(self, tmp_path):
        """A note with no ended: value sorts without crashing (graceful fallback)."""
        vault = _make_vault(tmp_path)
        with_ended = _write_session_note(
            vault, "2026-01-02-0900-delta-worktree.md",
            status="shelved", ended="2026-01-02T09:00:00Z",
        )
        # Note with ended: but empty value (no crash expected)
        no_ended = _write_session_note(
            vault, "2026-01-01-0900-gamma-worktree.md",
            status="shelved", started="2026-01-01T09:00:00Z", ended=None,
        )
        sessions = load_script("sessions")
        # Must not raise — sort completes successfully
        result = sessions.find_shelved_notes(vault)
        assert len(result) == 2
        # The note with ended: should sort before the one without
        assert result[0] == with_ended

    def test_falls_back_to_started_when_no_ended(self, tmp_path):
        """A note missing ended: falls back to started: for sort."""
        vault = _make_vault(tmp_path)
        # Both notes lack ended:; sort should use started:
        newer = _write_session_note(
            vault, "2026-01-03-0900-epsilon-worktree.md",
            status="shelved", started="2026-01-03T09:00:00Z", ended=None,
        )
        older = _write_session_note(
            vault, "2026-01-01-0900-zeta-worktree.md",
            status="shelved", started="2026-01-01T09:00:00Z", ended=None,
        )
        sessions = load_script("sessions")
        result = sessions.find_shelved_notes(vault)
        assert result[0] == newer
        assert result[1] == older

    def test_slug_filter_narrows_results(self, tmp_path):
        """slug filter returns only notes whose filename encodes that slug."""
        vault = _make_vault(tmp_path)
        target = _write_session_note(
            vault, "2026-01-02-0900-target-worktree.md",
            status="shelved", ended="2026-01-02T09:00:00Z",
        )
        _write_session_note(
            vault, "2026-01-01-0900-other-worktree.md",
            status="shelved", ended="2026-01-01T09:00:00Z",
        )
        sessions = load_script("sessions")
        result = sessions.find_shelved_notes(vault, slug="target-worktree")
        assert result == [target]

    def test_slug_filter_duplicate_slugs_predictable_order(self, tmp_path):
        """Multiple shelved notes with the same slug sort most-recent-first."""
        vault = _make_vault(tmp_path)
        older = _write_session_note(
            vault, "2026-01-01-0900-repeated-worktree.md",
            status="shelved", ended="2026-01-01T09:00:00Z",
        )
        newer = _write_session_note(
            vault, "2026-01-03-1200-repeated-worktree.md",
            status="shelved", ended="2026-01-03T12:00:00Z",
        )
        sessions = load_script("sessions")
        result = sessions.find_shelved_notes(vault, slug="repeated-worktree")
        assert result == [newer, older]

    def test_slug_filter_no_match_returns_empty(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_session_note(
            vault, "2026-01-02-0900-other-worktree.md",
            status="shelved", ended="2026-01-02T09:00:00Z",
        )
        sessions = load_script("sessions")
        result = sessions.find_shelved_notes(vault, slug="nonexistent-slug")
        assert result == []


# ===========================================================================
# resume_note
# ===========================================================================

class TestResumeNote:
    """resume_note flips shelved/handoff → active."""

    def test_flips_shelved_to_active(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="shelved", ended="2026-01-01T11:00:00Z",
        )
        sessions = load_script("sessions")
        result = sessions.resume_note(note)
        assert result is True
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "active"

    def test_flips_handoff_to_active(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="handoff", ended="2026-01-01T11:00:00Z",
        )
        sessions = load_script("sessions")
        result = sessions.resume_note(note)
        assert result is True
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "active"

    def test_returns_false_for_active_note(self, tmp_path):
        """resume_note on an already-active note is a no-op (returns False)."""
        vault = _make_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md", status="active")
        sessions = load_script("sessions")
        result = sessions.resume_note(note)
        assert result is False

    def test_returns_false_for_complete_note(self, tmp_path):
        """resume_note on a complete note is a no-op (not resumable)."""
        vault = _make_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="complete", ended="2026-01-01T11:00:00Z",
        )
        sessions = load_script("sessions")
        result = sessions.resume_note(note)
        assert result is False

    def test_resumed_note_passes_status_validator(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="shelved", ended="2026-01-01T11:00:00Z",
        )
        sessions = load_script("sessions")
        sessions.resume_note(note)
        fm = load_script("frontmatter").parse_frontmatter(note)
        sv = load_script("status_validator")
        assert sv.is_valid_status(fm["type"], fm["status"])


# ===========================================================================
# lore handoff CLI
# ===========================================================================

class TestLoreHandoffSetsStatusShelved:
    """lore handoff sets active → shelved + stamps ended: + commits."""

    def test_sets_status_shelved(self, tmp_path):
        vault = _git_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md")
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        result = run_cli(
            ["handoff"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        assert result.returncode == 0, result.stderr
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "shelved"

    def test_stamps_ended(self, tmp_path):
        vault = _git_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md")
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        run_cli(
            ["handoff"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm.get("ended"), f"ended should be stamped; got {fm.get('ended')!r}"

    def test_commits_vault(self, tmp_path):
        vault = _git_vault(tmp_path)
        _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md")
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        run_cli(
            ["handoff"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        log = subprocess.run(
            ["git", "-C", str(vault), "log", "--oneline"],
            capture_output=True, text=True,
        )
        assert log.stdout.strip(), "expected a commit after lore handoff"

    def test_shelved_note_passes_status_validator(self, tmp_path):
        vault = _git_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md")
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        run_cli(
            ["handoff"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        fm = load_script("frontmatter").parse_frontmatter(note)
        sv = load_script("status_validator")
        assert sv.is_valid_status(fm["type"], fm["status"])

    def test_no_active_note_exits_zero_with_notice(self, tmp_path):
        vault = _git_vault(tmp_path)
        fake_cwd = tmp_path / "no-such-worktree"
        fake_cwd.mkdir()
        result = run_cli(
            ["handoff"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        assert result.returncode == 0
        combined = result.stdout + result.stderr
        assert "no active session" in combined.lower() or "nothing to" in combined.lower()


class TestLoreHandoffIdempotency:
    """lore handoff on an already-shelved note: notice + no-op, no re-stamp."""

    def test_already_shelved_exits_zero(self, tmp_path):
        vault = _git_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="shelved", ended="2026-01-01T09:00:00Z",
        )
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        result = run_cli(
            ["handoff"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        assert result.returncode == 0

    def test_already_shelved_prints_notice(self, tmp_path):
        vault = _git_vault(tmp_path)
        _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="shelved", ended="2026-01-01T09:00:00Z",
        )
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        result = run_cli(
            ["handoff"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        combined = result.stdout + result.stderr
        assert "already" in combined.lower()

    def test_already_shelved_does_not_re_stamp_ended(self, tmp_path):
        """A second handoff must not overwrite the original ended: timestamp."""
        vault = _git_vault(tmp_path)
        original_ended = "2026-01-01T09:00:00Z"
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="shelved", ended=original_ended,
        )
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        run_cli(
            ["handoff"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["ended"] == original_ended, "re-stamp must not overwrite original ended:"

    def test_already_shelved_does_not_make_extra_commit(self, tmp_path):
        """Idempotent handoff on shelved note does not commit again."""
        vault = _git_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="shelved", ended="2026-01-01T09:00:00Z",
        )
        # Stage and commit the note first so the vault is clean
        subprocess.run(["git", "-C", str(vault), "add", str(note)], check=True)
        subprocess.run(
            ["git", "-C", str(vault), "commit", "-m", "seed"],
            check=True, capture_output=True,
        )
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        run_cli(
            ["handoff"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        log = subprocess.run(
            ["git", "-C", str(vault), "log", "--oneline"],
            capture_output=True, text=True,
        )
        # Only the initial seed commit — no extra commit from the idempotent run
        assert log.stdout.strip().count("\n") == 0, (
            "idempotent handoff must not create an extra commit"
        )


# ===========================================================================
# lore finish on already-shelved: no false "Finalized:" (council Reliability C1)
# ===========================================================================

class TestLoreFinishOnShelvedNote:
    """lore finish on an already-shelved note prints notice, returns 0, no 'Finalized:'."""

    def test_returns_zero(self, tmp_path):
        vault = _git_vault(tmp_path)
        _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="shelved", ended="2026-01-01T09:00:00Z",
        )
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        result = run_cli(
            ["finish"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        assert result.returncode == 0

    def test_prints_already_notice(self, tmp_path):
        vault = _git_vault(tmp_path)
        _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="shelved", ended="2026-01-01T09:00:00Z",
        )
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        result = run_cli(
            ["finish"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        combined = result.stdout + result.stderr
        assert "already" in combined.lower(), (
            f"expected 'already' notice; got: {combined!r}"
        )

    def test_does_not_print_finalized(self, tmp_path):
        """No false 'Finalized:' when the note is already shelved."""
        vault = _git_vault(tmp_path)
        _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="shelved", ended="2026-01-01T09:00:00Z",
        )
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        result = run_cli(
            ["finish"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        assert "Finalized:" not in result.stdout, (
            f"must not print false 'Finalized:'; stdout: {result.stdout!r}"
        )


# ===========================================================================
# lore handoff --pickup-hints-file (P3B2-3 fix)
# ===========================================================================

class TestLoreHandoffPickupHintsFile:
    """lore handoff --pickup-hints-file writes hints into ## Pickup hints AND shelves."""

    def test_writes_pickup_hints_section_and_shelves(self, tmp_path):
        """Hints file content lands in ## Pickup hints; note flips to shelved."""
        vault = _git_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md")
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        hints_file = tmp_path / "hints.md"
        hints_file.write_text("Next: fix the widget\nBlocker: waiting on review\n")
        result = run_cli(
            ["handoff", "--pickup-hints-file", str(hints_file)],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        assert result.returncode == 0, result.stderr
        body = note.read_text()
        assert "## Pickup hints" in body
        assert "Next: fix the widget" in body
        assert "status: shelved" in body

    def test_hints_written_before_shelving(self, tmp_path):
        """The ## Pickup hints section is present in the final shelved note."""
        vault = _git_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md")
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        hints_file = tmp_path / "hints.md"
        hints_file.write_text("Blocker: upstream merge needed\n")
        run_cli(
            ["handoff", "--pickup-hints-file", str(hints_file)],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "shelved"
        assert "Blocker: upstream merge needed" in note.read_text()

    def test_plain_handoff_still_shelves_without_hints(self, tmp_path):
        """Plain lore handoff (no --pickup-hints-file) still just shelves (regression)."""
        vault = _git_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md")
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        result = run_cli(
            ["handoff"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        assert result.returncode == 0, result.stderr
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "shelved"

    def test_already_shelved_with_hints_file_is_idempotent_noop(self, tmp_path):
        """Already-shelved note + hints file: idempotent no-op, hints NOT double-written."""
        vault = _git_vault(tmp_path)
        # Write a note that's already shelved with a Pickup hints section.
        sessions_dir = vault / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        note = sessions_dir / "2026-01-01-1000-alpha-worktree.md"
        note.write_text(
            "---\n"
            "type: session\n"
            "project: test-project\n"
            "worktree: alpha-worktree\n"
            "branch: feature-branch\n"
            "started: 2026-01-01T10:00:00Z\n"
            "ended: 2026-01-01T11:00:00Z\n"
            "subsystems: []\n"
            "phase: Orient\n"
            "session_id: sid-fixture\n"
            "status: shelved\n"
            "---\n\n"
            "# Session: alpha-worktree\n\n"
            "## Pickup hints\n\n"
            "Original hint text.\n\n"
            "## What we did\n\n"
        )
        fake_cwd = tmp_path / "alpha-worktree"
        fake_cwd.mkdir()
        hints_file = tmp_path / "hints.md"
        hints_file.write_text("New hint text — must not appear.\n")
        result = run_cli(
            ["handoff", "--pickup-hints-file", str(hints_file)],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        assert result.returncode == 0
        body = note.read_text()
        # The already-shelved guard fires before writing hints — new text must not be written.
        assert "New hint text" not in body
        # Original content preserved.
        assert "Original hint text." in body
