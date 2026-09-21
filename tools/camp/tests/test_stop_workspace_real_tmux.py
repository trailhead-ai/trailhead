"""Real-tmux test for `stop_workspace` (task/stopping-a-workspace-reconciles-
previews-kills-and-checks).

Drives a REAL tmux 3.7c server on a throwaway `-L` socket, the same
redirection trick `test_window_binding_end_to_end.py` uses: `_REAL_TMUX` is
captured by absolute path at import time, before the autouse `_sandbox_tmux`
fixture in `conftest.py` rewrites `PATH` to a no-server stub for the rest of
the suite, and a thin `tmux` wrapper on `PATH` transparently redirects every
call `camp.launch.tmux.Tmux` makes onto the isolated socket.

No pty is needed here (unlike the key-binding end-to-end suite) — nothing
under test reads keystrokes; the two windows are created directly through
tmux's own CLI before `stop_workspace` (production code, unmodified) is
called against them.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# Captured NOW, before conftest's _sandbox_tmux fixture prepends a stub to
# PATH for every other test in the suite.
_REAL_TMUX = shutil.which("tmux")


def _sock_run(sock: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_REAL_TMUX, "-L", sock, *args], capture_output=True, text=True, timeout=5
    )


@pytest.fixture()
def real_tmux_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A throwaway real tmux server, reached by every `tmux` call this
    process's production code makes (`Tmux._run(["tmux", ...])` resolves the
    bare name against `PATH`) — a wrapper first on `PATH` redirects it onto
    an isolated `-L` socket, exactly like `test_window_binding_end_to_end.py`.
    """
    sock = f"camp_stop_e2e_{os.getpid()}_{id(object())}"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "tmux"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        f"os.execv({_REAL_TMUX!r}, [{_REAL_TMUX!r}, '-L', {sock!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))
    try:
        yield sock
    finally:
        subprocess.run(
            [_REAL_TMUX, "-L", sock, "kill-server"], capture_output=True, timeout=5
        )


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_stop_workspace_kills_a_real_session_and_previews_the_sleep_window(
    real_tmux_socket: str, tmp_path: Path
) -> None:
    from camp.launch.naming import workspace_session_name
    from camp.launch.stop_workspace import Stopped, stop_workspace
    from camp.launch.tmux import Tmux

    sock = real_tmux_socket
    session = workspace_session_name("g", "slug")

    # Two windows: the session's own first window (an idle shell), and a
    # second running `sleep` — the process the preview must name as
    # foreground.
    created = _sock_run(sock, "new-session", "-d", "-s", session, "-n", "shell")
    assert created.returncode == 0, created.stderr
    opened = _sock_run(sock, "new-window", "-t", session, "-n", "work", "sleep", "100000")
    assert opened.returncode == 0, opened.stderr

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    lines: list[str] = []
    outcome = stop_workspace(
        "g",
        "slug",
        ws_dir,
        tmux=Tmux(),
        poll_timeout=5.0,
        poll_interval=0.05,
        emit=lines.append,
    )

    assert isinstance(outcome, Stopped)
    assert outcome.tmux_session == session

    foreground = [row for row in outcome.preview.windows if row.kind == "foreground"]
    assert len(foreground) == 1
    assert foreground[0].command == "sleep"

    after = _sock_run(sock, "has-session", "-t", f"={session}")
    assert after.returncode != 0
