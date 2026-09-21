"""``camp window unbind`` — the operator-facing removal for camp's
server-global tmux window-creation-key binding.

The paired verb for `camp.launch.binding.install_window_key_binding`
(installed automatically by `create_workspace_session`, never typed by an
operator). Unlike `window-dispatch` (`cli/window_dispatch.py`), this IS a
verb an operator types — it is the one documented way back when the key
misfires or the operator simply wants their ordinary window-creation key
back, and it must work from a plain shell with no dependency on the key it
is restoring (see `camp.launch.binding.remove_window_key_binding`'s
docstring for why there is no "already default" special case).

Dispatched groupless, before group resolution, for the same reason `kill`
and `attach` are (see `cli/dispatch.py`'s `_SKIP_GROUP_RESOLVE` comment): a
sibling group's malformed config must never block an operator reaching for
the one command that takes a broken key binding back.
"""

from __future__ import annotations

from typing import Callable

from ..launch.binding import WindowBindingRemovalError, remove_window_key_binding
from .parser import CampParser

#: Printed on success. Two sentences because removal has two honest end
#: states: it put back the binding camp displaced, or — with nothing
#: captured — left the key at tmux's compiled-in default. The first does
#: not claim the binding was the operator's own, since camp cannot tell a
#: hand-written binding from tmux's stock one and only knows it replaced
#: something.
_RESTORED_PRIOR = "camp: the window-creation key is back to the binding camp replaced"
_RESTORED_DEFAULT = "camp: the window-creation key is back to its tmux default"


def dispatch_window_verb(
    subcommand: str | None,
    *,
    remove: Callable[[object], None],
    tmux: object,
) -> tuple[bool, str]:
    """The pure decision behind `camp window <subcommand>`: given the
    subcommand token and an injected *remove* call (`remove_window_key_binding`
    in production) against *tmux*, returns `(ok, message)` — never raises,
    never prints, never exits, so it is testable with no real tmux and no
    real process exit involved.

    `unbind` is the only subcommand today (`camp window new`, named in the
    design doc, belongs to a different task and is out of this one's
    scope) — anything else, including no subcommand at all, is a usage
    refusal rather than an attempt to guess intent.
    """
    if subcommand != "unbind":
        return False, "unbind is the only subcommand — usage: camp window unbind"

    try:
        restored_prior = remove(tmux)
    except WindowBindingRemovalError as exc:
        return False, str(exc)

    return True, (_RESTORED_PRIOR if restored_prior else _RESTORED_DEFAULT)


def _cmd_window_cli(args: list[str]) -> None:
    """``camp window unbind``'s real wiring: parse the one positional,
    build a real `Tmux`, and report through `CampParser.die` / stdout —
    never a raw traceback, since this is the command an operator reaches
    for specifically because something is already broken.
    """
    from ..launch.tmux import Tmux

    parser = CampParser(verb="window")
    parser.add_argument("subcommand", nargs="?")
    parsed = parser.parse_args(args)

    ok, message = dispatch_window_verb(
        parsed.subcommand, remove=remove_window_key_binding, tmux=Tmux()
    )
    if not ok:
        parser.die(message)
        return

    print(message)
