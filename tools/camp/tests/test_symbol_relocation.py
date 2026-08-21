"""The three symbols camp depends on no longer live under `camp/bookmark/`.

`harness_for` (harness-seam resolution), `groupless_subverb` (groupless subverb
classification), and the "who am I" session-id read are each imported from a home
named for what they are, so the bookmark package can be deleted without breaking
anything camp still needs.

Test contract:
- `camp.bookmark` exports neither `harness_for` nor `groupless_subverb`, and
  `camp.bookmark.capture` no longer carries the session-id env-var read — a later
  re-import cannot quietly reintroduce the dependency.
- The launch, sessions, and resume paths each still resolve the same harness for
  the same group, since those three consumers are what motivated the move.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


class _FakeHarness:
    name = "fake"


# ---------------------------------------------------------------------------
# bookmark no longer owns them
# ---------------------------------------------------------------------------


def test_bookmark_package_does_not_export_harness_for() -> None:
    import camp.bookmark as bookmark

    assert not hasattr(bookmark, "harness_for")


def test_bookmark_package_does_not_export_groupless_subverb() -> None:
    import camp.bookmark as bookmark

    assert not hasattr(bookmark, "groupless_subverb")


def test_bookmark_capture_does_not_own_the_session_id_read() -> None:
    import camp.bookmark.capture as capture

    assert not hasattr(capture, "_SESSION_ID_ENV_VARS")


# ---------------------------------------------------------------------------
# the new homes
# ---------------------------------------------------------------------------


def test_harness_for_lives_with_the_harness_profile() -> None:
    from camp.launch.profile import harness_for

    assert callable(harness_for)


def test_groupless_subverb_lives_with_the_dispatchers() -> None:
    from camp.cli.groupless import groupless_subverb

    assert groupless_subverb([]) is None


def test_current_session_id_lives_with_launch_identity() -> None:
    from camp.launch.identity import SESSION_ID_ENV_VARS, current_session_id

    assert current_session_id({SESSION_ID_ENV_VARS[0]: "sess-1"}) == "sess-1"
    assert current_session_id({}) is None


# ---------------------------------------------------------------------------
# the three consumers still resolve the same harness
# ---------------------------------------------------------------------------


def test_launch_path_resolves_the_group_harness(monkeypatch) -> None:
    """`camp launch`'s confirm step asks the group's harness (cli.session:launch_and_confirm)."""
    import camp.cli.session as cli_session

    harness = _FakeHarness()
    seen: list[dict] = []
    confirmed: list[object] = []

    def fake_harness_for(group):
        seen.append(group)
        return harness

    launched = type("LaunchedSession", (), {
        "session_id": "sess-1", "launch_dir": "/tmp/ws", "tmux_name": "camp-ws-abc",
    })()
    monkeypatch.setattr("camp.launch.profile.harness_for", fake_harness_for)
    monkeypatch.setattr("camp.launch.session.launch_session", lambda *a, **k: launched)
    monkeypatch.setattr(
        "camp.launch.session.confirm_session",
        lambda h, l, env=None: confirmed.append(h),
    )

    group = {"group": {"name": "g"}}
    assert cli_session.launch_and_confirm(group, "ws", env={}) is launched
    assert seen == [group]
    assert confirmed == [harness]


def test_sessions_path_resolves_addressable_harnesses(monkeypatch) -> None:
    """`camp sessions` builds its harness pool via cli.session:_addressable_harnesses."""
    import camp.cli.session as cli_session

    harness = _FakeHarness()
    seen: list[dict] = []

    def fake_harness_for(config):
        seen.append(config)
        return harness

    monkeypatch.setattr("camp.launch.profile.harness_for", fake_harness_for)
    monkeypatch.setattr(cli_session, "_harness_display_name", lambda h: "fake")

    group = {"group": {"name": "g"}}
    assert cli_session._addressable_harnesses([group]) == [harness]
    assert seen == [group]


def test_resume_path_resolves_the_group_harness(monkeypatch) -> None:
    """`camp launch --resume`'s enumeration asks the group's harness (cli.session:_enumerate_sessions)."""
    import camp.cli.session as cli_session

    harness = _FakeHarness()
    seen: list[dict] = []

    def fake_harness_for(group):
        seen.append(group)
        return harness

    monkeypatch.setattr("camp.launch.profile.harness_for", fake_harness_for)
    monkeypatch.setattr(
        "camp.launch.session.enumerate_records", lambda h, ws, env: ["record"]
    )

    group = {"group": {"name": "g"}}
    assert cli_session._enumerate_sessions(group, None, {}) == ["record"]
    assert seen == [group]
