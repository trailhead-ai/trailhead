"""Opt-in end-to-end test of the gateway's run cancel, run on andromeda.

    TRAILHEAD_GATEWAY_E2E=1 .venv/bin/python -m pytest -n 0 trailhead/tests/test_gateway_e2e_cancel.py

It needs what the run scenario in ``test_gateway_e2e_andromeda.py`` needs (an Outpost with the operator's ``[runs]``
table, the ``outpost-run`` group and the run engine), plus the act wrapper provisioned with
``setpriv --pdeathsig KILL``. Only one Outpost can bind the engine filter, so this scenario lives in its own module:
the run scenario's module-scoped Outpost is stopped before this one starts. ``scripts/run-e2e-cancel-red-then-green.sh``
runs it against the commits before ``run cancel`` and then against this branch.
"""

from __future__ import annotations

import os
import json
import re
import secrets
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from ._gateway_e2e_support import (
    ArgumentSampler,
    token_recorder,
    HomeSentinel,
    EngineUnavailable,
    act_container_names,
    remove_act_containers,
    runner_act_processes,
    prepull_image,
    build_fixture_repo,
    require_run_host,
    make_camp,
    real_outpost,
    register_cmd,
    run_fake_harness,
    step_env,
    install_shims,
    start_fake_harness,
    gateway_call,
    scan_leaks,
)

GATED = os.environ.get("TRAILHEAD_GATEWAY_E2E") == "1"
pytestmark = pytest.mark.skipif(not GATED, reason="opt-in: set TRAILHEAD_GATEWAY_E2E=1 (see module docstring)")


#: Executor's wait between asking act's process group to stop and killing it (its ``graceMs`` default, 5000 ms).
CANCEL_GRACE_SECONDS = 5
#: What a cancel may take beyond the grace: the container removal, the kill's reaping and the executor's poll.
CANCEL_MARGIN_SECONDS = 25
HOLD_MARKER = "cancel-marker-42"


def _containers_or_none(engine_socket):
    """The act containers the engine holds, or None when it would not say, which the assertion reads as a failure."""
    try:
        return act_container_names(engine_socket)
    except EngineUnavailable:
        return None


@dataclass
class CancelWorld:
    hold_id: int
    behind_id: int
    hold_logged_marker: bool
    control_processes: list
    control_containers: list
    behind_states_seen: list
    behind_cancel: str
    behind_final: tuple
    foreign_cancel: str
    hold_after_foreign: tuple
    hold_cancel: str
    cancel_seconds: float | None
    hold_final: tuple
    processes_after: list
    containers_after: list | None
    hold_log: str
    listed: list
    outpost_log: str
    leaks: list


def _wait(deadline_seconds, observe):
    """Poll *observe* (a value that is falsy until it is what we wait for); the last value, whether or not it came."""
    deadline = time.monotonic() + deadline_seconds
    value = observe()
    while not value and time.monotonic() < deadline:
        time.sleep(0.25)
        value = observe()
    return value


@pytest.fixture(scope="module")
def cancel_world(tmp_path_factory):
    host = require_run_host()
    root = tmp_path_factory.mktemp("gw-cancel")
    camp = make_camp(root)
    home = Path(os.environ["HOME"])
    leftover = _wait(30, lambda: not runner_act_processes())
    assert leftover, f"an act process is already running as outpost-runner: {runner_act_processes()}"
    sentinel = HomeSentinel(home / f".outpost-e2e-{secrets.token_hex(4)}", f"SENTINEL-{secrets.token_hex(8)}")
    starters: list = []
    try:
        sentinel.create()
        with (
            real_outpost(root, camp, runs=host) as outpost,
            token_recorder(outpost) as recorder,
            ArgumentSampler(interval=0.05) as sampler,
        ):
            image = host.platforms["ubuntu-latest"]
            prepull_image(host.engine_socket, image)
            build_fixture_repo(
                camp.w1 / "app",
                image=image,
                ssh=home / ".ssh",
                sentinel=sentinel,
                ports=(outpost.port, outpost.port),
                operator_home=home,
            )
            gateway = install_shims(root, outpost, port=recorder.port) / "outpost-gateway"
            env = step_env(root, outpost, port=recorder.port)
            port = outpost.port
            n = iter(range(10_000))

            def gw(cwd, *argv):
                """Run one gateway command from a fresh harness in *cwd*; its output, ending ``rc=<n>``."""
                steps = [("register", register_cmd()), ("cmd", gateway_call(gateway, *argv))]
                return run_fake_harness(root, f"h{next(n)}", cwd, steps, env)["cmd"]

            w1 = camp.w1 / "app"

            def start(job):
                """Start *job* from a harness that stays alive until the scenario ends: Outpost cancels a run whose
                starting session has ended."""
                name = f"h{next(n)}"
                stop = root / f"{name}.stop"
                script = (
                    f"{register_cmd()} >/dev/null 2>&1; {gateway_call(gateway, 'run', 'start', job)} 2>&1; "
                    f'echo "rc=$?"; while [ ! -e {shlex.quote(str(stop))} ]; do sleep 0.2; done'
                )
                proc = start_fake_harness(root, name, w1, script, env)
                starters.append((proc, stop))
                out = ""
                for line in proc.stdout:
                    out += line
                    if line.startswith("rc="):
                        break
                found = re.search(r"^(\d+)$", out, re.M)
                assert found, f"start {job} did not print a run id: {out!r}"
                return int(found.group(1))

            def row(run_id):
                return next(((r[3], r[4]) for r in outpost.runs() if r[0] == run_id), (None, None))

            hold_id = start("cancel_hold")
            logged = _wait(120, lambda: HOLD_MARKER in gw(w1, "run", "view", str(hold_id), "--log"))
            control_processes = runner_act_processes()
            control_containers = act_container_names(host.engine_socket)

            behind_id = start("cancel_behind")
            seen = [row(behind_id)[0]]
            behind_cancel = gw(w1, "run", "cancel", str(behind_id))
            _wait(20, lambda: seen.append(row(behind_id)[0]) or row(behind_id)[0] == "completed")
            behind_final = row(behind_id)

            foreign_cancel = gw(camp.w2, "run", "cancel", str(hold_id))
            hold_after_foreign = row(hold_id)

            t0 = time.monotonic()
            hold_cancel = gw(w1, "run", "cancel", str(hold_id))
            done = _wait(CANCEL_GRACE_SECONDS + CANCEL_MARGIN_SECONDS, lambda: row(hold_id)[0] == "completed")
            seconds = time.monotonic() - t0 if done else None
            print(f"\ncancel of the running job took {seconds if seconds is None else round(seconds, 1)}s to complete")
            if seconds is None:
                remove_act_containers(host.engine_socket)
            hold_final = row(hold_id)
            _wait(5, lambda: not runner_act_processes())
            yield CancelWorld(
                hold_id,
                behind_id,
                bool(logged),
                control_processes,
                control_containers,
                seen,
                behind_cancel,
                behind_final,
                foreign_cancel,
                hold_after_foreign,
                hold_cancel,
                seconds,
                hold_final,
                runner_act_processes(),
                _containers_or_none(host.engine_socket),
                gw(w1, "run", "view", str(hold_id), "--log"),
                gw(w1, "run", "list", "--json"),
                outpost.log.read_text()[-3000:],
                scan_leaks(outpost, recorder, sampler, host),
            )
    finally:
        for proc, stop in starters:
            stop.touch()
        for proc, _ in starters:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            proc.stdout.close()
        remove_act_containers(host.engine_socket)
        sentinel.remove()


