"""Tests for launch/stop.py — the stop engine.

Test contract:
- A ref resolving to exactly one candidate stops that session, and the tmux
  name targeted is the candidate's own ``derived_name`` — never reconstructed
  from parts and never prefix-matched.
- An ambiguous ref comes back as the resolver's own ``Ambiguous``, with no
  signal sent: park and resume share one resolver and one ambiguity contract.
- Ownership is proven from the pane command, not from the name. A pane
  occupying the derived name whose command is anything else is refused. BOTH
  shapes camp composes are accepted — the launch shape and the resume shape —
  because every session that has ever been resumed carries the second one, and
  a check written against the launch shape alone would refuse the whole
  steady-state population park creates.
- The concierge anchor is refused by an explicit gate, proven with the anchor
  in the candidate pool AND owning a tmux session whose command matches — so
  the refusal is the gate's doing, not a side effect of the anchor happening to
  own nothing.
- Stopping the caller's own session refuses, on the session id the harness
  publishes into the environment camp is running in.
- Success is absence: a session still enumerable after the kill is a distinct
  outcome, not a success.
- The already-down oracle, all three branches: not-live with no tmux session is
  already-down; live with no tmux session is refused, under its own reason,
  as one camp did not launch; a tmux session that is not live is killed anyway to release the name.
- A second stop of the same ref is success (already-down), not an error.
- A tmux that does not answer is its own refusal, never a stop: absence of the
  name is the only evidence of success, and an unanswered question is not
  absence. Every wait the engine takes is bounded.

Nothing here shells out: the harness is a stand-in and tmux is an in-memory
fake, so the engine is exercised on a machine with no tmux and no harness.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)

_UUID_A = "aaaaaaaa-1111-4111-8111-111111111111"
_UUID_B = "bbbbbbbb-2222-4222-8222-222222222222"
_UUID_ANCHOR = "cccccccc-3333-4333-8333-333333333333"


def _env(state_root: Path, **extra: str) -> dict[str, str]:
    return {
        "CAMP_STATE_DIR": str(state_root),
        "HOME": str(state_root.parent / "home"),
        "CONCIERGE_STATE_DIR": str(state_root.parent / "concierge"),
        **extra,
    }


def _group(name: str) -> dict[str, Any]:
    return {"group": {"name": name}}


def _workspace(state_root: Path, group: str, slug: str) -> Path:
    ws = state_root / group / "worktrees" / slug
    ws.mkdir(parents=True)
    return ws


def _transcript(session_id: str, cwd: Path | None, *, age_seconds: float = 60.0):
    from trailhead.harness.base import SessionTranscript

    return SessionTranscript(
        session_id=session_id,
        cwd=cwd,
        modified_at=_NOW - timedelta(seconds=age_seconds),
    )


def _record(session_id: str, cwd: Path):
    from trailhead.harness.base import SessionRecord

    return SessionRecord(
        session_id=session_id,
        cwd=cwd,
        kind="agent",
        controllable=True,
        name=None,
        pid=None,
        started_at=None,
    )


class _FakeHarness:
    """Stand-in for the harness seam: the two launch shapes plus the scrub."""

    name = "fakeharness"

    def session_launch(self, workspace, session_id, *, session_name=None):
        argv = ["fakeharness", "--control", "--sid", session_id]
        if session_name is not None:
            argv += ["--name", session_name]
        return argv

    def session_resume(self, session_id):
        return ["fakeharness", "--reenter", session_id]

    def session_launch_env_unset(self):
        return ["FAKE_TOKEN", "FAKE_SOCKET"]


class _FakeTmux:
    """tmux, as a dict of session name -> pane start command."""

    def __init__(self, panes: dict[str, str] | None = None, *, undead: bool = False) -> None:
        self.panes = dict(panes or {})
        self.undead = undead
        self.killed: list[str] = []

    def has_session(self, name: str) -> bool:
        return name in self.panes

    def pane_command(self, name: str) -> str | None:
        return self.panes.get(name)

    def kill_session(self, name: str) -> None:
        self.killed.append(name)
        if not self.undead:
            self.panes.pop(name, None)


def _launched_pane(harness, session_id: str, derived_name: str, workspace: Path) -> str:
    """The pane command camp composes for a fresh launch."""
    scrub = " ".join(f"-u {name}" for name in harness.session_launch_env_unset())
    argv = harness.session_launch(workspace, session_id, session_name=derived_name)
    return f"env {scrub} " + " ".join(argv)


def _resumed_pane(harness, session_id: str) -> str:
    """The pane command camp composes for a resume — a different shape."""
    scrub = " ".join(f"-u {name}" for name in harness.session_launch_env_unset())
    argv = harness.session_resume(session_id)
    return f"env {scrub} " + " ".join(argv)


def _stop(ref, *, tmux, transcripts=(), live_records=(), groups=None, env, harness=None):
    from camp.launch.stop import stop_session

    return stop_session(
        ref,
        harness=harness if harness is not None else _FakeHarness(),
        transcripts=transcripts,
        live_records=live_records,
        groups=groups if groups is not None else [_group("g")],
        env=env,
        tmux=tmux,
        now=_NOW,
        sleep=lambda _seconds: None,
    )


def _fixture(tmp_path: Path, *, slug: str = "feat-a", session_id: str = _UUID_A):
    """One live, camp-launched session in a configured workspace."""
    state = tmp_path / "state"
    ws = _workspace(state, "g", slug)
    env = _env(state)
    harness = _FakeHarness()
    derived = f"camp-{slug}-{session_id[:8]}"
    tmux = _FakeTmux({derived: _launched_pane(harness, session_id, derived, ws)})
    return state, ws, env, harness, derived, tmux


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_a_ref_resolving_to_one_candidate_stops_it_under_its_derived_name(tmp_path: Path) -> None:
    from camp.launch.stop import Stopped

    state, ws, env, harness, derived, tmux = _fixture(tmp_path)

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert isinstance(outcome, Stopped)
    assert outcome.candidate.session_id == _UUID_A
    assert tmux.killed == [derived]
    assert outcome.candidate.derived_name == derived


def test_an_ambiguous_ref_returns_candidates_and_sends_no_signal(tmp_path: Path) -> None:
    from camp.launch.recovery import Ambiguous

    state = tmp_path / "state"
    ws_a = _workspace(state, "g", "feat-a")
    ws_b = _workspace(state, "g", "feat-b")
    tmux = _FakeTmux()

    outcome = _stop(
        "camp-feat-",
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws_a), _transcript(_UUID_B, ws_b)],
        env=_env(state),
    )

    assert isinstance(outcome, Ambiguous)
    assert {c.session_id for c in outcome.candidates} == {_UUID_A, _UUID_B}
    assert tmux.killed == []


def test_a_ref_matching_nothing_reports_no_match_and_sends_no_signal(tmp_path: Path) -> None:
    from camp.launch.recovery import NoMatch

    state = tmp_path / "state"
    ws = _workspace(state, "g", "feat-a")
    tmux = _FakeTmux()

    outcome = _stop(
        "zzzz", tmux=tmux, transcripts=[_transcript(_UUID_A, ws)], env=_env(state)
    )

    assert isinstance(outcome, NoMatch)
    assert outcome.pool_size == 1
    assert tmux.killed == []


# ---------------------------------------------------------------------------
# Ownership — the pane command, not the name
# ---------------------------------------------------------------------------


def test_a_pane_holding_the_name_with_a_foreign_command_is_refused(tmp_path: Path) -> None:
    from camp.launch.stop import REFUSED_NOT_CAMP_LAUNCHED, Refused

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    tmux = _FakeTmux({derived: "sleep 100000"})

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert isinstance(outcome, Refused)
    assert outcome.reason == REFUSED_NOT_CAMP_LAUNCHED
    assert tmux.killed == []


def test_a_resumed_panes_command_is_owned_too(tmp_path: Path) -> None:
    """A resumed pane carries a DIFFERENT shape — and is the steady state park
    creates. A check bound to the launch shape alone would refuse every session
    that has ever been resumed."""
    from camp.launch.stop import Stopped

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    tmux = _FakeTmux({derived: _resumed_pane(harness, _UUID_A)})

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert isinstance(outcome, Stopped)
    assert tmux.killed == [derived]


def test_a_camp_shaped_pane_carrying_another_sessions_id_is_refused(tmp_path: Path) -> None:
    """The shape alone is not enough: it must be THIS session's command."""
    from camp.launch.stop import REFUSED_NOT_CAMP_LAUNCHED, Refused

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    tmux = _FakeTmux({derived: _resumed_pane(harness, _UUID_B)})

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert isinstance(outcome, Refused)
    assert outcome.reason == REFUSED_NOT_CAMP_LAUNCHED
    assert tmux.killed == []


