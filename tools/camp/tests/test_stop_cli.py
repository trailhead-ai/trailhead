"""Test contract: `camp stop <slug>` wired into the CLI.

Test contract (from
`task/camp-stop-slug-the-verb-and-its-end-to-end-proof`):

- `camp stop <slug>` on a running session prints the preview then
  `stopped <session>` on stdout, reconciliation lines on stderr, exit 0.
- `camp stop <slug>` on a workspace with no session prints
  `not running <session>`, exit 0, and no kill reaches the seam.
- `camp stop nosuch` prints the refusal naming `nosuch` on stderr, exit 1,
  and no tmux call is made.
- `camp stop` with no slug refuses naming the missing slug, exit 1, and
  reads nothing from stdin even when interactive.
- `camp stop <slug> --json` on the still-present arm prints one JSON object
  with `outcome: still-present` and exit 1; on success `outcome: stopped`
  and the preview fields; stdout parses as exactly one JSON value.
- A workspace slug that is also a reserved verb name cannot reach here
  (`test_verb_aliases.py`'s reservation test, extended with `stop`).
- Real tmux end to end (skipif-guarded, in
  `test_stop_cli_real_tmux.py`).

Every test drives the real entry point (`camp.cli.dispatch.main`), never
`_cmd_stop_cli` directly. No real tmux is touched here — the tmux seam is
`_ScriptedTmux`, borrowed from `test_stop_workspace.py`, the same fake the
engine's own unit tests use.
"""
from __future__ import annotations

import importlib
import json
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

from test_stop_workspace import _ScriptedTmux  # noqa: E402


def _dispatch_module():
    return importlib.import_module("camp.cli.dispatch")


def _cli_session_module():
    return importlib.import_module("camp.cli.session")


def _lifecycle_module():
    return importlib.import_module("camp.provision.lifecycle")


def _stop_module():
    return importlib.import_module("camp.cli.stop")


class _FakeTTY:
    """A stdin stand-in that answers `isatty()` true and raises on any read
    — `camp stop` with no slug must never reach a read call at all."""

    def isatty(self) -> bool:
        return True

    def readline(self):  # pragma: no cover - must never be called
        raise AssertionError("camp stop with no slug read from stdin")

    def read(self, *a, **k):  # pragma: no cover - must never be called
        raise AssertionError("camp stop with no slug read from stdin")


