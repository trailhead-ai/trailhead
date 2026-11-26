"""`trailhead gateway register --hook` against a stub Outpost and a fake harness ancestor."""

from __future__ import annotations

import json
import time

import pytest

from trailhead.tests._gateway_support import (
    await_harness,
    REGISTRATION_AUTHORITY,
    StubOutpost,
    free_port,
    register_runner_cmd,
    run_under_harness,
    runner_env,
    silent_listener,
    write_authority,
)
from trailhead.tests._harness_process_support import make_fake_harness

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
        found = []
        proc = run_under_harness(
            launcher,
            register_runner_cmd(port, list(argv)),
            env=runner_env(outpost_state, trailhead_state=trailhead_state, extra=extra_env),
            on_start=lambda pid: found.append(await_harness(pid)),
            stdin=stdin,
        )
        return proc, found[0]

    return _run


def _hook_output(proc):
    """The one JSON object the hook printed, with the invariants every such object holds."""
    assert proc.stdout.strip(), "the hook printed nothing"
    out = json.loads(proc.stdout)
    line = out["systemMessage"]
    assert "\n" not in line and line.strip()
    assert out["hookSpecificOutput"] == {"hookEventName": "SessionStart", "additionalContext": line}
    assert set(out) == {"systemMessage", "hookSpecificOutput"}
    return line


def _log_lines(trailhead_state):
    log = trailhead_state / "gateway" / "register.log"
    return log.read_text().splitlines() if log.exists() else []


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
        message = _hook_output(proc)
        assert proc.stderr.strip() == reason
        (line,) = _log_lines(trailhead_state)
        parts = line.split()
        assert parts[2] == reason
        assert parts[1] == f"ws-{harness[0]}-{harness[1]}"
        assert line.endswith(f" {message}")

    def test_a_missing_authority_file_exits_zero_and_is_reason_coded(self, run, trailhead_state):
        with StubOutpost() as stub:
            proc, _ = run(stub.port)
        assert stub.requests == []
        assert proc.returncode == 0
        assert proc.stderr.strip() == "authority_missing"
        (line,) = _log_lines(trailhead_state)
        assert line.split()[2] == "authority_missing"

    def test_a_closed_port_exits_zero_well_inside_two_seconds(self, run, outpost_state, trailhead_state):
        write_authority(outpost_state)
        started = time.monotonic()
        proc, _ = run(free_port())
        elapsed = time.monotonic() - started
        assert proc.returncode == 0
        assert proc.stderr.strip() == "outpost_unreachable"
        assert _log_lines(trailhead_state)[0].split()[2] == "outpost_unreachable"
        assert elapsed < 2.0

    def test_a_reply_that_is_not_the_contract_is_reason_coded_without_echoing_it(
        self, run, outpost_state, trailhead_state
    ):
        write_authority(outpost_state)
        with StubOutpost(register_reply=(422, {"reason": ECHOED_SECRET, "message": ECHOED_SECRET})) as stub:
            proc, _ = run(stub.port)
        assert proc.returncode == 0
        assert proc.stderr.strip() == "unexpected_reply"
        assert _log_lines(trailhead_state)[0].split()[2] == "unexpected_reply"

    def test_no_harness_ancestor_registers_nothing_and_is_reason_coded(self, run, outpost_state, trailhead_state):
        write_authority(outpost_state)
        with StubOutpost() as stub:
            proc, _ = run(stub.port, extra_env={"REGISTER_RUNNER_NO_HARNESS": "1"})
        assert stub.requests == []
        assert proc.returncode == 0
        assert proc.stderr.strip() == "no_harness"
        (line,) = _log_lines(trailhead_state)
        assert line.split()[1:3] == ["-", "no_harness"]

    @pytest.mark.parametrize("scenario", ["refused", "missing", "unreachable"])
    def test_no_secret_reaches_stdout_stderr_or_the_log(self, run, outpost_state, trailhead_state, scenario):
        write_authority(outpost_state)
        if scenario == "missing":
            (outpost_state / "registration" / "credential").unlink()
        port = free_port()
        if scenario == "refused":
            with StubOutpost(register_reply=(422, {"reason": "not_in_workspace", "message": ECHOED_SECRET})) as stub:
                proc, _ = run(stub.port)
        else:
            proc, _ = run(port)
        everything = proc.stdout + proc.stderr + "\n".join(_log_lines(trailhead_state))
        assert REGISTRATION_AUTHORITY not in everything
        assert ECHOED_SECRET not in everything
        assert _log_lines(trailhead_state), "a failure must leave a log line"


