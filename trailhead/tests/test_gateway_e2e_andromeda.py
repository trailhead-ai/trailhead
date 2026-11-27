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

The denial probes (``test_a_hand_started_session_*``, ``test_an_outpost_launched_session_*``, run by
``run-e2e-sandbox-denials.sh``) put the operator and registration credentials, another session's credential and
the database folder (with a ``-wal`` made after the probing command started) in front of a real sandboxed session,
by shell and by the Read tool, under the dotfiles' rendered settings and under the settings the real launch profile
renders, and also run a PATH-shadowed ``outpost-gateway`` and a page request that used to be handed the UI cookie.
The pinned ``outpost-gateway`` those sessions run is the one ``trailhead install`` writes, not a wrapper.

The run scenario (``test_run_scenario_*``) also needs the run account andromeda's dotfiles provision: an Outpost
with the operator's ``[runs]`` table, the ``outpost-run`` group and the run engine. A preflight fails the
whole scenario at once, saying what to do, when the host is not ready; among the things it checks is that the
operator's own Outpost is not serving the engine filter, which only one Outpost can bind, so stop it for the run.
``TRAILHEAD_GATEWAY_E2E_OUTPOST`` and ``TRAILHEAD_GATEWAY_E2E_TRAILHEAD`` name the checkouts whose Outpost and
gateway command the scenario runs (``scripts/run-e2e-red-then-green.sh`` points them at the commits before the run
slice, where every run assertion must fail). The cancel scenario (``test_cancel_scenario_*``) is run by
``scripts/run-e2e-cancel-red-then-green.sh``, which needs the act wrapper provisioned with ``setpriv --pdeathsig KILL``.
What a session reaches, and what this suite leaves untested, is
listed in the report on the task, not here.
"""

from __future__ import annotations

import os
import json
import re
import secrets
import shlex
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ._gateway_e2e_support import (
    CHECKS,
    _out,
    ArgumentSampler,
    TokenRecorder,
    known_secrets,
    outpost_request_records,
    secrets_in_text,
    stored_run_logs,
    READ_CONTROL,
    READ_CONTROL_TEXT,
    LateFileActor,
    check_a_shadowed_gateway_cannot_read_the_credential,
    check_no_cookie_is_handed_to_a_page_request,
    check_the_auth_header_helper_prints_no_token,
    check_the_launched_session_reaches_its_own_workspace_runs,
    check_read_is_refused,
    check_the_late_side_file_is_refused,
    check_the_read_control_is_readable,
    check_the_read_tool_is_denied,
    denial_facts,
    denial_steps,
    launched_session_settings,
    meeting_folder,
    read_tool_paths,
    write_late_file_probe,
    write_path_shadow,
    RUN_SESSION_TIMEOUT_SECONDS,
    SESSION_JOBS,
    HomeSentinel,
    EngineUnavailable,
    act_container_names,
    act_containers,
    remove_act_containers,
    runner_act_processes,
    prepull_image,
    probe_problems,
    build_fixture_repo,
    check_container_inspect,
    exit_code,
    parse_stamped_lines,
    require_run_host,
    run_steps,
    start_fake_harness,
    stream_script,
    streamed_as_produced,
    GROUP,
    W1,
    W2,
    Facts,
    check_nothing_is_registered_before_the_first_gateway_call,
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
            ("register", register_cmd()),
            ("list", list_cmd(root)),
            ("register_from_w2", f'cd "{camp.w2}" && {register_cmd()}'),
            ("list_after_w2", list_cmd(root)),
        ],
        env,
    )
    h2 = run_fake_harness(root, "h2", camp.elsewhere, [("register", register_cmd())], env)
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


def _facts(outpost):
    files = outpost.credential_files()
    return Facts([(f.parent.name, stat.S_IMODE(f.stat().st_mode)) for f in files], outpost.registry_workspaces())


def _session(tmp_path_factory, bystander, label, *, cwd="w1", deny_read=True, with_hook=True, before_facts=None):
    """One real sandboxed session against its own fresh Outpost: ``(Facts, Session)``.

    When *before_facts* is a list, the Outpost's state just before the session starts is appended to it.
    """
    root = tmp_path_factory.mktemp(label)
    camp = make_camp(root)
    with real_outpost(root, camp) as outpost:
        shim_bin = install_shims(root, outpost)
        env = step_env(root, outpost)
        settings = render_sandbox_settings(
            dotfiles_checkout(), outpost.state, outpost.port, deny_read=deny_read, gateway=shim_bin / "outpost-gateway"
        )
        steps = sandbox_steps(outpost.state, outpost.port, bystander, shim_bin / "outpost-gateway")
        session = run_claude_session(
            root,
            label,
            cwd={"w1": camp.w1, "w2": camp.w2}[cwd],
            settings=settings,
            steps=steps,
            env=env,
            shim_bin=shim_bin,
            with_hook=with_hook,
            before=(lambda: before_facts.append(_facts(outpost))) if before_facts is not None else None,
        )
        facts = _facts(outpost)
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


def test_without_the_hook_the_first_gateway_call_registers_w1_and_lists_an_empty_run_list(tmp_path_factory, bystander):
    before = []
    facts, session = _session(tmp_path_factory, bystander, "no-hook", with_hook=False, before_facts=before)
    [before_the_session] = before
    check_nothing_is_registered_before_the_first_gateway_call(before_the_session)
    _ran(session, "run_list")
    check_hook_registered_the_session_for_w1(facts, session)
    check_run_list_prints_the_empty_list(facts, session)


def test_a_session_started_in_w2_registers_w2_and_the_w1_checks_fail(tmp_path_factory, bystander):
    facts, session = _session(tmp_path_factory, bystander, "broken-wrong-workspace", cwd="w2")
    _ran(session, "run_list")
    assert [ws for _, ws in facts.registry] == [f"{GROUP}/{W2}"], f"the hook did not register W2: {facts}"
    with pytest.raises(AssertionError, match="ws-two"):
        check_hook_registered_the_session_for_w1(facts, session)
    with pytest.raises(AssertionError, match="ws-two"):
        check_run_list_prints_the_empty_list(facts, session)


# --- what a sandboxed session must not read, run or be handed (AC82, AC83, AC84) ---------------------------


def _denial_session(tmp_path_factory, label, *, kind, deny_read=True):
    """One real sandboxed session probing the denials, against its own Outpost: ``(DenialFacts, Session, read paths)``.

    *kind* is whose settings the session runs under: ``hand`` is a hand-started session, under the dotfiles' real
    template; ``launched`` is one Outpost launched, under the settings the real launch profile renders.
    Another workspace's session is registered first, so there is a credential that is not this session's to read.
    """
    root = tmp_path_factory.mktemp(label)
    camp = make_camp(root)
    with real_outpost(root, camp) as outpost:
        env = step_env(root, outpost)
        run_fake_harness(root, "other-session", camp.w2, [("register", register_cmd())], env)
        [other] = outpost.credential_files()
        other_key = other.parent.name
        shim_bin = install_shims(root, outpost)
        gateway = shim_bin / "outpost-gateway"
        write_path_shadow(root / "shadow", outpost.state)
        rendezvous = meeting_folder(camp.w1)
        write_late_file_probe(rendezvous)
        actor = LateFileActor(outpost.state / "db", rendezvous)
        (camp.w1 / ".claude").mkdir(exist_ok=True)
        (camp.w1 / READ_CONTROL).write_text(READ_CONTROL_TEXT)
        if kind == "hand":
            settings = render_sandbox_settings(
                dotfiles_checkout(), outpost.state, outpost.port, deny_read=deny_read, gateway=gateway
            )
        else:
            settings = launched_session_settings(root, outpost.state, camp.w1)
            settings["sandbox"]["autoAllowBashIfSandboxed"] = True
            settings["sandbox"].setdefault("network", {}).setdefault("allowedDomains", []).append(
                f"127.0.0.1:{outpost.port}"
            )
        steps = denial_steps(outpost.state, other_key, rendezvous, outpost.port)
        if kind == "hand":
            steps["eval_start"] = _eval_start_step(camp.w1, shim_bin / "portage-eval", outpost.state)
        if kind == "launched":
            steps["gateway_run_list"] = f"{gateway} run list"
            steps["claude_folder_write_at_launch"] = f"echo '{{}}' > '{camp.w1}/.claude/settings.json' && echo wrote"
            steps["claude_folder_write_after_launch"] = (
                f"mkdir -p '{camp.w1}/later/.claude' && echo '{{}}' > '{camp.w1}/later/.claude/settings.json' && echo wrote"
            )
        paths = read_tool_paths(outpost.state, other_key, camp.w1)
        session = run_claude_session(
            root,
            label,
            cwd=camp.w1,
            settings=settings,
            steps=steps,
            env=env,
            shim_bin=shim_bin,
            with_hook=kind == "hand",
            read_paths=paths,
            extra_path=root / "shadow",
            during=actor,
        )
        facts = denial_facts(outpost.state, other_key, actor)
    shutil.rmtree(rendezvous, ignore_errors=True)
    return facts, session, paths


def _eval_start_step(workspace: Path, eval_shim: Path, state: Path) -> str:
    """A scaffold that reads the operator credential and records that it ran, then `portage-eval start` aimed at its
    plugin; the step prints the start's exit status and whether the scaffold ran."""
    plugin = workspace / "eval-plugin"
    (plugin / "evals").mkdir(parents=True, exist_ok=True)
    scaffold = plugin / "scaffold.sh"
    scaffold.write_text(f'#!/bin/sh\ncat "{state}/operator/credential" > /dev/null && touch "{workspace}/eval-scaffold-ran"\n')
    scaffold.chmod(0o755)
    return (
        f'cd "{workspace}" && {shlex.quote(str(eval_shim))} start --require-version 2 --plugin eval-plugin --case c; '
        f'echo "start_exit=$?"; sleep 2; if [ -e "{workspace}/eval-scaffold-ran" ]; then echo scaffold_ran=yes; '
        "else echo scaffold_ran=no; fi"
    )


