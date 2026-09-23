"""Tests for launch/stop.py — the stop engine.

Test contract:
- A ref resolving to exactly one candidate stops that session, and the tmux
  name targeted is the candidate's own ``derived_name`` — never reconstructed
  from parts and never prefix-matched.
- An ambiguous ref comes back as the resolver's own ``Ambiguous``, with no
  signal sent: park and resume share one resolver and one ambiguity contract.
- Ownership is proven from the pane command, not from the name. A pane
  occupying the derived name whose command is anything else is refused. Only
  the resume shape is accepted — every session that has ever been resumed
  carries it, which is the steady-state population park creates. A pane
  carrying the retired launch composition (`--remote-control`/`--name`) is
  refused: no method on the seam composes that shape any more.
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
- Both wall-clock budgets resolve from the environment when the caller names
  neither: an override moves the default, an explicit ``timeout=`` /
  ``poll_timeout=`` still wins, and a malformed or non-positive setting leaves
  the shipped budget alone.
- ``Tmux.list_sessions`` extends the same tri-state to a general listing
  rather than a scoped existence query: a no-server condition on stderr is the
  only non-zero exit answered as empty, every other non-zero exit (and an
  unanswerable ``_run``) is UNANSWERED, and a malformed row is dropped without
  blanking the rest of the answer — with the drop counted so a caller can tell
  the two apart.

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

#: The variable the stand-in harness binds an account with. Not Claude Code's
#: own name, so a recognizer matching a hardcoded prefix cannot pass.
ACCOUNT_KEY = "FAKE_ACCOUNT_DIR"


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
    """Stand-in for the harness seam: the resume shape plus the scrub."""

    name = "fakeharness"

    def session_resume(self, session_id):
        return ["fakeharness", "--reenter", session_id]

    def session_launch_env_unset(self):
        return ["FAKE_TOKEN", "FAKE_SOCKET"]

    def session_launch_env_set(self, account, *, env=None):
        """The account binding the launch engine composes into a pane.

        The key is deliberately NOT Claude Code's: a recognizer that hardcodes a
        variable name instead of asking the harness fails every test built on
        this stand-in.
        """
        return {ACCOUNT_KEY: str((env or {}).get("HOME", "/fake-home"))}


class _FakeTmux:
    """tmux, as a dict of session name -> pane start command."""

    def __init__(
        self,
        panes: dict[str, str] | None = None,
        *,
        undead: bool = False,
        windows: dict[str, int] | None = None,
    ) -> None:
        self.panes = dict(panes or {})
        self.undead = undead
        self.windows = dict(windows or {})
        self.killed: list[str] = []

    def has_session(self, name: str) -> bool:
        return name in self.panes

    def pane_command(self, name: str) -> str | None:
        return self.panes.get(name)

    def kill_session(self, name: str) -> None:
        self.killed.append(name)
        if not self.undead:
            self.panes.pop(name, None)

    def list_sessions(self):
        from camp.launch.stop import SessionListing, TmuxSession

        return SessionListing(
            sessions=tuple(
                TmuxSession(name=name, windows=self.windows.get(name, 1))
                for name in self.panes
            ),
        )


def _old_launch_pane(harness, session_id: str, derived_name: str) -> str:
    """A pane command carrying `--remote-control` and `--name` — the shape no
    method on the seam composes any more. Spelled as a literal argv, since
    there is no method left to ask for it."""
    scrub = " ".join(f"-u {name}" for name in harness.session_launch_env_unset())
    argv = ["fakeharness", "--remote-control", "--session-id", session_id, "--name", derived_name]
    return f"env {scrub} " + " ".join(argv)


def _resumed_pane(harness, session_id: str) -> str:
    """The pane command camp composes for a resume — a different shape."""
    scrub = " ".join(f"-u {name}" for name in harness.session_launch_env_unset())
    argv = harness.session_resume(session_id)
    return f"env {scrub} " + " ".join(argv)


def _stop(ref, *, tmux, transcripts=(), live_records=(), groups=None, env, harness=None, **overrides):
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
        **overrides,
    )


def _fixture(tmp_path: Path, *, slug: str = "feat-a", session_id: str = _UUID_A):
    """One live, camp-launched session in a configured workspace."""
    state = tmp_path / "state"
    ws = _workspace(state, "g", slug)
    env = _env(state)
    harness = _FakeHarness()
    derived = f"camp-{slug}-{session_id[:8]}"
    tmux = _FakeTmux({derived: _resumed_pane(harness, session_id)})
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


def test_the_old_launch_shape_is_refused_the_resume_shape_is_owned(
    tmp_path: Path,
) -> None:
    """Two inputs, two answers. The old launch composition — a literal fixture
    argv carrying `--remote-control` and `--name`, since no method on the seam
    composes it any more — is refused. The resume shape, behind the same
    scrub prefix, is owned: it is the steady state every parked-and-resumed
    session carries."""
    from camp.launch.stop import REFUSED_NOT_CAMP_LAUNCHED, Refused, Stopped

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)

    old_shape_tmux = _FakeTmux({derived: _old_launch_pane(harness, _UUID_A, derived)})
    old_shape_outcome = _stop(
        _UUID_A[:8],
        tmux=old_shape_tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )
    assert isinstance(old_shape_outcome, Refused)
    assert old_shape_outcome.reason == REFUSED_NOT_CAMP_LAUNCHED
    assert old_shape_tmux.killed == []

    resume_tmux = _FakeTmux({derived: _resumed_pane(harness, _UUID_A)})
    resume_outcome = _stop(
        _UUID_A[:8],
        tmux=resume_tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )
    assert isinstance(resume_outcome, Stopped)
    assert resume_tmux.killed == [derived]


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
    tmux = _FakeTmux({derived: _resumed_pane(harness, _UUID_ANCHOR)})

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
        {derived: _resumed_pane(harness, _UUID_A)}, undead=True
    )

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
        # The subject is the outcome, not the budget: the engine polls once
        # before it consults the deadline, so the shortest budget still
        # exercises the whole path. TestStopBudgetEnvironmentSeams is where
        # the budget itself is pinned.
        poll_timeout=0.0,
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
    tmux = _MuteTmux({derived: _resumed_pane(harness, _UUID_A)})

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

    tmux = _GoesQuiet({derived: _resumed_pane(harness, _UUID_A)})

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

    tmux = _SlowToAnswer({derived: _resumed_pane(harness, _UUID_A)})

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

    tmux = _QuietPane({derived: _resumed_pane(harness, _UUID_A)})

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


# ---------------------------------------------------------------------------
# ownership survives the config-dir assignment in the pane command
# ---------------------------------------------------------------------------


def _resumed_pane_with_account(harness, session_id: str, account_dir: str) -> str:
    """The pane command camp composes for a resume when an account binding rides the pane."""
    scrub = " ".join(f"-u {name}" for name in harness.session_launch_env_unset())
    argv = harness.session_resume(session_id)
    return f"env {scrub} {ACCOUNT_KEY}={account_dir} " + " ".join(argv)


def test_a_pane_carrying_the_account_binding_is_still_camp_launched(
    tmp_path: Path,
) -> None:
    """camp must recognize its own pane, and must learn the assignment's KEY from
    the harness rather than naming one itself. The kill-time environment supplies
    no VALUE: a session launched under one account is routinely stopped from a
    shell running under another, and ownership cannot depend on the two agreeing."""
    from camp.launch.stop import Stopped

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    tmux = _FakeTmux(
        {
            derived: _resumed_pane_with_account(
                harness, _UUID_A, "/home/someone/.account-other"
            )
        }
    )

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


def test_a_defaulted_pane_is_recognized_too(tmp_path: Path) -> None:
    """The defaulted launch composes the same shape with the harness's own
    default value — it is not a pane with no assignment at all."""
    from camp.launch.stop import Stopped

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    tmux = _FakeTmux(
        {
            derived: _resumed_pane_with_account(
                harness, _UUID_A, env["HOME"]
            )
        }
    )

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


def test_an_unrecognized_extra_operand_is_still_not_camp_launched(tmp_path: Path) -> None:
    """The tolerance is exactly the keys the harness names — it must not widen
    into accepting any extra operand, which would let camp reclaim a pane it
    never composed."""
    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    scrub = " ".join(f"-u {n}" for n in harness.session_launch_env_unset())
    argv = harness.session_resume(_UUID_A)
    pane = f"env {scrub} SOMETHING_ELSE=/tmp " + " ".join(argv)
    tmux = _FakeTmux({derived: pane})

    _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert tmux.killed == []


def test_a_relative_account_operand_is_not_camp_launched(tmp_path: Path) -> None:
    """The launch engine only ever composes an absolute value, so a relative one
    is not a shape camp could have produced."""
    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)
    tmux = _FakeTmux(
        {
            derived: _resumed_pane_with_account(
                harness, _UUID_A, "relative/.account"
            )
        }
    )

    _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=harness,
    )

    assert tmux.killed == []


def test_a_declared_account_pane_is_owned_even_when_the_default_states_nothing(
    tmp_path: Path,
) -> None:
    """A harness whose DEFAULT is the variable's absence answers `None` with an
    empty mapping — so probing with `None` learns no keys, and every pane camp
    launched for a group that DID declare an account stops being recognized as
    camp's own. The keys must be learned from a binding the harness actually
    composes."""
    from camp.launch.stop import Stopped

    state, ws, env, _harness, derived, _tmux = _fixture(tmp_path)

    class _AbsentDefault(_FakeHarness):
        def session_launch_env_set(self, account, *, env=None):
            if account is None:
                return {}
            return {ACCOUNT_KEY: account}

    harness = _AbsentDefault()
    tmux = _FakeTmux(
        {
            derived: _resumed_pane_with_account(
                harness, _UUID_A, "/home/someone/.account-other"
            )
        }
    )

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


def test_a_stop_time_environment_cannot_cost_the_probe_its_keys(
    tmp_path: Path,
) -> None:
    """Key learning must not depend on the shell `camp stop` runs in.

    A real harness refuses to compose a binding when the environment it is
    handed already states a conflicting config dir of its own — a legitimate
    refusal at launch, where the operator is choosing an account. Handing that
    same environment to the KEY probe turns it into a stop-time hazard: both
    probes raise, no keys are learned, and every camp-launched pane carrying an
    account assignment is refused as not-camp-launched. The probe asks only what
    the harness NAMES its assignments, so it must ask with an environment that
    can carry no conflict.
    """
    from camp.launch.stop import Stopped

    state, ws, env, _harness, derived, _tmux = _fixture(tmp_path)

    class _RefusesAConflictingEnv(_FakeHarness):
        def session_launch_env_set(self, account, *, env=None):
            if (env or {}).get("CONFLICTING_SEAM"):
                raise RuntimeError("the environment already states a config dir")
            return {ACCOUNT_KEY: account or "/fake-home"}

    harness = _RefusesAConflictingEnv()
    tmux = _FakeTmux(
        {
            derived: _resumed_pane_with_account(
                harness, _UUID_A, "/home/someone/.account-other"
            )
        }
    )
    stop_time_env = dict(env)
    stop_time_env["CONFLICTING_SEAM"] = str(tmp_path / "some-other-config-dir")

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=stop_time_env,
        harness=harness,
    )

    assert isinstance(outcome, Stopped)
    assert tmux.killed == [derived]


def test_a_harness_that_raises_composing_the_binding_refuses_rather_than_raising(
    tmp_path: Path,
) -> None:
    """Asking the harness for its keys is a third-party call like every other the
    ownership check makes: one that raises is a refusal, not a traceback."""
    from camp.launch import stop

    state, ws, env, harness, derived, _tmux = _fixture(tmp_path)

    class _BrokenBinding(_FakeHarness):
        def session_launch_env_set(self, account, *, env=None):
            raise RuntimeError("third-party harness blew up")

    broken = _BrokenBinding()
    tmux = _FakeTmux(
        {derived: _resumed_pane_with_account(broken, _UUID_A, env["HOME"])}
    )

    outcome = _stop(
        _UUID_A[:8],
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
        harness=broken,
    )

    assert isinstance(outcome, stop.Refused)
    assert outcome.reason == stop.REFUSED_NOT_CAMP_LAUNCHED
    assert tmux.killed == []


# ---------------------------------------------------------------------------
# Enumeration — Tmux.list_sessions
#
# Every test below drives the real seam (stop.Tmux()) with subprocess.run
# stubbed at the module level, per the pattern the pane_command tri-state
# tests above already use — never a fixed constant, never _FakeTmux, because
# the property under test is what Tmux.list_sessions does with what tmux
# printed.
# ---------------------------------------------------------------------------


def _completed(*, returncode: int, stdout: str = "", stderr: str = ""):
    import subprocess as _subprocess

    return _subprocess.CompletedProcess(
        args=["tmux"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_list_sessions_answers_empty_when_tmux_reports_no_rows(monkeypatch) -> None:
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess, "run", lambda *a, **k: _completed(returncode=0, stdout="")
    )

    result = stop.Tmux().list_sessions()

    assert isinstance(result, stop.SessionListing)
    assert result.sessions == ()


def test_list_sessions_parses_one_session(monkeypatch) -> None:
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="3|camp-feat-a-11112222\n"),
    )

    result = stop.Tmux().list_sessions()

    assert isinstance(result, stop.SessionListing)
    assert result.sessions == (stop.TmuxSession(name="camp-feat-a-11112222", windows=3),)


def test_list_sessions_parses_many_sessions(monkeypatch) -> None:
    from camp.launch import stop

    stdout = "1|camp-feat-a-11112222\n2|camp-feat-b-22223333\n5|not-camp-at-all\n"
    monkeypatch.setattr(
        stop.subprocess, "run", lambda *a, **k: _completed(returncode=0, stdout=stdout)
    )

    result = stop.Tmux().list_sessions()

    assert isinstance(result, stop.SessionListing)
    assert result.sessions == (
        stop.TmuxSession(name="camp-feat-a-11112222", windows=1),
        stop.TmuxSession(name="camp-feat-b-22223333", windows=2),
        stop.TmuxSession(name="not-camp-at-all", windows=5),
    )


def test_list_sessions_reads_the_window_count_per_row_not_a_default(monkeypatch) -> None:
    """Two sessions in one answer, with DIFFERENT counts — this fails if the
    count comes from a length or a hardcoded default rather than the row."""
    from camp.launch import stop

    stdout = "1|camp-feat-a-11112222\n7|camp-feat-b-22223333\n"
    monkeypatch.setattr(
        stop.subprocess, "run", lambda *a, **k: _completed(returncode=0, stdout=stdout)
    )

    result = stop.Tmux().list_sessions()

    counts = {s.name: s.windows for s in result.sessions}
    assert counts == {"camp-feat-a-11112222": 1, "camp-feat-b-22223333": 7}


def test_a_session_name_containing_the_delimiter_is_parsed_whole(monkeypatch) -> None:
    """This is the test that fails if the fields are ordered name-first or the
    split on the delimiter is unbounded rather than split-once."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="9|foo|bar\n"),
    )

    result = stop.Tmux().list_sessions()

    assert result.sessions == (stop.TmuxSession(name="foo|bar", windows=9),)


