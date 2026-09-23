"""Tests for launch/inventory.py — the pure classifier.

Test contract:
- A workspace whose derived name is in the enumeration classifies `running`
  and carries the count from that row; the same workspace with the name
  absent classifies `none`.
- The count carried is the matched session's own, proven by two running
  workspaces with different counts in one call.
- A retired-form session belonging to no workspace yields an unmanaged row
  carrying the tmux name and no slug and no path, under the widened scope.
- Disclosure scope varies the answer: group scope returns a count and zero
  unmanaged rows; widened scope returns the rows, and the count equals the
  number of rows the widened call returns.
- The scope argument changes ONLY the leftovers: workspace rows are
  byte-identical under both scopes.
- Precedence: a retired-form session whose name is ALSO a workspace's
  derived name classifies as that workspace's session, with exactly one row
  and no unmanaged row beside it.
- Adoption refusal: an unmanaged session whose name component equals a
  workspace's slug is still unmanaged and still carries no slug; the
  workspace's own row is untouched.
- A session matching neither a workspace nor the retired form produces no
  row at all — asserted separately for a name with no `camp-` prefix and a
  `camp-` name with a non-hex tail.
- The unanswered sentinel makes every workspace row `unknown` and emits no
  unmanaged rows.
- An empty enumeration with workspaces present gives every workspace
  `none`; an empty enumeration with no workspaces gives zero rows.
- Row order: workspace rows keep input order, unmanaged rows follow —
  proven by varying the input order.
- `SessionListing.dropped` is carried through to `Classification.dropped`.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _import():
    from camp.launch import inventory
    from camp.launch.stop import SessionListing, TmuxSession, UNANSWERED

    return inventory, SessionListing, TmuxSession, UNANSWERED


def test_workspace_with_derived_name_present_classifies_running_and_absent_classifies_none():
    inventory, SessionListing, TmuxSession, _ = _import()

    ws = inventory.Workspace(group="trailhead", slug="camp-cli", path="/w/camp-cli")
    name = inventory_name(inventory, ws)

    listing_present = SessionListing(sessions=(TmuxSession(name=name, windows=3),))
    result_present = inventory.classify_sessions(
        [ws], listing_present, scope=inventory.DisclosureScope.GROUP
    )
    assert result_present.workspaces == (
        inventory.WorkspaceSession(
            slug="camp-cli", path="/w/camp-cli", state=inventory.STATE_RUNNING, windows=3
        ),
    )

    listing_absent = SessionListing(sessions=())
    result_absent = inventory.classify_sessions(
        [ws], listing_absent, scope=inventory.DisclosureScope.GROUP
    )
    assert result_absent.workspaces == (
        inventory.WorkspaceSession(
            slug="camp-cli", path="/w/camp-cli", state=inventory.STATE_NONE
        ),
    )


def inventory_name(inventory, ws):
    from camp.launch.naming import workspace_session_name

    return workspace_session_name(ws.group, ws.slug)


def test_count_carried_is_each_matched_sessions_own():
    inventory, SessionListing, TmuxSession, _ = _import()

    ws_a = inventory.Workspace(group="trailhead", slug="alpha", path="/w/alpha")
    ws_b = inventory.Workspace(group="trailhead", slug="beta", path="/w/beta")
    name_a = inventory_name(inventory, ws_a)
    name_b = inventory_name(inventory, ws_b)

    listing = SessionListing(
        sessions=(
            TmuxSession(name=name_a, windows=2),
            TmuxSession(name=name_b, windows=7),
        )
    )
    result = inventory.classify_sessions(
        [ws_a, ws_b], listing, scope=inventory.DisclosureScope.GROUP
    )
    windows_by_slug = {row.slug: row.windows for row in result.workspaces}
    assert windows_by_slug == {"alpha": 2, "beta": 7}


def test_retired_session_belonging_to_no_workspace_is_unmanaged_under_widened_scope():
    inventory, SessionListing, TmuxSession, _ = _import()

    ws = inventory.Workspace(group="trailhead", slug="alpha", path="/w/alpha")
    leftover_name = "camp-somebody-deadbeef"

    listing = SessionListing(sessions=(TmuxSession(name=leftover_name, windows=4),))
    result = inventory.classify_sessions(
        [ws], listing, scope=inventory.DisclosureScope.WIDENED
    )
    assert result.unmanaged == (
        inventory.UnmanagedSession(name=leftover_name, windows=4),
    )
    # No slug, no path is a structural property of UnmanagedSession itself —
    # it carries neither field.
    assert not hasattr(result.unmanaged[0], "slug")
    assert not hasattr(result.unmanaged[0], "path")


def test_disclosure_scope_varies_only_the_leftover_shape_and_counts_agree():
    inventory, SessionListing, TmuxSession, _ = _import()

    ws = inventory.Workspace(group="trailhead", slug="alpha", path="/w/alpha")
    leftover_name = "camp-somebody-deadbeef"

    listing = SessionListing(sessions=(TmuxSession(name=leftover_name, windows=1),))

    group_result = inventory.classify_sessions(
        [ws], listing, scope=inventory.DisclosureScope.GROUP
    )
    widened_result = inventory.classify_sessions(
        [ws], listing, scope=inventory.DisclosureScope.WIDENED
    )

    assert group_result.unmanaged == ()
    assert group_result.unmanaged_count == 1
    assert len(widened_result.unmanaged) == widened_result.unmanaged_count
    assert group_result.unmanaged_count == widened_result.unmanaged_count


def test_scope_changes_only_leftovers_workspace_rows_identical_under_both_scopes():
    inventory, SessionListing, TmuxSession, _ = _import()

    ws = inventory.Workspace(group="trailhead", slug="alpha", path="/w/alpha")
    name = inventory_name(inventory, ws)
    leftover_name = "camp-somebody-deadbeef"

    listing = SessionListing(
        sessions=(
            TmuxSession(name=name, windows=5),
            TmuxSession(name=leftover_name, windows=1),
        )
    )

    group_result = inventory.classify_sessions(
        [ws], listing, scope=inventory.DisclosureScope.GROUP
    )
    widened_result = inventory.classify_sessions(
        [ws], listing, scope=inventory.DisclosureScope.WIDENED
    )
    assert group_result.workspaces == widened_result.workspaces


def test_precedence_derived_name_matching_retired_form_claims_the_workspace_row():
    inventory, SessionListing, TmuxSession, _ = _import()

    # A slug whose tail reads as 8 hex chars, so the derived name ALSO
    # satisfies is_retired_session_name's pattern.
    ws = inventory.Workspace(group="trailhead", slug="deadbeef", path="/w/deadbeef")
    name = inventory_name(inventory, ws)
    from camp.launch.naming import is_retired_session_name

    assert is_retired_session_name(name)  # precondition for this test to mean anything

    listing = SessionListing(sessions=(TmuxSession(name=name, windows=2),))
    result = inventory.classify_sessions(
        [ws], listing, scope=inventory.DisclosureScope.WIDENED
    )
    assert result.workspaces == (
        inventory.WorkspaceSession(
            slug="deadbeef", path="/w/deadbeef", state=inventory.STATE_RUNNING, windows=2
        ),
    )
    assert result.unmanaged == ()
    assert result.unmanaged_count == 0


def test_adoption_refusal_unmanaged_session_sharing_a_slug_component_stays_unmanaged():
    inventory, SessionListing, TmuxSession, _ = _import()

    ws = inventory.Workspace(group="trailhead", slug="alpha", path="/w/alpha")
    # Retired-form name whose component happens to equal the workspace's
    # slug, but is NOT the workspace's own derived name.
    lookalike_name = "camp-alpha-deadbeef"
    real_name = inventory_name(inventory, ws)
    assert lookalike_name != real_name

    listing = SessionListing(sessions=(TmuxSession(name=lookalike_name, windows=9),))
    result = inventory.classify_sessions(
        [ws], listing, scope=inventory.DisclosureScope.WIDENED
    )
    assert result.workspaces == (
        inventory.WorkspaceSession(slug="alpha", path="/w/alpha", state=inventory.STATE_NONE),
    )
    assert result.unmanaged == (
        inventory.UnmanagedSession(name=lookalike_name, windows=9),
    )


def test_session_matching_neither_workspace_nor_retired_form_produces_no_row_no_prefix():
    inventory, SessionListing, TmuxSession, _ = _import()

    listing = SessionListing(sessions=(TmuxSession(name="not-camp-at-all", windows=1),))
    result = inventory.classify_sessions(
        [], listing, scope=inventory.DisclosureScope.WIDENED
    )
    assert result.unmanaged == ()
    assert result.unmanaged_count == 0


def test_session_matching_neither_workspace_nor_retired_form_produces_no_row_bad_hex_tail():
    inventory, SessionListing, TmuxSession, _ = _import()

    listing = SessionListing(sessions=(TmuxSession(name="camp-somebody-notahexta", windows=1),))
    result = inventory.classify_sessions(
        [], listing, scope=inventory.DisclosureScope.WIDENED
    )
    assert result.unmanaged == ()
    assert result.unmanaged_count == 0


def test_unanswered_sentinel_makes_every_workspace_unknown_and_no_unmanaged_rows():
    inventory, SessionListing, TmuxSession, UNANSWERED = _import()

    ws_a = inventory.Workspace(group="trailhead", slug="alpha", path="/w/alpha")
    ws_b = inventory.Workspace(group="trailhead", slug="beta", path="/w/beta")

    result = inventory.classify_sessions(
        [ws_a, ws_b], UNANSWERED, scope=inventory.DisclosureScope.WIDENED
    )
    assert result.workspaces == (
        inventory.WorkspaceSession(slug="alpha", path="/w/alpha", state=inventory.STATE_UNKNOWN),
        inventory.WorkspaceSession(slug="beta", path="/w/beta", state=inventory.STATE_UNKNOWN),
    )
    assert result.unmanaged == ()
    assert result.unmanaged_count == 0


def test_empty_enumeration_with_workspaces_gives_none_state_to_each():
    inventory, SessionListing, TmuxSession, _ = _import()

    ws_a = inventory.Workspace(group="trailhead", slug="alpha", path="/w/alpha")
    ws_b = inventory.Workspace(group="trailhead", slug="beta", path="/w/beta")

    result = inventory.classify_sessions(
        [ws_a, ws_b],
        SessionListing(sessions=()),
        scope=inventory.DisclosureScope.GROUP,
    )
    assert result.workspaces == (
        inventory.WorkspaceSession(slug="alpha", path="/w/alpha", state=inventory.STATE_NONE),
        inventory.WorkspaceSession(slug="beta", path="/w/beta", state=inventory.STATE_NONE),
    )


def test_empty_enumeration_with_no_workspaces_gives_zero_rows():
    inventory, SessionListing, TmuxSession, _ = _import()

    result = inventory.classify_sessions(
        [], SessionListing(sessions=()), scope=inventory.DisclosureScope.GROUP
    )
    assert result.workspaces == ()
    assert result.unmanaged == ()


def test_row_order_workspace_rows_preserve_input_order_both_directions():
    inventory, SessionListing, TmuxSession, _ = _import()

    ws_a = inventory.Workspace(group="trailhead", slug="alpha", path="/w/alpha")
    ws_b = inventory.Workspace(group="trailhead", slug="beta", path="/w/beta")

    forward = inventory.classify_sessions(
        [ws_a, ws_b], SessionListing(sessions=()), scope=inventory.DisclosureScope.GROUP
    )
    backward = inventory.classify_sessions(
        [ws_b, ws_a], SessionListing(sessions=()), scope=inventory.DisclosureScope.GROUP
    )
    assert [row.slug for row in forward.workspaces] == ["alpha", "beta"]
    assert [row.slug for row in backward.workspaces] == ["beta", "alpha"]


def test_row_order_unmanaged_rows_preserve_enumeration_order():
    inventory, SessionListing, TmuxSession, _ = _import()

    listing_forward = SessionListing(
        sessions=(
            TmuxSession(name="camp-aaa-deadbeef", windows=1),
            TmuxSession(name="camp-bbb-deadbeef", windows=1),
        )
    )
    listing_backward = SessionListing(
        sessions=(
            TmuxSession(name="camp-bbb-deadbeef", windows=1),
            TmuxSession(name="camp-aaa-deadbeef", windows=1),
        )
    )
    forward = inventory.classify_sessions(
        [], listing_forward, scope=inventory.DisclosureScope.WIDENED
    )
    backward = inventory.classify_sessions(
        [], listing_backward, scope=inventory.DisclosureScope.WIDENED
    )
    assert [row.name for row in forward.unmanaged] == ["camp-aaa-deadbeef", "camp-bbb-deadbeef"]
    assert [row.name for row in backward.unmanaged] == ["camp-bbb-deadbeef", "camp-aaa-deadbeef"]


def test_dropped_count_is_carried_through_from_the_enumeration():
    inventory, SessionListing, TmuxSession, _ = _import()

    listing = SessionListing(sessions=(), dropped=3)
    result = inventory.classify_sessions(
        [], listing, scope=inventory.DisclosureScope.GROUP
    )
    assert result.dropped == 3

    result_zero = inventory.classify_sessions(
        [], SessionListing(sessions=()), scope=inventory.DisclosureScope.GROUP
    )
    assert result_zero.dropped == 0


def test_a_sibling_groups_live_session_is_not_a_leftover_when_the_host_claims_it():
    """A group-scoped call knows only its OWN workspaces, so a live session
    belonging to a sibling group on the same machine falls through to the
    retired-form check and matches (any slug ending in 8 hex characters
    does). `host_claimed_names` is the host's full claiming set, which the
    caller supplies — the classifier stays pure — and a name in it is
    neither named nor counted as a leftover."""
    inventory, SessionListing, TmuxSession, _ = _import()

    ours = inventory.Workspace(group="groupa", slug="web", path="/w/web")
    sibling_live_name = "camp-groupb-deadbeef"
    listing = SessionListing(
        sessions=(TmuxSession(name=sibling_live_name, windows=3),)
    )

    unclaimed = inventory.classify_sessions(
        [ours], listing, scope=inventory.DisclosureScope.WIDENED
    )
    assert unclaimed.unmanaged_count == 1
    assert [u.name for u in unclaimed.unmanaged] == [sibling_live_name]

    claimed = inventory.classify_sessions(
        [ours],
        listing,
        scope=inventory.DisclosureScope.WIDENED,
        host_claimed_names=(sibling_live_name,),
    )
    assert claimed.unmanaged == ()
    assert claimed.unmanaged_count == 0


def test_host_claimed_names_narrows_only_the_leftovers_never_a_workspace_row():
    """The host's claiming set suppresses a leftover; it must not touch the
    asked group's own rows, including the case where the group's own live
    session name is in the set (it always is — the group is part of the
    host)."""
    inventory, SessionListing, TmuxSession, _ = _import()

    ours = inventory.Workspace(group="groupa", slug="web", path="/w/web")
    our_name = inventory_name(inventory, ours)
    listing = SessionListing(sessions=(TmuxSession(name=our_name, windows=2),))

    result = inventory.classify_sessions(
        [ours],
        listing,
        scope=inventory.DisclosureScope.WIDENED,
        host_claimed_names=(our_name, "camp-groupb-deadbeef"),
    )
    assert result.workspaces == (
        inventory.WorkspaceSession(
            slug="web", path="/w/web", state=inventory.STATE_RUNNING, windows=2
        ),
    )
    assert result.unmanaged == ()


def test_matched_workspace_row_carries_the_sessions_own_activity():
    inventory, SessionListing, TmuxSession, _ = _import()

    ws = inventory.Workspace(group="trailhead", slug="camp-cli", path="/w/camp-cli")
    name = inventory_name(inventory, ws)
    listing = SessionListing(sessions=(TmuxSession(name=name, windows=3, activity=1700000000),))

    result = inventory.classify_sessions([ws], listing, scope=inventory.DisclosureScope.GROUP)

    assert result.workspaces == (
        inventory.WorkspaceSession(
            slug="camp-cli",
            path="/w/camp-cli",
            state=inventory.STATE_RUNNING,
            windows=3,
            activity=1700000000,
        ),
    )


def test_unmanaged_row_carries_the_sessions_own_activity():
    inventory, SessionListing, TmuxSession, _ = _import()

    ws = inventory.Workspace(group="trailhead", slug="alpha", path="/w/alpha")
    leftover_name = "camp-somebody-deadbeef"
    listing = SessionListing(
        sessions=(TmuxSession(name=leftover_name, windows=4, activity=1600000000),)
    )

    result = inventory.classify_sessions([ws], listing, scope=inventory.DisclosureScope.WIDENED)

    assert result.unmanaged == (
        inventory.UnmanagedSession(name=leftover_name, windows=4, activity=1600000000),
    )


def test_format_sessions_cell_running_shows_window_count():
    inventory, _, _, _ = _import()
    assert inventory.format_sessions_cell(inventory.STATE_RUNNING, 3) == "3"


def test_format_sessions_cell_none_state_shows_zero():
    inventory, _, _, _ = _import()
    assert inventory.format_sessions_cell(inventory.STATE_NONE, None) == "0"


def test_format_sessions_cell_unknown_state_shows_question_mark():
    inventory, _, _, _ = _import()
    assert inventory.format_sessions_cell(inventory.STATE_UNKNOWN, None) == "?"


def test_format_sessions_cell_absent_state_shows_question_mark():
    inventory, _, _, _ = _import()
    assert inventory.format_sessions_cell(None, None) == "?"


def test_format_sessions_cell_unmanaged_shows_window_count():
    inventory, _, _, _ = _import()
    assert inventory.format_sessions_cell(inventory.STATE_UNMANAGED, 5) == "5"


def test_format_last_touched_buckets_by_elapsed_time():
    inventory, _, _, _ = _import()
    now = 1_700_000_000.0
    assert inventory.format_last_touched(now - 10, now=now) == "just now"
    assert inventory.format_last_touched(now - 5 * 60, now=now) == "5m ago"
    assert inventory.format_last_touched(now - 3 * 3600, now=now) == "3h ago"
    assert inventory.format_last_touched(now - 2 * 86400, now=now) == "2d ago"
    assert inventory.format_last_touched(now - 21 * 86400, now=now) == "3w ago"


def test_format_last_touched_none_is_the_absent_marker():
    inventory, _, _, _ = _import()
    assert inventory.format_last_touched(None, now=1_700_000_000.0) == inventory.HUMAN_ABSENT


def test_workspace_row_cells_local_row_epoch_last_touched():
    inventory, _, _, _ = _import()
    now = 1_700_000_000.0
    row = {
        "slug": "camp-cli",
        "workspace_path": "/w/camp-cli",
        "state": inventory.STATE_RUNNING,
        "window_count": 3,
        "last_touched": now - 60,
    }
    cells = inventory.workspace_row_cells(row, now=now, show_group=False)
    assert cells == ["camp-cli", "3", "1m ago"]


def test_workspace_row_cells_relayed_row_iso_last_touched():
    """A relayed/merged row's `last_touched` arrives as an ISO string (the
    `--json` wire shape), not an epoch float — the cell builder must read
    either representation the same way."""
    inventory, _, _, _ = _import()
    now = 1_700_000_000.0
    row = {
        "slug": "camp-cli",
        "workspace_path": "/w/camp-cli",
        "state": inventory.STATE_RUNNING,
        "window_count": 3,
        "last_touched": inventory_to_iso(now - 3600),
    }
    cells = inventory.workspace_row_cells(row, now=now, show_group=False)
    assert cells == ["camp-cli", "3", "1h ago"]


def inventory_to_iso(ts):
    from camp.launch.lasttouched import to_iso_utc

    return to_iso_utc(ts)


def test_workspace_row_cells_unmanaged_row_uses_tmux_session_as_workspace_cell():
    inventory, _, _, _ = _import()
    row = {
        "slug": None,
        "tmux_session": "camp-somebody-deadbeef",
        "workspace_path": None,
        "state": inventory.STATE_UNMANAGED,
        "window_count": 2,
        "last_touched": None,
    }
    cells = inventory.workspace_row_cells(row, now=1_700_000_000.0, show_group=False)
    assert cells == ["camp-somebody-deadbeef", "2", inventory.HUMAN_ABSENT]


def test_workspace_row_cells_show_group_appends_group_column():
    inventory, _, _, _ = _import()
    row = {
        "slug": "camp-cli",
        "workspace_path": "/w/camp-cli",
        "state": inventory.STATE_NONE,
        "window_count": 0,
        "group": "trailhead",
        "last_touched": None,
    }
    cells = inventory.workspace_row_cells(row, now=1_700_000_000.0, show_group=True)
    assert cells == ["camp-cli", "0", inventory.HUMAN_ABSENT, "trailhead"]


def test_workspace_row_cells_raises_keyerror_on_missing_workspace_path():
    inventory, _, _, _ = _import()
    row = {"slug": "camp-cli", "state": inventory.STATE_NONE}
    import pytest

    with pytest.raises(KeyError):
        inventory.workspace_row_cells(row, now=1_700_000_000.0, show_group=False)


def test_workspace_row_cells_escapes_a_control_character_in_a_peer_supplied_field():
    inventory, _, _, _ = _import()
    row = {
        "slug": None,
        "tmux_session": "camp-evil\nrow-deadbeef",
        "workspace_path": None,
        "state": inventory.STATE_UNMANAGED,
        "window_count": 1,
        "last_touched": None,
    }
    cells = inventory.workspace_row_cells(row, now=1_700_000_000.0, show_group=False)
    assert "\n" not in cells[0]
    assert "\\x0a" in cells[0]
