"""Real-tmux end-to-end test for the door's resurrection arm
(task/resurrection-proven-end-to-end-on-real-tmux).

Drives a REAL tmux 3.7c server on a throwaway `-L` socket through
`redirected_tmux_socket`, borrowed from `test_stop_workspace_real_tmux.py` —
the same redirection `test_stop_cli_real_tmux.py` uses — rather than a
second copy of it: `_REAL_TMUX` is captured by absolute path at import time,
before the autouse `_sandbox_tmux` fixture in `conftest.py` rewrites `PATH`
to a no-server stub for the rest of the suite, and a thin `tmux` wrapper
first on `PATH` transparently redirects every call camp's production code
makes onto the isolated socket. The CLI harness (`_isolated_env`, `_run`
through the real entry point) is borrowed from `test_stop_cli.py` the same
way `test_stop_cli_real_tmux.py` borrows it.

A workspace's window record (three entries: two conversations, one command
line) is written directly with `write_window_record` — production code,
unmodified — with NO tmux session ever created for it, so `camp attach
<slug>` reaches the door's resurrect arm exactly as a real stopped
workspace would. Only `camp.launch.profile.harness_for` is faked, answering
a transcript path and a resume argv for the two conversation entries and a
small env-unset list — the harness boundary the resurrection planner reads;
tmux itself, `camp attach`, and `camp stop` are never faked. `_parsable_groups`
and `cmd_ls_group` are faked the same way `test_stop_cli_real_tmux.py` fakes
them, so the test needs no real group config on disk.
"""

from __future__ import annotations

import importlib
import shutil
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

from test_stop_cli import _isolated_env, _run  # noqa: E402
from test_stop_workspace_real_tmux import (  # noqa: E402
    _REAL_TMUX,
    _sock_run,
    redirected_tmux_socket,
)

CONV_A = "8f2c1a3e-aaaa-bbbb-cccc-111122223333"
CONV_B = "41aa7c02-aaaa-bbbb-cccc-222233334444"
CMD_LINE = "pytest -x tests/"


@pytest.fixture()
def real_tmux_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """This module's throwaway real tmux server, on its own socket name —
    the same redirection `test_stop_cli_real_tmux.py` uses."""
    yield from redirected_tmux_socket(tmp_path, monkeypatch, "camp_resurrect_e2e")


class _FakeHarness:
    """Stand-in for the trailhead harness seam.

    Answers the three methods the resurrection planner reads
    (`session_transcript_path`, `session_resume`, `session_launch_env_unset`,
    per the task's scope), plus the small surface `camp attach`'s own
    session-pool build reads on every invocation before it ever reaches the
    door (`session_enumerate`/`parse_session_list`/`session_transcripts`/
    `session_launch_env_set`) — real production code that runs whether or
    not resurrection is in play, so it must not die on a harness that only
    implements the three methods the design doc names.
    """

    name = "fakeharness"

    def __init__(self, *, transcripts: dict, resumes: dict, scrub=("FAKE_TOKEN",)) -> None:
        self._transcripts = dict(transcripts)
        self._resumes = dict(resumes)
        self._scrub = tuple(scrub)

    # -- what the resurrection planner reads --------------------------------

    def session_launch_env_unset(self):
        return list(self._scrub)

    def session_transcript_path(self, session_id, workspace, *, env=None):
        return self._transcripts.get(session_id)

    def session_resume(self, session_id):
        return self._resumes.get(session_id)

    # -- what `camp attach`'s pool build reads on every call ----------------

    def session_enumerate(self, workspace):
        return [sys.executable, "-c", "pass"]

    def parse_session_list(self, stdout):
        return []

    def session_transcripts(self, workspace=None, *, env=None):
        return []

    def session_launch_env_set(self, account, *, env=None):
        return {}


def _wire_workspace(
    monkeypatch: pytest.MonkeyPatch, *, ws_dir: Path, slug: str, group_name: str, harness: _FakeHarness
) -> None:
    cli_session = importlib.import_module("camp.cli.session")
    lifecycle = importlib.import_module("camp.provision.lifecycle")
    profile = importlib.import_module("camp.launch.profile")

    monkeypatch.setattr(cli_session, "_parsable_groups", lambda: [{"group": {"name": group_name}}])
    monkeypatch.setattr(profile, "harness_for", lambda group: harness)

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


