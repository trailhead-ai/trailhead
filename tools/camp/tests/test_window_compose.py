"""Tests for camp.launch.window_compose — the verb that composes a window
inside a workspace's tmux session and records it (AC19, AC20).

Test contract (see
task/composing-a-window-camp-chooses-the-conversation-id-and-records-it-at-creation):

1. The recorded conversation id is the one camp passed to the harness — vary
   it and the recorded value follows.
2. The recorded working directory is the workspace-relative form of where
   the window was actually rooted; vary the directory and the recorded
   value follows.
3. The recorded tmux window id is the one the seam reported, not a
   predicted or sequential value.
4. The recorded name is the window's actual name in tmux, not a value
   recorded in parallel with whatever tmux was told — vary the name and the
   recorded value follows; the name camp recorded and the name tmux holds
   cannot diverge.
5. A window created with an explicit command records that command line and
   no conversation id; a window created as a conversation records the id
   and no command line. Both directions pinned.
6. The composed argv contains neither the remote-control flag nor the
   visible-name flag.
7. The scrub appears inside the command the window runs.
8. The record is written before the invocation reports success, and a
   window is recorded exactly once per creation.

Every test drives `compose_window` against a fake `Tmux` double (so the
argv `new_window` is called with, and what it echoes back, are both fully
controlled) and a fake harness (so the scrub set is controlled) — never a
real subprocess. `camp.launch.tmux`'s own suite pins the real argv/parse
against a `subprocess.run` stand-in and (manually, this task) against a real
tmux 3.7c binary; this file pins only the composition logic layered on top.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

GROUP = {"group": {"name": "testgroup"}}
GROUP_ACCOUNT_A = {
    "group": {"name": "testgroup"},
    "launch": {"account": "/tmp/acct-a"},
}
GROUP_ACCOUNT_B = {
    "group": {"name": "testgroup"},
    "launch": {"account": "/tmp/acct-b"},
}


class FakeHarness:
    """Stand-in for the trailhead harness seam — the scrub method AND the
    account-binding method `resolve_launch_environment` calls."""

    def __init__(
        self,
        scrub=("SCRUB_ONE", "SCRUB_TWO", "CLAUDE_CONFIG_DIR"),
        account_var="CLAUDE_CONFIG_DIR",
    ):
        self._scrub = tuple(scrub)
        self._account_var = account_var

    def session_launch_env_unset(self):
        return list(self._scrub)

    def session_launch_env_set(self, account, *, env=None):
        if account is None:
            return {}
        return {self._account_var: account}


class FakeTmux:
    """Stand-in for `camp.launch.tmux.Tmux` — records every `new_window`
    call verbatim and answers with a canned (or diverging) result."""

    def __init__(self, *, window_id="@1", window_name_override=None):
        self._window_id = window_id
        self._window_name_override = window_name_override
        self.calls: list[dict] = []

    def new_window(self, name, *, cwd, window_name, command, timeout=None):
        from camp.launch.tmux import NewWindowResult

        self.calls.append(
            {
                "name": name,
                "cwd": cwd,
                "window_name": window_name,
                "command": list(command),
            }
        )
        actual_name = (
            self._window_name_override
            if self._window_name_override is not None
            else window_name
        )
        return NewWindowResult(window_id=self._window_id, window_name=actual_name)


def _recorded_entries(ws_dir: Path):
    from camp.group.window_record import read_window_record, window_record_path_for

    return read_window_record(window_record_path_for(ws_dir)).entries


@pytest.fixture(autouse=True)
def _fake_harness(monkeypatch):
    import camp.launch.window_compose as wc

    monkeypatch.setattr(wc, "harness_for", lambda group: FakeHarness())


# ---------------------------------------------------------------------------
# 1. Conversation id
# ---------------------------------------------------------------------------


def test_recorded_conversation_id_is_the_one_passed_to_the_harness(tmp_path, monkeypatch):
    import camp.launch.window_compose as wc

    ids = iter(["conv-aaaa", "conv-bbbb"])
    monkeypatch.setattr(wc.uuid, "uuid4", lambda: next(ids))

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux1 = FakeTmux()
    result1 = wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=tmux1
    )
    tmux2 = FakeTmux()
    result2 = wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w2", tmux=tmux2
    )

    # The id recorded is the id that reached the composed argv — not two
    # independently-generated values that merely happen to agree.
    assert "conv-aaaa" in tmux1.calls[0]["command"]
    assert "conv-bbbb" in tmux2.calls[0]["command"]
    assert result1.conversation_id == "conv-aaaa"
    assert result2.conversation_id == "conv-bbbb"
    assert result1.conversation_id != result2.conversation_id

    entries = _recorded_entries(ws_dir)
    assert entries[0].conversation_id == "conv-aaaa"
    assert entries[1].conversation_id == "conv-bbbb"


# ---------------------------------------------------------------------------
# 2. Working directory
# ---------------------------------------------------------------------------


def test_recorded_cwd_is_the_workspace_relative_form_of_the_actual_root(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    (ws_dir / "sub_a").mkdir(parents=True)
    (ws_dir / "sub_b").mkdir(parents=True)

    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir / "sub_a", window_name="w1", tmux=FakeTmux()
    )
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir / "sub_b", window_name="w2", tmux=FakeTmux()
    )

    entries = _recorded_entries(ws_dir)
    assert entries[0].cwd == "sub_a"
    assert entries[1].cwd == "sub_b"
    assert entries[0].cwd != entries[1].cwd


# ---------------------------------------------------------------------------
# 3. tmux window id
# ---------------------------------------------------------------------------


def test_recorded_window_id_is_the_one_the_seam_reported(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1",
        tmux=FakeTmux(window_id="@42"),
    )
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w2",
        tmux=FakeTmux(window_id="@99"),
    )

    entries = _recorded_entries(ws_dir)
    assert entries[0].window_id == "@42"
    assert entries[1].window_id == "@99"


# ---------------------------------------------------------------------------
# 4. Window name — read back, cannot diverge
# ---------------------------------------------------------------------------


def test_recorded_name_follows_the_requested_name_when_tmux_honors_it(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="alpha", tmux=FakeTmux()
    )
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="beta", tmux=FakeTmux()
    )

    entries = _recorded_entries(ws_dir)
    assert entries[0].name == "alpha"
    assert entries[1].name == "beta"


def test_recorded_name_is_tmuxs_answer_not_the_requested_name_when_they_diverge(
    tmp_path,
):
    """tmux is free to alter a requested name (e.g. de-duplication); the
    recorded name must be what tmux reported back, never the request."""
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux = FakeTmux(window_name_override="alpha (1)")
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="alpha", tmux=tmux
    )

    entries = _recorded_entries(ws_dir)
    assert entries[0].name == "alpha (1)"
    assert entries[0].name != "alpha"


# ---------------------------------------------------------------------------
# 5. Explicit command vs. conversation — both directions
# ---------------------------------------------------------------------------


def test_explicit_command_records_the_command_line_and_no_conversation_id(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1",
        command=["sleep", "30"], tmux=FakeTmux(),
    )

    entries = _recorded_entries(ws_dir)
    assert entries[0].conversation_id is None
    assert entries[0].command_line == "sleep 30"


def test_conversation_window_records_the_id_and_no_command_line(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=FakeTmux()
    )

    entries = _recorded_entries(ws_dir)
    assert entries[0].conversation_id is not None
    assert entries[0].command_line is None


# ---------------------------------------------------------------------------
# 6. No remote-control, no visible-name flag
# ---------------------------------------------------------------------------


def test_composed_argv_carries_neither_remote_control_nor_name_flag(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux = FakeTmux()
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=tmux
    )

    command = tmux.calls[0]["command"]
    assert "--remote-control" not in command
    assert "--name" not in command


# ---------------------------------------------------------------------------
# 7. Scrub inside the command the window runs
# ---------------------------------------------------------------------------


def test_scrub_rides_inside_the_command_tmux_runs(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux = FakeTmux()
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=tmux
    )

    command = tmux.calls[0]["command"]
    assert command[0] == "env"
    for var in ("SCRUB_ONE", "SCRUB_TWO"):
        idx = command.index(var)
        assert command[idx - 1] == "-u"


# ---------------------------------------------------------------------------
# 7b. Account binding — the group's declared account, from the one resolver
# ---------------------------------------------------------------------------


def test_two_groups_declaring_different_accounts_produce_different_assignments(
    tmp_path,
):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux_a = FakeTmux()
    wc.compose_window(
        GROUP_ACCOUNT_A, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=tmux_a
    )
    tmux_b = FakeTmux()
    wc.compose_window(
        GROUP_ACCOUNT_B, "slug", ws_dir, cwd=ws_dir, window_name="w2", tmux=tmux_b
    )

    assert "CLAUDE_CONFIG_DIR=/tmp/acct-a" in tmux_a.calls[0]["command"]
    assert "CLAUDE_CONFIG_DIR=/tmp/acct-b" in tmux_b.calls[0]["command"]


def test_a_group_declaring_no_account_produces_no_assignment_but_still_scrubs_it(
    tmp_path,
):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux = FakeTmux()
    wc.compose_window(GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=tmux)

    command = tmux.calls[0]["command"]
    assert not any(token.startswith("CLAUDE_CONFIG_DIR=") for token in command)
    idx = command.index("CLAUDE_CONFIG_DIR")
    assert command[idx - 1] == "-u"


def test_every_scrub_token_precedes_every_assignment_precedes_the_binary(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux = FakeTmux()
    wc.compose_window(
        GROUP_ACCOUNT_A, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=tmux
    )

    command = tmux.calls[0]["command"]
    assert command[0] == "env"
    u_indices = [i for i, tok in enumerate(command) if tok == "-u"]
    assignment_idx = command.index("CLAUDE_CONFIG_DIR=/tmp/acct-a")
    binary_idx = command.index("--session-id") - 1
    assert max(u_indices) < assignment_idx < binary_idx


def test_the_bound_account_matches_resolve_launch_environment_and_ignores_ambient_env(
    tmp_path,
):
    """The binding compose_window puts on the pane must equal what
    `resolve_launch_environment` itself returns for the same group and env —
    never a second, independent read of `[launch] account`. Setting an
    ambient CLAUDE_CONFIG_DIR proves it never survives into the assignment:
    it is scrubbed, and the assignment (when there is one) carries only the
    declared account's value."""
    import camp.launch.window_compose as wc
    from camp.launch.session import resolve_launch_environment

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    ambient_env = {
        "HOME": str(tmp_path),
        "CLAUDE_CONFIG_DIR": "/ambient/should-not-survive",
    }

    tmux = FakeTmux()
    wc.compose_window(
        GROUP_ACCOUNT_A, "slug", ws_dir, cwd=ws_dir, window_name="w1",
        tmux=tmux, env=ambient_env,
    )

    harness = FakeHarness()
    expected_account, expected_binding, _scrub, _launch_env = resolve_launch_environment(
        harness, wc.resolve_harness_profile(GROUP_ACCOUNT_A), GROUP_ACCOUNT_A, ambient_env
    )

    command = tmux.calls[0]["command"]
    for name, value in expected_binding.items():
        assert f"{name}={value}" in command
    assert not any(
        token == "CLAUDE_CONFIG_DIR=/ambient/should-not-survive" for token in command
    )
    assert expected_account == "/tmp/acct-a"