BASH_DENIAL_CHECKS = [
    check_read_is_refused("operator_read"),
    check_read_is_refused("registration_read"),
    check_read_is_refused("other_session_read"),
    check_read_is_refused("db_read"),
    check_the_late_side_file_is_refused,
    check_a_shadowed_gateway_cannot_read_the_credential,
    check_no_cookie_is_handed_to_a_page_request,
    check_the_auth_header_helper_prints_no_token,
]
READ_TOOL_DENIAL_CHECKS = [
    check_the_read_tool_is_denied(name)
    for name in ("operator_credential", "registration_authority", "other_session_credential", "database")
]


@pytest.fixture(scope="module")
def hand_started_session(tmp_path_factory):
    return _denial_session(tmp_path_factory, "denial-hand", kind="hand")


@pytest.fixture(scope="module")
def launched_session(tmp_path_factory):
    return _denial_session(tmp_path_factory, "denial-launched", kind="launched")


@pytest.mark.parametrize("check", BASH_DENIAL_CHECKS, ids=lambda c: c.__name__)
def test_a_hand_started_session_cannot_read_or_run_what_it_must_not_by_shell(check, hand_started_session):  # AC82, AC84
    facts, session, _ = hand_started_session
    check(facts, session)


def test_a_hand_started_session_running_eval_is_stopped_inside_the_sandbox_and_no_scaffold_reads_the_credential(
    hand_started_session,
):  # AC82
    facts, session, _ = hand_started_session
    assert facts.exists["operator_read"], "the operator credential does not exist, so a refusal proves nothing"
    out = _ran(session, "eval_start")
    assert "could-not-leave-sandbox" in out, f"eval did not stop inside the sandbox: {out!r}"
    assert "start_exit=3" in out, out
    assert "scaffold_ran=no" in out, f"a scaffold ran and read the operator credential: {out!r}"


