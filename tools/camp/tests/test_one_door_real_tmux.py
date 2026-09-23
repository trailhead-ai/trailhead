"""Real-tmux end-to-end proof — `camp attach` brings up a workspace
session whose every pane starts on the group's account, `camp launch` is a
dead redirect, and a group's stale `[launch] roots` key is tolerated with a
notice on every `camp` invocation that reads it.

Drives a REAL tmux 3.7c server on a throwaway `-L` socket, the same
redirection trick `test_stop_workspace_real_tmux.py` uses: `_REAL_TMUX` is
captured by absolute path at import time, before the autouse `_sandbox_tmux`
fixture in `conftest.py` rewrites `PATH` to a no-server stub for the rest of
the suite, and a thin `tmux` wrapper first on `PATH` transparently redirects
every call camp's OWN production code makes onto the isolated socket.

`camp attach` is driven through the REAL CLI entry point
(`camp.cli.dispatch.main`, via `_run`/`_isolated_env` borrowed from
`test_stop_cli.py`, exactly as `test_resurrect_real_tmux.py` and
`test_stop_cli_real_tmux.py` do). Two things are faked, both borrowed
verbatim from `test_resurrect_real_tmux.py`'s own real-tmux suite, never
reinvented: `camp.provision.lifecycle.cmd_ls_group` (so the test needs no
real git worktree on disk for a member's `repo_root`) and, only for the
transfer-rooting test, the trailhead harness seam
(`camp.launch.profile.harness_for`) — the resurrection planner's own
boundary, faked there for the same reason `test_resurrect_real_tmux.py`
fakes it: only the harness knows a transcript path and a resume argv, and
nothing else about tmux is stood in for.

The account tests deliberately do NOT fake the harness. A group's
`[launch] account` is bound by the REAL `camp.launch.profile.harness_for`
resolving the REAL `ClaudeCodeHarness` (the group's `[harness]` block is
omitted, so `resolve_harness_profile` defaults `binary` to `"claude"`,
which `trailhead.harness.get_harness` resolves unconditionally — a pure
registry lookup, no filesystem check). A tiny `claude` stub on `PATH`
answers `ClaudeCodeHarness.session_enumerate`'s `claude agents --json …`
call with an empty list, since `camp attach` builds its addressable pool
before it reaches the door. What each test asserts on is the pane's OWN
environment, dumped by the pane itself — never what camp reports it set.
"""

from __future__ import annotations

import importlib
import os
import shutil
import stat
import subprocess
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

# Captured NOW, before conftest's _sandbox_tmux fixture prepends a stub to
# PATH for every other test in the suite.
_REAL_TMUX = shutil.which("tmux")

from test_stop_cli import _run  # noqa: E402
from test_stop_workspace_real_tmux import _sock_run  # noqa: E402
from test_resurrect_real_tmux import (  # noqa: E402
    _FakeHarness,
    _capture_pane,
    _list_windows,
    _wait_for_output,
    _wait_for_shell,
    _wire_workspace,
)

pytestmark = pytest.mark.real_home  # this test intentionally talks to a real tmux server


_CLAUDE_STUB = (
    "#!/usr/bin/env python3\n"
    "import sys, time\n"
    "if 'agents' in sys.argv:\n"
    "    print('[]')\n"
    "else:\n"
    "    time.sleep(30)\n"
)


