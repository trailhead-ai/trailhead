"""Shared session-note lifecycle logic for the lore plugin.

Importable by the SessionStart and WorktreeRemove hooks, the `lore` CLI, and
tests. Every function takes the resolved vault path explicitly — there is no
module-global vault. Reuses the frontmatter parser from this package's
`frontmatter` module.

Responsibilities:
  - ensure_session_note: create-or-resume the per-worktree session note
  - session_note_path / all_session_notes_for_worktree: worktree-scoped finders
  - write_note_atomic: crash-safe file write (temp + os.replace)
  - finalize_note: set status: complete + ended: on a session note
  - get_vault_stats: lightweight counts for the SessionStart index
  - render_vault_index: the always-emitted baseline context block
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
from pathlib import Path

import frontmatter
from vault import bucket_dir, iter_note_paths

# Statuses that mean a session note is already finalized — do not re-stamp.
_TERMINAL_STATUSES = frozenset(("complete", "shelved", "finalized", "handoff"))

# Reuse an existing session note for the same worktree if it was touched within
# this many seconds — covers Claude Code restarts/crashes mid-session.
RESUME_WINDOW_SECONDS = 30 * 60

# The capture skills (lore new …) backlink into these session-note headings, so
# they are load-bearing — keep all five.
REQUIRED_SECTIONS = ("What we did", "Decided", "Deferred", "Learned", "Open questions")

# Slash commands surfaced in the baseline index so the model is reminded the
# capture primitives exist.
LORE_COMMANDS = (
    "`/lore:defer`",
    "`/lore:dead-end`",
    "`/lore:decision`",
    "`/lore:radar`",
    "`/lore:area`",
)


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
    for p in sorted(iter_note_paths(sessions_dir, recursive=True), key=lambda p: p.name, reverse=True):
        if _is_note_for_worktree(p, worktree_name):
            return p
    return None


def all_session_notes_for_worktree(vault: Path, worktree_name: str) -> list[Path]:
    """Return every session note matching this worktree, newest first."""
    sessions_dir = Path(vault) / "sessions"
    if not sessions_dir.is_dir():
        return []
    return sorted(
        (p for p in iter_note_paths(sessions_dir, recursive=True) if _is_note_for_worktree(p, worktree_name)),
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
    # terminal note (finished/shelved) is left alone: an explicit finish means
    # the next start earns a fresh note.
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


def finalize_note(note: Path, ended_iso: str, status: str = "complete") -> bool:
    """Set status: <status> + ended: on a session note.

    Parameterized: pass ``status="shelved"`` to shelve a note rather than
    complete it. Default ``"complete"`` preserves all existing callers.

    Returns False (no-op) if the note is already terminal or has no
    frontmatter. Writes atomically so a mid-write crash leaves the original
    intact.
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
            new_fm_lines.append(f"status: {status}")
            status_seen = True
        elif stripped.startswith("ended:"):
            new_fm_lines.append(f"ended: {ended_iso}")
            ended_seen = True
        else:
            new_fm_lines.append(line)
    if not status_seen:
        new_fm_lines.append(f"status: {status}")
    if not ended_seen:
        new_fm_lines.append(f"ended: {ended_iso}")

    new_text = "---" + "\n".join(new_fm_lines) + body
    return write_note_atomic(note, new_text)


# Statuses from which a note can be resumed (flipped back to active).
_RESUMABLE_STATUSES = frozenset(("shelved", "handoff"))


