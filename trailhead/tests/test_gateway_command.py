"""`outpost-gateway run list` against a stub Outpost and a fake harness ancestor."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tarfile
import threading

import pytest

from trailhead.gateway import command, outpost_http

from trailhead.tests._gateway_support import (
    CLIENT_ID,
    CLIENT_SECRET,
    TOKEN,
    Cut,
    StubOutpost,
    free_port,
    run_gateway_runner,
    runner_env,
    write_credential,
)
from trailhead.tests._harness_process_support import make_fake_harness

RUNS = [
    {"id": 1, "workspace": "trailhead/ws", "job": "unit-tests", "state": "completed",
     "result": "failure", "reason": "no runner for ubuntu-22.04", "created_at": "2026-11-10T09:00:00Z"},
    {"id": 2, "workspace": "trailhead/ws", "job": "lint", "state": "in_progress",
     "result": None, "reason": None, "created_at": "2026-11-10T09:05:00Z"},
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
        assert lines[0].split() == ["ID", "JOB", "STATE", "RESULT", "REASON", "CREATED"]
        assert lines[1].split() == [
            "1", "unit-tests", "completed", "failure", "no", "runner", "for", "ubuntu-22.04", "2026-11-10T09:00:00Z"
        ]
        assert lines[2].split() == ["2", "lint", "in_progress", "-", "-", "2026-11-10T09:05:00Z"]

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


# --- run start / watch / view -------------------------------------------------------------

WORKFLOW = ".github/workflows/ci.yml"
CI_YML = """name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: make
  test:
    runs-on: ubuntu-latest
"""
LINT_YML = """name: lint
jobs:
  lint:
    runs-on: ubuntu-latest
