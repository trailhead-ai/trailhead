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
    `list_window_binding` with whatever the test primed."""

    def __init__(self, *, existing_binding: str | None = None) -> None:
        self._binding = existing_binding
        self.install_calls: list[str] = []
        self.reset_calls: int = 0
        self._reset_result: object = _OK

    def list_window_binding(self):
        return self._binding

    def install_window_binding(self, true_command, *, timeout=None):
        self.install_calls.append(true_command)
        self._binding = f"bind-key -T prefix c if-shell ... {true_command} ...\n"
        return None

    def reset_window_binding(self, *, timeout=None):
        self.reset_calls += 1
        return self._reset_result


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


def test_notify_false_suppresses_the_notice_even_on_first_install(capsys):
    from camp.launch.binding import install_window_key_binding

    tmux = _FakeTmux()
    install_window_key_binding(tmux, camp_bin="/opt/camp/cli/camp", notify=False)

    assert len(tmux.install_calls) == 1
    assert capsys.readouterr().err == ""


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
