"""Tests for camp.host.handoff — the interactive attach handoff.

Test contract:
- The local argv names the resolved session's derived name, not the harness's
  own name.
- The remote argv requests a terminal and uses the host's declared camp
  location, not the bare command name — a host declaring no location still
  produces the documented default.
- The remote command is quoted by reusing the listing transport's own
  quote-and-join, never a second implementation of it. Tested with a battery
  of metacharacters, each asserted to reach the far side intact and
  uninterpreted.
- The interactive invocation carries the same fixed connection options the
  listing transport pins, differing only by requesting a terminal.
- A missing local binary is a camp refusal, not a traceback.
- The seam is called exactly once, with that argv, and nothing after the
  call runs.
- The remote path does not reach the listing transport.
- The exec seam flushes stdout and stderr immediately before it execs.

No real remote host is contacted anywhere in this file.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from camp.host import handoff  # noqa: E402
from camp.host import transport  # noqa: E402
from camp.host.config import Host  # noqa: E402
from camp.launch.recovery import SessionCandidate  # noqa: E402


def _candidate(derived_name: str) -> SessionCandidate:
    return SessionCandidate(
        session_id="sess-1",
        derived_name=derived_name,
        root=None,
        age_seconds=None,
        live=True,
        root_missing=False,
        unreadable=False,
    )


_HOST = Host(ssh="andromeda", camp_bin="/opt/camp/bin/camp")
_DEFAULT_HOST = Host(ssh="andromeda", camp_bin="camp")


# ---------------------------------------------------------------------------
# Local argv — names the resolved session's derived name, not the harness's
# own name.
# ---------------------------------------------------------------------------


def test_local_argv_names_the_derived_name_not_the_harness_name():
    candidate = _candidate("camp-my-session-abcd1234")
    harness_own_name = "claude-code-session-xyz"

    argv = handoff.local_argv(candidate.derived_name)

    assert argv == ["tmux", "attach", "-t", "camp-my-session-abcd1234"]
    assert harness_own_name not in argv


# ---------------------------------------------------------------------------
# Remote argv — terminal + host's declared camp location, default handling.
# ---------------------------------------------------------------------------


def test_remote_argv_uses_the_hosts_declared_camp_bin():
    argv = handoff.remote_argv(_HOST, "camp-my-session-abcd1234")
    joined = argv[-1]
    assert shlex.split(joined)[0] == "/opt/camp/bin/camp"


def test_remote_argv_uses_the_documented_default_when_host_declares_none():
    argv = handoff.remote_argv(_DEFAULT_HOST, "camp-my-session-abcd1234")
    joined = argv[-1]
    assert shlex.split(joined)[0] == "camp"


def test_remote_argv_requests_a_terminal():
    argv = handoff.remote_argv(_HOST, "camp-my-session-abcd1234")
    assert argv[:2] == ["ssh", "-t"]


# ---------------------------------------------------------------------------
# Quote-and-join reuse — proven by identity, not by copy.
# ---------------------------------------------------------------------------


def test_remote_argv_reuses_the_transports_own_quote_and_join(monkeypatch):
    calls = []
    real = transport.quote_and_join

    def spy(camp_bin, remote_argv):
        calls.append((camp_bin, list(remote_argv)))
        return real(camp_bin, remote_argv)

    monkeypatch.setattr(handoff, "quote_and_join", spy)

    handoff.remote_argv(_HOST, "some-ref")

    assert calls == [(_HOST.camp_bin, ["attach", "some-ref"])]


# ---------------------------------------------------------------------------
# The metacharacter battery — a battery, not a sample.
# ---------------------------------------------------------------------------

#: Each template carries a `{sentinel}` the shell would create *only* if it
#: interpreted the metacharacter. The sentinel path is filled in per-test from
#: `tmp_path`, so its existence is an outcome only the injection can produce —
#: an absolute path baked in here would make the assertion pass vacuously.
_METACHARACTER_BATTERY = [
    "camp-foo; touch {sentinel}",
    "camp-foo$(touch {sentinel})",
    "camp-foo`touch {sentinel}`",
    "camp-foo\ntouch {sentinel}",
    "camp-foo'with\"quotes",
]


@pytest.mark.parametrize("hostile_ref", _METACHARACTER_BATTERY)
def test_remote_argv_survives_a_local_shell_standing_in_for_the_far_side(
    hostile_ref, tmp_path
):
    """ssh joins trailing argv with a space and hands it to the remote shell.

    Standing in for that remote shell with a LOCAL `sh -c`, never a real
    host: the joined command must parse back to the exact original remote
    argv (no metacharacter interpreted) and must not execute anything extra
    (no injection).
    """
    sentinel = tmp_path / "injection-fired"
    hostile_ref = hostile_ref.format(sentinel=sentinel)

    fake_camp_bin = tmp_path / "fake_camp"
    recording = tmp_path / "recorded_argv"
    fake_camp_bin.write_text(
        "#!/bin/sh\nfor a in \"$@\"; do printf '%s\\0' \"$a\"; done > "
        + shlex.quote(str(recording)) + "\n"
    )
    fake_camp_bin.chmod(0o755)

    host = Host(ssh="andromeda", camp_bin=str(fake_camp_bin))
    argv = handoff.remote_argv(host, hostile_ref)
    joined = argv[-1]

    result = subprocess.run(
        ["sh", "-c", joined], capture_output=True, text=True, timeout=10, cwd=str(tmp_path)
    )
    assert not sentinel.exists()
    assert result.returncode == 0, result.stderr

    received = recording.read_bytes().split(b"\x00")[:-1]
    assert received == [b"attach", hostile_ref.encode()]


# ---------------------------------------------------------------------------
# Fixed connection options — same as the listing transport, differing only
# by the terminal request.
# ---------------------------------------------------------------------------


def test_remote_argv_carries_the_transports_fixed_connection_options():
    argv = handoff.remote_argv(_HOST, "some-ref")
    assert "-o" in argv
    joined = " ".join(argv)
    assert "-o BatchMode=yes" in joined
    assert "-o StrictHostKeyChecking=yes" in joined
    assert f"-o ConnectTimeout={transport.DEFAULT_CONNECT_TIMEOUT_SECONDS:g}" in joined


def test_remote_argv_honors_an_explicit_connect_timeout():
    argv = handoff.remote_argv(_HOST, "some-ref", connect_timeout=5.0)
    assert "-o ConnectTimeout=5" in " ".join(argv)


# ---------------------------------------------------------------------------
# A missing local binary is a camp refusal, not a traceback.
# ---------------------------------------------------------------------------

_HANDOFF_SCRIPT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {plugin_dir!r})
    from camp.host import handoff

    handoff.handoff(["camp-definitely-does-not-exist-anywhere-xyz", "attach", "ref"])
    """
)


