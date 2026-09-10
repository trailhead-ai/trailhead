"""EPHEMERAL assumption probe — NOT part of the shipped suite.

Resolves the Known Unknown: "Can `resolve_session_ref` answer over a pool
narrowed to live, camp-owned sessions without forking the resolver — and does
a narrowed pool still distinguish 'stopped' from 'no match'?"

Delete this file (and this file only) once the finding is folded into the
real task/plan. Reuses fixtures from test_launch_stop.py, exactly as the
shipped test_attach_ownership.py does.
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
)


# ---------------------------------------------------------------------------
# Q2 — narrowing the POOL before matching collapses "stopped" into "no match"
# ---------------------------------------------------------------------------


def test_narrowing_inputs_to_live_only_before_matching_collapses_stopped_and_nomatch(
    tmp_path: Path,
) -> None:
    """The WRONG shape: filter transcripts/live_records to live-only, THEN call
    resolve_session_ref. A ref naming a real but stopped session and a ref
    naming nothing at all come back as the identical Resolution."""
    from camp.launch.recovery import NoMatch, resolve_session_ref

    state, ws_a, env, harness, derived_a, _tmux = _fixture(tmp_path, slug="feat-a", session_id=_UUID_A)
    ws_b = state / "g" / "worktrees" / "feat-b"
    ws_b.mkdir(parents=True)

    # Session A is STOPPED (transcript only). Session B is live. Narrow the
    # pool to live-only BEFORE calling the resolver, as the delta design's
    # literal wording ("a pool narrowed to live... sessions") would do.
    narrowed_transcripts = [_transcript(_UUID_B, ws_b)]
    narrowed_live = [_record(_UUID_B, ws_b)]

    stopped_ref_outcome = resolve_session_ref(
        _UUID_A[:8],
        transcripts=narrowed_transcripts,
        live_records=narrowed_live,
        groups=[_group("g")],
        env=env,
        now=_NOW,
    )
    nonsense_ref_outcome = resolve_session_ref(
        "zzzzzzzz",
        transcripts=narrowed_transcripts,
        live_records=narrowed_live,
        groups=[_group("g")],
        env=env,
        now=_NOW,
    )

    # Both a real, stopped session's ref and a ref matching nothing whatsoever
    # come back as the SAME value — indistinguishable.
    assert isinstance(stopped_ref_outcome, NoMatch)
    assert isinstance(nonsense_ref_outcome, NoMatch)
    assert stopped_ref_outcome == nonsense_ref_outcome


def test_calling_over_the_full_unnarrowed_pool_then_filtering_on_live_distinguishes_them(
    tmp_path: Path,
) -> None:
    """The shape that works: call the resolver UNFORKED over the full pool
    (not narrowed), then read `.live` off the Resolved candidate. This needs
    no copy of the matcher's rules — it is resolve_session_ref, called plain."""
    from camp.launch.recovery import NoMatch, Resolved, resolve_session_ref

    state, ws_a, env, harness, derived_a, _tmux = _fixture(tmp_path, slug="feat-a", session_id=_UUID_A)
    ws_b = state / "g" / "worktrees" / "feat-b"
    ws_b.mkdir(parents=True)

    full_transcripts = [_transcript(_UUID_A, ws_a), _transcript(_UUID_B, ws_b)]
    full_live = [_record(_UUID_B, ws_b)]  # only B is live; A is stopped

    stopped_outcome = resolve_session_ref(
        _UUID_A[:8],
        transcripts=full_transcripts,
        live_records=full_live,
        groups=[_group("g")],
        env=env,
        now=_NOW,
    )
    nonsense_outcome = resolve_session_ref(
        "zzzzzzzz",
        transcripts=full_transcripts,
        live_records=full_live,
        groups=[_group("g")],
        env=env,
        now=_NOW,
    )

    assert isinstance(stopped_outcome, Resolved)
    assert stopped_outcome.candidate.live is False  # "not running" is derivable here
    assert isinstance(nonsense_outcome, NoMatch)  # genuinely distinct from the above
    assert stopped_outcome != nonsense_outcome


# ---------------------------------------------------------------------------
# Q3 — ownership narrowing composes with liveness narrowing, post-hoc
# ---------------------------------------------------------------------------


