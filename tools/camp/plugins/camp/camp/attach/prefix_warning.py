"""The nested-multiplexer prefix-conflict warning.

`camp attach` hands the operator's terminal to `tmux attach`. If the operator
is already inside a tmux session — indicated by the ``TMUX`` environment
variable tmux itself sets in every pane it starts — attaching nests a second
tmux inside the first, and the nested session's key prefix (default
``C-b``) will conflict with the outer one's: a prefix keystroke reaches
whichever tmux owns the front-most pane, not necessarily the one the
operator meant. This is not an error — nesting is a legitimate, sometimes
deliberate, workflow — so the response is a notice on stderr, never a
refusal: it does not change the exit status and never prevents the handoff.

The decision is made from the *operator's own* environment, read once,
locally, before any handoff. It is never made from a remote target's
environment: `camp attach --host` and `-a` still hand this module the
operator's own local environment, because the thing that would conflict is
the local terminal's outer tmux, not anything the target machine runs. The
target's own `TMUX` (if it happens to be set on that machine, e.g. because
the operator left a stray session running there) is irrelevant to whether
attaching from *here* nests inside a multiplexer already wrapping *this*
terminal, and this module has no path to reach it at all: it takes exactly
one environment mapping and consults exactly one key in it.
"""

from __future__ import annotations

import os
import sys

#: Set by tmux in every pane it starts. Its presence is the standard signal
#: that the calling process is already running inside a tmux session.
_MULTIPLEXER_ENV_VAR = "TMUX"

MESSAGE = (
    "camp: attaching from inside a tmux session — the nested session's key "
    "prefix may conflict with this one's."
)


def inside_multiplexer(env: dict[str, str] | None = None) -> bool:
    """Whether *env* (defaulting to ``os.environ``) indicates the caller is
    already inside a tmux session.

    Args:
        env: Override for the environment consulted (for hermetic tests).
             Defaults to ``os.environ`` — always the operator's own local
             process environment, never a remote target's.
    """
    if env is None:
        env = os.environ
    return bool(env.get(_MULTIPLEXER_ENV_VAR))


def warn_if_nested(env: dict[str, str] | None = None) -> None:
    """Print the prefix-conflict warning to stderr if `inside_multiplexer`.

    A no-op otherwise. Never raises, never touches the exit status, and
    performs no handoff of its own — the caller emits this before handing
    off, not instead of it.
    """
    if inside_multiplexer(env):
        print(MESSAGE, file=sys.stderr)
