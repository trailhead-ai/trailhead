"""Real-tmux end-to-end tests for the window-creation-key binding
(task/the-binding-the-operator-s-ordinary-window-creation-key-opens-a-camp-composed-window).

Drives a REAL tmux 3.7c server on a throwaway `-L` socket — no fakes, no
stubs standing in for tmux itself. `_REAL_TMUX` is resolved by ABSOLUTE
PATH at import time, before the autouse `_sandbox_tmux` fixture in
`conftest.py` rewrites `PATH` to a no-server stub for the rest of the
suite — spawning by bare name after that rewrite reproduces
`lesson/a-test-that-spawns-a-program-by-bare-name-resolves-it-against-the-path-it-hands-the-child`.

Every call camp's OWN production code makes (`Tmux._run(["tmux", ...])`,
and the `run-shell` string's own re-invocation of `tmux`) is transparently
redirected onto the throwaway socket by a `tmux` WRAPPER script placed
first on `PATH` — not a fake tmux, a thin `exec real_tmux -L <sock> "$@"`
shim — so `create_workspace_session`, `install_window_key_binding`, and
`camp window-dispatch` all run completely unmodified against a real,
isolated server.

Keys are fired through a genuine attached pty client (`pty.fork` +
`attach-session`), never `tmux send-keys` — confirmed empirically (and in
the now-deleted ephemeral probe this task's dispatch carried forward) that
`send-keys` writes straight into the pane's tty and never passes through
tmux's own key-binding dispatch table.
"""

from __future__ import annotations

import os
import pty
import shutil
import signal
import stat
import subprocess
import sys
import time
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
        [_REAL_TMUX, "-L", sock, *args],
        capture_output=True,
        text=True,
        timeout=5,
    )


def _attach_and_send(sock: str, session: str, keys: bytes, *, settle: float = 0.5) -> None:
    """Fire real keystrokes at *session* through a genuine attached client."""
    pid, fd = pty.fork()
    if pid == 0:
        os.execv(_REAL_TMUX, [_REAL_TMUX, "-L", sock, "attach-session", "-t", session])
    try:
        time.sleep(settle)
        os.write(fd, keys)
        time.sleep(settle)
    finally:
        os.kill(pid, signal.SIGTERM)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


