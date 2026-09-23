"""Real-tmux end-to-end test for `camp stop <slug>`
(task/camp-stop-slug-the-verb-and-its-end-to-end-proof).

Drives a REAL tmux 3.7c server on a throwaway `-L` socket through
`redirected_tmux_socket`, borrowed from `test_stop_workspace_real_tmux.py`
— the engine's own real-tmux test — rather than a second copy of it: it
captures `_REAL_TMUX` by absolute path at import time, before the
autouse `_sandbox_tmux` fixture in `conftest.py` rewrites `PATH` to a
no-server stub for the rest of the suite, and puts a thin `tmux` wrapper
first on `PATH` that transparently redirects every call camp's production
code makes onto the isolated socket. The CLI harness (`_isolated_env`,
`_run` through the real entry point) is borrowed from `test_stop_cli.py`
the same way.

A window is opened directly in tmux and written into the workspace's
window record (`record_window_entry`, production code), then closed
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

from test_stop_cli import _isolated_env, _run  # noqa: E402
from test_stop_workspace_real_tmux import (  # noqa: E402
    _REAL_TMUX,
    _sock_run,
    redirected_tmux_socket,
)


@pytest.fixture()
def real_tmux_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """This module's throwaway real tmux server, on its own socket name —
    the same redirection the engine's real-tmux test uses."""
    yield from redirected_tmux_socket(tmp_path, monkeypatch, "camp_stop_cli_e2e")


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
    from camp.group.window_record import (
        WindowEntry,
        read_window_record,
        record_window_entry,
        window_record_path_for,
    )

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

    # A second window, recorded in the workspace's window record.
    opened = _sock_run(
        sock, "new-window", "-P", "-F", "#{window_id}", "-t", f"={session}", "-n", "work",
        "-c", str(ws_dir), "sleep 100000",
    )
    assert opened.returncode == 0, opened.stderr
    closed_window_id = opened.stdout.strip()
    record_window_entry(
        ws_dir,
        WindowEntry(window_id=closed_window_id, name="work", cwd=".", command_line="sleep 100000"),
    )

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
