"""Tests for cli/window.py — ``camp window unbind``, the operator-facing
removal for camp's server-global tmux window-creation-key binding.

Test contract (task/taking-the-binding-back-removal-restores-the-key-without-killing-the-server):
- The command's refusal path, if tmux cannot be reached, is camp's own
  message and not a raw traceback.

Every test here drives `dispatch_window_verb` — the pure decision behind
the CLI verb — against an injected `remove` call and a fake `tmux`, never a
real tmux subprocess or a real `sys.exit`; the real-tmux behavioural
contract (key behaviour, server survival, reinstall-composes, the
no-op-succeeds shape) is covered end to end in
`test_window_binding_end_to_end.py`, matching the split
`test_cli_window_dispatch.py` already establishes for the paired install
verb.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def test_unbind_calls_remove_and_reports_the_restored_message():
    from camp.cli.window import dispatch_window_verb

    calls = []

    def fake_remove(tmux):
        calls.append(tmux)

    ok, message = dispatch_window_verb("unbind", remove=fake_remove, tmux="the-tmux")

    assert ok is True
    assert calls == ["the-tmux"]
    assert "default" in message.lower()


def test_unbind_when_removal_raises_reports_camps_own_message_not_a_traceback():
    """The refusal-path contract bullet: tmux unreachable surfaces as
    camp's own words, not a raw exception propagating out of this
    function."""
    from camp.cli.window import dispatch_window_verb
    from camp.launch.binding import WindowBindingRemovalError

    def failing_remove(tmux):
        raise WindowBindingRemovalError("camp: could not reach tmux")

    ok, message = dispatch_window_verb("unbind", remove=failing_remove, tmux="the-tmux")

    assert ok is False
    assert message == "camp: could not reach tmux"


def test_an_unknown_subcommand_is_a_usage_refusal_never_reaching_remove():
    from camp.cli.window import dispatch_window_verb

    calls = []

    def fake_remove(tmux):
        calls.append(tmux)

    ok, message = dispatch_window_verb("bogus", remove=fake_remove, tmux="the-tmux")

    assert ok is False
    assert calls == []
    assert "unbind" in message.lower()


def test_no_subcommand_at_all_is_the_same_usage_refusal():
    from camp.cli.window import dispatch_window_verb

    ok, message = dispatch_window_verb(None, remove=lambda tmux: None, tmux="the-tmux")

    assert ok is False
    assert "unbind" in message.lower()
