"""Shared config resolver for the lore plugin.

Every hook and CLI call resolves the vault location and the acting user
through these two pure functions. Vault location comes from $LORE_VAULT
(default ~/lore); the acting user from $LORE_USER, then git config, then
a generic fallback. Neither function raises.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Iterator

# Strip control characters (incl. newlines/tabs) from user-provided strings.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def bucket_dir(folder: Path, date_or_ts: str) -> Path:
    """Return the ``YYYY-MM`` month-bucket subdir of *folder* for a note.

    *date_or_ts* is any string starting with ``YYYY-MM`` (an ISO timestamp like
    ``2026-06-15T09:30:00Z`` or a bare ``2026-06-15`` date); the first seven
    characters (``YYYY-MM``) name the bucket. Callers ``mkdir(parents=True,
    exist_ok=True)`` the result before writing — creation is idempotent and
    race-safe.
    """
    return Path(folder) / date_or_ts[:7]


def iter_note_paths(directory: Path, recursive: bool = False) -> Iterator[Path]:
    """Yield ``.md`` notes in *directory*.

    Flat by default (top-level ``*.md`` only). When *recursive* is True, also
    descends exactly one level into ``YYYY-MM/`` month buckets (``*/*.md``) —
    the date-bucketed archive layout. Files and directories whose name starts
    with ``_`` (e.g. ``_index.md``, ``_test/``, ``_archive/``) are always
    skipped. Recursion is deliberately bounded to one level (not ``rglob``) so
    deeper or special dirs are never descended.

    The date-bucketed folders (sessions/plans/specs/designs and the living
    folders deferred/dead-ends/lessons/decisions/radar) pass ``recursive=True``;
    the name-keyed folders (areas/tools/etc.) stay flat.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return
    for md in directory.glob("*.md"):
        if not md.name.startswith("_"):
            yield md
    if recursive:
        for sub in directory.iterdir():
            if not sub.is_dir() or sub.name.startswith("_"):
                continue
            for md in sub.glob("*.md"):
                if not md.name.startswith("_"):
                    yield md


def resolve_vault() -> str:
    """Return the resolved vault path.

    Priority: $LORE_VAULT (expanded + resolved), else ~/lore resolved.
    Never raises.
    """
    raw = os.environ.get("LORE_VAULT", "").strip()
    target = raw if raw else "~/lore"
    try:
        return str(Path(target).expanduser().resolve())
    except Exception:
        return str(Path(target).expanduser())


def _sanitize(value: str) -> str:
    """Strip control chars (newlines/tabs included) and surrounding whitespace."""
    return _CONTROL_RE.sub("", value).strip()


def resolve_user() -> str:
    """Return the acting user's name.

    Fixed fallback order: $LORE_USER → `git config user.name` → "you".
    Sanitized (control chars stripped, whitespace trimmed). Never raises.
    """
    raw = os.environ.get("LORE_USER", "")
    name = _sanitize(raw)
    if name:
        return name

    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            name = _sanitize(result.stdout or "")
            if name:
                return name
    except Exception:
        pass

    return "you"


def resolve_project(cwd: Path | None = None) -> str:
    """Infer the current project name from the git remote URL.

    `https://github.com/acme/my-repo.git` → `my-repo`
    `git@github.com:acme/project.git` → `project`

    Falls back to the CWD's directory name. Never raises.
    """
    target = cwd or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "-C", str(target), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        url = (result.stdout or "").strip()
        if url and result.returncode == 0:
            m = re.search(r"[/:]([^/:]+?)(?:\.git)?/?$", url)
            if m:
                return m.group(1)
    except Exception:
        pass

    return target.name


_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

# Matches the timestamp prefix and captures everything after it as the worktree.
_STEM_WORKTREE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}-(.+)$")


def _worktree_from_stem(stem: str) -> str | None:
    """Return the worktree name encoded in a session note stem, or None."""
    m = _STEM_WORKTREE_RE.match(stem)
    return m.group(1) if m else None


def find_session_note(vault: Path, worktree_name: str | None = None) -> Path | None:
    """Return the newest matching session note in vault/sessions/, or None.

    Candidates must have a date-prefixed filename (``YYYY-MM-DD-HHMM-<worktree>.md``).
    When *worktree_name* is given, only notes whose stem encodes exactly that
    worktree are considered — this prevents a note from worktree ``beta``
    being returned when the caller belongs to worktree ``alpha``.  The match
    is done by parsing the worktree out of the stem (anchored on the timestamp
    boundary) so ``foo`` cannot accidentally match ``super-foo``.
    When *worktree_name* is None, returns the newest dated note overall.
    Returns None when no matching note exists or the sessions dir is missing.
    """
    sessions_dir = Path(vault) / "sessions"
    if not sessions_dir.is_dir():
        return None

    def _is_candidate(p: Path) -> bool:
        if not _DATE_PREFIX_RE.match(p.name):
            return False
        if worktree_name is None:
            return True
        return _worktree_from_stem(p.stem) == worktree_name

    notes = sorted(
        (p for p in iter_note_paths(sessions_dir, recursive=True) if _is_candidate(p)),
        key=lambda p: p.name,
        reverse=True,
    )
    return notes[0] if notes else None


def find_session_note_by_session_id(vault: Path, session_id: str) -> Path | None:
    """Return the session note whose frontmatter carries ``session_id: <id>``.

    This is the exact, cwd-independent resolver: the id is written into the
    note's frontmatter at creation time, so a match is unambiguous regardless
    of where the caller's cwd happens to be (sibling repo, subdir, canonical
    checkout). Only the frontmatter block is inspected — a ``session_id:``
    string appearing in the body never counts.

    Returns None when the id is empty, the sessions dir is missing, or no note
    matches. Never raises.
    """
    if not session_id:
        return None
    sessions_dir = Path(vault) / "sessions"
    if not sessions_dir.is_dir():
        return None

    needle = f"session_id: {session_id}"
    matches: list[Path] = []
    for p in iter_note_paths(sessions_dir, recursive=True):
        try:
            text = p.read_text()
        except Exception:
            continue
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)  # close of the frontmatter block
        front = text[:end] if end >= 0 else text
        if any(line.strip() == needle for line in front.splitlines()):
            matches.append(p)
    if not matches:
        return None
    # Pathological: two notes share an id → prefer the newest stem.
    return sorted(matches, key=lambda p: p.name, reverse=True)[0]


# A `.claude/worktrees/<name>/` path segment marks a Claude Code worktree.
_WORKTREES_RE = re.compile(r"/\.claude/worktrees/([^/]+)")


def detect_worktree_name(cwd: Path | None = None) -> str:
    """Best-effort worktree name for the current session.

    Mirrors how the session-note filename is *created* (the SessionStart hook
    names it from ``$CLAUDE_PROJECT_DIR`` basename), so resolution matches
    creation. Order:

      1. ``$CLAUDE_PROJECT_DIR`` basename — the tightest match.
      2. A ``.claude/worktrees/<name>/`` segment anywhere in the path → ``<name>``
         (handles sibling-repo or subdir cwds inside a worktree).
      3. git toplevel basename (a subdir of a plain checkout).
      4. cwd basename.

    Never raises.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "").strip()
    if project_dir:
        m = _WORKTREES_RE.search(project_dir)
        return m.group(1) if m else Path(project_dir).name

    target = Path(cwd) if cwd is not None else Path.cwd()
    target_str = str(target)

    m = _WORKTREES_RE.search(target_str)
    if m:
        return m.group(1)

    try:
        result = subprocess.run(
            ["git", "-C", target_str, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        top = (result.stdout or "").strip()
        if top and result.returncode == 0:
            m = _WORKTREES_RE.search(top)
            return m.group(1) if m else Path(top).name
    except Exception:
        pass

    return target.name


def resolve_session_note(
    vault: Path,
    session_id: str | None = None,
    worktree_name: str | None = None,
    cwd: Path | None = None,
) -> Path | None:
    """Resolve the session note for the current session.

    Order: exact session-id frontmatter match (cwd-independent) → worktree-name
    fallback (newest by filename timestamp). ``worktree_name`` defaults to
    :func:`detect_worktree_name` when not supplied. Returns None when nothing
    resolves.
    """
    if session_id:
        hit = find_session_note_by_session_id(vault, session_id)
        if hit is not None:
            return hit
    if worktree_name is None:
        worktree_name = detect_worktree_name(cwd)
    return find_session_note(vault, worktree_name=worktree_name or None)