def resume_note(note: Path) -> bool:
    """Flip a shelved or handoff session note back to active.

    Returns False (no-op) when the note is not in a resumable status
    (active, complete, finalized) or has no frontmatter.
    Writes atomically.
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
    fm_text = text[3:end]
    body = text[end:]
    fm_lines = fm_text.splitlines()

    current_status: str | None = None
    for line in fm_lines:
        stripped = line.strip()
        if stripped.startswith("status:"):
            current_status = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            break

    if current_status not in _RESUMABLE_STATUSES:
        return False

    new_fm_lines: list[str] = []
    for line in fm_lines:
        stripped = line.strip()
        if stripped.startswith("status:"):
            new_fm_lines.append("status: active")
        else:
            new_fm_lines.append(line)

    new_text = "---" + "\n".join(new_fm_lines) + body
    return write_note_atomic(note, new_text)


def _ts_sort_key(note: Path) -> str:
    """Return a sortable timestamp string for a session note.

    Prefers the ``ended:`` frontmatter field; falls back to ``started:``.
    Returns an empty string when neither is set so missing-timestamp notes
    sort last (empty string < any ISO timestamp).
    """
    try:
        fm = frontmatter.parse_frontmatter(note)
    except Exception:
        return ""
    ended = fm.get("ended") or ""
    if isinstance(ended, str) and ended.strip():
        return ended.strip()
    started = fm.get("started") or ""
    if isinstance(started, str) and started.strip():
        return started.strip()
    return ""


def find_shelved_notes(vault: Path, slug: str | None = None) -> list[Path]:
    """Return session notes whose status is in {shelved, handoff}.

    Sorted most-recent-first by frontmatter timestamp (``ended:`` falling
    back to ``started:``); notes missing both timestamps sort last.

    When *slug* is given, only notes whose filename encodes exactly that
    worktree slug are returned (same stem-parse logic as
    ``all_session_notes_for_worktree``).

    Never raises — returns [] when the sessions directory is missing or
    unreadable.
    """
    sessions_dir = Path(vault) / "sessions"
    if not sessions_dir.is_dir():
        return []

    _shelved = frozenset(("shelved", "handoff"))
    results: list[Path] = []

    for p in iter_note_paths(sessions_dir, recursive=True):
        if slug is not None and _worktree_from_stem(p.stem) != slug:
            continue
        try:
            fm = frontmatter.parse_frontmatter(p)
        except Exception:
            continue
        if fm.get("status") in _shelved:
            results.append(p)

    # Secondary key (filename stem, which embeds YYYY-MM-DD-HHMM) makes the
    # order deterministic across machines when frontmatter timestamps tie or are
    # absent — otherwise ties fall back to filesystem glob() order.
    return sorted(results, key=lambda p: (_ts_sort_key(p), p.stem), reverse=True)


def build_action_index(vault: Path) -> dict[str, dict[str, int]]:
    """Walk collaboration/ and dead-ends/, aggregate by action name.

    Returns {action_name: {"collaboration": N, "dead_ends": M}}.
    Notes with status graduated or obsolete are excluded.
    """
    vault = Path(vault)
    index: dict[str, dict[str, int]] = {}

    def _scan(directory: Path, bucket: str, recursive: bool = False) -> None:
        if not directory.is_dir():
            return
        for p in iter_note_paths(directory, recursive=recursive):
            fm = frontmatter.parse_frontmatter(p)
            if fm.get("status") in ("graduated", "obsolete"):
                continue
            actions = fm.get("actions")
            if isinstance(actions, str):
                actions = [actions]
            if not isinstance(actions, list):
                continue
            for a in actions:
                if not isinstance(a, str) or not a:
                    continue
                entry = index.setdefault(a, {"collaboration": 0, "dead_ends": 0})
                entry[bucket] += 1

    # collaboration stays flat (out of scope); dead-ends is a living folder.
    _scan(vault / "collaboration", "collaboration")
    _scan(vault / "dead-ends", "dead_ends", recursive=True)
    return index


def render_action_guards(vault: Path) -> str | None:
    """Short summary of action guards for always-on SessionStart injection.

    Returns None when there are no active guards.
    """
    index = build_action_index(vault)
    if not index:
        return None
    lines = [
        "### Action guards — check before acting",
        "",
        "Active observations guard these actions. When you think \"I need to do X\", "
        "check if X matches one of these, and if so, read the matching notes "
        "before taking the action.",
        "",
    ]
    for action in sorted(index.keys()):
        counts = index[action]
        parts = []
        if counts.get("collaboration", 0) > 0:
            parts.append(f"{counts['collaboration']} collaboration")
        if counts.get("dead_ends", 0) > 0:
            parts.append(f"{counts['dead_ends']} dead-end")
        lines.append(f"- **`{action}`** — {', '.join(parts)}")
    lines.append("")
    return "\n".join(lines)


def list_tool_notes(vault: Path) -> list[tuple[str, str]]:
    """Enumerate tools/*.md as (name, summary) tuples.

    Name is frontmatter `name:` or falls back to filename stem.
    Summary is frontmatter `summary:` or an empty string.
    """
    tools_dir = Path(vault) / "tools"
    if not tools_dir.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for p in sorted(tools_dir.glob("*.md")):
        if p.name.lower() == "readme.md":
            continue
        fm = frontmatter.parse_frontmatter(p)
        name = fm.get("name") or p.stem
        summary = fm.get("summary") or ""
        if isinstance(name, str) and isinstance(summary, str):
            out.append((name, summary))
    return out


def render_tool_notes(vault: Path) -> str | None:
    """Short index of available tool notes for always-on SessionStart injection.

    Returns None when no tool notes exist.
    """
    tools = list_tool_notes(vault)
    if not tools:
        return None
    lines = [
        "### Tool notes — read before first invocation",
        "",
        "Running mental models for specific tools. When about to use one "
        "of these for the first time in this session, read the matching file.",
        "",
    ]
    for name, summary in tools:
        if summary:
            lines.append(f"- **`{name}`** — {summary}")
        else:
            lines.append(f"- **`{name}`**")
    lines.append("")
    return "\n".join(lines)


def _count(directory: Path, predicate, recursive: bool = False) -> int:
    if not directory.is_dir():
        return 0
    return sum(
        1
        for p in iter_note_paths(directory, recursive=recursive)
        if predicate(frontmatter.parse_frontmatter(p))
    )


def get_vault_stats(vault: Path) -> dict:
    """Lightweight counts for the SessionStart baseline index. Never raises."""
    vault = Path(vault)
    stats = {
        "areas": 0,
        "open_deferred": 0,
        "dead_ends": 0,
        "active_lessons": 0,
        "sessions": 0,
    }
    if not vault.exists():
        return stats
    stats["areas"] = _count(vault / "areas", lambda fm: True)
    stats["open_deferred"] = _count(
        vault / "deferred",
        lambda fm: fm.get("type") == "deferred"
        and fm.get("status") in ("open", "scheduled", "resurfaced"),
        recursive=True,
    )
    stats["dead_ends"] = _count(
        vault / "dead-ends", lambda fm: fm.get("type") == "dead-end",
        recursive=True,
    )
    stats["active_lessons"] = _count(
        vault / "lessons",
        lambda fm: fm.get("type") == "lesson" and fm.get("status", "active") == "active",
        recursive=True,
    )
    stats["sessions"] = _count(
        vault / "sessions", lambda fm: fm.get("type") == "session", recursive=True
    )
    return stats


def render_vault_index(
    vault: Path,
    worktree_name: str,
    project: str,
    session_note: Path | None,
    session_created: bool,
    warning: str | None = None,
    session_note_display: str | None = None,
) -> str:
    """Build the always-emitted baseline context block.

    ``session_note_display``: when provided, used verbatim as the session note
    pointer in the banner instead of the vault-relative path. The hook supplies
    a pre-rendered clickable URL here when the link server is active.
    """
    vault = Path(vault)
    stats = get_vault_stats(vault)
    lines: list[str] = [f"## Lore vault — {worktree_name} ({project})", ""]

    if warning:
        lines.append(f"**Warning:** {warning}")
        lines.append("")

    if session_note is not None:
        if session_note_display is not None:
            display = session_note_display
        else:
            try:
                display = str(session_note.relative_to(vault))
            except ValueError:
                display = session_note.name
        verb = "created" if session_created else "resumed"
        lines.append(
            f"**Session note:** `{display}` ({verb} for this worktree). "
            "Append progress as work happens."
        )
        lines.append("")

    lines.append(
        f"**Vault state:** {stats['areas']} area profiles · "
        f"{stats['open_deferred']} open deferred · "
        f"{stats['dead_ends']} dead-ends · "
        f"{stats['active_lessons']} active lessons · "
        f"{stats['sessions']} session notes"
    )
    lines.append("")
    lines.append("**Capture commands:** " + ", ".join(LORE_COMMANDS) + ".")
    lines.append("")

    tool_block = render_tool_notes(vault)
    if tool_block:
        lines.append(tool_block)

    guard_block = render_action_guards(vault)
    if guard_block:
        lines.append(guard_block)

    return "\n".join(lines)