def test_a_session_name_carrying_a_pipe_survives_intact_with_the_correct_count(
    monkeypatch,
) -> None:
    """Measured as legal on tmux 3.7c via
    ``rename-session -t x 'camp-pipe|9|evil-1a2b3c4d'`` — a real input, not a
    hypothetical."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=0, stdout="9|camp-pipe|9|evil-1a2b3c4d\n"),
    )

    result = stop.Tmux().list_sessions()

    assert result.sessions == (
        stop.TmuxSession(name="camp-pipe|9|evil-1a2b3c4d", windows=9),
    )


def test_a_no_server_condition_answers_empty_not_unanswered(monkeypatch) -> None:
    """A machine with no tmux server genuinely has zero sessions — the ONE
    non-zero exit that means empty rather than unanswered."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="error connecting to /tmp/tmux-501/default (No such file or directory)",
        ),
    )

    result = stop.Tmux().list_sessions()

    assert isinstance(result, stop.SessionListing)
    assert result.sessions == ()


def test_a_stale_socket_with_no_server_listening_is_an_empty_listing(monkeypatch) -> None:
    """tmux's other "no server" shape: the socket path exists but nothing
    listens on it (`no server running on <socket>`). Same meaning as the
    connect failure, same answer: an empty listing, not an outage."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="no server running on /tmp/tmux-501/default",
        ),
    )

    result = stop.Tmux().list_sessions()

    assert isinstance(result, stop.SessionListing)
    assert result.sessions == ()


def test_no_server_running_on_with_no_operand_is_unanswered(monkeypatch) -> None:
    """The stale-socket shape always names the socket it found dead —
    `no server running on <socket>`. A stderr line that merely ends in the
    bare phrase, with nothing after the trailing space, is not that shape
    (real tmux answers always carry an operand) and must not be read as an
    empty server."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="camp: no server running on ",
        ),
    )

    result = stop.Tmux().list_sessions()

    assert result is stop.UNANSWERED


