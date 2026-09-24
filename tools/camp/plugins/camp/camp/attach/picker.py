"""The re-prompt loop and unreadable-pool result shared by every numbered
picker `camp attach` can show.

`camp attach` addresses a workspace by slug in one group; its one remaining
picker is `attach.door_target`'s bare (no-slug) form, over that group's
workspaces. This module holds the two pieces that picker needs:
:class:`PoolUnreadable`, the read-failure result every numbered picker in
this codebase reports through, and :func:`_prompt_for_index`, the
re-prompt loop (blank, non-numeric, and out-of-range input each re-prompt; a
valid choice returns) — generalized here so it has exactly one
implementation regardless of what it is picking among.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import IO


@dataclass(frozen=True)
class PoolUnreadable:
    """The pool a picker would present could not be enumerated at all.

    Distinct from an empty pool on purpose — an unreadable pool is never
    allowed to present as "nothing is running" (or, for
    `attach.door_target`'s workspace picker, "no workspaces exist").
    """

    reason: str


def _prompt_for_index(
    count: int,
    *,
    stdin: IO[str],
    stdout: IO[str],
    render,
) -> int | None:
    """The re-prompt loop shared by every numbered picker over *count* rows.

    *render* writes whatever header and rows this call's caller wants shown
    — this function knows nothing about what a "row" is, only how many
    there are, so a workspace picker and a session picker share the exact
    same re-prompt behaviour (blank, non-numeric, and out-of-range input
    each re-prompt; a valid choice returns) without either copying the
    other's loop. Returns the chosen row's 0-based index, or ``None`` on
    EOF (an empty ``readline()``), which a caller renders as its own
    "nothing was read" outcome.
    """
    while True:
        render()
        stdout.write("> ")
        stdout.flush()

        line = stdin.readline()
        if not line:
            return None

        choice = line.strip()
        if not choice.isdigit():
            continue

        index = int(choice)
        if index < 1 or index > count:
            continue

        return index - 1