def test_foreign_ownership_is_distinguishable_from_nomatch_and_from_a_stopped_session(
    tmp_path: Path,
) -> None:
    """A session camp does not own, a session that is stopped, and a ref that
    matches nothing at all must be THREE distinguishable outcomes, reached by
    composing resolve_session_ref (unforked) with resolve_ownership (also
    unforked) as two post-hoc checks over the one Resolved candidate — not by
    forking either."""
    from camp.attach.ownership import Foreign, Owned, resolve_ownership
    from camp.launch.recovery import NoMatch, Resolved, resolve_session_ref

    state, ws_a, env, harness, derived_a, _owned_tmux = _fixture(
        tmp_path, slug="feat-a", session_id=_UUID_A
    )
    ws_b = state / "g" / "worktrees" / "feat-b"
    ws_b.mkdir(parents=True)
    derived_b = f"camp-feat-b-{_UUID_B[:8]}"

    full_transcripts = [_transcript(_UUID_A, ws_a), _transcript(_UUID_B, ws_b)]
    full_live = [_record(_UUID_A, ws_a), _record(_UUID_B, ws_b)]  # both live

    # A's pane is camp's own; B's pane is a foreign command tmux is running.
    tmux = _FakeTmux(
        {
            derived_a: _launched_pane(harness, _UUID_A, derived_a, ws_a),
            derived_b: "sleep 100000",
        }
    )

    owned_outcome = resolve_session_ref(
        _UUID_A[:8],
        transcripts=full_transcripts,
        live_records=full_live,
        groups=[_group("g")],
        env=env,
        now=_NOW,
    )
    foreign_outcome = resolve_session_ref(
        _UUID_B[:8],
        transcripts=full_transcripts,
        live_records=full_live,
        groups=[_group("g")],
        env=env,
        now=_NOW,
    )
    nomatch_outcome = resolve_session_ref(
        "zzzzzzzz",
        transcripts=full_transcripts,
        live_records=full_live,
        groups=[_group("g")],
        env=env,
        now=_NOW,
    )

    assert isinstance(owned_outcome, Resolved)
    assert isinstance(resolve_ownership(harness, tmux, owned_outcome.candidate), Owned)

    assert isinstance(foreign_outcome, Resolved)
    assert isinstance(resolve_ownership(harness, tmux, foreign_outcome.candidate), Foreign)

    assert isinstance(nomatch_outcome, NoMatch)

    # All three are pairwise distinguishable by an attach-side caller composing
    # the two unforked functions, none of them collapsed into another.
    assert type(owned_outcome) is Resolved
    assert type(foreign_outcome) is Resolved
    assert type(nomatch_outcome) is NoMatch


# ---------------------------------------------------------------------------
# Q4 — the empty ref never resolves, over any pool composition
# ---------------------------------------------------------------------------


def test_empty_ref_never_resolves_even_over_a_single_candidate_pool(tmp_path: Path) -> None:
    from camp.launch.recovery import Resolved, resolve_session_ref

    state, ws_a, env, harness, derived_a, _tmux = _fixture(tmp_path, slug="feat-a", session_id=_UUID_A)

    outcome = resolve_session_ref(
        "",
        transcripts=[_transcript(_UUID_A, ws_a)],
        live_records=[_record(_UUID_A, ws_a)],
        groups=[_group("g")],
        env=env,
        now=_NOW,
    )

    assert not isinstance(outcome, Resolved)


# ---------------------------------------------------------------------------
# Q5 — the resolver is genuinely shared with `camp kill`
# ---------------------------------------------------------------------------


def test_mutating_the_shared_matcher_moves_kill_and_a_narrowed_pool_caller_together(
    tmp_path: Path, monkeypatch
) -> None:
    """Patch the pool-building function resolve_session_ref calls internally
    (looked up at call time from camp.launch.recovery's own globals — the
    same globals regardless of which import alias invoked the function
    object). If the resolver is genuinely shared, both the kill path
    (camp.launch.stop.stop_session) and a direct attach-style caller
    (camp.launch.recovery.resolve_session_ref) must see the mutation."""
    from camp.launch import recovery
    from camp.launch.recovery import NoMatch, Resolved

    state, ws_a, env, harness, derived_a, tmux = _fixture(tmp_path, slug="feat-a", session_id=_UUID_A)
    transcripts = [_transcript(_UUID_A, ws_a)]
    live_records = [_record(_UUID_A, ws_a)]
    groups = [_group("g")]

    # Baseline: both paths resolve the live, owned session before the mutation.
    baseline_direct = recovery.resolve_session_ref(
        _UUID_A[:8], transcripts=transcripts, live_records=live_records, groups=groups, env=env, now=_NOW
    )
    assert isinstance(baseline_direct, Resolved)

    baseline_kill = _stop(
        _UUID_A[:8], tmux=tmux, transcripts=transcripts, live_records=live_records, env=env, harness=harness
    )
    from camp.launch.stop import Stopped

    assert isinstance(baseline_kill, Stopped)

    # Mutate the matcher's pool source: make every candidate pool empty.
    monkeypatch.setattr(recovery, "session_candidates", lambda **kwargs: ())

    mutated_direct = recovery.resolve_session_ref(
        _UUID_A[:8], transcripts=transcripts, live_records=live_records, groups=groups, env=env, now=_NOW
    )
    mutated_kill = _stop(
        _UUID_A[:8], tmux=tmux, transcripts=transcripts, live_records=live_records, env=env, harness=harness
    )

    assert isinstance(mutated_direct, NoMatch)
    assert isinstance(mutated_kill, NoMatch)
