"""Real-tmux end-to-end proof for the one-door slice
(task/one-door-proven-end-to-end-on-real-tmux) — `camp attach` is the only
way a conversation window ever gets composed, `camp launch` is a dead
redirect, and a group's stale `[launch] roots` key is tolerated with a
one-time notice.

Drives a REAL tmux 3.7c server on a throwaway `-L` socket, the same
redirection trick `test_window_binding_end_to_end.py` and
`test_stop_workspace_real_tmux.py` use: `_REAL_TMUX` is captured by
absolute path at import time, before the autouse `_sandbox_tmux` fixture in
`conftest.py` rewrites `PATH` to a no-server stub for the rest of the
suite, and a thin `tmux` wrapper first on `PATH` transparently redirects
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

The account-binding tests deliberately do NOT fake the harness. A group's
`[launch] account` is bound by the REAL `camp.launch.profile.harness_for`
resolving the REAL `ClaudeCodeHarness` (the group's `[harness]` block is
omitted, so `resolve_harness_profile` defaults `binary` to `"claude"`,
which `trailhead.harness.get_harness` resolves unconditionally — a pure
registry lookup, no filesystem check). That resolution happens in TWO
places for one `camp attach` call: in-process, building the addressable
pool `camp attach` reads before it ever reaches the door (harmless here,
answered by a tiny `claude` stub binary on `PATH` that satisfies
`ClaudeCodeHarness.session_enumerate`'s `claude agents --json …` call with
an empty list); and — the one that matters — inside the REAL, SEPARATE
`camp window-dispatch` subprocess the window-creation key's `run-shell`
spawns when a real key is pressed through a genuine attached pty client
(never `send-keys` — see `test_window_binding_end_to_end.py`'s module
docstring for why that distinction is load-bearing). That subprocess reads
the group's `[launch] account` from the REAL toml file on disk (the tmux
SERVER's own environment, fixed at first start via `new_session(env=...)`,
carries `CAMP_CONFIG_DIR` into it) and resolves the SAME real harness fresh
— nothing in this test process reaches across that process boundary.
"""

from __future__ import annotations

import importlib
import os
import shlex
import shutil
import stat
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

# Captured NOW, before conftest's _sandbox_tmux fixture prepends a stub to
# PATH for every other test in the suite.
_REAL_TMUX = shutil.which("tmux")

from test_stop_cli import _run  # noqa: E402
from test_window_binding_end_to_end import (  # noqa: E402
    _attach_and_send,
    _record_entries_reach,
    _sock_run,
)
from test_resurrect_real_tmux import (  # noqa: E402
    _FakeHarness,
    _capture_pane,
    _list_windows,
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
    """One throwaway tmux server plus a real, on-disk group config — so the
    genuine `camp window-dispatch` subprocess the window-creation key's
    `run-shell` spawns resolves `[launch] account` from a real file, exactly
    as it would outside a test. `launch_block` is the raw `[launch] …` toml
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


def _pane_start_command(sock: str, window_id: str) -> str:
    result = _sock_run(sock, "list-panes", "-t", window_id, "-F", "#{pane_start_command}")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _press_the_key_and_wait(sock: str, session: str, ws_dir: Path) -> None:
    """Fires the real window-creation key through a genuine attached pty
    client (never `send-keys` — see `test_window_binding_end_to_end.py`'s
    module docstring on why that distinction is load-bearing) and waits for
    the composed window's entry to land in the workspace's window record."""
    _attach_and_send(
        sock,
        session,
        b"\x02c",
        settle=1.5,
        until=_record_entries_reach(ws_dir, 1),
    )


# ---------------------------------------------------------------------------
# Delivers bullet 1/2: a declared (or absent) account, real key press,
# real `camp window-dispatch` subprocess, real pane_start_command.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_a_group_declaring_an_account_composes_the_pane_with_the_account_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_server
) -> None:
    from camp.launch.naming import workspace_session_name
    from camp.group.window_record import read_window_record, window_record_path_for

    account_dir = str(tmp_path / "acct-a")
    server = make_server(launch_block=f'[launch]\naccount = "{account_dir}"\n')
    slug = "camp-cli"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    _wire_group_listing(monkeypatch, ws_dir=ws_dir, slug=slug)

    code = _run(["attach", slug, "--group", server.group_name], monkeypatch)
    assert code == 0

    session = workspace_session_name(server.group_name, slug)
    _press_the_key_and_wait(server.sock, session, ws_dir)

    rows = _list_windows(server.sock, session)
    assert len(rows) == 2, rows
    composed_id = rows[1][0]

    pane_command = _pane_start_command(server.sock, composed_id)
    tokens = shlex.split(pane_command)

    assignment = f"CLAUDE_CONFIG_DIR={account_dir}"
    assert assignment in tokens, tokens
    scrub_indices = [i for i, t in enumerate(tokens) if t == "-u"]
    assert scrub_indices, tokens
    assignment_index = tokens.index(assignment)
    session_id_index = tokens.index("--session-id")
    assert max(scrub_indices) < assignment_index < session_id_index, tokens
    assert "--remote-control" not in tokens
    assert "--name" not in tokens

    record = read_window_record(window_record_path_for(ws_dir))
    assert record.status == "ok"
    assert len(record.entries) == 1, record
    assert record.entries[0].conversation_id is not None


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_a_group_declaring_no_account_composes_the_pane_with_no_assignment(
    monkeypatch: pytest.MonkeyPatch, make_server
) -> None:
    from camp.launch.naming import workspace_session_name

    server = make_server(launch_block="")
    slug = "camp-cli"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    _wire_group_listing(monkeypatch, ws_dir=ws_dir, slug=slug)

    code = _run(["attach", slug, "--group", server.group_name], monkeypatch)
    assert code == 0

    session = workspace_session_name(server.group_name, slug)
    _press_the_key_and_wait(server.sock, session, ws_dir)

    rows = _list_windows(server.sock, session)
    assert len(rows) == 2, rows
    composed_id = rows[1][0]

    pane_command = _pane_start_command(server.sock, composed_id)
    tokens = shlex.split(pane_command)

    assert not any(t.startswith("CLAUDE_CONFIG_DIR=") for t in tokens), tokens
    assert "CLAUDE_CONFIG_DIR" in tokens, (
        "the default is still scrubbed even with no declared account",
        tokens,
    )
    assert "--session-id" in tokens
    assert "--remote-control" not in tokens
    assert "--name" not in tokens


# ---------------------------------------------------------------------------
# Delivers bullet 3: `camp launch` is a dead redirect that never touches
# tmux.
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
# Delivers bullet 4: a stale `[launch] roots` key is tolerated, with a
# one-time-per-config-path notice.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_roots_prints_the_notice_once_and_a_second_attach_does_not_repeat_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, make_server
) -> None:
    server = make_server(launch_block='[launch]\nroots = ["~/code"]\n')
    slug = "camp-cli"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    _wire_group_listing(monkeypatch, ws_dir=ws_dir, slug=slug)

    code = _run(["attach", slug, "--group", server.group_name], monkeypatch)
    first = capsys.readouterr()
    assert code == 0, first.err

    notice = "is no longer used and grants nothing — remove it"
    assert first.err.count(notice) == 1, first.err

    code2 = _run(["attach", slug, "--group", server.group_name], monkeypatch)
    second = capsys.readouterr()
    assert code2 == 0, second.err
    assert notice not in second.err, second.err


# ---------------------------------------------------------------------------
# Also delivers: a transferred conversation's member subpath roots the
# resurrected window there, varied across two members.
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
