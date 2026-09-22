"""Tests for launch/binding.py — installing camp's server-global tmux
window-creation-key binding.

Test contract (task/the-binding-the-operator-s-ordinary-window-creation-key-opens-a-camp-composed-window):
- The first install in a server emits the notice; a subsequent install does
  not.
- Installing twice is idempotent: the seam's own `install_window_binding`
  is still called both times (so a stale binding self-heals), but the
  notice fires only once.
- The composed dispatch command never interpolates anything but
  `#{session_id}` into shell text, and does so single-quoted — the one
  value this task's design allows into the `run-shell` string, guarded
  against shell expansion of tmux's own `$N` session-id spelling.

Every test here drives a hand-rolled `Tmux` stand-in — this module never
builds its own `["tmux", ...]` argv (that lives in `launch/tmux.py`); what
there IS to test is the idempotency decision and the command it composes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _completed(*, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    """What the real `Tmux` seam hands back — `subprocess.CompletedProcess`,
    the same type every other tmux double in this suite returns."""
    return subprocess.CompletedProcess(
        args=["tmux"], returncode=returncode, stdout="", stderr=stderr
    )


_OK = _completed()


class _FakeTmux:
    """Records every `install_window_binding` call and answers
    `list_window_binding` with whatever the test primed.

    Also stands in for the server-global user option camp stores the
    displaced binding in, and for `source_command`, the replay path.
    """

    def __init__(
        self,
        *,
        existing_binding: str | None = None,
        install_result: object = _OK,
    ) -> None:
        self._binding = existing_binding
        self.install_calls: list[str] = []
        self.reset_calls: int = 0
        self._reset_result: object = _OK
        self._install_result: object = install_result
        self.server_options: dict[str, str] = {}
        self.sourced: list[str] = []

    def list_window_binding(self):
        return self._binding

    def install_window_binding(self, true_command, *, timeout=None):
        self.install_calls.append(true_command)
        self._binding = f"bind-key -T prefix c if-shell ... {true_command} ...\n"
        return self._install_result

    def reset_window_binding(self, *, timeout=None):
        self.reset_calls += 1
        return self._reset_result

    def set_server_option(self, key, value, *, timeout=None):
        self.server_options[key] = value
        return _OK

    def show_server_option(self, key, *, timeout=None):
        return self.server_options.get(key)

    def unset_server_option(self, key, *, timeout=None):
        self.server_options.pop(key, None)
        return _OK

    def source_command(self, command, *, timeout=None):
        self.sourced.append(command)
        return _OK


def test_first_install_in_a_server_emits_the_notice(capsys):
    from camp.launch.binding import install_window_key_binding

    tmux = _FakeTmux(existing_binding="bind-key -T prefix c new-window\n")

    install_window_key_binding(tmux, camp_bin="/opt/camp/cli/camp")

    assert len(tmux.install_calls) == 1
    err = capsys.readouterr().err
    assert "installed" in err.lower()


def test_a_subsequent_install_over_the_same_binding_does_not_emit_the_notice(capsys):
    """Idempotent: the seam call still fires (so a stale/foreign binding is
    reasserted), but the notice — meant for "this key's behaviour just
    changed" — fires only the first time."""
    from camp.launch.binding import install_window_key_binding

    tmux = _FakeTmux()
    install_window_key_binding(tmux, camp_bin="/opt/camp/cli/camp")
    capsys.readouterr()  # discard the first notice

    install_window_key_binding(tmux, camp_bin="/opt/camp/cli/camp")

    assert len(tmux.install_calls) == 2, "must re-issue the bind-key call every time"
    assert tmux.install_calls[0] == tmux.install_calls[1], (
        "must compose the identical true_command both times"
    )
    err = capsys.readouterr().err
    assert err == ""


def test_the_composed_command_names_the_given_camp_binary(capsys):
    from camp.launch.binding import install_window_key_binding

    tmux_a = _FakeTmux()
    install_window_key_binding(tmux_a, camp_bin="/opt/camp-a/cli/camp")
    tmux_b = _FakeTmux()
    install_window_key_binding(tmux_b, camp_bin="/opt/camp-b/cli/camp")
    capsys.readouterr()

    assert "/opt/camp-a/cli/camp" in tmux_a.install_calls[0]
    assert "/opt/camp-b/cli/camp" in tmux_b.install_calls[0]
    assert tmux_a.install_calls[0] != tmux_b.install_calls[0]


