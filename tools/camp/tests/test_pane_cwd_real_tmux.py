"""Real-tmux tests: every pane camp spawns lands in its requested directory
even when the tmux SERVER's own working directory has been deleted.

These tests pin that the pane enters its directory itself, without relying on
tmux's `-c`. They run through a PATH shim that strips `-c`; on tmux 3.4 a
pre-fix pane then lands in `$HOME` (the client falls back to home when its cwd
is unreadable), so the shim is what makes the tests bite. That the bug is
real on tmux 3.7c (which ignores `-c` once the server's cwd is gone) rests on
the manual reproduction in the task record, not on these tests. Only the
exact-argv unit tests in `test_launch_tmux.py` pin that `-c` is still passed.
Every test here starts a real server on a private `-L` socket from a temp
directory, deletes that directory, then calls the `Tmux` method under test.
Nothing here ever touches the default tmux socket.

`_REAL_TMUX` is resolved by absolute path at import time, before conftest's
autouse `_sandbox_tmux` fixture rewrites `PATH`; the PATH shim below redirects
production code's bare `tmux` calls onto the private socket.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_REAL_TMUX = shutil.which("tmux")
_LSOF = shutil.which("lsof")

pytestmark = [
    pytest.mark.real_home,
    pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)"),
    pytest.mark.skipif(_LSOF is None, reason="no lsof on PATH"),
]

_WAIT_SECONDS = 4.0
_PATHS = ["new_session", "new_session_with_window", "new_window", "new_window_empty_command"]
_AWKWARD_NAME = "we ird; $HOME \"q'x"


def _sock_run(
    sock: str, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_REAL_TMUX, "-L", sock, *args],
        capture_output=True,
        text=True,
        timeout=5,
        cwd=None if cwd is None else str(cwd),
        env=env,
    )


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    env.update(extra)
    return env


@pytest.fixture()
def stale_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real server on a private socket whose own cwd has been deleted, fronted
    by a `tmux` shim that drops `-c`: for `new-session`/`new-window` the shim
    drops the first `-c <dir>` and runs the real client from a deleted
    directory, so the ONLY thing that can put a pane in the requested
    directory is the pane command itself. On tmux 3.4 a pre-fix pane then
    lands in `$HOME` (the client falls back to home); deleting the server's
    own cwd has no effect there. The tmux 3.7c link rests on the manual
    reproduction in the task record.

    The server is started (by a `starter` session running `sleep`) from a temp
    dir that is then removed. Yields `(sock, start_server_with_env)`.
    """
    sock = f"camp_pane_cwd_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    bin_dir = tmp_path / "shim"
    bin_dir.mkdir()
    wrapper = bin_dir / "tmux"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, tempfile\n"
        "args = sys.argv[1:]\n"
        "if args[:1] in (['new-session'], ['new-window']) and '-c' in args:\n"
        "    i = args.index('-c')\n"
        "    del args[i:i + 2]\n"
        "    gone = tempfile.mkdtemp()\n"
        "    os.chdir(gone)\n"
        "    os.rmdir(gone)\n"
        f"os.execv({_REAL_TMUX!r}, [{_REAL_TMUX!r}, '-L', {sock!r}, *args])\n",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))
    monkeypatch.delenv("TMUX", raising=False)

    def start(env: dict[str, str] | None = None) -> None:
        starter_dir = tmp_path / "server-start-dir"
        starter_dir.mkdir()
        started = _sock_run(
            sock,
            "new-session", "-d", "-s", "starter", "-c", str(tmp_path), "sleep", "600",
            cwd=starter_dir,
            env=env or _clean_env(),
        )
        assert started.returncode == 0, started.stderr
        shutil.rmtree(starter_dir)

    try:
        yield sock, start
    finally:
        subprocess.run([_REAL_TMUX, "-L", sock, "kill-server"], capture_output=True, timeout=5)


