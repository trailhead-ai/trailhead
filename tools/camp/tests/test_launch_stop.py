"""Tests for launch/stop.py — the tmux seam re-export and the shared re-poll wait.

Test contract:
- ``Tmux.pane_command`` distinguishes an unanswered pane (tmux never returned)
  from an absent one (tmux answered, no pane there) — the tri-state lives in
  the seam itself.
- ``Tmux.list_sessions`` extends the same tri-state to a general listing
  rather than a scoped existence query: a no-server condition on stderr is the
  only non-zero exit answered as empty, every other non-zero exit (and an
  unanswerable ``_run``) is UNANSWERED, and a malformed row is dropped without
  blanking the rest of the answer — with the drop counted so a caller can tell
  the two apart.

Nothing here shells out: every test drives the real seam with subprocess.run
stubbed at the module level.

This module also holds `_FakeHarness`, `_group`, `_transcript`, and `_UUID_A`
— fixtures `test_attach_door_dispatch.py` imports for the same stand-in
harness and session shapes, unrelated to the Tmux tests below.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)

_UUID_A = "aaaaaaaa-1111-4111-8111-111111111111"

#: The variable the stand-in harness binds an account with. Not Claude Code's
#: own name, so a recognizer matching a hardcoded prefix cannot pass.
ACCOUNT_KEY = "FAKE_ACCOUNT_DIR"


def _group(name: str) -> dict[str, Any]:
    return {"group": {"name": name}}


def _transcript(session_id: str, cwd: Path | None, *, age_seconds: float = 60.0):
    from trailhead.harness.base import SessionTranscript

    return SessionTranscript(
        session_id=session_id,
        cwd=cwd,
        modified_at=_NOW - timedelta(seconds=age_seconds),
    )


class _FakeHarness:
    """Stand-in for the harness seam: the resume shape plus the scrub.

    Shared beyond this module — `test_attach_door_dispatch.py` imports this
    and the three fixtures above it, since the door dispatch it drives needs
    the same stand-in harness and session shapes this file's own Tmux tests
    do not.
    """

    name = "fakeharness"

    def session_resume(self, session_id):
        return ["fakeharness", "--reenter", session_id]

    def session_launch_env_unset(self):
        return ["FAKE_TOKEN", "FAKE_SOCKET"]

    def session_launch_env_set(self, account, *, env=None):
        """The account binding the launch engine composes into a pane.

        The key is deliberately NOT Claude Code's: a recognizer that hardcodes a
        variable name instead of asking the harness fails every test built on
        this stand-in.
        """
        return {ACCOUNT_KEY: str((env or {}).get("HOME", "/fake-home"))}


def test_the_real_tmux_seam_separates_an_unanswered_pane_from_an_absent_one(
    monkeypatch,
) -> None:
    """The tri-state lives in the seam: a call that never returned is
    UNANSWERED, and a session with no pane is None."""
    import subprocess as _subprocess

    from camp.launch import stop

    def _timeout(*args, **kwargs):
        raise _subprocess.TimeoutExpired(cmd="tmux", timeout=1)

    monkeypatch.setattr(stop.subprocess, "run", _timeout)
    assert stop.Tmux().pane_command("camp-x") is stop.UNANSWERED

    def _empty(*args, **kwargs):
        return _subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(stop.subprocess, "run", _empty)
    assert stop.Tmux().pane_command("camp-x") is None


# ---------------------------------------------------------------------------
# Enumeration — Tmux.list_sessions
#
# Every test below drives the real seam (stop.Tmux()) with subprocess.run
# stubbed at the module level, per the pattern the pane_command tri-state
# tests above already use — never a fixed constant, never _FakeTmux, because
# the property under test is what Tmux.list_sessions does with what tmux
# printed.
# ---------------------------------------------------------------------------


def _completed(*, returncode: int, stdout: str = "", stderr: str = ""):
    import subprocess as _subprocess

    return _subprocess.CompletedProcess(
        args=["tmux"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_list_sessions_answers_empty_when_tmux_reports_no_rows(monkeypatch) -> None:
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess, "run", lambda *a, **k: _completed(returncode=0, stdout="")
    )

    result = stop.Tmux().list_sessions()

    assert isinstance(result, stop.SessionListing)
    assert result.sessions == ()


def test_list_sessions_parses_one_session(monkeypatch) -> None:
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="3|1700000000|camp-feat-a-11112222\n"),
    )

    result = stop.Tmux().list_sessions()

    assert isinstance(result, stop.SessionListing)
    assert result.sessions == (stop.TmuxSession(name="camp-feat-a-11112222", windows=3, activity=1700000000),)


def test_list_sessions_parses_many_sessions(monkeypatch) -> None:
    from camp.launch import stop

    stdout = "1|1700000000|camp-feat-a-11112222\n2|1700000000|camp-feat-b-22223333\n5|1700000000|not-camp-at-all\n"
    monkeypatch.setattr(
        stop.subprocess, "run", lambda *a, **k: _completed(returncode=0, stdout=stdout)
    )

    result = stop.Tmux().list_sessions()

    assert isinstance(result, stop.SessionListing)
    assert result.sessions == (
        stop.TmuxSession(name="camp-feat-a-11112222", windows=1, activity=1700000000),
        stop.TmuxSession(name="camp-feat-b-22223333", windows=2, activity=1700000000),
        stop.TmuxSession(name="not-camp-at-all", windows=5, activity=1700000000),
    )


def test_list_sessions_reads_the_window_count_per_row_not_a_default(monkeypatch) -> None:
    """Two sessions in one answer, with DIFFERENT counts — this fails if the
    count comes from a length or a hardcoded default rather than the row."""
    from camp.launch import stop

    stdout = "1|1700000000|camp-feat-a-11112222\n7|1700000000|camp-feat-b-22223333\n"
    monkeypatch.setattr(
        stop.subprocess, "run", lambda *a, **k: _completed(returncode=0, stdout=stdout)
    )

    result = stop.Tmux().list_sessions()

    counts = {s.name: s.windows for s in result.sessions}
    assert counts == {"camp-feat-a-11112222": 1, "camp-feat-b-22223333": 7}


def test_a_session_name_containing_the_delimiter_is_parsed_whole(monkeypatch) -> None:
    """This is the test that fails if the fields are ordered name-first or the
    split on the delimiter is unbounded rather than split-once."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="9|1700000000|foo|bar\n"),
    )

    result = stop.Tmux().list_sessions()

    assert result.sessions == (stop.TmuxSession(name="foo|bar", windows=9, activity=1700000000),)