def test_the_session_id_placeholder_is_single_quoted_against_shell_expansion(capsys):
    """`#{session_id}` expands (tmux-side) to a literal `$N` — a shell
    metacharacter. The composed run-shell string must hold it inside single
    quotes so `/bin/sh -c` treats the resulting `$N` as inert text rather
    than expanding it as a positional parameter."""
    from camp.launch.binding import install_window_key_binding

    tmux = _FakeTmux()
    install_window_key_binding(tmux, camp_bin="/opt/camp/cli/camp")
    capsys.readouterr()

    command = tmux.install_calls[0]
    assert "'#{session_id}'" in command
    assert "--session-id '#{session_id}'" in command


def test_notice_is_suppressed_when_tmux_refuses_the_first_install(capsys):
    """The notice claims "prefix+c now opens a camp-composed window" — that
    is only true when tmux actually accepted the bind-key call. A failed
    install (non-zero exit) must not print the notice, even on a first
    install."""
    from camp.launch.binding import install_window_key_binding

    tmux = _FakeTmux(install_result=_completed(returncode=1, stderr="tmux refused"))

    install_window_key_binding(tmux, camp_bin="/opt/camp/cli/camp")

    assert len(tmux.install_calls) == 1, "the seam call still fires"
    err = capsys.readouterr().err
    assert err == ""


def test_notice_is_suppressed_when_tmux_could_not_be_asked_at_all(capsys):
    """`install_window_binding` answers `None` when tmux could not be
    reached at all — the same "did not answer" case must not be read as a
    successful install either."""
    from camp.launch.binding import install_window_key_binding

    tmux = _FakeTmux(install_result=None)

    install_window_key_binding(tmux, camp_bin="/opt/camp/cli/camp")

    err = capsys.readouterr().err
    assert err == ""


def test_the_composed_command_degrades_to_tmux_default_when_the_binary_is_missing(capsys):
    """Finding 5: `_DEFAULT_CAMP_BIN` bakes in this checkout's path at
    import time; a deleted worktree (this repo dogfoods camp from
    disposable ones) leaves that path stale forever. The composed command
    must resolve executability at FIRE time (a shell existence test
    embedded in the command itself) rather than trust the path was still
    good when it was installed — proven here at the argv-composition level;
    the real-tmux proof (this command actually still opens a window) lives
    in test_window_binding_end_to_end.py."""
    from camp.launch.binding import install_window_key_binding

    tmux = _FakeTmux()
    install_window_key_binding(tmux, camp_bin="/opt/camp/cli/camp")
    capsys.readouterr()

    command = tmux.install_calls[0]
    assert "-x /opt/camp/cli/camp" in command, command
    assert "else tmux new-window" in command, command


def test_the_composed_command_escapes_a_double_quote_in_the_camp_binary_path(capsys):
    """Finding 10: the composed command sits inside a tmux DOUBLE-quoted
    `run-shell "..."` string. `shlex.quote` only guards the SHELL layer —
    a literal `"` in camp_bin survives it unescaped (shlex.quote wraps in
    single quotes, which do not need internal escaping for the shell) and
    would terminate tmux's own double-quoted token early, producing a
    malformed binding. The composed string must escape it for tmux's own
    quoting layer too."""
    from camp.launch.binding import install_window_key_binding

    tmux = _FakeTmux()
    install_window_key_binding(tmux, camp_bin='/opt/ca"mp/cli/camp')
    capsys.readouterr()

    command = tmux.install_calls[0]
    # The command is `run-shell "<inner>"` — exactly one literal `"`
    # immediately after `run-shell ` (the opening delimiter) and exactly
    # one at the very end (the closing delimiter); every OTHER `"` in the
    # string (i.e. the one from the path) must be backslash-escaped.
    assert command.startswith('run-shell "')
    assert command.endswith('"')
    body = command[len('run-shell "'):-1]
    assert '\\"' in body, body
    # No unescaped `"` remains inside the body.
    unescaped = body.replace('\\"', "")
    assert '"' not in unescaped, unescaped


