"""P3B2-4: lore `shelved` + `resume` CLI subcommands (pickup surface).

TDD: tests written before implementation. All fixtures are SYNTHETIC
(invented vocabulary, no real vault/session/repo names).

`find_shelved_notes` + `resume_note` already exist in sessions.py (P3B2-2) but
are NOT reachable via the CLI. The forge `/forge:pickup` skill can only call
lore through PATH/CLI (cross-plugin), so this slice exposes:

  - `lore shelved [--slug SLUG]`  list shelved/handoff notes, most-recent-first,
    printing each note's path + timestamp + a short context fragment (first
    Pickup-hints line, else the title) so an interactive list is usable.
  - `lore resume <file-or-slug>`  flip a shelved/handoff note → active via
    resume_note; clear no-op message on a non-shelved note.

Covers:
- shelved: lists shelved+handoff, excludes active/complete, most-recent-first
- shelved: --slug filter narrows
- shelved: prints path + timestamp + a context fragment per note
- shelved: empty vault → clear "nothing shelved" message, exit 0
- resume: by explicit file path flips shelved → active, prints confirmation
- resume: by slug flips shelved → active
- resume: non-shelved (active/complete) note → clear message, no false success
- resume: missing note → error message, non-zero
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


def _write_session_note(
    vault: Path,
    filename: str,
    status: str = "active",
    started: str = "2026-01-01T10:00:00Z",
    ended: str | None = None,
    pickup_hints: str | None = None,
) -> Path:
    sessions_dir = vault / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    note = sessions_dir / filename
    ended_line = f"ended: {ended}" if ended else "ended:"
    hints_block = (
        f"## Pickup hints\n\n{pickup_hints}\n\n" if pickup_hints else "## Pickup hints\n\n"
    )
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
        f"status: {status}\n"
        f"---\n\n"
        f"# Session: alpha-worktree\n\n"
        f"{hints_block}"
        f"## What we did\n\n"
    )
    return note


# ===========================================================================
# lore shelved
# ===========================================================================

class TestLoreShelvedList:
    def test_lists_shelved_note(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_session_note(
            vault, "2026-01-02-0900-beta-worktree.md",
            status="shelved", ended="2026-01-02T09:00:00Z",
        )
        result = run_cli(["shelved"], env={"LORE_VAULT": str(vault)})
        assert result.returncode == 0, result.stderr
        assert "2026-01-02-0900-beta-worktree.md" in result.stdout

    def test_lists_handoff_note(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_session_note(
            vault, "2026-01-02-0900-beta-worktree.md",
            status="handoff", ended="2026-01-02T09:00:00Z",
        )
        result = run_cli(["shelved"], env={"LORE_VAULT": str(vault)})
        assert result.returncode == 0
        assert "2026-01-02-0900-beta-worktree.md" in result.stdout

    def test_excludes_active_and_complete(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_session_note(vault, "2026-01-01-1000-active-worktree.md", status="active")
        _write_session_note(
            vault, "2026-01-01-1100-done-worktree.md",
            status="complete", ended="2026-01-01T11:00:00Z",
        )
        result = run_cli(["shelved"], env={"LORE_VAULT": str(vault)})
        assert result.returncode == 0
        assert "active-worktree" not in result.stdout
        assert "done-worktree" not in result.stdout

    def test_most_recent_first(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_session_note(
            vault, "2026-01-01-0900-older-worktree.md",
            status="shelved", ended="2026-01-01T09:00:00Z",
        )
        _write_session_note(
            vault, "2026-01-03-0900-newer-worktree.md",
            status="shelved", ended="2026-01-03T09:00:00Z",
        )
        result = run_cli(["shelved"], env={"LORE_VAULT": str(vault)})
        assert result.returncode == 0
        newer_idx = result.stdout.index("newer-worktree")
        older_idx = result.stdout.index("older-worktree")
        assert newer_idx < older_idx, "most-recent must be listed first"

    def test_slug_filter_narrows(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_session_note(
            vault, "2026-01-02-0900-target-worktree.md",
            status="shelved", ended="2026-01-02T09:00:00Z",
        )
        _write_session_note(
            vault, "2026-01-01-0900-other-worktree.md",
            status="shelved", ended="2026-01-01T09:00:00Z",
        )
        result = run_cli(["shelved", "--slug", "target-worktree"], env={"LORE_VAULT": str(vault)})
        assert result.returncode == 0
        assert "target-worktree" in result.stdout
        assert "other-worktree" not in result.stdout

    def test_prints_timestamp(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_session_note(
            vault, "2026-01-02-0900-beta-worktree.md",
            status="shelved", ended="2026-01-02T09:00:00Z",
        )
        result = run_cli(["shelved"], env={"LORE_VAULT": str(vault)})
        assert "2026-01-02T09:00:00Z" in result.stdout

    def test_prints_context_fragment_from_pickup_hints(self, tmp_path):
        vault = _make_vault(tmp_path)
        _write_session_note(
            vault, "2026-01-02-0900-beta-worktree.md",
            status="shelved", ended="2026-01-02T09:00:00Z",
            pickup_hints="Next: finish the parser refactor",
        )
        result = run_cli(["shelved"], env={"LORE_VAULT": str(vault)})
        assert "Next: finish the parser refactor" in result.stdout

    def test_empty_vault_clear_message(self, tmp_path):
        vault = _make_vault(tmp_path)
        result = run_cli(["shelved"], env={"LORE_VAULT": str(vault)})
        assert result.returncode == 0
        combined = (result.stdout + result.stderr).lower()
        assert "nothing" in combined or "no shelved" in combined


# ===========================================================================
# lore resume
# ===========================================================================

class TestLoreResume:
    def test_resume_by_path_flips_to_active(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="shelved", ended="2026-01-01T11:00:00Z",
        )
        result = run_cli(["resume", str(note)], env={"LORE_VAULT": str(vault)})
        assert result.returncode == 0, result.stderr
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "active"

    def test_resume_prints_confirmation(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="shelved", ended="2026-01-01T11:00:00Z",
        )
        result = run_cli(["resume", str(note)], env={"LORE_VAULT": str(vault)})
        combined = (result.stdout + result.stderr).lower()
        assert "resumed" in combined or "active" in combined

    def test_resume_handoff_note(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="handoff", ended="2026-01-01T11:00:00Z",
        )
        result = run_cli(["resume", str(note)], env={"LORE_VAULT": str(vault)})
        assert result.returncode == 0
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "active"

    def test_resume_by_slug_flips_to_active(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-02-0900-target-worktree.md",
            status="shelved", ended="2026-01-02T09:00:00Z",
        )
        result = run_cli(["resume", "target-worktree"], env={"LORE_VAULT": str(vault)})
        assert result.returncode == 0, result.stderr
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "active"

    def test_resume_already_active_clear_message_no_false_success(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(vault, "2026-01-01-1000-alpha-worktree.md", status="active")
        result = run_cli(["resume", str(note)], env={"LORE_VAULT": str(vault)})
        combined = (result.stdout + result.stderr).lower()
        # A clear "not shelved / nothing to resume" message — not a false "resumed".
        assert "not" in combined or "already active" in combined or "nothing" in combined
        assert "resumed" not in result.stdout.lower().replace("not resumed", "")

    def test_resume_complete_note_no_op_message(self, tmp_path):
        vault = _make_vault(tmp_path)
        note = _write_session_note(
            vault, "2026-01-01-1000-alpha-worktree.md",
            status="complete", ended="2026-01-01T11:00:00Z",
        )
        result = run_cli(["resume", str(note)], env={"LORE_VAULT": str(vault)})
        combined = (result.stdout + result.stderr).lower()
        assert "not" in combined or "nothing" in combined
        # Status must be unchanged.
        fm = load_script("frontmatter").parse_frontmatter(note)
        assert fm["status"] == "complete"

    def test_resume_missing_note_errors_nonzero(self, tmp_path):
        vault = _make_vault(tmp_path)
        result = run_cli(["resume", "no-such-worktree"], env={"LORE_VAULT": str(vault)})
        assert result.returncode != 0
        combined = (result.stdout + result.stderr).lower()
        assert "no" in combined or "not found" in combined or "error" in combined
