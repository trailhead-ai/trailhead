"""KU1 assumption probe: does `lore session candidate` → `lore finish` finalize
the SAME artifact that candidate was written to?

The plan (KU1) suspects a split:
  - `lore session candidate` writes to `sessions/<GUID>.md` (no frontmatter,
    body-only, via session_store.create_or_append)
  - `lore finish` resolves via vault.resolve_session_note, which:
    1. find_session_note_by_session_id: requires `text.startswith("---")` (frontmatter)
    2. find_session_note (worktree fallback): requires date-prefix `YYYY-MM-DD...` filename

If the GUID file is body-only (no frontmatter) and has a UUID filename (not
date-prefixed), NEITHER resolver arm can find it — finish silently prints
"no active session note found" and exits 0 without finalizing anything.

This test proves or disproves that claim by running the actual CLI sequence
end-to-end and inspecting which file was finalized (if any).

EPHEMERAL — remove after KU1 gate. File to clean up:
  tools/lore/tests/test_ku1_session_note_coherence_TEMP.py (entire file)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"

SID = "11111111-2222-4333-8444-555555555555"


def _run(args, *, vault: Path, state_dir: Path, stdin_text=None, env_extra=None):
    """Run lore CLI as subprocess with isolated vault + XDG dirs."""
    full_env = dict(os.environ)
    full_env["LORE_VAULT"] = str(vault)
    full_env["XDG_STATE_HOME"] = str(state_dir)
    full_env["XDG_CONFIG_HOME"] = str(state_dir / "_xdg_config")
    full_env["LORE_EMAIL"] = "tester@example.com"
    # Clear ambient session/project env that could confuse resolution.
    for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_PROJECT_DIR"):
        full_env.pop(k, None)
    if env_extra:
        full_env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=full_env,
        input=stdin_text,
    )


def _git_vault(tmp_path: Path) -> tuple[Path, Path]:
    """A vault that is its own git repo + isolated state dir."""
    vault = tmp_path / "vault"
    (vault / "sessions").mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir(parents=True)
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    for k, v in (("user.email", "t@e.st"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(vault), "config", k, v],
                       check=True, capture_output=True)
    return vault, state


class TestKU1SessionNoteCoherence:
    """The critical assumption gate: candidate → finish lands on the same artifact."""

    def test_finish_finds_candidate_file(self, tmp_path):
        """VALIDATES if finish finds and finalizes the same file candidate wrote to.
        INVALIDATES if finish prints 'no active session note' (file not found),
        meaning candidate wrote to a file that finish cannot resolve.
        """
        vault, state = _git_vault(tmp_path)

        # Step 1: lore session candidate writes a capture to sessions/<GUID>.md
        r = _run(
            ["session", "candidate",
             "--session-id", SID,
             "--kind", "lesson",
             "--phase", "Build"],
            vault=vault, state_dir=state,
            stdin_text="a lesson captured during the session\n",
            env_extra={"CLAUDE_CODE_SESSION_ID": SID},
        )
        assert r.returncode == 0, f"candidate failed: {r.stderr}"

        # The file candidate wrote to.
        candidate_file = vault / "sessions" / f"{SID}.md"
        assert candidate_file.exists(), "candidate must create sessions/<GUID>.md"
        candidate_text = candidate_file.read_text()
        assert "lesson" in candidate_text, "candidate body must appear in the file"

        # Step 2: lore session-note — does it find the candidate file?
        r_note = _run(
            ["session-note", "--session-id", SID],
            vault=vault, state_dir=state,
            env_extra={"CLAUDE_CODE_SESSION_ID": SID},
        )
        # Print for diagnostic in test failure output.
        print(f"session-note stdout: {r_note.stdout!r}")
        print(f"session-note stderr: {r_note.stderr!r}")
        print(f"session-note rc: {r_note.returncode}")

        # Step 3: lore finish — does it finalize the candidate file?
        r_finish = _run(
            ["finish", "--session-id", SID],
            vault=vault, state_dir=state,
            env_extra={"CLAUDE_CODE_SESSION_ID": SID},
        )
        print(f"finish stdout: {r_finish.stdout!r}")
        print(f"finish stderr: {r_finish.stderr!r}")
        print(f"finish rc: {r_finish.returncode}")

        # Check what files exist in sessions/ after finish.
        sessions_dir = vault / "sessions"
        all_files = list(sessions_dir.iterdir())
        print(f"sessions/ contents: {[f.name for f in all_files]}")

        # The probe assertion: if KU1 is VALIDATED (coherent), finish must have
        # finalized the candidate file — setting status: complete on it.
        candidate_after = candidate_file.read_text()
        print(f"candidate file after finish:\n{candidate_after}")

        # The key check: was status: complete written into the candidate file?
        finalized_here = "status: complete" in candidate_after

        # Also check whether finish claimed it found nothing.
        finish_said_no_note = "no active session note" in r_finish.stdout

        # Now assert the coherence property:
        # VALIDATED = finish found the file AND finalized it.
        # INVALIDATED = finish said "no active session note" (GUID-log vs longform-note split).
        assert not finish_said_no_note, (
            "INVALIDATED: `lore finish` printed 'no active session note' after "
            "`lore session candidate` wrote to sessions/<GUID>.md. "
            "The two commands target different artifacts:\n"
            f"  candidate wrote to: {candidate_file}\n"
            f"  finish resolved:    (nothing — resolve_session_note returned None)\n"
            f"  finish output: {r_finish.stdout!r}\n"
            f"  session-note output: {r_note.stdout!r} / {r_note.stderr!r}\n"
            "This is the KU1 gap: GUID-named body-only files have no frontmatter "
            "(so find_session_note_by_session_id skips them) and no date-prefix "
            "(so find_session_note's _DATE_PREFIX_RE filter skips them)."
        )
        assert finalized_here, (
            "INVALIDATED: finish did not set 'status: complete' on the candidate file. "
            f"candidate file contents after finish:\n{candidate_after}"
        )

    def test_session_note_cli_finds_candidate_file(self, tmp_path):
        """Does `lore session-note --session-id <GUID>` resolve the GUID file?

        This is the same resolution path that `checkpoint` would use to READ the
        active note. If this also returns exit 1 (not found), checkpoint cannot
        read what candidate wrote.
        """
        vault, state = _git_vault(tmp_path)

        # Write a candidate (creates sessions/<GUID>.md body-only).
        r = _run(
            ["session", "candidate",
             "--session-id", SID,
             "--kind", "decision",
             "--phase", "Orient"],
            vault=vault, state_dir=state,
            stdin_text="a decision to record\n",
            env_extra={"CLAUDE_CODE_SESSION_ID": SID},
        )
        assert r.returncode == 0, f"candidate failed: {r.stderr}"

        candidate_file = vault / "sessions" / f"{SID}.md"
        assert candidate_file.exists()

        # Now ask session-note to resolve it.
        r_note = _run(
            ["session-note", "--session-id", SID],
            vault=vault, state_dir=state,
            env_extra={"CLAUDE_CODE_SESSION_ID": SID},
        )
        print(f"session-note stdout: {r_note.stdout!r}")
        print(f"session-note stderr: {r_note.stderr!r}")

        # VALIDATED if session-note exits 0 and returns the candidate file path.
        # INVALIDATED if session-note exits 1 (can't find the GUID file).
        assert r_note.returncode == 0, (
            "INVALIDATED: `lore session-note --session-id <GUID>` exits 1 — "
            "it cannot locate the file that `lore session candidate` created.\n"
            f"  candidate file: {candidate_file}\n"
            f"  candidate file text (first 200 chars): {candidate_file.read_text()[:200]!r}\n"
            f"  session-note stderr: {r_note.stderr!r}\n"
            "Root cause: find_session_note_by_session_id requires frontmatter "
            "(text.startswith('---')) but create_or_append writes a body-only file "
            "with header '# session: <GUID>\\n\\n' — no frontmatter block."
        )

        # If it did resolve, it should point to the candidate file.
        resolved_rel = r_note.stdout.strip()
        print(f"session-note resolved: {resolved_rel}")
        expected_rel = f"sessions/{SID}.md"
        assert resolved_rel == expected_rel, (
            f"session-note returned {resolved_rel!r}, expected {expected_rel!r}"
        )
