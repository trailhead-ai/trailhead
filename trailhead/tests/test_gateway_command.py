"""`outpost-gateway run list` against a stub Outpost and a fake harness ancestor."""

from __future__ import annotations

import json
import os

import pytest

from trailhead.tests._gateway_support import (
    CLIENT_ID,
    CLIENT_SECRET,
    TOKEN,
    StubOutpost,
    free_port,
    run_gateway_runner,
    runner_env,
    write_credential,
)
from trailhead.tests._harness_process_support import make_fake_harness

RUNS = [
    {"id": "run-1", "workspace": "trailhead/ws", "job": "unit-tests", "state": "finished",
     "result": "passed", "created_at": "2026-11-10T09:00:00Z"},
    {"id": "run-2", "workspace": "trailhead/ws", "job": "lint", "state": "running",
     "result": None, "created_at": "2026-11-10T09:05:00Z"},
]

SANDBOX_LINE = "outpost-gateway is running inside the sandbox; run it on its own, not piped or chained"


@pytest.fixture
def launcher(tmp_path):
    return make_fake_harness(tmp_path / "fake")


@pytest.fixture
def state(tmp_path):
    d = tmp_path / "outpost-state"
    d.mkdir()
    return d


def _run(launcher, tmp_path, state, stub_port, argv=("run", "list"), *, seed=True, extra_env=None):
    return run_gateway_runner(
        launcher,
        list(argv),
        state_dir=state,
        port=stub_port,
        seed=(lambda key: write_credential(state, key)) if seed is True else seed,
        extra_env=extra_env,
        scratch=tmp_path,
    )


class TestRunList:
    def test_prints_the_stub_s_runs_as_a_table(self, launcher, tmp_path, state):
        with StubOutpost(workspace="trailhead/ws", runs=RUNS) as stub:
            r = _run(launcher, tmp_path, state, stub.port)
        assert r.proc.returncode == 0, r.proc.stderr
        lines = r.proc.stdout.splitlines()
        assert lines[0].split() == ["ID", "JOB", "STATE", "RESULT", "CREATED"]
        assert lines[1].split() == ["run-1", "unit-tests", "finished", "passed", "2026-11-10T09:00:00Z"]
        assert lines[2].split() == ["run-2", "lint", "running", "-", "2026-11-10T09:05:00Z"]

    def test_json_flag_prints_the_workspace_and_runs(self, launcher, tmp_path, state):
        with StubOutpost(workspace="trailhead/ws", runs=RUNS) as stub:
            r = _run(launcher, tmp_path, state, stub.port, ("run", "list", "--json"))
        assert r.proc.returncode == 0, r.proc.stderr
        assert json.loads(r.proc.stdout) == {"workspace": "trailhead/ws", "runs": RUNS}

    def test_no_runs_line_names_the_workspace(self, launcher, tmp_path, state):
        with StubOutpost(workspace="acme/feature-x", runs=[]) as stub:
            r = _run(launcher, tmp_path, state, stub.port)
        assert r.proc.returncode == 0, r.proc.stderr
        assert r.proc.stdout == "No runs in workspace acme/feature-x\n"

    def test_token_request_carries_the_credential_files_id_and_secret(self, launcher, tmp_path, state):
        def seed(key):
            write_credential(state, key, client_id="other-id", client_secret="other-secret")

        with StubOutpost(client_id="other-id", client_secret="other-secret", runs=RUNS) as stub:
            r = _run(launcher, tmp_path, state, stub.port, seed=seed)
            token_req = next(q for q in stub.requests if q.path == "/broker/token")
        assert r.proc.returncode == 0, r.proc.stderr
        assert token_req.method == "POST"
        import base64

        assert token_req.headers["authorization"] == "Basic " + base64.b64encode(b"other-id:other-secret").decode()
        assert token_req.body == "grant_type=client_credentials"

    def test_runs_request_carries_the_bearer_token_the_stub_issued(self, launcher, tmp_path, state):
        with StubOutpost(token="a-different-token", runs=RUNS) as stub:
            r = _run(launcher, tmp_path, state, stub.port)
            runs_req = next(q for q in stub.requests if q.path == "/gateway/runs")
        assert r.proc.returncode == 0, r.proc.stderr
        assert runs_req.headers["authorization"] == "Bearer a-different-token"

    def test_requests_ignore_proxy_environment(self, launcher, tmp_path, state):
        with StubOutpost(runs=RUNS) as stub:
            r = _run(
                launcher, tmp_path, state, stub.port,
                extra_env={
                    "HTTP_PROXY": "http://127.0.0.1:9",
                    "http_proxy": "http://127.0.0.1:9",
                    "NO_PROXY": "",
                    "no_proxy": "",
                },
            )
        assert r.proc.returncode == 0, r.proc.stderr


