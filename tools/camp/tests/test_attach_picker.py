"""Tests for `camp.attach.picker` — the numbered picker over attachable sessions.

Test contract:
- Zero, one, and many candidates each render their own outcome; the
  one-candidate case still prompts rather than auto-attaching.
- A listed number selects the session on that line; ordering is most
  recently active first, asserted by feeding two orderings of the same set.
- Blank input, an out-of-range number, and a non-numeric line each re-prompt
  rather than exiting, and a valid choice after a bad one still selects.
- A pool that could not be enumerated refuses with a reason naming what
  failed — never an empty list and never exit 0.
- Without a terminal, the picker refuses with a stated reason and reads
  nothing at all — asserted by a stdin fixture that would fail the test if
  read.
- A stopped session is never offered; a session camp does not own is never
  offered.
"""

from __future__ import annotations

import io
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
    _env,
    _group,
    _launched_pane,
    _record,
    _transcript,
    _workspace,
)


class _ExplodingSequence:
    """An iterable that raises the moment it is actually iterated."""

    def __iter__(self):
        raise OSError("transcript store unreadable")


class _RefusingStdin(io.StringIO):
    """A stdin stand-in that fails the test if anything ever reads it."""

    def readline(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("picker read stdin without a terminal")


def _fixture_two(tmp_path: Path):
    """Two live, camp-owned sessions at different ages, same workspace root."""
    state = tmp_path / "state"
    ws_a = _workspace(state, "g", "feat-a")
    ws_b = _workspace(state, "g", "feat-b")
    env = _env(state)
    harness = _FakeHarness()

    derived_a = f"camp-feat-a-{_UUID_A[:8]}"
    derived_b = f"camp-feat-b-{_UUID_B[:8]}"
    tmux = _FakeTmux(
        {
            derived_a: _launched_pane(harness, _UUID_A, derived_a, ws_a),
            derived_b: _launched_pane(harness, _UUID_B, derived_b, ws_b),
        }
    )

    # A (older, age 300s) vs B (fresher, age 30s) — B must list first.
    transcripts = [_transcript(_UUID_A, ws_a, age_seconds=300.0), _transcript(_UUID_B, ws_b, age_seconds=30.0)]
    live_records = [_record(_UUID_A, ws_a), _record(_UUID_B, ws_b)]
    groups = [_group("g")]
    return state, env, harness, tmux, transcripts, live_records, groups


def _build_pool(*, harness, tmux, transcripts, live_records, groups, env, machine="local"):
    from camp.attach.picker import local_pool

    return local_pool(
        harness=harness,
        tmux=tmux,
        transcripts=transcripts,
        live_records=live_records,
        groups=groups,
        env=env,
        machine=machine,
        now=_NOW,
    )


# ---------------------------------------------------------------------------
# Zero / one / many
# ---------------------------------------------------------------------------


def test_zero_candidates_states_so_and_never_prompts(tmp_path: Path) -> None:
    from camp.attach.picker import NothingToOffer, PoolReady, pick_session

    outcome = pick_session(
        PoolReady(rows=()),
        stdin=_RefusingStdin(""),
        stdout=io.StringIO(),
        isatty=True,
    )

    assert isinstance(outcome, NothingToOffer)


def test_one_candidate_still_prompts_rather_than_auto_attaching(tmp_path: Path) -> None:
    from camp.attach.picker import Picked, Row, PoolReady, pick_session

    state, env, harness, tmux, transcripts, live_records, groups = _fixture_two(tmp_path)
    pool = _build_pool(
        harness=harness,
        tmux=tmux,
        transcripts=[transcripts[0]],
        live_records=[live_records[0]],
        groups=groups,
        env=env,
    )
    assert isinstance(pool, PoolReady)
    assert len(pool.rows) == 1

    stdin = io.StringIO("")  # nothing typed yet — must NOT auto-select
    outcome = pick_session(pool, stdin=stdin, stdout=io.StringIO(), isatty=True)

    # An empty read (EOF, no line) with one candidate must not silently pick it.
    from camp.attach.picker import PoolUnreadable

    assert not isinstance(outcome, Picked)
    assert isinstance(outcome, PoolUnreadable)

    stdin2 = io.StringIO("1\n")
    outcome2 = pick_session(pool, stdin=stdin2, stdout=io.StringIO(), isatty=True)
    assert isinstance(outcome2, Picked)
    assert outcome2.row.candidate.session_id == _UUID_A


def test_many_candidates_lists_them_all(tmp_path: Path) -> None:
    from camp.attach.picker import Picked, PoolReady, pick_session

    state, env, harness, tmux, transcripts, live_records, groups = _fixture_two(tmp_path)
    pool = _build_pool(
        harness=harness, tmux=tmux, transcripts=transcripts, live_records=live_records, groups=groups, env=env
    )
    assert isinstance(pool, PoolReady)
    assert len(pool.rows) == 2

    out = io.StringIO()
    outcome = pick_session(pool, stdin=io.StringIO("2\n"), stdout=out, isatty=True)

    assert isinstance(outcome, Picked)
    assert "1)" in out.getvalue()
    assert "2)" in out.getvalue()


# ---------------------------------------------------------------------------
# Selection and ordering
# ---------------------------------------------------------------------------


def test_a_listed_number_selects_the_session_on_that_line(tmp_path: Path) -> None:
    from camp.attach.picker import Picked, pick_session

    state, env, harness, tmux, transcripts, live_records, groups = _fixture_two(tmp_path)
    pool = _build_pool(
        harness=harness, tmux=tmux, transcripts=transcripts, live_records=live_records, groups=groups, env=env
    )

    outcome = pick_session(pool, stdin=io.StringIO("1\n"), stdout=io.StringIO(), isatty=True)

    assert isinstance(outcome, Picked)
    # Freshest (B, age 30s) is row 1 under most-recently-active-first ordering.
    assert outcome.row.candidate.session_id == _UUID_B


def test_ordering_is_most_recently_active_first_regardless_of_input_order(tmp_path: Path) -> None:
    state, env, harness, tmux, transcripts, live_records, groups = _fixture_two(tmp_path)

    forward = _build_pool(
        harness=harness, tmux=tmux, transcripts=transcripts, live_records=live_records, groups=groups, env=env
    )
    reversed_transcripts = list(reversed(transcripts))
    reversed_records = list(reversed(live_records))
    backward = _build_pool(
        harness=harness,
        tmux=tmux,
        transcripts=reversed_transcripts,
        live_records=reversed_records,
        groups=groups,
        env=env,
    )

    from camp.attach.picker import PoolReady

    assert isinstance(forward, PoolReady) and isinstance(backward, PoolReady)
    forward_ids = [row.candidate.session_id for row in forward.rows]
    backward_ids = [row.candidate.session_id for row in backward.rows]

    assert forward_ids == [_UUID_B, _UUID_A]
    assert backward_ids == [_UUID_B, _UUID_A]


# ---------------------------------------------------------------------------
# Bad-input re-prompting
# ---------------------------------------------------------------------------


def test_blank_out_of_range_and_non_numeric_each_reprompt_then_a_valid_choice_selects(
    tmp_path: Path,
) -> None:
    from camp.attach.picker import Picked, pick_session

    state, env, harness, tmux, transcripts, live_records, groups = _fixture_two(tmp_path)
    pool = _build_pool(
        harness=harness, tmux=tmux, transcripts=transcripts, live_records=live_records, groups=groups, env=env
    )

    stdin = io.StringIO("\n99\nnotanumber\n1\n")
    outcome = pick_session(pool, stdin=stdin, stdout=io.StringIO(), isatty=True)

    assert isinstance(outcome, Picked)
    assert outcome.row.candidate.session_id == _UUID_B


# ---------------------------------------------------------------------------
# Pool could not be enumerated
# ---------------------------------------------------------------------------


def test_unreadable_pool_refuses_with_a_reason_naming_what_failed(tmp_path: Path) -> None:
    from camp.attach.picker import PoolUnreadable, pick_session

    state, env, harness, tmux, transcripts, live_records, groups = _fixture_two(tmp_path)
    pool = _build_pool(
        harness=harness,
        tmux=tmux,
        transcripts=_ExplodingSequence(),
        live_records=live_records,
        groups=groups,
        env=env,
    )

    assert isinstance(pool, PoolUnreadable)
    assert "transcript store unreadable" in pool.reason

    outcome = pick_session(pool, stdin=_RefusingStdin(""), stdout=io.StringIO(), isatty=True)
    assert isinstance(outcome, PoolUnreadable)
    assert "transcript store unreadable" in outcome.reason


# ---------------------------------------------------------------------------
# No terminal
# ---------------------------------------------------------------------------


def test_no_terminal_refuses_and_reads_nothing_at_all(tmp_path: Path) -> None:
    from camp.attach.picker import PoolUnreadable, pick_session

    state, env, harness, tmux, transcripts, live_records, groups = _fixture_two(tmp_path)
    pool = _build_pool(
        harness=harness, tmux=tmux, transcripts=transcripts, live_records=live_records, groups=groups, env=env
    )

    outcome = pick_session(pool, stdin=_RefusingStdin(""), stdout=io.StringIO(), isatty=False)

    assert isinstance(outcome, PoolUnreadable)
    assert outcome.reason


# ---------------------------------------------------------------------------
# Ownership / liveness filtering
# ---------------------------------------------------------------------------


def test_a_stopped_session_is_never_offered(tmp_path: Path) -> None:
    state, env, harness, tmux, transcripts, live_records, groups = _fixture_two(tmp_path)

    # Drop B's live record — it becomes a transcript-only (stopped) session.
    pool = _build_pool(
        harness=harness,
        tmux=tmux,
        transcripts=transcripts,
        live_records=[live_records[0]],
        groups=groups,
        env=env,
    )

    from camp.attach.picker import PoolReady

    assert isinstance(pool, PoolReady)
    ids = [row.candidate.session_id for row in pool.rows]
    assert _UUID_A in ids
    assert _UUID_B not in ids


def test_a_session_camp_does_not_own_is_never_offered(tmp_path: Path) -> None:
    state, env, harness, tmux, transcripts, live_records, groups = _fixture_two(tmp_path)

    derived_b = f"camp-feat-b-{_UUID_B[:8]}"
    foreign_tmux = _FakeTmux(
        {
            f"camp-feat-a-{_UUID_A[:8]}": tmux.panes[f"camp-feat-a-{_UUID_A[:8]}"],
            derived_b: "sleep 100000",
        }
    )

    pool = _build_pool(
        harness=harness,
        tmux=foreign_tmux,
        transcripts=transcripts,
        live_records=live_records,
        groups=groups,
        env=env,
    )

    from camp.attach.picker import PoolReady

    assert isinstance(pool, PoolReady)
    ids = [row.candidate.session_id for row in pool.rows]
    assert _UUID_A in ids
    assert _UUID_B not in ids