class _OneDoorServer:
    """One throwaway tmux server plus a real, on-disk group config, so
    `camp attach` resolves `[launch] account` from a real file, exactly as
    it would outside a test. `launch_block` is the raw `[launch] …` toml
    text to append (empty string for none)."""

    def __init__(self, tmp_path: Path, *, launch_block: str = "", group_name: str = "onedoor") -> None:
        self.sock = f"camp_one_door_e2e_{os.getpid()}_{id(self)}"
        self.group_name = group_name

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        wrapper = bin_dir / "tmux"
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            f"os.execv({_REAL_TMUX!r}, [{_REAL_TMUX!r}, '-L', {self.sock!r}, *sys.argv[1:]])\n",
            encoding="utf-8",
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)

        claude_stub = bin_dir / "claude"
        claude_stub.write_text(_CLAUDE_STUB, encoding="utf-8")
        claude_stub.chmod(claude_stub.stat().st_mode | stat.S_IEXEC)

        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"
        groups_dir = config_dir / "groups"
        groups_dir.mkdir(parents=True)
        # No [harness] block: resolve_harness_profile defaults binary to
        # "claude", which trailhead.harness.get_harness resolves to the REAL
        # ClaudeCodeHarness unconditionally (a pure registry lookup).
        (groups_dir / f"{self.group_name}.toml").write_text(
            f'[group]\nname = "{self.group_name}"\n\n'
            '[[members]]\nname = "m"\nrepo_root = "/tmp/camp-one-door-fake-repo"\n\n'
            f"{launch_block}",
            encoding="utf-8",
        )

        venv_bin = Path(sys.executable).resolve().parent

        self.env = {
            "HOME": str(home),
            "PATH": os.pathsep.join([str(bin_dir), str(venv_bin), "/usr/bin", "/bin"]),
            "CAMP_CONFIG_DIR": str(config_dir),
            "CAMP_STATE_DIR": str(state_dir),
        }

    def workspace_dir(self, slug: str) -> Path:
        from camp.group.manifest import workspace_dir

        return workspace_dir(self.group_name, slug, env=self.env)

    def kill(self) -> None:
        subprocess.run([_REAL_TMUX, "-L", self.sock, "kill-server"], capture_output=True, timeout=5)


@pytest.fixture
def make_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a `_OneDoorServer`, export its environment, and kill every server
    it built at teardown."""
    servers: list[_OneDoorServer] = []

    def _make(**kw) -> _OneDoorServer:
        srv = _OneDoorServer(tmp_path, **kw)
        for key, value in srv.env.items():
            monkeypatch.setenv(key, value)
        servers.append(srv)
        return srv

    yield _make
    for srv in servers:
        srv.kill()


def _wire_group_listing(monkeypatch: pytest.MonkeyPatch, *, ws_dir: Path, slug: str) -> None:
    """The one thing faked for the account tests: `cmd_ls_group`, so
    `camp attach`'s workspace listing needs no real git worktree on disk for
    a member's `repo_root` — borrowed verbatim from
    `test_resurrect_real_tmux.py`'s own `_wire_workspace`, minus the harness
    fake (the account tests want the REAL harness resolved)."""
    lifecycle = importlib.import_module("camp.provision.lifecycle")

    def fake_cmd_ls_group(group, *, env=None, tmux=None, **_kw):
        return lifecycle.GroupListing(
            entries=[
                {
                    "slug": slug,
                    "workspace_path": str(ws_dir),
                    "state": None,
                    "window_count": None,
                }
            ],
            unmanaged=[],
            unmanaged_count=0,
            notice=None,
        )

    monkeypatch.setattr(lifecycle, "cmd_ls_group", fake_cmd_ls_group)


# ---------------------------------------------------------------------------
# The session carries the account: every pane it starts — its first one, and
# one the operator opens by hand — lands on the group's declared account with
# the harness's scrub applied, even on a tmux server whose own global
# environment carries a parent session's markers and a different account.
# ---------------------------------------------------------------------------


def _pane_environment(tmp_path: Path, run) -> dict[str, str]:
    """Have a pane dump its environment to a file via *run(path)*, wait for
    it, and parse it — the pane's own view, not anything camp reports."""
    dump = tmp_path / f"env-{len(list(tmp_path.glob('env-*')))}.txt"
    run(dump)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if dump.exists() and dump.read_text(encoding="utf-8").endswith("\n"):
            break
        time.sleep(0.05)
    lines = dump.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


def _first_pane_environment(tmp_path: Path, sock: str, session: str) -> dict[str, str]:
    first = _list_windows(sock, session)[0][0]
    _wait_for_shell(sock, first, frozenset({"sh", "bash", "zsh", "fish", "dash"}))
    return _pane_environment(
        tmp_path,
        lambda dump: _sock_run(sock, "send-keys", "-t", first, f"env > {dump}", "Enter"),
    )


def _hand_opened_window_environment(tmp_path: Path, sock: str, session: str) -> dict[str, str]:
    return _pane_environment(
        tmp_path,
        lambda dump: _sock_run(sock, "new-window", "-t", f"={session}", f"env > {dump}; sleep 30"),
    )