def test_an_unrelated_error_mentioning_a_missing_file_is_unanswered(monkeypatch) -> None:
    """The no-server condition is `error connecting to <socket> (No such
    file or directory)`. A non-zero exit that merely CARRIES that phrase —
    a config file tmux could not source, a shell wrapper's own complaint —
    is an outage, and reading it as an empty server would render every
    workspace `none` during a live one: a wrong answer wearing a right
    answer's clothes."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="/etc/tmux.conf: 3: No such file or directory",
        ),
    )

    result = stop.Tmux().list_sessions()

    assert result is stop.UNANSWERED


def test_an_unsafe_socket_directory_is_unanswered_not_empty(monkeypatch) -> None:
    """A live outage, not an empty server: this is the test that fails if the
    implementation keys on the exit status alone."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="directory /tmp/tmux-501 has unsafe permissions",
        ),
    )

    result = stop.Tmux().list_sessions()

    assert result is stop.UNANSWERED


def test_an_unreachable_socket_is_unanswered_not_empty(monkeypatch) -> None:
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(
            returncode=1,
            stderr="error connecting to /some/very/long/path (File name too long)",
        ),
    )

    result = stop.Tmux().list_sessions()

    assert result is stop.UNANSWERED


def test_an_unrecognised_stderr_on_non_zero_exit_routes_to_unanswered(monkeypatch) -> None:
    """The conservative default: a future tmux rewording degrades to unknown
    rather than to a confident wrong answer."""
    from camp.launch import stop

    monkeypatch.setattr(
        stop.subprocess,
        "run",
        lambda *a, **k: _completed(returncode=1, stderr="some new tmux error text"),
    )

    result = stop.Tmux().list_sessions()

    assert result is stop.UNANSWERED


