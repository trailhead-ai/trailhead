"""Real-tmux end-to-end proof — a workspace is addressed only by its slug.

Drives a REAL tmux 3.7c server on a throwaway `-L` socket, the same
redirection trick `test_one_door_real_tmux.py` uses: `_REAL_TMUX` is
captured by absolute path at import time in the helper modules this file
imports from, before the autouse `_sandbox_tmux` fixture in `conftest.py`
rewrites `PATH` to a no-server stub for the rest of the suite, and a thin
`tmux` wrapper first on `PATH` transparently redirects every call camp's
OWN production code makes onto the isolated socket.

Everything is driven through the REAL CLI entry point
(`camp.cli.dispatch.main`, via `_run`/`_isolated_env`, borrowed verbatim
from `test_stop_cli.py`, exactly as `test_one_door_real_tmux.py` does).
The throwaway server, its on-disk group config, and the one thing faked
(`camp.provision.lifecycle.cmd_ls_group`, so no real git worktree is
needed for a member's `repo_root`) are `test_one_door_real_tmux.py`'s own
`make_server` fixture and `_wire_group_listing` helper, imported rather
than copied.

Each test asserts on the private server's `list-sessions` output before
and after the invocation, plus exit status and stderr — never on the
operator's own default tmux server.
"""

from __future__ import annotations

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

from test_stop_cli import _run  # noqa: E402
from test_stop_workspace_real_tmux import _sock_run  # noqa: E402
from test_one_door_real_tmux import _REAL_TMUX, _wire_group_listing  # noqa: E402

#: Pulls `test_one_door_real_tmux`'s `make_server` fixture (and every other
#: fixture it declares) into this module without importing it by name — an
#: ordinary import of an already-`@pytest.fixture`-decorated object collides
#: with a same-named test parameter and ruff flags it as a redefinition.
#: `make_server` stays declared once, in `test_one_door_real_tmux.py`,
#: never copied here.
pytest_plugins = ["test_one_door_real_tmux"]

pytestmark = pytest.mark.real_home  # this test intentionally talks to a real tmux server


# ---------------------------------------------------------------------------
# `camp sessions` and `camp kill <ref>` redirect and leave the socket
# untouched.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
@pytest.mark.parametrize(
    "argv,replacement",
    [(["sessions"], "list"), (["kill", "deadbeef"], "stop")],
    ids=["sessions", "kill"],
)
def test_retired_session_verb_redirects_and_leaves_the_socket_untouched(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    make_server,
    argv: list[str],
    replacement: str,
) -> None:
    server = make_server()
    _sock_run(server.sock, "new-session", "-d", "-s", "plainsess", "-x", "80", "-y", "24")
    before_sessions = _sock_run(server.sock, "list-sessions").stdout.splitlines()
    before_windows = _sock_run(server.sock, "list-windows", "-t", "plainsess").stdout.splitlines()

    code = _run(argv, monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert captured.err.strip() == (
        f"camp {argv[0]}: this command has been replaced — use 'camp {replacement}' instead."
    )
    assert captured.out == ""

    after_sessions = _sock_run(server.sock, "list-sessions").stdout.splitlines()
    after_windows = _sock_run(server.sock, "list-windows", "-t", "plainsess").stdout.splitlines()
    assert after_sessions == before_sessions
    assert after_windows == before_windows


# ---------------------------------------------------------------------------
# `camp attach` reads its argument as a slug in one resolved group, and
# nothing else — a ref-shaped argument refuses as an unknown slug.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_camp_attach_refuses_a_ref_shaped_argument_as_an_unknown_slug(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, make_server
) -> None:
    server = make_server()
    slug = "camp-cli"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    _wire_group_listing(monkeypatch, ws_dir=ws_dir, slug=slug)

    before_sessions = _sock_run(server.sock, "list-sessions")

    ref = "0123abcd"
    code = _run(["attach", ref, "--group", server.group_name], monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert captured.err.strip() == (
        f"camp attach: no workspace named {ref} in group {server.group_name} — "
        "'camp list' shows its workspaces"
    )
    assert captured.out == ""

    after_sessions = _sock_run(server.sock, "list-sessions")
    # No server is even running yet — a session refers to a workspace that
    # was never created.
    assert after_sessions.returncode == before_sessions.returncode
    assert after_sessions.stdout == before_sessions.stdout


# ---------------------------------------------------------------------------
# `camp attach <slug>` for a real workspace creates its session; `camp stop
# <slug>` then removes it.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_camp_attach_creates_the_workspace_session_and_camp_stop_removes_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, make_server
) -> None:
    from camp.launch.naming import workspace_session_name

    server = make_server()
    slug = "camp-cli"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    _wire_group_listing(monkeypatch, ws_dir=ws_dir, slug=slug)

    session = workspace_session_name(server.group_name, slug)

    before_sessions = _sock_run(server.sock, "list-sessions")
    assert before_sessions.returncode != 0 or session not in before_sessions.stdout

    code = _run(["attach", slug, "--group", server.group_name], monkeypatch)
    captured = capsys.readouterr()
    assert code == 0, captured.err

    after_attach = _sock_run(server.sock, "list-sessions", "-F", "#{session_name}")
    assert after_attach.returncode == 0, after_attach.stderr
    assert session in after_attach.stdout.splitlines()

    code = _run(["stop", slug, "--group", server.group_name], monkeypatch)
    captured = capsys.readouterr()
    assert code == 0, captured.err

    after_stop = _sock_run(server.sock, "list-sessions", "-F", "#{session_name}")
    assert session not in after_stop.stdout.splitlines()