def test_remove_window_key_binding_issues_the_reset_call_and_succeeds():
    """Removal always issues the reset call — whether or not a camp binding
    was ever installed on this server (contract bullet: "no binding
    installed" reports the same end state and succeeds, not an error)."""
    from camp.launch.binding import remove_window_key_binding

    never_installed = _FakeTmux()  # no existing_binding primed
    remove_window_key_binding(never_installed)
    assert never_installed.reset_calls == 1

    already_installed = _FakeTmux(existing_binding="bind-key -T prefix c if-shell ...\n")
    remove_window_key_binding(already_installed)
    assert already_installed.reset_calls == 1


def test_remove_window_key_binding_raises_camps_own_message_when_tmux_unreachable():
    """The refusal path: tmux could not be asked at all (`reset_window_binding`
    answers `None`, `Tmux`'s own tri-state for "did not answer") surfaces as
    camp's own exception with camp's own words, never a raw exception a
    caller has to translate itself."""
    from camp.launch.binding import WindowBindingRemovalError, remove_window_key_binding

    tmux = _FakeTmux()
    tmux._reset_result = None

    try:
        remove_window_key_binding(tmux)
        assert False, "expected WindowBindingRemovalError"
    except WindowBindingRemovalError as exc:
        assert "camp:" in str(exc)
        assert "tmux" in str(exc).lower()


def test_remove_window_key_binding_raises_camps_own_message_on_non_zero_exit():
    from camp.launch.binding import WindowBindingRemovalError, remove_window_key_binding

    tmux = _FakeTmux()
    tmux._reset_result = _completed(returncode=1, stderr="some tmux server error")

    try:
        remove_window_key_binding(tmux)
        assert False, "expected WindowBindingRemovalError"
    except WindowBindingRemovalError as exc:
        assert "camp:" in str(exc)
        assert "some tmux server error" in str(exc)


def test_remove_window_key_binding_succeeds_when_no_tmux_server_is_running_at_all():
    """A `bind-key` call answers non-zero with tmux's own "no server
    running" stderr shape when the socket's server was never started —
    unlike `new-session`, `bind-key` does not auto-start one (confirmed
    against real tmux 3.7c). That is not a reachability failure: with no
    server at all, the key is already tmux's default — the same "answered
    empty, not UNANSWERED" distinction `Tmux.list_sessions` already draws
    on this exact stderr shape."""
    from camp.launch.binding import remove_window_key_binding

    tmux = _FakeTmux()
    tmux._reset_result = _completed(
        returncode=1,
        stderr="error connecting to /tmp/tmux-501/camp_test_sock (No such file or directory)",
    )

    remove_window_key_binding(tmux)  # must not raise
    assert tmux.reset_calls == 1


def test_remove_window_key_binding_succeeds_when_the_socket_is_stale():
    """The other stderr shape tmux prints for "no server": the socket file
    exists but nothing listens on it — `no server running on <socket>` —
    which a server that exited without unlinking its socket leaves behind.
    Same meaning, same answer: the key is already tmux's default."""
    from camp.launch.binding import remove_window_key_binding

    tmux = _FakeTmux()
    tmux._reset_result = _completed(
        returncode=1,
        stderr="no server running on /tmp/tmux-501/camp_test_sock",
    )

    remove_window_key_binding(tmux)  # must not raise
    assert tmux.reset_calls == 1


# ---------------------------------------------------------------------------
# Giving the operator's own binding back
# ---------------------------------------------------------------------------


def test_the_first_install_captures_the_binding_it_displaces(capsys):
    """camp overwrites a server-global table entry it does not own, so it
    records what was there in order to put it back. Varied across two
    different pre-existing bindings so this cannot pass by storing a
    constant."""
    from camp.launch.binding import _PRIOR_BINDING_OPTION, install_window_key_binding

    for prior in (
        'bind-key    -T prefix c       new-window -c "#{pane_current_path}"',
        "bind-key -r -T prefix c       split-window -h",
    ):
        tmux = _FakeTmux(existing_binding=f"bind-key -T prefix n next-window\n{prior}\n")
        install_window_key_binding(tmux, camp_bin="/opt/camp/cli/camp")
        capsys.readouterr()

        assert tmux.server_options[_PRIOR_BINDING_OPTION] == prior


