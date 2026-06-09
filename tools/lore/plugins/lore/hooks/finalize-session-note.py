#!/usr/bin/env python3
"""WorktreeRemove hook: finalize the session note for the worktree being
removed and commit the vault.

Resolves the vault via `resolve_vault()`. For each session note matching the
removed worktree, set `status: complete` and fill `ended:` (UTC), written
atomically so a kill mid-write can never leave a half-written note. Then, if
the vault is itself a git repo, commit the change.

Git-tree guard: before committing, assert `git -C <vault> rev-parse
--show-toplevel` resolves to the resolved vault path. If it does not (the vault
is not a git repo, or $LORE_VAULT points inside some other tree), skip the
commit with a logged notice rather than operating on the wrong tree.

Never raises — never blocks worktree removal.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sessions  # noqa: E402
from sessions import (  # noqa: E402
    finalize_note,
    is_skeleton_body,
    sweep_orphan_skeletons,
    write_note_atomic,
)
from vault import resolve_vault  # noqa: E402


def read_stdin_json() -> dict:
    try:
        data = sys.stdin.read()
        if not data.strip():
            return {}
        return json.loads(data)
    except Exception:
        return {}


def get_worktree_name_from_payload(payload: dict) -> str:
    for key in ("worktree_name", "worktree", "name"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            return v
    for key in ("worktree_path", "path", "cwd"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            return Path(v).name
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return Path(cwd).name


def git(vault: Path, *args: str) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), *args],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
    except Exception as e:  # noqa: BLE001
        return 1, "", f"{type(e).__name__}: {e}"


def vault_is_git_toplevel(vault: Path) -> bool:
    """True iff `git rev-parse --show-toplevel` for the vault resolves to the
    vault path itself — never operate on a parent or unrelated tree."""
    rc, out, _ = git(vault, "rev-parse", "--show-toplevel")
    if rc != 0 or not out:
        return False
    try:
        return Path(out).resolve() == Path(vault).resolve()
    except Exception:
        return False


def commit_vault(
    vault: Path,
    worktree_name: str,
    note_paths: list[Path],
    deleted_paths: list[Path],
) -> None:
    if not vault_is_git_toplevel(vault):
        print(
            "finalize-session-note: vault is not its own git toplevel "
            f"({vault}) — skipping commit",
            file=sys.stderr,
        )
        return

    rc, out, _ = git(vault, "status", "--porcelain")
    if rc != 0 or not out.strip():
        return

    for note in note_paths:
        git(vault, "add", "--", str(note))
    for deleted in deleted_paths:
        git(vault, "add", "--", str(deleted))

    rc, staged_out, _ = git(vault, "diff", "--cached", "--name-only")
    if rc != 0 or not staged_out.strip():
        return

    subject = f"session: finalize {worktree_name}"
    rc, _, stderr = git(vault, "commit", "-m", subject)
    if rc != 0:
        print(f"finalize-session-note: commit failed: {stderr[:200]}", file=sys.stderr)


def main() -> int:
    payload = read_stdin_json()
    vault = Path(resolve_vault())
    if not vault.exists():
        print(json.dumps({}))
        return 0

    worktree_name = get_worktree_name_from_payload(payload)
    if not worktree_name:
        print(json.dumps({}))
        return 0

    notes = sessions.all_session_notes_for_worktree(vault, worktree_name)
    if not notes:
        print(json.dumps({}))
        return 0

    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    finalized_notes: list[Path] = []
    deleted_notes: list[Path] = []
    for note in notes:
        try:
            if is_skeleton_body(note):
                note.unlink()
                deleted_notes.append(note)
            else:
                finalize_note(note, now_iso)
                finalized_notes.append(note)
        except Exception as e:  # noqa: BLE001
            print(f"finalize-session-note: {note.name}: {type(e).__name__}: {e}",
                  file=sys.stderr)

    try:
        swept = sweep_orphan_skeletons(vault, exclude=set(notes))
        deleted_notes.extend(swept)
    except Exception as e:  # noqa: BLE001
        print(f"finalize-session-note: sweep: {type(e).__name__}: {e}", file=sys.stderr)

    try:
        commit_vault(vault, worktree_name, finalized_notes, deleted_notes)
    except Exception as e:  # noqa: BLE001
        print(f"finalize-session-note: commit: {type(e).__name__}: {e}", file=sys.stderr)

    print(json.dumps({}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