# ---------------------------------------------------------------------------
# The gates
# ---------------------------------------------------------------------------


def test_the_concierge_anchor_is_refused_even_when_it_owns_a_matching_session(
    tmp_path: Path,
) -> None:
    """The anchor is in the pool and owns a tmux session whose command passes
    the ownership check. Only the explicit gate stands between it and a
    lockout."""
    from camp.launch.stop import REFUSED_ANCHOR, Refused

    state = tmp_path / "state"
    ws = _workspace(state, "g", "concierge")
    env = _env(state)
    concierge_state = Path(env["CONCIERGE_STATE_DIR"])
    concierge_state.mkdir(parents=True)
    (concierge_state / "session_id").write_text(f"{_UUID_ANCHOR}\n", encoding="utf-8")

    harness = _FakeHarness()
    derived = f"camp-concierge-{_UUID_ANCHOR[:8]}"
    tmux = _FakeTmux({derived: _launched_pane(harness, _UUID_ANCHOR, derived, ws)})

    outcome = _stop(
        _UUID_ANCHOR[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_ANCHOR, ws)],
        live_records=[_record(_UUID_ANCHOR, ws)],
        env=env,
        harness=harness,
    )

    assert isinstance(outcome, Refused)
    assert outcome.reason == REFUSED_ANCHOR
    assert tmux.killed == []


