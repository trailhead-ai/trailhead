"""camp bookmarks — durable named pointers from a camp workspace to a session.

``store`` owns the global ref-keyed JSON store and its CRUD surface; ``capture``
owns the ``camp bookmark`` command that records the CURRENT session; ``render``
owns ``camp bookmark ls``/``rm``; ``resume`` owns ``camp resume``, which prints
what the shell integration needs to re-enter a bookmarked session.

Which group a command asks its harness question about depends on the command.
Capture bookmarks the workspace the shell is standing in, so it uses the group
resolved from cwd. ``ls``, ``rm``, and
``resume`` name a ref instead, and a ref is exactly the thing you look up WITHOUT
knowing its group — so they read the group off the STORED record
(:func:`harness_for_bookmark`) and work from any cwd, including a plain shell
outside every group directory. The seam resolution itself is
:func:`camp.launch.profile.harness_for`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only; trailhead may not be installed
    from trailhead.harness.base import Harness


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
    answer :func:`camp.launch.profile.harness_for` gives a group with no
    ``[harness]`` block.
    """
    from ..launch.profile import harness_for

    return harness_for(group_config_for(record.get("group") or "", env=env) or {})
