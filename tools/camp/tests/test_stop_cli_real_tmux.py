"""Real-tmux end-to-end test for `camp stop <slug>`
(task/camp-stop-slug-the-verb-and-its-end-to-end-proof).

Drives a REAL tmux 3.7c server on a throwaway `-L` socket — the same
redirection trick `test_stop_workspace_real_tmux.py` and
`test_window_binding_end_to_end.py` use: `_REAL_TMUX` is captured by
absolute path at import time, before the autouse `_sandbox_tmux` fixture in
`conftest.py` rewrites `PATH` to a no-server stub for the rest of the
suite, and a thin `tmux` wrapper placed first on `PATH` transparently
redirects every call camp's production code makes onto the isolated
socket.

A window is opened through `compose_window` (production code, unmodified,
with an explicit `command=` so no harness config is needed), then closed
directly in tmux — exactly the "a window closed in tmux since the record
was last written" scenario the design doc's reconciliation states
describe — before `camp stop <slug>` is driven through the REAL CLI entry
point (`camp.cli.dispatch.main`), never `stop_workspace` or `_cmd_stop_cli`
directly. Only the group/workspace listing is faked (`cmd_ls_group`,
`_parsable_groups`) — the same seam `test_attach_door_dispatch.py` fakes
for its own real-tmux-adjacent dispatch tests — so the test needs no real
group config on disk; tmux itself is the one thing under test that is
never faked.
"""

from __future__ import annotations

import importlib
import os
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


def _sock_run(sock: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_REAL_TMUX, "-L", sock, *args], capture_output=True, text=True, timeout=5
    )


@pytest.fixture()
def real_tmux_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A throwaway real tmux server, reached by every `tmux` call this
    process's production code makes — a wrapper first on `PATH` redirects
    it onto an isolated `-L` socket, exactly like
    `test_stop_workspace_real_tmux.py`.
    """
    sock = f"camp_stop_cli_e2e_{os.getpid()}_{id(object())}"
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


def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _run(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    dispatch = importlib.import_module("camp.cli.dispatch")
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    try:
        dispatch.main()
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def _wire_workspace(monkeypatch: pytest.MonkeyPatch, *, ws_dir: Path, slug: str, group_name: str) -> None:
    cli_session = importlib.import_module("camp.cli.session")
    lifecycle = importlib.import_module("camp.provision.lifecycle")

    monkeypatch.setattr(cli_session, "_parsable_groups", lambda: [{"group": {"name": group_name}}])

    def fake_cmd_ls_group(group, *, env=None, tmux=None, **kw):
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
def test_camp_stop_kills_a_real_session_reports_the_dropped_window_and_updates_the_record(
    real_tmux_socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.launch.naming import workspace_session_name
    from camp.launch.window_compose import compose_window
    from camp.group.window_record import read_window_record, window_record_path_for

    sock = real_tmux_socket
    group_name = "g"
    slug = "camp-cli"
    session = workspace_session_name(group_name, slug)

    _isolated_env(tmp_path, monkeypatch)

    ws_dir = tmp_path / "state" / group_name / "worktrees" / slug
    ws_dir.mkdir(parents=True)

    # The session's own first window (an idle shell) — created directly in
    # tmux, exactly like the engine's own real-tmux test.
    created = _sock_run(sock, "new-session", "-d", "-s", session, "-n", "shell")
    assert created.returncode == 0, created.stderr

    # A second window, opened through `compose_window` (production code,
    # unmodified) — an explicit command, so no harness config is needed —
    # which records it into the workspace's window record.
    entry = compose_window(
        {"group": {"name": group_name}},
        slug,
        ws_dir,
        cwd=ws_dir,
        window_name="work",
        command=["sleep", "100000"],
    )
    closed_window_id = entry.window_id

    before = read_window_record(window_record_path_for(ws_dir))
    assert any(e.window_id == closed_window_id for e in before.entries)

    # Close it in tmux since the record was last written — the
    # reconciliation state the design doc describes as "A window closed in
    # tmux is dropped from the record".
    closed = _sock_run(sock, "kill-window", "-t", f"{session}:{closed_window_id}")
    assert closed.returncode == 0, closed.stderr

    _wire_workspace(monkeypatch, ws_dir=ws_dir, slug=slug, group_name=group_name)

    code = _run(["stop", slug, "--group", group_name], monkeypatch)
    captured = capsys.readouterr()

    assert code == 0
    assert "dropped" in captured.err
    assert closed_window_id in captured.err
    assert captured.out.startswith(f"stopping {session}:")
    assert captured.out.strip().splitlines()[-1] == f"stopped {session}"

    after_has_session = _sock_run(sock, "has-session", "-t", f"={session}")
    assert after_has_session.returncode != 0

    after_record = read_window_record(window_record_path_for(ws_dir))
    assert not any(e.window_id == closed_window_id for e in after_record.entries)