def test_an_os_error_launching_tmux_is_unanswered(monkeypatch) -> None:
    from camp.launch import stop

    def _raise(*args, **kwargs):
        raise OSError("tmux not found")

    monkeypatch.setattr(stop.subprocess, "run", _raise)

    assert stop.Tmux().list_sessions() is stop.UNANSWERED


def test_a_timeout_expired_launching_tmux_is_unanswered(monkeypatch) -> None:
    import subprocess as _subprocess

    from camp.launch import stop

    def _raise(*args, **kwargs):
        raise _subprocess.TimeoutExpired(cmd="tmux", timeout=1)

    monkeypatch.setattr(stop.subprocess, "run", _raise)

    assert stop.Tmux().list_sessions() is stop.UNANSWERED


def test_a_malformed_row_with_no_delimiter_is_dropped_and_reported(monkeypatch) -> None:
    """One bad row must not blank the listing, and the drop must be visible —
    a caller must be able to tell 'one row was unparseable' apart from 'the
    answer was empty'."""
    from camp.launch import stop

    stdout = "garbage-no-delimiter\n3|camp-feat-a-11112222\n"
    monkeypatch.setattr(
        stop.subprocess, "run", lambda *a, **k: _completed(returncode=0, stdout=stdout)
    )

    result = stop.Tmux().list_sessions()

    assert result.sessions == (stop.TmuxSession(name="camp-feat-a-11112222", windows=3),)
    assert result.dropped == 1


