"""`trailhead gateway register --hook` against a stub Outpost and a fake harness ancestor."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import pytest

from trailhead.harness import harness_process
from trailhead.tests._gateway_support import (
    REGISTRATION_AUTHORITY,
    REPO_ROOT,
    StubOutpost,
    write_authority,
)
from trailhead.tests._harness_process_support import make_fake_harness

RUNNER = REPO_ROOT / "trailhead" / "tests" / "_register_runner.py"
ECHOED_SECRET = "echoed-secret-SEEDED-0a1b"
HOOK = ("gateway", "register", "--hook")


@pytest.fixture
def launcher(tmp_path):
    return make_fake_harness(tmp_path / "fake")


@pytest.fixture
def outpost_state(tmp_path):
    d = tmp_path / "outpost-state"
    d.mkdir()
    return d


@pytest.fixture
def trailhead_state(tmp_path):
    d = tmp_path / "trailhead-state"
    d.mkdir()
    return d


@pytest.fixture
def run(launcher, outpost_state, trailhead_state):
    def _run(port, argv=HOOK, *, extra_env=None, stdin='{"cwd": "/elsewhere"}'):
        env = {
            **os.environ,
            "OUTPOST_STATE_DIR": str(outpost_state),
            "TRAILHEAD_STATE_DIR": str(trailhead_state),
            "PYTHONPATH": str(REPO_ROOT),
        }
        env.update(extra_env or {})
        script = f'read go; "{sys.executable}" "{RUNNER}" {port} ' + " ".join(f"'{a}'" for a in argv)
        proc = subprocess.Popen(
            [str(launcher), "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=REPO_ROOT,
        )
        harness = harness_process(start_pid=proc.pid)
        out, err = proc.communicate("go\n" + stdin, timeout=60)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err), harness

    return _run


def _log_lines(trailhead_state):
    log = trailhead_state / "gateway" / "register.log"
    return log.read_text().splitlines() if log.exists() else []


def _closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestRequest:
    def test_sends_the_authority_and_exactly_the_harness_pid_and_start_time(self, run, outpost_state):
        write_authority(outpost_state)
        with StubOutpost() as stub:
            proc, harness = run(stub.port)
        assert proc.returncode == 0, proc.stderr
        (seen,) = stub.requests
        assert (seen.method, seen.path) == ("POST", "/gateway/sessions")
        assert seen.headers["authorization"] == f"Bearer {REGISTRATION_AUTHORITY}"
        assert json.loads(seen.body) == {"pid": harness[0], "start_time": harness[1]}

    def test_success_prints_nothing_and_writes_no_log(self, run, outpost_state, trailhead_state):
        write_authority(outpost_state)
        with StubOutpost() as stub:
            proc, _ = run(stub.port)
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
        assert _log_lines(trailhead_state) == []

    def test_a_different_authority_file_is_what_is_sent(self, run, outpost_state):
        write_authority(outpost_state, "another-authority-SEEDED-22")
        with StubOutpost(authority="another-authority-SEEDED-22") as stub:
            proc, _ = run(stub.port)
        assert stub.requests[0].headers["authorization"] == "Bearer another-authority-SEEDED-22"
        assert proc.stderr == ""

    def test_running_twice_sends_two_requests_and_stays_exit_zero(self, run, outpost_state):
        write_authority(outpost_state)
        with StubOutpost() as stub:
            first, _ = run(stub.port)
            second, _ = run(stub.port)
        assert (first.returncode, second.returncode) == (0, 0)
        assert len(stub.requests) == 2


class TestIgnoresTheCaller:
    def test_arguments_and_environment_do_not_change_the_registered_process(self, run, outpost_state):
        write_authority(outpost_state)
        with StubOutpost() as stub:
            proc, harness = run(
                stub.port,
                (*HOOK, "--pid", "1", "--workspace", "evil/ws", "--session-key", "ws-1-1"),
                extra_env={
                    "PID": "1",
                    "WORKSPACE": "evil/ws",
                    "SESSION_KEY": "ws-1-1",
                    "OUTPOST_SESSION_KEY": "ws-1-1",
                },
            )
        assert proc.returncode == 0, proc.stderr
        (seen,) = stub.requests
        assert json.loads(seen.body) == {"pid": harness[0], "start_time": harness[1]}

    def test_the_hook_input_cwd_does_not_reach_the_request(self, run, outpost_state):
        write_authority(outpost_state)
        with StubOutpost() as stub:
            run(stub.port, stdin='{"cwd": "/some/other/workspace", "session_id": "abc"}')
        body = json.loads(stub.requests[0].body)
        assert set(body) == {"pid", "start_time"}


FAILURES = [
    pytest.param(401, "registration_authority_required", id="401"),
    pytest.param(422, "not_in_workspace", id="422"),
    pytest.param(409, "process_mismatch", id="409"),
    pytest.param(403, "host_not_allowed", id="403"),
    pytest.param(400, "invalid_request", id="400"),
    pytest.param(500, "internal", id="500"),
]


class TestFailurePaths:
    @pytest.mark.parametrize(("status", "reason"), FAILURES)
    def test_a_refusal_exits_zero_names_the_reason_on_stderr_and_logs_one_line(
        self, run, outpost_state, trailhead_state, status, reason
    ):
        write_authority(outpost_state)
        with StubOutpost(register_reply=(status, {"reason": reason, "message": ECHOED_SECRET})) as stub:
            proc, harness = run(stub.port)
        assert proc.returncode == 0
        assert proc.stdout == ""
        assert proc.stderr.strip() == reason
        (line,) = _log_lines(trailhead_state)
        parts = line.split()
        assert parts[-1] == reason
        assert parts[-2] == f"ws-{harness[0]}-{harness[1]}"

    def test_a_missing_authority_file_exits_zero_and_is_reason_coded(self, run, trailhead_state):
        with StubOutpost() as stub:
            proc, _ = run(stub.port)
        assert stub.requests == []
        assert proc.returncode == 0
        assert proc.stderr.strip() == "authority_missing"
        (line,) = _log_lines(trailhead_state)
        assert line.split()[-1] == "authority_missing"

    def test_a_closed_port_exits_zero_well_inside_two_seconds(self, run, outpost_state, trailhead_state):
        write_authority(outpost_state)
        started = time.monotonic()
        proc, _ = run(_closed_port())
        elapsed = time.monotonic() - started
        assert proc.returncode == 0
        assert proc.stderr.strip() == "outpost_unreachable"
        assert _log_lines(trailhead_state)[0].split()[-1] == "outpost_unreachable"
        assert elapsed < 2.0

    def test_a_reply_that_is_not_the_contract_is_reason_coded_without_echoing_it(
        self, run, outpost_state, trailhead_state
    ):
        write_authority(outpost_state)
        with StubOutpost(register_reply=(422, {"reason": ECHOED_SECRET, "message": ECHOED_SECRET})) as stub:
            proc, _ = run(stub.port)
        assert proc.returncode == 0
        assert proc.stderr.strip() == "unexpected_reply"
        assert _log_lines(trailhead_state)[0].split()[-1] == "unexpected_reply"

    def test_no_harness_ancestor_registers_nothing_and_is_reason_coded(self, run, outpost_state, trailhead_state):
        write_authority(outpost_state)
        with StubOutpost() as stub:
            proc, _ = run(stub.port, extra_env={"REGISTER_RUNNER_NO_HARNESS": "1"})
        assert stub.requests == []
        assert proc.returncode == 0
        assert proc.stderr.strip() == "no_harness"
        (line,) = _log_lines(trailhead_state)
        assert line.split()[-2:] == ["-", "no_harness"]

    @pytest.mark.parametrize("scenario", ["refused", "missing", "unreachable"])
    def test_no_secret_reaches_stdout_stderr_or_the_log(self, run, outpost_state, trailhead_state, scenario):
        write_authority(outpost_state)
        if scenario == "missing":
            (outpost_state / "registration" / "credential").unlink()
        port = _closed_port()
        if scenario == "refused":
            with StubOutpost(register_reply=(422, {"reason": "not_in_workspace", "message": ECHOED_SECRET})) as stub:
                proc, _ = run(stub.port)
        else:
            proc, _ = run(port)
        everything = proc.stdout + proc.stderr + "\n".join(_log_lines(trailhead_state))
        assert REGISTRATION_AUTHORITY not in everything
        assert ECHOED_SECRET not in everything
        assert _log_lines(trailhead_state), "a failure must leave a log line"


class TestWithoutHookFlag:
    def test_reports_success_and_exit_zero(self, run, outpost_state):
        write_authority(outpost_state)
        with StubOutpost() as stub:
            proc, harness = run(stub.port, ("gateway", "register"))
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == f"registered ws-{harness[0]}-{harness[1]}"

    def test_reports_failure_with_a_nonzero_exit(self, run, outpost_state):
        write_authority(outpost_state)
        with StubOutpost(register_reply=(422, {"reason": "not_in_workspace", "message": "x"})) as stub:
            proc, _ = run(stub.port, ("gateway", "register"))
        assert proc.returncode == 1
        assert "not_in_workspace" in proc.stderr


class TestAuthorityProblems:
    @pytest.mark.parametrize(
        ("setup", "reason"),
        [
            pytest.param(
                lambda d: (d / "registration" / "credential").mkdir(parents=True),
                "authority_unreadable",
                id="directory",
            ),
            pytest.param(lambda d: write_authority(d, "bad\x07authority"), "authority_unreadable", id="control-char"),
            pytest.param(lambda d: write_authority(d, ""), "authority_missing", id="empty"),
        ],
    )
    def test_each_is_reason_coded_with_nothing_sent(self, run, outpost_state, trailhead_state, setup, reason):
        setup(outpost_state)
        with StubOutpost() as stub:
            proc, _ = run(stub.port)
        assert stub.requests == []
        assert proc.returncode == 0
        assert proc.stderr.strip() == reason
        assert _log_lines(trailhead_state)[0].split()[-1] == reason

    def test_an_unforeseen_failure_exits_zero_with_a_generic_reason(self, run, outpost_state, trailhead_state):
        write_authority(outpost_state, "authority-日本語")
        with StubOutpost() as stub:
            proc, _ = run(stub.port)
        assert proc.returncode == 0
        assert proc.stderr.strip() == "unexpected_failure"
        assert _log_lines(trailhead_state)[0].split()[-2:] == ["-", "unexpected_failure"]
        assert "日本語" not in proc.stderr + "".join(_log_lines(trailhead_state))