def test_stopping_the_callers_own_session_refuses(tmp_path: Path) -> None:
    from camp.launch.identity import SESSION_ID_ENV_VARS
    from camp.launch.stop import REFUSED_SELF, Refused

    state, ws, _env_, harness, derived, tmux = _fixture(tmp_path)
    env = _env(state, **{SESSION_ID_ENV_VARS[0]: _UUID_A})

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert isinstance(outcome, Refused)
    assert outcome.reason == REFUSED_SELF
    assert tmux.killed == []


def test_another_sessions_id_in_the_environment_does_not_refuse(tmp_path: Path) -> None:
    from camp.launch.identity import SESSION_ID_ENV_VARS
    from camp.launch.stop import Stopped

    state, ws, _env_, harness, derived, tmux = _fixture(tmp_path)
    env = _env(state, **{SESSION_ID_ENV_VARS[0]: _UUID_B})

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert isinstance(outcome, Stopped)


# ---------------------------------------------------------------------------
# Success is absence
# ---------------------------------------------------------------------------


def test_a_session_still_present_after_the_kill_is_not_a_success(tmp_path: Path) -> None:
    from camp.launch.stop import StillPresent

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    tmux = _FakeTmux(
        {derived: _launched_pane(harness, _UUID_A, derived, ws)}, undead=True
    )

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert isinstance(outcome, StillPresent)
    assert outcome.candidate.derived_name == derived
    assert tmux.killed  # the kill WAS issued; issuance is not the success test


# ---------------------------------------------------------------------------
# The already-down oracle — all three branches
# ---------------------------------------------------------------------------


def test_not_live_with_no_tmux_session_is_already_down(tmp_path: Path) -> None:
    from camp.launch.stop import AlreadyDown

    state = tmp_path / "state"
    ws = _workspace(state, "g", "feat-a")
    tmux = _FakeTmux()

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        env=_env(state),
    )

    assert isinstance(outcome, AlreadyDown)
    assert tmux.killed == []