class TestNoCredentialMessage:
    """When no credential results, the hook says so in one line: on stdout for the session, and in the log."""

    def test_unreachable_outpost_says_it_will_register_on_the_first_gateway_call(
        self, run, outpost_state, trailhead_state
    ):
        write_authority(outpost_state)
        proc, _ = run(free_port())
        assert proc.returncode == 0
        message = _hook_output(proc)
        assert "no gateway credential" in message.lower()
        assert "it will register on the first gateway call once Outpost is back" in message
        assert _log_lines(trailhead_state)[0].endswith(f" {message}")

    def test_a_refusal_carries_outposts_reason_and_its_next_step(self, run, outpost_state):
        write_authority(outpost_state)
        reply = (403, {"reason": "host_not_allowed", "message": "Open Outpost from the host."})
        with StubOutpost(register_reply=reply) as stub:
            proc, _ = run(stub.port)
        message = _hook_output(proc)
        assert "host_not_allowed" in message
        assert "Open Outpost from the host." in message

    def test_a_refusal_without_a_message_carries_the_reasons_next_step(self, run, outpost_state):
        write_authority(outpost_state)
        with StubOutpost(register_reply=(500, {"reason": "internal"})) as stub:
            proc, _ = run(stub.port)
        message = _hook_output(proc)
        assert "internal" in message

    def test_outside_a_camp_workspace_says_so_in_the_gateway_commands_words(self, run, outpost_state):
        from trailhead.gateway.register import NO_GATEWAY_OUTSIDE_WORKSPACE

        write_authority(outpost_state)
        with StubOutpost(register_reply=(422, {"reason": "not_in_workspace", "message": ECHOED_SECRET})) as stub:
            proc, _ = run(stub.port)
        message = _hook_output(proc)
        assert NO_GATEWAY_OUTSIDE_WORKSPACE in message
        assert ECHOED_SECRET not in message

    def test_a_local_failure_names_its_reason_and_next_step(self, run):
        with StubOutpost() as stub:
            proc, _ = run(stub.port)
        message = _hook_output(proc)
        assert "authority_missing" in message
        assert "Start Outpost" in message

    def test_a_message_with_control_characters_and_newlines_stays_one_clean_line(self, run, outpost_state):
        write_authority(outpost_state)
        dirty = "Do this.\nThen\tthat\x1b[31m now\x07."
        with StubOutpost(register_reply=(403, {"reason": "host_not_allowed", "message": dirty})) as stub:
            proc, _ = run(stub.port)
        message = _hook_output(proc)
        assert "Do this. Then that" in message
        assert not any(ord(c) < 0x20 or 0x7f <= ord(c) <= 0x9f for c in message)

    def test_a_silent_outpost_exits_zero_with_the_line_well_inside_two_seconds(self, run, outpost_state):
        write_authority(outpost_state)
        with silent_listener() as port:
            started = time.monotonic()
            proc, _ = run(port)
            elapsed = time.monotonic() - started
        assert proc.returncode == 0
        assert "once Outpost is back" in _hook_output(proc)
        assert elapsed < 2.0, elapsed


