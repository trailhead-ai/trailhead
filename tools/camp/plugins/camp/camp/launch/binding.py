"""Installs camp's server-global tmux window-creation-key binding.

`install_window_key_binding` is the one call `create_workspace_session`
makes to wire the operator's ordinary window-creation key (prefix+``c``)
into camp's own composition — see `camp.launch.tmux.Tmux.install_window_binding`
for the argv this issues and why its else-branch reproduces tmux's own
compiled-in default rather than deferring to one (tmux has no
revert-to-default primitive).

Idempotency and the first-install notice
------------------------------------------
Installing the identical binding twice must not accumulate or duplicate the
table entry — `bind-key` overwrites the single entry regardless, so the
`install_window_binding` call itself is already idempotent. What is NOT
free is the operator-facing notice: pressing this key now behaves
differently, matching `camp.attach.prefix_warning`'s notice for the same
class of surprise (a key's behaviour silently changing underneath the
operator). That notice must fire once per server, not once per workspace
created on it.

"First in this server" is read BEFORE the install call, from
`Tmux.list_window_binding` (`tmux list-keys -T prefix c`): if the exact
`true_command` this call is about to install is already present in that
output, another camp process already installed the identical binding and
this is a re-install — no notice. Anything else (tmux's own stock
`new-window` binding, a foreign one, or no answer at all) is treated as a
first install. The seam call still fires either way, so a stale or foreign
binding self-heals on the very next workspace creation.

The composed command
----------------------
The ONLY value interpolated into the `run-shell` string is
`#{session_id}` — tmux's own numeric session identifier (`$3`), never
group or slug (see `window_dispatch`'s module docstring for why those
travel as session-local tmux OPTIONS instead). `#{session_id}` expands
(tmux-side, before the shell ever sees the string) to a literal `$N` — a
shell metacharacter — so it is wrapped in single quotes in the composed
string: `/bin/sh -c` then treats the resulting `$N` as inert text rather
than expanding it as an empty positional parameter. Verified against a
real tmux server in `test_window_binding.py`'s end-to-end test.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Protocol

#: This module lives at plugins/camp/camp/launch/binding.py; parents[2] is
#: the plugin root (plugins/camp/), the same directory cli/dispatch.py's own
#: `_PLUGIN_ROOT` resolves to from its own location at the same package
#: depth — see that module's comment for why `.resolve()` is load-bearing
#: (an installed plugin may be reached through a symlink).
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CAMP_BIN = str(_PLUGIN_ROOT / "cli" / "camp")

#: Printed on stderr the first time a server ever gets this binding —
#: mirrors `camp.attach.prefix_warning.MESSAGE`'s one-line, non-repeating
#: notice for the same class of surprise (a key's behaviour changing
#: underneath the operator).
_NOTICE = "camp: installed a tmux key binding — prefix+c now opens a camp-composed window"


class _TmuxLike(Protocol):
    def list_window_binding(self) -> str | None: ...

    def install_window_binding(self, true_command: str, *, timeout: float | None = None): ...


def _dispatch_true_command(camp_bin: str) -> str:
    """The `if-shell` true-branch command string: `run-shell "<camp_bin>
    window-dispatch --session-id '#{session_id}'"`.

    `#{session_id}` is single-quoted WITHIN the run-shell string — see the
    module docstring's "The composed command" section for why that quoting
    is load-bearing rather than cosmetic.
    """
    inner = f"{shlex.quote(camp_bin)} window-dispatch --session-id '#{{session_id}}'"
    return f'run-shell "{inner}"'


def install_window_key_binding(
    tmux: _TmuxLike,
    *,
    camp_bin: str | None = None,
    notify: bool = True,
) -> bool:
    """Install (or idempotently re-issue) camp's prefix+``c`` binding
    against *tmux*.

    Returns whether this call performed a first install (the notice fired
    or would have, had *notify* been true) — mainly useful to callers/tests
    that want to assert on the decision without parsing stderr.
    """
    resolved_bin = camp_bin if camp_bin is not None else _DEFAULT_CAMP_BIN
    true_command = _dispatch_true_command(resolved_bin)

    before = tmux.list_window_binding()
    is_first_install = before is None or true_command not in before

    tmux.install_window_binding(true_command)

    if is_first_install and notify:
        print(_NOTICE, file=sys.stderr)

    return is_first_install
