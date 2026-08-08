"""camp bookmarks — durable named pointers from a camp workspace to a session.

``store`` owns the global ref-keyed JSON store and its CRUD surface; ``capture``
owns the ``camp bookmark`` command that records the CURRENT session; ``render``
owns ``camp bookmark ls``/``rm``; ``resume`` owns ``camp resume``, which prints
what the shell integration needs to re-enter a bookmarked session.

:func:`harness_for` lives here rather than in any one of them because all three
need the same first step — turning a group's configured harness binary into the
trailhead harness object whose seam answers transcript, resume, and retention
questions.

Which group that is depends on the command. Capture bookmarks the workspace the
shell is standing in, so it uses the group resolved from cwd. ``ls``, ``rm``, and
``resume`` name a ref instead, and a ref is exactly the thing you look up WITHOUT
knowing its group — so they read the group off the STORED record
(:func:`harness_for_bookmark`) and work from any cwd, including a plain shell
outside every group directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

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


def group_config_for(name: str, *, env: dict[str, str] | None = None) -> dict | None:
    """Return the configured camp group named *name*, or None when unresolvable.

    Reads the group configs by NAME rather than from cwd, which is what lets a
    ref-addressed command answer for a group the invoking shell is nowhere near.
    Every failure — no config dir, a malformed file, no such group, camp running
    without trailhead importable — collapses to None: the caller is asking a
    question whose "I don't know" answer already has a defined degradation.
    """
    try:
        import trailhead.paths as _paths

        from ..group.config import load_all_groups

        kwargs: dict[str, Any] = {"env": env} if env is not None else {}
        groups_dir = _paths.config_dir("camp", **kwargs) / "groups"
        for config in load_all_groups(groups_dir):
            if config.get("group", {}).get("name") == name:
                return config
    except Exception:
        return None
    return None


def harness_for_bookmark(
    record: dict[str, Any], *, env: dict[str, str] | None = None
) -> Harness | None:
    """Return the harness of the group the BOOKMARK was captured in.

    A bookmark's transcript, resume argv, and retention window all belong to the
    harness that ran the session — which is the harness of the group recorded on
    the record, not of whichever group the invoking shell happens to sit in. Two
    groups may run different harnesses, so asking the wrong one yields the wrong
    answer while looking entirely successful.

    An unconfigured group falls back to the baked-in default profile, the same
    answer :func:`harness_for` gives a group with no ``[harness]`` block.
    """
    return harness_for(group_config_for(record.get("group") or "", env=env) or {})


def groupless_subverb(
    rest: list[str],
) -> tuple[Callable[[list[str], dict[str, str] | None], None], list[str]] | None:
    """Return (handler, args) for a ``camp bookmark`` subverb that needs no group.

    ``ls`` and ``rm`` address the GLOBAL store and name their own targets, so they
    must run from any cwd; bare ``camp bookmark`` captures the current workspace
    and stays cwd-scoped. Both dispatchers consult this ONE classifier so the two
    entry points cannot disagree about which subverbs need a group.

    ``--group`` is dropped from a COPY of *rest* first, so the subverb is
    classified off the first remaining token regardless of whether ``--group``
    appeared before or after it — ``camp bookmark --group g ls`` and
    ``camp bookmark ls --group g`` both classify as ``ls``. The caller must not
    re-consume ``--group`` itself: this is the only place it is dropped.
    """
    if not rest:
        return None

    from ..spine import _consume_flag_value
    from .render import cmd_bookmark_ls, cmd_bookmark_rm

    args = list(rest)
    _consume_flag_value(args, "--group")  # resolved (or unresolvable) upstream; drop it
    if not args:
        return None
    handler = {"ls": cmd_bookmark_ls, "rm": cmd_bookmark_rm}.get(args[0])
    if handler is None:
        return None
    return handler, args[1:]