def _attach_on_a_server_carrying_a_parent_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_server, *, launch_block: str
):
    """Start the workspace session through the real `camp attach` from a
    process environment that carries a parent Claude session's marker and a
    different account — exactly what a tmux server started from inside an
    agent session inherits as its global environment."""
    from camp.launch.naming import workspace_session_name

    server = make_server(launch_block=launch_block)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "someone-elses-account"))
    slug = "camp-cli"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    _wire_group_listing(monkeypatch, ws_dir=ws_dir, slug=slug)

    assert _run(["attach", slug, "--group", server.group_name], monkeypatch) == 0

    session = workspace_session_name(server.group_name, slug)
    global_env = _sock_run(server.sock, "show-environment", "-g").stdout
    assert "CLAUDECODE=1" in global_env, "the server itself must carry the parent marker"
    return server, session


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
@pytest.mark.parametrize("pane", ["first", "hand-opened"])
def test_a_declared_accounts_session_starts_every_pane_on_that_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_server, pane: str
) -> None:
    account_dir = str(tmp_path / "acct-a")
    server, session = _attach_on_a_server_carrying_a_parent_session(
        tmp_path, monkeypatch, make_server, launch_block=f'[launch]\naccount = "{account_dir}"\n'
    )

    read = _first_pane_environment if pane == "first" else _hand_opened_window_environment
    env = read(tmp_path, server.sock, session)

    assert env.get("CLAUDE_CONFIG_DIR") == account_dir
    assert "CLAUDECODE" not in env


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
@pytest.mark.parametrize("pane", ["first", "hand-opened"])
def test_a_group_declaring_no_account_starts_every_pane_on_the_default_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_server, pane: str
) -> None:
    server, session = _attach_on_a_server_carrying_a_parent_session(
        tmp_path, monkeypatch, make_server, launch_block=""
    )

    read = _first_pane_environment if pane == "first" else _hand_opened_window_environment
    env = read(tmp_path, server.sock, session)

    assert "CLAUDE_CONFIG_DIR" not in env, "the inherited account must not leak into the pane"
    assert "CLAUDECODE" not in env


