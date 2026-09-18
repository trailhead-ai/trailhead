"""EPHEMERAL assumption probe — NOT permanent coverage.

Resolves the unknown blocking the task that binds the operator's ordinary
window-creation key to a camp-composed window: can a server-global tmux key
binding dispatch on whether the *current* session
is a camp workspace (via a session-local option camp writes), while every
other session on that same server keeps tmux's real default behaviour for
that key?

Drives a REAL tmux 3.7c server on a throwaway `-L` socket — no fakes, no
stubs. Fires the key through a genuine attached pty client (`pty.fork` +
`attach-session`), not `send-keys` (which writes straight into the pane's
tty and never passes through tmux's own key-binding dispatch — confirmed
empirically while building this probe: `send-keys` produced zero new
windows in every session, camp-marked or not).

Resolves the tmux binary ABSOLUTELY, and does so at IMPORT TIME — before the
autouse `_sandbox_tmux` fixture in tools/camp/tests/conftest.py rewrites
`PATH` to a no-server stub for the rest of the suite. Spawning by bare name
after that rewrite reproduces
lesson/a-test-that-spawns-a-program-by-bare-name-resolves-it-against-the-path-it-hands-the-child.

DELETE this whole file once task/the-binding-... lands with its own
behavioural tests covering the same ground (see that task's Test contract).
"""

from __future__ import annotations

import os
import pty
import shutil
import signal
import subprocess
import time

import pytest

# Captured NOW, before conftest's _sandbox_tmux fixture prepends a stub to
# PATH for every other test in the suite.
_REAL_TMUX = shutil.which("tmux")

pytestmark = pytest.mark.real_home  # this probe intentionally talks to a real tmux server


def _run(sock: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_REAL_TMUX, "-L", sock, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )


def _attach_and_send(sock: str, session: str, keys: bytes) -> None:
    """Fire real keystrokes at `session` through a genuine attached client.

    Uses a pty-backed `tmux attach-session`, exactly as an operator's real
    terminal would, so the bytes pass through tmux's own prefix/key-table
    dispatch rather than being written directly into the pane's tty.
    """
    pid, fd = pty.fork()
    if pid == 0:
        os.execv(_REAL_TMUX, [_REAL_TMUX, "-L", sock, "attach-session", "-t", session])
    try:
        time.sleep(0.4)
        os.write(fd, keys)
        time.sleep(0.4)
    finally:
        os.kill(pid, signal.SIGTERM)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_global_binding_dispatches_on_session_mark_and_falls_back_to_default(tmp_path) -> None:
    sock = f"camp_probe_{os.getpid()}"
    dispatch_log = tmp_path / "dispatch.log"

    try:
        # Three sessions on ONE server: the camp-marked one, an ordinary one,
        # and a forgery — named exactly like a camp session but never marked.
        _run(sock, "new-session", "-d", "-s", "campsess", "-x", "80", "-y", "24")
        _run(sock, "new-session", "-d", "-s", "plainsess", "-x", "80", "-y", "24")
        _run(sock, "new-session", "-d", "-s", "campsess-forged", "-x", "80", "-y", "24")

        # The mark: a session-LOCAL option, set only on the real camp session.
        _run(sock, "set-option", "-t", "campsess", "@camp_workspace", "1")

        # A server-global binding, bound to a stable dispatcher (a fixed
        # run-shell command string) rather than to any camp verb's argv
        # shape. Its else-branch is tmux's own real default for prefix+c
        # (`new-window`) — there is no "fall through to the previous
        # binding" primitive in tmux; the default has to be reproduced
        # explicitly, which is itself part of what this probe establishes.
        _run(
            sock,
            "bind-key",
            "-T",
            "prefix",
            "c",
            "if-shell",
            "-F",
            "#{@camp_workspace}",
            f'run-shell "echo DISPATCHED:#{{session_name}} >> {dispatch_log}"',
            "new-window",
        )
        first_install = _run(sock, "list-keys", "-T", "prefix", "c").stdout

        # Idempotence: installing the identical binding again must not
        # accumulate or duplicate the entry.
        _run(
            sock,
            "bind-key",
            "-T",
            "prefix",
            "c",
            "if-shell",
            "-F",
            "#{@camp_workspace}",
            f'run-shell "echo DISPATCHED:#{{session_name}} >> {dispatch_log}"',
            "new-window",
        )
        second_install = _run(sock, "list-keys", "-T", "prefix", "c").stdout
        assert first_install == second_install
        c_entries = [
            line
            for line in _run(sock, "list-keys", "-T", "prefix").stdout.splitlines()
            if " prefix c " in line
        ]
        assert len(c_entries) == 1

        # Fire the real key (C-b then c) through a genuine attached client
        # in each session.
        _attach_and_send(sock, "campsess", b"\x02c")
        _attach_and_send(sock, "plainsess", b"\x02c")
        _attach_and_send(sock, "campsess-forged", b"\x02c")

        camp_windows = _run(sock, "list-windows", "-t", "campsess").stdout.splitlines()
        plain_windows = _run(sock, "list-windows", "-t", "plainsess").stdout.splitlines()
        forged_windows = _run(sock, "list-windows", "-t", "campsess-forged").stdout.splitlines()

        # Point 2 (negative case): a session with NO mark behaves exactly
        # like tmux's own unbound default — prefix+c opened a real new
        # window (this is what a totally unbound key would also do, since
        # the else-branch IS the compiled-in default command).
        assert len(plain_windows) == 2, plain_windows

        # Point 3 (forgery case): a session named like a camp session but
        # never marked is treated as not-camp — same default fallback, not
        # camp's dispatch — proving the branch reads the mark, not the name.
        assert len(forged_windows) == 2, forged_windows

        # Point 1 (positive case): the marked session did NOT get a bare
        # new-window; instead the stable dispatcher ran.
        assert len(camp_windows) == 1, camp_windows
        assert dispatch_log.exists()
        assert dispatch_log.read_text().strip() == "DISPATCHED:campsess"

        # Point 4: the option is genuinely session-scoped — reading it from
        # a different session's context does not see campsess's value.
        plain_option = subprocess.run(
            [_REAL_TMUX, "-L", sock, "show-options", "-t", "plainsess", "@camp_workspace"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert plain_option.returncode != 0
        assert plain_option.stdout.strip() == ""

        camp_option = _run(sock, "show-options", "-t", "campsess", "@camp_workspace")
        assert camp_option.stdout.strip() == "@camp_workspace 1"
    finally:
        subprocess.run(
            [_REAL_TMUX, "-L", sock, "kill-server"],
            capture_output=True,
            timeout=5,
        )

