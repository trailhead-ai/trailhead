"""Real-tmux test for `Tmux.new_session_with_window`
(task/the-seam-creates-a-session-with-a-named-first-window-and-reads-back-its-id).

Drives a REAL tmux 3.7c server on a throwaway `-L` socket — no fakes, no
stubs standing in for tmux itself. `_REAL_TMUX` is resolved by ABSOLUTE PATH
at import time, before the autouse `_sandbox_tmux` fixture in `conftest.py`
rewrites `PATH` to a no-server stub for the rest of the suite — spawning by
bare name after that rewrite would resolve against the stub instead, per
`lesson/a-test-that-spawns-a-program-by-bare-name-resolves-it-against-the-path-it-hands-the-child`.
Pattern copied from `test_stop_workspace_real_tmux.py`: a thin `tmux` wrapper first on `PATH`
transparently redirects every call camp's OWN production code makes onto the
isolated socket, and the server is killed in teardown.
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

pytestmark = pytest.mark.real_home  # this test intentionally talks to a real tmux server


def _sock_run(sock: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_REAL_TMUX, "-L", sock, *args], capture_output=True, text=True, timeout=5
    )


@pytest.fixture()
def real_tmux_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A throwaway real tmux server, reached by every `tmux` call this
    process's production code makes (`Tmux._run`/`subprocess.run(["tmux",
    ...])` resolves the bare name against `PATH`) — a wrapper first on
    `PATH` redirects it onto an isolated `-L` socket, exactly like
    `test_stop_workspace_real_tmux.py`. Kills the server on the way out.
    """
    sock = f"camp_new_session_e2e_{os.getpid()}_{id(object())}"
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
def test_new_session_with_window_creates_and_reads_back_a_real_first_window(
    real_tmux_socket: str, tmp_path: Path
) -> None:
    from camp.launch.tmux import NewWindowResult, Tmux

    sock = real_tmux_socket
    session = f"resurrect-{os.getpid()}"
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    result = Tmux().new_session_with_window(
        session, cwd=ws_dir, window_name="restored", command=()
    )

    assert isinstance(result, NewWindowResult)

    listed = _sock_run(
        sock, "list-windows", "-t", f"={session}", "-F", "#{window_id}\t#{window_name}\t#{pane_current_path}"
    )
    assert listed.returncode == 0, listed.stderr
    rows = [line.split("\t") for line in listed.stdout.splitlines()]
    assert len(rows) == 1, "the created session must hold exactly one window"
    window_id, window_name, current_path = rows[0]

    assert window_name == "restored"
    assert current_path == str(ws_dir.resolve())
    assert window_id == result.window_id, (
        "the id read back on the creating call must equal list-windows' id "
        "for the same window"
    )


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_new_session_with_window_a_second_call_against_the_same_name_answers_duplicate(
    real_tmux_socket: str, tmp_path: Path
) -> None:
    from camp.launch.tmux import DUPLICATE, Tmux

    sock = real_tmux_socket
    session = f"resurrect-dup-{os.getpid()}"
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux = Tmux()
    first = tmux.new_session_with_window(session, cwd=ws_dir, window_name="one", command=())
    assert first is not DUPLICATE

    second = tmux.new_session_with_window(session, cwd=ws_dir, window_name="two", command=())

    assert second is DUPLICATE

    listed = _sock_run(sock, "list-windows", "-t", f"={session}")
    assert listed.returncode == 0, listed.stderr
    assert len(listed.stdout.splitlines()) == 1, (
        "a duplicate create must leave the original session holding "
        "exactly the one window it started with"
    )
