"""``camp bookmark ls`` / ``camp bookmark rm`` — list and remove global bookmarks.

``ls`` is GLOBAL: it lists every bookmark in the store, most-recently-updated
first, regardless of which group's workspace the invoking shell happens to be
in. Its four columns are ref / group-workspace / age / note; a bookmark whose
transcript or workspace has since disappeared gets an inline marker rather than
being silently dropped — camp core only ever CHECKS existence, never derives or
repairs a path (the seam the transcript/workspace paths came from already
resolved them once, at capture time). A still-healthy bookmark whose transcript is
nearing the harness's retention cleanup gets a proactive ``expires ~Nd`` marker
instead — the point at which a user can still do something about it.

``rm`` removes exactly the named ref; a ref that does not exist is refused,
naming the ref, rather than silently succeeding.
"""

from __future__ import annotations

import datetime as dt
import math
import sys
import time
from pathlib import Path
from typing import Any

from . import store

#: Fraction of the harness's retention window after which a session counts as
#: approaching expiry. Mirrors ``SESSION_RETENTION_WARNING_FRACTION`` in
#: trailhead's harness seam — ``trailhead doctor`` warns off the same cutoff, and
#: a user reading both surfaces must see one deadline. It is restated here rather
#: than imported so camp keeps listing bookmarks when it runs standalone, without
#: trailhead importable; a test pins the two values equal.
_RETENTION_WARNING_FRACTION = 0.8

_EMPTY_HINT = (
    "camp bookmark: no bookmarks yet — run 'camp bookmark' from inside a "
    "workspace to capture one"
)

_COLUMNS = ("REF", "WORKSPACE", "AGE", "NOTE")

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def format_age(updated_at: str, *, now: dt.datetime) -> str:
    """Return a short human age (e.g. ``3h``, ``2d``) for *updated_at*.

    An unparsable timestamp renders as ``?`` rather than raising — a display
    concern must never crash the whole listing over one malformed record.
    """
    try:
        then = dt.datetime.strptime(updated_at, _TIMESTAMP_FORMAT).replace(
            tzinfo=dt.timezone.utc
        )
    except (ValueError, TypeError):
        return "?"

    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"


def _expiry_marker(transcript: Path, retention_days: int | None) -> str | None:
    """Return ``"expires ~Nd"`` once a transcript is near the harness's cleanup.

    The harness deletes transcripts older than its retention window, which would
    leave the bookmark pointing at nothing. Warning at a fraction of that window
    (the same cutoff ``trailhead doctor`` uses) is what makes the marker
    PROACTIVE — a user still has time to resume or re-capture the session.

    ``retention_days`` of None means the harness reported no window; camp then
    says nothing rather than inventing a deadline. Age comes from the file's
    mtime, the same clock the harness's own cleanup runs on.
    """
    if not retention_days or retention_days <= 0:
        return None
    try:
        age_days = (time.time() - transcript.stat().st_mtime) / 86400
    except OSError:
        return None
    if age_days < retention_days * _RETENTION_WARNING_FRACTION:
        return None
    # Round the remainder UP: a partial day left is still a day a user can act
    # in, and ``~0d`` is reserved for a transcript already past the window.
    return f"expires ~{max(0, math.ceil(retention_days - age_days))}d"


def _status_marker(
    record: dict[str, Any],
    *,
    env: dict[str, str] | None,
    retention_days: int | None = None,
) -> str | None:
    """Return the single most actionable marker for a row, or None when healthy.

    Ordered most-broken first — workspace gone, transcript gone, then approaching
    expiry — because a row carries ONE marker: something already lost outranks
    something about to be lost, and reporting both would bury the former.
    """
    from ..group.manifest import workspace_dir

    workspace = workspace_dir(record["group"], record["slug"], env=env)
    if not workspace.exists():
        return "workspace gone"
    transcript = Path(record["transcript_path"])
    if not transcript.exists():
        return "transcript gone"
    return _expiry_marker(transcript, retention_days)


def resolve_retention_days(group: dict) -> int | None:
    """Ask the group's harness how long it keeps session transcripts, or None.

    Every failure — no trailhead install, an unrecognized harness, a harness with
    no retention concept — collapses to None, because the answer only drives an
    advisory marker: a listing must never fail over one.
    """
    try:
        from trailhead.harness import get_harness

        from ..launch.profile import resolve_harness_profile

        binary = Path(resolve_harness_profile(group).binary).name
        return get_harness(binary).session_retention_days()
    except Exception:
        return None


def render_bookmarks(
    bookmarks: list[dict[str, Any]],
    *,
    env: dict[str, str] | None = None,
    now: dt.datetime | None = None,
    retention_days: int | None = None,
) -> str:
    """Render *bookmarks* (already ordered by the caller) as a column table.

    Zero bookmarks renders the empty-state hint, never a header-only table — an
    agent scanning for "any bookmarks?" should not have to distinguish an empty
    table from a rendering bug.
    """
    if not bookmarks:
        return _EMPTY_HINT

    now = now or dt.datetime.now(dt.timezone.utc)
    rows: list[tuple[str, str, str, str]] = []
    for record in bookmarks:
        workspace_label = f"{record['group']}/{record['slug']}"
        age = format_age(record.get("updated_at", ""), now=now)
        note = record.get("note") or ""
        marker = _status_marker(record, env=env, retention_days=retention_days)
        if marker:
            note = f"{note}  [{marker}]" if note else f"[{marker}]"
        rows.append((record["ref"], workspace_label, age, note))

    widths = [len(header) for header in _COLUMNS]
    for row in rows:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row)]

    lines = ["  ".join(h.ljust(w) for h, w in zip(_COLUMNS, widths))]
    for row in rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
    return "\n".join(lines)


def cmd_bookmark_ls(args: list[str], group: dict, env: dict[str, str] | None) -> None:
    """``camp bookmark ls`` — print every bookmark, most-recently-updated first."""
    if args:
        print(f"camp bookmark ls: unexpected argument {args[0]!r}", file=sys.stderr)
        sys.exit(1)
    bookmarks = store.list_bookmarks_by_recency(env=env)
    print(
        render_bookmarks(
            bookmarks, env=env, retention_days=resolve_retention_days(group)
        )
    )


def cmd_bookmark_rm(args: list[str], group: dict, env: dict[str, str] | None) -> None:
    """``camp bookmark rm <ref>`` — remove exactly the named bookmark."""
    if not args:
        print("camp bookmark rm: usage: camp bookmark rm <ref>", file=sys.stderr)
        sys.exit(1)
    ref, rest = args[0], args[1:]
    if rest:
        print(f"camp bookmark rm: unexpected argument {rest[0]!r}", file=sys.stderr)
        sys.exit(1)

    removed = store.delete_by_ref(ref, env=env)
    if not removed:
        print(f"camp bookmark rm: no bookmark named {ref!r}", file=sys.stderr)
        sys.exit(1)
    print(f"camp: removed bookmark {ref!r}")
