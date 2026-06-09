"""P1-D tests: skeleton detection, orphan sweep, finalize branch, idempotency guard.

All four behaviors land atomically (plan council Reliability) — a partial state
could misclassify real content as a skeleton and delete it on the live plugin.

Fixture discipline: all worktree names, slugs, and note content are SYNTHETIC —
no real brain content, no real subsystem names (public repo leak-gate rule).
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


def load_hook(name: str):
    for d in (str(HOOKS_DIR), str(SCRIPTS_DIR)):
        if d not in sys.path:
            sys.path.insert(0, d)
    for cached in (name, "sessions", "vault", "frontmatter", "status_validator"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, HOOKS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_sessions():
    for d in (str(SCRIPTS_DIR),):
        if d not in sys.path:
            sys.path.insert(0, d)
    for cached in ("sessions", "vault", "frontmatter", "status_validator"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location("sessions", SCRIPTS_DIR / "sessions.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "sessions").mkdir(parents=True)
    return vault


def _git_vault(tmp_path: Path) -> Path:
    vault = _make_vault(tmp_path)
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "t@e.st"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "Tester"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "commit.gpgsign", "false"],
                   check=True, capture_output=True)
    return vault


def _skeleton_note(vault: Path, worktree: str = "synth-alpha") -> Path:
    """Write a skeleton session note — only headings and HTML comments, no real content."""
    sessions_dir = vault / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    note = sessions_dir / f"2099-01-01-0000-{worktree}.md"
    note.write_text(
        f"---\n"
        f"type: session\n"
        f"project: synth-proj\n"
        f"worktree: {worktree}\n"
        f"branch: synth-branch\n"
        f"started: 2099-01-01T00:00:00Z\n"
        f"ended:\n"
        f"subsystems: []\n"
        f"phase: Orient\n"
        f"session_id: synth-sid-1\n"
        f"status: active\n"
        f"---\n\n"
        f"# Session: {worktree}\n\n"
        f"Started 2099-01-01 00:00 UTC on branch `synth-branch` in project `synth-proj`.\n\n"
        f"## What we did\n"
        f"<!-- Append as work happens. -->\n\n"
        f"## Decided\n"
        f"<!-- Non-obvious decisions. Each is or becomes a decisions/ note. -->\n\n"
        f"## Deferred\n"
        f"<!-- Links to deferred/ notes created in this session. -->\n\n"
        f"## Learned\n"
        f"<!-- Gotchas, subsystem corrections, links to dead-ends/ notes. -->\n\n"
        f"## Open questions\n"
        f"<!-- Unresolved threads. -->\n"
    )
    return note


def _real_content_note(vault: Path, worktree: str = "synth-beta") -> Path:
    """Write a session note with real content beyond the skeleton template."""
    note = _skeleton_note(vault, worktree)
    text = note.read_text()
    # Insert actual content under "## What we did"
    text = text.replace(
        "## What we did\n<!-- Append as work happens. -->",
        "## What we did\n<!-- Append as work happens. -->\n\nWired the synth-gadget to the synth-widget.",
    )
    note.write_text(text)
    return note


def _terminal_note(vault: Path, status: str, worktree: str = "synth-gamma") -> Path:
    """Write a session note that already has a terminal status."""
    note = _real_content_note(vault, worktree)
    text = note.read_text()
    text = text.replace("status: active", f"status: {status}")
    note.write_text(text)
    return note


def _run_finalize(payload: dict, vault: Path):
    mod = load_hook("finalize-session-note")
    out = io.StringIO()
    env = {"LORE_VAULT": str(vault), "PATH": os.environ.get("PATH", ""),
           "HOME": os.environ.get("HOME", "")}
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with mock.patch("sys.stdout", out):
                mod.main()
    return out.getvalue(), mod


# ---------------------------------------------------------------------------
# is_skeleton_body — unit tests (pure function, no I/O)
# ---------------------------------------------------------------------------

class TestIsSkeletonBody:
    def _load_fn(self):
        s = load_sessions()
        return s.is_skeleton_body

    def test_skeleton_note_returns_true(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _skeleton_note(vault)
        assert self._load_fn()(note) is True

    def test_real_content_note_returns_false(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _real_content_note(vault)
        assert self._load_fn()(note) is False

    def test_no_frontmatter_returns_false(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = vault / "sessions" / "2099-01-01-0000-synth-nofm.md"
        note.write_text("# Session: synth-nofm\n\nNo frontmatter here.\n")
        assert self._load_fn()(note) is False

    def test_nonexistent_file_returns_false(self, tmp_path):
        note = tmp_path / "sessions" / "nonexistent.md"
        assert self._load_fn()(note) is False

    def test_note_with_only_heading_and_blank_lines_is_skeleton(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = vault / "sessions" / "2099-01-01-0000-synth-minimal.md"
        note.write_text(
            "---\ntype: session\nstatus: active\n---\n\n"
            "# Session: synth-minimal\n\n"
            "## What we did\n\n"
            "## Decided\n"
        )
        assert self._load_fn()(note) is True

    def test_note_with_started_line_is_skeleton(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = vault / "sessions" / "2099-01-01-0000-synth-wstarted.md"
        note.write_text(
            "---\ntype: session\nstatus: active\n---\n\n"
            "# Session: synth-wstarted\n\n"
            "Started 2099-01-01 00:00 UTC on branch `synth-b` in project `synth-p`.\n\n"
            "## What we did\n\n"
        )
        assert self._load_fn()(note) is True

    def test_any_extra_line_is_real_content(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = vault / "sessions" / "2099-01-01-0000-synth-extra.md"
        note.write_text(
            "---\ntype: session\nstatus: active\n---\n\n"
            "# Session: synth-extra\n\n"
            "## What we did\n\nActually did something.\n"
        )
        assert self._load_fn()(note) is False

    def test_multiline_html_comment_is_real_content(self, tmp_path):
        """A multi-line <!-- ... --> block is NOT a skeleton line."""
        vault = _make_vault(tmp_path)
        note = vault / "sessions" / "2099-01-01-0000-synth-mlcomment.md"
        note.write_text(
            "---\ntype: session\nstatus: active\n---\n\n"
            "# Session: synth-mlcomment\n\n"
            "## What we did\n"
            "<!--\nThis comment spans\nmultiple lines\n-->\n"
        )
        # Each line is checked individually; multi-line comment has internal lines
        # that don't start with <!-- and don't end with --> — they are real content
        assert self._load_fn()(note) is False


# ---------------------------------------------------------------------------
# finalize hook: skeleton → deleted, not finalized
# ---------------------------------------------------------------------------

class TestFinalizeSkeletonDeleted:
    def test_skeleton_note_is_deleted_on_worktree_remove(self, tmp_path):
        vault = _git_vault(tmp_path)
        note = _skeleton_note(vault, worktree="synth-alpha")
        assert note.exists()
        _run_finalize({"worktree": "synth-alpha"}, vault)
        assert not note.exists(), "skeleton note should have been deleted"

    def test_skeleton_note_not_finalized(self, tmp_path):
        """After WorktreeRemove, no finalized version of the skeleton exists."""
        vault = _git_vault(tmp_path)
        note = _skeleton_note(vault, worktree="synth-alpha")
        _run_finalize({"worktree": "synth-alpha"}, vault)
        # The original note path is gone — no remnant
        assert not note.exists()


# ---------------------------------------------------------------------------
# finalize hook: real content → finalized
# ---------------------------------------------------------------------------

class TestFinalizeRealContent:
    def test_real_note_gets_status_complete(self, tmp_path):
        vault = _git_vault(tmp_path)
        note = _real_content_note(vault, worktree="synth-beta")
        _run_finalize({"worktree": "synth-beta"}, vault)
        assert note.exists(), "real note should still exist after finalize"
        text = note.read_text()
        assert "status: complete" in text

    def test_real_note_gets_ended_timestamp(self, tmp_path):
        vault = _git_vault(tmp_path)
        note = _real_content_note(vault, worktree="synth-beta")
        _run_finalize({"worktree": "synth-beta"}, vault)
        text = note.read_text()
        # ended: should be set to a non-empty value
        import re
        m = re.search(r"ended: (.+)", text)
        assert m and m.group(1).strip(), "ended: should be set to a timestamp"


# ---------------------------------------------------------------------------
# finalize hook: idempotency guard — terminal status notes left untouched
# ---------------------------------------------------------------------------

class TestFinalizeIdempotencyGuard:
    def _check_not_restamped(self, vault: Path, note: Path, original_text: str, worktree: str):
        _run_finalize({"worktree": worktree}, vault)
        assert note.read_text() == original_text, (
            f"note with terminal status was re-stamped (idempotency guard missing)"
        )

    def test_already_complete_not_restamped(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _terminal_note(vault, status="complete", worktree="synth-delta")
        # Set a specific ended: to verify it's not overwritten
        text = note.read_text().replace("ended:\n", "ended: 2099-01-01T00:00:00Z\n")
        note.write_text(text)
        original = note.read_text()
        self._check_not_restamped(vault, note, original, "synth-delta")

    def test_already_shelved_not_restamped(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _terminal_note(vault, status="shelved", worktree="synth-epsilon")
        text = note.read_text().replace("ended:\n", "ended: 2099-01-01T00:00:00Z\n")
        note.write_text(text)
        original = note.read_text()
        self._check_not_restamped(vault, note, original, "synth-epsilon")

    def test_already_finalized_not_restamped(self, tmp_path):
        """status: finalized (brain vocab, accepted in P1-B) → not re-stamped."""
        vault = _make_vault(tmp_path)
        note = _terminal_note(vault, status="finalized", worktree="synth-zeta")
        text = note.read_text().replace("ended:\n", "ended: 2099-01-01T00:00:00Z\n")
        note.write_text(text)
        original = note.read_text()
        self._check_not_restamped(vault, note, original, "synth-zeta")

    def test_already_handoff_not_restamped(self, tmp_path):
        """status: handoff (brain vocab, accepted in P1-B) → not re-stamped."""
        vault = _make_vault(tmp_path)
        note = _terminal_note(vault, status="handoff", worktree="synth-eta")
        text = note.read_text().replace("ended:\n", "ended: 2099-01-01T00:00:00Z\n")
        note.write_text(text)
        original = note.read_text()
        self._check_not_restamped(vault, note, original, "synth-eta")


# ---------------------------------------------------------------------------
# sweep_orphan_skeletons — orphan sweep behavior
# ---------------------------------------------------------------------------

class TestSweepOrphanSkeletons:
    def _load_sweep(self):
        s = load_sessions()
        return s.sweep_orphan_skeletons

    def test_old_skeleton_in_other_worktree_is_swept(self, tmp_path):
        """An orphan skeleton older than RESUME_WINDOW_SECONDS in another worktree → deleted."""
        vault = _make_vault(tmp_path)
        s = load_sessions()
        orphan = _skeleton_note(vault, worktree="synth-orphan")
        # Backdate well past the resume window
        old_mtime = time.time() - (s.RESUME_WINDOW_SECONDS + 120)
        os.utime(orphan, (old_mtime, old_mtime))
        current_notes = {vault / "sessions" / "2099-01-01-0000-synth-current.md"}
        sweep = self._load_sweep()
        sweep(vault, exclude=current_notes)
        assert not orphan.exists(), "old orphan skeleton should have been swept"

    def test_recent_skeleton_in_other_worktree_is_kept(self, tmp_path):
        """A skeleton newer than RESUME_WINDOW_SECONDS → not swept (may be mid-bootstrap)."""
        vault = _make_vault(tmp_path)
        s = load_sessions()
        recent = _skeleton_note(vault, worktree="synth-recent")
        # Recent mtime (default — just created)
        current_notes: set[Path] = set()
        sweep = self._load_sweep()
        sweep(vault, exclude=current_notes)
        assert recent.exists(), "recent skeleton should be kept (within resume window)"

    def test_current_worktree_notes_never_swept(self, tmp_path):
        """Notes in the exclude set (current worktree) are never touched."""
        vault = _make_vault(tmp_path)
        s = load_sessions()
        current = _skeleton_note(vault, worktree="synth-current")
        # Backdate to make it eligible by age
        old_mtime = time.time() - (s.RESUME_WINDOW_SECONDS + 120)
        os.utime(current, (old_mtime, old_mtime))
        # Include it in the exclude set — it is the current worktree note
        sweep = self._load_sweep()
        sweep(vault, exclude={current})
        assert current.exists(), "current-worktree note must never be swept"

    def test_real_content_note_not_swept_even_if_old(self, tmp_path):
        """An old note with real content in another worktree is NOT swept."""
        vault = _make_vault(tmp_path)
        s = load_sessions()
        real = _real_content_note(vault, worktree="synth-real-old")
        old_mtime = time.time() - (s.RESUME_WINDOW_SECONDS + 120)
        os.utime(real, (old_mtime, old_mtime))
        sweep = self._load_sweep()
        sweep(vault, exclude=set())
        assert real.exists(), "real-content note must not be swept even if old"

    def test_sessions_dir_missing_does_not_raise(self, tmp_path):
        """Missing sessions/ directory → sweep is a no-op, no exception."""
        vault = tmp_path / "vault"
        vault.mkdir()
        sweep = self._load_sweep()
        sweep(vault, exclude=set())  # should not raise

    def test_sweep_called_from_finalize_hook(self, tmp_path):
        """End-to-end: WorktreeRemove sweeps old orphan skeletons in other worktrees."""
        vault = _git_vault(tmp_path)
        # Current worktree has a real note
        current = _real_content_note(vault, worktree="synth-main")
        # Orphan skeleton in another worktree, backdated past resume window
        s = load_sessions()
        orphan = _skeleton_note(vault, worktree="synth-stale")
        old_mtime = time.time() - (s.RESUME_WINDOW_SECONDS + 120)
        os.utime(orphan, (old_mtime, old_mtime))

        _run_finalize({"worktree": "synth-main"}, vault)

        # Current note finalized, orphan swept
        assert current.exists()
        assert not orphan.exists(), "orphan skeleton swept by finalize hook"


# ---------------------------------------------------------------------------
# Critical bug C1: tracked skeleton deletions must be committed (clean tree)
# ---------------------------------------------------------------------------

def _git_commit_file(vault: Path, note: Path) -> None:
    """Add and commit a single file into the vault git repo."""
    subprocess.run(["git", "-C", str(vault), "add", "--", str(note)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-m", "add skeleton fixture"],
                   check=True, capture_output=True)


class TestTrackedSkeletonDeletionCommitted:
    """Deletions of tracked skeleton notes must be staged and committed so the
    vault tree is clean after WorktreeRemove.

    The bug: `commit_vault` only staged `finalized_notes` (the modifications).
    Tracked file deletions were never staged, leaving the vault dirty.
    """

    def test_tracked_skeleton_deletion_leaves_clean_tree(self, tmp_path):
        """WorktreeRemove a tracked skeleton → file gone AND git tree clean."""
        vault = _git_vault(tmp_path)
        note = _skeleton_note(vault, worktree="synth-tracked-del")
        _git_commit_file(vault, note)

        # Confirm note is now tracked and clean
        rc, out, _ = _git_status(vault)
        assert rc == 0 and not out.strip(), "vault should be clean before hook runs"

        _run_finalize({"worktree": "synth-tracked-del"}, vault)

        assert not note.exists(), "tracked skeleton should be deleted by hook"
        rc, out, _ = _git_status(vault)
        assert rc == 0 and not out.strip(), (
            f"vault should be clean after deletion committed; got: {out!r}"
        )

    def test_all_skeleton_case_commits_deletion(self, tmp_path):
        """When ALL notes for the worktree are skeletons (no finalized_notes),
        the commit must still run with the staged deletions."""
        vault = _git_vault(tmp_path)
        # Two skeleton notes for the same worktree — both tracked
        note1 = _skeleton_note(vault, worktree="synth-all-skel")
        _git_commit_file(vault, note1)
        # Rename second note to simulate two notes for same worktree
        note2_path = vault / "sessions" / "2099-01-02-0000-synth-all-skel.md"
        note2_path.write_text(note1.read_text())
        _git_commit_file(vault, note2_path)

        _run_finalize({"worktree": "synth-all-skel"}, vault)

        assert not note1.exists(), "first skeleton deleted"
        assert not note2_path.exists(), "second skeleton deleted"
        rc, out, _ = _git_status(vault)
        assert rc == 0 and not out.strip(), (
            f"all-skeleton case: vault should be clean; got: {out!r}"
        )

    def test_tracked_orphan_skeleton_sweep_leaves_clean_tree(self, tmp_path):
        """Orphan skeletons deleted by sweep_orphan_skeletons must also be committed."""
        vault = _git_vault(tmp_path)
        # Current worktree has a real note (untracked — drives the finalize path)
        current = _real_content_note(vault, worktree="synth-real-current")
        # Orphan skeleton tracked in git, backdated
        s = load_sessions()
        orphan = _skeleton_note(vault, worktree="synth-orphan-tracked")
        _git_commit_file(vault, orphan)
        old_mtime = time.time() - (s.RESUME_WINDOW_SECONDS + 120)
        os.utime(orphan, (old_mtime, old_mtime))

        _run_finalize({"worktree": "synth-real-current"}, vault)

        assert not orphan.exists(), "tracked orphan skeleton should be swept"
        rc, out, _ = _git_status(vault)
        assert rc == 0 and not out.strip(), (
            f"vault should be clean after orphan deletion committed; got: {out!r}"
        )


def _git_status(vault: Path) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", "-C", str(vault), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
