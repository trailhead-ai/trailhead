"""Tests for `resolve_session_environment` — the removals and assignments a
workspace's tmux session states once, so every pane in it starts on the
group's account with the harness's parent-session markers gone.

Driven against the REAL `ClaudeCodeHarness` (a group with no `[harness]`
block resolves `binary = "claude"`, which `trailhead.harness.get_harness`
answers with a pure registry lookup), so the variable names asserted here
are the harness's own, never retyped from its module.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _group(account: str | None = None, *, binary: str | None = None) -> dict:
    group: dict = {"group": {"name": "g"}, "members": []}
    if account is not None:
        group["launch"] = {"account": account}
    if binary is not None:
        group["harness"] = {"binary": binary}
    return group


def _claude():
    from trailhead.harness import get_harness

    return get_harness("claude")


def test_a_declared_account_is_assigned_and_every_other_scrubbed_name_is_removed(tmp_path):
    from camp.launch.session import resolve_session_environment

    harness = _claude()
    account = str(tmp_path / "acct")
    env = {"HOME": str(tmp_path)}

    stated = resolve_session_environment(harness, _group(account), env)

    binding = harness.session_launch_env_set(account, env=env)
    assert dict(stated.assignments) == binding
    assert binding, "a declared account must bind to at least one variable"
    scrub = set(harness.session_launch_env_unset())
    assert set(stated.removals) == scrub - set(binding)


def test_no_declared_account_removes_every_scrubbed_name_and_assigns_nothing(tmp_path):
    from camp.launch.session import resolve_session_environment

    harness = _claude()

    stated = resolve_session_environment(harness, _group(), {"HOME": str(tmp_path)})

    assert dict(stated.assignments) == {}
    assert set(stated.removals) == set(harness.session_launch_env_unset())


def test_the_assignment_follows_the_declared_account(tmp_path):
    from camp.launch.session import resolve_session_environment

    harness = _claude()
    env = {"HOME": str(tmp_path)}

    a = resolve_session_environment(harness, _group(str(tmp_path / "a")), env)
    b = resolve_session_environment(harness, _group(str(tmp_path / "b")), env)

    assert dict(a.assignments) != dict(b.assignments)


def test_no_harness_and_no_account_states_nothing(tmp_path):
    from camp.launch.session import resolve_session_environment

    stated = resolve_session_environment(None, _group(), {"HOME": str(tmp_path)})

    assert stated.removals == ()
    assert stated.assignments == ()


def test_no_harness_with_a_declared_account_refuses_rather_than_dropping_the_account(tmp_path):
    from camp.launch.session import LaunchError, resolve_session_environment

    with pytest.raises(LaunchError, match="acct"):
        resolve_session_environment(
            None, _group(str(tmp_path / "acct"), binary="unknown-harness"), {"HOME": str(tmp_path)}
        )


def test_no_group_states_nothing(tmp_path):
    from camp.launch.session import resolve_session_environment

    stated = resolve_session_environment(_claude(), None, {"HOME": str(tmp_path)})

    assert stated.removals == ()
    assert stated.assignments == ()
