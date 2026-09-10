"""Tests for `camp.attach.resolve` — the non-attaching attach-ref resolver.

Test contract:
- A session-id prefix and a derived-name prefix each resolve to the same
  session; both halves of the grammar are accepted, not one.
- An empty reference never resolves, whatever the pool holds.
- A reference matching two sessions on this machine answers ambiguous and
  names both.
- A reference matching a session that exists but is not live answers
  not-running — never no-match; the two are separate outcomes with separate
  operator next-steps.
- A reference matching only a session camp does not own answers no-match.
- The resolver never calls the handoff seam: it only answers, it never
  attaches.
- The resolver reached here is the same one `camp kill` reaches — mutating
  the shared matcher changes both verbs' answers together.

Built over the full `transcripts ∪ live_records` pool, unforked
(`recovery.resolve_session_ref`), then narrowed on the ANSWER: liveness read
off the resolved candidate, ownership checked post-hoc via
`camp.attach.ownership.resolve_ownership`. Narrowing the *inputs* to
live-owned sessions before matching collapses "stopped" and "no match" into
one outcome — the exact bug this module exists to avoid.
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
    _NOW,
    _UUID_A,
    _UUID_B,
    _FakeHarness,
    _FakeTmux,
    _fixture,
    _group,
    _launched_pane,
    _record,
    _stop,
    _transcript,
    _workspace,
)


def _resolve(ref, *, harness, tmux, transcripts=(), live_records=(), groups=None, env, now=_NOW):
    from camp.attach.resolve import resolve_attach_ref

    return resolve_attach_ref(
        ref,
        harness=harness,
        tmux=tmux,
        transcripts=transcripts,
        live_records=live_records,
        groups=groups if groups is not None else [_group("g")],
        env=env,
        now=now,
    )


# ---------------------------------------------------------------------------
# Grammar — both halves accepted
# ---------------------------------------------------------------------------


def test_session_id_prefix_and_derived_name_prefix_resolve_the_same_session(tmp_path: Path) -> None:
    from camp.attach.resolve import Resolved

    state, ws, env, harness, derived, tmux = _fixture(tmp_path)

    by_id = _resolve(
        _UUID_A[:8],
        harness=harness,
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
    )
    by_name = _resolve(
        derived[:10],
        harness=harness,
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
    )

    assert isinstance(by_id, Resolved)
    assert isinstance(by_name, Resolved)
    assert by_id.candidate.session_id == _UUID_A
    assert by_name.candidate.session_id == _UUID_A


# ---------------------------------------------------------------------------
# Empty ref
# ---------------------------------------------------------------------------


def test_empty_ref_never_resolves_even_over_a_single_candidate_pool(tmp_path: Path) -> None:
    from camp.attach.resolve import Ambiguous, Resolved

    state, ws, env, harness, derived, tmux = _fixture(tmp_path)

    outcome = _resolve(
        "",
        harness=harness,
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
    )

    assert not isinstance(outcome, Resolved)
    assert isinstance(outcome, Ambiguous)
    assert len(outcome.candidates) == 1


# ---------------------------------------------------------------------------
# Ambiguity
# ---------------------------------------------------------------------------


def test_a_ref_matching_two_sessions_answers_ambiguous_naming_both(tmp_path: Path) -> None:
    from camp.attach.resolve import Ambiguous

    state = tmp_path / "state"
    ws_a = _workspace(state, "g", "feat-a")
    ws_b = _workspace(state, "g", "feat-b")
    env = {"CAMP_STATE_DIR": str(state)}
    harness = _FakeHarness()
    derived_a = f"camp-feat-a-{_UUID_A[:8]}"
    derived_b = f"camp-feat-b-{_UUID_B[:8]}"
    tmux = _FakeTmux(
        {
            derived_a: _launched_pane(harness, _UUID_A, derived_a, ws_a),
            derived_b: _launched_pane(harness, _UUID_B, derived_b, ws_b),
        }
    )

    outcome = _resolve(
        "camp-feat-",
        harness=harness,
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws_a), _transcript(_UUID_B, ws_b)],
        live_records=[_record(_UUID_A, ws_a), _record(_UUID_B, ws_b)],
        env=env,
    )

    assert isinstance(outcome, Ambiguous)
    assert {c.session_id for c in outcome.candidates} == {_UUID_A, _UUID_B}


# ---------------------------------------------------------------------------
# Not-running vs no-match — the corrected direction
# ---------------------------------------------------------------------------


def test_a_stopped_session_answers_not_running_never_no_match(tmp_path: Path) -> None:
    from camp.attach.resolve import NoMatch, NotRunning

    state, ws_a, env, harness, derived_a, _tmux = _fixture(tmp_path, slug="feat-a", session_id=_UUID_A)
    ws_b = state / "g" / "worktrees" / "feat-b"
    ws_b.mkdir(parents=True)

    # A is stopped (transcript only); B is live. Full, unnarrowed pool.
    transcripts = [_transcript(_UUID_A, ws_a), _transcript(_UUID_B, ws_b)]
    live_records = [_record(_UUID_B, ws_b)]
    tmux = _FakeTmux()

    stopped_outcome = _resolve(
        _UUID_A[:8], harness=harness, tmux=tmux, transcripts=transcripts, live_records=live_records, env=env
    )
    nonsense_outcome = _resolve(
        "zzzzzzzz", harness=harness, tmux=tmux, transcripts=transcripts, live_records=live_records, env=env
    )

    assert isinstance(stopped_outcome, NotRunning)
    assert stopped_outcome.candidate.session_id == _UUID_A
    assert isinstance(nonsense_outcome, NoMatch)
    assert type(stopped_outcome) is not type(nonsense_outcome)


# ---------------------------------------------------------------------------
# Not-owned collapses to no-match
# ---------------------------------------------------------------------------


def test_a_session_camp_does_not_own_answers_no_match(tmp_path: Path) -> None:
    from camp.attach.resolve import NoMatch, Resolved

    state, ws_a, env, harness, derived_a, owned_tmux = _fixture(
        tmp_path, slug="feat-a", session_id=_UUID_A
    )
    ws_b = state / "g" / "worktrees" / "feat-b"
    ws_b.mkdir(parents=True)
    derived_b = f"camp-feat-b-{_UUID_B[:8]}"

    transcripts = [_transcript(_UUID_A, ws_a), _transcript(_UUID_B, ws_b)]
    live_records = [_record(_UUID_A, ws_a), _record(_UUID_B, ws_b)]
    tmux = _FakeTmux(
        {
            derived_a: _launched_pane(harness, _UUID_A, derived_a, ws_a),
            derived_b: "sleep 100000",  # foreign — not one camp composed
        }
    )

    owned_outcome = _resolve(
        _UUID_A[:8], harness=harness, tmux=tmux, transcripts=transcripts, live_records=live_records, env=env
    )
    foreign_outcome = _resolve(
        _UUID_B[:8], harness=harness, tmux=tmux, transcripts=transcripts, live_records=live_records, env=env
    )

    assert isinstance(owned_outcome, Resolved)
    assert isinstance(foreign_outcome, NoMatch)
    assert foreign_outcome.pool_size == 2


# ---------------------------------------------------------------------------
# --resolve attaches nothing
# ---------------------------------------------------------------------------


def test_resolve_never_calls_the_handoff_seam(tmp_path: Path, monkeypatch) -> None:
    from camp.attach.resolve import Resolved
    from camp.host import handoff

    def _blow_up(argv, *, exec_seam=handoff.default_exec_seam):
        raise AssertionError("handoff seam called by a resolve-only path")

    monkeypatch.setattr(handoff, "handoff", _blow_up)

    state, ws, env, harness, derived, tmux = _fixture(tmp_path)

    outcome = _resolve(
        _UUID_A[:8],
        harness=harness,
        tmux=tmux,
        transcripts=[_transcript(_UUID_A, ws)],
        live_records=[_record(_UUID_A, ws)],
        env=env,
    )

    assert isinstance(outcome, Resolved)
    assert outcome.candidate.session_id == _UUID_A


# ---------------------------------------------------------------------------
# Shared resolver — same one `camp kill` reaches
# ---------------------------------------------------------------------------


def test_mutating_the_shared_matcher_moves_kill_and_attach_resolve_together(
    tmp_path: Path, monkeypatch
) -> None:
    from camp.attach.resolve import NoMatch as AttachNoMatch
    from camp.attach.resolve import Resolved
    from camp.launch import recovery
    from camp.launch.recovery import NoMatch as RecoveryNoMatch
    from camp.launch.stop import Stopped

    state, ws, env, harness, derived, tmux = _fixture(tmp_path)
    transcripts = [_transcript(_UUID_A, ws)]
    live_records = [_record(_UUID_A, ws)]
    groups = [_group("g")]

    baseline_attach = _resolve(
        _UUID_A[:8],
        harness=harness,
        tmux=tmux,
        transcripts=transcripts,
        live_records=live_records,
        groups=groups,
        env=env,
    )
    assert isinstance(baseline_attach, Resolved)

    baseline_kill = _stop(
        _UUID_A[:8], tmux=tmux, transcripts=transcripts, live_records=live_records, env=env, harness=harness
    )
    assert isinstance(baseline_kill, Stopped)

    monkeypatch.setattr(recovery, "session_candidates", lambda **kwargs: ())

    mutated_attach = _resolve(
        _UUID_A[:8],
        harness=harness,
        tmux=tmux,
        transcripts=transcripts,
        live_records=live_records,
        groups=groups,
        env=env,
    )
    mutated_kill = _stop(
        _UUID_A[:8], tmux=tmux, transcripts=transcripts, live_records=live_records, env=env, harness=harness
    )

    assert isinstance(mutated_attach, AttachNoMatch)
    assert isinstance(mutated_kill, RecoveryNoMatch)