def _cwd_of(pid: str) -> str:
    out = subprocess.run(
        [_LSOF, "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=5
    ).stdout
    names = [line[1:] for line in out.splitlines() if line.startswith("n")]
    return names[0] if names else ""


def _wait_until(probe, done, timeout: float = _WAIT_SECONDS):
    """Call *probe* until *done* accepts its answer or *timeout* passes; return
    the last answer."""
    deadline = time.monotonic() + timeout
    seen = probe()
    while not done(seen) and time.monotonic() < deadline:
        time.sleep(0.05)
        seen = probe()
    return seen


def _await_cwd(pid: str, expected: Path) -> str:
    return _wait_until(lambda: _cwd_of(pid), lambda seen: seen == str(expected))


def _pane_pid(sock: str, target: str) -> str:
    listed = _sock_run(sock, "list-panes", "-t", target, "-F", "#{pane_pid}")
    assert listed.returncode == 0, listed.stderr
    return listed.stdout.split()[0]


def _spawn(path: str, sock: str, session: str, cwd: Path) -> str:
    """Spawn one pane through camp's seam; return its pane pid."""
    from camp.launch.tmux import NewWindowResult, Tmux

    tmux = Tmux()
    if path == "new_session":
        done = tmux.new_session(session, cwd=cwd, timeout=5)
        assert done.returncode == 0, done.stderr
        return _pane_pid(sock, f"={session}:")
    if path == "new_session_with_window":
        answer = tmux.new_session_with_window(
            session, cwd=cwd, window_name="w", command=("sleep", "600"), timeout=5
        )
        assert isinstance(answer, NewWindowResult), answer
        return _pane_pid(sock, answer.window_id)
    command = ("sleep", "600") if path == "new_window" else ()
    answer = tmux.new_window_with_reason(
        "starter", cwd=cwd, window_name="w", command=command, timeout=5
    )
    assert isinstance(answer, NewWindowResult), answer
    return _pane_pid(sock, answer.window_id)


@pytest.mark.parametrize("path", _PATHS)
def test_pane_lands_in_the_requested_dir_when_the_server_cwd_is_deleted(
    stale_server, tmp_path: Path, path: str
) -> None:
    sock, start = stale_server
    start()
    want = tmp_path / "ws"
    want.mkdir()

    pid = _spawn(path, sock, "camp-t", want)

    assert _await_cwd(pid, want) == str(want)


@pytest.mark.parametrize("path", _PATHS)
def test_a_dir_with_shell_metacharacters_is_entered_exactly(
    stale_server, tmp_path: Path, path: str
) -> None:
    sock, start = stale_server
    start()
    want = tmp_path / _AWKWARD_NAME
    want.mkdir()

    pid = _spawn(path, sock, "camp-t", want)

    assert _await_cwd(pid, want) == str(want)


def test_respawn_first_pane_restarts_in_the_requested_dir_when_the_server_cwd_is_deleted(
    stale_server, tmp_path: Path
) -> None:
    from camp.launch.tmux import Tmux

    sock, start = stale_server
    start()
    want = tmp_path / "ws"
    want.mkdir()
    old_pid = _spawn("new_session", sock, "camp-t", want)
    assert _await_cwd(old_pid, want) == str(want)

    answer = Tmux().respawn_first_pane("camp-t", timeout=5)
    assert answer is not None and answer.returncode == 0, answer

    new_pid = _wait_until(lambda: _pane_pid(sock, "=camp-t:"), lambda pid: pid != old_pid)
    assert new_pid != old_pid, "respawn-pane -k must start a new process"
    assert _await_cwd(new_pid, want) == str(want)


def test_a_missing_dir_never_runs_the_command_and_leaves_no_pane(
    stale_server, tmp_path: Path
) -> None:
    """`new_window` is the spawn path whose tmux client does not itself start in
    *cwd*, so a missing directory reaches the pane command (the session-creating
    paths fail earlier, in `subprocess.run(cwd=...)`)."""
    from camp.launch.tmux import NewWindowResult, Tmux

    sock, start = stale_server
    start()
    missing = tmp_path / "does-not-exist"
    marker = tmp_path / "ran"
    command = ("sh", "-c", f"touch {marker}; sleep 600")

    answer = Tmux().new_window_with_reason(
        "starter", cwd=missing, window_name="doomed", command=command, timeout=5
    )

    assert isinstance(answer, NewWindowResult), answer
    names = _wait_until(
        lambda: _sock_run(sock, "list-windows", "-t", "=starter", "-F", "#{window_name}").stdout.split(),
        lambda names: "doomed" not in names,
        timeout=2.0,
    )
    assert "doomed" not in names, (answer, names)
    assert not marker.exists(), "the wrapped command must not run when the cd fails"


@pytest.mark.skipif(
    not (os.path.exists("/bin/sh") and os.path.exists("/bin/bash")), reason="needs /bin/sh and /bin/bash"
)
def test_empty_command_pane_runs_tmux_default_shell_not_the_starters_shell(
    stale_server, tmp_path: Path
) -> None:
    sock, start = stale_server
    start(_clean_env(SHELL="/bin/sh"))
    set_shell = _sock_run(sock, "set", "-g", "default-shell", "/bin/bash")
    assert set_shell.returncode == 0, set_shell.stderr
    want = tmp_path / "ws"
    want.mkdir()

    pid = _spawn("new_session", sock, "camp-t", want)

    comm = _wait_until(
        lambda: subprocess.run(
            ["ps", "-o", "comm=", "-p", pid], capture_output=True, text=True, timeout=5
        ).stdout.strip(),
        lambda comm: os.path.basename(comm) == "bash",
    )
    assert os.path.basename(comm) == "bash"


@pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="needs /bin/bash")
def test_empty_command_pane_is_a_login_shell(stale_server, tmp_path: Path) -> None:
    sock, start = stale_server
    start()
    set_shell = _sock_run(sock, "set", "-g", "default-shell", "/bin/bash")
    assert set_shell.returncode == 0, set_shell.stderr
    want = tmp_path / "ws"
    want.mkdir()
    marker = tmp_path / "login"

    pid = _spawn("new_session", sock, "camp-t", want)
    assert _await_cwd(pid, want) == str(want)
    sent = _sock_run(
        sock, "send-keys", "-t", "=camp-t:", f"shopt -q login_shell && touch '{marker}'", "Enter"
    )
    assert sent.returncode == 0, sent.stderr

    assert _wait_until(marker.exists, bool), "the empty-command pane's shell is not a login shell"
