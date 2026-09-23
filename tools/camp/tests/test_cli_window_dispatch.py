"""Tests for cli/window_dispatch.py — the internal verb the window-creation
key binding's `run-shell` fires against.

Test contract (task/the-binding-the-operator-s-ordinary-window-creation-key-opens-a-camp-composed-window):
- Reads `@camp_group`/`@camp_slug` back off the session the binding fired
  from (via the tmux-minted `#{session_id}`), never a session NAME.
- A stored slug is re-gated through `validate_workspace_slug` before any
  path join — a corrupted or hand-edited stored slug must not reach
  `compose_window`.
- A refusal (missing marks, an invalid stored slug, an unknown group, or
  `compose_window` itself refusing) reports through `display-message`
  against the firing session, carrying the refusal's own words — never a
  raw traceback, since this runs detached with no tty of its own.
- The composed window is rooted at the workspace directory the resolved
  (group, slug) derives, never a directory this verb invents.

Every test drives `dispatch_window` — the pure function behind the CLI
verb — against injected fakes (`tmux`, `all_configs`, `workspace_dir_fn`,
`compose`), never a real tmux subprocess or a real groups directory on
disk; `cli/dispatch.py`'s own wiring (parsing `--session-id`, resolving the
real groups dir, constructing a real `Tmux`) has no logic of its own to
test beyond "it calls `dispatch_window`", which the real-tmux end-to-end
test in `test_window_binding_end_to_end.py` covers behaviourally.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

GROUP = {"group": {"name": "testgroup"}}


class _FakeTmux:
    def __init__(self, *, options: dict[str, str] | None = None) -> None:
        self._options = dict(options or {})
        self.display_messages: list[tuple[str, str]] = []

    def show_option(self, target, key):
        return self._options.get(key)

    def display_message(self, target, message, *, timeout=None):
        self.display_messages.append((target, message))


def _compose_recording(calls: list[dict]):
    def _compose(group, slug, ws_dir, *, cwd, window_name, tmux=None, env=None, command=None):
        calls.append(
            {
                "group": group,
                "slug": slug,
                "ws_dir": ws_dir,
                "cwd": cwd,
                "window_name": window_name,
            }
        )

    return _compose


def test_missing_marks_refuse_with_no_compose_call(tmp_path):
    from camp.cli.window_dispatch import dispatch_window

    tmux = _FakeTmux(options={})
    calls: list[dict] = []

    dispatch_window(
        "$3",
        tmux=tmux,
        all_configs=[GROUP],
        workspace_dir_fn=lambda g, s, env=None: tmp_path / "ws",
        compose=_compose_recording(calls),
    )

    assert calls == []
    assert len(tmux.display_messages) == 1
    target, message = tmux.display_messages[0]
    assert target == "$3"
    assert "camp:" in message


def test_a_stored_slug_with_a_path_separator_is_refused_before_any_compose_call(tmp_path):
    """The stored-value gate: `validate_workspace_slug` runs on the value
    READ BACK from the option, not merely trusted because it came from
    camp's own set-option call at creation time."""
    from camp.cli.window_dispatch import dispatch_window

    tmux = _FakeTmux(options={"@camp_group": "testgroup", "@camp_slug": "../etc"})
    calls: list[dict] = []

    dispatch_window(
        "$3",
        tmux=tmux,
        all_configs=[GROUP],
        workspace_dir_fn=lambda g, s, env=None: tmp_path / "ws",
        compose=_compose_recording(calls),
    )

    assert calls == [], "an invalid stored slug must never reach compose_window"
    assert len(tmux.display_messages) == 1
    assert "camp:" in tmux.display_messages[0][1]


def test_an_unknown_group_is_refused_with_no_compose_call(tmp_path):
    from camp.cli.window_dispatch import dispatch_window

    tmux = _FakeTmux(options={"@camp_group": "no-such-group", "@camp_slug": "feat-x"})
    calls: list[dict] = []

    dispatch_window(
        "$3",
        tmux=tmux,
        all_configs=[GROUP],
        workspace_dir_fn=lambda g, s, env=None: tmp_path / "ws",
        compose=_compose_recording(calls),
    )

    assert calls == []
    assert len(tmux.display_messages) == 1
    assert "camp:" in tmux.display_messages[0][1]