def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setenv("CAMP_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("CAMP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _run(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    dispatch = _dispatch_module()
    monkeypatch.setattr(sys, "argv", ["camp", *argv])
    try:
        dispatch.main()
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def _wire_one_workspace(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path: Path,
    tmux,
    slug: str = "camp-cli",
    group_name: str = "g",
) -> Path:
    """Wires a resolvable `--group g` carrying exactly one workspace
    (*slug*), pointing every `Tmux()` construction `cli/stop.py` makes at
    *tmux* — the caller's own `_ScriptedTmux` (or equivalent) — via the
    same `stop_module.Tmux` factory seam `cli/stop.py` reads. Mirrors
    `test_attach_door_dispatch.py`'s `_wire_one_workspace`.
    """
    ws = tmp_path / "state" / group_name / "worktrees" / slug
    ws.mkdir(parents=True, exist_ok=True)

    cli_session = _cli_session_module()
    lifecycle = _lifecycle_module()
    stop_cli = _stop_module()

    monkeypatch.setattr(cli_session, "_parsable_groups", lambda: [{"group": {"name": group_name}}])
    monkeypatch.setattr(stop_cli, "Tmux", lambda *a, **k: tmux)

    def fake_cmd_ls_group(group, *, env=None, tmux=None, **kw):
        return lifecycle.GroupListing(
            entries=[
                {
                    "slug": slug,
                    "workspace_path": str(ws),
                    "state": None,
                    "window_count": None,
                }
            ],
            unmanaged=[],
            unmanaged_count=0,
            notice=None,
        )

    monkeypatch.setattr(lifecycle, "cmd_ls_group", fake_cmd_ls_group)
    return ws


def _derived_name(group_name: str, slug: str) -> str:
    from camp.launch.naming import workspace_session_name

    return workspace_session_name(group_name, slug)


# ---------------------------------------------------------------------------
# Success: a running session
# ---------------------------------------------------------------------------


def test_running_session_prints_preview_then_stopped_reconcile_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.launch.tmux import TmuxWindow
    from camp.group.window_record import WindowEntry, window_record_path_for, write_window_record

    _isolated_env(tmp_path, monkeypatch)
    session = _derived_name("g", "camp-cli")

    # A record entry whose window is gone in the live listing, so the
    # reconciliation half of the ordering claim has something to print.
    ws = tmp_path / "state" / "g" / "worktrees" / "camp-cli"
    ws.mkdir(parents=True, exist_ok=True)
    write_window_record(
        window_record_path_for(ws),
        [WindowEntry(window_id="@9", name="gone", cwd=".", conversation_id="dead-conv")],
    )

    tmux = _ScriptedTmux(
        initial=True,
        listing_windows=(TmuxWindow(window_id="@1", current_path="/ws", current_command="bash", name="shell"),),
        poll_sequence=(False,),
    )
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, slug="camp-cli")

    code = _run(["stop", "camp-cli", "--group", "g"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 0
    assert "dropped @9" in captured.err
    # The preview (count line + rows) is printed exactly once: `emit`
    # streams it before the kill, and the outcome line printed afterward
    # must not re-render it a second time.
    assert captured.out.splitlines() == [
        f"stopping {session}: 1 window",
        '  @1 "shell"',
        f"stopped {session}",
    ]
    assert tmux.killed == [session]


def test_no_session_prints_not_running_exit_zero_no_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    session = _derived_name("g", "camp-cli")
    tmux = _ScriptedTmux(initial=False)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, slug="camp-cli")

    code = _run(["stop", "camp-cli", "--group", "g"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out.strip() == f"not running {session}"
    assert tmux.killed == []
    assert ("kill", session) not in tmux.calls


def test_nosuch_slug_refuses_naming_it_no_tmux_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    tmux = _ScriptedTmux(initial=True)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, slug="camp-cli")

    code = _run(["stop", "nosuch", "--group", "g"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert "nosuch" in captured.err
    assert "camp stop:" in captured.err
    assert tmux.calls == []


def test_nosuch_slug_json_refusal_prints_on_stdout_not_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    tmux = _ScriptedTmux(initial=True)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, slug="camp-cli")

    code = _run(["stop", "nosuch", "--group", "g", "--json"], monkeypatch)
    out = capsys.readouterr().out

    payload = json.loads(out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["outcome"] is None
    assert "nosuch" in payload["reason"]
    assert len(out.strip().splitlines()) == 1


def test_tmux_unanswered_refuses_naming_the_session_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    session = _derived_name("g", "camp-cli")
    tmux = _ScriptedTmux(initial=None)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, slug="camp-cli")

    code = _run(["stop", "camp-cli", "--group", "g"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert captured.out == ""
    assert "camp stop:" in captured.err
    assert session in captured.err
    assert "did not answer" in captured.err
    assert tmux.killed == []


def test_tmux_unanswered_refuses_as_one_json_object_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    session = _derived_name("g", "camp-cli")
    tmux = _ScriptedTmux(initial=None)
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, slug="camp-cli")

    code = _run(["stop", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert code == 1
    assert payload["ok"] is False
    assert payload["outcome"] is None
    assert session in payload["reason"]
    assert len(out.strip().splitlines()) == 1


@pytest.mark.parametrize(
    "make_tmux_and_record",
    ["corrupt", "unanswered_session", "no_such_session", "dropped_rows"],
)
def test_every_not_reconciled_reason_routes_to_stderr_under_json_stdout_still_one_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    make_tmux_and_record: str,
) -> None:
    from camp.launch.tmux import UNANSWERED, TmuxWindow, WindowListing
    from camp.group.window_record import WindowEntry, window_record_path_for, write_window_record

    _isolated_env(tmp_path, monkeypatch)
    ws = tmp_path / "state" / "g" / "worktrees" / "camp-cli"
    ws.mkdir(parents=True, exist_ok=True)

    fresh_listing = TmuxWindow(window_id="@1", current_path="/ws", current_command="bash", name="shell")

    if make_tmux_and_record == "corrupt":
        window_record_path_for(ws).write_text("{not json", encoding="utf-8")
        tmux = _ScriptedTmux(initial=True, listing_windows=(fresh_listing,), poll_sequence=(False,))
    elif make_tmux_and_record == "unanswered_session":
        tmux = _ScriptedTmux(
            initial=True, listing_sequence=[UNANSWERED], listing_windows=(fresh_listing,), poll_sequence=(False,)
        )
    elif make_tmux_and_record == "no_such_session":
        tmux = _ScriptedTmux(
            initial=True, listing_sequence=[None], listing_windows=(fresh_listing,), poll_sequence=(False,)
        )
    else:  # dropped_rows
        write_window_record(
            window_record_path_for(ws),
            [WindowEntry(window_id="@1", name="a", cwd=".", conversation_id="c")],
        )
        tmux = _ScriptedTmux(
            initial=True,
            listing_sequence=[WindowListing(windows=(fresh_listing,), dropped=1)],
            listing_windows=(fresh_listing,),
            poll_sequence=(False,),
        )

    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, slug="camp-cli")

    code = _run(["stop", "camp-cli", "--group", "g", "--json"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 0
    assert "not reconciled" in captured.err
    payload = json.loads(captured.out)
    assert payload["outcome"] == "stopped"
    assert len(captured.out.strip().splitlines()) == 1


def test_no_slug_refuses_naming_the_missing_slug_never_reads_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "stdin", _FakeTTY())

    code = _run(["stop"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 1
    assert "camp stop:" in captured.err
    assert "slug" in captured.err


def test_json_still_present_carries_outcome_and_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _isolated_env(tmp_path, monkeypatch)
    tmux = _ScriptedTmux(initial=True, poll_sequence=(True, True, True))
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, slug="camp-cli")

    code = _run(
        ["stop", "camp-cli", "--group", "g", "--json"], monkeypatch
    )
    out = capsys.readouterr().out

    payload = json.loads(out)
    assert code == 1
    assert payload["outcome"] == "still-present"
    # stdout parses as exactly one JSON value: exactly one non-empty line.
    assert len(out.strip().splitlines()) == 1


def test_unanswered_preview_listing_prints_could_not_list_not_zero_windows_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.launch.tmux import UNANSWERED, TmuxWindow, WindowListing

    _isolated_env(tmp_path, monkeypatch)
    session = _derived_name("g", "camp-cli")
    answered = WindowListing(
        windows=(TmuxWindow(window_id="@1", current_path="/ws", current_command="bash", name="shell"),)
    )
    tmux = _ScriptedTmux(listing_sequence=[answered, UNANSWERED], poll_sequence=(False,))
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, slug="camp-cli")

    code = _run(["stop", "camp-cli", "--group", "g"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out.splitlines() == [
        f"camp: could not list the windows of {session}; stopping without a preview",
        f"stopped {session}",
    ]
    assert "0 windows" not in captured.out


def test_unanswered_preview_listing_json_carries_null_windows_and_listed_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.launch.tmux import UNANSWERED, TmuxWindow, WindowListing

    _isolated_env(tmp_path, monkeypatch)
    answered = WindowListing(
        windows=(TmuxWindow(window_id="@1", current_path="/ws", current_command="bash", name="shell"),)
    )
    tmux = _ScriptedTmux(listing_sequence=[answered, UNANSWERED], poll_sequence=(False,))
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, slug="camp-cli")

    code = _run(["stop", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert code == 0
    assert payload["windows"] is None
    assert payload["listed"] is False
    assert len(out.strip().splitlines()) == 1


def test_a_dropped_listing_row_reason_reaches_stderr_and_kill_still_proceeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.launch.tmux import TmuxWindow, WindowListing

    _isolated_env(tmp_path, monkeypatch)
    session = _derived_name("g", "camp-cli")
    fresh = TmuxWindow(window_id="@1", current_path="/ws", current_command="bash", name="shell")
    tmux = _ScriptedTmux(
        listing_sequence=[WindowListing(windows=(fresh,), dropped=1)],
        listing_windows=(fresh,),
        poll_sequence=(False,),
    )
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, slug="camp-cli")

    code = _run(["stop", "camp-cli", "--group", "g"], monkeypatch)
    captured = capsys.readouterr()

    assert code == 0
    assert "not reconciled" in captured.err
    assert "camp: window record at " in captured.err
    assert tmux.killed == [session]


def test_json_stopped_carries_outcome_and_preview_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from camp.launch.tmux import TmuxWindow

    _isolated_env(tmp_path, monkeypatch)
    tmux = _ScriptedTmux(
        initial=True,
        listing_windows=(TmuxWindow(window_id="@1", current_path="/ws", current_command="bash", name="shell"),),
        poll_sequence=(False,),
    )
    _wire_one_workspace(monkeypatch, tmp_path=tmp_path, tmux=tmux, slug="camp-cli")

    code = _run(["stop", "camp-cli", "--group", "g", "--json"], monkeypatch)
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert code == 0
    assert payload["outcome"] == "stopped"
    assert payload["windows"] == 1