def test_live_with_no_tmux_session_is_refused_as_one_camp_did_not_launch(
    tmp_path: Path,
) -> None:
    from camp.launch.stop import REFUSED_LIVE_WITHOUT_SESSION, Refused

    state = tmp_path / "state"
    ws = _workspace(state, "g", "feat-a")
    tmux = _FakeTmux()

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=_env(state),
    )

    assert isinstance(outcome, Refused)
    assert outcome.reason == REFUSED_LIVE_WITHOUT_SESSION
    assert tmux.killed == []


def test_a_tmux_session_that_is_not_live_is_killed_anyway_to_release_the_name(
    tmp_path: Path,
) -> None:
    from camp.launch.stop import Stopped

    state, ws, env, harness, derived, tmux = _fixture(tmp_path)

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[],
        env=env,
        harness=harness,
    )

    assert isinstance(outcome, Stopped)
    assert outcome.candidate.live is False
    assert tmux.killed == [derived]


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


def test_a_second_stop_of_the_same_ref_is_success(tmp_path: Path) -> None:
    from camp.launch.stop import AlreadyDown, Stopped

    state, ws, env, harness, derived, tmux = _fixture(tmp_path)
    transcripts = [_transcript(_UUID_A, ws)]

    first = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=transcripts,
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )
    assert isinstance(first, Stopped)

    # The session is gone: no tmux session, and it no longer enumerates live.
    second = _stop(
        _UUID_A[:8], tmux=tmux, transcripts=transcripts, env=env, harness=harness
    )

    assert isinstance(second, AlreadyDown)


# ---------------------------------------------------------------------------
# A tmux that will not answer
# ---------------------------------------------------------------------------


class _MuteTmux(_FakeTmux):
    """tmux as an unanswering process: every existence question times out.

    `Tmux.has_session` degrades a timed-out or unlaunchable call to ``None``,
    and this fake reproduces that at the seam rather than by shelling out.
    """

    def has_session(self, name: str) -> bool | None:
        return None


def test_a_tmux_that_never_answers_is_refused_rather_than_reported_stopped(
    tmp_path: Path,
) -> None:
    """The one outcome a hung tmux must never produce is a success.

    `has_session` cannot answer, and absence is the ONLY evidence of a stop —
    so an unanswered question is its own refusal, distinct from both
    already-down and still-present.
    """
    from camp.launch.stop import REFUSED_TMUX_UNANSWERED, Refused

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    tmux = _MuteTmux({derived: _launched_pane(harness, _UUID_A, derived, ws)})

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert isinstance(outcome, Refused)
    assert outcome.reason == REFUSED_TMUX_UNANSWERED
    assert tmux.killed == []


def test_a_tmux_that_stops_answering_after_the_kill_is_refused_not_stopped(
    tmp_path: Path,
) -> None:
    """The kill went out and then tmux went quiet: camp does not know."""
    from camp.launch.stop import REFUSED_TMUX_UNANSWERED, Refused

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)

    class _GoesQuiet(_FakeTmux):
        def has_session(self, name):
            if self.killed:
                return None
            return super().has_session(name)

    tmux = _GoesQuiet({derived: _launched_pane(harness, _UUID_A, derived, ws)})

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert isinstance(outcome, Refused)
    assert outcome.reason == REFUSED_TMUX_UNANSWERED
    assert tmux.killed == [derived]


def test_every_tmux_wait_is_bounded_by_a_phone_usable_budget() -> None:
    """A stop is run from a phone. Every wait in the engine is bounded, and the
    whole worst case stays inside a handful of seconds — an unbounded wait with
    no output is indistinguishable from a hang."""
    from camp.launch import stop

    assert 0 < stop.TMUX_TIMEOUT_SECONDS <= 10
    assert 0 < stop.POLL_TIMEOUT_SECONDS <= 10
    assert 0 < stop.POLL_INTERVAL_SECONDS <= stop.POLL_TIMEOUT_SECONDS