def test_cancel_scenario_a_running_job_ends_cancelled_within_the_grace_and_leaves_no_act_or_container(cancel_world):
    w = cancel_world
    assert w.hold_logged_marker, (
        f"the job never logged its marker, so it was never running; its log read:\n{w.hold_log[-1500:]}"
    )
    assert w.control_processes and w.control_containers, (
        f"while the job ran there was no act process {w.control_processes} or container {w.control_containers} "
        "to see go, so the checks after the cancel prove nothing"
    )
    assert w.hold_cancel.splitlines()[-1] == "rc=0" and "cancel requested" in w.hold_cancel, w.hold_cancel
    limit = CANCEL_GRACE_SECONDS + CANCEL_MARGIN_SECONDS
    assert w.cancel_seconds is not None and w.cancel_seconds <= limit, (
        f"the run did not complete within {limit}s of the cancel: {w.hold_final}; cancel said {w.hold_cancel!r}; "
        f"Outpost's log:\n{w.outpost_log}"
    )
    assert w.hold_final == ("completed", "cancelled")
    assert w.processes_after == [], f"act is still running as outpost-runner: {w.processes_after}"
    assert w.containers_after == [], f"the engine lists act containers, or would not say (None): {w.containers_after}"
    assert {r["id"]: (r["state"], r["result"]) for r in json.loads(w.listed.rsplit("\nrc=", 1)[0])["runs"]}[
        w.hold_id
    ] == ("completed", "cancelled")


def test_cancel_scenario_a_queued_run_behind_it_ends_cancelled_without_ever_running(cancel_world):
    w = cancel_world
    assert w.behind_states_seen[0] == "queued", f"the run was not queued behind the running one: {w.behind_states_seen}"
    assert w.behind_cancel.splitlines()[-1] == "rc=0", w.behind_cancel
    assert w.behind_final == ("completed", "cancelled"), f"{w.behind_final}; cancel said {w.behind_cancel!r}"
    assert "in_progress" not in w.behind_states_seen, w.behind_states_seen


def test_cancel_scenario_another_workspaces_session_cannot_cancel_it_and_the_run_is_unchanged(cancel_world):
    w = cancel_world
    assert w.foreign_cancel.splitlines()[-1] != "rc=0", w.foreign_cancel
    assert "run_not_found" in w.foreign_cancel, w.foreign_cancel
    assert w.hold_after_foreign == ("in_progress", None), w.hold_after_foreign


def test_cancel_scenario_no_secret_or_token_reaches_any_arguments_run_log_or_request_record(
    cancel_world,
):  # AC81, AC85, AC86
    assert cancel_world.leaks == [], cancel_world.leaks


def test_cancel_scenario_the_cancelled_runs_log_holds_what_the_job_wrote_before_the_cancel(cancel_world):
    w = cancel_world
    assert w.hold_log.splitlines()[-1] == "rc=0", w.hold_log
    assert HOLD_MARKER in w.hold_log, f"the log of the cancelled run is missing the job's output:\n{w.hold_log[-1500:]}"