def test_a_session_name_carrying_a_pipe_survives_intact_with_the_correct_count(
    monkeypatch,
) -> None:
    """Measured as legal on tmux 3.7c via
    ``rename-session -t x 'camp-pipe|9|evil-1a2b3c4d'`` — a real input, not a
    hypothetical."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="9|1700000000|camp-pipe|9|evil-1a2b3c4d\n"),
    )

    result = stop.Tmux().list_sessions()

    assert result.sessions == (
        stop.TmuxSession(name="camp-pipe|9|evil-1a2b3c4d", windows=9, activity=1700000000),
    )


def test_a_no_server_condition_answers_empty_not_unanswered(monkeypatch) -> None:
    """A machine with no tmux server genuinely has zero sessions — the ONE
    non-zero exit that means empty rather than unanswered."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="error connecting to /tmp/tmux-501/default (No such file or directory)",
        ),
    )

    result = stop.Tmux().list_sessions()

    assert isinstance(result, stop.SessionListing)
    assert result.sessions == ()


def test_a_stale_socket_with_no_server_listening_is_an_empty_listing(monkeypatch) -> None:
    """tmux's other "no server" shape: the socket path exists but nothing
    listens on it (`no server running on <socket>`). Same meaning as the
    connect failure, same answer: an empty listing, not an outage."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="no server running on /tmp/tmux-501/default",
        ),
    )

    result = stop.Tmux().list_sessions()

    assert isinstance(result, stop.SessionListing)
    assert result.sessions == ()


def test_no_server_running_on_with_no_operand_is_unanswered(monkeypatch) -> None:
    """The stale-socket shape always names the socket it found dead —
    `no server running on <socket>`. A stderr line that merely ends in the
    bare phrase, with nothing after the trailing space, is not that shape
    (real tmux answers always carry an operand) and must not be read as an
    empty server."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="camp: no server running on ",
        ),
    )

    result = stop.Tmux().list_sessions()

    assert result is stop.UNANSWERED


