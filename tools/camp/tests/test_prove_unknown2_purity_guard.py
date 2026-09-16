"""EPHEMERAL assumption probe — NOT part of the suite, delete before merge.

Resolves Unknown 2 of task/carry-session-state-into-camp-list-on-every-dispatch-axis:
whether any existing assertion holds `camp list` to "no subprocess at all" rather
than the weaker "no harness exec". `camp.provision.lifecycle.cmd_ls_group` is
monkeypatched to perform a REAL subprocess call (a tmux-shaped read) before
returning its normal answer, then the actual `camp list` CLI entry point
(`camp.cli.workspace._cmd_ls_group_cli`) is invoked exactly as
`tools/camp/tests/test_camp_list.py::TestListEmpty` invokes it. If any guard in
the suite polices "no subprocess", this call would raise or the surrounding
fixture would fail. It does not — the call completes, prints the same output,
and exits cleanly, which is the observable behaviour that answers the unknown.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def test_cmd_ls_group_performing_a_real_subprocess_read_is_not_blocked(
    tmp_path, capsys, monkeypatch
):
    workspace = importlib.import_module("camp.cli.workspace")
    lifecycle = importlib.import_module("camp.provision.lifecycle")

    real_cmd_ls_group = lifecycle.cmd_ls_group
    subprocess_calls = []

    def _cmd_ls_group_with_a_tmux_shaped_subprocess_read(group, *, env=None):
        # Stand in for the tmux `list-sessions` read Task 4 wires in: a real
        # subprocess invocation, not a mock, executed before the normal
        # (empty-group) answer is returned.
        completed = subprocess.run(
            [sys.executable, "-c", "print('camp-mygroup-nosuch\\t0')"],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess_calls.append(completed.stdout)
        return real_cmd_ls_group(group, env=env)

    monkeypatch.setattr(
        lifecycle, "cmd_ls_group", _cmd_ls_group_with_a_tmux_shaped_subprocess_read
    )
    # workspace.py imports cmd_ls_group locally inside the function body
    # (`from ..provision.lifecycle import cmd_ls_group, render_workspace_list`),
    # so patching the lifecycle module attribute is what the real call resolves
    # through — not a module-level name in workspace.py.

    group = {
        "group": {"name": "listgrp"},
        "members": [],
        "branch": {"pattern": "worktree-{slug}"},
    }
    env = {"CAMP_STATE_DIR": str(tmp_path / "state")}

    workspace._cmd_ls_group_cli([], group, env)

    out = capsys.readouterr().out
    assert out == "", f"empty group must still produce no stdout, got {out!r}"
    assert subprocess_calls == ["camp-mygroup-nosuch\t0\n"], (
        "the subprocess read must actually have run — this is the observable "
        "evidence that nothing in the CLI path blocked it"
    )