@pytest.mark.parametrize("check", READ_TOOL_DENIAL_CHECKS, ids=lambda c: c.__name__)
def test_a_hand_started_session_cannot_read_what_it_must_not_with_the_read_tool(check, hand_started_session):  # AC82
    _, session, paths = hand_started_session
    check(session.reads, paths)


def test_the_read_tool_reads_a_file_the_hand_started_settings_leave_alone(hand_started_session):
    _, session, paths = hand_started_session
    check_the_read_control_is_readable(session.reads, paths)


@pytest.mark.parametrize("check", BASH_DENIAL_CHECKS, ids=lambda c: c.__name__)
def test_an_outpost_launched_session_cannot_read_or_run_what_it_must_not_by_shell(check, launched_session):  # AC83
    facts, session, _ = launched_session
    check(facts, session)


def test_an_outpost_launched_session_cannot_write_settings_under_a_claude_folder_that_existed_at_launch(
    launched_session,
):
    _, session, _ = launched_session
    out = _out(session, "claude_folder_write_at_launch")
    assert out.startswith("Exit code") and "wrote" not in out, f"the write was not refused: {out!r}"


@pytest.mark.skipif(
    sys.platform.startswith("linux"),
    reason=(
        "the Linux sandbox expands its write denials once at launch, so a .claude folder made after launch is "
        "writable there; closed by "
        "task/on-linux-launched-sessions-cannot-write-git-or-claude-folders-or-vaults-created-after-launch"
    ),
)
def test_an_outpost_launched_session_cannot_write_settings_under_a_claude_folder_created_after_launch(
    launched_session,
):
    _, session, _ = launched_session
    out = _out(session, "claude_folder_write_after_launch")
    assert out.startswith("Exit code") and "wrote" not in out, f"the write was not refused: {out!r}"


