"""Opt-in end-to-end test of the gateway slice, run on andromeda.

    TRAILHEAD_GATEWAY_E2E=1 .venv/bin/python -m pytest -n 0 trailhead/tests/test_gateway_e2e_andromeda.py

It starts a real Outpost on a free port with a temp state directory and a temp camp layout of two
workspaces, and drives the real registration step and gateway command. Two kinds of harness:

* fake harnesses (a ``claude``-named shell) show registration scoped by the harness's directory;
* a real ``claude -p`` session, its Bash sandboxed by settings rendered from the dotfiles' real
  template, shows what the sandboxed shell can and cannot do. It runs the plugin's real SessionStart
  hook, then scripted commands whose output is read back from the session's stream.

The sandboxed session needs the real Claude Code sandbox, so it cannot run inside another sandbox: run
the whole file from a host terminal (the operator's ``!``). Each sandboxed assertion is also run against
a deliberately broken setup (no hook, no ``denyRead``, wrong workspace) and must fail there.
"""

from __future__ import annotations

import os
import stat
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ._gateway_e2e_support import (
    CHECKS,
    GROUP,
    W1,
    W2,
    Facts,
    check_credential_file_read_is_refused,
    check_hook_registered_the_session_for_w1,
    check_run_list_prints_the_empty_list,
    list_cmd,
    make_camp,
    real_outpost,
    register_cmd,
    render_sandbox_settings,
    run_claude_session,
    run_fake_harness,
    sandbox_steps,
    step_env,
    dotfiles_checkout,
    install_shims,
)

GATED = os.environ.get("TRAILHEAD_GATEWAY_E2E") == "1"
pytestmark = pytest.mark.skipif(not GATED, reason="opt-in: set TRAILHEAD_GATEWAY_E2E=1 (see module docstring)")

NO_RUNS_W1 = f"No runs in workspace {GROUP}/{W1}"


# --- fake harnesses (no sandbox needed) --------------------------------------------------------------


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("gw-e2e")
    camp = make_camp(root)
    with real_outpost(root, camp) as outpost:
        yield root, camp, outpost


@pytest.fixture(scope="module")
def fake_runs(world):
    root, camp, outpost = world
    env = step_env(root, outpost)
    port = outpost.port
    h1 = run_fake_harness(
        root,
        "h1",
        camp.w1,
        [
            ("register", register_cmd(port)),
            ("list", list_cmd(port, root)),
            ("register_from_w2", f'cd "{camp.w2}" && {register_cmd(port)}'),
            ("list_after_w2", list_cmd(port, root)),
        ],
        env,
    )
    h2 = run_fake_harness(root, "h2", camp.elsewhere, [("register", register_cmd(port))], env)
    return h1, h2


def test_a_harness_started_in_w1_registers_and_gets_a_credential_scoped_to_w1(world, fake_runs):  # AC13
    _, _, outpost = world
    h1, _ = fake_runs
    assert h1["register"].splitlines()[-1] == "rc=0", h1["register"]
    files = outpost.credential_files()
    assert [f.parent.name for f in files] == [h1["register"].split()[1]]
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert outpost.registry_workspaces() == [(files[0].parent.name, f"{GROUP}/{W1}")]


def test_run_list_under_a_registered_harness_prints_the_empty_list_for_its_workspace(fake_runs):  # AC23, AC40
    h1, _ = fake_runs
    assert h1["list"] == f"{NO_RUNS_W1}\nrc=0"


def test_the_same_harness_in_w2_registers_again_and_still_holds_only_w1(world, fake_runs):  # AC16, AC17
    _, _, outpost = world
    h1, _ = fake_runs
    assert h1["register_from_w2"].splitlines()[-1] == "rc=0", h1["register_from_w2"]
    assert h1["list_after_w2"] == f"{NO_RUNS_W1}\nrc=0"
    assert [ws for _, ws in outpost.registry_workspaces()] == [f"{GROUP}/{W1}"]
    assert len(outpost.credential_files()) == 1


