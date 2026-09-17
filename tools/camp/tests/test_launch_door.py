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
- The JSON object carries the same `tmux_session` string that
  `workspace_session_name` derives for that group and slug — derived here,
  never retyped.

Pure values throughout: nothing here does I/O, prints, or touches tmux.
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
        _EXIT_STATUS,
        exit_status,
    )

    subclasses = DoorOutcome.__subclasses__()
    assert len(subclasses) >= 2, "the door must declare more than zero outcome members"

    for cls in subclasses:
        assert cls in _EXIT_STATUS, f"{cls.__name__} has no exit-status mapping"
        expected = 0 if cls in (Created, Connected) else 1
        instance = _instantiate_for_exit_status(cls)
        assert exit_status(instance) == expected


def _instantiate_for_exit_status(cls):
    from camp.launch.door import Connected, Created

    if cls in (Created, Connected):
        return _make_success(cls, tmux_session="camp-trailhead-camp-cli")
    return cls()


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


def test_json_tmux_session_matches_workspace_session_name_derivation():
    from camp.launch.door import Created, render_json
    from camp.launch.naming import workspace_session_name

    group, slug = "trailhead", "camp-cli"
    session = workspace_session_name(group, slug)

    outcome = _make_success(Created, tmux_session=session, slug=slug, group=group)
    obj = render_json(outcome)

    assert obj["tmux_session"] == session
