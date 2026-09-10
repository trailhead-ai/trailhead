"""Behavioural tests for `camp.host.merge.answer_all_hosts_concurrently` —
the bounded thread pool that fans declared hosts out while the caller's
local answer runs on its own pool slot, per
`docs/design/the-all-hosts-answer-merges-every-declared-machine.md`'s
"Concurrency" section and
`task/contact-declared-hosts-concurrently`'s test contract.

Concurrency is pinned deterministically — a `threading.Barrier` a serial
implementation cannot satisfy — rather than by timing alone; the wall-clock
assertions are the acceptance criterion's own coarser form, not the primary
proof.

Every stub `runner` here is injected — no test contacts a real host over
SSH.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import threading
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _merge_module():
    return importlib.import_module("camp.host.merge")


def _relay_module():
    return importlib.import_module("camp.host.relay")


def _transport_module():
    return importlib.import_module("camp.host.transport")


def _config_module():
    return importlib.import_module("camp.host.config")


def _host(ssh: str):
    config = _config_module()
    return config.Host(ssh=ssh, camp_bin="camp")


def _local_answer(rows=None, notices=None, exit_code=0):
    def _call():
        return (rows or [], notices or [], exit_code)

    return _call


# ---------------------------------------------------------------------------
# concurrency, pinned deterministically via a barrier
# ---------------------------------------------------------------------------


def test_all_declared_hosts_answer_concurrently_via_barrier():
    """N stub hosts whose injected runner blocks on threading.Barrier(N).

    A serial fan-out calls the runner for one host at a time, so fewer than
    N threads ever reach the barrier together and `.wait()` times out with
    `BrokenBarrierError` for every host — caught by the worker and turned
    into an internal-fault row (`answered=False`). A concurrent fan-out
    starts every worker at once, the barrier releases, and every host
    answers for real.
    """
    merge = _merge_module()
    transport = _transport_module()

    n = 4
    barrier = threading.Barrier(n, timeout=5)

    def runner(argv, timeout, env):
        barrier.wait()
        return transport.RawResult(stdout="[]", stderr="", exit_code=0)

    hosts = [(f"host{i}", _host(f"host{i}")) for i in range(n)]

    local_result, host_answers = merge.answer_all_hosts_concurrently(
        _local_answer(),
        hosts,
        verb="list",
        remote_argv=["list", "--all-groups", "--json"],
        runner=runner,
    )

    assert local_result == ([], [], 0)
    assert len(host_answers) == n
    for host_name, answer in host_answers:
        assert answer.answered is True, (host_name, answer.rows)
        assert answer.rows == []


# ---------------------------------------------------------------------------
# the acceptance criterion's own coarser wall-clock form
# ---------------------------------------------------------------------------


def test_never_responding_hosts_complete_within_twice_the_connect_timeout():
    merge = _merge_module()
    transport = _transport_module()

    n = 5
    sleep_seconds = 0.15

    def runner(argv, timeout, env):
        time.sleep(sleep_seconds)
        raise subprocess.TimeoutExpired(cmd=argv, timeout=sleep_seconds)

    hosts = [(f"host{i}", _host(f"host{i}")) for i in range(n)]

    start = time.monotonic()
    local_result, host_answers = merge.answer_all_hosts_concurrently(
        _local_answer(),
        hosts,
        verb="list",
        remote_argv=["list", "--all-groups", "--json"],
        runner=runner,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 2 * sleep_seconds
    assert len(host_answers) == n
    for host_name, answer in host_answers:
        assert answer.answered is False
        assert "did not finish" in answer.rows[0]["reason"]


# ---------------------------------------------------------------------------
# the local answer overlaps the fan-out
# ---------------------------------------------------------------------------


def test_local_answer_overlaps_fanout_rather_than_preceding_it():
    merge = _merge_module()
    transport = _transport_module()

    local_sleep = 0.25
    remote_sleep = 0.25

    def slow_local():
        time.sleep(local_sleep)
        return ([{"ok": True, "slug": "local-slug"}], [], 0)

    def runner(argv, timeout, env):
        time.sleep(remote_sleep)
        return transport.RawResult(stdout="[]", stderr="", exit_code=0)

    hosts = [("andromeda", _host("andromeda"))]

    start = time.monotonic()
    local_result, host_answers = merge.answer_all_hosts_concurrently(
        slow_local,
        hosts,
        verb="list",
        remote_argv=["list", "--all-groups", "--json"],
        runner=runner,
    )
    elapsed = time.monotonic() - start

    # Overlapping: elapsed is about the larger of the two sleeps. Serialized
    # (local then fan-out, or fan-out then local): elapsed is about their
    # sum. The threshold sits strictly between the two.
    assert elapsed < (local_sleep + remote_sleep) - 0.1
    assert local_result == ([{"ok": True, "slug": "local-slug"}], [], 0)


# ---------------------------------------------------------------------------
# row order is declaration order, regardless of completion order
# ---------------------------------------------------------------------------


def test_row_order_is_declared_order_regardless_of_completion_order():
    merge = _merge_module()
    transport = _transport_module()

    def runner(argv, timeout, env):
        # argv is the assembled ssh argv; the destination is second-to-last
        # (see camp.host.transport.run_camp), so a single injected runner
        # can behave differently per host without a per-host runner seam.
        host_ssh = argv[-2]
        if host_ssh == "a-declared-first-but-slow":
            time.sleep(0.2)
            stdout = '[{"ok": true, "slug": "from-a"}]'
        else:
            stdout = '[{"ok": true, "slug": "from-b"}]'
        return transport.RawResult(stdout=stdout, stderr="", exit_code=0)

    hosts = [
        ("a-declared-first-but-slow", _host("a-declared-first-but-slow")),
        ("b-declared-second-but-fast", _host("b-declared-second-but-fast")),
    ]

    _local_result, host_answers = merge.answer_all_hosts_concurrently(
        _local_answer(),
        hosts,
        verb="list",
        remote_argv=["list", "--all-groups", "--json"],
        runner=runner,
    )

    assert [name for name, _ in host_answers] == [
        "a-declared-first-but-slow",
        "b-declared-second-but-fast",
    ]
    assert host_answers[0][1].rows[0]["slug"] == "from-a"
    assert host_answers[1][1].rows[0]["slug"] == "from-b"


# ---------------------------------------------------------------------------
# a worker's unexpected internal exception is never a host failure
# ---------------------------------------------------------------------------


def _every_reachable_relay_failure_reason() -> set[str]:
    """Every reason `answer_for_host` itself produces for a genuinely
    reachable transport outcome — derived by actually driving it through
    each of the transport's own closed-outcome-set classes, never by a
    literal count. The internal-fault reason must differ from all of them."""
    relay = _relay_module()
    transport = _transport_module()
    host = _host("some-host")

    outcomes = [
        transport.Unreachable(reason="Connection timed out"),
        transport.StoppedResponding(execution_timeout=60.0),
        transport.IdentityUnknown(),
        transport.IdentityChanged(),
        transport.CampNotResolvable(),
        transport.CredentialsRefused(),
        transport.RemoteRefusal(stdout="", stderr="unmatched failure", exit_code=255),
        transport.Answered(stdout='["not", "objects"]', stderr="", exit_code=0),
    ]

    original_run_camp = transport.run_camp
    reasons: set[str] = set()
    try:
        for outcome in outcomes:
            def fake_run_camp(host_arg, remote_argv, runner=None, outcome=outcome, **kwargs):
                return outcome

            transport.run_camp = fake_run_camp
            answer = relay.answer_for_host("list", host, "some-host", ["list"])
            if not answer.answered:
                reasons.add(answer.rows[0]["reason"])
    finally:
        transport.run_camp = original_run_camp

    return reasons


def test_internal_fault_reason_differs_from_every_relay_failure_reason():
    merge = _merge_module()
    known_reasons = _every_reachable_relay_failure_reason()
    assert known_reasons  # sanity: the derivation actually found some

    def raising_runner(argv, timeout, env):
        raise RuntimeError("unexpected worker bug")

    hosts = [("buggy", _host("buggy"))]

    _local_result, host_answers = merge.answer_all_hosts_concurrently(
        _local_answer(),
        hosts,
        verb="list",
        remote_argv=["list", "--all-groups", "--json"],
        runner=raising_runner,
    )

    [(host_name, answer)] = host_answers
    assert host_name == "buggy"
    assert answer.answered is False
    reason = answer.rows[0]["reason"]
    assert reason not in known_reasons


def test_internal_fault_leaves_other_hosts_and_local_exit_code_unaffected():
    merge = _merge_module()
    transport = _transport_module()

    def runner(argv, timeout, env):
        host_ssh = argv[-2]
        if host_ssh == "buggy":
            raise RuntimeError("unexpected worker bug")
        return transport.RawResult(
            stdout='[{"ok": true, "slug": "ok-row"}]', stderr="", exit_code=0
        )

    hosts = [
        ("buggy", _host("buggy")),
        ("fine", _host("fine")),
    ]

    local_result, host_answers = merge.answer_all_hosts_concurrently(
        _local_answer(exit_code=7),
        hosts,
        verb="list",
        remote_argv=["list", "--all-groups", "--json"],
        runner=runner,
    )

    by_name = dict(host_answers)
    assert by_name["buggy"].answered is False
    assert by_name["fine"].answered is True
    assert by_name["fine"].rows == [{"ok": True, "slug": "ok-row", "host": "fine"}]
    # the local answer's own exit code is untouched by a remote worker's fault
    assert local_result[2] == 7
