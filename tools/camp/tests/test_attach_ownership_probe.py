"""EPHEMERAL assumption probe for task/attach-only-offers-panes-camp-owns.

Resolves the unknown: is `stop._is_camp_launched` reachable as a standalone
predicate `camp attach` can call, and what does it need in hand?

Delete this file (and this file only) once the unknown is dispositioned --
it is not meant to survive into the real attach-ownership module.
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


# ---------------------------------------------------------------------------
# Q1 + Q2: signature, and what it needs in hand
# ---------------------------------------------------------------------------


def test_is_camp_launched_is_callable_standalone_with_harness_candidate_pane(
    tmp_path: Path,
) -> None:
    """`_is_camp_launched(harness, candidate, pane_command)` is a plain
    module-level function -- importable and callable with nothing but the
    harness seam, a `SessionCandidate`, and a pane-command string. No `Tmux`
    instance, no live tmux session, no kill request in flight."""
    from camp.launch.recovery import resolve_session_ref
    from camp.launch.stop import _is_camp_launched

    state, ws, env, harness, derived, tmux = _fixture(tmp_path)
    resolution = resolve_session_ref(
        _UUID_A[:8],
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("g")],
        env=env,
        now=None,
    )
    candidate = resolution.candidate
    pane_command = tmux.panes[derived]

    assert _is_camp_launched(harness, candidate, pane_command) is True


def test_owned_and_foreign_panes_agree_with_the_kill_path(tmp_path: Path) -> None:
    """The predicate's True/False answer is cross-checked against what
    `stop_session` (the kill path) does with the exact same fixture."""
    from camp.launch.stop import REFUSED_NOT_CAMP_LAUNCHED, Refused, Stopped, _is_camp_launched

    state, ws, env, harness, derived, tmux = _fixture(tmp_path)
    from camp.launch.recovery import resolve_session_ref

    resolution = resolve_session_ref(
        _UUID_A[:8],
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("g")],
        env=env,
        now=None,
    )
    candidate = resolution.candidate

    owned_pane = tmux.panes[derived]
    assert _is_camp_launched(harness, candidate, owned_pane) is True
    owned_outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )
    assert isinstance(owned_outcome, Stopped)

    foreign_tmux = _FakeTmux({derived: "sleep 100000"})
    foreign_pane = foreign_tmux.panes[derived]
    assert _is_camp_launched(harness, candidate, foreign_pane) is False
    foreign_outcome = _stop(
        _UUID_A[:8],
        tmux=foreign_tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )
    assert isinstance(foreign_outcome, Refused)
    assert foreign_outcome.reason == REFUSED_NOT_CAMP_LAUNCHED


def test_both_owning_shapes_are_recognised_by_the_predicate_alone(tmp_path: Path) -> None:
    """Both pane-command shapes the kill path's ownership check recognises --
    freshly launched and resumed -- are recognised by the bare predicate too."""
    from camp.launch.recovery import resolve_session_ref
    from camp.launch.stop import _is_camp_launched

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    resolution = resolve_session_ref(
        _UUID_A[:8],
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("g")],
        env=env,
        now=None,
    )
    candidate = resolution.candidate

    launched = _launched_pane(harness, _UUID_A, derived, ws)
    resumed = _resumed_pane(harness, _UUID_A)

    assert _is_camp_launched(harness, candidate, launched) is True
    assert _is_camp_launched(harness, candidate, resumed) is True


# ---------------------------------------------------------------------------
# Q3: is the three-way (owned / foreign / undescribable) distinction
# observable to a caller of the predicate ALONE?
# ---------------------------------------------------------------------------


def test_undescribable_is_not_a_third_answer_of_the_predicate_itself(tmp_path: Path) -> None:
    """`_is_camp_launched` takes `pane_command: str | None` -- a two-valued
    input. The `_Unanswered` sentinel `tmux.pane_command()` can return is a
    THIRD state that `stop_session` branches on *before* ever calling
    `_is_camp_launched` (stop.py's own `if isinstance(pane, _Unanswered): ...`
    guard, ahead of the ownership call). The predicate alone never sees it and
    cannot distinguish it from either True or False -- a caller of the
    predicate in isolation must re-implement that upstream isinstance check
    itself, using the same `Tmux.pane_command` seam, to recover the
    distinction stop_session makes."""
    from camp.launch.recovery import resolve_session_ref
    from camp.launch.stop import UNANSWERED, _Unanswered, _is_camp_launched

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    resolution = resolve_session_ref(
        _UUID_A[:8],
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("g")],
        env=env,
        now=None,
    )
    candidate = resolution.candidate

    # UNANSWERED is not a str, so shlex.split explodes on it inside the
    # predicate: it is not a value the predicate's own contract accepts, and
    # it is not silently treated as foreign (False) either.
    assert isinstance(UNANSWERED, _Unanswered)
    raised = False
    try:
        _is_camp_launched(harness, candidate, UNANSWERED)  # type: ignore[arg-type]
    except (TypeError, AttributeError):
        raised = True
    assert raised, (
        "the predicate has no branch for _Unanswered -- a caller must filter "
        "it out before calling, exactly as stop_session does"
    )


def test_undescribable_cross_checked_against_kill_distinct_from_foreign(
    tmp_path: Path,
) -> None:
    """Both halves of the owned/foreign/undescribable distinction -- not just
    the foreign one -- are cross-checked against the kill path, and land on
    DIFFERENT refusal reasons."""
    from camp.launch import stop
    from camp.launch.stop import REFUSED_NOT_CAMP_LAUNCHED, REFUSED_TMUX_UNANSWERED, Refused

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)

    class _QuietPane(_FakeTmux):
        def pane_command(self, name: str):
            return stop.UNANSWERED

    undescribable_tmux = _QuietPane({derived: _launched_pane(harness, _UUID_A, derived, ws)})
    undescribable_outcome = _stop(
        _UUID_A[:8],
        tmux=undescribable_tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )
    assert isinstance(undescribable_outcome, Refused)
    assert undescribable_outcome.reason == REFUSED_TMUX_UNANSWERED

    foreign_tmux = _FakeTmux({derived: "sleep 100000"})
    foreign_outcome = _stop(
        _UUID_A[:8],
        tmux=foreign_tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )
    assert isinstance(foreign_outcome, Refused)
    assert foreign_outcome.reason == REFUSED_NOT_CAMP_LAUNCHED

    assert undescribable_outcome.reason != foreign_outcome.reason


# ---------------------------------------------------------------------------
# Q4: is the predicate genuinely derived from what camp composes at launch?
# ---------------------------------------------------------------------------


def test_mutating_what_the_harness_composes_moves_the_predicates_answer(
    tmp_path: Path,
) -> None:
    """Prove derivation, not restatement: change what `session_launch`
    composes and watch `_is_camp_launched`'s answer for the SAME pane command
    flip, with no change to the predicate itself."""
    from camp.launch.recovery import resolve_session_ref
    from camp.launch.stop import _is_camp_launched

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    resolution = resolve_session_ref(
        _UUID_A[:8],
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("g")],
        env=env,
        now=None,
    )
    candidate = resolution.candidate

    original_pane = _launched_pane(harness, _UUID_A, derived, ws)
    assert _is_camp_launched(harness, candidate, original_pane) is True

    class _MutatedHarness(_FakeHarness):
        """Composes an EXTRA flag camp did not compose before."""

        def session_launch(self, workspace, session_id, *, session_name=None, settings_path=None):
            argv = ["fakeharness", "--control", "--sid", session_id, "--mutated-flag"]
            if session_name is not None:
                argv += ["--name", session_name]
            return argv

    mutated_harness = _MutatedHarness()

    # Same pane command as before, but what camp composes moved: the old
    # pane no longer matches, because it never carried --mutated-flag.
    assert _is_camp_launched(mutated_harness, candidate, original_pane) is False

    # The pane camp NOW composes is accepted.
    mutated_pane = _launched_pane(mutated_harness, _UUID_A, derived, ws)
    assert mutated_pane != original_pane
    assert _is_camp_launched(mutated_harness, candidate, mutated_pane) is True
    # And the old harness no longer recognises the newly-composed pane.
    assert _is_camp_launched(harness, candidate, mutated_pane) is False