# ---------------------------------------------------------------------------
# `camp launch` is a dead redirect that never touches tmux.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_camp_launch_redirects_and_leaves_the_socket_untouched(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, make_server
) -> None:
    server = make_server()
    _sock_run(server.sock, "new-session", "-d", "-s", "plainsess", "-x", "80", "-y", "24")
    before_sessions = _sock_run(server.sock, "list-sessions").stdout.splitlines()
    before_windows = _sock_run(server.sock, "list-windows", "-t", "plainsess").stdout.splitlines()

    code = _run(["launch", "camp-cli"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert (
        captured.err.strip()
        == "camp launch: this command has been replaced — use 'camp attach' instead."
    )
    assert captured.out == ""

    after_sessions = _sock_run(server.sock, "list-sessions").stdout.splitlines()
    after_windows = _sock_run(server.sock, "list-windows", "-t", "plainsess").stdout.splitlines()
    assert after_sessions == before_sessions
    assert after_windows == before_windows


# ---------------------------------------------------------------------------
# A stale `[launch] roots` key is tolerated, with one notice per `camp`
# invocation.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_roots_prints_the_notice_exactly_once_on_every_separate_camp_invocation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, make_server
) -> None:
    """What an operator sees: a group config carrying the stale `roots` key
    gets the notice on `camp attach`, every time — not once ever. The
    dedup in `camp.group.config._ROOTS_NOTICE_EMITTED` is scoped to a
    single process's lifetime (one `camp doctor` run touching many group
    configs prints each config's notice only once), never across separate
    `camp` invocations — a real second `camp attach` is a fresh process
    with an empty dedup set. `_run` here drives both attaches in this same
    pytest process, so the dedup set is cleared between them to model that
    fresh-process reality rather than the coincidence of sharing one."""
    from camp.group.config import _ROOTS_NOTICE_EMITTED

    server = make_server(launch_block='[launch]\nroots = ["~/code"]\n')
    slug = "camp-cli"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    _wire_group_listing(monkeypatch, ws_dir=ws_dir, slug=slug)

    notice = "is no longer used and grants nothing — remove it"

    code = _run(["attach", slug, "--group", server.group_name], monkeypatch)
    first = capsys.readouterr()
    assert code == 0, first.err
    assert first.err.count(notice) == 1, first.err

    _ROOTS_NOTICE_EMITTED.clear()

    code2 = _run(["attach", slug, "--group", server.group_name], monkeypatch)
    second = capsys.readouterr()
    assert code2 == 0, second.err
    assert second.err.count(notice) == 1, second.err


# ---------------------------------------------------------------------------
# A transferred conversation's member subpath roots the resurrected
# window there, varied across two members.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_a_transferred_conversations_member_subpath_roots_the_resurrected_window_there(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, make_server
) -> None:
    """`camp transfer` needs a second host to set up, so this seeds the
    received record the way `test_transfer_receive.py` does — writing a
    `WindowEntry` whose `cwd` is a member subpath directly via the real
    `write_window_record`, rather than running an actual two-host transfer
    — then reaches it through the door (`camp attach`), exactly the way an
    operator would after the receiving side's provisioner ran. Varied
    across TWO members (not member-vs-root) so the resurrected windows'
    directories cannot trivially agree by both being the workspace root.
    """
    from camp.group.window_record import WindowEntry, read_window_record, write_window_record, window_record_path_for
    from camp.launch.naming import workspace_session_name

    server = make_server()
    slug = "camp-cli"
    group_name = server.group_name
    session = workspace_session_name(group_name, slug)
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    (ws_dir / "member-a").mkdir()
    (ws_dir / "member-b").mkdir()

    conv_a = "8f2c1a3e-aaaa-bbbb-cccc-111122223333"
    conv_b = "41aa7c02-aaaa-bbbb-cccc-222233334444"
    write_window_record(
        window_record_path_for(ws_dir),
        [
            WindowEntry(window_id="@300", name="member-a", cwd="member-a", conversation_id=conv_a),
            WindowEntry(window_id="@301", name="member-b", cwd="member-b", conversation_id=conv_b),
        ],
    )

    harness = _FakeHarness(
        transcripts={
            conv_a: Path("/fake/transcripts") / f"{conv_a}.jsonl",
            conv_b: Path("/fake/transcripts") / f"{conv_b}.jsonl",
        },
        resumes={
            conv_a: ["claude", "--resume", conv_a],
            conv_b: ["claude", "--resume", conv_b],
        },
    )
    _wire_workspace(monkeypatch, ws_dir=ws_dir, slug=slug, group_name=group_name, harness=harness)

    code = _run(["attach", slug, "--group", group_name], monkeypatch)
    captured = capsys.readouterr()
    assert code == 0, captured.err

    rows = _list_windows(server.sock, session)
    assert len(rows) == 2, rows
    dirs = [row[2] for row in rows]
    assert dirs == [
        str((ws_dir / "member-a").resolve()),
        str((ws_dir / "member-b").resolve()),
    ]
    assert dirs[0] != dirs[1], "the two members must root at two DIFFERENT directories"

    window_ids = [row[0] for row in rows]
    for window_id in window_ids:
        _wait_for_shell(server.sock, window_id, frozenset({"sh", "bash", "zsh", "fish"}))

    pane_a = _capture_pane(server.sock, window_ids[0])
    pane_b = _capture_pane(server.sock, window_ids[1])
    assert f"camp: this window held conversation {conv_a}" in pane_a
    assert f"camp: resume it with: claude --resume {conv_a}" in pane_a
    assert f"camp: this window held conversation {conv_b}" in pane_b
    assert f"camp: resume it with: claude --resume {conv_b}" in pane_b

    after = read_window_record(window_record_path_for(ws_dir))
    assert {e.cwd for e in after.entries} == {"member-a", "member-b"}


# ---------------------------------------------------------------------------
# Resurrection binds the same account the workspace session carries — a
# declared-account window's resurrected pane carries the binding in its
# real, running environment, not just in a stub script string.
# ---------------------------------------------------------------------------