def test_an_outpost_launched_session_runs_the_gateway_shim_as_itself(launched_session):
    facts, session, _ = launched_session
    check_the_launched_session_reaches_its_own_workspace_runs(facts, session)


@pytest.mark.parametrize("check", READ_TOOL_DENIAL_CHECKS, ids=lambda c: c.__name__)
def test_an_outpost_launched_session_cannot_read_what_it_must_not_with_the_read_tool(check, launched_session):  # AC83
    _, session, paths = launched_session
    check(session.reads, paths)


def test_the_read_tool_reads_a_file_the_launched_settings_leave_alone(launched_session):
    _, session, paths = launched_session
    check_the_read_control_is_readable(session.reads, paths)


@pytest.mark.parametrize(
    "check",
    [
        check_read_is_refused("operator_read"),
        check_read_is_refused("db_read"),
        check_the_late_side_file_is_refused,
        check_a_shadowed_gateway_cannot_read_the_credential,
    ],
    ids=lambda c: c.__name__,
)
def test_without_the_denials_the_shell_probes_read_what_they_should_not_and_their_checks_fail(
    check, tmp_path_factory
):
    facts, session, _ = _denial_session(tmp_path_factory, "denial-broken", kind="hand", deny_read=False)
    with pytest.raises(AssertionError):
        check(facts, session)


# --- the dotfiles' rendered settings (need the operator's dotfiles checkout and chezmoi) -------------


def test_the_rendered_read_denials_name_the_absolute_temp_state_in_double_slash_form(tmp_path):
    state = tmp_path / "outpost-state"
    settings = render_sandbox_settings(dotfiles_checkout(), state, 7000)
    deny = settings["permissions"]["deny"]
    assert f"Read(/{state}/sessions/**)" in deny
    assert f"Read(/{state}/registration/**)" in deny
    assert f"Read(/{state}/db/**)" in deny
    assert str(state / "db") in settings["sandbox"]["filesystem"]["denyRead"]
    assert render_sandbox_settings(dotfiles_checkout(), state, 7000, deny_read=False)["permissions"]["deny"] == []


def test_the_rendered_settings_exclude_and_allow_only_the_path_the_session_types(tmp_path):
    wrapper = tmp_path / "shims" / "outpost-gateway"
    settings = render_sandbox_settings(dotfiles_checkout(), tmp_path / "state", 7000, gateway=wrapper)
    command = sandbox_steps(tmp_path, 7000, 7001, wrapper)["run_list"]

    assert command == f"{wrapper} run list"
    assert f"{wrapper} *" in settings["sandbox"]["excludedCommands"]
    assert f"Bash({wrapper}:*)" in settings["permissions"]["allow"]
    assert "outpost-gateway" not in settings["sandbox"]["excludedCommands"]


# --- the run scenario: a sandboxed session runs jobs through the run account -----------------------------


class _Counting(ThreadingHTTPServer):
    """A loopback listener that counts every connection it accepts, whether or not anything is said on it."""

    connections = 0

    def get_request(self):
        accepted = super().get_request()
        self.connections += 1
        return accepted


