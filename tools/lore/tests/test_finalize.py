"""Slice 7 tests: lore finish CLI subcommand.

Covers (TDD — written before implementation):
- lore finish: finds the active session note for the current worktree, sets
  status: complete and ended: (non-empty UTC timestamp), and commits.
- lore finish with no session note: exits 0, prints a notice, no error.
- The finalized note passes status_validator for type=session.
- The commit is made (atomic write + git) when vault is a proper git toplevel.
- A non-git vault: status is set but commit is skipped (soft-fail).
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


def run_cli(args, env=None, cwd=None, input_text=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True, text=True, env=full_env, cwd=cwd, input=input_text,
    )


def load_script(name: str):
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    for cached in (name, "vault", "frontmatter", "status_validator", "sessions"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "sessions").mkdir(parents=True)
    return vault


def _git_vault(tmp_path: Path) -> Path:
    """A vault that is its own git repo (toplevel == vault)."""
    vault = _make_vault(tmp_path)
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "t@e.st"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "Tester"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "commit.gpgsign", "false"],
                   check=True, capture_output=True)
    return vault


def _seed_session_note(vault: Path, worktree: str = "my-worktree") -> Path:
    """Write a minimal active session note with the correct filename format."""
    sessions_dir = vault / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    note = sessions_dir / f"2026-06-02-1200-{worktree}.md"
    note.write_text(
        f"---\n"
        f"type: session\n"
        f"project: test-project\n"
        f"worktree: {worktree}\n"
        f"branch: main\n"
        f"started: 2026-06-02T12:00:00Z\n"
        f"ended:\n"
        f"subsystems: []\n"
        f"phase: Orient\n"
        f"session_id: sid-1\n"
        f"status: active\n"
        f"---\n\n"
        f"# Session: {worktree}\n\n"
        f"## What we did\n\n"
        f"## Decided\n\n"
        f"## Deferred\n\n"
        f"## Learned\n\n"
        f"## Open questions\n"
    )
    return note


# ---------------------------------------------------------------------------
# lore finish: sets status: complete + ended:
# ---------------------------------------------------------------------------

class TestLoreFinishSetsStatus:
    def test_sets_status_complete(self, tmp_path):
        vault = _git_vault(tmp_path)
        note = _seed_session_note(vault, worktree="my-worktree")
        fake_cwd = tmp_path / "my-worktree"
        fake_cwd.mkdir()
        result = run_cli(
            ["finish"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        assert result.returncode == 0, result.stderr
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "complete"

    def test_sets_nonempty_ended(self, tmp_path):
        vault = _git_vault(tmp_path)
        note = _seed_session_note(vault, worktree="my-worktree")
        fake_cwd = tmp_path / "my-worktree"
        fake_cwd.mkdir()
        run_cli(
            ["finish"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm.get("ended"), f"ended is empty: {fm.get('ended')!r}"

    def test_finalized_note_passes_status_validator(self, tmp_path):
        vault = _git_vault(tmp_path)
        note = _seed_session_note(vault, worktree="my-worktree")
        fake_cwd = tmp_path / "my-worktree"
        fake_cwd.mkdir()
        run_cli(
            ["finish"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        fm = load_script("frontmatter").parse_frontmatter(note)
        sv = load_script("status_validator")
        assert sv.is_valid_status(fm["type"], fm["status"])

    def test_commits_after_finalize(self, tmp_path):
        vault = _git_vault(tmp_path)
        _seed_session_note(vault, worktree="my-worktree")
        fake_cwd = tmp_path / "my-worktree"
        fake_cwd.mkdir()
        run_cli(
            ["finish"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        log = subprocess.run(
            ["git", "-C", str(vault), "log", "--oneline"],
            capture_output=True, text=True,
        )
        assert log.returncode == 0
        assert log.stdout.strip(), "expected a commit in the vault after lore finish"


# ---------------------------------------------------------------------------
# lore finish: no session note → exit 0 + notice
# ---------------------------------------------------------------------------

class TestLoreFinishNoSession:
    def test_exits_zero_with_notice_when_no_session(self, tmp_path):
        vault = _git_vault(tmp_path)
        fake_cwd = tmp_path / "empty-worktree"
        fake_cwd.mkdir()
        result = run_cli(
            ["finish"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        assert result.returncode == 0, result.stderr
        combined = result.stdout + result.stderr
        assert "no active session" in combined.lower() or "nothing to finalize" in combined.lower()

    def test_no_error_when_sessions_dir_missing(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        fake_cwd = tmp_path / "wt"
        fake_cwd.mkdir()
        result = run_cli(
            ["finish"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# lore finish: non-git vault → status set, commit soft-fail
# ---------------------------------------------------------------------------

class TestLoreFinishNonGitVault:
    def test_status_set_even_when_not_git_toplevel(self, tmp_path):
        vault = _make_vault(tmp_path)  # NOT a git repo
        note = _seed_session_note(vault, worktree="my-worktree")
        fake_cwd = tmp_path / "my-worktree"
        fake_cwd.mkdir()
        result = run_cli(
            ["finish"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )
        assert result.returncode == 0, result.stderr
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "complete"
        assert not (vault / ".git").exists()


# ---------------------------------------------------------------------------
# Fix 1 regression: already-complete note + untracked stray file → exit 0, no commit
# ---------------------------------------------------------------------------

class TestFinishNoopWithStrayUntracked:
    """Regression guard for the git status --porcelain bug.

    When the session note is already complete (nothing to stage after `lore
    finish` marks it), an unrelated untracked file in the vault must NOT cause
    cmd_finish to attempt a commit on an empty index — which would make git exit
    1 and propagate a false failure.  The gate must be on the staged index
    (`git diff --cached --quiet`), not the working tree.
    """

    def _seed_complete_note(self, vault: Path, worktree: str = "my-worktree") -> Path:
        """Seed a session note that is ALREADY complete (status: complete)."""
        sessions_dir = vault / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        note = sessions_dir / f"2026-06-02-1200-{worktree}.md"
        note.write_text(
            f"---\n"
            f"type: session\n"
            f"project: test-project\n"
            f"worktree: {worktree}\n"
            f"branch: main\n"
            f"started: 2026-06-02T12:00:00Z\n"
            f"ended: 2026-06-02T13:00:00Z\n"
            f"subsystems: []\n"
            f"phase: Orient\n"
            f"session_id: sid-1\n"
            f"status: complete\n"
            f"---\n\n"
            f"# Session: {worktree}\n\n"
            f"## What we did\n\nDone.\n"
        )
        return note

    def test_noop_finish_with_stray_untracked_exits_zero(self, tmp_path):
        """Already-complete note + untracked stray file → cmd_finish returns 0."""
        vault = _git_vault(tmp_path)
        self._seed_complete_note(vault, worktree="my-worktree")
        # commit the complete note so the vault is clean
        subprocess.run(["git", "-C", str(vault), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(vault), "commit", "-m", "baseline"],
                       check=True, capture_output=True)
        # add an unrelated untracked file (not staged, not committed)
        stray = vault / "sessions" / "scratch-untracked.md"
        stray.write_text("# Not yet tracked\n")
        commit_count_before = subprocess.run(
            ["git", "-C", str(vault), "rev-list", "--count", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()

        fake_cwd = tmp_path / "my-worktree"
        fake_cwd.mkdir(exist_ok=True)
        result = run_cli(
            ["finish"],
            env={"LORE_VAULT": str(vault)},
            cwd=str(fake_cwd),
        )

        assert result.returncode == 0, (
            f"cmd_finish must return 0 for a no-op finish with a stray untracked file.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # no new commit should have been made
        commit_count_after = subprocess.run(
            ["git", "-C", str(vault), "rev-list", "--count", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert commit_count_before == commit_count_after, (
            f"cmd_finish must NOT commit when there is nothing staged "
            f"(commits before={commit_count_before}, after={commit_count_after})."
        )