def test_an_unrelated_error_mentioning_a_missing_file_is_unanswered(monkeypatch) -> None:
    """The no-server condition is `error connecting to <socket> (No such
    file or directory)`. A non-zero exit that merely CARRIES that phrase —
    a config file tmux could not source, a shell wrapper's own complaint —
    is an outage, and reading it as an empty server would render every
    workspace `none` during a live one: a wrong answer wearing a right
    answer's clothes."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="/etc/tmux.conf: 3: No such file or directory",
        ),
    )

    result = stop.Tmux().list_sessions()

    assert result is stop.UNANSWERED


def test_an_unsafe_socket_directory_is_unanswered_not_empty(monkeypatch) -> None:
    """A live outage, not an empty server: this is the test that fails if the
    implementation keys on the exit status alone."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="directory /tmp/tmux-501 has unsafe permissions",
        ),
    )

    result = stop.Tmux().list_sessions()

    assert result is stop.UNANSWERED


def test_an_unreachable_socket_is_unanswered_not_empty(monkeypatch) -> None:
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="error connecting to /some/very/long/path (File name too long)",
        ),
    )

    result = stop.Tmux().list_sessions()

    assert result is stop.UNANSWERED


def test_an_unrecognised_stderr_on_non_zero_exit_routes_to_unanswered(monkeypatch) -> None:
    """The conservative default: a future tmux rewording degrades to unknown
    rather than to a confident wrong answer."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=1, stderr="some new tmux error text"),
    )

    result = stop.Tmux().list_sessions()

    assert result is stop.UNANSWERED


def test_an_os_error_launching_tmux_is_unanswered(monkeypatch) -> None:
    from camp.launch import stop

    def _raise(*args, **kwargs):
        raise OSError("tmux not found")

    monkeypatch.setattr(stop.subprocess, "run", _raise)

    assert stop.Tmux().list_sessions() is stop.UNANSWERED


def test_a_timeout_expired_launching_tmux_is_unanswered(monkeypatch) -> None:
    import subprocess as _subprocess

    from camp.launch import stop

    def _raise(*args, **kwargs):
        raise _subprocess.TimeoutExpired(cmd="tmux", timeout=1)

    monkeypatch.setattr(stop.subprocess, "run", _raise)

    assert stop.Tmux().list_sessions() is stop.UNANSWERED


def test_a_malformed_row_with_no_delimiter_is_dropped_and_reported(monkeypatch) -> None:
    """One bad row must not blank the listing, and the drop must be visible —
    a caller must be able to tell 'one row was unparseable' apart from 'the
    answer was empty'."""
    from camp.launch import stop

    stdout = "garbage-no-delimiter\n3|1700000000|camp-feat-a-11112222\n"
    monkeypatch.setattr(
        stop.subprocess, "run", lambda *a, **k: _completed(returncode=0, stdout=stdout)
    )

    result = stop.Tmux().list_sessions()

    assert result.sessions == (stop.TmuxSession(name="camp-feat-a-11112222", windows=3, activity=1700000000),)
    assert result.dropped == 1


def test_a_malformed_row_with_a_non_numeric_count_is_dropped_and_reported(
    monkeypatch,
) -> None:
    from camp.launch import stop

    stdout = "not-a-number|camp-feat-a-11112222\n3|1700000000|camp-feat-b-22223333\n"
    monkeypatch.setattr(
        stop.subprocess, "run", lambda *a, **k: _completed(returncode=0, stdout=stdout)
    )

    result = stop.Tmux().list_sessions()

    assert result.sessions == (stop.TmuxSession(name="camp-feat-b-22223333", windows=3, activity=1700000000),)
    assert result.dropped == 1


def test_list_sessions_argv_is_list_sessions_with_dash_f(  # inert-gate: allow seam wiring, no input to vary
    monkeypatch,
) -> None:
    from camp.launch import stop

    captured: dict[str, Any] = {}

    def _record(args, **kwargs):
        captured["args"] = args
        return _completed(returncode=0, stdout="")

    monkeypatch.setattr(stop.subprocess, "run", _record)

    stop.Tmux().list_sessions()

    assert captured["args"][0] == "tmux"
    assert "list-sessions" in captured["args"]
    assert "-F" in captured["args"]
