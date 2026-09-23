""""Last touched" resolution for `camp list`/`ls` — the most recent of a
workspace's tmux session activity, its members' worktree git activity, and
its manifest's own mtime.

Harness-agnostic and git-subprocess-free by design: a member worktree's
activity is read straight off its per-worktree gitdir's `index` and
`logs/HEAD` mtimes (the files git itself touches on every commit, checkout,
and ref update), resolved by reading the worktree's `.git` entry directly —
never a `git` subprocess call, and never a harness's own transcript
directory, which would make this feature depend on which harness happened to
run the session.

A missing file anywhere in the chain is skipped silently rather than
degrading the whole answer; `None` propagates only when NOTHING is
observable, which is the one state `camp list` renders `-` for.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

#: The prefix a linked worktree's `.git` FILE carries before its target
#: gitdir path (confirmed against git's own worktree implementation).
_GITDIR_PREFIX = "gitdir:"

#: The strftime/strptime format `to_iso_utc`/`from_iso_utc` round-trip
#: through — whole seconds only, which is all a "5m ago" style relative
#: rendering can ever distinguish.
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def resolve_worktree_gitdir(worktree_path: Path) -> Path | None:
    """Resolve *worktree_path*'s per-worktree gitdir.

    A linked worktree's `.git` is a FILE containing `gitdir: <path>` (the
    path may be relative to *worktree_path*); an ordinary repo's `.git` is
    the gitdir itself, as a DIRECTORY. Returns `None` when `.git` is
    neither, is unreadable, or the file's content does not carry the
    expected prefix — never raises, since a workspace with an
    unexpected `.git` shape is exactly the "nothing observable" case.
    """
    git_entry = worktree_path / ".git"
    if git_entry.is_dir():
        return git_entry
    if not git_entry.is_file():
        return None
    try:
        content = git_entry.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not content.startswith(_GITDIR_PREFIX):
        return None
    raw = content[len(_GITDIR_PREFIX):].strip()
    if not raw:
        return None
    gitdir = Path(raw)
    if not gitdir.is_absolute():
        gitdir = worktree_path / gitdir
    return Path(os.path.normpath(gitdir))


def _default_stat_mtime(path: Path) -> float:
    return path.stat().st_mtime


def worktree_activity_mtime(
    worktree_path: Path,
    *,
    resolve_gitdir: Callable[[Path], Path | None] = resolve_worktree_gitdir,
    stat_mtime: Callable[[Path], float] = _default_stat_mtime,
) -> float | None:
    """The most recent mtime of *worktree_path*'s gitdir `index` and
    `logs/HEAD` — the two files git touches on every commit, checkout, and
    ref update. Either missing is skipped silently; both missing, or the
    gitdir itself unresolvable, is `None`.

    *resolve_gitdir* / *stat_mtime* are the injectable seams a test drives
    without a real worktree on disk.
    """
    gitdir = resolve_gitdir(worktree_path)
    if gitdir is None:
        return None
    mtimes: list[float] = []
    for candidate in (gitdir / "index", gitdir / "logs" / "HEAD"):
        try:
            mtimes.append(stat_mtime(candidate))
        except OSError:
            continue
    return max(mtimes) if mtimes else None


def workspace_last_touched(
    *,
    tmux_activity: float | None,
    member_worktree_paths: Sequence[Path],
    manifest_path: Path,
) -> float | None:
    """A workspace's "last touched" instant: the most recent of *tmux_activity*
    (already read by the caller from tmux's session listing), every member
    worktree's :func:`worktree_activity_mtime`, and *manifest_path*'s own
    mtime. `None` only when none of the three are observable.
    """
    candidates: list[float] = []
    if tmux_activity is not None:
        candidates.append(tmux_activity)
    for member_path in member_worktree_paths:
        activity = worktree_activity_mtime(member_path)
        if activity is not None:
            candidates.append(activity)
    try:
        candidates.append(manifest_path.stat().st_mtime)
    except OSError:
        pass
    return max(candidates) if candidates else None


def to_iso_utc(ts: float | None) -> str | None:
    """*ts* (a Unix epoch) as an ISO-8601 UTC string, or `None` for `None` —
    the `--json` wire shape for `last_touched`."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(_ISO_FORMAT)


def from_iso_utc(value: str | None) -> float | None:
    """The inverse of :func:`to_iso_utc`. `None`, or a string that does not
    parse (an older or malformed relayed row), returns `None` rather than
    raising — the caller's fallback is already "unknown"."""
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, _ISO_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.timestamp()
