"""Installs camp's server-global tmux window-creation-key binding.

`install_window_key_binding` is the one call `create_workspace_session`
makes to wire the operator's ordinary window-creation key (prefix+``c``)
into camp's own composition — see `camp.launch.tmux.Tmux.install_window_binding`
for the argv this issues and why its else-branch reproduces tmux's own
compiled-in default rather than deferring to one (tmux has no
revert-to-default primitive).

Giving the key back
---------------------
prefix+``c`` is a server-global table entry with a single slot, and it is
not camp's: an operator's own `.tmux.conf` line lives there, and tmux does
not re-read that file, so overwriting it would destroy it for the life of
the server. So the FIRST install captures the line it is about to displace
— read from the same `list-keys` output the first-install check already
reads — into a server-global user option, and `remove_window_key_binding`
replays it. A key that carried no binding at all is captured as an
`unbind-key`, so "put it back how it was" holds in that direction too.

The capture is deliberately server-scoped and in-memory rather than on
disk: it must live exactly as long as the binding it describes, and a file
would outlive the server and offer a later, unrelated one a binding from a
dead server.

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
`Tmux.list_window_binding` (`tmux list-keys -T prefix` — the WHOLE table,
never a per-key filter; see that method's own docstring): if
`_dispatch_marker`'s quote-free substring is already present in that
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
real tmux server in `test_window_binding_end_to_end.py`.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Protocol

from .tmux import _NO_SERVER_STDERR_RE, prefix_window_binding_line

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

#: Where the binding camp displaced is kept until `camp window unbind` puts
#: it back. A SERVER-global user option, because that is exactly the
#: lifetime of the thing being displaced: camp's binding is server-global
#: and dies with the server, so the memory of what it replaced must live
#: and die on the same boundary. It is deliberately not on disk — a file
#: would outlive the server and hand a later, unrelated server a binding
#: from a dead one.
_PRIOR_BINDING_OPTION = "@camp_prior_window_binding"

#: What is captured when the key carried NO binding before camp took it.
#: Storing nothing would be indistinguishable from "camp never installed on
#: this server", which falls back to tmux's compiled-in default — and so
#: would impose a binding on an operator who had deliberately removed one.
#: Captured as a command rather than a flag so the restore path has exactly
#: one shape: replay whatever was stored.
_CAPTURED_UNBOUND = "unbind-key -T prefix c"


class _TmuxLike(Protocol):
    def list_window_binding(self) -> str | None: ...

    def install_window_binding(self, true_command: str, *, timeout: float | None = None): ...

    def reset_window_binding(self, *, timeout: float | None = None): ...

    def set_server_option(self, key: str, value: str, *, timeout: float | None = None): ...

    def show_server_option(self, key: str, *, timeout: float | None = None) -> str | None: ...

    def unset_server_option(self, key: str, *, timeout: float | None = None): ...

    def source_command(self, command: str, *, timeout: float | None = None): ...


class WindowBindingRemovalError(Exception):
    """Removal could not be completed — tmux either did not answer at all
    or answered with a non-zero exit. Carries camp's own words; the one
    caller (`cli/window.py`'s `unbind` verb) reports `str(exc)` verbatim on
    stderr rather than letting a raw exception surface, since this is a
    command an operator runs from a shell specifically because something
    is already wrong."""


def _dispatch_marker(camp_bin: str) -> str:
    """A stable, QUOTE-FREE substring of the composed dispatch invocation:
    `<camp_bin> window-dispatch --session-id`.

    Used to detect "already installed" rather than the full composed
    command — see :func:`_dispatch_true_command`'s docstring for why a
    verbatim comparison against `Tmux.list_window_binding`'s answer cannot
    work: `list-keys` re-serializes a `run-shell` string's internal quoting
    (backslash-escaping the embedded `"` characters our own composed string
    carries), so the string this module built and the string `list-keys`
    prints back are never byte-identical even when they describe the exact
    same binding. A substring with no quote characters of its own survives
    that round-trip unescaped, confirmed against a real tmux 3.7c server.
    """
    return f"{shlex.quote(camp_bin)} window-dispatch --session-id"


def _tmux_escape_dquoted(text: str) -> str:
    """Escape *text* for embedding inside a tmux DOUBLE-quoted command-string
    token (the `"..."` in `run-shell "<text>"`).

    `bind-key`'s composed argv reaches tmux as one already-built string —
    never through an OS shell (see `Tmux.install_window_binding`) — so tmux
    ITSELF re-tokenizes `true_command` using its own command grammar, which
    treats an embedded `"` as ending the quoted token early, same as a shell
    would. `shlex.quote` (applied by :func:`_dispatch_marker` to `camp_bin`)
    only guards the SEPARATE, inner shell layer `run-shell` hands its
    argument to (`/bin/sh -c`); a literal `"` in a path survives
    `shlex.quote` unescaped (it wraps in single quotes, which need no
    internal `"` escaping for the shell) and would still break tmux's own
    outer quoting. Backslash-escaping `\\` and `"` here is the fix for that
    SECOND, tmux-level layer — applied last, after the shell-level quoting
    is already in place, so the two layers compose rather than collide.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _dispatch_true_command(camp_bin: str) -> str:
    """The `if-shell` true-branch command string: `run-shell "<guard>"`,
    where `<guard>` is a shell `if`/`else` that resolves *camp_bin*'s
    executability at FIRE time (every key press), not merely at install
    time.

    This repo dogfoods camp from disposable worktrees, so `camp_bin` (a
    path baked in at import time — see `_DEFAULT_CAMP_BIN`) going stale
    between install and a later key press is an ordinary end state, not an
    exotic one. Without the guard, a stale/missing binary makes
    `run-shell`'s inner `/bin/sh -c` fail silently (command not found) and
    the key opens NO window at all — a dead key, confirmed against real
    tmux. With it, a missing/non-executable binary falls through to
    `tmux new-window -t '#{session_id}'`, the SAME degrade-to-default
    behaviour the outer `if-shell`'s own else-branch already provides for
    an unmarked session (see `Tmux.install_window_binding`) — explicitly
    targeted at the firing session (never the ambient "current" session)
    since `run-shell`'s spawned shell is not itself bound to the key
    press's session context the way a direct `if-shell` else-branch is.

    Built from :func:`_dispatch_marker` so the marker stays a substring of
    this string by construction (no longer a literal PREFIX now that the
    `if`/`else` guard precedes it) — the "already installed" check searches
    `list-keys` output for the marker anywhere in the text; substring
    presence is all that check ever required.

    `#{session_id}` is single-quoted WITHIN the run-shell string — see the
    module docstring's "The composed command" section for why that quoting
    is load-bearing rather than cosmetic. The whole guard is passed through
    :func:`_tmux_escape_dquoted` before being wrapped in `run-shell "..."`,
    fixing the separate tmux-level quoting layer described there.
    """
    quoted_bin = shlex.quote(camp_bin)
    guard = (
        f"if [ -x {quoted_bin} ]; then {_dispatch_marker(camp_bin)} '#{{session_id}}'; "
        f"else tmux new-window -t '#{{session_id}}'; fi"
    )
    return f'run-shell "{_tmux_escape_dquoted(guard)}"'


def install_window_key_binding(
    tmux: _TmuxLike,
    *,
    camp_bin: str | None = None,
) -> None:
    """Install (or idempotently re-issue) camp's prefix+``c`` binding
    against *tmux*.

    The seam call fires every time — a stale or foreign binding self-heals
    on the next workspace creation. Only the operator-facing notice is
    conditional; see the module docstring for how "first in this server" is
    decided.
    """
    resolved_bin = camp_bin if camp_bin is not None else _DEFAULT_CAMP_BIN
    true_command = _dispatch_true_command(resolved_bin)
    marker = _dispatch_marker(resolved_bin)

    before = tmux.list_window_binding()
    is_first_install = before is None or marker not in before

    if is_first_install:
        _capture_displaced_binding(tmux, before)

    result = tmux.install_window_binding(true_command)
    installed = result is not None and result.returncode == 0

    if is_first_install and installed:
        print(_NOTICE, file=sys.stderr)


def _capture_displaced_binding(tmux: _TmuxLike, table: str | None) -> None:
    """Remember the prefix+``c`` binding this install is about to overwrite,
    so :func:`remove_window_key_binding` can put it back.

    Runs on the FIRST install only — the caller's own "is this camp's
    binding already?" answer decides. Re-capturing on every workspace would
    store CAMP's binding as the thing to restore the moment a second
    workspace was created on the same server, which is worse than not
    capturing at all: the operator would get camp's dispatch handed back to
    them by the very verb that exists to take it away, with nothing to
    indicate it.

    The line is stored exactly as `list-keys` rendered it (see
    :func:`~camp.launch.tmux.prefix_window_binding_line`), because the
    restore replays it through tmux's own parser rather than camp's.

    A table camp could not read at all (`None` — tmux did not answer) is not
    a capture: there is no evidence about what was there, and recording a
    guess would be worse than the fallback. Nothing is stored, and removal
    falls back to tmux's compiled-in default.
    """
    if table is None:
        return
    displaced = prefix_window_binding_line(table)
    tmux.set_server_option(
        _PRIOR_BINDING_OPTION, displaced if displaced is not None else _CAPTURED_UNBOUND
    )


def remove_window_key_binding(tmux: _TmuxLike) -> bool:
    """Give the server-global prefix+``c`` key back — the paired removal
    for :func:`install_window_key_binding`.

    When the install captured the binding it displaced, that binding is
    replayed and the key is exactly what it was before camp touched it.
    When nothing was captured — camp never installed on this server, or
    installed before it learned to capture — the key is reasserted to
    tmux's compiled-in default instead.

    Either way this is an END STATE request, not an undo of a specific
    prior action, so there is no "already default" branch to special-case
    and no way for the fallback to fail simply because there was nothing to
    remove — see `Tmux.reset_window_binding`'s own docstring for why
    reasserting the default is always safe and idempotent.

    A server that was never started at all is the SAME end state, not a
    failure: unlike `new-session`, a bare `bind-key` call does not
    auto-start a tmux server (confirmed against real tmux 3.7c), so it
    answers non-zero with tmux's own "no server running" stderr shape
    (`camp.launch.tmux._NO_SERVER_STDERR_RE`) whenever the socket's server
    does not exist. With no server, the key is already tmux's default —
    the identical "answered empty, not unreachable" distinction
    `Tmux.list_sessions` already draws on this same stderr shape — so this
    is treated as success, never surfaced as a refusal.

    Answers ``True`` when the operator's OWN displaced binding was put back
    and ``False`` when the key was left at tmux's compiled-in default
    because camp had nothing captured. The caller reports a different
    sentence for each: telling an operator the key is "back to its tmux
    default" when their own `.tmux.conf` binding has just been restored
    describes the opposite of what happened.

    Raises :class:`WindowBindingRemovalError`, carrying camp's own words,
    when tmux could not be asked at all or answered with any OTHER
    non-zero exit — never lets tmux's own exception or stderr reach the
    caller raw.
    """
    captured = tmux.show_server_option(_PRIOR_BINDING_OPTION)
    if captured is not None:
        _replay_captured_binding(tmux, captured)
        return True

    result = tmux.reset_window_binding()
    if result is None:
        raise WindowBindingRemovalError(
            "camp: could not reach tmux to reset the window-creation key"
        )
    if result.returncode != 0:
        stderr = result.stderr or ""
        if _NO_SERVER_STDERR_RE.search(stderr):
            return False
        detail = stderr.strip()
        suffix = f" — {detail}" if detail else ""
        raise WindowBindingRemovalError(
            f"camp: tmux refused to reset the window-creation key{suffix}"
        )
    return False


def _replay_captured_binding(tmux: _TmuxLike, captured: str) -> None:
    """Put back the exact binding camp displaced, then forget it.

    The captured text is a tmux command tmux itself wrote, replayed through
    tmux's own parser — camp never re-tokenizes it (see
    :meth:`~camp.launch.tmux.Tmux.source_command`).

    The capture is dropped only after a successful replay: leaving it in
    place on failure means a retry still has something to restore, while
    dropping it would silently downgrade every later attempt to the
    compiled-in default. A refused replay is a failed removal and says so —
    reporting success here would leave camp's own binding installed while
    telling the operator it was gone.
    """
    result = tmux.source_command(captured)
    if result is None:
        raise WindowBindingRemovalError(
            "camp: could not reach tmux to restore the window-creation key"
        )
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        suffix = f" — {detail}" if detail else ""
        raise WindowBindingRemovalError(
            f"camp: tmux refused to restore the window-creation key{suffix}"
        )
    tmux.unset_server_option(_PRIOR_BINDING_OPTION)