def test_the_re_poll_is_bounded_in_wall_clock_not_in_sleep_time(tmp_path: Path) -> None:
    """The budget an operator experiences is WALL CLOCK, and the re-poll's own
    calls are what spend it: every `has_session` may itself burn a full
    `TMUX_TIMEOUT_SECONDS` before answering. Counting only the sleeps would let
    a busy tmux stretch a 5-second budget into minutes with no output.

    The clock here is driven by the engine's own calls, so it measures the
    thing: the whole re-poll finishes inside the budget plus at most the one
    tmux call that was in flight when it expired.
    """
    from camp.launch import stop

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    clock = {"now": 0.0}

    class _SlowToAnswer(_FakeTmux):
        """Answers, but each answer costs a full tmux timeout."""

        def has_session(self, name: str) -> bool:
            clock["now"] += stop.TMUX_TIMEOUT_SECONDS
            return True

    tmux = _SlowToAnswer({derived: _launched_pane(harness, _UUID_A, derived, ws)})

    def _sleep(seconds: float) -> None:
        clock["now"] += seconds

    outcome = stop.stop_session(
        _UUID_A[:8],
        harness=harness,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        groups=[_group("g")],
        env=env,
        tmux=tmux,
        now=_NOW,
        sleep=_sleep,
        monotonic=lambda: clock["now"],
    )

    assert isinstance(outcome, stop.StillPresent)
    # The pre-kill existence probe, plus the re-poll: its budget plus the one
    # call that was in flight when the budget ran out.
    assert clock["now"] <= (
        stop.POLL_TIMEOUT_SECONDS + 2 * stop.TMUX_TIMEOUT_SECONDS
    )


# ---------------------------------------------------------------------------
# An unanswerable pane question is its own reason, never a foreign pane
# ---------------------------------------------------------------------------


def test_a_pane_question_tmux_never_answered_is_refused_as_unanswered(
    tmp_path: Path,
) -> None:
    """tmux answered `has-session` and then went quiet. Nothing is known about
    what holds the name, and reporting a squatter would send the operator
    hunting a pane that does not exist."""
    from camp.launch import stop

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)

    class _QuietPane(_FakeTmux):
        def pane_command(self, name: str):
            return stop.UNANSWERED

    tmux = _QuietPane({derived: _launched_pane(harness, _UUID_A, derived, ws)})

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert isinstance(outcome, stop.Refused)
    assert outcome.reason == stop.REFUSED_TMUX_UNANSWERED
    assert tmux.killed == []


def test_the_real_tmux_seam_separates_an_unanswered_pane_from_an_absent_one(
    monkeypatch,
) -> None:
    """The tri-state lives in the seam: a call that never returned is
    UNANSWERED, and a session with no pane is None."""
    import subprocess as _subprocess

    from camp.launch import stop

    def _timeout(*args, **kwargs):
        raise _subprocess.TimeoutExpired(cmd="tmux", timeout=1)

    monkeypatch.setattr(stop.subprocess, "run", _timeout)
    assert stop.Tmux().pane_command("camp-x") is stop.UNANSWERED

    def _empty(*args, **kwargs):
        return _subprocess.CompletedProcess(args=["tmux"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(stop.subprocess, "run", _empty)
    assert stop.Tmux().pane_command("camp-x") is None


def test_a_harness_that_raises_composing_the_scrub_refuses_rather_than_raising(
    tmp_path: Path,
) -> None:
    """Every harness call the ownership check makes is a third-party call. One
    that raises is a refusal — an ACTION verb's contract is one `camp kill:`
    line, not a traceback."""
    from camp.launch import stop

    state, ws, env, harness, derived, tmux = _fixture(tmp_path)

    class _BrokenScrub(_FakeHarness):
        def session_launch_env_unset(self):
            raise RuntimeError("third-party harness blew up")

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=_BrokenScrub(),
    )

    assert isinstance(outcome, stop.Refused)
    assert outcome.reason == stop.REFUSED_NOT_CAMP_LAUNCHED
    assert tmux.killed == []