def test_a_malformed_row_with_a_non_numeric_count_is_dropped_and_reported(
    monkeypatch,
) -> None:
    from camp.launch import stop

    stdout = "not-a-number|camp-feat-a-11112222\n3|camp-feat-b-22223333\n"
    monkeypatch.setattr(
        stop.subprocess, "run", lambda *a, **k: _completed(returncode=0, stdout=stdout)
    )

    result = stop.Tmux().list_sessions()

    assert result.sessions == (stop.TmuxSession(name="camp-feat-b-22223333", windows=3),)
    assert result.dropped == 1


def test_list_sessions_argv_is_list_sessions_with_dash_f(  # inert-gate: allow seam wiring, no input to vary
    monkeypatch,
) -> None:
    from camp.launch import stop

    captured: dict[str, Any] = {}

    def _record(args, **kwargs):
        captured["args"] = args
        return _completed(returncode=0, stdout="")

    monkeypatch.setattr(stop.subprocess, "run", _record)

    stop.Tmux().list_sessions()

    assert captured["args"][0] == "tmux"
    assert "list-sessions" in captured["args"]
    assert "-F" in captured["args"]


# ---------------------------------------------------------------------------
# The two wall-clock budgets, and the environment seam over them
# ---------------------------------------------------------------------------


class _CountingTmux(_FakeTmux):
    """A fake that records how many times the engine re-polled for absence."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.has_session_calls = 0

    def has_session(self, name: str) -> bool:
        self.has_session_calls += 1
        return super().has_session(name)


def _ticking_clock(step: float = 1.0):
    """A monotonic clock advancing a fixed step per read.

    The poll count then reads the budget directly: one read sets the deadline
    and one more is spent per pass, so a longer budget buys proportionally more
    polls and the two budgets are distinguishable without real waiting.
    """
    state = {"t": 0.0}

    def _now() -> float:
        state["t"] += step
        return state["t"] - step

    return _now


class TestStopPollBudget:
    """The re-poll budget is the caller's to name.

    The engine reads no environment (see the module docstring): `camp kill`
    resolves any override at its edge and passes it in. What is pinned here is
    that the value passed is the one spent — the property that override relies
    on. `CAMP_TEST_STOP_POLL_TIMEOUT_SECONDS` reaching this parameter is
    pinned at the CLI, in `test_session_cli_kill_budget`.
    """

    _runs = 0

    def _polls_before_giving_up(self, tmp_path, **kwargs):
        """Drive a kill whose session never goes; return how many polls it managed."""
        from camp.launch.stop import StillPresent

        # Each call gets its own root: two calls per test is the point (a
        # budget is only readable by comparing two of them), and `_fixture`
        # builds a workspace tree that cannot be built twice in one place.
        root = tmp_path / f"run{self._runs}"
        self._runs += 1
        root.mkdir()
        state, ws, env, harness, derived, _unused = _fixture(root)
        tmux = _CountingTmux(
            {derived: _resumed_pane(harness, _UUID_A)}, undead=True
        )

        outcome = _stop(
            _UUID_A[:8],
            tmux=tmux,
            transcripts=[_transcript(_UUID_A, ws)],
            live_records=[_record(_UUID_A, ws)],
            env=env,
            harness=harness,
            monotonic=_ticking_clock(),
            **kwargs,
        )

        assert isinstance(outcome, StillPresent)
        return tmux.has_session_calls

    def test_a_shorter_budget_buys_fewer_polls(self, tmp_path) -> None:
        short = self._polls_before_giving_up(tmp_path, poll_timeout=1.0)
        longer = self._polls_before_giving_up(tmp_path, poll_timeout=4.0)

        assert short < longer, (
            f"the budget should decide the polling: {short} polls at 1s, "
            f"{longer} at 4s"
        )

    def test_the_default_is_the_shipped_budget(self, tmp_path) -> None:
        """A caller naming nothing gets what every real kill gets."""
        from camp.launch import stop

        polls = self._polls_before_giving_up(tmp_path)

        assert polls >= stop.POLL_TIMEOUT_SECONDS