def test_a_valid_mark_pair_composes_a_window_rooted_at_the_derived_workspace_dir(tmp_path):
    """Varied across two distinct (group, slug) pairs so the composed call
    is proven to follow what was actually read back, not a fixed value."""
    from camp.cli.window_dispatch import dispatch_window

    ws_dir_a = tmp_path / "ws-a"
    calls_a: list[dict] = []
    tmux_a = _FakeTmux(options={"@camp_group": "testgroup", "@camp_slug": "feat-x"})

    dispatch_window(
        "$3",
        tmux=tmux_a,
        all_configs=[GROUP],
        workspace_dir_fn=lambda g, s, env=None: ws_dir_a,
        compose=_compose_recording(calls_a),
    )

    assert tmux_a.display_messages == []
    assert len(calls_a) == 1
    assert calls_a[0]["group"] is GROUP
    assert calls_a[0]["slug"] == "feat-x"
    assert calls_a[0]["ws_dir"] == ws_dir_a
    assert calls_a[0]["cwd"] == ws_dir_a

    ws_dir_b = tmp_path / "ws-b"
    calls_b: list[dict] = []
    tmux_b = _FakeTmux(options={"@camp_group": "testgroup", "@camp_slug": "feat-y"})

    dispatch_window(
        "$3",
        tmux=tmux_b,
        all_configs=[GROUP],
        workspace_dir_fn=lambda g, s, env=None: ws_dir_b,
        compose=_compose_recording(calls_b),
    )

    assert calls_b[0]["slug"] == "feat-y"
    assert calls_b[0]["ws_dir"] == ws_dir_b
    assert calls_a[0]["slug"] != calls_b[0]["slug"]
    assert calls_a[0]["ws_dir"] != calls_b[0]["ws_dir"]


def test_compose_windows_own_refusal_message_reaches_display_message_verbatim(tmp_path):
    """`WindowRefused` / `WindowComposeError` are the operator-facing
    refusal surface `compose_window` already composes (AC21's directory
    floor, or a tmux create failure) — this verb forwards the exception's
    OWN message, adding no redaction logic of its own, since
    `WindowAtCredentialStore` already withholds the path by construction."""
    from camp.launch.window_compose import WindowOutsideWorkspace
    from camp.cli.window_dispatch import dispatch_window

    def _refusing_compose(group, slug, ws_dir, *, cwd, window_name, tmux=None, env=None, command=None):
        raise WindowOutsideWorkspace("camp: cannot open window — directory /tmp/x is outside")

    tmux = _FakeTmux(options={"@camp_group": "testgroup", "@camp_slug": "feat-x"})

    dispatch_window(
        "$3",
        tmux=tmux,
        all_configs=[GROUP],
        workspace_dir_fn=lambda g, s, env=None: tmp_path / "ws",
        compose=_refusing_compose,
    )

    assert len(tmux.display_messages) == 1
    target, message = tmux.display_messages[0]
    assert target == "$3"
    assert message == "camp: cannot open window — directory /tmp/x is outside"


def test_a_record_write_failure_after_tmux_created_the_window_does_not_escape(tmp_path):
    """`compose_window` creates the tmux window BEFORE it writes the
    record (`window_compose.py`'s own contract) — a `WindowRecordError` or
    `OSError` out of that write must not propagate past `dispatch_window`
    the way `WindowRefused`/`WindowComposeError` already don't; the window
    genuinely exists by then, so the refusal must say something true about
    that, not just repeat the same wording used when nothing was created."""
    from camp.group.window_record import WindowRecordError
    from camp.cli.window_dispatch import dispatch_window

    def _compose_then_fail_to_record(group, slug, ws_dir, *, cwd, window_name, tmux=None, env=None, command=None):
        raise WindowRecordError("camp: cannot read window record at /ws/windows.json: disk full")

    tmux = _FakeTmux(options={"@camp_group": "testgroup", "@camp_slug": "feat-x"})

    dispatch_window(
        "$3",
        tmux=tmux,
        all_configs=[GROUP],
        workspace_dir_fn=lambda g, s, env=None: tmp_path / "ws",
        compose=_compose_then_fail_to_record,
    )

    assert len(tmux.display_messages) == 1
    target, message = tmux.display_messages[0]
    assert target == "$3"
    assert "camp:" in message
    # The refusal wording used for a pre-tmux refusal ("cannot open window")
    # is not true here — the window WAS created; the message must not
    # falsely claim otherwise.
    assert "cannot open window" not in message


