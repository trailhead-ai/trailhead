"""KU-1 assumption prover: lore finish --worktree <slug> resolves a camp-created note.

Assumption to prove:
  A session note created by a camp workspace (where the worktree-name stored in the
  note IS the workspace slug — because detect_worktree_name() returns
  Path($CLAUDE_PROJECT_DIR).name and Claude is launched at workspace_dir =
  central_state_dir(group)/worktrees/<slug>) is finalized by
  `lore finish --worktree <slug>`.

The test is EPHEMERAL. After the assumption is proven, this file should be removed.

Test file to clean up:
  tools/lore/tests/test_ku1_assumption_prover.py
  (the entirety of this file, lines 1 to EOF)
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"


def _run_lore(args, *, vault: Path, tmp_path: Path, **extra_env):
    """Run the lore CLI subprocess with an isolated vault and state dir."""
    env = dict(os.environ)
    env["LORE_VAULT"] = str(vault)
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg_config")
    env["LORE_EMAIL"] = "tester@example.com"
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _git_vault(tmp_path: Path) -> Path:
    """Create a minimal git-backed vault."""
    vault = tmp_path / "vault"
    (vault / "sessions").mkdir(parents=True)
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "t@e.st"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "Tester"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "commit.gpgsign", "false"],
                   check=True, capture_output=True)
    return vault


def _seed_camp_session_note(vault: Path, slug: str) -> Path:
    """Write a camp-shaped active session note where worktree = slug.

    This mirrors what detect_worktree_name() produces when Claude Code is
    launched via 'camp ai <slug>' with cwd=workspace_dir (named <slug>):
      Path($CLAUDE_PROJECT_DIR).name  →  <slug>
    That name is passed to ensure_session_note as worktree_name, stored in
    both the frontmatter 'worktree:' field and the filename stem.
    """
    sessions_dir = vault / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    # Filename follows the YYYY-MM-DD-HHMM-<worktree>.md convention
    note = sessions_dir / f"2026-06-21-1200-{slug}.md"
    note.write_text(
        f"---\n"
        f"type: session\n"
        f"project: test-project\n"
        f"worktree: {slug}\n"
        f"branch: worktree-{slug}\n"
        f"started: 2026-06-21T12:00:00Z\n"
        f"ended:\n"
        f"areas: []\n"
        f"phase: Orient\n"
        f"session_id:\n"
        f"status: active\n"
        f"---\n\n"
        f"# Session: {slug}\n\n"
        f"## What we did\n\n"
        f"## Decided\n\n"
        f"## Learned\n\n"
        f"## Open questions\n"
    )
    return note


def _parse_frontmatter(note: Path) -> dict:
    """Parse the YAML frontmatter block (stdlib only, no PyYAML)."""
    text = note.read_text()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    block = text[4:end]  # skip opening "---\n"
    fm: dict = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip()
    return fm


class TestKU1LoreFinishWorktreeSlug:
    """KU-1: lore finish --worktree <slug> resolves a camp-shaped session note."""

    def test_finish_with_slug_finds_camp_shaped_note(self, tmp_path):
        """
        Given: A vault with an active session note where worktree == slug
               (the shape a camp workspace produces).
        When:  lore finish --worktree <slug> is called.
        Then:  The note transitions to status: complete with a non-empty ended: field.

        This validates KU-1: the --worktree selector matches how camp names session
        notes, so the planned 'camp rm' finalize step in reconcile_break() is not
        a silent no-op.
        """
        slug = "lore-refactor"  # Representative camp slug
        vault = _git_vault(tmp_path)

        # Seed a note the same way a camp session creates it:
        # detect_worktree_name() -> Path($CLAUDE_PROJECT_DIR).name -> slug
        note = _seed_camp_session_note(vault, slug)

        # Verify initial state
        fm_before = _parse_frontmatter(note)
        assert fm_before["status"] == "active", (
            f"Expected status: active before finish, got: {fm_before.get('status')}"
        )
        assert fm_before.get("ended", "") == "", (
            f"Expected ended: empty before finish, got: {fm_before.get('ended')}"
        )

        # This is the exact call that Slice 1 inserts into reconcile_break()
        result = _run_lore(
            ["finish", "--worktree", slug],
            vault=vault,
            tmp_path=tmp_path,
        )

        assert result.returncode == 0, (
            f"lore finish --worktree {slug!r} should exit 0.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # The note must transition to status: complete
        fm_after = _parse_frontmatter(note)
        assert fm_after.get("status") == "complete", (
            f"Expected status: complete after finish, got: {fm_after.get('status')!r}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}\n"
            f"Note content:\n{note.read_text()}"
        )

        # ended: must be non-empty (UTC timestamp)
        assert fm_after.get("ended", ""), (
            f"Expected non-empty ended: after finish, got: {fm_after.get('ended')!r}.\n"
            f"Note content:\n{note.read_text()}"
        )

        # The stdout must mention the note was finalized (not the no-op notice)
        combined = result.stdout + result.stderr
        assert "Finalized:" in combined or "finalized" in combined.lower(), (
            f"Expected 'Finalized:' in output, got:\n{combined}"
        )

    def test_finish_no_op_for_different_slug(self, tmp_path):
        """
        Negative control: --worktree with a non-matching slug exits 0 with a notice
        (does NOT finalize the note for a different worktree-name).

        This confirms that --worktree is the specific selector and won't
        accidentally finalize a note belonging to another workspace.
        """
        slug = "lore-refactor"
        wrong_slug = "some-other-workspace"
        vault = _git_vault(tmp_path)
        note = _seed_camp_session_note(vault, slug)

        result = _run_lore(
            ["finish", "--worktree", wrong_slug],
            vault=vault,
            tmp_path=tmp_path,
        )

        assert result.returncode == 0, (
            f"lore finish with non-matching worktree should still exit 0.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "no active session" in combined.lower() or "nothing to finalize" in combined.lower(), (
            f"Expected no-op notice, got:\n{combined}"
        )

        # The original note must remain active (unaffected)
        fm = _parse_frontmatter(note)
        assert fm.get("status") == "active", (
            f"Note for {slug!r} should still be active when --worktree {wrong_slug!r} was passed.\n"
            f"status was: {fm.get('status')}"
        )