class TestAuthorityIsReadSafely:
    """The registration authority is read as the gateway credential is: no link followed, a regular file, the
    operator's, mode 0600."""

    def test_an_authority_that_is_a_link_is_refused_with_a_message_and_nothing_is_sent(self, run, outpost_state):
        real = write_authority(outpost_state)
        planted = real.with_name("elsewhere")
        real.rename(planted)
        real.symlink_to(planted)
        with StubOutpost() as stub:
            proc, _ = run(stub.port)
        assert stub.requests == []
        assert proc.returncode == 0
        message = _hook_output(proc)
        assert "symbolic link" in message and "refusing to read" in message
        assert REGISTRATION_AUTHORITY not in proc.stdout + proc.stderr

    @pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o700, 0o400])
    def test_an_authority_whose_mode_is_not_0600_is_refused_and_nothing_is_sent(self, run, outpost_state, mode):
        write_authority(outpost_state, mode=mode)
        with StubOutpost() as stub:
            proc, _ = run(stub.port)
        assert stub.requests == []
        message = _hook_output(proc)
        assert f"mode {mode:04o}" in message and "0600" in message

    def test_an_authority_that_is_not_a_regular_file_is_refused_and_nothing_is_sent(self, run, outpost_state):
        path = outpost_state / "registration" / "credential"
        path.mkdir(parents=True)
        with StubOutpost() as stub:
            proc, _ = run(stub.port)
        assert stub.requests == []
        assert "not a regular file" in _hook_output(proc)

    def test_an_authority_the_operator_does_not_own_is_refused_and_nothing_is_sent(self, outpost_state, monkeypatch):
        import os

        from trailhead.gateway import register

        write_authority(outpost_state)
        monkeypatch.setenv("OUTPOST_STATE_DIR", str(outpost_state))
        monkeypatch.setattr(register, "_operator_uid", lambda: os.geteuid() + 1)
        with pytest.raises(register.Refused) as refused:
            register._read_authority()
        assert "not owned by you" in refused.value.message

    def test_an_authority_the_operator_owns_with_mode_0600_is_read(self, outpost_state, monkeypatch):
        from trailhead.gateway import register

        write_authority(outpost_state, "an-authority-SEEDED-5")
        monkeypatch.setenv("OUTPOST_STATE_DIR", str(outpost_state))
        assert register._read_authority() == "an-authority-SEEDED-5"


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
        assert _log_lines(trailhead_state)[0].split()[2] == reason

    def test_an_unforeseen_failure_exits_zero_with_a_generic_reason(self, run, outpost_state, trailhead_state):
        write_authority(outpost_state, "authority-日本語")
        with StubOutpost() as stub:
            proc, _ = run(stub.port)
        assert proc.returncode == 0
        assert proc.stderr.strip() == "unexpected_failure"
        assert _log_lines(trailhead_state)[0].split()[1:3] == ["-", "unexpected_failure"]
        assert "日本語" not in proc.stderr + "".join(_log_lines(trailhead_state))


class TestRefusalCarriesOutpostsNextStep:
    """What the on-demand registration of the gateway command reads from a refusal; the hook prints only the reason
    code."""

    def _register(self, monkeypatch, outpost_state, port, **kwargs):
        from trailhead.gateway import register

        monkeypatch.setenv("OUTPOST_STATE_DIR", str(outpost_state))
        monkeypatch.setattr(register, "harness_process", lambda: (4242, 987654))
        write_authority(outpost_state)
        return register.register(port=port, **kwargs)

    @pytest.mark.parametrize("message", ["Move to a camp workspace.", "Try again later."])
    def test_a_refusal_holds_the_message_outpost_sent_with_its_reason(self, message, monkeypatch, outpost_state):
        from trailhead.gateway import register

        with StubOutpost(register_reply=(422, {"reason": "not_in_workspace", "message": message})) as stub:
            with pytest.raises(register.Refused) as refused:
                self._register(monkeypatch, outpost_state, stub.port)
        assert (refused.value.reason, refused.value.message) == ("not_in_workspace", message)

    def test_a_refusal_without_a_message_holds_none(self, monkeypatch, outpost_state):
        from trailhead.gateway import register

        with StubOutpost(register_reply=(500, {"reason": "internal"})) as stub:
            with pytest.raises(register.Refused) as refused:
                self._register(monkeypatch, outpost_state, stub.port)
        assert (refused.value.reason, refused.value.message) == ("internal", None)

    def test_the_port_asked_for_is_the_port_called(self, monkeypatch, outpost_state):
        with StubOutpost() as stub:
            key = self._register(monkeypatch, outpost_state, stub.port, timeout=None)
            assert [q.path for q in stub.requests] == ["/gateway/sessions"]
        assert key == "ws-4242-987654"