@dataclass
class RunWorld:
    session: object
    rows_after_session: list
    stream_out: str
    inspected: list
    secrets: list
    w2: dict
    w2_second: dict
    w1_cross: dict
    rows_final: list
    listener_connections: int
    outpost_log: str
    marker: str
    ports: tuple
    leaks: list


def _gw(gateway, *argv):
    return shlex.join([str(gateway), *argv])


def _control_the_listener(listener):
    """Prove the listener counts a connection that says nothing, then reset it.

    A later count of 0 then means something.
    """
    with socket.create_connection(listener.server_address, timeout=5):
        pass
    deadline = time.monotonic() + 5
    while listener.connections < 1 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert listener.connections == 1, "the counting listener did not count a connection made to it"
    listener.connections = 0


@pytest.fixture(scope="module")
def run_world(tmp_path_factory):
    host = require_run_host()
    root = tmp_path_factory.mktemp("gw-runs")
    camp = make_camp(root)
    home = Path(os.environ["HOME"])
    sentinel = HomeSentinel(home / f".outpost-e2e-{secrets.token_hex(4)}", f"SENTINEL-{secrets.token_hex(8)}")
    listener = _Counting(("127.0.0.1", 0), _Quiet)
    threading.Thread(target=listener.serve_forever, daemon=True).start()
    try:
        sentinel.create()
        _control_the_listener(listener)
        with (
            real_outpost(root, camp, runs=host) as outpost,
            TokenRecorder(outpost.port) as recorder,
            ArgumentSampler(interval=0.05) as sampler,
        ):
            ssh = home / ".ssh"
            image = host.platforms["ubuntu-latest"]
            prepull_image(host.engine_socket, image)
            ports = (outpost.port, listener.server_address[1])
            for ws in (camp.w1, camp.w2):
                build_fixture_repo(ws / "app", image=image, ssh=ssh, sentinel=sentinel, ports=ports, operator_home=home)
            shim_bin = install_shims(root, outpost, port=recorder.port)
            gateway = shim_bin / "outpost-gateway"
            env = step_env(root, outpost)
            settings = render_sandbox_settings(dotfiles_checkout(), outpost.state, outpost.port, gateway=gateway)
            session = run_claude_session(
                root,
                "runs",
                cwd=camp.w1 / "app",
                settings=settings,
                steps=run_steps(gateway),
                env={**env, "BASH_DEFAULT_TIMEOUT_MS": "300000", "BASH_MAX_TIMEOUT_MS": "300000"},
                shim_bin=shim_bin,
                with_hook=True,
                timeout=RUN_SESSION_TIMEOUT_SECONDS,
            )
            rows_after_session = outpost.runs()

            proc = start_fake_harness(root, "stream", camp.w1 / "app", stream_script(outpost.port, gateway), env)
            inspected: list = []
            deadline = time.monotonic() + 240
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    proc.kill()
                    break
                if not inspected:
                    inspected = act_containers(host.engine_socket, "-slow-")
                time.sleep(0.5)
            stream_out = proc.stdout.read()

            port = outpost.port
            first_id = rows_after_session[0][0] if rows_after_session else 1
            w2 = run_fake_harness(
                root,
                "w2",
                camp.w2 / "app",
                [
                    ("register", register_cmd()),
                    ("list_before", _gw(gateway, "run", "list")),
                    ("view_w1_run", _gw(gateway, "run", "view", str(first_id))),
                    ("watch_w1_run", _gw(gateway, "run", "watch", str(first_id))),
                    ("view_missing", _gw(gateway, "run", "view", "99999")),
                    ("start_own", _gw(gateway, "run", "start", "pass")),
                ],
                env,
            )
            own_id = (re.search(r"^(\d+)$", w2["start_own"], re.M) or [None, "0"])[1]
            w2_second = run_fake_harness(
                root,
                "w2b",
                camp.w2 / "app",
                [
                    ("register", register_cmd()),
                    ("watch_own", _gw(gateway, "run", "watch", own_id, "--exit-status")),
                    ("list_after", _gw(gateway, "run", "list")),
                ],
                env,
            )
            w1_cross = run_fake_harness(
                root,
                "w1x",
                camp.w1 / "app",
                [
                    ("register", register_cmd()),
                    ("view_w2_run", _gw(gateway, "run", "view", own_id)),
                    ("list", _gw(gateway, "run", "list", "--json")),
                ],
                env,
            )
            minted = []
            for f in outpost.credential_files():
                minted.append(json.loads(f.read_text())["client_secret"])
            minted.append((outpost.state / "registration" / "credential").read_text().strip())
            leaks = _scan_leaks(outpost, recorder, sampler, host)
            yield RunWorld(
                session,
                rows_after_session,
                stream_out,
                inspected,
                minted,
                w2,
                w2_second,
                w1_cross,
                outpost.runs(),
                listener.connections,
                outpost.log.read_text()[-4000:],
                sentinel.marker,
                ports,
                leaks,
            )
    finally:
        sentinel.remove()
        listener.shutdown()
        listener.server_close()