def test_missing_local_binary_produces_camp_refusal_shape(tmp_path):
    script = tmp_path / "missing_binary_handoff.py"
    script.write_text(_HANDOFF_SCRIPT.format(plugin_dir=str(_PLUGIN_DIR)))

    result = subprocess.run(
        [sys.executable, "-B", str(script)], capture_output=True, text=True, timeout=10
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.startswith("camp: ")


# ---------------------------------------------------------------------------
# The exec seam flushes stdout/stderr immediately before it execs.
# ---------------------------------------------------------------------------

_FLUSH_SCRIPT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {plugin_dir!r})
    from camp.host import handoff

    print("PRE-EXEC-OUTPUT", end="")
    handoff.handoff(["echo", "EXEC-MARKER"])
    """
)


def test_handoff_flushes_before_exec_so_prior_output_is_not_lost(tmp_path):
    script = tmp_path / "flush_handoff.py"
    script.write_text(_FLUSH_SCRIPT.format(plugin_dir=str(_PLUGIN_DIR)))

    result = subprocess.run(
        [sys.executable, "-B", str(script)], capture_output=True, text=True, timeout=10
    )

    assert result.returncode == 0
    assert result.stdout == "PRE-EXEC-OUTPUTEXEC-MARKER\n"


# ---------------------------------------------------------------------------
# The seam is called exactly once, with that argv, and nothing after the
# call runs.
# ---------------------------------------------------------------------------


def test_seam_is_called_exactly_once_with_the_given_argv():
    calls = []

    def fake_seam(argv):
        calls.append(list(argv))

    handoff.handoff(["ssh", "-t", "andromeda", "camp attach ref"], exec_seam=fake_seam)

    assert calls == [["ssh", "-t", "andromeda", "camp attach ref"]]


def test_nothing_after_the_seam_call_runs_when_it_raises_a_non_startup_error():
    """A non-OSError raised by the seam propagates unchanged — nothing in
    handoff() catches it, transforms it, or runs any further logic after the
    call.
    """

    class _Marker(Exception):
        pass

    def raising_seam(argv):
        raise _Marker("boom")

    with pytest.raises(_Marker):
        handoff.handoff(["tmux", "attach", "-t", "x"], exec_seam=raising_seam)


# ---------------------------------------------------------------------------
# The remote path does not reach the listing transport.
# ---------------------------------------------------------------------------


def test_remote_argv_never_calls_the_listing_transports_run_camp(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("remote_argv must not call transport.run_camp")

    monkeypatch.setattr(transport, "run_camp", _fail)

    argv = handoff.remote_argv(_HOST, "some-ref")

    assert argv[0] == "ssh"
