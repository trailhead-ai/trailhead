"""Real-tmux tests: a tmux server that camp starts runs from the user's home
directory, never from a workspace directory.

On tmux 3.7c a deleted server cwd overrides `-c` for every later pane, so the
server's cwd must outlive every workspace. These tests start a real server on a
private `-L` socket through camp's two server-starting seams and read the
server process's cwd with `lsof`. Nothing here touches the default tmux socket.

`_REAL_TMUX` is resolved by absolute path at import time, before conftest's
autouse `_sandbox_tmux` fixture rewrites `PATH`; the PATH shim from
`test_stop_workspace_real_tmux.redirected_tmux_socket` redirects production
code's bare `tmux` calls onto the private socket.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_TESTS_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_pane_cwd_real_tmux import _LSOF, _cwd_of, _wait_until  # noqa: E402
from test_stop_workspace_real_tmux import (  # noqa: E402
    _REAL_TMUX,
    _sock_run,
    redirected_tmux_socket,
)

pytestmark = [
    pytest.mark.real_home,
    pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)"),
    pytest.mark.skipif(_LSOF is None, reason="no lsof on PATH"),
]

_PATHS = ["new_session", "new_session_with_window"]


def _tmux_version() -> tuple[int, int]:
    if _REAL_TMUX is None:
        return (0, 0)
    out = subprocess.run([_REAL_TMUX, "-V"], capture_output=True, text=True, timeout=5).stdout
    found = re.search(r"(\d+)\.(\d+)", out)
    return (int(found.group(1)), int(found.group(2))) if found else (0, 0)


@pytest.fixture()
def private_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every bare `tmux` call pinned to a private socket — see
    `redirected_tmux_socket`. Yields the socket name."""
    monkeypatch.delenv("TMUX", raising=False)
    yield from redirected_tmux_socket(tmp_path, monkeypatch, "camp_server_cwd")


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "HOME")}
    env.update(extra)
    return env


def _server_cwd(sock: str) -> str:
    shown = _sock_run(sock, "display-message", "-p", "#{pid}")
    assert shown.returncode == 0, shown.stderr
    return _cwd_of(shown.stdout.strip())


def _start(path: str, session: str, cwd: Path, env: dict[str, str]) -> None:
    """Start the server through one of camp's two server-starting seams."""
    from camp.launch.tmux import NewWindowResult, Tmux

    tmux = Tmux()
    if path == "new_session":
        done = tmux.new_session(session, cwd=cwd, env=env, timeout=5)
        assert done.returncode == 0, done.stderr
        return
    answer = tmux.new_session_with_window(
        session, cwd=cwd, window_name="w", command=("sleep", "600"), env=env, timeout=5
    )
    assert isinstance(answer, NewWindowResult), answer


@pytest.mark.parametrize("path", _PATHS)
def test_server_cwd_is_the_injected_home_not_the_workspace(
    private_server, tmp_path: Path, path: str
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    _start(path, "camp-t", ws, _env(HOME=str(home)))

    assert _server_cwd(private_server) == str(home)


@pytest.mark.parametrize("path", _PATHS)
def test_server_cwd_follows_the_injected_home(private_server, tmp_path: Path, path: str) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    seen = []
    for label in ("home-a", "home-b"):
        home = tmp_path / label
        home.mkdir()
        _start(path, "camp-t", ws, _env(HOME=str(home)))
        seen.append(_server_cwd(private_server))
        _sock_run(private_server, "kill-server")
        _wait_until(
            lambda: _sock_run(private_server, "list-sessions").returncode, lambda rc: rc != 0
        )

    assert seen == [str(tmp_path / "home-a"), str(tmp_path / "home-b")]


def test_new_session_with_window_reads_os_environ_home_when_env_is_none(
    private_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from camp.launch.tmux import NewWindowResult, Tmux

    ws = tmp_path / "ws"
    ws.mkdir()
    home = tmp_path / "environ-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    answer = Tmux().new_session_with_window(
        "camp-t", cwd=ws, window_name="w", command=("sleep", "600"), timeout=5
    )

    assert isinstance(answer, NewWindowResult), answer
    assert _server_cwd(private_server) == str(home)


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("home_state", ["unset", "empty", "missing"])
def test_unusable_home_falls_back_to_root(
    private_server, tmp_path: Path, path: str, home_state: str
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    env = {
        "unset": _env(),
        "empty": _env(HOME=""),
        "missing": _env(HOME=str(tmp_path / "no-such-home")),
    }[home_state]

    _start(path, "camp-t", ws, env)

    assert _server_cwd(private_server) == "/"


@pytest.mark.skipif(
    _tmux_version() < (3, 7),
    reason="tmux < 3.7 honours -c on a server whose cwd is deleted, so this cannot bite",
)
@pytest.mark.parametrize("path", _PATHS)
def test_hand_opened_window_lands_in_the_session_dir_after_a_workspace_is_deleted(
    private_server, tmp_path: Path, path: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ws_a = tmp_path / "ws-a"
    ws_a.mkdir()
    ws_b = tmp_path / "ws-b"
    ws_b.mkdir()
    env = _env(HOME=str(home))
    _start(path, "camp-a", ws_a, env)
    _start(path, "camp-b", ws_b, env)
    shutil.rmtree(ws_a)

    opened = _sock_run(private_server, "new-window", "-d", "-t", "=camp-b:", "-P", "-F", "#{pane_pid}")
    assert opened.returncode == 0, opened.stderr
    pid = opened.stdout.strip()

    assert _wait_until(lambda: _cwd_of(pid), lambda seen: seen == str(ws_b)) == str(ws_b)