def _started(world, job):
    out = _ran(world.session, f"start_{job}")
    found = re.search(r"^(\d+)$", out, re.M)
    assert found, f"start {job} did not print a run id: {out!r}"
    return int(found.group(1)), out


def _followed(world, job):
    out = _ran(world.session, f"watch_{job}")
    return exit_code(out), out


def test_run_scenario_start_returns_the_run_id_at_once_and_the_job_runs_on(run_world):  # AC36
    for n, job in enumerate(SESSION_JOBS, 1):
        assert _started(run_world, job)[0] == n
    started = re.search(r"@@start rc=(\d+) id=(\d+) t0=([\d.]+) t1=([\d.]+)", run_world.stream_out)
    assert started, f"the harness never started the slow job: {run_world.stream_out[-800:]}"
    rc, _, t0, t1 = int(started[1]), started[2], float(started[3]), float(started[4])
    ended = re.search(r"@@end ([\d.]+)", run_world.stream_out)
    assert rc == 0 and ended, run_world.stream_out[-800:]
    assert t1 - t0 < 10, f"start took {t1 - t0:.1f}s, so it waited for the job"
    assert float(ended[1]) - t1 >= 15, "the slow job was over as soon as start returned"


def test_run_scenario_watch_streams_output_as_it_is_produced(run_world):  # AC38
    stamped = parse_stamped_lines(run_world.stream_out)
    assert streamed_as_produced(stamped, "stream-first-42", "stream-second-42", 10.0) is None, run_world.stream_out[
        -1500:
    ]


def test_run_scenario_exit_status_is_0_for_a_pass_1_for_a_failure_and_3_for_a_run_that_did_not_execute(
    run_world,
):  # AC39
    assert _followed(run_world, "pass")[0] == 0
    assert "pass-job-ran-42" in _followed(run_world, "pass")[1]
    assert _followed(run_world, "fail")[0] == 1
    assert "fail-job-ran-42" in _followed(run_world, "fail")[1]
    assert _followed(run_world, "mount_priv")[0] == 3


def test_run_scenario_the_list_shows_each_runs_state_and_result(run_world):  # AC40, AC43
    listing = json.loads(_ran(run_world.session, "run_list"))
    by_job = {r["job"]: r for r in listing["runs"]}
    assert listing["workspace"] == f"{GROUP}/{W1}"
    assert set(by_job) == set(SESSION_JOBS), "a run exists that the session did not start, or one is missing"
    assert all(r["state"] == "completed" for r in by_job.values())
    assert by_job["pass"]["result"] == "success"
    assert by_job["fail"]["result"] == "failure" and by_job["fail"]["reason"] is None


def test_run_scenario_a_matrix_job_runs_the_first_values_and_reports_them(run_world):  # AC45, AC46
    _, start_out = _started(run_world, "matrix")
    assert "color=red, size=small" in start_out
    assert "3 other matrix combinations not run" in start_out
    out = _followed(run_world, "matrix")[1]
    assert "matrix-ran 42 red-small" in out
    assert "green" not in out and "large" not in out
    assert "color=red, size=small" in _ran(run_world.session, "view_matrix")


def test_run_scenario_the_job_runs_as_a_pull_request_event(run_world):  # AC47
    assert "event=pull_request" in _followed(run_world, "tree")[1]


def test_run_scenario_the_uncommitted_tree_is_what_runs_and_an_ignored_file_is_absent(run_world):  # AC48, AC49, AC51
    out = _followed(run_world, "tree")[1]
    assert "tracked=edited-uncommitted" in out
    assert "untracked=new-file" in out
    assert "ignored=absent" in out
    assert "workflow-edit=applied" in out and "workflow-edit=NOT-applied" not in out