def test_the_tmux_request_still_carries_no_dash_e_with_account_bound(tmp_path):
    import camp.launch.window_compose as wc
    import inspect

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    tmux = FakeTmux()
    wc.compose_window(
        GROUP_ACCOUNT_A, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=tmux
    )

    sig = inspect.signature(tmux.new_window)
    assert "env" not in sig.parameters
    assert set(tmux.calls[0].keys()) == {"name", "cwd", "window_name", "command"}


# ---------------------------------------------------------------------------
# 8. Written before success is reported, exactly once
# ---------------------------------------------------------------------------


def test_record_written_exactly_once_and_before_success_is_reported(
    tmp_path, monkeypatch
):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    calls = []
    real_append = wc.append_window_entry

    def spy(ws_dir_arg, entry):
        calls.append(entry)
        real_append(ws_dir_arg, entry)

    monkeypatch.setattr(wc, "append_window_entry", spy)

    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=FakeTmux()
    )

    assert len(calls) == 1


def test_a_record_write_failure_propagates_rather_than_reporting_success(
    tmp_path, monkeypatch
):
    """If the write itself fails, no success can have been reported for a
    window whose record never landed — the write must happen ON THE PATH to
    a returned result, not after it."""
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    def boom(ws_dir_arg, entry):
        raise RuntimeError("disk full")

    monkeypatch.setattr(wc, "append_window_entry", boom)

    with pytest.raises(RuntimeError, match="disk full"):
        wc.compose_window(
            GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1", tmux=FakeTmux()
        )


