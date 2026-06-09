#!/usr/bin/env python3
"""SessionStart hook: ensure a session note exists for this worktree and emit
the baseline vault index.

Resolves the vault via `resolve_vault()` ($LORE_VAULT, default ~/lore). Creates
or resumes a `YYYY-MM-DD-HHMM-<worktree>.md` session note (worktree = CWD
basename), then emits a baseline index (vault stats, the session-note pointer,
and the capture-command reminder).

Branch-keyword area recall was removed (2026-06-05): every camp branch is
`worktree-<slug>`, so the `worktree` keyword matched on every session and the
recall block became noise. To be reintroduced in a smarter form later; see the
deferred note in the vault.

Never raises — on any error emits `{}` so it can never block session start.
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
import link_server  # noqa: E402
from vault import resolve_project, resolve_vault  # noqa: E402

FOOTGUN_WARNING = (
    "LORE_VAULT unset — using ~/lore; run `lore init` or set the env var"
)


def read_stdin_json() -> dict:
    try:
        data = sys.stdin.read()
        if not data.strip():
            return {}
        return json.loads(data)
    except Exception:
        return {}


def get_worktree_path() -> Path:
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return Path(cwd)


def get_branch_name(repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


def footgun_warning() -> str | None:
    """Return the footgun warning when $LORE_VAULT is unset AND ~/lore is
    absent — a mis-set path would otherwise silently fork the vault."""
    if os.environ.get("LORE_VAULT", "").strip():
        return None
    if (Path.home() / "lore").exists():
        return None
    return FOOTGUN_WARNING


def build_context(session_id: str) -> str | None:
    vault = Path(resolve_vault())
    worktree = get_worktree_path()
    worktree_name = worktree.name
    project = resolve_project(worktree)
    branch = get_branch_name(worktree)
    warning = footgun_warning()

    session_note, created = sessions.ensure_session_note(
        vault=vault,
        worktree_name=worktree_name,
        branch=branch,
        project=project,
        now_iso=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now_human=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        session_id=session_id,
    )

    session_note_display: str | None = None
    if session_note is not None:
        try:
            vault_rel = str(session_note.relative_to(vault))
            state_dir_env = os.environ.get("LORE_LINK_STATE_DIR", "")
            state_dir = (
                Path(state_dir_env) if state_dir_env
                else link_server.DEFAULT_STATE_DIR
            )
            session_note_display = link_server.note_link(vault_rel, state_dir=state_dir)
        except Exception:
            pass

    index = sessions.render_vault_index(
        vault=vault,
        worktree_name=worktree_name,
        project=project,
        session_note=session_note,
        session_created=created,
        warning=warning,
        session_note_display=session_note_display,
    )

    return index


def main() -> int:
    payload = read_stdin_json()
    session_id = payload.get("session_id", "") if isinstance(payload, dict) else ""
    try:
        context = build_context(session_id)
    except Exception as e:  # noqa: BLE001
        print(f"session-context: {type(e).__name__}: {e}", file=sys.stderr)
        context = None

    if not context:
        print(json.dumps({}))
        return 0
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