class _AccountBindingHarness(_FakeHarness):
    """A `_FakeHarness` (borrowed from `test_resurrect_real_tmux.py`) that
    also answers `session_launch_env_set` — the method
    `resolve_launch_environment` calls to bind a declared account — so
    resurrection has a real binding to resolve, not just a scrub."""

    _ACCOUNT_VAR = "CLAUDE_CONFIG_DIR"

    def session_launch_env_set(self, account, *, env=None):
        if account is None:
            return {}
        return {self._ACCOUNT_VAR: account}


def _wire_workspace_with_account(
    monkeypatch: pytest.MonkeyPatch, *, ws_dir: Path, slug: str, group_name: str,
    harness: _AccountBindingHarness, account_dir: str,
) -> None:
    """Like `test_resurrect_real_tmux.py`'s own `_wire_workspace`, except
    the faked group declares `[launch] account = account_dir` — so
    `create_or_connect_workspace_session`'s `group` reaches the
    resurrection planner with an account to bind, exactly as `camp attach`
    would resolve it from a real config file on disk."""
    cli_session = importlib.import_module("camp.cli.session")
    lifecycle = importlib.import_module("camp.provision.lifecycle")
    profile = importlib.import_module("camp.launch.profile")

    group = {"group": {"name": group_name}, "launch": {"account": account_dir}}
    monkeypatch.setattr(cli_session, "_parsable_groups", lambda: [group])
    monkeypatch.setattr(profile, "harness_for", lambda g: harness)

    def fake_cmd_ls_group(g, *, env=None, tmux=None, **kw):
        return lifecycle.GroupListing(
            entries=[
                {
                    "slug": slug,
                    "workspace_path": str(ws_dir),
                    "state": None,
                    "window_count": None,
                }
            ],
            unmanaged=[],
            unmanaged_count=0,
            notice=None,
        )

    monkeypatch.setattr(lifecycle, "cmd_ls_group", fake_cmd_ls_group)


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_a_declared_accounts_resurrected_pane_has_the_binding_in_its_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, make_server
) -> None:
    """A group with a declared account whose window record has an entry but
    no live tmux session (a "stopped" workspace) resurrects through the
    door, and the resurrected pane's REAL, RUNNING environment — not just
    the stub script's text — carries the bound account. Checked by sending
    a real shell command into the resurrected pane and reading back what it
    printed, since `#{pane_start_command}` on a resurrection's stub shows
    the stub's own positional-argument shape (the binding VALUE as a bare
    token), not the resolved environment the shell actually execs into."""
    from camp.group.window_record import WindowEntry, write_window_record, window_record_path_for
    from camp.launch.naming import workspace_session_name
    from camp.launch.stop_workspace import _COMMON_SHELL_BASENAMES

    account_dir = str(tmp_path / "acct-a")
    server = make_server(group_name="onedoor-acct")
    slug = "camp-cli"
    group_name = server.group_name
    session = workspace_session_name(group_name, slug)
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)

    conv = "8f2c1a3e-aaaa-bbbb-cccc-111122223333"
    write_window_record(
        window_record_path_for(ws_dir),
        [WindowEntry(window_id="@400", name="conv-a", cwd=".", conversation_id=conv)],
    )

    harness = _AccountBindingHarness(
        transcripts={conv: Path("/fake/transcripts") / f"{conv}.jsonl"},
        resumes={conv: ["claude", "--resume", conv]},
    )
    _wire_workspace_with_account(
        monkeypatch, ws_dir=ws_dir, slug=slug, group_name=group_name,
        harness=harness, account_dir=account_dir,
    )

    code = _run(["attach", slug, "--group", group_name], monkeypatch)
    captured = capsys.readouterr()
    assert code == 0, captured.err

    rows = _list_windows(server.sock, session)
    assert len(rows) == 1, rows
    window_id = rows[0][0]
    _wait_for_shell(server.sock, window_id, _COMMON_SHELL_BASENAMES)

    pane = _capture_pane(server.sock, window_id)
    assert f"camp: resume it with: claude --resume {conv}" in pane, (
        "an accepted account must still resurrect with a resume line", pane
    )

    marker = "camp-account-check"
    _sock_run(
        server.sock, "send-keys", "-t", window_id,
        f'echo {marker}-"$CLAUDE_CONFIG_DIR"', "Enter",
    )
    output = _wait_for_output(server.sock, window_id, marker)
    assert f"{marker}-{account_dir}" in output.replace("\n", ""), output
