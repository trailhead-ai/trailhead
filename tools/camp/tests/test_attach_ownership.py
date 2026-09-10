"""Tests for `camp.attach.ownership` — the attach-time pane-ownership predicate.

Test contract:
- A session whose pane start command is one camp composed is attachable.
- A session whose pane start command is foreign is not attachable — and the
  same fixture is one `camp kill` also disowns, asserted through the kill
  path rather than restated.
- Both pane-command shapes the kill ownership check recognises are recognised
  here too; a fixture matching only one of them does not silently pass.
- A pane the multiplexer cannot describe at all is not attachable, and is
  distinguishable from a pane it describes as foreign.
- Both halves of that distinction are cross-checked against the kill path,
  not just the foreign one.
- Mutating what camp composes at launch changes this predicate's answer.

The predicate is reached by driving `stop._is_camp_launched` — the exact
check `camp kill` applies — never by restating its rule beside it. Every
fixture here is shared with `test_launch_stop.py` for that reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_TESTS_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_launch_stop import (  # noqa: E402
    _fixture,
    _group,
    _launched_pane,
    _record,
    _resumed_pane,
    _stop,
    _transcript,
    _FakeHarness,
    _FakeTmux,
    _UUID_A,
)


def _candidate(tmp_path: Path):
    from camp.launch.recovery import resolve_session_ref

    state, ws, env, harness, derived, tmux = _fixture(tmp_path)
    resolution = resolve_session_ref(
        _UUID_A[:8],
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("g")],
        env=env,
        now=None,
    )
    return state, ws, env, harness, derived, tmux, resolution.candidate


def test_owned_pane_is_attachable(tmp_path: Path) -> None:
    from camp.attach.ownership import Owned, resolve_ownership

    state, ws, env, harness, derived, tmux, candidate = _candidate(tmp_path)

    answer = resolve_ownership(harness, tmux, candidate)

    assert isinstance(answer, Owned)


def test_foreign_pane_is_not_attachable_cross_checked_with_kill(tmp_path: Path) -> None:
    from camp.attach.ownership import Foreign, resolve_ownership
    from camp.launch.stop import REFUSED_NOT_CAMP_LAUNCHED, Refused

    state, ws, env, harness, derived, _tmux, candidate = _candidate(tmp_path)

    foreign_tmux = _FakeTmux({derived: "sleep 100000"})

    answer = resolve_ownership(harness, foreign_tmux, candidate)
    assert isinstance(answer, Foreign)

    outcome = _stop(
        _UUID_A[:8],
        tmux=foreign_tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )
    assert isinstance(outcome, Refused)
    assert outcome.reason == REFUSED_NOT_CAMP_LAUNCHED


def test_both_owning_shapes_are_recognised(tmp_path: Path) -> None:
    from camp.attach.ownership import Owned, resolve_ownership

    state, ws, env, harness, derived, _tmux, candidate = _candidate(tmp_path)

    launched_tmux = _FakeTmux({derived: _launched_pane(harness, _UUID_A, derived, ws)})
    resumed_tmux = _FakeTmux({derived: _resumed_pane(harness, _UUID_A)})

    assert isinstance(resolve_ownership(harness, launched_tmux, candidate), Owned)
    assert isinstance(resolve_ownership(harness, resumed_tmux, candidate), Owned)


def test_undescribable_pane_is_distinct_from_foreign(tmp_path: Path) -> None:
    from camp.attach.ownership import Foreign, Undescribable, resolve_ownership
    from camp.launch import stop

    state, ws, env, harness, derived, _tmux, candidate = _candidate(tmp_path)

    class _QuietPane(_FakeTmux):
        def pane_command(self, name: str):
            return stop.UNANSWERED

    undescribable_tmux = _QuietPane({derived: _launched_pane(harness, _UUID_A, derived, ws)})
    foreign_tmux = _FakeTmux({derived: "sleep 100000"})

    undescribable_answer = resolve_ownership(harness, undescribable_tmux, candidate)
    foreign_answer = resolve_ownership(harness, foreign_tmux, candidate)

    assert isinstance(undescribable_answer, Undescribable)
    assert isinstance(foreign_answer, Foreign)
    assert type(undescribable_answer) is not type(foreign_answer)


def test_undescribable_and_foreign_are_cross_checked_against_kill(tmp_path: Path) -> None:
    from camp.attach.ownership import Foreign, Undescribable, resolve_ownership
    from camp.launch import stop
    from camp.launch.stop import (
        REFUSED_NOT_CAMP_LAUNCHED,
        REFUSED_TMUX_UNANSWERED,
        Refused,
    )

    state, ws, env, harness, derived, _tmux, candidate = _candidate(tmp_path)

    class _QuietPane(_FakeTmux):
        def pane_command(self, name: str):
            return stop.UNANSWERED

    undescribable_tmux = _QuietPane({derived: _launched_pane(harness, _UUID_A, derived, ws)})
    foreign_tmux = _FakeTmux({derived: "sleep 100000"})

    undescribable_outcome = _stop(
        _UUID_A[:8],
        tmux=undescribable_tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )
    foreign_outcome = _stop(
        _UUID_A[:8],
        tmux=foreign_tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert isinstance(undescribable_outcome, Refused)
    assert undescribable_outcome.reason == REFUSED_TMUX_UNANSWERED
    assert isinstance(foreign_outcome, Refused)
    assert foreign_outcome.reason == REFUSED_NOT_CAMP_LAUNCHED
    assert undescribable_outcome.reason != foreign_outcome.reason

    assert isinstance(resolve_ownership(harness, undescribable_tmux, candidate), Undescribable)
    assert isinstance(resolve_ownership(harness, foreign_tmux, candidate), Foreign)


def test_mutating_composed_launch_argv_changes_the_answer(tmp_path: Path) -> None:
    from camp.attach.ownership import Foreign, Owned, resolve_ownership

    state, ws, env, harness, derived, _tmux, candidate = _candidate(tmp_path)

    original_tmux = _FakeTmux({derived: _launched_pane(harness, _UUID_A, derived, ws)})
    assert isinstance(resolve_ownership(harness, original_tmux, candidate), Owned)

    class _MutatedHarness(_FakeHarness):
        """Composes an EXTRA flag camp did not compose before."""

        def session_launch(self, workspace, session_id, *, session_name=None, settings_path=None):
            argv = ["fakeharness", "--control", "--sid", session_id, "--mutated-flag"]
            if session_name is not None:
                argv += ["--name", session_name]
            return argv

    mutated_harness = _MutatedHarness()

    # Same pane command as before, but what camp composes moved: the old
    # pane no longer matches under the mutated harness.
    assert isinstance(resolve_ownership(mutated_harness, original_tmux, candidate), Foreign)

    mutated_tmux = _FakeTmux({derived: _launched_pane(mutated_harness, _UUID_A, derived, ws)})
    assert isinstance(resolve_ownership(mutated_harness, mutated_tmux, candidate), Owned)
    # And the old harness no longer recognises the newly-composed pane.
    assert isinstance(resolve_ownership(harness, mutated_tmux, candidate), Foreign)