class TestFailurePaths:
    def _fails_with(self, r, text):
        assert r.proc.returncode != 0
        assert r.proc.stdout == ""
        assert r.proc.stderr.splitlines() == [text]

    def test_no_harness_ancestor_says_it_is_running_in_the_sandbox(self, tmp_path, state):
        import subprocess
        import sys
        from trailhead.tests._gateway_support import GATEWAY_RUNNER, REPO_ROOT

        with StubOutpost(runs=RUNS) as stub:
            proc = subprocess.run(
                [sys.executable, str(GATEWAY_RUNNER), str(stub.port), str(tmp_path / "env.json"), "run", "list"],
                capture_output=True, text=True, cwd=REPO_ROOT,
                env=runner_env(state, extra={"GATEWAY_RUNNER_NO_HARNESS": "1"}),
            )
            assert stub.requests == []
        assert proc.returncode != 0
        assert proc.stdout == ""
        assert proc.stderr.strip() == SANDBOX_LINE

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
    def test_unreadable_credential_file_says_it_is_running_in_the_sandbox(self, launcher, tmp_path, state):
        def seed(key):
            write_credential(state, key).chmod(0)

        with StubOutpost(runs=RUNS) as stub:
            r = _run(launcher, tmp_path, state, stub.port, seed=seed)
            assert stub.requests == []
        self._fails_with(r, SANDBOX_LINE)

    def test_missing_credential_file_points_at_the_register_log(self, launcher, tmp_path, state):
        with StubOutpost(runs=RUNS) as stub:
            r = _run(
                launcher, tmp_path, state, stub.port, seed=None, extra_env={"XDG_STATE_HOME": str(tmp_path / "xdg")}
            )
        expected_log = tmp_path / "xdg" / "trailhead" / "gateway" / "register.log"
        self._fails_with(r, f"outpost-gateway: this session has no gateway credential; see {expected_log} for why")

    def test_credential_missing_refusal_is_named_by_its_reason_code(self, launcher, tmp_path, state):
        body = {"reason": "credential_missing", "message": "No credential was presented."}
        with StubOutpost(runs_reply=(401, body)) as stub:
            r = _run(launcher, tmp_path, state, stub.port)
        self._fails_with(r, "outpost-gateway: credential_missing: No credential was presented.")

    def test_credential_unrecognised_refusal_is_named_by_its_reason_code(self, launcher, tmp_path, state):
        body = {"reason": "credential_unrecognised", "message": "The token is not recognised."}
        with StubOutpost(runs_reply=(401, body)) as stub:
            r = _run(launcher, tmp_path, state, stub.port)
        self._fails_with(r, "outpost-gateway: credential_unrecognised: The token is not recognised.")

    def test_403_refusal_is_named_by_its_reason_code(self, launcher, tmp_path, state):
        body = {"reason": "workspace_missing", "message": "No workspace.", "kind": "operator"}
        with StubOutpost(runs_reply=(403, body)) as stub:
            r = _run(launcher, tmp_path, state, stub.port)
        self._fails_with(r, "outpost-gateway: workspace_missing: No workspace.")

    def test_token_exchange_refusal_is_named_by_its_error_code(self, launcher, tmp_path, state):
        with StubOutpost(client_secret="not-the-seeded-secret") as stub:
            r = _run(launcher, tmp_path, state, stub.port)
            assert [q.path for q in stub.requests] == ["/broker/token"]
        self._fails_with(r, "outpost-gateway: Outpost refused the credential: invalid_client")

    def test_closed_port_says_outpost_is_unreachable(self, launcher, tmp_path, state):
        port = free_port()
        r = _run(launcher, tmp_path, state, port)
        self._fails_with(r, f"outpost-gateway: Outpost is not reachable at 127.0.0.1:{port}; is it running?")

    def test_malformed_credential_file_is_reported_as_no_usable_credential(self, launcher, tmp_path, state):
        def seed(key):
            (write_credential(state, key)).write_text("{not json")

        with StubOutpost() as stub:
            r = _run(
                launcher, tmp_path, state, stub.port, seed=seed, extra_env={"XDG_STATE_HOME": str(tmp_path / "xdg")}
            )
            assert stub.requests == []
        assert r.proc.returncode != 0
        assert "this session has no gateway credential" in r.proc.stderr


