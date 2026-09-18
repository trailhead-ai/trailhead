"""EPHEMERAL assumption probe — delete after the tmux-seam task lands real coverage.

Resolves the unknown blocking task/the-tmux-seam-creates-a-window-and-reports-the-id-tmux-assigned:
does `tmux new-window -P -F '#{window_id}'` report the window id tmux assigned on
the SAME invocation that creates the window, so camp can create-and-read-back in
one call without a second `list-windows` round trip (and the race that would open
if it needed one)?

Drives a real, private tmux server (its own `-L` socket, torn down in a finally)
rather than the suite's `_sandbox_tmux` stub — the question is about real tmux
behaviour, which a stub cannot answer. Captures the real tmux binary's absolute
path at import time, before any test's PATH-prepending fixture runs, so this
probe is unaffected by `_sandbox_tmux`.
"""
from __future__ import annotations

import shutil
import subprocess
import uuid

import pytest

_REAL_TMUX = shutil.which("tmux")

pytestmark = pytest.mark.skipif(_REAL_TMUX is None, reason="no real tmux binary on this machine")


def _tmux(socket_name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_REAL_TMUX, "-L", socket_name, *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_new_window_reports_id_on_creating_invocation_bare() -> None:
    """The id `new-window -P -F` prints matches what `list-windows` reports after."""
    socket_name = f"camp-probe-{uuid.uuid4().hex[:8]}"
    try:
        started = _tmux(socket_name, "new-session", "-d", "-s", "probe", "-x", "80", "-y", "24")
        assert started.returncode == 0, started.stderr

        created = _tmux(socket_name, "new-window", "-P", "-F", "#{window_id}")
        assert created.returncode == 0, created.stderr
        reported_id = created.stdout.strip()
        assert reported_id.startswith("@")
        assert created.stdout == f"{reported_id}\n"  # exact shape: id + single trailing newline

        listed = _tmux(socket_name, "list-windows", "-F", "#{window_id}")
        assert listed.returncode == 0, listed.stderr
        actual_ids = listed.stdout.split()
        assert reported_id in actual_ids
    finally:
        _tmux(socket_name, "kill-server")


def test_new_window_reports_id_with_command_and_working_directory() -> None:
    """Same guarantee holds when the window runs a command with -c set, not just a bare window."""
    socket_name = f"camp-probe-{uuid.uuid4().hex[:8]}"
    try:
        started = _tmux(socket_name, "new-session", "-d", "-s", "probe", "-x", "80", "-y", "24")
        assert started.returncode == 0, started.stderr

        created = _tmux(
            socket_name,
            "new-window",
            "-P",
            "-F",
            "#{window_id}",
            "-c",
            "/tmp",
            "-n",
            "withcmd",
            "sleep",
            "30",
        )
        assert created.returncode == 0, created.stderr
        reported_id = created.stdout.strip()
        assert reported_id.startswith("@")

        listed = _tmux(socket_name, "list-windows", "-F", "#{window_id} #{window_name}")
        assert listed.returncode == 0, listed.stderr
        assert f"{reported_id} withcmd" in listed.stdout
    finally:
        _tmux(socket_name, "kill-server")
