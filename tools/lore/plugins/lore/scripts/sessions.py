"""Shared session-note lifecycle logic for the lore plugin.

Importable by the `lore` CLI and tests. Every function takes the resolved vault
path explicitly — there is no module-global vault. Reuses the frontmatter parser
from this package's `frontmatter` module.

Responsibilities:
  - ensure_session_note: create-or-resume the per-worktree session note
  - session_note_path / all_session_notes_for_worktree: worktree-scoped finders
  - write_note_atomic: crash-safe file write (temp + os.replace)
  - finalize_note: set status: complete + ended: on a session note — stamping a
    frontmatter note in-place, or writing a ``sessions/<GUID>.json`` metadata
    sidecar for the body-only GUID capture file (the ``.md`` stays body-only;
    Slice 0.5, KU1)

The SessionStart context-injection hook and its render helpers (render_vault_index,
get_vault_stats, render_tool_notes, list_tool_notes, render_action_guards,
build_action_index, and the capture-commands tuple) were removed in Slice 2, S5
(F5: lore is fully pull — orientation lives in agent-rules + S6 skill descriptions;
no SessionStart hook).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import frontmatter
from vault import bucket_dir, iter_note_paths

# Statuses that mean a session note is already finalized — do not re-stamp or
# re-create. `complete` is the only terminal status the CLI writes (Slice 2
# retired shelve/handoff). Per no-backwards-compat, the legacy
# `shelved`/`finalized`/`handoff` values are gone — no migration-window shim.
_TERMINAL_STATUSES = frozenset(("complete",))

# Reuse an existing session note for the same worktree if it was touched within
# this many seconds — covers Claude Code restarts/crashes mid-session.
RESUME_WINDOW_SECONDS = 30 * 60

# The capture skills (lore new …) backlink into these session-note headings, so
# they are load-bearing — keep all five.
REQUIRED_SECTIONS = ("What we did", "Decided", "Deferred", "Learned", "Open questions")


def _filename_stamp(now_iso: str) -> str:
    """Render `YYYY-MM-DDTHH:MM…` → `YYYY-MM-DD-HHMM`."""
    head = now_iso[:16]  # YYYY-MM-DDTHH:MM
    date_part, _, time_part = head.partition("T")
    return f"{date_part}-{time_part.replace(':', '')}"


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


def _session_body() -> str:
    return (
        "## What we did\n"
        "<!-- Append as work happens. -->\n\n"
        "## Decided\n"
        "<!-- Non-obvious decisions. Each is or becomes a decisions/ note. -->\n\n"
        "## Deferred\n"
        "<!-- Links to deferred/ notes created in this session. -->\n\n"
        "## Learned\n"
        "<!-- Gotchas, area corrections, links to dead-ends/ notes. -->\n\n"
        "## Open questions\n"
        "<!-- Unresolved threads. -->\n"
    )


def ensure_session_note(
    vault: Path,
    worktree_name: str,
    branch: str,
    project: str,
    now_iso: str,
    now_human: str,
    session_id: str = "",
) -> tuple[Path, bool]:
    """Create-or-resume a session note for this worktree.

    Filename: `YYYY-MM-DD-HHMM-<worktree>.md`. If a prior note for this
    worktree was modified within `RESUME_WINDOW_SECONDS`, reuse it; otherwise
    create a fresh note. Returns (path, created).
    """
    vault = Path(vault)
    sessions_dir = vault / "sessions"
    try:
        sessions_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # Primary resume signal: a matching session_id. `camp` resumes a worktree
    # via `claude -r <slug>`, which preserves the Claude session_id across
    # restarts, so a note carrying this session_id belongs to the session being
    # resumed — reuse it regardless of how long ago it was last touched. Without
    # this, resuming past RESUME_WINDOW_SECONDS forks a duplicate note. A
    # terminal note (see _TERMINAL_STATUSES) is left alone: an explicit finish
    # means the next start earns a fresh note.
    if session_id:
        for note in all_session_notes_for_worktree(vault, worktree_name):
            try:
                fm = frontmatter.parse_frontmatter(note)
            except Exception:
                continue
            if str(fm.get("session_id", "")).strip() != session_id:
                continue
            if str(fm.get("status", "")).strip() in _TERMINAL_STATUSES:
                break
            return note, False

    # Fallback for a new/absent session_id: resume the newest note for this
    # worktree if it was touched within the mtime window.
    existing = session_note_path(vault, worktree_name)
    if existing is not None:
        try:
            age = time.time() - existing.stat().st_mtime
        except Exception:
            age = float("inf")
        if age < RESUME_WINDOW_SECONDS:
            return existing, False

    month_dir = bucket_dir(sessions_dir, now_iso)
    try:
        month_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    new_path = month_dir / f"{_filename_stamp(now_iso)}-{worktree_name}.md"
    sid_line = f"session_id: {session_id}\n" if session_id else "session_id:\n"
    content = (
        "---\n"
        "type: session\n"
        f"project: {project}\n"
        f"worktree: {worktree_name}\n"
        f"branch: {branch}\n"
        f"started: {now_iso}\n"
        "ended:\n"
        "areas: []\n"
        "phase: Orient\n"
        f"{sid_line}"
        "status: active\n"
        "---\n\n"
        f"# Session: {worktree_name}\n\n"
        f"Started {now_human} on branch `{branch}` in project `{project}`.\n\n"
        + _session_body()
    )
    try:
        new_path.write_text(content)
        return new_path, True
    except Exception:
        return new_path, False


def write_note_atomic(note: Path, text: str) -> bool:
    """Write *text* to *note* atomically via a temp file + os.replace.

    A crash before the replace leaves the original intact and cleans up the
    temp file. Returns True on success, False on failure.
    """
    note = Path(note)
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(note.parent), prefix=f".{note.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp_path, note)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"sessions: write_note_atomic {note.name}: {type(e).__name__}: {e}",
              file=sys.stderr)
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return False


def write_json_atomic(path: Path, obj: dict) -> bool:
    """Serialize *obj* to *path* atomically (temp file + os.replace).

    Mirrors :func:`write_note_atomic` and the record sidecar format
    (``record_store.py``): compact single-line JSON, sorted keys, no trailing
    newline — lockstep with record_store to avoid mixed-format vault writes.
    A crash before the replace leaves the original intact and cleans up the temp
    file. Returns True on success, False on failure.
    """
    # Compact format: mirrors record_store.py — sorted keys, no trailing newline.
    return write_note_atomic(path, json.dumps(obj, sort_keys=True, separators=(",", ":")))


_SESSION_HEADER_RE = re.compile(r"^# session: (\S+)\s*$", re.MULTILINE)


def _finalize_body_only(note: Path, ended_iso: str) -> bool:
    """Finalize a body-only GUID capture file via a JSON sidecar (Slice 0.5, KU1).

    The capture file (``session_store.create_or_append``) is frontmatter-less:
    a ``# session: <id>`` header followed by appended candidate/referenced
    lines. To finalize, write session metadata into a sibling
    ``sessions/<GUID>.json`` sidecar — consistent with vault records
    (``<kind>/<name>.md`` + ``<kind>/<name>.json``) rather than as frontmatter
    on the body. The sidecar carries ``{type: "session", session_id, status,
    ended}`` (``session_id`` lifted from the ``# session: <id>`` header, falling
    back to the file stem); any existing ``created-at``/``created-by``
    provenance already on the sidecar is preserved. Serialized with
    ``json.dumps(indent=2, sort_keys=True)`` to match the record sidecar format,
    written atomically. The ``.md`` is left body-only and untouched (no
    frontmatter is ever prepended).

    Idempotency: the sidecar status is the idempotency source for GUID notes —
    if a sidecar already exists with a terminal status, this is a no-op
    (returns False). Returns False if the body is empty/unreadable.
    """
    text = note.read_text()
    m = _SESSION_HEADER_RE.search(text)
    session_id = m.group(1) if m else note.stem

    sidecar_path = note.with_suffix(".json")
    existing: dict = {}
    if sidecar_path.exists():
        try:
            loaded = json.loads(sidecar_path.read_text())
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}
    if existing.get("status") in _TERMINAL_STATUSES:
        return False  # already finalized — a second finish is a no-op

    obj = dict(existing)
    obj["type"] = "session"
    obj["session_id"] = session_id
    obj["status"] = "complete"
    obj["ended"] = ended_iso
    return write_json_atomic(sidecar_path, obj)


def finalize_note(note: Path, ended_iso: str) -> bool:
    """Set status: complete + ended: on a session note.

    The only terminal status a session note reaches via the CLI is
    ``complete`` (``lore finish``).

    Handles BOTH session-note shapes (Slice 0.5, KU1):

      - **Frontmatter note** (legacy date-prefixed shape): stamp ``status`` +
        ``ended`` in-place in the existing frontmatter block.
      - **Body-only GUID capture file** (``# session: <id>``, no frontmatter —
        what ``session_store.create_or_append`` writes): write session metadata
        into a sibling ``sessions/<GUID>.json`` sidecar (consistent with vault
        records), leaving the ``.md`` body-only and untouched. The sidecar
        status — not frontmatter on the ``.md`` — is the idempotency source: a
        second call on an already-terminal sidecar is a no-op.

    Returns False (no-op) if the note is already terminal or has no body.
    Writes atomically so a mid-write crash leaves the original intact.
    """
    try:
        text = note.read_text()
    except Exception:
        return False
    if not text.startswith("---"):
        return _finalize_body_only(note, ended_iso)
    end = text.find("\n---", 3)
    if end < 0:
        return False
    fm_text = text[3:end]
    body = text[end:]
    fm_lines = fm_text.splitlines()

    for line in fm_lines:
        stripped = line.strip()
        if stripped.startswith("status:"):
            current = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            if current in _TERMINAL_STATUSES:
                return False
            break

    new_fm_lines: list[str] = []
    status_seen = ended_seen = False
    for line in fm_lines:
        stripped = line.strip()
        if stripped.startswith("status:"):
            new_fm_lines.append("status: complete")
            status_seen = True
        elif stripped.startswith("ended:"):
            new_fm_lines.append(f"ended: {ended_iso}")
            ended_seen = True
        else:
            new_fm_lines.append(line)
    if not status_seen:
        new_fm_lines.append("status: complete")
    if not ended_seen:
        new_fm_lines.append(f"ended: {ended_iso}")

    new_text = "---" + "\n".join(new_fm_lines) + body
    return write_note_atomic(note, new_text)