# ---------------------------------------------------------------------------
# AC21 — the directory floor (task/the-directory-floor-refuse-the-window-record-nothing)
#
# Test contract:
#  1. A directory inside the workspace, clear of the deny-list, is accepted —
#     the control case.
#  2. Outside the workspace root: refused, no window created, record unchanged.
#  3. At / under / above a declared credential store: refused, no window
#     created, record unchanged — all three positions.
#  4. The two refusals are distinguishable, and the credential refusal's
#     message does not contain the offending path.
#  5. Neither refusal produces a raw traceback.
#  6. A symlink pointing outside the workspace is refused on the resolved
#     path, not the spelling — and the resolved path is what reaches tmux.
# ---------------------------------------------------------------------------


def _install_account(tmp_path, account_path):
    """Write a group config declaring `[launch] account = account_path` for
    "testgroup" (the name `GROUP` above uses), and return the env that
    points camp's config resolver at it. Mirrors
    `test_launch_eligibility.py`'s `_install_group_configs`."""
    groups_dir = tmp_path / "camp-config" / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    body = (
        '[group]\nname = "testgroup"\n\n'
        '[[members]]\nname = "myrepo"\nrepo_root = "/tmp/myrepo"\n\n'
        f'[launch]\naccount = "{account_path}"\n'
    )
    (groups_dir / "testgroup.toml").write_text(body, encoding="utf-8")
    return {"HOME": str(tmp_path), "CAMP_CONFIG_DIR": str(tmp_path / "camp-config")}


