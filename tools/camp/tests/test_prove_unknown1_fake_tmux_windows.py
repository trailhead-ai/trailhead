"""EPHEMERAL assumption probe — NOT part of the suite, delete before merge.

Resolves Unknown 1 of task/carry-session-state-into-camp-list-on-every-dispatch-axis:
whether the end-to-end fake tmux stub at
`tools/camp/tests/test_session_cli.py:256-345` can be given a `list-sessions` arm
reporting a per-session window count that VARIES with the fixture, without
rewriting the stub or reshaping the JSON table (`CAMP_FAKE_TMUX_TABLE_FILE`) in a
way that would break its existing consumers (`has-session`, `list-panes`,
`kill-session`, and `new-session`'s own write).

Design probed: the real stub is reused UNMODIFIED (imported as
`session_cli._TMUX_STUB`), plus one additive arm appended to a copy of it. The
additive arm reads window counts from a SECOND, optional sidecar file
(`CAMP_FAKE_TMUX_WINDOWS_FILE`, session-name -> int), never touching the shape of
the existing `CAMP_FAKE_TMUX_TABLE_FILE` table (session-name -> pane command
string). A session with no entry in the sidecar defaults to 1 window, so the
addition is backward compatible with every existing table produced by
`new-session`.

This is additive, not a reshape: `has-session` / `list-panes` / `kill-session`
keep reading `table[name]` as a plain string exactly as before. The probe proves
this two ways: (1) it drives `list-sessions` with two sessions carrying different
window counts and asserts both are reported faithfully; (2) it re-drives
`has-session`, `list-panes`, and `kill-session` against the SAME table the
`list-sessions` read just touched, proving the existing consumers are
unaffected.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SOURCE = Path(__file__).resolve().parent / "test_session_cli.py"
_spec = importlib.util.spec_from_file_location("camp_tests_session_cli_probe", _SOURCE)
assert _spec and _spec.loader, _SOURCE
_session_cli = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _session_cli
_spec.loader.exec_module(_session_cli)

# The additive `list-sessions` arm. Appended verbatim to the real, UNMODIFIED
# `_TMUX_STUB` text — nothing above this arm in the stub is touched.
_LIST_SESSIONS_ARM = '''
elif args and args[0] == "list-sessions":
    table = _table()
    windows_path = os.environ.get("CAMP_FAKE_TMUX_WINDOWS_FILE")
    windows = {}
    if windows_path and os.path.exists(windows_path):
        with open(windows_path) as handle:
            windows = json.load(handle)
    for name in table:
        count = windows.get(name, 1)
        # Window count first, name as the remainder: a session name may
        # legally carry a "|" (or, per the stub's own delimiter here, a tab)
        # on real tmux, so the count is not read out of the name's tail.
        print(f"{count}\\t{name}")
'''


def _stub_with_list_sessions() -> str:
    stub = _session_cli._TMUX_STUB
    assert "elif args and args[0] == \"kill-session\":" in stub, (
        "the real stub's shape changed under this probe — re-derive the splice point"
    )
    # Insert the new arm right after the existing kill-session block (the stub's
    # last arm), so every existing arm's text is byte-identical to the shipped
    # stub and only new code is appended.
    return stub + _LIST_SESSIONS_ARM


def test_list_sessions_reports_a_window_count_that_varies_with_the_fixture(tmp_path):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    tmux = bin_dir / "tmux"
    tmux.write_text(_stub_with_list_sessions(), encoding="utf-8")
    tmux.chmod(0o755)

    sessions_file = tmp_path / "sessions.tsv"
    sessions_file.write_text("", encoding="utf-8")
    argv_file = tmp_path / "argv.tsv"
    argv_file.write_text("", encoding="utf-8")
    table_file = tmp_path / "table.json"
    table_file.write_text("{}", encoding="utf-8")
    windows_file = tmp_path / "windows.json"

    env = {**os.environ}
    env["CAMP_FAKE_SESSIONS_FILE"] = str(sessions_file)
    env["CAMP_FAKE_TMUX_ARGV_FILE"] = str(argv_file)
    env["CAMP_FAKE_TMUX_TABLE_FILE"] = str(table_file)
    env["CAMP_FAKE_TMUX_WINDOWS_FILE"] = str(windows_file)

    def run(*args):
        return subprocess.run(
            [str(tmux), *args], capture_output=True, text=True, env=env, check=False
        )

    # Register two sessions through the UNMODIFIED new-session arm.
    r1 = run("new-session", "-d", "-s", "camp-mygroup-alpha", "-c", "/tmp/alpha", "sleep", "1")
    assert r1.returncode == 0, r1.stderr
    r2 = run("new-session", "-d", "-s", "camp-mygroup-beta", "-c", "/tmp/beta", "sleep", "1")
    assert r2.returncode == 0, r2.stderr

    # Give the two running sessions DIFFERENT window counts, set directly by
    # the fixture — camp does not create windows in this slice, so the test
    # harness must be able to stage a window count without a tmux command to
    # drive it through.
    windows_file.write_text(
        json.dumps({"camp-mygroup-alpha": 3, "camp-mygroup-beta": 1}), encoding="utf-8"
    )

    listed = run("list-sessions")
    assert listed.returncode == 0, listed.stderr
    rows = {}
    for line in listed.stdout.splitlines():
        count, name = line.split("\t", 1)
        rows[name] = int(count)

    assert rows == {"camp-mygroup-alpha": 3, "camp-mygroup-beta": 1}
    # The two running workspaces in one listing carry DIFFERENT counts — this
    # is exactly the CLI assertion Task 4's test contract needs; a hardcoded
    # count could not produce this result.
    assert rows["camp-mygroup-alpha"] != rows["camp-mygroup-beta"]


def test_existing_consumers_are_unaffected_by_the_additive_arm(tmp_path):
    """has-session / list-panes / kill-session, reading the SAME table a
    list-sessions call just touched, behave exactly as they do against the
    real, un-probed stub — proving the addition is additive, not a reshape."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    tmux = bin_dir / "tmux"
    tmux.write_text(_stub_with_list_sessions(), encoding="utf-8")
    tmux.chmod(0o755)

    sessions_file = tmp_path / "sessions.tsv"
    sessions_file.write_text("", encoding="utf-8")
    argv_file = tmp_path / "argv.tsv"
    argv_file.write_text("", encoding="utf-8")
    table_file = tmp_path / "table.json"
    table_file.write_text("{}", encoding="utf-8")

    env = {**os.environ}
    env["CAMP_FAKE_SESSIONS_FILE"] = str(sessions_file)
    env["CAMP_FAKE_TMUX_ARGV_FILE"] = str(argv_file)
    env["CAMP_FAKE_TMUX_TABLE_FILE"] = str(table_file)

    def run(*args):
        return subprocess.run(
            [str(tmux), *args], capture_output=True, text=True, env=env, check=False
        )

    run("new-session", "-d", "-s", "camp-mygroup-gamma", "-c", "/tmp/gamma", "sleep", "1")

    # list-sessions runs first (no windows sidecar set — must default cleanly).
    listed = run("list-sessions")
    assert listed.returncode == 0
    assert listed.stdout.strip() == "1\tcamp-mygroup-gamma"

    has = run("has-session", "-t", "camp-mygroup-gamma")
    assert has.returncode == 0

    panes = run("list-panes", "-t", "camp-mygroup-gamma")
    assert panes.returncode == 0
    assert panes.stdout.strip().endswith("sleep 1")

    killed = run("kill-session", "-t", "camp-mygroup-gamma")
    assert killed.returncode == 0

    has_after = run("has-session", "-t", "camp-mygroup-gamma")
    assert has_after.returncode == 1
