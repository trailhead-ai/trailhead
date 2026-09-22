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

import contextlib
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

from typing import Callable

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


#: The attaching client's terminal type. `attach-session` needs a usable
#: `TERM` and REFUSES without one — `open terminal failed: terminal does not
#: support clear` — so it cannot be inherited: a CI runner's environment
#: routinely has `TERM` unset where a developer's shell always has it set.
#: Pinned rather than defaulted so the attach behaves identically in both.
_ATTACH_TERM = "screen"


def _await_client(sock: str, session: str, fd: int, timeout: float) -> str | None:
    """Wait until tmux reports a client attached to *session*.

    Answers ``None`` on success, or whatever the client wrote to its pty
    before giving up — tmux's own refusal, which is the only thing that
    explains WHY nothing attached.

    The child's output is drained as we poll rather than after: a client
    that refuses prints its complaint and exits immediately, and reading a
    pty whose child has already gone raises `OSError` instead of handing
    back what it said.
    """
    os.set_blocking(fd, False)
    said: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            chunk = os.read(fd, 4096)
        except (BlockingIOError, OSError):
            chunk = b""
        if chunk:
            said.append(chunk.decode(errors="replace"))
        if _sock_run(sock, "list-clients", "-t", session).stdout.strip():
            return None
        time.sleep(0.05)
    return "".join(said).strip()


def _window_count_reaches(sock: str, session: str, n: int) -> "Callable[[], bool]":
    """Predicate: *session* holds at least *n* windows."""
    return lambda: len(
        _sock_run(sock, "list-windows", "-t", session).stdout.splitlines()
    ) >= n


def _record_entries_reach(ws_dir, n: int) -> "Callable[[], bool]":
    """Predicate: the workspace's window record holds at least *n* entries."""
    from camp.group.window_record import read_window_record, window_record_path_for

    return lambda: len(read_window_record(window_record_path_for(ws_dir)).entries) >= n


@contextlib.contextmanager
def _attached_client(sock: str, session: str, *, timeout: float = 5.0):
    """Hold a real attached client on *session* for the body of the block.

    `_attach_and_send` attaches only long enough to deliver a keystroke; this
    is for the case where the thing under test must happen WHILE a client is
    attached, rather than being caused by one.
    """
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(
            _REAL_TMUX,
            [_REAL_TMUX, "-L", sock, "attach-session", "-t", session],
            {**os.environ, "TERM": _ATTACH_TERM},
        )
    try:
        complaint = _await_client(sock, session, fd, timeout=timeout)
        if complaint is not None:
            raise AssertionError(
                f"no client ever attached to {session!r}; tmux said: {complaint!r}"
            )
        yield
    finally:
        os.kill(pid, signal.SIGTERM)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