def _empty_env(tmp_path):
    """An env with no declared accounts, so only the fixed floor applies."""
    return {"HOME": str(tmp_path), "CAMP_CONFIG_DIR": str(tmp_path / "camp-config-empty")}


# --- 1. Control case ---------------------------------------------------


def test_directory_inside_workspace_clear_of_deny_list_is_accepted(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    result = wc.compose_window(
        GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1",
        tmux=FakeTmux(), env=_empty_env(tmp_path),
    )

    assert result.window_id == "@1"
    assert _recorded_entries(ws_dir)[0].window_id == "@1"


# --- 2. Outside the workspace root --------------------------------------


def test_directory_outside_workspace_root_is_refused(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    tmux = FakeTmux()
    with pytest.raises(wc.WindowOutsideWorkspace):
        wc.compose_window(
            GROUP, "slug", ws_dir, cwd=outside, window_name="w1",
            tmux=tmux, env=_empty_env(tmp_path),
        )

    assert tmux.calls == []
    assert _recorded_entries(ws_dir) == ()


# --- 3. At / under / above a declared credential store ------------------


def test_directory_at_a_declared_credential_store_is_refused(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    secrets = ws_dir / "secrets"
    secrets.mkdir(parents=True)
    env = _install_account(tmp_path, str(secrets))

    tmux = FakeTmux()
    with pytest.raises(wc.WindowAtCredentialStore):
        wc.compose_window(
            GROUP, "slug", ws_dir, cwd=secrets, window_name="w1",
            tmux=tmux, env=env,
        )

    assert tmux.calls == []
    assert _recorded_entries(ws_dir) == ()


def test_directory_under_a_declared_credential_store_is_refused(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    nested = ws_dir / "secrets" / "nested"
    nested.mkdir(parents=True)
    env = _install_account(tmp_path, str(ws_dir / "secrets"))

    tmux = FakeTmux()
    with pytest.raises(wc.WindowAtCredentialStore):
        wc.compose_window(
            GROUP, "slug", ws_dir, cwd=nested, window_name="w1",
            tmux=tmux, env=env,
        )

    assert tmux.calls == []
    assert _recorded_entries(ws_dir) == ()


def test_directory_above_a_declared_credential_store_is_refused(tmp_path):
    """The workspace root itself, containing the declared store — the
    ancestor direction, which is what stops a wide root from laundering the
    store inside it past the gate."""
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    (ws_dir / "secrets").mkdir(parents=True)
    env = _install_account(tmp_path, str(ws_dir / "secrets"))

    tmux = FakeTmux()
    with pytest.raises(wc.WindowAtCredentialStore):
        wc.compose_window(
            GROUP, "slug", ws_dir, cwd=ws_dir, window_name="w1",
            tmux=tmux, env=env,
        )

    assert tmux.calls == []
    assert _recorded_entries(ws_dir) == ()


# --- 4. Distinguishable; credential refusal omits the path ---------------


def test_the_two_refusals_are_distinguishable_types(tmp_path):
    import camp.launch.window_compose as wc

    assert wc.WindowOutsideWorkspace is not wc.WindowAtCredentialStore
    assert not issubclass(wc.WindowOutsideWorkspace, wc.WindowAtCredentialStore)
    assert not issubclass(wc.WindowAtCredentialStore, wc.WindowOutsideWorkspace)


def test_containment_refusal_names_the_offending_path(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    with pytest.raises(wc.WindowOutsideWorkspace) as exc_info:
        wc.compose_window(
            GROUP, "slug", ws_dir, cwd=outside, window_name="w1",
            tmux=FakeTmux(), env=_empty_env(tmp_path),
        )

    assert str(outside.resolve()) in str(exc_info.value)


def test_credential_refusal_does_not_name_the_offending_path(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    secrets = ws_dir / "secrets"
    secrets.mkdir(parents=True)
    env = _install_account(tmp_path, str(secrets))

    with pytest.raises(wc.WindowAtCredentialStore) as exc_info:
        wc.compose_window(
            GROUP, "slug", ws_dir, cwd=secrets, window_name="w1",
            tmux=FakeTmux(), env=env,
        )

    assert str(secrets.resolve()) not in str(exc_info.value)


# --- 5. No raw traceback --------------------------------------------------


def test_neither_refusal_produces_a_raw_traceback(tmp_path):
    """Drive both refusals through the surface a caller actually sees: the
    exception it catches. Each must be one of the two typed refusals with a
    clean, single-line `camp:`-prefixed message — not an unrelated
    exception (KeyError/AttributeError/etc.) whose str() is stack-trace-shaped
    noise a caller would have to pass through raw."""
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    try:
        wc.compose_window(
            GROUP, "slug", ws_dir, cwd=outside, window_name="w1",
            tmux=FakeTmux(), env=_empty_env(tmp_path),
        )
        assert False, "expected WindowOutsideWorkspace"
    except wc.WindowRefused as exc:
        assert isinstance(exc, wc.WindowOutsideWorkspace)
        message = str(exc)
        assert message.startswith("camp:")
        assert "Traceback" not in message
        assert "\n" not in message

    secrets = ws_dir / "secrets"
    secrets.mkdir(parents=True)
    env = _install_account(tmp_path, str(secrets))

    try:
        wc.compose_window(
            GROUP, "slug", ws_dir, cwd=secrets, window_name="w2",
            tmux=FakeTmux(), env=env,
        )
        assert False, "expected WindowAtCredentialStore"
    except wc.WindowRefused as exc:
        assert isinstance(exc, wc.WindowAtCredentialStore)
        message = str(exc)
        assert message.startswith("camp:")
        assert "Traceback" not in message
        assert "\n" not in message


# --- 6. Symlink: decided on the resolved path, and the resolved path -----
#        is what reaches tmux -----------------------------------------


def test_symlink_pointing_outside_the_workspace_is_refused(tmp_path):
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    link = ws_dir / "escape"
    link.symlink_to(outside)

    tmux = FakeTmux()
    with pytest.raises(wc.WindowOutsideWorkspace):
        wc.compose_window(
            GROUP, "slug", ws_dir, cwd=link, window_name="w1",
            tmux=tmux, env=_empty_env(tmp_path),
        )

    assert tmux.calls == []
    assert _recorded_entries(ws_dir) == ()


def test_the_resolved_path_not_the_symlink_spelling_reaches_tmux(tmp_path):
    """Closes the symlink-swap finding: containment is decided on the
    resolved path, and that SAME resolved path is what the tmux request
    actually carries — a symlink swapped between the check and window
    creation cannot smuggle a different directory to tmux."""
    import camp.launch.window_compose as wc

    ws_dir = tmp_path / "ws"
    real_sub = ws_dir / "real_sub"
    real_sub.mkdir(parents=True)
    link_sub = ws_dir / "link_sub"
    link_sub.symlink_to(real_sub)

    tmux = FakeTmux()
    wc.compose_window(
        GROUP, "slug", ws_dir, cwd=link_sub, window_name="w1",
        tmux=tmux, env=_empty_env(tmp_path),
    )

    assert tmux.calls[0]["cwd"] == real_sub.resolve()
    assert tmux.calls[0]["cwd"] != link_sub
