"""Tests for launch/naming.py — the group-qualified session name and the
retired-name recognizer.

Test contract:
- The name varies with the group: two different groups and the same slug
  produce two different names.
- The name varies with the slug: same group, two slugs, two names.
- A group or slug carrying a tmux target separator (``.``, ``:``) yields a
  name containing neither, and a name that still differs from the name
  derived for a sibling that differs only in the separated component.
- A component that folds away to nothing falls back rather than producing a
  name with an empty segment or a trailing hyphen.
- ``is_retired_session_name`` accepts both live observed forms and the
  degraded root-unknown form; rejects a name with no ``camp-`` prefix, a
  ``camp-`` name whose tail is not 8 hex, and a ``camp-`` name whose tail is
  hex but the wrong length. Each rejection is its own test.
- Round-trip guard: a slug whose tail reads as a hex component still produces
  a ``workspace_session_name`` that ``is_retired_session_name`` also accepts.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def test_name_varies_with_group():
    from camp.launch.naming import workspace_session_name

    name_a = workspace_session_name("groupa", "myslug")
    name_b = workspace_session_name("groupb", "myslug")
    assert name_a != name_b


def test_name_varies_with_slug():
    from camp.launch.naming import workspace_session_name

    name_a = workspace_session_name("mygroup", "sluga")
    name_b = workspace_session_name("mygroup", "slugb")
    assert name_a != name_b


def test_separator_characters_are_folded_out_of_the_name():
    from camp.launch.naming import workspace_session_name

    name = workspace_session_name("my.group", "my:slug")
    assert "." not in name
    assert ":" not in name


def test_separator_folding_does_not_collapse_distinct_siblings():
    from camp.launch.naming import workspace_session_name

    name_a = workspace_session_name("mygroup", "a.b")
    name_b = workspace_session_name("mygroup", "a:b")
    assert name_a != name_b


def test_component_that_folds_to_nothing_falls_back_without_empty_segment():
    from camp.launch.naming import workspace_session_name

    name = workspace_session_name("!!!", "myslug")
    assert "--" not in name
    assert not name.endswith("-")
    # the fully-substituted group component must fall back to something,
    # not vanish and leave the name reading as camp-myslug
    assert name != "camp-myslug"


def test_is_retired_session_name_accepts_live_observed_forms():
    from camp.launch.naming import is_retired_session_name

    assert is_retired_session_name("camp-audio-assebly-f4919a6f") is True
    assert is_retired_session_name("camp-ets-workshop-e2026480") is True


def test_is_retired_session_name_accepts_degraded_root_unknown_form():
    from camp.launch.naming import is_retired_session_name

    assert is_retired_session_name("camp-f4919a6f") is True


def test_is_retired_session_name_rejects_missing_camp_prefix():
    from camp.launch.naming import is_retired_session_name

    assert is_retired_session_name("other-audio-assebly-f4919a6f") is False


def test_is_retired_session_name_rejects_non_hex_tail():
    from camp.launch.naming import is_retired_session_name

    assert is_retired_session_name("camp-audio-assembly-notahexxx") is False


def test_is_retired_session_name_rejects_hex_tail_wrong_length():
    from camp.launch.naming import is_retired_session_name

    assert is_retired_session_name("camp-audio-assembly-f4919a6") is False
    assert is_retired_session_name("camp-audio-assembly-f4919a6f0") is False


def test_ambiguous_slug_round_trips_through_both_functions():
    from camp.launch.naming import is_retired_session_name, workspace_session_name

    name = workspace_session_name("mygroup", "proj-deadbeef")
    assert is_retired_session_name(name) is True