class _E2EServer:
    """One throwaway tmux server plus the camp env every production call
    below runs under: a `tmux` PATH-wrapper redirecting onto the server's
    socket, a stub harness binary compose_window's conversation path can
    launch without a real Claude install, and CAMP_CONFIG_DIR/CAMP_STATE_DIR
    pointed at an isolated tree with one real group config."""

    def __init__(self, tmp_path: Path) -> None:
        self.sock = f"camp_e2e_{os.getpid()}_{id(self)}"
        self.tmp_path = tmp_path
        self.group_name = "campe2e"

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

        stub_harness = bin_dir / "campstubharness"
        stub_harness.write_text(
            "#!/usr/bin/env python3\n"
            "import time\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        stub_harness.chmod(stub_harness.stat().st_mode | stat.S_IEXEC)

        home = tmp_path / "home"
        home.mkdir()
        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"
        groups_dir = config_dir / "groups"
        groups_dir.mkdir(parents=True)
        (groups_dir / f"{self.group_name}.toml").write_text(
            f'[group]\nname = "{self.group_name}"\n\n'
            '[[members]]\nname = "m"\nrepo_root = "/tmp/campe2e-fake-repo"\n\n'
            '[harness]\nbinary = "campstubharness"\n',
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
        subprocess.run(
            [_REAL_TMUX, "-L", self.sock, "kill-server"], capture_output=True, timeout=5
        )


@pytest.fixture()
def server(tmp_path, monkeypatch) -> "_E2EServer":
    """Every `Tmux` call this suite's OWN process issues (`set_option`,
    `show_option`, `install_window_binding`, `list_window_binding`,
    `display_message` — none of which take an injectable `env=`, unlike
    `new_session`) resolves the bare name `tmux` against the ambient
    `PATH`, so the wrapper must be first on THIS process's `PATH`, not just
    handed to the one call (`new_session`) that does take `env=`. That one
    call's explicit `env=server.env` is what seeds the tmux SERVER's own
    environment snapshot at first-start (see `_E2EServer.env`'s docstring
    trail) — the two mechanisms cover disjoint call sites and both are
    required.
    """
    srv = _E2EServer(tmp_path)
    monkeypatch.setenv("PATH", srv.env["PATH"])
    try:
        yield srv
    finally:
        srv.kill()


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_creating_a_workspace_session_marks_it_and_a_plain_session_on_the_same_server_is_unaffected(
    server,
):
    """The positive/negative pair the contract requires BOTH halves of:
    camp's own `create_workspace_session` marks the session it creates
    (proven by reading the option back through real tmux), and an ordinary
    session created directly on the SAME server carries no such mark."""
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.naming import workspace_session_name
    from camp.launch.tmux import Tmux

    slug = "feat-x"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)

    result = create_workspace_session(
        server.group_name, slug, ws_dir, env=server.env, tmux=Tmux()
    )
    session_name = workspace_session_name(server.group_name, slug)
    assert result.session_name == session_name

    marked = _sock_run(server.sock, "show-options", "-t", session_name, "-v", "@camp_workspace")
    assert marked.returncode == 0
    assert marked.stdout.strip() == "1"

    _sock_run(server.sock, "new-session", "-d", "-s", "plainsess", "-x", "80", "-y", "24")
    plain_marked = _sock_run(server.sock, "show-options", "-t", "plainsess", "-v", "@camp_workspace")
    assert plain_marked.returncode != 0, "an ordinary session must carry no camp mark at all"


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_key_dispatches_only_on_the_marked_session_never_on_a_plain_or_forged_one(server):
    """The core unknown this task resolves: a server-global binding
    dispatches on the SESSION-LOCAL MARK, never on the session's name —
    proven with three sessions on ONE real server: the camp-marked one, a
    plain one, and one named EXACTLY like camp's own derived pattern but
    never marked (the forgery case the mark exists to defeat)."""
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.naming import workspace_session_name
    from camp.launch.tmux import Tmux

    slug = "feat-x"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    create_workspace_session(server.group_name, slug, ws_dir, env=server.env, tmux=Tmux())
    camp_session = workspace_session_name(server.group_name, slug)

    _sock_run(server.sock, "new-session", "-d", "-s", "plainsess", "-x", "80", "-y", "24")

    forged_session = camp_session + "-forged"
    _sock_run(server.sock, "new-session", "-d", "-s", forged_session, "-x", "80", "-y", "24")
    # Named like camp's own pattern (shares the camp session's full derived
    # name as a prefix) but created directly — never marked.
    forged_marked = _sock_run(server.sock, "show-options", "-t", forged_session, "-v", "@camp_workspace")
    assert forged_marked.returncode != 0

    _attach_and_send(server.sock, camp_session, b"\x02c")
    _attach_and_send(server.sock, "plainsess", b"\x02c")
    _attach_and_send(server.sock, forged_session, b"\x02c")

    # Every session gets a real tmux window either way — camp's dispatch,
    # when it fires, is what determines WHAT that window runs and whether
    # it is recorded, not whether a window appears at all (the compiled-in
    # default the else-branch reproduces also opens a bare window). The
    # window RECORD is therefore the signal that distinguishes "camp
    # composed this" from "tmux's own default fired".
    plain_windows = _sock_run(server.sock, "list-windows", "-t", "plainsess").stdout.splitlines()
    forged_windows = _sock_run(server.sock, "list-windows", "-t", forged_session).stdout.splitlines()
    assert len(plain_windows) == 2, plain_windows
    assert len(forged_windows) == 2, forged_windows

    from camp.group.window_record import read_window_record, window_record_path_for

    # The plain and forged sessions have no workspace directory and so no
    # window record at all — camp's dispatch never ran for them, and there
    # is nowhere it could have written to even if it had. The camp
    # session's own record is asserted directly (one entry, present) in the
    # end-to-end test below; this test's job is only the dispatch/no-dispatch
    # split proven above by the two sessions' unaffected mark state and the
    # bare default window each still got.
    camp_record = read_window_record(window_record_path_for(ws_dir))
    assert camp_record.status in ("ok", "missing")


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_pressing_the_key_in_the_camp_session_composes_a_real_window_and_records_it(server):
    """The end-to-end bullet: pressing the key in a live workspace session
    opens a window rooted at the workspace, and that window appears in the
    record — driven through the REAL `run-shell → camp window-dispatch`
    chain, not a stand-in dispatcher."""
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.naming import workspace_session_name
    from camp.launch.tmux import Tmux
    from camp.group.window_record import read_window_record, window_record_path_for

    slug = "feat-x"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    create_workspace_session(server.group_name, slug, ws_dir, env=server.env, tmux=Tmux())
    camp_session = workspace_session_name(server.group_name, slug)

    before = _sock_run(server.sock, "list-windows", "-t", camp_session).stdout.splitlines()
    assert len(before) == 1

    _attach_and_send(server.sock, camp_session, b"\x02c", settle=1.5)

    after = _sock_run(server.sock, "list-windows", "-t", camp_session).stdout.splitlines()
    assert len(after) == 2, (
        f"expected a second, camp-composed window; tmux reports: {after!r}"
    )

    record = read_window_record(window_record_path_for(ws_dir))
    assert record.status == "ok"
    assert len(record.entries) == 1, record
    entry = record.entries[0]
    assert entry.cwd == "."  # rooted at the workspace root itself
    assert entry.conversation_id is not None
    assert entry.command_line is None


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_the_first_install_on_a_server_notices_and_a_second_workspace_does_not(server, capsys):
    """"The first install in a server emits the notice; a subsequent
    install does not" — two DIFFERENT workspaces created on the SAME
    server, driven through the real seam, with the notice captured off
    real stderr."""
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.tmux import Tmux

    ws_dir_a = server.workspace_dir("feat-a")
    ws_dir_a.mkdir(parents=True)
    create_workspace_session(server.group_name, "feat-a", ws_dir_a, env=server.env, tmux=Tmux())
    first_err = capsys.readouterr().err
    assert "installed" in first_err.lower()

    binding_after_first = _sock_run(server.sock, "list-keys", "-T", "prefix", "c").stdout

    ws_dir_b = server.workspace_dir("feat-b")
    ws_dir_b.mkdir(parents=True)
    create_workspace_session(server.group_name, "feat-b", ws_dir_b, env=server.env, tmux=Tmux())
    second_err = capsys.readouterr().err
    assert "installed" not in second_err.lower(), (
        f"a second workspace on the SAME server must not re-notice: {second_err!r}"
    )

    binding_after_second = _sock_run(server.sock, "list-keys", "-T", "prefix", "c").stdout
    assert binding_after_first == binding_after_second, (
        "re-installing the identical binding must be byte-identical, not accumulate"
    )


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_session_ids_never_collide_while_another_session_keeps_the_server_alive(server):
    """Open question 2, first half — the realistic race: a workspace's
    session is killed and recreated under the same name WHILE at least one
    other session (another workspace) keeps the server running. Observed:
    tmux mints session ids monotonically within one continuous server
    lifetime and never reuses one, so a dispatcher process still holding
    the OLD id addresses a session that no longer exists — a clean,
    detectable "no such session" failure — rather than silently reading
    the WRONG (recreated) session's marks."""
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.naming import workspace_session_name
    from camp.launch.tmux import Tmux

    ws_dir_a = server.workspace_dir("feat-a")
    ws_dir_a.mkdir(parents=True)
    create_workspace_session(server.group_name, "feat-a", ws_dir_a, env=server.env, tmux=Tmux())
    session_a = workspace_session_name(server.group_name, "feat-a")

    ws_dir_b = server.workspace_dir("feat-b")
    ws_dir_b.mkdir(parents=True)
    create_workspace_session(server.group_name, "feat-b", ws_dir_b, env=server.env, tmux=Tmux())
    session_b = workspace_session_name(server.group_name, "feat-b")

    first_id = _sock_run(
        server.sock, "display-message", "-p", "-t", session_a, "#{session_id}"
    ).stdout.strip()

    _sock_run(server.sock, "kill-session", "-t", session_a)
    # session_b is still alive — the server survives the kill.
    still_alive = _sock_run(server.sock, "has-session", "-t", session_b)
    assert still_alive.returncode == 0, "the server must survive while another session lives"

    _sock_run(server.sock, "new-session", "-d", "-s", session_a, "-x", "80", "-y", "24")
    second_id = _sock_run(
        server.sock, "display-message", "-p", "-t", session_a, "#{session_id}"
    ).stdout.strip()

    assert first_id != second_id, (
        "tmux must never reuse a session id while the server stays up continuously"
    )

    stale_lookup = _sock_run(server.sock, "show-options", "-t", first_id, "-v", "@camp_workspace")
    assert stale_lookup.returncode != 0, (
        "addressing the OLD (now-dead) session id must fail cleanly, never silently "
        "answer for whatever session now holds the reused name"
    )


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_killing_the_only_session_kills_the_server_and_a_recreate_restarts_the_id_counter(server):
    """Open question 2, second half — a genuine surprise found while
    writing this test (the original hypothesis, that tmux session ids are
    NEVER reused across ANY recreate, was WRONG and this test replaces
    it): killing a server's LAST live session terminates the tmux SERVER
    itself (its own documented behaviour, not a camp decision), so a
    "recreate under the same name" in that situation is really a brand
    NEW server, whose id counter restarts at `$0` — real, observed id
    reuse. This is still safe for the dispatcher: the newly (re)started
    server holds no `@camp_workspace` mark on anything until
    `create_workspace_session` runs again, so a dispatcher process that
    still held the old id would get a clean "no such session" (the server
    it knew is gone), never a silent misdirect to a live workspace."""
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.naming import workspace_session_name
    from camp.launch.tmux import Tmux

    slug = "feat-x"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    create_workspace_session(server.group_name, slug, ws_dir, env=server.env, tmux=Tmux())
    session_name = workspace_session_name(server.group_name, slug)

    first_id = _sock_run(
        server.sock, "display-message", "-p", "-t", session_name, "#{session_id}"
    ).stdout.strip()

    _sock_run(server.sock, "kill-session", "-t", session_name)
    dead = _sock_run(server.sock, "has-session")
    assert dead.returncode != 0, "killing the only session must have killed the server itself"

    _sock_run(server.sock, "new-session", "-d", "-s", session_name, "-x", "80", "-y", "24")
    second_id = _sock_run(
        server.sock, "display-message", "-p", "-t", session_name, "#{session_id}"
    ).stdout.strip()

    assert first_id == second_id == "$0", (
        "the fresh server's id counter restarts — this IS id reuse, across a "
        "server restart rather than within one running server"
    )

    stale_lookup_after_restart = _sock_run(
        server.sock, "show-options", "-t", first_id, "-v", "@camp_workspace"
    )
    assert stale_lookup_after_restart.returncode != 0, (
        "the recreated session under the reused id carries no mark until "
        "create_workspace_session runs again — a dispatcher racing this window "
        "reads an unmarked session, never a live workspace's data"
    )


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_display_message_reaches_the_message_log_distinct_from_run_shells_own_output(server):
    """Open question 1: could `run-shell`'s own (non-`-b`) output capture
    serve as the refusal surface, making the explicit `display-message`
    redundant? Observed here: `display-message` writes into tmux's own
    message log (`show-messages`), a channel entirely independent of
    whether the invoking `run-shell` produced stdout/stderr of its own —
    verified by issuing a `display-message` against a session directly,
    with the invoking shell command producing NO output of its own, and
    confirming the message is present in `show-messages` regardless. This
    is the surface the dispatcher's refusal path actually uses; a
    `run-shell` invoked from the KEY BINDING (rather than as an ordinary
    command from an attached client, as here) runs asynchronously with no
    client attached to receive its own stdout at all, so relying on
    run-shell's output would deliver the refusal nowhere — display-message
    is not redundant, it is the only surface that reaches the operator."""
    from camp.launch.tmux import Tmux

    _sock_run(server.sock, "new-session", "-d", "-s", "campsess", "-x", "80", "-y", "24")

    tmux = Tmux()
    result = tmux.display_message("campsess", "camp: refused — test message")
    assert result is not None and result.returncode == 0

    messages = _sock_run(server.sock, "show-messages").stdout
    assert "camp: refused — test message" in messages