def _attach_and_send(
    sock: str,
    session: str,
    keys: bytes,
    *,
    settle: float = 0.5,
    until: "Callable[[], bool] | None" = None,
) -> None:
    """Fire real keystrokes at *session* through a genuine attached client.

    Waits for tmux to REPORT the client attached before writing the keys,
    rather than sleeping a fixed interval and hoping. The fixed sleep was
    both slower than it needed to be locally and not long enough to trust on
    a loaded runner — and, worse, silent: a client that never attached at
    all looked exactly like one that attached and ignored the key, so every
    caller failed with "expected a second window, got one" instead of the
    actual reason.

    *until* is the caller's own "the key's effect has landed" predicate —
    a window appearing, a record gaining an entry. Given one, the keystroke
    is followed by polling it rather than by another fixed sleep: what
    follows a key press here is a whole `run-shell` dispatch spawning a
    process and writing a file, and how long that takes depends on what else
    is running. A caller that asserts an EFFECT should pass one; the bare
    sleep remains for the callers that assert nothing happened, which have
    no effect to wait for and must simply give it time.
    """
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(
            _REAL_TMUX,
            [_REAL_TMUX, "-L", sock, "attach-session", "-t", session],
            {**os.environ, "TERM": _ATTACH_TERM},
        )
    try:
        complaint = _await_client(sock, session, fd, timeout=max(settle, 5.0))
        if complaint is not None:
            raise AssertionError(
                f"no client ever attached to {session!r}; tmux said: {complaint!r}"
            )
        os.set_blocking(fd, True)
        os.write(fd, keys)
        if until is None:
            time.sleep(settle)
            return
        deadline = time.monotonic() + max(settle, 5.0)
        while time.monotonic() < deadline:
            if until():
                return
            time.sleep(0.05)
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

    # A name camp's OWN naming function would produce for a different,
    # legitimate workspace — not a hand-built lookalike (a string-concat
    # suffix is not a shape workspace_session_name ever emits) — created
    # directly rather than through create_workspace_session, so it is real
    # camp-pattern name that camp itself never created and never marked.
    forged_session = workspace_session_name(server.group_name, "forged-slug")
    assert forged_session != camp_session
    _sock_run(server.sock, "new-session", "-d", "-s", forged_session, "-x", "80", "-y", "24")
    forged_marked = _sock_run(server.sock, "show-options", "-t", forged_session, "-v", "@camp_workspace")
    assert forged_marked.returncode != 0

    # settle=1.5 on the camp session only, matching the dedicated
    # compose test below: camp's branch spawns a real subprocess
    # (`camp window-dispatch`) that must run to completion before the
    # record reflects it, unlike the plain/forged sessions' else-branch
    # (`new-window`), which is synchronous inside tmux itself.
    _attach_and_send(
        server.sock,
        camp_session,
        b"\x02c",
        settle=1.5,
        until=_record_entries_reach(ws_dir, 1),
    )
    # These two assert camp composed nothing, but they still assert a WINDOW
    # — tmux's own default opens one either way — so they wait for that.
    _attach_and_send(
        server.sock, "plainsess", b"\x02c", until=_window_count_reaches(server.sock, "plainsess", 2)
    )
    _attach_and_send(
        server.sock,
        forged_session,
        b"\x02c",
        until=_window_count_reaches(server.sock, forged_session, 2),
    )

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
    # session's own record is the one place a bypass of the mark check
    # would actually show up: a dispatch that fired for plainsess or
    # forged_session, in some broken future where the mark gate no longer
    # decides, has nowhere else to write to but THIS same ws_dir — so
    # pinning it at exactly "ok" with exactly one entry (never "missing",
    # never more than one) proves camp's dispatch fired precisely once,
    # for the marked session alone, rather than merely "did not error".
    camp_record = read_window_record(window_record_path_for(ws_dir))
    assert camp_record.status == "ok", camp_record
    assert len(camp_record.entries) == 1, camp_record


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

    _attach_and_send(
        server.sock,
        camp_session,
        b"\x02c",
        settle=1.5,
        until=_record_entries_reach(ws_dir, 1),
    )

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
def test_a_stale_camp_binary_path_still_opens_a_window_instead_of_a_dead_key(server):
    """Finding 5: `_DEFAULT_CAMP_BIN` bakes in this checkout's path at
    install time. This repo dogfoods camp from disposable worktrees, so a
    checkout getting deleted after a binding was installed is an ordinary
    end state — proven here by installing the binding against a camp_bin
    that never existed on disk at all, then pressing the key: the key must
    still open a real tmux window (the compiled-in `new-window` default),
    never a dead key. The window record staying empty is the proof that
    camp's own composition never ran — the fallback fired, not camp's
    branch happening to succeed anyway."""
    from camp.launch.binding import install_window_key_binding
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.naming import workspace_session_name
    from camp.launch.tmux import Tmux
    from camp.group.window_record import read_window_record, window_record_path_for

    slug = "feat-x"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    create_workspace_session(server.group_name, slug, ws_dir, env=server.env, tmux=Tmux())
    camp_session = workspace_session_name(server.group_name, slug)

    # Overwrite the working binding just installed with one pointing at a
    # camp_bin that has never existed — reproducing "the checkout got
    # deleted after install" without needing an actual worktree deletion.
    install_window_key_binding(Tmux(), camp_bin=str(server.tmp_path / "no-such-checkout" / "cli" / "camp"))

    before = _sock_run(server.sock, "list-windows", "-t", camp_session).stdout.splitlines()
    assert len(before) == 1

    _attach_and_send(
        server.sock,
        camp_session,
        b"\x02c",
        settle=1.5,
        until=_window_count_reaches(server.sock, camp_session, 2),
    )

    after = _sock_run(server.sock, "list-windows", "-t", camp_session).stdout.splitlines()
    assert len(after) == 2, (
        f"a stale camp_bin must still degrade to tmux's default window, "
        f"never a dead key; tmux reports: {after!r}"
    )

    record = read_window_record(window_record_path_for(ws_dir))
    assert record.entries == (), (
        "camp's own composition must never have run — the fallback branch "
        f"fired, not a lucky success despite the missing binary: {record!r}"
    )


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

    with _attached_client(server.sock, "campsess"):
        result = Tmux().display_message("campsess", "camp: refused — test message")
        assert result is not None and result.returncode == 0
        messages = _sock_run(server.sock, "show-messages").stdout

    # `show-messages` interleaves two kinds of line: `<client> command: <argv>`,
    # echoing every command the server ran, and `<client> message: <text>`, the
    # messages actually DELIVERED to a client. Only the second is evidence of
    # anything. Matching the raw text against the whole log matches the command
    # echo of camp's own `display-message` call — which is present even with no
    # client attached and nothing displayed anywhere, so the assertion passed
    # while proving nothing.
    delivered = [line for line in messages.splitlines() if " message: " in line]
    assert any("camp: refused — test message" in line for line in delivered), (
        f"no DELIVERED message carried the refusal; full log:\n{messages}"
    )