class TestHygiene:
    """Recognisable secret and token values are seeded; they must appear nowhere."""

    SCENARIOS = {
        "success": dict(),
        "success-json": dict(argv=("run", "list", "--json")),
        "empty": dict(runs=[]),
        "runs-401": dict(runs_reply=(401, {"reason": "credential_revoked", "message": "Revoked."})),
        "runs-403": dict(runs_reply=(403, {"reason": "scope_missing", "message": "Nope.", "kind": "x"})),
        "token-refused": dict(token_reply=(401, {"error": "invalid_client"})),
        "token-garbage": dict(token_reply=(200, "<<not json>>")),
        "runs-garbage": dict(runs_reply=(200, "<<not json>>")),
        "runs-wrong-shape": dict(runs_reply=(200, {"workspace": 7, "runs": "x"})),
    }

    @pytest.mark.parametrize("name", sorted(SCENARIOS))
    def test_secret_and_token_never_appear_in_output_or_environment(self, name, launcher, tmp_path, state):
        spec = dict(self.SCENARIOS[name])
        argv = spec.pop("argv", ("run", "list"))
        spec.setdefault("runs", RUNS)
        with StubOutpost(**spec) as stub:
            r = _run(launcher, tmp_path, state, stub.port, argv)
        for secret in (CLIENT_SECRET, TOKEN):
            assert secret not in r.proc.stdout
            assert secret not in r.proc.stderr
            assert "Traceback" not in r.proc.stderr
            if r.env_after is not None:
                assert all(secret not in v for v in r.env_after.values())
                assert all(secret not in k for k in r.env_after)
        assert "AssertionError" not in r.proc.stderr, "the command started a subprocess"

    def test_secret_never_appears_when_outpost_is_unreachable(self, launcher, tmp_path, state):
        port = free_port()
        r = _run(launcher, tmp_path, state, port)
        assert r.proc.returncode != 0
        for secret in (CLIENT_SECRET, TOKEN):
            assert secret not in r.proc.stdout
            assert secret not in r.proc.stderr
            assert "Traceback" not in r.proc.stderr
            assert r.env_after is not None
            assert all(secret not in v for v in r.env_after.values())
            assert all(secret not in k for k in r.env_after)
        assert "AssertionError" not in r.proc.stderr, "the command started a subprocess"

    def test_a_reply_of_the_wrong_shape_is_reported_as_unreadable(self, launcher, tmp_path, state):
        with StubOutpost(runs_reply=(200, {"workspace": 7, "runs": "x"})) as stub:
            r = _run(launcher, tmp_path, state, stub.port)
        assert r.proc.returncode != 0
        assert r.proc.stderr.strip() == "outpost-gateway: Outpost answered with a reply this command cannot read"


class TestRedirects:
    CANNOT_READ = "outpost-gateway: Outpost answered with a reply this command cannot read"

    @pytest.mark.parametrize("route", ["token", "runs"])
    def test_a_redirect_is_not_followed_off_the_first_listener(self, route, launcher, tmp_path, state):
        with StubOutpost(runs=RUNS) as second:
            kwargs = {f"{route}_redirect": f"http://127.0.0.1:{second.port}/stolen"}
            with StubOutpost(runs=RUNS, **kwargs) as first:
                r = _run(launcher, tmp_path, state, first.port)
            assert second.requests == []
        assert r.proc.returncode != 0
        assert r.proc.stdout == ""
        assert r.proc.stderr.splitlines() == [self.CANNOT_READ]
        for secret in (CLIENT_SECRET, TOKEN):
            assert secret not in r.proc.stderr


class TestIgnoresCallerInput:
    def _decoy(self, state, key):
        write_credential(state, key, client_id="decoy-id", client_secret="decoy-secret")

    def test_session_key_environment_variables_do_not_change_the_credential_read(self, launcher, tmp_path, state):
        decoy_key = "ws-1-1"
        self._decoy(state, decoy_key)

        env = {
            name: decoy_key
            for name in (
                "OUTPOST_SESSION_KEY",
                "OUTPOST_SESSION",
                "TRAILHEAD_SESSION_KEY",
                "SESSION_KEY",
                "CAMP_SESSION_KEY",
            )
        }
        with StubOutpost(runs=RUNS) as stub:
            r = _run(launcher, tmp_path, state, stub.port, extra_env=env)
            token_req = next(q for q in stub.requests if q.path == "/broker/token")
        assert r.proc.returncode == 0, r.proc.stderr
        import base64

        basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        assert token_req.headers["authorization"] == "Basic " + basic

    @pytest.mark.parametrize("flag", ["--session-key", "--session", "--key"])
    def test_a_session_key_argument_is_refused_and_nothing_is_read_or_sent(self, flag, launcher, tmp_path, state):
        self._decoy(state, "ws-1-1")
        with StubOutpost(runs=RUNS) as stub:
            r = _run(launcher, tmp_path, state, stub.port, ("run", "list", flag, "ws-1-1"))
            assert stub.requests == []
        assert r.proc.returncode != 0
        assert r.proc.stdout == ""


class TestHelpNamesTheInvocation:
    """A session has to type the shim's absolute path, so --help says where it is."""

    @pytest.mark.parametrize("name", ["state-a", "state b"])
    def test_help_shows_the_shim_path_under_the_state_dir_in_use(self, tmp_path, monkeypatch, capsys, name):
        from trailhead.gateway.command import main

        monkeypatch.setenv("TRAILHEAD_STATE_DIR", str(tmp_path / name))
        with pytest.raises(SystemExit) as stop:
            main(["--help"])
        assert stop.value.code == 0
        assert f"run it as: {tmp_path / name / 'bin' / 'outpost-gateway'} run list" in capsys.readouterr().out