def _default_harness(**overrides) -> _FakeHarness:
    transcripts = {
        CONV_A: Path("/fake/transcripts") / f"{CONV_A}.jsonl",
        CONV_B: Path("/fake/transcripts") / f"{CONV_B}.jsonl",
    }
    resumes = {
        CONV_A: ["claude", "--resume", CONV_A],
        CONV_B: ["claude", "--resume", CONV_B],
    }
    kwargs = {"transcripts": transcripts, "resumes": resumes}
    kwargs.update(overrides)
    return _FakeHarness(**kwargs)


def _three_entries():
    from camp.group.window_record import WindowEntry

    return [
        WindowEntry(window_id="@100", name="conv-a", cwd=".", conversation_id=CONV_A),
        WindowEntry(window_id="@101", name="conv-b", cwd=".", conversation_id=CONV_B),
        WindowEntry(window_id="@102", name="cmd-c", cwd=".", command_line=CMD_LINE),
    ]


def _wait_for_shell(sock: str, window_id: str, shell_names: frozenset, timeout: float = 5.0) -> str:
    """Poll `#{pane_current_command}` for *window_id* until it names a
    shell — capture-pane races the stub's `exec`, so a fixed sleep would be
    both slow and occasionally wrong."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        result = _sock_run(sock, "display-message", "-p", "-t", window_id, "#{pane_current_command}")
        if result.returncode == 0:
            last = result.stdout.strip()
            if last in shell_names:
                return last
        time.sleep(0.05)
    raise AssertionError(f"window {window_id!r} never reached a shell (last saw {last!r})")


def _capture_pane(sock: str, window_id: str) -> str:
    result = _sock_run(sock, "capture-pane", "-p", "-t", window_id)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _list_windows(sock: str, session: str) -> list[list[str]]:
    result = _sock_run(
        sock, "list-windows", "-t", f"={session}", "-F", "#{window_id}\t#{window_name}\t#{pane_current_path}"
    )
    assert result.returncode == 0, result.stderr
    return [line.split("\t") for line in result.stdout.splitlines()]


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_resurrection_brings_back_three_windows_and_reconciles_clean_on_the_next_attach(
    real_tmux_socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.group.window_record import read_window_record, window_record_path_for
    from camp.launch.naming import workspace_session_name
    from camp.launch.stop_workspace import _COMMON_SHELL_BASENAMES

    sock = real_tmux_socket
    group_name = "g"
    slug = "camp-cli"
    session = workspace_session_name(group_name, slug)

    _isolated_env(tmp_path, monkeypatch)

    ws_dir = tmp_path / "state" / group_name / "worktrees" / slug
    ws_dir.mkdir(parents=True)

    from camp.group.window_record import write_window_record

    write_window_record(window_record_path_for(ws_dir), _three_entries())

    _wire_workspace(monkeypatch, ws_dir=ws_dir, slug=slug, group_name=group_name, harness=_default_harness())

    # -- first attach: no session exists, the record has three entries ------
    code = _run(["attach", slug, "--group", group_name], monkeypatch)
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out.strip() == f"resurrected {session} (3 windows)"
    # The one-time key-binding notice fires on the first session a server
    # ever creates, regardless of arm — unrelated to resurrection itself
    # (see `launch/binding.py`); nothing that failed or was dropped is on
    # stderr, which is what this window record's own report claims.
    assert "dropped" not in captured.err
    assert "did not come back" not in captured.err

    rows = _list_windows(sock, session)
    assert len(rows) == 3, "the resurrected session must hold exactly three windows"
    names = [row[1] for row in rows]
    dirs = [row[2] for row in rows]
    assert names == ["conv-a", "conv-b", "cmd-c"], "record order must be preserved"
    assert dirs == [str(ws_dir.resolve())] * 3

    window_ids = [row[0] for row in rows]
    first_id, _second_id, third_id = window_ids

    for window_id in window_ids:
        _wait_for_shell(sock, window_id, _COMMON_SHELL_BASENAMES)

    first_pane = _capture_pane(sock, first_id)
    assert f"camp: this window held conversation {CONV_A}" in first_pane
    assert f"camp: resume it with: claude --resume {CONV_A}" in first_pane

    third_pane = _capture_pane(sock, third_id)
    assert f"camp: this window was opened with: {CMD_LINE}" in third_pane

    # -- the record after resurrection holds the ids tmux just assigned -----
    after = read_window_record(window_record_path_for(ws_dir))
    assert {e.window_id for e in after.entries} == set(window_ids)

    # -- a second attach is the connect arm: reconciliation drops nothing ---
    code2 = _run(["attach", slug, "--group", group_name], monkeypatch)
    captured2 = capsys.readouterr()

    assert code2 == 0
    assert captured2.err == "", "reconciliation must drop nothing (AC43)"
    assert captured2.out.strip() == f"connected {session}"

    _sock_run(sock, "kill-server")


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_resurrection_drops_a_missing_directory_and_resurrects_the_rest(
    real_tmux_socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.group.window_record import WindowEntry, write_window_record, window_record_path_for
    from camp.launch.naming import workspace_session_name

    sock = real_tmux_socket
    group_name = "g"
    slug = "camp-cli"
    session = workspace_session_name(group_name, slug)

    _isolated_env(tmp_path, monkeypatch)

    ws_dir = tmp_path / "state" / group_name / "worktrees" / slug
    ws_dir.mkdir(parents=True)
    (ws_dir / "review").mkdir()

    entries = [
        WindowEntry(window_id="@200", name="conv-a", cwd=".", conversation_id=CONV_A),
        WindowEntry(window_id="@201", name="review", cwd="review", conversation_id=CONV_B),
        WindowEntry(window_id="@202", name="cmd-c", cwd=".", command_line=CMD_LINE),
    ]
    write_window_record(window_record_path_for(ws_dir), entries)

    # The directory a recorded entry points at is removed AFTER the record
    # was written, before the attach that must drop it.
    shutil.rmtree(ws_dir / "review")

    _wire_workspace(monkeypatch, ws_dir=ws_dir, slug=slug, group_name=group_name, harness=_default_harness())

    code = _run(["attach", slug, "--group", group_name], monkeypatch)
    captured = capsys.readouterr()

    assert code == 0
    assert "dropped" in captured.err
    assert "@201" in captured.err
    assert CONV_B in captured.err
    assert captured.out.strip() == f"resurrected {session} (2 windows; 1 dropped)"

    rows = _list_windows(sock, session)
    assert len(rows) == 2

    _sock_run(sock, "kill-server")


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_camp_stop_previews_a_resurrected_session_and_the_record_survives_with_restamped_ids(
    real_tmux_socket: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.group.window_record import read_window_record, window_record_path_for, write_window_record
    from camp.launch.naming import workspace_session_name
    from camp.launch.stop_workspace import _COMMON_SHELL_BASENAMES

    sock = real_tmux_socket
    group_name = "g"
    slug = "camp-cli"
    session = workspace_session_name(group_name, slug)

    _isolated_env(tmp_path, monkeypatch)

    ws_dir = tmp_path / "state" / group_name / "worktrees" / slug
    ws_dir.mkdir(parents=True)

    write_window_record(window_record_path_for(ws_dir), _three_entries())

    _wire_workspace(monkeypatch, ws_dir=ws_dir, slug=slug, group_name=group_name, harness=_default_harness())

    code = _run(["attach", slug, "--group", group_name], monkeypatch)
    captured = capsys.readouterr()
    assert code == 0, captured.err

    rows = _list_windows(sock, session)
    window_ids = [row[0] for row in rows]
    for window_id in window_ids:
        _wait_for_shell(sock, window_id, _COMMON_SHELL_BASENAMES)

    resurrected_ids = {e.window_id for e in read_window_record(window_record_path_for(ws_dir)).entries}
    assert resurrected_ids == set(window_ids)

    code2 = _run(["stop", slug, "--group", group_name], monkeypatch)
    captured2 = capsys.readouterr()

    assert code2 == 0
    assert captured2.out.startswith(f"stopping {session}:")
    assert captured2.out.strip().splitlines()[-1] == f"stopped {session}"
    assert f"conversation {CONV_A}  exited" in captured2.out
    assert f"conversation {CONV_B}  exited" in captured2.out

    after_has_session = _sock_run(sock, "has-session", "-t", f"={session}")
    assert after_has_session.returncode != 0

    after_record = read_window_record(window_record_path_for(ws_dir))
    assert {e.window_id for e in after_record.entries} == resurrected_ids

    _sock_run(sock, "kill-server")