def test_an_unbound_key_is_captured_as_an_unbind_not_as_nothing(capsys):
    """An operator who deliberately unbound this key must get it back
    unbound. Storing nothing would be indistinguishable from "camp never
    ran here", which falls back to tmux's compiled-in default — imposing a
    binding the operator had removed."""
    from camp.launch.binding import _PRIOR_BINDING_OPTION, install_window_key_binding

    tmux = _FakeTmux(existing_binding="bind-key -T prefix n next-window\n")
    install_window_key_binding(tmux, camp_bin="/opt/camp/cli/camp")
    capsys.readouterr()

    assert tmux.server_options[_PRIOR_BINDING_OPTION] == "unbind-key -T prefix c"


def test_a_re_install_does_not_overwrite_the_captured_binding(capsys):
    """The capture happens on the first install only. Re-capturing on a
    second workspace would store CAMP's own binding as the thing to restore,
    so unbind would hand the operator camp's dispatch back — permanently,
    and with no way to tell."""
    from camp.launch.binding import _PRIOR_BINDING_OPTION, install_window_key_binding

    prior = 'bind-key    -T prefix c       new-window -c "#{pane_current_path}"'
    tmux = _FakeTmux(existing_binding=f"{prior}\n")

    install_window_key_binding(tmux, camp_bin="/opt/camp/cli/camp")
    install_window_key_binding(tmux, camp_bin="/opt/camp/cli/camp")
    capsys.readouterr()

    assert len(tmux.install_calls) == 2, "the seam call still fires every time"
    assert tmux.server_options[_PRIOR_BINDING_OPTION] == prior


def test_removal_replays_the_captured_binding_and_drops_the_capture():
    """Removal restores what was captured rather than tmux's compiled-in
    default, and clears the capture afterwards so a later install starts
    over instead of restoring a binding from two servers ago."""
    from camp.launch.binding import _PRIOR_BINDING_OPTION, remove_window_key_binding

    prior = 'bind-key    -T prefix c       new-window -c "#{pane_current_path}"'
    tmux = _FakeTmux()
    tmux.server_options[_PRIOR_BINDING_OPTION] = prior

    remove_window_key_binding(tmux)

    assert tmux.sourced == [prior]
    assert tmux.reset_calls == 0, "the compiled default must not also be asserted over it"
    assert _PRIOR_BINDING_OPTION not in tmux.server_options


def test_removal_falls_back_to_the_tmux_default_when_nothing_was_captured():
    """Nothing captured means camp never installed on this server, or
    installed before it learned to capture. Either way the honest end state
    is tmux's own default — the behaviour this verb has always had, kept as
    the fallback rather than replaced by it."""
    from camp.launch.binding import remove_window_key_binding

    tmux = _FakeTmux()

    remove_window_key_binding(tmux)

    assert tmux.reset_calls == 1
    assert tmux.sourced == []


def test_a_replay_tmux_refuses_is_reported_not_swallowed():
    """The replay is the whole point of the verb, so a refused replay is a
    failed removal — reported with camp's own words, never a success that
    silently left camp's binding in place."""
    from camp.launch.binding import (
        _PRIOR_BINDING_OPTION,
        WindowBindingRemovalError,
        remove_window_key_binding,
    )

    tmux = _FakeTmux()
    tmux.server_options[_PRIOR_BINDING_OPTION] = "bind-key -T prefix c new-window"
    tmux.source_command = lambda command, *, timeout=None: _completed(
        returncode=1, stderr="tmux: bad command"
    )

    try:
        remove_window_key_binding(tmux)
        assert False, "expected WindowBindingRemovalError"
    except WindowBindingRemovalError as exc:
        assert "camp:" in str(exc)
        assert "bad command" in str(exc)