def test_run_scenario_a_symlink_arrives_as_a_link_and_nothing_of_the_operators_home_comes_through_it(
    run_world,
):  # AC50, AC59
    code, out = _followed(run_world, "tree")
    assert code == 0, out[-1500:]
    assert "tracked=edited-uncommitted" in out, "the tree job did not run its probes"
    assert "symlink=link" in out
    assert re.search(r"ssh-via-link: cat: .*(No such file|Permission denied)", out), out[-1500:]
    assert re.search(r"home-via-link: cat: .*(No such file|Permission denied)", out), out[-1500:]
    assert run_world.marker not in out
    # The nested .git file is not in the copy: git never lists it, so the command cannot upload it. What act
    # would do with one is covered by the run account's own proof, not here.
    assert "dotgit=absent" in out


def test_run_scenario_a_local_action_is_read_from_the_copy_and_one_aimed_at_the_operators_home_is_not(
    run_world,
):  # AC59
    code, out = _followed(run_world, "local_action")
    assert "ok-action-ran-42" in out, f"the control local action did not run:\n{out[-1500:]}"
    assert code == 1, f"the job should have run and failed at the sentinel step, exit {code}:\n{out[-1500:]}"
    after_control = out.split("ok-action-ran-42", 1)[1]
    assert re.search(r"(?i)(can't find|not found|no such file|failure|failed|error)", after_control), out[-1500:]
    assert run_world.marker not in out
    assert "local-action-step-42" not in out, "the job went on past the step aimed at the operator's home"


def test_run_scenario_a_run_cannot_reach_the_operators_home_the_engine_or_the_hosts_loopback(run_world):  # AC59, AC63
    out = _followed(run_world, "probe")[1]
    assert probe_problems(out, run_world.ports, run_world.marker) == [], out[-3500:]
    assert run_world.listener_connections == 0, "a run connected to a listener on the host's loopback"


def test_run_scenario_the_job_container_carries_no_bind_mount_no_secret_and_no_privileged_mode(
    run_world,
):  # AC61, AC62, AC64
    names = [c.get("Name") for c in run_world.inspected]
    assert len(run_world.inspected) == 1, (
        f"expected exactly the slow job's container, saw {names}; log:\n{run_world.stream_out[-800:]}"
    )
    assert check_container_inspect(run_world.inspected[0], run_world.secrets) == [], names


def test_run_scenario_no_secret_or_token_reaches_any_arguments_run_log_or_request_record(run_world):  # AC81, AC85, AC86
    assert run_world.leaks == [], run_world.leaks


def test_run_scenario_a_run_reaches_a_public_host(run_world):  # AC65
    assert "public github.com:443: reached" in _followed(run_world, "public")[1]


def test_run_scenario_a_container_asking_for_a_host_mount_and_privileged_mode_is_stopped_and_exits_3(
    run_world,
):  # AC61, AC64
    """act's own container-options policy is the first layer and refuses the job before any container exists;
    the engine filter is the second and would refuse the create. Either stopping it is correct, so the reason
    may name either; the run must end as one that did not execute, exit 3.
    """
    code, out = _followed(run_world, "mount_priv")
    row = {r["job"]: r for r in json.loads(_ran(run_world.session, "run_list"))["runs"]}["mount_priv"]
    act_layer = "run did not execute: act refused the job's container options"
    filter_layer = "run did not execute: the engine filter refused a container: "
    reason = row["reason"] or ""
    assert code == 3 and reason.startswith((act_layer, filter_layer)), (
        f"exit {code}, reason {reason!r}; the run's log:\n{out[-3000:]}"
    )
    assert "mount-priv-job-ran-42" not in out, "the job ran"
    assert re.search(r"(?i)(--privileged|privileged|Binds|host bind mount)", out), (
        f"the log names neither refused option:\n{out[-3000:]}"
    )