def test_a_harness_started_outside_any_workspace_gets_no_credential(world, fake_runs):  # AC15
    _, _, outpost = world
    _, h2 = fake_runs
    assert h2["register"] == "not_in_workspace\nrc=1"
    assert len(outpost.credential_files()) == 1  # only h1's
    assert [ws for _, ws in outpost.registry_workspaces()] == [f"{GROUP}/{W1}"]


# --- a real sandboxed session ------------------------------------------------------------------------


class _Quiet(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def bystander():
    """A live HTTP listener on another loopback port, so a refusal there cannot be 'nothing listening'."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _session(tmp_path_factory, bystander, label, *, cwd="w1", deny_read=True, with_hook=True):
    """One real sandboxed session against its own fresh Outpost: ``(Facts, Session)``."""
    root = tmp_path_factory.mktemp(label)
    camp = make_camp(root)
    with real_outpost(root, camp) as outpost:
        shim_bin, _ = install_shims(root, outpost)
        env = step_env(root, outpost)
        settings = render_sandbox_settings(dotfiles_checkout(), outpost.state, outpost.port, deny_read=deny_read)
        steps = sandbox_steps(outpost.state, outpost.port, bystander)
        session = run_claude_session(
            root,
            label,
            cwd={"w1": camp.w1, "w2": camp.w2}[cwd],
            settings=settings,
            steps=steps,
            env=env,
            shim_bin=shim_bin,
            with_hook=with_hook,
        )
        files = outpost.credential_files()
        facts = Facts([(f.parent.name, stat.S_IMODE(f.stat().st_mode)) for f in files], outpost.registry_workspaces())
    return facts, session


@pytest.fixture(scope="module")
def good_session(tmp_path_factory, bystander):
    return _session(tmp_path_factory, bystander, "good")


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_the_sandboxed_session_satisfies(check, good_session):
    check(*good_session)


def test_the_bystander_listener_answers_outside_the_sandbox(bystander):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    assert opener.open(f"http://127.0.0.1:{bystander}/", timeout=3).read() == b"ok"


def _ran(session, step):
    """The step's output; a session that did not start, or a step the model did not run, fails the test."""
    assert step in session.outputs, f"step {step!r} was not run as written (session exit {session.exit_code})"
    return session.outputs[step]


def test_without_denyread_the_credential_read_succeeds_and_the_refusal_check_fails(tmp_path_factory, bystander):
    facts, session = _session(tmp_path_factory, bystander, "broken-no-deny-read", deny_read=False)
    out = _ran(session, "credential_read")
    assert not out.startswith("Exit code"), f"the read did not succeed, so denyRead is not what was removed: {out!r}"
    assert facts.credentials, "no credential file exists, so nothing was there to read"
    with pytest.raises(AssertionError, match="was not refused"):
        check_credential_file_read_is_refused(facts, session)


def test_without_the_hook_nothing_registers_and_the_list_and_registration_checks_fail(tmp_path_factory, bystander):
    facts, session = _session(tmp_path_factory, bystander, "broken-no-hook", with_hook=False)
    _ran(session, "run_list")
    assert facts.credentials == [] and facts.registry == [], f"something registered without the hook: {facts}"
    with pytest.raises(AssertionError, match="no credential file"):
        check_hook_registered_the_session_for_w1(facts, session)
    with pytest.raises(AssertionError, match="no gateway credential"):
        check_run_list_prints_the_empty_list(facts, session)


def test_a_session_started_in_w2_registers_w2_and_the_w1_checks_fail(tmp_path_factory, bystander):
    facts, session = _session(tmp_path_factory, bystander, "broken-wrong-workspace", cwd="w2")
    _ran(session, "run_list")
    assert [ws for _, ws in facts.registry] == [f"{GROUP}/{W2}"], f"the hook did not register W2: {facts}"
    with pytest.raises(AssertionError, match="ws-two"):
        check_hook_registered_the_session_for_w1(facts, session)
    with pytest.raises(AssertionError, match="ws-two"):
        check_run_list_prints_the_empty_list(facts, session)