def test_an_oserror_from_the_record_write_also_does_not_escape(tmp_path):
    from camp.cli.window_dispatch import dispatch_window

    def _compose_then_oserror(group, slug, ws_dir, *, cwd, window_name, tmux=None, env=None, command=None):
        raise OSError("no space left on device")

    tmux = _FakeTmux(options={"@camp_group": "testgroup", "@camp_slug": "feat-x"})

    dispatch_window(
        "$3",
        tmux=tmux,
        all_configs=[GROUP],
        workspace_dir_fn=lambda g, s, env=None: tmp_path / "ws",
        compose=_compose_then_oserror,
    )

    assert len(tmux.display_messages) == 1
    assert "camp:" in tmux.display_messages[0][1]


def test_a_launch_error_out_of_compose_is_refused_with_no_compose_side_effects(tmp_path):
    """`compose_window` can raise `camp.launch.session.LaunchError` (a
    declared account the harness refuses, a `TRAILHEAD_CLAUDE_DIR`
    conflict, or a harness whose scrub/binding answers `None`) — this must
    fold into the same `display-message` refusal path as
    `WindowRefused`/`WindowComposeError`, not escape and leave the key press
    doing nothing."""
    from camp.launch.session import LaunchError
    from camp.cli.window_dispatch import dispatch_window

    def _refusing_compose(group, slug, ws_dir, *, cwd, window_name, tmux=None, env=None, command=None):
        raise LaunchError(
            "camp: cannot bind an account — harness claude will not bind this "
            "session to the declared account claude-levr: relative path"
        )

    tmux = _FakeTmux(options={"@camp_group": "testgroup", "@camp_slug": "feat-x"})

    dispatch_window(
        "$3",
        tmux=tmux,
        all_configs=[GROUP],
        workspace_dir_fn=lambda g, s, env=None: tmp_path / "ws",
        compose=_refusing_compose,
    )

    assert len(tmux.display_messages) == 1
    target, message = tmux.display_messages[0]
    assert target == "$3"
    assert message == (
        "camp: cannot bind an account — harness claude will not bind this "
        "session to the declared account claude-levr: relative path"
    )


def test_camp_window_dispatch_verb_reaches_the_real_handler_end_to_end(tmp_path):
    """Proves the `dispatch.py` wiring, not just `dispatch_window`'s own
    logic: invokes the REAL `cli/camp` binary with `window-dispatch
    --session-id <id>` and a stub `tmux` on PATH that logs every argv it
    receives — the log must show `show-options -t <id> ...`, which only
    happens if `main()` actually routed to `_cmd_window_dispatch_cli`
    rather than falling through to the bare-slug/unknown-verb refusal (a
    fall-through would invoke no tmux call at all)."""
    import os
    import stat
    import subprocess

    repo_root = Path(__file__).resolve().parents[3]
    plugin_dir = repo_root / "tools" / "camp" / "plugins" / "camp"
    cli_camp = plugin_dir / "cli" / "camp"

    log_file = tmp_path / "tmux-argv.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "tmux"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"open({str(log_file)!r}, 'a').write(' '.join(sys.argv[1:]) + chr(10))\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "CAMP_CONFIG_DIR": str(tmp_path / "config"),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
    }

    subprocess.run(
        [sys.executable, str(cli_camp), "window-dispatch", "--session-id", "$7"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )

    log_lines = log_file.read_text().splitlines() if log_file.exists() else []
    assert any(
        "show-options" in line and "$7" in line for line in log_lines
    ), f"window-dispatch never reached a real tmux show-options call — log: {log_lines!r}"