_CAMP_BIN = str(_PLUGIN_DIR / "cli" / "camp")


def _run_camp_window_unbind(server: "_E2EServer") -> subprocess.CompletedProcess[str]:
    """Invoke the REAL `camp` CLI entry point — `camp window unbind` — as a
    genuine subprocess against *server*'s throwaway socket, exercising
    `cli/dispatch.py`'s real routing and `cli/window.py`'s real wiring, not
    just the pure functions behind them (those are covered by
    `test_cli_window.py` and `test_launch_binding.py`)."""
    return subprocess.run(
        [_CAMP_BIN, "window", "unbind"],
        env=server.env,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_unbind_stops_the_key_composing_in_the_camp_session_and_leaves_a_plain_one_alone(server):
    """Test contract bullet 1: after removal, pressing the key in a marked
    camp workspace session no longer composes anything (the record gains no
    new entry — camp's branch never fires), and a plain, never-marked
    session on the same server is unaffected either way. Both sessions
    still get a real tmux window, from the binding camp put back — here
    tmux's own stock `new-window`, since nothing customized it on this
    server. The signal that distinguishes "camp composed this" from "the
    restored binding fired" is the window record, exactly as the paired
    install test above uses it."""
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.naming import workspace_session_name
    from camp.launch.tmux import Tmux
    from camp.group.window_record import read_window_record, window_record_path_for

    slug = "feat-x"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    create_workspace_session(server.group_name, slug, ws_dir, env=server.env, tmux=Tmux())
    camp_session = workspace_session_name(server.group_name, slug)

    _sock_run(server.sock, "new-session", "-d", "-s", "plainsess", "-x", "80", "-y", "24")

    result = _run_camp_window_unbind(server)
    assert result.returncode == 0, result.stderr

    _attach_and_send(
        server.sock,
        camp_session,
        b"\x02c",
        settle=1.0,
        until=_window_count_reaches(server.sock, camp_session, 2),
    )
    _attach_and_send(
        server.sock,
        "plainsess",
        b"\x02c",
        settle=1.0,
        until=_window_count_reaches(server.sock, "plainsess", 2),
    )

    camp_windows = _sock_run(server.sock, "list-windows", "-t", camp_session).stdout.splitlines()
    plain_windows = _sock_run(server.sock, "list-windows", "-t", "plainsess").stdout.splitlines()
    assert len(camp_windows) == 2, camp_windows
    assert len(plain_windows) == 2, plain_windows

    record = read_window_record(window_record_path_for(ws_dir))
    assert record.entries == (), (
        "the camp session's own key press must not have composed anything "
        f"after unbind: {record!r}"
    )


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_unbind_with_no_binding_ever_installed_still_reports_success(server):
    """Test contract bullet 2: removal when no binding is installed reports
    the same end state and succeeds — not an error — proven on a server
    that has NEVER had `create_workspace_session` (and so never
    `install_window_key_binding`) run against it at all."""
    result = _run_camp_window_unbind(server)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "camp: the window-creation key is back to its tmux default"


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_unbind_then_a_new_workspace_session_reinstalls_the_binding(server):
    """Test contract bullet 3: removal is not sticky — creating a new
    workspace session after `unbind` reinstalls the binding, proven by
    pressing the key in that later session and observing a real compose
    (a second window plus a window-record entry), not tmux's bare
    default."""
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.naming import workspace_session_name
    from camp.launch.tmux import Tmux
    from camp.group.window_record import read_window_record, window_record_path_for

    result = _run_camp_window_unbind(server)
    assert result.returncode == 0, result.stderr

    slug = "feat-x"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    create_workspace_session(server.group_name, slug, ws_dir, env=server.env, tmux=Tmux())
    camp_session = workspace_session_name(server.group_name, slug)

    _attach_and_send(
        server.sock,
        camp_session,
        b"\x02c",
        settle=1.5,
        until=_record_entries_reach(ws_dir, 1),
    )

    windows = _sock_run(server.sock, "list-windows", "-t", camp_session).stdout.splitlines()
    assert len(windows) == 2, windows

    record = read_window_record(window_record_path_for(ws_dir))
    assert record.status == "ok"
    assert len(record.entries) == 1, (
        "the binding must be REINSTALLED by the new create_workspace_session "
        f"call, composing (not defaulting) the window: {record!r}"
    )


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_unbind_does_not_kill_or_restart_the_server_and_every_session_survives(server):
    """Test contract bullet 4: removal does not kill or restart the tmux
    server, and every session on it survives — asserted by re-polling
    `has-session` for each session AFTER unbind, not merely by reading
    `unbind`'s own exit code."""
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.tmux import Tmux

    ws_dir_a = server.workspace_dir("feat-a")
    ws_dir_a.mkdir(parents=True)
    create_workspace_session(server.group_name, "feat-a", ws_dir_a, env=server.env, tmux=Tmux())

    _sock_run(server.sock, "new-session", "-d", "-s", "plainsess", "-x", "80", "-y", "24")

    result = _run_camp_window_unbind(server)
    assert result.returncode == 0, result.stderr

    from camp.launch.naming import workspace_session_name

    camp_session = workspace_session_name(server.group_name, "feat-a")
    camp_alive = _sock_run(server.sock, "has-session", "-t", camp_session)
    plain_alive = _sock_run(server.sock, "has-session", "-t", "plainsess")
    assert camp_alive.returncode == 0, "the camp workspace session must survive unbind"
    assert plain_alive.returncode == 0, "the plain session must survive unbind"

    server_alive = _sock_run(server.sock, "has-session")
    assert server_alive.returncode == 0, "the server itself must still be reachable"


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_unbind_leaves_the_workspace_window_record_untouched(server):
    """Test contract bullet 5: removal leaves every workspace's window
    record untouched — a workspace that already composed one window before
    `unbind` runs still shows exactly that one entry, unchanged, after."""
    from camp.launch.workspace_session import create_workspace_session
    from camp.launch.naming import workspace_session_name
    from camp.launch.tmux import Tmux
    from camp.group.window_record import read_window_record, window_record_path_for

    slug = "feat-x"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    create_workspace_session(server.group_name, slug, ws_dir, env=server.env, tmux=Tmux())
    camp_session = workspace_session_name(server.group_name, slug)

    _attach_and_send(
        server.sock,
        camp_session,
        b"\x02c",
        settle=1.5,
        until=_record_entries_reach(ws_dir, 1),
    )

    before = read_window_record(window_record_path_for(ws_dir))
    assert len(before.entries) == 1, before

    result = _run_camp_window_unbind(server)
    assert result.returncode == 0, result.stderr

    after = read_window_record(window_record_path_for(ws_dir))
    assert after.entries == before.entries, (
        f"the window record must be byte-for-byte unchanged by unbind: "
        f"before={before!r} after={after!r}"
    )


_OPERATOR_BINDING_COMMAND = 'new-window -c "#{pane_current_path}"'


def _prefix_c_line(sock: str) -> str | None:
    """The `c` line as tmux itself renders it in `list-keys -T prefix`, or
    None when the key carries no binding at all.

    Matched on the key POSITION (the operand right after `-T prefix`), never
    on the text ` c ` appearing anywhere in the line: tmux's stock
    `display-menu` bindings embed bare `c` operands inside their own menu
    definitions, so a text search finds them too.
    """
    import re

    table = _sock_run(sock, "list-keys", "-T", "prefix").stdout
    pattern = re.compile(r"^bind-key\s+(?:-\S+\s+)*-T\s+prefix\s+c\s", re.M)
    for line in table.splitlines():
        if pattern.match(line):
            return line
    return None


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_unbind_gives_the_operator_their_own_prefix_c_binding_back(server):
    """The operator's `.tmux.conf` binding is not camp's to destroy.

    `new-window -c "#{pane_current_path}"` — opening the new window in the
    current pane's directory — is a near-ubiquitous customization of this
    exact key. camp overwrites the server-global table entry to install its
    own dispatch, so it must put back what was there, not tmux's
    compiled-in default. Asserted byte-for-byte against tmux's own
    `list-keys` rendering, on a real server, across a full install/unbind
    cycle.
    """
    from camp.launch.naming import workspace_session_name
    from camp.launch.tmux import Tmux
    from camp.launch.workspace_session import create_workspace_session

    _sock_run(server.sock, "new-session", "-d", "-s", "plainsess", "-x", "80", "-y", "24")
    _sock_run(
        server.sock, "bind-key", "-T", "prefix", "c", "new-window", "-c", "#{pane_current_path}"
    )
    before = _prefix_c_line(server.sock)
    assert before is not None and "pane_current_path" in before, before

    slug = "feat-x"
    ws_dir = server.workspace_dir(slug)
    ws_dir.mkdir(parents=True)
    create_workspace_session(server.group_name, slug, ws_dir, env=server.env, tmux=Tmux())
    camp_session = workspace_session_name(server.group_name, slug)

    during = _prefix_c_line(server.sock)
    assert during is not None and "pane_current_path" not in during, (
        f"camp's install must have taken the key over: {during!r}"
    )

    result = _run_camp_window_unbind(server)
    assert result.returncode == 0, result.stderr

    after = _prefix_c_line(server.sock)
    assert after == before, f"expected the operator's own binding back\nbefore: {before!r}\nafter:  {after!r}"

    # And it WORKS, not merely reads right: pressing the key in the camp
    # session now runs the operator's binding, so the window opens rooted at
    # the pane's own directory rather than being composed by camp.
    _attach_and_send(
        server.sock,
        camp_session,
        b"\x02c",
        settle=1.0,
        until=_window_count_reaches(server.sock, camp_session, 2),
    )
    windows = _sock_run(server.sock, "list-windows", "-t", camp_session).stdout.splitlines()
    assert len(windows) == 2, windows


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_a_second_workspace_does_not_overwrite_the_captured_operator_binding(server):
    """The capture happens on the FIRST install only. A second workspace on
    the same server re-issues the bind (so a stale one self-heals) and must
    not record camp's own binding as the thing to restore — which would make
    unbind hand the operator camp's dispatch back instead of their own
    binding, and permanently."""
    from camp.launch.tmux import Tmux
    from camp.launch.workspace_session import create_workspace_session

    # A session first: `bind-key` does NOT auto-start a tmux server (unlike
    # `new-session`), so setting the operator's binding against a socket with
    # no server is a silent no-op and would leave this test asserting nothing.
    _sock_run(server.sock, "new-session", "-d", "-s", "plainsess", "-x", "80", "-y", "24")
    _sock_run(
        server.sock, "bind-key", "-T", "prefix", "c", "new-window", "-c", "#{pane_current_path}"
    )
    before = _prefix_c_line(server.sock)
    assert before is not None and "pane_current_path" in before, before

    for slug in ("feat-x", "feat-y"):
        ws_dir = server.workspace_dir(slug)
        ws_dir.mkdir(parents=True)
        create_workspace_session(server.group_name, slug, ws_dir, env=server.env, tmux=Tmux())

    assert _run_camp_window_unbind(server).returncode == 0
    assert _prefix_c_line(server.sock) == before


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_unbind_leaves_the_key_unbound_when_the_operator_had_unbound_it(server):
    """The other end of the same contract: an operator who deliberately
    unbound this key gets it back UNBOUND, not restored to tmux's
    compiled-in default — "put it back how it was" has to mean that in both
    directions, or camp is still imposing a binding the operator removed."""
    from camp.launch.tmux import Tmux
    from camp.launch.workspace_session import create_workspace_session

    _sock_run(server.sock, "new-session", "-d", "-s", "plainsess", "-x", "80", "-y", "24")
    _sock_run(server.sock, "unbind-key", "-T", "prefix", "c")
    assert _prefix_c_line(server.sock) is None

    ws_dir = server.workspace_dir("feat-x")
    ws_dir.mkdir(parents=True)
    create_workspace_session(server.group_name, "feat-x", ws_dir, env=server.env, tmux=Tmux())
    assert _prefix_c_line(server.sock) is not None, "camp's install should have bound the key"

    assert _run_camp_window_unbind(server).returncode == 0
    assert _prefix_c_line(server.sock) is None


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_list_windows_reports_a_rename_a_running_command_and_a_killed_session(server, tmp_path):
    """`Tmux.list_windows` against a REAL server: a session with two
    windows — one renamed after creation, one running `sleep` in the
    foreground — lists both ids, the renamed window's new name, and the
    directory tmux itself reports; killing the session then answers `None`,
    not `UNANSWERED` and not an empty listing."""
    from camp.launch.tmux import Tmux

    name = "camp-e2e-list-windows"
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    tmux = Tmux()

    # A second, unrelated session keeps the SERVER alive once ours is
    # killed below — otherwise the last session's kill tears down the
    # whole server, and `list-windows` answers "no server running" instead
    # of the "can't find session" shape this test means to exercise.
    _sock_run(server.sock, "new-session", "-d", "-s", "keepalive")

    created = tmux.new_session(name, cwd=str(ws_dir), env=server.env, timeout=5)
    assert created.returncode == 0, created.stderr

    second = tmux.new_window(
        name, cwd=str(ws_dir), window_name="worker", command=["sleep", "30"]
    )
    assert second is not None and not isinstance(second, str), second

    first_before = _sock_run(server.sock, "list-windows", "-t", name, "-F", "#{window_id}")
    first_id = first_before.stdout.splitlines()[0]
    renamed = _sock_run(server.sock, "rename-window", "-t", first_id, "renamed-window")
    assert renamed.returncode == 0, renamed.stderr

    listing = tmux.list_windows(name)

    from camp.launch.tmux import WindowListing

    assert isinstance(listing, WindowListing), listing
    assert listing.dropped == 0
    by_name = {window.name: window for window in listing.windows}
    assert set(by_name) == {"renamed-window", "worker"}
    assert by_name["renamed-window"].window_id == first_id
    assert by_name["renamed-window"].current_path == str(ws_dir)
    assert by_name["worker"].current_command == "sleep"
    assert by_name["worker"].window_id == second.window_id

    _sock_run(server.sock, "kill-session", "-t", name)
    assert tmux.list_windows(name) is None
