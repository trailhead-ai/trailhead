"""Real-tmux proof — `camp new <slug> --no-attach -- <argv…>` starts the
argv as the workspace session's first pane, under the session's environment
and cwd, with every element arriving as one unmodified argument.

Drives the REAL CLI (`camp.cli.dispatch.main`) against a REAL tmux server on a
throwaway `-L` socket, reusing `test_one_door_real_tmux.py`'s server, config
and PATH-wrapper scaffolding: injected config and state dirs only, the live
camp install and the real tmux server are never touched. What each test asserts
on is a file the pane's own process wrote.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_TESTS_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import test_one_door_real_tmux as _door  # noqa: E402
from test_stop_cli import _run  # noqa: E402
from test_stop_workspace_real_tmux import _sock_run  # noqa: E402

_REAL_TMUX = _door._REAL_TMUX
pytestmark = _door.pytestmark
make_server = _door.make_server

_NEEDS_TMUX = pytest.mark.skipif(
    _REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)"
)


@pytest.fixture(autouse=True)
def _no_provisioner(monkeypatch: pytest.MonkeyPatch) -> None:
    import camp.provision.provision as provision

    monkeypatch.setattr(provision, "spawn_detached_provisioner", lambda **kw: None)


def _wait_for_file(path: Path, *, timeout: float = 10) -> bytes:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            time.sleep(0.1)
            return path.read_bytes()
        time.sleep(0.05)
    raise AssertionError(f"the pane never wrote {path}")


@_NEEDS_TMUX
def test_the_command_runs_under_the_groups_account_at_the_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_server
) -> None:
    account_dir = str(tmp_path / "acct-a")
    server = make_server(launch_block=f'[launch]\naccount = "{account_dir}"\n')
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "someone-elses-account"))
    out = tmp_path / "out.txt"

    code = _run(
        [
            "new", "ws1", "--no-attach", "--group", server.group_name, "--",
            "sh", "-c", f'echo "$CLAUDE_CONFIG_DIR $PWD" > {out}; sleep 30',
        ],
        monkeypatch,
    )

    assert code == 0
    seen_account, seen_pwd = _wait_for_file(out).decode().split()
    assert seen_account == account_dir, "the session's account, not the server's"
    assert Path(seen_pwd).resolve() == server.workspace_dir("ws1").resolve()


@_NEEDS_TMUX
@pytest.mark.parametrize(
    "element",
    ['has space', "it's", 'say "hi"', "$(touch PWNED)", "a;b", "`id`", "x\ny", "-n"],
    ids=["space", "apostrophe", "dquote", "cmdsubst", "semicolon", "backtick", "newline", "dash-n"],
)
def test_an_argument_with_shell_syntax_reaches_the_pane_as_one_unmodified_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_server, element: str
) -> None:
    server = make_server()
    out = tmp_path / "arg.bin"

    code = _run(
        [
            "new", "ws2", "--no-attach", "--group", server.group_name, "--",
            "sh", "-c", f'printf %s "$1" > {out}; sleep 30', "sh", element,
        ],
        monkeypatch,
    )

    assert code == 0
    assert _wait_for_file(out) == element.encode()
    assert not (server.workspace_dir("ws2") / "PWNED").exists()


@_NEEDS_TMUX
def test_the_option_looking_tokens_after_double_dash_all_reach_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_server, capsys
) -> None:
    server = make_server()
    out = tmp_path / "argv.txt"
    tokens = ["-g", "--host", "x", "--dry-run", "--json"]

    code = _run(
        [
            "new", "ws3", "--no-attach", "--group", server.group_name, "--",
            "sh", "-c", f'printf "%s\\n" "$@" > {out}; sleep 30', "sh", *tokens,
        ],
        monkeypatch,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert _wait_for_file(out).decode().splitlines() == tokens
    assert captured.out.strip() == str(server.workspace_dir("ws3")), "not switched to JSON"
    assert _sock_run(server.sock, "has-session", "-t", "=camp-onedoor-ws3").returncode == 0
