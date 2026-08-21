"""Which subverbs of a group-taking verb need no group resolved.

Camp resolves a group from cwd before dispatching most verbs, but some subverbs
address a GLOBAL, ref-keyed store and name their own target — those must run from
any cwd, including a plain shell outside every group directory. Classification
lives here, beside the dispatchers, because THREE entry points ask the question
(`cli.dispatch`'s pre-resolve special case, its group-aware dispatcher, and the
spine's no-group fallback) and they must never disagree about the answer.
"""

from __future__ import annotations

from typing import Callable


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

    from ..bookmark.render import cmd_bookmark_ls, cmd_bookmark_rm
    from ..spine import _consume_flag_value

    args = list(rest)
    _consume_flag_value(args, "--group")  # resolved (or unresolvable) upstream; drop it
    if not args:
        return None
    handler = {"ls": cmd_bookmark_ls, "rm": cmd_bookmark_rm}.get(args[0])
    if handler is None:
        return None
    return handler, args[1:]
