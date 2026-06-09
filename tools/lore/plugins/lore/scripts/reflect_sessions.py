"""Session-selection helper for the reflect ritual.

Selects session notes from a vault's ``sessions/`` directory that fall within
a date window and carry a finalized status. This is the deterministic,
unit-testable predicate the reflect skill uses to gather its inputs.

Finalized statuses:
  - ``complete``   — canonical lore status (set by ``lore finish``)
  - ``finalized``  — accepted for back-compat (brain's legacy status; P1-B)

Sessions with status ``active`` are always excluded — they represent work still
in progress and will be picked up by a future reflection.

The ``ended`` frontmatter field is used as the date anchor. A session whose
``ended`` date falls within ``[window_start, window_end]`` (inclusive, ISO date
prefix comparison) is included. Sessions with no ``ended`` value are excluded.
"""
from __future__ import annotations

from pathlib import Path

import frontmatter
from vault import iter_note_paths

_FINALIZED_STATUSES = frozenset(("complete", "finalized"))


def sessions_in_window(
    vault: Path,
    _period: str,
    window_start: str,
    window_end: str,
) -> list[Path]:
    """Return session notes whose ``ended`` date falls within the window.

    Args:
        vault:        Root of the lore vault.
        _period:      Human label for the period (e.g. ``"2026-05"``). Not used
                      for filtering — kept for callers that want to pass context.
        window_start: ISO date string for the start of the window (inclusive),
                      e.g. ``"2026-05-01"``.
        window_end:   ISO date string for the end of the window (inclusive),
                      e.g. ``"2026-05-31"``.

    Returns:
        Sorted list of matching session note paths (newest last).
    """
    sessions_dir = Path(vault) / "sessions"
    if not sessions_dir.is_dir():
        return []

    selected: list[Path] = []
    for note in iter_note_paths(sessions_dir, recursive=True):
        fm = frontmatter.parse_frontmatter(note)
        status = fm.get("status", "")
        if status not in _FINALIZED_STATUSES:
            continue
        ended = fm.get("ended", "")
        if not ended:
            continue
        # Compare by ISO date prefix (first 10 chars: YYYY-MM-DD)
        ended_date = str(ended)[:10]
        if window_start <= ended_date <= window_end:
            selected.append(note)

    return sorted(selected)
