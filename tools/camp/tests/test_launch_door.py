"""Tests for launch/door.py — the door's outcome type and its renderings.

Test contract (task/the-door-s-outcome-renders-as-a-line-an-object-and-an-exit-status):
- The human line varies with the outcome: created and connected against the
  same session produce two different lines, and each names the session.
- The JSON object's `outcome` varies the same way, with `attached` varying
  independently of it — all four combinations of (created, connected) x
  (attached, not attached) are distinct objects.
- Exit status is 0 for created, 0 for connected, and 1 for each refusal
  member — the mapping is walked over the closed set of `DoorOutcome`
  subclasses via reflection, so a new member added without an entry fails.
- A slug or session name carrying a newline or a C0 control character cannot
  inject a second line into the human rendering, including a control
  character a `\\n`-only per-field escaper would not have covered.

Pure values throughout: nothing here does I/O, prints, or touches tmux. (The
property that a dispatch's emitted `tmux_session` actually matches
`workspace_session_name`'s derivation is a dispatch-layer claim, not a pure
rendering one — pinned in `test_attach_door_dispatch.py` against the CLI's
actually-emitted object instead.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _make_success(cls, *, tmux_session: str, attached: bool = True, slug="camp-cli", group="trailhead"):
    from pathlib import Path as _Path

    return cls(
        slug=slug,
        group=group,
        tmux_session=tmux_session,
        workspace_path=_Path("/workspaces/camp-cli"),
        attached=attached,
    )


def test_human_line_varies_with_outcome_and_each_names_the_session():
    from camp.launch.door import Connected, Created, render_human

    session = "camp-trailhead-camp-cli"
    created_line = render_human(_make_success(Created, tmux_session=session))
    connected_line = render_human(_make_success(Connected, tmux_session=session))

    assert created_line != connected_line
    assert session in created_line
    assert session in connected_line


def test_json_outcome_and_attached_vary_independently_across_four_combinations():
    from camp.launch.door import Connected, Created, render_json

    session = "camp-trailhead-camp-cli"
    objects = [
        render_json(_make_success(cls, tmux_session=session, attached=attached))
        for cls in (Created, Connected)
        for attached in (True, False)
    ]

    serialized = {json.dumps(obj, sort_keys=True) for obj in objects}
    assert len(serialized) == 4, "all four (outcome x attached) combinations must be distinct objects"

    outcomes = {obj["outcome"] for obj in objects}
    assert outcomes == {"created", "connected"}
    attached_values = {obj["attached"] for obj in objects}
    assert attached_values == {True, False}


def test_exit_status_table_covers_every_known_member_via_reflection():
    from camp.launch.door import (
        Connected,
        Created,
        DoorOutcome,
        Resurrected,
        _EXIT_STATUS,
        exit_status,
    )

    subclasses = DoorOutcome.__subclasses__()
    assert len(subclasses) >= 2, "the door must declare more than zero outcome members"

    for cls in subclasses:
        assert cls in _EXIT_STATUS, f"{cls.__name__} has no exit-status mapping"
        if cls is Resurrected:
            # Resurrected's exit status is a function of `failed`, not a
            # fixed per-type value — checked separately below.
            continue
        expected = 0 if cls in (Created, Connected) else 1
        instance = _instantiate_for_exit_status(cls)
        assert exit_status(instance) == expected


def _instantiate_for_exit_status(cls):
    from camp.launch.door import Connected, Created

    if cls in (Connected, Created):
        return _make_success(cls, tmux_session="camp-trailhead-camp-cli")
    return cls()


def _make_resurrected(*, restored: int, failed: int, dropped: int, attached: bool = True):
    from camp.launch.door import Resurrected

    return Resurrected(
        slug="camp-cli",
        group="trailhead",
        tmux_session="camp-trailhead-camp-cli",
        workspace_path=Path("/workspaces/camp-cli"),
        attached=attached,
        restored=restored,
        failed=failed,
        dropped=dropped,
    )


def test_resurrected_exit_status_is_0_when_nothing_failed_and_2_when_something_did():
    from camp.launch.door import exit_status

    assert exit_status(_make_resurrected(restored=3, failed=0, dropped=0)) == 0
    assert exit_status(_make_resurrected(restored=2, failed=1, dropped=0)) == 2


def test_resurrected_human_line_varies_with_restored_failed_and_dropped():
    from camp.launch.door import render_human

    whole = render_human(_make_resurrected(restored=3, failed=0, dropped=0))
    partial = render_human(_make_resurrected(restored=2, failed=1, dropped=0))
    dropped = render_human(_make_resurrected(restored=2, failed=0, dropped=1))

    assert whole.endswith("(3 windows)")
    assert partial.endswith("(2 of 3 windows; 1 did not come back)")
    assert dropped.endswith("(2 windows; 1 dropped)")
    assert whole != partial != dropped

    both = render_human(_make_resurrected(restored=1, failed=1, dropped=1))
    assert both.endswith("(1 of 2 windows; 1 did not come back)"), (
        "failed must take precedence over dropped in the human line when both are non-zero"
    )


def test_resurrected_human_line_singularizes_window_when_restored_is_one():
    from camp.launch.door import render_human

    one_whole = render_human(_make_resurrected(restored=1, failed=0, dropped=0))
    one_dropped = render_human(_make_resurrected(restored=1, failed=0, dropped=2))
    two_dropped = render_human(_make_resurrected(restored=2, failed=0, dropped=1))
    partial_one_of_three = render_human(_make_resurrected(restored=1, failed=2, dropped=0))

    assert one_whole.endswith("(1 window)")
    assert one_dropped.endswith("(1 window; 2 dropped)")
    assert two_dropped.endswith("(2 windows; 1 dropped)")
    # the failed-branch total stays plural even when only one window is restored
    assert partial_one_of_three.endswith("(1 of 3 windows; 2 did not come back)")


def test_resurrected_json_carries_a_windows_key_created_and_connected_do_not():
    from camp.launch.door import Created, render_json

    resurrected_obj = render_json(_make_resurrected(restored=2, failed=1, dropped=0))
    assert resurrected_obj["outcome"] == "resurrected"
    assert resurrected_obj["windows"] == {"restored": 2, "failed": 1, "dropped": 0}

    created_obj = render_json(_make_success(Created, tmux_session="camp-trailhead-camp-cli"))
    assert "windows" not in created_obj


def test_refused_record_unreadable_exit_status_and_word():
    from camp.launch.door import RefusedRecordUnreadable, exit_status, refusal_outcome_word

    outcome = RefusedRecordUnreadable()
    assert exit_status(outcome) == 1
    assert refusal_outcome_word(outcome) == "record_unreadable"


def test_a_new_outcome_member_without_a_mapping_raises_instead_of_defaulting():
    from camp.launch.door import DoorOutcome, exit_status

    class _UnregisteredOutcome(DoorOutcome):
        pass

    with pytest.raises(KeyError):
        exit_status(_UnregisteredOutcome())


def test_a_newline_in_the_tmux_session_cannot_inject_a_second_line():
    from camp.launch.door import Created, render_human

    outcome = _make_success(Created, tmux_session="camp-trailhead-evil\nInjected: pwned")
    line = render_human(outcome)

    assert "\n" not in line
    assert "\\x0a" in line


def test_a_carriage_return_in_the_tmux_session_is_escaped_too():
    # A per-field escaper that only substitutes literal "\n" would let a bare
    # "\r" straight through — the composed-whole rule this pins.
    from camp.launch.door import Connected, render_human

    outcome = _make_success(Connected, tmux_session="camp-trailhead-evil\rInjected")
    line = render_human(outcome)

    assert "\r" not in line
    assert "\\x0d" in line


