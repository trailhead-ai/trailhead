"""camp bookmarks — durable named pointers from a camp workspace to a session.

``store`` owns the global ref-keyed JSON store and its CRUD surface; ``capture``
owns the ``camp bookmark`` command that records the CURRENT session; ``render``
owns ``camp bookmark ls``/``rm``; ``resume`` owns ``camp resume``, which prints
what the shell integration needs to re-enter a bookmarked session.

:func:`harness_for` lives here rather than in any one of them because all three
need the same first step — turning a group's configured harness binary into the
trailhead harness object whose seam answers transcript, resume, and retention
questions.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only; trailhead may not be installed
    from trailhead.harness.base import Harness


def harness_for(group: dict) -> Harness | None:
    """Return the trailhead harness backing *group*, or None if unrecognized.

    ``None`` means camp cannot name a harness for this group at all. Callers
    degrade on it exactly as they degrade on a harness that answers ``None`` from
    the seam itself — a user cannot act on the difference, and neither outcome
    yields the transcript, argv, or retention window that was asked for.

    Both imports are deferred: camp ships as a standalone CLI, so a caller that
    runs without trailhead installed must fail inside a caller's own guard rather
    than at module import.
    """
    from trailhead.harness import HarnessError, get_harness

    from ..launch.profile import resolve_harness_profile

    binary = Path(resolve_harness_profile(group).binary).name
    try:
        return get_harness(binary)
    except HarnessError:
        return None