def test_run_scenario_an_image_that_cannot_be_pulled_exits_3_with_the_pull_reason(
    run_world,
):  # act's real pull-error text
    code, out = _followed(run_world, "pull_missing")
    row = {r["job"]: r for r in json.loads(_ran(run_world.session, "run_list"))["runs"]}["pull_missing"]
    assert code == 3 and (row["reason"] or "").startswith("run did not execute: an image could not be pulled"), (
        f"exit {code}, reason {row['reason']!r}; act's own log lines, "
        f"which the executor's pull pattern is checked against:\n{out[-3000:]}"
    )


def test_run_scenario_an_unmapped_label_is_refused_at_start_and_nothing_runs(run_world):
    out = _ran(run_world.session, "start_unmapped")
    assert exit_code(out) == 2 and "platform_unmapped" in out and "windows-latest" in out, out
    assert [r[2] for r in run_world.rows_after_session] == SESSION_JOBS, "the refused start left a run behind"


def test_run_scenario_sudo_as_the_run_account_fails_inside_the_sandbox(run_world):
    out = _ran(run_world.session, "sudo_act")
    assert exit_code(out) != 0 and "no new privileges" in out.lower(), out


def test_run_scenario_another_workspaces_session_cannot_see_or_start_the_first_ones_runs(run_world):  # AC70, AC71
    w2 = run_world.w2
    assert w2["list_before"] == f"No runs in workspace {GROUP}/{W2}\nrc=0"
    missing = w2["view_missing"]
    assert missing.splitlines()[-1] == "rc=2" and "no run 99999 in this workspace" in missing
    for step in ("view_w1_run", "watch_w1_run"):
        assert w2[step].splitlines()[-1] == "rc=2" and "in this workspace" in w2[step], w2[step]
    assert re.search(r"^\d+$", w2["start_own"], re.M), w2["start_own"]
    own = {r[2]: r[1] for r in run_world.rows_final if r[1] == f"{GROUP}/{W2}"}
    assert own == {"pass": f"{GROUP}/{W2}"}, run_world.rows_final
    assert [r[1] for r in run_world.rows_final].count(f"{GROUP}/{W1}") == len(SESSION_JOBS) + 1
    second = run_world.w2_second
    assert second["watch_own"].splitlines()[-1] == "rc=0"
    assert "pass" in second["list_after"] and not {"slow", "mount_priv", "fail"} & set(second["list_after"].split())
    cross = run_world.w1_cross
    assert "in this workspace" in cross["view_w2_run"] and cross["view_w2_run"].splitlines()[-1] == "rc=2"
    w1_jobs = [r["job"] for r in json.loads(cross["list"].rsplit("\nrc=", 1)[0])["runs"]]
    assert w1_jobs.count("pass") == 1 and "slow" in w1_jobs


# --- the cancel scenario: a real cancel stops act and its containers -------------------------------------

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


def _scan_leaks(outpost, recorder, sampler, host):
    watched = known_secrets(outpost, recorder.tokens)
    return sampler.problems(watched) + secrets_in_text(
        watched, {**outpost_request_records(outpost), **stored_run_logs(host.runs_folder)}
    )


@pytest.fixture(scope="module")
def cancel_world(tmp_path_factory):
    host = require_run_host()
    root = tmp_path_factory.mktemp("gw-cancel")
    camp = make_camp(root)
    home = Path(os.environ["HOME"])
    leftover = _wait(30, lambda: not runner_act_processes())
    assert leftover, f"an act process is already running as outpost-runner: {runner_act_processes()}"
    sentinel = HomeSentinel(home / f".outpost-e2e-{secrets.token_hex(4)}", f"SENTINEL-{secrets.token_hex(8)}")
    try:
        sentinel.create()
        with (
            real_outpost(root, camp, runs=host) as outpost,
            TokenRecorder(outpost.port) as recorder,
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
            env = step_env(root, outpost)
            port = outpost.port
            n = iter(range(10_000))

            def gw(cwd, *argv):
                """Run one gateway command from a fresh harness in *cwd*; its output, ending ``rc=<n>``."""
                steps = [("register", register_cmd()), ("cmd", _gw(gateway, *argv))]
                return run_fake_harness(root, f"h{next(n)}", cwd, steps, env)["cmd"]

            w1 = camp.w1 / "app"

            def start(job):
                out = gw(w1, "run", "start", job)
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
                _scan_leaks(outpost, recorder, sampler, host),
            )
    finally:
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
