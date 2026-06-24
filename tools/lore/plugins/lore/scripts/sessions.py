"""Shared session-note helpers for the lore plugin.

Importable by the `lore` CLI and tests. Every function takes the resolved vault
path explicitly — there is no module-global vault.

Scope after Slice 2: this module retains only the **plural ``sessions/`` finders**
+ the **orphan-skeleton sweep** for the legacy date-prefixed frontmatter notes.
The session **finalization** lifecycle is gone — Slice 2 replaced ``lore finish``
(which wrote ``status: complete`` via ``finalize_note`` / ``_finalize_body_only``)
with ``lore flush`` (dirty → clean + ``annotations[flushed-at]``, owned by
``session_store.flush_session`` against the singular ``session/`` record), and the
frontmatter-note CREATE path (``ensure_session_note``) was already orphaned by
Slice 1's move to singular indexed records. No code path writes ``complete`` /
``active`` anymore (that vocab was retired in Slice 0).

The SessionStart context-injection hook and its render helpers were removed in
Slice 2, S5 (F5: lore is fully pull — orientation lives in agent-rules + S6 skill
descriptions; no SessionStart hook).
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from vault import iter_note_paths

# Reuse an existing session note for the same worktree if it was touched within
# this many seconds — covers Claude Code restarts/crashes mid-session. Used by the
# orphan-skeleton sweep to spare freshly-created notes from a sibling worktree.
RESUME_WINDOW_SECONDS = 30 * 60


# Matches the mandatory timestamp prefix YYYY-MM-DD-HHMM in a session note stem.
_STEM_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}-(.+)$")


def _worktree_from_stem(stem: str) -> str | None:
    """Extract the worktree name from a session note stem.

    Stem format: ``YYYY-MM-DD-HHMM-<worktree>``.  Returns None if the stem
    does not match the expected format.
    """
    m = _STEM_PREFIX_RE.match(stem)
    return m.group(1) if m else None


def _is_note_for_worktree(path: Path, worktree_name: str) -> bool:
    """True iff the note's stem encodes exactly ``worktree_name``."""
    wt = _worktree_from_stem(path.stem)
    return wt == worktree_name


def session_note_path(vault: Path, worktree_name: str) -> Path | None:
    """Return the newest session note for this worktree, or None."""
    sessions_dir = Path(vault) / "sessions"
    if not sessions_dir.is_dir():
        return None
    for p in sorted(
        iter_note_paths(sessions_dir, recursive=True), key=lambda p: p.name, reverse=True
    ):
        if _is_note_for_worktree(p, worktree_name):
            return p
    return None


def all_session_notes_for_worktree(vault: Path, worktree_name: str) -> list[Path]:
    """Return every session note matching this worktree, newest first."""
    sessions_dir = Path(vault) / "sessions"
    if not sessions_dir.is_dir():
        return []
    return sorted(
        (
            p
            for p in iter_note_paths(sessions_dir, recursive=True)
            if _is_note_for_worktree(p, worktree_name)
        ),
        key=lambda p: p.name,
        reverse=True,
    )


def is_skeleton_body(note: Path) -> bool:
    """Return True if the note body is still the untouched skeleton template.

    A skeleton contains only the title line, the "Started …" line, section
    headings, single-line HTML comment placeholders, and blank lines — no real
    content was ever appended.
    """
    try:
        text = note.read_text()
    except Exception:
        return False
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end < 0:
        return False
    body = text[end + 4:]  # skip past closing "\n---"

    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# Session:"):
            continue
        if line.startswith("Started ") and "on branch" in line:
            continue
        if line.startswith("## ") or line.startswith("### "):
            continue
        if line.startswith("<!--") and line.endswith("-->"):
            continue
        return False
    return True


def sweep_orphan_skeletons(vault: Path, exclude: set[Path]) -> list[Path]:
    """Delete untouched skeleton notes from other worktrees before the vault commit.

    Only notes older than `RESUME_WINDOW_SECONDS` are eligible — newer skeletons
    may belong to a sibling worktree that is still bootstrapping.

    Returns the list of paths actually deleted (so callers can stage them).
    """
    sessions_dir = Path(vault) / "sessions"
    if not sessions_dir.is_dir():
        return []
    now = time.time()
    deleted: list[Path] = []
    for note in iter_note_paths(sessions_dir, recursive=True):
        if note in exclude:
            continue
        try:
            if now - note.stat().st_mtime < RESUME_WINDOW_SECONDS:
                continue
        except Exception:
            continue
        try:
            if is_skeleton_body(note):
                note.unlink()
                deleted.append(note)
        except Exception as e:
            print(
                f"sessions: sweep {note.name}: {type(e).__name__}: {e}",
                file=sys.stderr,
            )
    return deleted