"""
NOT_FOUND = (404, {"reason": "run_not_found", "message": "No run with that id."})


def view(**over):
    base = {
        "id": 7, "workspace": "trailhead/ws", "workflow": WORKFLOW, "job": "build", "matrix": None,
        "state": "completed", "result": "success", "reason": None,
        "created_at": "2026-11-10T09:00:00Z", "started_at": "2026-11-10T09:00:01Z",
        "finished_at": "2026-11-10T09:00:09Z", "exit_code": 0,
    }
    return {**base, **over}


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, hermetic_git_env):
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "sub").mkdir()
    (root / WORKFLOW).write_text(CI_YML)
    (root / ".gitignore").write_text("ignored.log\n")
    (root / "tracked.txt").write_text("v1\n")
    (root / "gone.txt").write_text("gone\n")
    (root / "script.sh").write_text("#!/bin/sh\n")
    (root / "script.sh").chmod(0o755)
    (root / "sub" / "inner.txt").write_text("inner\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")
    (root / "tracked.txt").write_text("v2 edited\n")
    (root / "gone.txt").unlink()
    (root / "untracked.txt").write_text("fresh\n")
    (root / "ignored.log").write_text("noise\n")
    (root / "link").symlink_to("/etc/passwd")
    (root / "nested").mkdir()
    git(root / "nested", "init", "-q")
    (root / "nested" / "f.txt").write_text("n\n")
    return root


@pytest.fixture
def scratch_home(tmp_path):
    """The operator home the command makes its private upload folder under."""
    home = tmp_path / "scratch-home"
    home.mkdir()
    return home


@pytest.fixture
def cli(tmp_path, state, scratch_home, monkeypatch, capsys):
    monkeypatch.setenv("OUTPOST_STATE_DIR", str(state))
    monkeypatch.setattr(command, "session_key", lambda: "ws-test-1")
    write_credential(state, "ws-test-1")

    def call(port, *argv, cwd=None):
        if cwd is not None:
            monkeypatch.chdir(cwd)
        capsys.readouterr()
        rc = command.main(list(argv), port=port, scratch_home=scratch_home)
        out, err = capsys.readouterr()
        return rc, out, err

    return call


def start_request(stub):
    return next(q for q in stub.requests if q.method == "POST" and q.path.startswith("/gateway/runs"))


def uploaded(stub):
    return tarfile.open(fileobj=io.BytesIO(start_request(stub).raw))


class TestRunStart:
    def test_upload_holds_the_working_tree_and_only_it(self, cli, repo):
        with StubOutpost() as stub:
            rc, _, err = cli(stub.port, "run", "start", "build", cwd=repo)
            tar = uploaded(stub)
        assert rc == 0, err
        names = set(tar.getnames())
        assert tar.extractfile("tracked.txt").read() == b"v2 edited\n"
        assert "untracked.txt" in names
        assert "sub/inner.txt" in names
        assert "ignored.log" not in names
        assert "gone.txt" not in names
        assert not any(n.startswith("nested") for n in names)
        assert not any(n.startswith(".git/") for n in names)

    def test_a_symlink_is_sent_as_a_link_with_no_content(self, cli, repo):
        with StubOutpost() as stub:
            cli(stub.port, "run", "start", "build", cwd=repo)
            member = uploaded(stub).getmember("link")
        assert member.issym()
        assert member.linkname == "/etc/passwd"
        assert member.size == 0

    def test_the_user_exec_bit_is_kept(self, cli, repo):
        with StubOutpost() as stub:
            cli(stub.port, "run", "start", "build", cwd=repo)
            tar = uploaded(stub)
        assert tar.getmember("script.sh").mode & 0o100
        assert not tar.getmember("tracked.txt").mode & 0o100

    def test_from_a_subfolder_the_upload_is_rooted_at_the_top_level(self, cli, repo):
        with StubOutpost() as stub:
            rc, _, err = cli(stub.port, "run", "start", "build", cwd=repo / "sub")
            names = set(uploaded(stub).getnames())
        assert rc == 0, err
        assert {"tracked.txt", "sub/inner.txt", WORKFLOW} <= names

    def test_outside_a_git_work_tree_it_is_refused_before_anything_is_sent(self, cli, tmp_path):
        bare = tmp_path / "plain"
        bare.mkdir()
        with StubOutpost() as stub:
            rc, out, err = cli(stub.port, "run", "start", "build", "--workflow", WORKFLOW, cwd=bare)
            assert stub.requests == []
        assert rc == 2
        assert out == ""
        assert err == "outpost-gateway: this folder is not inside a git work tree\n"

    def test_prints_only_the_id_and_returns_without_following_the_run(self, cli, repo):
        with StubOutpost() as stub:
            rc, out, err = cli(stub.port, "run", "start", "build", cwd=repo)
            paths = [q.path.split("?")[0] for q in stub.requests]
        assert rc == 0, err
        assert out == "7\n"
        assert paths == ["/broker/token", "/gateway/runs"]

    def test_the_request_names_the_workflow_and_job_and_carries_the_bearer_token(self, cli, repo):
        with StubOutpost(token="t-123") as stub:
            cli(stub.port, "run", "start", "test", cwd=repo)
            post = start_request(stub)
        assert post.path == "/gateway/runs?workflow=.github%2Fworkflows%2Fci.yml&job=test"
        assert post.headers["authorization"] == "Bearer t-123"
        assert post.headers["content-length"] == str(len(post.raw))

    def test_the_matrix_combination_chosen_goes_to_stderr(self, cli, repo):
        reply = (202, {"id": 9, "state": "queued", "matrix": {"os": "linux", "node": "20"}, "not_run": 0})
        with StubOutpost(start_reply=reply) as stub:
            rc, out, err = cli(stub.port, "run", "start", "build", cwd=repo)
        assert (rc, out) == (0, "9\n")
        assert err == "outpost-gateway: matrix combination: os=linux, node=20\n"

    def test_other_combinations_not_run_are_counted_on_stderr(self, cli, repo):
        reply = (202, {"id": 9, "state": "queued", "matrix": {"os": "linux"}, "not_run": 3})
        with StubOutpost(start_reply=reply) as stub:
            _, _, err = cli(stub.port, "run", "start", "build", cwd=repo)
        assert "3 other matrix combinations not run" in err

    def test_a_single_other_combination_is_still_reported(self, cli, repo):
        reply = (202, {"id": 9, "state": "queued", "matrix": {"os": "linux"}, "not_run": 1})
        with StubOutpost(start_reply=reply) as stub:
            _, _, err = cli(stub.port, "run", "start", "build", cwd=repo)
        assert "1 other matrix combinations not run" in err

    def test_no_note_without_a_matrix(self, cli, repo):
        with StubOutpost() as stub:
            _, _, err = cli(stub.port, "run", "start", "build", cwd=repo)
        assert err == ""

    def test_the_only_workflow_defining_the_job_is_the_default(self, cli, repo):
        (repo / ".github" / "workflows" / "lint.yaml").write_text(LINT_YML)
        with StubOutpost() as stub:
            cli(stub.port, "run", "start", "lint", cwd=repo)
            post = start_request(stub)
        assert "workflow=.github%2Fworkflows%2Flint.yaml&job=lint" in post.path

    def test_several_workflows_defining_the_job_are_refused(self, cli, repo):
        (repo / ".github" / "workflows" / "lint.yml").write_text(LINT_YML.replace("lint:", "build:"))
        with StubOutpost() as stub:
            rc, out, err = cli(stub.port, "run", "start", "build", cwd=repo)
            assert stub.requests == []
        assert rc == 2
        assert out == ""
        assert ".github/workflows/ci.yml" in err and ".github/workflows/lint.yml" in err
        assert "--workflow" in err

    def test_no_workflow_defining_the_job_is_refused(self, cli, repo):
        with StubOutpost() as stub:
            rc, _, err = cli(stub.port, "run", "start", "deploy", cwd=repo)
            assert stub.requests == []
        assert rc == 2
        assert "no workflow under .github/workflows/ defines job deploy" in err

    def test_an_explicit_workflow_is_sent_as_given(self, cli, repo):
        (repo / ".github" / "workflows" / "lint.yml").write_text(LINT_YML.replace("lint:", "build:"))
        with StubOutpost() as stub:
            rc, _, err = cli(stub.port, "run", "start", "build", "--workflow", ".github/workflows/lint.yml", cwd=repo)
            post = start_request(stub)
        assert rc == 0, err
        assert "workflow=.github%2Fworkflows%2Flint.yml" in post.path

    @pytest.mark.parametrize(
        "status, body",
        [
            (422, {"reason": "upload_refused", "message": "The upload holds a path outside the tree.", "rule": "path"}),
            (501, {"reason": "runs_not_configured", "message": "runs are not configured on this host"}),
            (429, {"reason": "queue_full", "message": "Too many queued runs.", "limit": 4}),
        ],
    )
    def test_outposts_refusal_is_printed_with_its_reason_and_message(self, status, body, cli, repo):
        with StubOutpost(start_reply=(status, body)) as stub:
            rc, out, err = cli(stub.port, "run", "start", "build", cwd=repo)
        assert rc == 2
        assert out == ""
        assert err == f"outpost-gateway: {body['reason']}: {body['message']}\n"

    def test_a_start_reply_without_an_integer_id_is_refused(self, cli, repo):
        with StubOutpost(start_reply=(202, {"id": "7; rm", "state": "queued"})) as stub:
            rc, out, err = cli(stub.port, "run", "start", "build", cwd=repo)
        assert (rc, out) == (2, "")
        assert "cannot read" in err

    def test_an_unreachable_outpost_exits_2(self, cli, repo):
        port = free_port()
        rc, _, err = cli(port, "run", "start", "build", cwd=repo)
        assert rc == 2
        assert f"Outpost is not reachable at 127.0.0.1:{port}" in err

    def test_a_scratch_folder_reached_through_a_symlink_is_refused_before_anything_is_sent(
        self, cli, repo, scratch_home, tmp_path
    ):
        writable = tmp_path / "session-writable"
        writable.mkdir()
        (scratch_home / ".local").symlink_to(writable)
        with StubOutpost() as stub:
            rc, out, err = cli(stub.port, "run", "start", "build", cwd=repo)
            assert stub.requests == []
        assert (rc, out) == (2, "")
        assert len(err.splitlines()) == 1 and "symlink" in err

    def test_the_session_s_git_config_does_not_shape_the_upload(self, cli, repo, tmp_path):
        excludes = tmp_path / "excludes"
        excludes.write_text("untracked.txt\n")
        git(repo, "config", "core.excludesFile", str(excludes))
        with StubOutpost() as stub:
            rc, _, err = cli(stub.port, "run", "start", "build", cwd=repo)
            names = set(uploaded(stub).getnames())
        assert rc == 0, err
        assert "untracked.txt" in names


class TestRunWatch:
    LOG = ["step one\n", "step two\n"]

    def _watch(self, cli, stub, *flags):
        return cli(stub.port, "run", "watch", "7", *flags)

    def test_a_completed_run_replays_its_whole_log(self, cli):
        with StubOutpost(view=view(), log_steps=self.LOG) as stub:
            rc, out, err = self._watch(cli, stub)
            follow = next(q for q in stub.requests if "/log" in q.path)
        assert (rc, out) == (0, "step one\nstep two\n")
        assert follow.path == "/gateway/runs/7/log?follow=1"
        assert follow.headers["authorization"] == f"Bearer {TOKEN}"

    def test_lines_are_printed_as_the_stream_delivers_them(self, cli, monkeypatch):
        first_printed = threading.Event()

        class Tap(io.StringIO):
            def write(self, text):
                if "first line" in text:
                    first_printed.set()
                return super().write(text)

        tap = Tap()
        monkeypatch.setattr(outpost_http, "IDLE_TIMEOUT_SECONDS", 3.0)
        with StubOutpost(view=view(), log_steps=["first line\n", first_printed, "second line\n"]) as stub:
            monkeypatch.setattr(sys, "stdout", tap)
            rc = command.main(["run", "watch", "7"], port=stub.port)
        assert rc == 0
        assert tap.getvalue() == "first line\nsecond line\n"

    @pytest.mark.parametrize(
        "name, stub_kwargs, code, stderr_has",
        [
            ("success", dict(view=view()), 0, ""),
            ("failure", dict(view=view(result="failure", exit_code=1)), 1, ""),
            (
                "did-not-execute",
                dict(view=view(result="failure", reason="no runner for macos-14", exit_code=None)),
                3,
                "no runner for macos-14",
            ),
            ("dropped-stream", dict(view=view(state="in_progress", result=None, exit_code=None)), 2, "ended before"),
        ],
    )
    def test_exit_status_maps_the_final_view(self, name, stub_kwargs, code, stderr_has, cli):
        with StubOutpost(log_steps=self.LOG, **stub_kwargs) as stub:
            rc, _, err = self._watch(cli, stub, "--exit-status")
        assert rc == code, err
        assert stderr_has in err

    def test_exit_status_is_2_when_outpost_is_unreachable(self, cli):
        rc, _, err = cli(free_port(), "run", "watch", "7", "--exit-status")
        assert rc == 2
        assert "not reachable" in err

    def test_without_the_flag_a_failed_run_still_exits_0_once_complete(self, cli):
        with StubOutpost(view=view(result="failure", exit_code=1), log_steps=self.LOG) as stub:
            rc, _, _ = self._watch(cli, stub)
        assert rc == 0

    def test_without_the_flag_a_dropped_stream_exits_2(self, cli):
        with StubOutpost(view=view(state="in_progress", result=None), log_steps=self.LOG) as stub:
            rc, _, _ = self._watch(cli, stub)
        assert rc == 2

    @pytest.mark.parametrize(
        "bad",
        [dict(state="finished", result=None), dict(result="passed"), dict(result=None), dict(reason=5)],
        ids=["state", "result", "completed-without-result", "reason-type"],
    )
    def test_a_view_outside_the_known_values_is_refused(self, bad, cli):
        with StubOutpost(view=view(**bad), log_steps=self.LOG) as stub:
            rc, _, err = self._watch(cli, stub, "--exit-status")
        assert rc == 2
        assert "cannot read" in err

    def test_an_unknown_run_prints_no_run_in_this_workspace(self, cli):
        with StubOutpost(view_reply=NOT_FOUND) as stub:
            stub.log_steps = []
            rc, out, err = cli(stub.port, "run", "watch", "99")
        assert (rc, out) == (2, "")
        assert err == "outpost-gateway: no run 99 in this workspace\n"

    @pytest.mark.parametrize("flags", [("--exit-status",), ()], ids=["with-flag", "without-flag"])
    def test_a_connection_cut_mid_chunk_is_a_dropped_stream(self, flags, cli):
        with StubOutpost(view=view(), log_steps=["one\n", Cut()], log_chunked=True) as stub:
            rc, out, err = self._watch(cli, stub, *flags)
        assert rc == 2
        assert out == "one\n"
        assert "was dropped" in err

    def test_a_quiet_running_job_is_followed_without_repeating_output(self, cli, monkeypatch):
        monkeypatch.setattr(outpost_http, "IDLE_TIMEOUT_SECONDS", 0.3)
        running = view(state="in_progress", result=None, exit_code=None)
        with StubOutpost(
            log_connections=[["one\n", "tw", threading.Event()], ["one\n", "two\n", "three\n"]],
            view_sequence=[running, view(result="failure", exit_code=1)],
        ) as stub:
            rc, out, err = self._watch(cli, stub, "--exit-status")
            follows = [q for q in stub.requests if q.path.endswith("/log?follow=1")]
        assert out == "one\ntwo\nthree\n"
        assert rc == 1, err
        assert len(follows) == 2

    def test_a_quiet_stream_whose_view_cannot_be_read_exits_2(self, cli, monkeypatch):
        monkeypatch.setattr(outpost_http, "IDLE_TIMEOUT_SECONDS", 0.3)
        refused = (500, {"reason": "boom", "message": "no"})
        with StubOutpost(view_reply=refused, log_steps=["one\n", threading.Event()]) as stub:
            rc, out, _ = self._watch(cli, stub, "--exit-status")
        assert (rc, out) == (2, "one\n")

    def test_a_quiet_stream_of_a_completed_run_is_not_reopened(self, cli, monkeypatch):
        monkeypatch.setattr(outpost_http, "IDLE_TIMEOUT_SECONDS", 0.3)
        with StubOutpost(
            view=view(result="failure", exit_code=1), log_connections=[["one\n", threading.Event()], ["one\n"]]
        ) as stub:
            rc, out, _ = self._watch(cli, stub, "--exit-status")
            follows = [q for q in stub.requests if "/log" in q.path]
        assert (rc, out) == (1, "one\n")
        assert len(follows) == 1

    def test_a_non_numeric_id_is_a_usage_error(self, cli):
        with StubOutpost() as stub, pytest.raises(SystemExit) as stop:
            cli(stub.port, "run", "watch", "abc")
        assert stop.value.code == 2


class TestRunView:
    def test_a_cancelled_run_is_shown(self, cli):
        with StubOutpost(view=view(result="cancelled", exit_code=None)) as stub:
            rc, out, _ = cli(stub.port, "run", "view", "7")
        assert rc == 0
        assert "result:   cancelled" in out.splitlines()

    def test_prints_state_result_reason_job_workflow_and_matrix(self, cli):
        v = view(result="failure", reason="no runner", matrix={"os": "linux", "node": "20"}, job="test")
        with StubOutpost(view=v) as stub:
            rc, out, _ = cli(stub.port, "run", "view", "7")
        assert rc == 0
        assert out.splitlines() == [
            "id:       7",
            "state:    completed",
            "result:   failure",
            "reason:   no runner",
            "job:      test",
            f"workflow: {WORKFLOW}",
            "matrix:   os=linux, node=20",
        ]

    def test_empty_fields_print_a_dash(self, cli):
        with StubOutpost(view=view(state="queued", result=None)) as stub:
            _, out, _ = cli(stub.port, "run", "view", "7")
        assert "result:   -" in out and "reason:   -" in out and "matrix:   -" in out

    def test_an_unknown_run_prints_no_run_in_this_workspace(self, cli):
        with StubOutpost(view_reply=NOT_FOUND) as stub:
            rc, out, err = cli(stub.port, "run", "view", "99")
        assert (rc, out) == (2, "")
        assert err == "outpost-gateway: no run 99 in this workspace\n"

    def test_a_refusal_is_printed_with_its_reason_and_message(self, cli):
        reply = (403, {"reason": "scope_missing", "message": "No scope."})
        with StubOutpost(view_reply=reply) as stub:
            rc, _, err = cli(stub.port, "run", "view", "7")
        assert (rc, err) == (2, "outpost-gateway: scope_missing: No scope.\n")

    def test_a_state_outside_the_known_values_is_refused(self, cli):
        with StubOutpost(view=view(state="finished", result=None)) as stub:
            rc, out, err = cli(stub.port, "run", "view", "7")
        assert (rc, out) == (2, "")
        assert "cannot read" in err


class TestRunWatchCancelled:
    CANCELLED = "outpost-gateway: run 7 was cancelled\n"

    def _watch(self, cli, stub, *flags):
        return cli(stub.port, "run", "watch", "7", *flags)

    def test_exit_status_exits_1_and_says_the_run_was_cancelled(self, cli):
        with StubOutpost(view=view(result="cancelled", exit_code=None), log_steps=["one\n"]) as stub:
            rc, out, err = self._watch(cli, stub, "--exit-status")
        assert (rc, out, err) == (1, "one\n", self.CANCELLED)

    def test_without_the_flag_it_says_so_and_exits_0(self, cli):
        with StubOutpost(view=view(result="cancelled", exit_code=None), log_steps=["one\n"]) as stub:
            rc, out, err = self._watch(cli, stub)
        assert (rc, out, err) == (0, "one\n", self.CANCELLED)

    @pytest.mark.parametrize("result", ["success", "failure"])
    def test_no_cancelled_line_for_a_run_that_was_not_cancelled(self, result, cli):
        with StubOutpost(view=view(result=result), log_steps=[]) as stub:
            _, _, err = self._watch(cli, stub)
        assert "cancelled" not in err

    def test_help_names_the_exit_code_a_cancelled_run_gets(self, cli, capsys):
        with StubOutpost(view=view(result="cancelled"), log_steps=[]) as stub:
            rc, _, _ = self._watch(cli, stub, "--exit-status")
        with pytest.raises(SystemExit):
            command.main(["--help"])
        watch_line = next(ln for ln in capsys.readouterr().out.splitlines() if "run watch" in ln)
        named = re.search(r"(\d) failed or cancelled", watch_line)
        assert named and int(named.group(1)) == rc


class TestRunViewLog:
    def _log(self, cli, stub):
        return cli(stub.port, "run", "view", "7", "--log")

    def test_prints_exactly_the_bytes_the_log_route_returned_without_following(self, cli):
        log = "caf\u00e9 one\r\n\ttwo, no trailing newline"
        with StubOutpost(view=view(), log_steps=[log]) as stub:
            rc, out, err = self._log(cli, stub)
            asked = [q for q in stub.requests if "/log" in q.path]
        assert (rc, out, err) == (0, log, "")
        assert [q.path for q in asked] == ["/gateway/runs/7/log"]
        assert asked[0].headers["authorization"] == f"Bearer {TOKEN}"

    def test_a_running_run_s_log_so_far_is_printed_and_it_exits(self, cli):
        running = view(state="in_progress", result=None, exit_code=None)
        with StubOutpost(view=running, log_steps=["so far\n"]) as stub:
            rc, out, _ = self._log(cli, stub)
        assert (rc, out) == (0, "so far\n")

    def test_a_queued_run_has_no_log_yet_and_no_log_is_requested(self, cli):
        with StubOutpost(view=view(state="queued", result=None, exit_code=None), log_steps=["x\n"]) as stub:
            rc, out, err = self._log(cli, stub)
            asked = [q for q in stub.requests if "/log" in q.path]
        assert (rc, out) == (0, "")
        assert err == "outpost-gateway: run 7 has not started, so it has no log yet\n"
        assert asked == []

    def test_a_log_that_is_not_a_regular_file_is_a_refusal(self, cli):
        reply = (500, {"reason": "log_not_a_regular_file",
                       "message": "The run's log is not a regular file, so Outpost will not read it."})
        with StubOutpost(view=view(), log_reply=reply) as stub:
            rc, out, err = self._log(cli, stub)
        assert (rc, out) == (2, "")
        assert err == (
            "outpost-gateway: log_not_a_regular_file: The run's log is not a regular file, "
            "so Outpost will not read it.\n"
        )

    def test_a_connection_cut_mid_chunk_is_a_dropped_log(self, cli):
        with StubOutpost(view=view(), log_steps=["one\n", Cut()], log_chunked=True) as stub:
            rc, _, err = self._log(cli, stub)
        assert rc == 2
        assert "was dropped" in err

    def test_an_unknown_run_prints_no_run_in_this_workspace(self, cli):
        with StubOutpost(view_reply=NOT_FOUND) as stub:
            rc, out, err = self._log(cli, stub)
        assert (rc, out) == (2, "")
        assert err == "outpost-gateway: no run 7 in this workspace\n"

    def test_a_run_cancelled_before_it_started_says_so_instead_of_printing_nothing(self, cli):
        run = view(result="cancelled", exit_code=None, started_at=None)
        with StubOutpost(view=run, log_steps=[]) as stub:
            rc, out, err = self._log(cli, stub)
        assert (rc, out) == (0, "")
        assert err == "outpost-gateway: run 7 never started, so it has no log\n"

    def test_a_run_cancelled_while_running_still_prints_its_log(self, cli):
        with StubOutpost(view=view(result="cancelled", exit_code=None), log_steps=["so far\n"]) as stub:
            rc, out, err = self._log(cli, stub)
        assert (rc, out, err) == (0, "so far\n", "")

    def test_without_the_flag_view_still_prints_the_fields_and_asks_for_no_log(self, cli):
        with StubOutpost(view=view(), log_steps=["x\n"]) as stub:
            rc, out, _ = cli(stub.port, "run", "view", "7")
            asked = [q for q in stub.requests if "/log" in q.path]
        assert rc == 0 and out.startswith("id:       7\n")
        assert asked == []


class TestRunCancel:
    def _cancel(self, cli, stub):
        return cli(stub.port, "run", "cancel", "7")

    def test_a_queued_run_completes_cancelled_at_once(self, cli):
        with StubOutpost() as stub:
            rc, out, err = self._cancel(cli, stub)
            sent = next(q for q in stub.requests if q.path.endswith("/cancel"))
        assert rc == 0, err
        assert (sent.method, sent.path) == ("POST", "/gateway/runs/7/cancel")
        assert sent.headers["authorization"] == f"Bearer {TOKEN}"
        assert out.splitlines() == [
            "id:       7",
            "state:    completed",
            "result:   cancelled",
            "cancel requested; run 7 will complete as cancelled",
        ]

    def test_a_cancel_the_route_answers_late_is_still_a_success(self, cli):
        with StubOutpost(cancel_delay=3.5) as stub:
            rc, out, err = self._cancel(cli, stub)
        assert rc == 0, err
        assert "cancel requested; run 7 will complete as cancelled" in out

    def test_a_running_run_is_still_in_progress_with_no_result_yet(self, cli):
        with StubOutpost(cancel_reply=(202, {"id": 7, "state": "in_progress", "result": None})) as stub:
            rc, out, _ = self._cancel(cli, stub)
        assert rc == 0
        assert out.splitlines()[1:3] == ["state:    in_progress", "result:   -"]
        assert "will complete as cancelled" in out

    @pytest.mark.parametrize(
        "reply, shown",
        [
            (
                (409, {"reason": "run_completed", "result": "success",
                       "message": "Run 7 has already completed with the result success, so it cannot be cancelled."}),
                "run_completed: Run 7 has already completed with the result success, so it cannot be cancelled.",
            ),
            (
                (409, {"reason": "run_completed", "result": "cancelled",
                       "message": "Run 7 has already completed with the result cancelled, so it cannot be cancelled."}),
                "run_completed: Run 7 has already completed with the result cancelled, so it cannot be cancelled.",
            ),
            (
                (409, {"reason": "run_not_active",
                       "message": "Run 7 is not being run by this Outpost, so it cannot be cancelled."}),
                "run_not_active: Run 7 is not being run by this Outpost, so it cannot be cancelled.",
            ),
            (NOT_FOUND, "run_not_found: No run with that id."),
            ((501, {"reason": "runs_not_configured", "message": "runs are not configured on this host"}),
             "runs_not_configured: runs are not configured on this host"),
            ((401, {"reason": "credential_unrecognised", "message": "The bearer token is not recognised."}),
             "credential_unrecognised: The bearer token is not recognised."),
            ((403, {"reason": "scope_missing", "message": "No scope."}), "scope_missing: No scope."),
        ],
        ids=["completed-success", "completed-cancelled", "not-active", "not-found", "not-configured", "401", "403"],
    )
    def test_a_refusal_prints_outposts_reason_and_message_and_exits_2(self, reply, shown, cli):
        with StubOutpost(cancel_reply=reply) as stub:
            rc, out, err = self._cancel(cli, stub)
        assert (rc, out) == (2, "")
        assert err == f"outpost-gateway: {shown}\n"

    @pytest.mark.parametrize(
        "body",
        [
            {"id": 7, "state": "completed", "result": "aborted"},
            {"id": 7, "state": "paused", "result": None},
            {"id": 7, "state": "completed", "result": None},
            {"id": 7, "state": "in_progress", "result": "cancelled"},
        ],
        ids=["unknown-result", "unknown-state", "completed-without-result", "result-before-completion"],
    )
    def test_a_202_outside_the_known_values_is_refused_as_unreadable(self, body, cli):
        with StubOutpost(cancel_reply=(202, body)) as stub:
            rc, out, err = self._cancel(cli, stub)
        assert (rc, out) == (2, "")
        assert "cannot read" in err

    def test_an_unreachable_outpost_exits_2(self, cli):
        rc, _, err = cli(free_port(), "run", "cancel", "7")
        assert rc == 2
        assert "not reachable" in err

    def test_a_non_numeric_id_is_a_usage_error(self, cli):
        with StubOutpost() as stub, pytest.raises(SystemExit) as stop:
            cli(stub.port, "run", "cancel", "abc")
        assert stop.value.code == 2


class TestRunListRefusals:
    def test_a_state_outside_the_known_values_is_refused_not_listed(self, cli):
        bad = [{**RUNS[1], "state": "finished"}]
        with StubOutpost(runs=bad) as stub:
            rc, out, err = cli(stub.port, "run", "list")
        assert (rc, out) == (2, "")
        assert "cannot read" in err

    def test_a_cancelled_run_is_listed_with_the_others(self, cli):
        runs = [*RUNS, {**RUNS[1], "id": 3, "job": "docs", "state": "completed", "result": "cancelled"}]
        with StubOutpost(runs=runs) as stub:
            rc, out, err = cli(stub.port, "run", "list")
        assert rc == 0, err
        rows = [ln.split() for ln in out.splitlines()[1:]]
        assert [r[0] for r in rows] == ["1", "2", "3"]
        assert rows[2][:4] == ["3", "docs", "completed", "cancelled"]

    def test_a_result_outside_the_known_values_is_refused_not_listed(self, cli):
        bad = [{**RUNS[0], "result": "aborted"}]
        with StubOutpost(runs=bad) as stub:
            rc, out, err = cli(stub.port, "run", "list")
        assert (rc, out) == (2, "")
        assert "cannot read" in err

    def test_every_refusal_reason_is_printed_and_exits_2(self, cli):
        reply = (501, {"reason": "runs_not_configured", "message": "runs are not configured on this host"})
        with StubOutpost(runs_reply=reply) as stub:
            rc, _, err = cli(stub.port, "run", "list")
        assert (rc, err) == (2, "outpost-gateway: runs_not_configured: runs are not configured on this host\n")


class TestHygieneAcrossVerbs:
    @pytest.mark.parametrize(
        "argv, stub_kwargs",
        [
            (("run", "start", "build"), dict()),
            (("run", "start", "build"), dict(start_reply=(422, {"reason": "upload_refused", "message": "no"}))),
            (("run", "watch", "7", "--exit-status"), dict(view=view(), log_steps=["x\n"])),
            (("run", "watch", "7", "--exit-status"), dict(view=view(result="failure", reason="r"), log_steps=[])),
            (("run", "view", "7"), dict(view=view())),
            (("run", "view", "7"), dict(view_reply=(401, {"reason": "credential_revoked", "message": "Revoked."}))),
            (("run", "list"), dict(runs=RUNS)),
            (("run", "cancel", "7"), dict()),
            (("run", "cancel", "7"), dict(cancel_reply=(401, {"reason": "credential_revoked", "message": "Revoked."}))),
            (("run", "watch", "7", "--exit-status"), dict(view=view(result="cancelled"), log_steps=["x\n"])),
            (("run", "view", "7", "--log"), dict(view=view(), log_steps=["x\n"])),
            (("run", "view", "7", "--log"), dict(view=view(state="queued", result=None))),
            (
                ("run", "view", "7", "--log"),
                dict(view=view(), log_reply=(500, {"reason": "log_not_a_regular_file", "message": "no"})),
            ),
        ],
    )
    def test_secret_and_token_appear_in_no_output_or_environment(self, argv, stub_kwargs, cli, repo):
        with StubOutpost(**stub_kwargs) as stub:
            _, out, err = cli(stub.port, *argv, cwd=repo)
        for secret in (CLIENT_SECRET, TOKEN):
            assert secret not in out and secret not in err
            assert all(secret not in k and secret not in v for k, v in os.environ.items())

    def test_the_exit_code_reaches_the_process_through_the_real_entry_point(self, launcher, tmp_path, state):
        with StubOutpost(view=view(result="failure", exit_code=1), log_steps=["boom\n"]) as stub:
            r = _run(launcher, tmp_path, state, stub.port, ("run", "watch", "7", "--exit-status"))
        assert r.proc.returncode == 1
        assert r.proc.stdout == "boom\n"
