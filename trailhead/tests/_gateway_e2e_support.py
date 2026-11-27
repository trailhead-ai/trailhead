"""Fixtures for the opt-in gateway end-to-end test (``test_gateway_e2e_andromeda.py``).

Everything here is real except the harness: a real Outpost on a free loopback port with
a temp state directory, a temp camp layout with two workspaces, the real registration
step and gateway command. The harness is either a fake (a ``claude``-named shell, see
``_harness_process_support``) or a real ``claude -p`` run whose Bash tool is sandboxed
by settings rendered from the dotfiles' real template.

Credentials here are throwaway values minted by the temp Outpost. Nothing prints one,
and the sandboxed steps are written so their output never carries one.
"""

from __future__ import annotations

import grp
import http.client
import json
import os
import re
import shlex
import shutil
import argparse
import xml.etree.ElementTree as ET
import socket
import stat
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from trailhead.install import resolve_cli_tools

from ._git_support import make_hermetic_git_env
from ._gateway_support import (
    REPO_ROOT,
    free_port,
    gateway_runner_cmd,
    register_runner_cmd,
    runner_env,
    short_state_dir,
)
from ._harness_process_support import make_fake_harness
from ._trusted_checkout import build_gateway_shims, make_trusted_checkout

GROUP = "e2e"
W1, W2 = "ws-one", "ws-two"
PLUGIN_DIR = REPO_ROOT / "tools" / "outpost" / "plugins" / "outpost"
SANDBOX_MODEL = "sonnet"
SESSION_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class Camp:
    state: Path
    w1: Path
    w2: Path
    elsewhere: Path


def make_camp(root: Path) -> Camp:
    state = root / "camp"
    dirs = {}
    for slug in (W1, W2):
        d = state / GROUP / "worktrees" / slug
        d.mkdir(parents=True)
        (d / "manifest.json").write_text("{}")
        dirs[slug] = d
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    return Camp(state, dirs[W1], dirs[W2], elsewhere)


def outpost_checkout() -> Path:
    """The Outpost checkout: ``TRAILHEAD_GATEWAY_E2E_OUTPOST``, else the sibling a camp workspace has."""
    path = Path(os.environ.get("TRAILHEAD_GATEWAY_E2E_OUTPOST") or REPO_ROOT.parent / "outpost")
    if not (path / "server" / "index.ts").is_file():
        raise AssertionError(f"no Outpost checkout at {path}; set TRAILHEAD_GATEWAY_E2E_OUTPOST")
    return path


def trailhead_checkout() -> Path:
    """The trailhead checkout whose gateway command the sessions run.

    ``TRAILHEAD_GATEWAY_E2E_TRAILHEAD``, else this one.
    """
    path = Path(os.environ.get("TRAILHEAD_GATEWAY_E2E_TRAILHEAD") or REPO_ROOT)
    if not (path / "trailhead" / "gateway" / "command.py").is_file():
        raise AssertionError(f"no trailhead checkout at {path}; set TRAILHEAD_GATEWAY_E2E_TRAILHEAD")
    return path


def dotfiles_checkout() -> Path:
    path = Path(os.environ.get("TRAILHEAD_GATEWAY_E2E_DOTFILES") or Path(os.environ["HOME"]) / ".local/share/chezmoi")
    if not (path / "dot_claude" / "private_settings.json.tmpl").is_file():
        raise AssertionError(f"no dotfiles checkout at {path}; set TRAILHEAD_GATEWAY_E2E_DOTFILES")
    return path


@dataclass(frozen=True)
class RealOutpost:
    port: int
    state: Path
    log: Path

    @property
    def db_path(self) -> Path:
        """Where this Outpost keeps its database: its ``db`` folder, or beside the credential folders for an older
        one."""
        folder = self.state / "db" / "state.db"
        return folder if folder.exists() else self.state / "state.db"

    def registry_workspaces(self) -> list[tuple[str, str | None]]:
        """``(session_id, workspace)`` for every live workspace-session client."""
        db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            return db.execute(
                "SELECT session_id, workspace FROM broker_clients "
                "WHERE principal_kind = 'workspace-session' AND revoked_at IS NULL ORDER BY session_id"
            ).fetchall()
        finally:
            db.close()

    def runs(self) -> list[tuple[int, str, str, str, str | None, str | None]]:
        """``(id, workspace, job, state, result, reason)`` of every run, in id order.

        The reason is None for a table from before runs could report one.
        """
        db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            columns = {row[1] for row in db.execute("PRAGMA table_info(gateway_runs)")}
            reason = "reason" if "reason" in columns else "NULL"
            return db.execute(
                f"SELECT id, workspace, job, state, result, {reason} FROM gateway_runs ORDER BY id"
            ).fetchall()
        finally:
            db.close()

    def credential_files(self) -> list[Path]:
        return sorted((self.state / "sessions").glob("*/gateway-client.json"))


def _health_ok(port: int) -> bool:
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            f"http://127.0.0.1:{port}/health", timeout=1
        ) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


@contextmanager
def real_outpost(
    root: Path, camp: Camp, *, runs: RunsHost | None = None, extra_env: dict[str, str] | None = None
) -> Iterator[RealOutpost]:
    """A real Outpost: ``node --import tsx server/index.ts`` on a free port, temp state, temp camp layout.

    Its state folder is a short one (the gateway socket path under it has to fit in a socket address). Waits for
    ``/health``, the registration authority file and the gateway socket, failing early if the process exits.
    With *runs*, its config is the operator's (so its ``[runs]`` is theirs) and it also waits for the engine
    filter's socket, which that table makes it bind. *extra_env* is added to the Outpost process's environment.
    """
    checkout = outpost_checkout()
    port, sites_port = free_port(), free_port()
    state = short_state_dir()
    home = root / "outpost-home"
    home.mkdir()
    log = root / "outpost.log"
    env = {
        **os.environ,
        "HOME": str(home),
        "OUTPOST_STATE_DIR": str(state),
        "OUTPOST_CONFIG_DIR": str(root / "outpost-config"),
        "CAMP_STATE_DIR": str(camp.state),
        "HTTP_PORT": str(port),
        "SITES_PORT": str(sites_port),
        "NODE_OPTIONS": "--no-experimental-webstorage",
        **(extra_env or {}),
    }
    if runs is not None:
        copy_operator_config(operator_config_path(), root / "outpost-config")
    with log.open("wb") as sink:
        proc = subprocess.Popen(
            ["node", "--import", "tsx", "server/index.ts"], cwd=checkout, env=env, stdout=sink, stderr=sink
        )
    try:
        deadline = time.monotonic() + 60
        # A checkout from before the engine filter has no filter to wait for.
        has_filter = runs is not None and (checkout / "server" / "runs" / "engine-filter.ts").is_file()
        filter_sock = runs.runs_folder / "filter.sock" if has_filter else None
        while not (
            _health_ok(port)
            and (state / "registration" / "credential").exists()
            and (state / "gateway" / "gateway.sock").exists()
            and (filter_sock is None or filter_sock.exists())
        ):
            if proc.poll() is not None:
                raise AssertionError(
                    f"Outpost exited {proc.returncode} before answering; log:\n{log.read_text()[-2000:]}"
                )
            if time.monotonic() > deadline:
                raise AssertionError(f"Outpost did not answer on {port} in 60s; log:\n{log.read_text()[-2000:]}")
            time.sleep(0.1)
        yield RealOutpost(port, state, log)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        shutil.rmtree(state, ignore_errors=True)


def step_env(root: Path, outpost: RealOutpost) -> dict[str, str]:
    """The environment the trailhead side runs in: where Outpost's state is, and a scratch trailhead state."""
    return runner_env(
        outpost.state, trailhead_state=root / "trailhead-state", extra={"PYTHONPATH": str(trailhead_checkout())}
    )


# --- a fake harness ---------------------------------------------------------------------------------


def _parse_sections(out: str) -> dict[str, str]:
    """The output of a script that wrote ``@@name`` before each step, by step name."""
    text: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in out.splitlines():
        if line.startswith("@@"):
            current = text.setdefault(line[2:], [])
        elif current is not None:
            current.append(line)
    return {name: "\n".join(lines) for name, lines in text.items()}


def run_fake_harness(
    root: Path, name: str, cwd: Path, steps: list[tuple[str, str]], env: dict[str, str]
) -> dict[str, str]:
    """Run *steps* — ``(name, shell command)`` — as children of a ``claude``-named process started in *cwd*.

    Each step's combined output, then ``rc=<n>``, lands in the section of its name.
    """
    launcher = make_fake_harness(root / name)
    script = "; ".join(f'echo "@@{n}"; {cmd} 2>&1; echo "rc=$?"' for n, cmd in steps)
    proc = subprocess.run([str(launcher), "-c", script], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    return _parse_sections(proc.stdout)


def start_fake_harness(root: Path, name: str, cwd: Path, script: str, env: dict[str, str]) -> subprocess.Popen[str]:
    """Like ``run_fake_harness`` but not waited for: *script* runs as a child of a ``claude``-named process."""
    launcher = make_fake_harness(root / name)
    return subprocess.Popen(
        [str(launcher), "-c", script], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )


def register_cmd() -> str:
    return register_runner_cmd(["gateway", "register"])


def list_cmd(scratch: Path) -> str:
    return gateway_runner_cmd(scratch / "env-after.json", ["run", "list"])


# --- a real sandboxed claude session ----------------------------------------------------------------


def render_sandbox_settings(
    dotfiles: Path, outpost_state: Path, port: int, *, deny_read: bool = True, gateway: Path | None = None
) -> dict:
    """The dotfiles' real settings, rendered, cut down to the sandbox and the Read denials.

    The real template names ``~/.local/state/outpost`` and ``127.0.0.1:7313``; here those name this
    test's Outpost state and port, since both are fixed in the template for the operator's own
    Outpost. The ``Read`` rules are rewritten to the ``//<absolute path>`` form Claude Code requires for an
    absolute path (a single slash is relative to the settings file). ``deny_read=False`` removes both
    ways the template hides the credential folders: the sandbox ``denyRead`` and the ``Read`` denials.
    The template excludes and allows the gateway by the absolute path of the shim ``trailhead install``
    writes; ``gateway`` is the path this test's own wrapper stands at instead.
    """
    template = dotfiles / "dot_claude" / "private_settings.json.tmpl"
    done = subprocess.run(
        ["chezmoi", "execute-template", "--file", "--source", str(dotfiles), str(template)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if done.returncode != 0:
        raise AssertionError(f"chezmoi could not render the settings template: {done.stderr[-500:]}")
    rendered = json.loads(done.stdout)
    text = json.dumps({"sandbox": rendered["sandbox"], "deny": rendered["permissions"]["deny"]})
    text = text.replace("Read(~/.local/state/outpost", f"Read(/{outpost_state}")
    text = text.replace("~/.local/state/outpost", str(outpost_state)).replace("127.0.0.1:7313", f"127.0.0.1:{port}")
    cut = json.loads(text)
    sandbox, deny = cut["sandbox"], cut["deny"]
    if not (sandbox["filesystem"]["denyRead"] and deny):
        raise AssertionError("the rendered settings carry no denyRead / Read denials: the template changed shape")
    if not deny_read:
        del sandbox["filesystem"]["denyRead"]
        deny = []
    allow = rendered["permissions"]["allow"]
    shim = f"{os.environ['HOME']}/.local/state/trailhead/bin/outpost-gateway"
    if f"Bash({shim}:*)" not in allow or shim not in sandbox["excludedCommands"]:
        raise AssertionError("the rendered settings do not allow and exclude the gateway shim: template changed")
    if gateway is not None:
        sandbox = json.loads(json.dumps(sandbox).replace(shim, str(gateway)))
        allow = json.loads(json.dumps(allow).replace(shim, str(gateway)))
    return {"sandbox": sandbox, "permissions": {"allow": allow, "deny": deny}}


def install_shims(root: Path, outpost: RealOutpost) -> Path:
    """The bin dir the real ``trailhead install`` writes the gateway shims into, aimed at *outpost*.

    ``build_cli_shims`` with the Outpost tool enabled writes the pinned ``outpost-gateway`` and
    ``trailhead-gateway-register`` shims into a temp trailhead state folder, from a trusted copy of the
    trailhead checkout, with the state folders baked in (so Outpost's state is ``outpost.state``, as the real
    install bakes the operator's). The shims run the real gateway command and the real hook's registration
    step. Both reach Outpost on the gateway socket under the baked Outpost state folder, so nothing about them is
    replaced; a ``TokenRecorder`` entered on that state folder sits in front of the socket they use.
    """
    env = {
        "TRAILHEAD_STATE_DIR": str(root / "trailhead-state"),
        "OUTPOST_STATE_DIR": str(outpost.state),
        "HOME": str(root / "outpost-home"),
    }
    checkout = trailhead_checkout()
    return build_gateway_shims(
        resolve_cli_tools({"camp": True, "lore": True, "portage": True}),
        env=env,
        checkout=make_trusted_checkout(root / "trusted-checkout", checkout),
    )


def sandbox_steps(state: Path, other_port: int, gateway: Path, outpost_port: int) -> dict[str, str]:
    """The Bash commands the sandboxed session runs, one tool call each, exactly as written.

    None prints a credential: the credential read is redirected to /dev/null, so only its exit status
    and error reach the result, and no step echoes the proxy variable. Each is a single simple command:
    a chained one would need an approval a ``-p`` run cannot give.
    """
    probe = 'curl -sS --max-time 10 --noproxy "" --proxy "$HTTP_PROXY" -w "\\nhttp=%{http_code}\\n"'
    unix = f'curl -sS --max-time 10 --unix-socket "{state}/gateway/gateway.sock" -w "\\nhttp=%{{http_code}}\\n"'
    post = "-X POST -H 'content-type: application/json' -d '{\"pid\":1,\"start_time\":1}'"
    return {
        "credential_read": f'cat "{state}"/sessions/*/gateway-client.json > /dev/null',
        "run_list": f"{gateway} run list",
        "command_route": f"{unix} http://localhost/gateway/runs",
        "tcp_command_route": f"{probe} http://127.0.0.1:{outpost_port}/gateway/runs",
        "other_loopback_port": f"{probe} http://127.0.0.1:{other_port}/",
        "register_without_authority": f"{unix} {post} http://localhost/gateway/sessions",
    }


def session_prompt(steps: dict[str, str], read_paths: dict[str, str] | None = None) -> str:
    numbered = "\n".join(f"{i}. {cmd}" for i, cmd in enumerate(steps.values(), 1))
    reads = ""
    if read_paths:
        listed = "\n".join(f"- {path}" for path in read_paths.values())
        reads = (
            "\nThen use the Read tool (never Bash) on each of these file paths, one Read call each, in order, "
            f"and do not retry or work around a refusal:\n{listed}\n"
        )
    return (
        "You are running a scripted sandbox probe. Run each numbered Bash command below EXACTLY as written, "
        "one Bash tool call per item, in order. Do not modify, combine, retry or work around any command, "
        "never ask to run anything outside the sandbox, and do not investigate failures. "
        f"When done, reply with the single word done.\n\n{numbered}\n{reads}"
    )


def tool_results(stream_jsonl: str) -> dict[str, str]:
    """Map each Bash tool call's command to its result text, from ``claude -p --output-format stream-json``."""
    commands: dict[str, str] = {}
    results: dict[str, str] = {}
    for line in stream_jsonl.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                commands[block["id"]] = block["input"].get("command", "")
            elif block.get("type") == "tool_result":
                body = block.get("content")
                if isinstance(body, list):
                    body = "".join(part.get("text", "") for part in body if isinstance(part, dict))
                results[block["tool_use_id"]] = body if isinstance(body, str) else ""
    out: dict[str, str] = {}
    for tool_id, command in commands.items():
        if tool_id in results:
            out.setdefault(command, results[tool_id])
    return out


@dataclass(frozen=True)
class Session:
    """What one ``claude -p`` run left: the output of each step by name (absent when not run as written)."""

    outputs: dict[str, str]
    exit_code: int
    stream: Path
    reads: dict[str, tuple[str, bool]] = field(default_factory=dict)  # Read tool: path -> (result, is_error)


def run_claude_session(
    root: Path,
    label: str,
    *,
    cwd: Path,
    settings: dict,
    steps: dict[str, str],
    env: dict[str, str],
    shim_bin: Path,
    with_hook: bool,
    timeout: int = SESSION_TIMEOUT_SECONDS,
    read_paths: dict[str, str] | None = None,
    extra_path: Path | None = None,
    during: Callable[[], None] | None = None,
    before: Callable[[], None] | None = None,
) -> Session:
    """One real ``claude -p`` session in *cwd*, its Bash sandboxed by *settings*; waits for the process to exit.

    *read_paths* adds Read-tool calls after the Bash steps. *extra_path* goes first on the session's PATH
    (a directory a session could have put a script in). *during* runs on a thread beside the session; *before*
    runs once, after the session's command is built and before the process starts.
    """
    if shutil.which("claude") is None:
        raise AssertionError("claude is not on PATH")
    settings_file = root / f"settings-{label}.json"
    settings_file.write_text(json.dumps(settings))
    cmd = [
        "claude",
        "-p",
        "--model",
        SANDBOX_MODEL,
        "--settings",
        str(settings_file),
        "--setting-sources",
        "project",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if with_hook:
        cmd += ["--plugin-dir", str(PLUGIN_DIR)]
    cmd.append(session_prompt(steps, read_paths))
    stream = root / f"session-{label}.jsonl"
    front = os.pathsep.join(str(p) for p in (extra_path, shim_bin) if p is not None)
    session_env = {**env, "PATH": f"{front}{os.pathsep}{env['PATH']}"}
    if before is not None:
        before()
    helper = threading.Thread(target=during, daemon=True) if during is not None else None
    if helper is not None:
        helper.start()
    with stream.open("w") as out:
        done = subprocess.run(
            cmd,
            cwd=cwd,
            env=session_env,
            stdout=out,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    text = stream.read_text()
    results = tool_results(text)
    return Session(
        {name: results[cmd] for name, cmd in steps.items() if cmd in results},
        done.returncode,
        stream,
        read_results(text),
    )


@dataclass(frozen=True)
class Facts:
    """What Outpost's side holds after a session, read while that session's Outpost was still up."""

    credentials: list[tuple[str, int]]  # (session key, file mode)
    registry: list[tuple[str, str | None]]  # (session key, workspace)


NOT_RUN = "step {step!r} was not run as written"


def _out(session: Session, step: str) -> str:
    assert step in session.outputs, NOT_RUN.format(step=step)
    return session.outputs[step]


def check_hook_registered_the_session_for_w1(facts: Facts, session: Session) -> None:  # AC13
    assert len(facts.credentials) == 1, f"no credential file, or more than one: {facts.credentials}"
    key, mode = facts.credentials[0]
    assert mode == 0o600, f"credential file mode is {oct(mode)}, expected 0o600"
    assert facts.registry == [(key, f"{GROUP}/{W1}")], f"registered {facts.registry}, expected {GROUP}/{W1}"


def check_nothing_is_registered_before_the_first_gateway_call(facts: Facts) -> None:
    assert not facts.credentials, f"a credential file exists before any gateway call: {facts.credentials}"
    assert not facts.registry, f"the registry holds a session before any gateway call: {facts.registry}"


def check_credential_file_read_is_refused(facts: Facts, session: Session) -> None:  # AC22
    assert facts.credentials, "no credential file exists outside the sandbox, so a refusal proves nothing"
    out = _out(session, "credential_read")
    assert out.startswith("Exit code") and "No such file or directory" in out, f"the read was not refused: {out!r}"


def check_run_list_prints_the_empty_list(facts: Facts, session: Session) -> None:  # AC23, AC40
    out = _out(session, "run_list").strip()
    assert out == f"No runs in workspace {GROUP}/{W1}", f"run list printed {out!r}"


_GATEWAY_ANSWERS = ("credential_missing", "registration_authority_required", "credential_unrecognised")


def _assert_refused_by_the_sandbox(out: str, what: str) -> None:
    """The unix socket is unreachable from the sandbox: curl fails to connect, so nothing came back."""
    statuses = set(re.findall(r"http=(\d+)", out))
    assert out.startswith("Exit code") and statuses == {"000"}, f"{what} was not refused by the sandbox: {out!r}"
    assert not any(answer in out for answer in _GATEWAY_ANSWERS), f"{what} got an answer from the gateway: {out!r}"


def check_the_command_route_socket_is_refused_inside_the_sandbox(facts: Facts, session: Session) -> None:  # AC100
    _assert_refused_by_the_sandbox(_out(session, "command_route"), "the command route over the socket")


def check_the_command_route_is_not_served_over_tcp(facts: Facts, session: Session) -> None:  # AC100
    out = _out(session, "tcp_command_route")
    assert "http=404" in out and "gateway_not_on_tcp" in out, f"the route was not refused over TCP: {out!r}"


def check_proxied_curl_to_another_loopback_port_is_refused(facts: Facts, session: Session) -> None:  # AC100
    out = _out(session, "other_loopback_port")
    assert "http=403" in out, f"the proxy did not refuse the other port: {out!r}"


def check_registering_over_the_socket_is_refused_inside_the_sandbox(facts: Facts, session: Session) -> None:  # AC16, AC17, AC18
    _assert_refused_by_the_sandbox(_out(session, "register_without_authority"), "registration over the socket")


CHECKS = [
    check_hook_registered_the_session_for_w1,
    check_credential_file_read_is_refused,
    check_run_list_prints_the_empty_list,
    check_the_command_route_socket_is_refused_inside_the_sandbox,
    check_the_command_route_is_not_served_over_tcp,
    check_proxied_curl_to_another_loopback_port_is_refused,
    check_registering_over_the_socket_is_refused_inside_the_sandbox,
]


# --- the run scenario: preflight -----------------------------------------------------------------------

RUN_GROUP = "outpost-run"
OUTPOST_UNIT = "outpost.service"


def operator_config_path() -> Path:
    return Path(os.environ["HOME"]) / ".config" / "outpost" / "config.toml"


@dataclass(frozen=True)
class RunsHost:
    """The operator's ``[runs]`` table, which the Outpost under test must carry unchanged."""

    engine_socket: str
    act_command: list[str]
    runs_folder: Path
    platforms: dict[str, str]


class RunsConfigError(Exception):
    pass


def load_runs_host(config_path: Path) -> RunsHost:
    try:
        doc = tomllib.loads(Path(config_path).read_text())
    except FileNotFoundError:
        raise RunsConfigError(
            f"{config_path} does not exist: apply the dotfiles (chezmoi apply) on this host"
        ) from None
    except (OSError, ValueError) as err:
        raise RunsConfigError(f"{config_path} cannot be read: {err}") from None
    runs = doc.get("runs")
    if not isinstance(runs, dict):
        raise RunsConfigError(
            f"{config_path} has no [runs] table: apply the dotfiles (chezmoi apply) on this host, "
            "which adds it where scripts/provision-outpost-runner has set the run account up"
        )
    try:
        host = RunsHost(
            str(runs["engine_socket"]), list(runs["act_command"]), Path(runs["runs_folder"]), dict(runs["platforms"])
        )
    except (KeyError, TypeError, ValueError) as err:
        raise RunsConfigError(
            f"the [runs] table in {config_path} is incomplete ({type(err).__name__}: {err})"
        ) from None
    if "ubuntu-latest" not in host.platforms:
        raise RunsConfigError(f"the [runs] platforms in {config_path} do not map ubuntu-latest, which the fixture uses")
    return host


def groups_of(pid: int, proc: Path = Path("/proc")) -> set[int]:
    """The real gid and the supplementary groups of *pid*, from ``<proc>/<pid>/status``."""
    found: set[int] = set()
    for line in (proc / str(pid) / "status").read_text().splitlines():
        name, _, rest = line.partition(":")
        if name == "Gid":
            found.add(int(rest.split()[0]))
        elif name == "Groups":
            found.update(int(g) for g in rest.split())
    return found


def _gid_of(name: str) -> int:
    return grp.getgrnam(name).gr_gid


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def engine_request(socket_path: str | Path, method: str, path: str, timeout: float = 10) -> tuple[int, bytes]:
    """One request to a container engine's (or the filter's) HTTP API on a unix socket."""
    conn = _UnixConnection(str(socket_path), timeout)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def engine_answers(socket_path: str | Path) -> bool:
    try:
        return engine_request(socket_path, "HEAD", "/_ping", timeout=5)[0] == 200
    except (OSError, http.client.HTTPException):
        return False


def preflight_problems(
    config_path: Path,
    *,
    pid: int,
    proc: Path = Path("/proc"),
    gid_of=_gid_of,
    ping=engine_answers,
    home: Path | None = None,
) -> list[str]:
    """Everything wrong with this host for the run scenario, each with what the operator should do; empty when ready."""
    try:
        host = load_runs_host(config_path)
    except RunsConfigError as err:
        return [str(err)]
    problems: list[str] = []
    try:
        gid = gid_of(RUN_GROUP)
    except KeyError:
        problems.append(f"there is no {RUN_GROUP} group: run sudo scripts/provision-outpost-runner from the dotfiles")
    else:
        if gid not in groups_of(pid, proc):
            uid = os.getuid()
            problems.append(
                f"this process does not hold the {RUN_GROUP} group, so the Outpost it starts cannot open the run "
                f"engine or write run copies. With lingering on, logging out does not refresh groups: run "
                f"`sudo systemctl restart user@{uid}.service`, then open a new terminal and rerun"
            )
    if not ping(host.engine_socket):
        problems.append(
            f"the run engine socket {host.engine_socket} does not answer: start the run account's engine "
            "(`sudo scripts/provision-outpost-runner --check` shows what is missing)"
        )
    filter_sock = host.runs_folder / "filter.sock"
    if os.path.lexists(filter_sock):
        if ping(str(filter_sock)):
            problems.append(
                f"{filter_sock} is already served, by your own Outpost running with [runs]; "
                "only one filter can bind it. "
                f"Stop the daemon for the run (`systemctl --user stop {OUTPOST_UNIT}`) and start it again afterwards "
                f"(`systemctl --user start {OUTPOST_UNIT}`)"
            )
        else:
            problems.append(
                f"{filter_sock} is a stale file left by a crashed Outpost, and a new Outpost refuses to start over it: "
                f"rm {filter_sock}"
            )
    mode = stat.S_IMODE((home or Path(os.environ["HOME"])).stat().st_mode)
    if mode & 0o007:
        problems.append(
            f"your home is {mode:04o}, which the run account could read: "
            "chmod 0750 it (scripts/provision-outpost-runner does)"
        )
    return problems


def require_run_host(*, config_path: Path | None = None, pid: int | None = None, **probes) -> RunsHost:
    """The operator's ``[runs]`` table, or an AssertionError naming everything to fix, before any scenario runs.

    ``probes`` (``proc``, ``gid_of``, ``ping``, ``home``) replace the host's own group, socket and home checks.
    """
    path = config_path if config_path is not None else operator_config_path()
    problems = preflight_problems(path, pid=pid if pid is not None else os.getpid(), **probes)
    if problems:
        raise AssertionError("the run scenario cannot start on this host:\n" + "\n".join(f"- {p}" for p in problems))
    return load_runs_host(path)


def _first_setup_check(error_text: str, message: str) -> str:
    """The first problem a setup report lists (`- <problem>`), else the error's own first line."""
    for line in error_text.splitlines():
        if line.startswith("- "):
            return line[2:].strip()
    first = (message or error_text).strip().splitlines()
    return first[0] if first else "unknown error"


def red_verdict(
    junit_xml: str,
    log: str,
    *,
    expect_fail: int,
    markers: tuple[str, ...],
    per_failure: bool = False,
    allow_pass: tuple[str, ...] = (),
) -> tuple[bool, str]:
    """The verdict of a red run over its recorded pytest output: (valid, one-line reason).

    Any errored test means the run did not set up, and the verdict names the first setup check that failed.
    Otherwise the run is valid when exactly ``expect_fail`` tests failed and a marker shows the missing
    surface: anywhere in the log, or, with ``per_failure``, in every failure (any other passing test, bar
    those named in ``allow_pass``, is then invalid).
    """
    root = ET.fromstring(junit_xml)
    cases = list(root.iter("testcase"))
    errors = [c for c in cases if c.find("error") is not None]
    failed = [c for c in cases if c.find("failure") is not None]
    if errors:
        err = errors[0].find("error")
        return False, "setup failed: " + _first_setup_check(err.text or "", err.get("message", ""))
    counts = f"{len(failed)} failures, 0 errors"
    if len(failed) != expect_fail:
        return False, f"invalid: {len(failed)} failures, expected {expect_fail} (0 errors)"
    if per_failure:
        bad = []
        for case in cases:
            fail = case.find("failure")
            if fail is None:
                if not any(name in case.get("name", "") for name in allow_pass):
                    bad.append(case.get("name", ""))
            elif not any(m in (fail.get("message", "") + (fail.text or "")) for m in markers):
                bad.append(case.get("name", ""))
        if bad:
            return False, f"invalid: {counts}; not failing on the missing surface: {', '.join(bad)}"
    elif not any(m in log for m in markers):
        return False, f"invalid: {counts}; none of {', '.join(repr(m) for m in markers)} in the output"
    return True, "red valid"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="_gateway_e2e_support")
    sub = parser.add_subparsers(dest="command", required=True)
    verdict = sub.add_parser("red-verdict", help="judge a red run from its junit report and log")
    verdict.add_argument("--xml", type=Path, required=True)
    verdict.add_argument("--log", type=Path, required=True)
    verdict.add_argument("--expect-fail", type=int, required=True)
    verdict.add_argument("--marker", action="append", required=True)
    verdict.add_argument("--per-failure", action="store_true")
    verdict.add_argument("--allow-pass", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        xml, log = args.xml.read_text(), args.log.read_text()
    except OSError as err:
        print(f"invalid: no pytest report to read ({err})")
        return 1
    ok, text = red_verdict(
        xml, log, expect_fail=args.expect_fail, markers=tuple(args.marker),
        per_failure=args.per_failure, allow_pass=tuple(args.allow_pass),
    )
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


def copy_operator_config(source: Path, dest_dir: Path) -> Path:
    """The Outpost under test reads the operator's config as it is.

    Its ``[runs]`` is therefore the operator's by construction.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "config.toml"
    shutil.copyfile(source, dest)
    return dest


# --- the run scenario: the fixture repo ------------------------------------------------------------------


@dataclass(frozen=True)
class HomeSentinel:
    """A world-readable folder in the operator's home holding text no run may ever show.

    Only the home's mode keeps it out.

    The scenario aims at it with a symlink, a ``./``-relative local action step, and a direct read from the
    job. The ``HEAD`` and ``config`` files make it look like a git directory, but the nested ``.git`` file
    that names it is never uploaded (git does not list it), so that route is covered by the run account's
    own proof only.
    """

    path: Path
    marker: str

    def create(self) -> None:
        self.path.mkdir(mode=0o755)
        self.path.chmod(0o755)
        files = {
            "secret.txt": self.marker + "\n",
            "HEAD": "ref: refs/heads/main\n",
            "config": f"[user]\n\tname = {self.marker}\n",
            "action.yml": "name: sentinel\nruns:\n  using: composite\n  steps:\n"
            f"    - run: echo {self.marker}\n      shell: bash\n",
        }
        for name, text in files.items():
            (self.path / name).write_text(text)
            (self.path / name).chmod(0o644)

    def remove(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


ENGINE_SOCKET_PATH = "/var/lib/outpost-runner-engine/docker.sock"
SOCKETS_PROBED = ("/var/run/docker.sock", "/run/docker.sock", ENGINE_SOCKET_PATH)
TARGETS_PROBED = ("127.0.0.1", "10.0.2.2", "172.17.0.1", "host.docker.internal")


_CONNECT_CONTROL = (
    "if timeout 20 bash -c 'exec 3<>/dev/tcp/github.com/443' 2>/dev/null; "
    'then echo "probe control public: re""ached"; '
    'else echo "probe control public: unre""ached"; fi'
)
_SOCKET_PROBE = (
    'if [ -e "$s" ]; '
    'then echo "probe engine socket $s: pre""sent"; '
    'else echo "probe engine socket $s: ab""sent"; fi'
)
_LOOPBACK_PROBE = (
    'if timeout 3 bash -c "exec 3<>/dev/tcp/$h/$p" 2>/dev/null; '
    'then echo "probe loopback $h:$p: REA""CHED"; '
    'else echo "probe loopback $h:$p: ref""used"; fi'
)
_PUBLIC_PROBE = (
    "if timeout 20 bash -c 'exec 3<>/dev/tcp/github.com/443' 2>/dev/null; "
    'then echo "public github.com:443: re""ached"; '
    'else echo "public github.com:443: unre""achable"; fi'
)

WORKFLOW = """\
name: e2e
on: pull_request
jobs:
  pass:
    runs-on: ubuntu-latest
    steps:
      - run: echo "pass-job-ran-$((6*7))"
  fail:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "fail-job-ran-$((6*7))"
          exit 1
  matrix:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        color: [red, green]
        size: [small, large]
    steps:
      - run: echo "matrix-ran $((6*7)) ${{ matrix.color }}-${{ matrix.size }}"
  tree:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "workflow-edit=NOT-ap""plied"
      - run: |
          echo "event=$GITHUB_EVENT_NAME"
          echo "tracked=$(cat tracked.txt)"
          echo "untracked=$(cat untracked.txt)"
          if [ -e ignored.txt ]; then echo "ignored=pre""sent"; else echo "ignored=ab""sent"; fi
          if [ -L link-to-ssh ]; then echo "symlink=li""nk"; else echo "symlink=not-a-""link"; fi
          echo "ssh-via-link: $(cat link-to-ssh/* 2>&1 | head -1)"
          echo "home-via-link: $(cat link-to-home/secret.txt 2>&1 | head -1)"
          if [ -e vendored/.git ]; then echo "dotgit=pre""sent"; else echo "dotgit=ab""sent"; fi
  local_action:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./ok-action
      - uses: @LOCAL_ACTION@
      - run: echo "local-action-step-$((6*7))"
  probe:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "probe control: $(ls / | wc -l)"
          @CONNECT_CONTROL@
          echo "probe home: $(ls "@HOME@" 2>&1 | head -1)"
          echo "probe sentinel read: $(cat "@SENTINEL@/secret.txt" 2>&1 | head -1)"
          for s in @SOCKETS@; do
            @SOCKET_PROBE@
          done
          echo "probe docker env: $(env | grep -ci docker || true)"
          hex=$(awk '$2=="00000000" {print $3; exit}' /proc/net/route)
          gw=""
          if [ -n "$hex" ]; then gw=$(printf '%d.%d.%d.%d' 0x${hex:6:2} 0x${hex:4:2} 0x${hex:2:2} 0x${hex:0:2}); fi
          echo "probe gateway: $gw"
          for h in 127.0.0.1 $gw 10.0.2.2 172.17.0.1 host.docker.internal; do
            for p in @OUTPOST_PORT@ @LISTENER_PORT@; do
              @LOOPBACK_PROBE@
            done
          done
  public:
    runs-on: ubuntu-latest
    steps:
      - run: |
          @PUBLIC_PROBE@
  mount_priv:
    runs-on: ubuntu-latest
    container:
      image: @IMAGE@
      options: --privileged -v /:/host
    steps:
      - run: echo "mount-priv-job-ran-$((6*7))"
  pull_missing:
    runs-on: ubuntu-latest
    container: docker.io/library/outpost-e2e-no-such-image-0000:never
    steps:
      - run: echo pull-missing-job-ran
  unmapped:
    runs-on: windows-latest
    steps:
      - run: echo unmapped-job-ran
  slow:
    runs-on: ubuntu-latest
    steps:
      - run: echo "stream-first-$((6*7))"
      - run: sleep 20
      - run: echo "stream-second-$((6*7))"
  cancel_hold:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "cancel-marker-$((6*7))"
          sleep 120
  cancel_behind:
    runs-on: ubuntu-latest
    steps:
      - run: echo "cancel-behind-ran-$((6*7))"
"""
WORKFLOW = (
    WORKFLOW.replace("@CONNECT_CONTROL@", _CONNECT_CONTROL)
    .replace("@SOCKET_PROBE@", _SOCKET_PROBE)
    .replace("@LOOPBACK_PROBE@", _LOOPBACK_PROBE)
    .replace("@PUBLIC_PROBE@", _PUBLIC_PROBE)
)

OK_ACTION = """\
name: ok
runs:
  using: composite
  steps:
    - run: echo "ok-action-ran-$((6*7))"
      shell: bash
"""


def _git(repo: Path, env: dict[str, str], *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True)


def build_fixture_repo(
    path: Path,
    *,
    image: str,
    ssh: Path,
    sentinel: HomeSentinel,
    ports: tuple[int, int],
    operator_home: Path,
) -> Path:
    """A git repo whose working tree differs from its commit in every way a run must honour.

    Committed: ``tracked.txt``, a ``.gitignore``, ``ok-action`` (a local action that must run: the control for the
    sentinel's), and the workflow whose ``tree`` job says the
    workflow edit was not applied. Then, uncommitted: ``tracked.txt`` edited, the workflow edited to say it
    was, an untracked file, an ignored file, symlinks to *ssh* and to the *sentinel* folder, and a
    nested folder whose ``.git`` file points into the sentinel folder.
    """
    path.mkdir(parents=True)
    env = make_hermetic_git_env(os.environ, path.parent)
    workflow_file = path / ".github" / "workflows" / "ci.yml"
    workflow_file.parent.mkdir(parents=True)
    local_action = "./" + "../" * 12 + str(sentinel.path).lstrip("/")
    workflow = (
        WORKFLOW.replace("@IMAGE@", image)
        .replace("@LOCAL_ACTION@", local_action)
        .replace("@HOME@", str(operator_home))
        .replace("@SENTINEL@", str(sentinel.path))
        .replace("@SOCKETS@", " ".join(SOCKETS_PROBED))
        .replace("@OUTPOST_PORT@", str(ports[0]))
        .replace("@LISTENER_PORT@", str(ports[1]))
    )
    workflow_file.write_text(workflow)
    (path / "ok-action").mkdir()
    (path / "ok-action" / "action.yml").write_text(OK_ACTION)
    (path / "tracked.txt").write_text("committed\n")
    (path / ".gitignore").write_text("ignored.txt\n")
    _git(path, env, "init", "-q", "-b", "main")
    _git(path, env, "add", "-A")
    _git(path, env, "commit", "-q", "-m", "baseline")
    (path / "tracked.txt").write_text("edited-uncommitted\n")
    workflow_file.write_text(workflow.replace('workflow-edit=NOT-ap""plied', 'workflow-edit=ap""plied'))
    (path / "untracked.txt").write_text("new-file\n")
    (path / "ignored.txt").write_text("ignored\n")
    (path / "link-to-ssh").symlink_to(ssh)
    (path / "link-to-home").symlink_to(sentinel.path)
    (path / "vendored").mkdir()
    (path / "vendored" / ".git").write_text(f"gitdir: {sentinel.path}\n")
    (path / "vendored" / "file.txt").write_text("vendored\n")
    return path


# --- the run scenario: what a session, a harness or the engine left behind ------------------------------


def exit_code(output: str) -> int:
    """The exit status of a Bash tool call from its result text: nonzero ones start ``Exit code N``."""
    found = re.match(r"Exit code (\d+)", output)
    return int(found.group(1)) if found else 0


def parse_stamped_lines(out: str) -> list[tuple[float, str]]:
    """``(arrival time, text)`` for each ``@@line <time> <text>`` line a timestamping harness script wrote."""
    stamped = []
    for line in out.splitlines():
        parts = line.split(" ", 2)
        if parts[0] == "@@line" and len(parts) == 3:
            stamped.append((float(parts[1]), parts[2]))
    return stamped


def streamed_as_produced(stamped: list[tuple[float, str]], early: str, late: str, gap: float) -> str | None:
    """None when the first line holding *early* reached the reader *gap* seconds before the first holding *late*."""
    times = {}
    for needle in (early, late):
        arrived = [t for t, text in stamped if needle in text]
        if not arrived:
            return f"{needle!r} never arrived; saw {[text for _, text in stamped]}"
        times[needle] = arrived[0]
    apart = times[late] - times[early]
    if apart < gap:
        return (
            f"{early!r} and {late!r} arrived together, {apart:.1f}s apart; the job wrote them at least {gap:.0f}s apart"
        )
    return None


# --- the run scenario: the session's steps -----------------------------------------------------------------

RUN_SESSION_TIMEOUT_SECONDS = 900
#: The jobs the sandboxed session starts and follows, in order; the n-th is run n in a fresh Outpost.
SESSION_JOBS = ["pass", "fail", "matrix", "tree", "local_action", "probe", "public", "mount_priv", "pull_missing"]
SUDO_ACT = "sudo -n -u outpost-runner /usr/local/lib/outpost-runner/act-run --version"


def run_steps(gateway: Path) -> dict[str, str]:
    """The sandboxed session's Bash commands, one tool call each. Run ids are the jobs' positions: Outpost is fresh."""
    steps: dict[str, str] = {}
    for n, job in enumerate(SESSION_JOBS, 1):
        steps[f"start_{job}"] = f"{gateway} run start {job}"
        steps[f"watch_{job}"] = f"{gateway} run watch {n} --exit-status"
        if job == "matrix":
            steps["view_matrix"] = f"{gateway} run view {n}"
    steps["start_unmapped"] = f"{gateway} run start unmapped"
    steps["run_list"] = f"{gateway} run list --json"
    steps["sudo_act"] = SUDO_ACT
    return steps


def stream_script(port: int, gateway: Path) -> str:
    """Start the ``slow`` job and follow it, stamping each line of output with when it arrived.

    *gateway* is the wrapper ``install_shims`` made: ``run start`` runs git, which the entry point
    the other fake-harness steps use refuses to allow.
    """
    gw = shlex.quote(str(gateway))
    return (
        f"{register_cmd()} >/dev/null 2>&1; "
        f"t0=$(date +%s.%N); id=$({gw} run start slow 2>/dev/null); rc=$?; t1=$(date +%s.%N); "
        'echo "@@start rc=$rc id=$id t0=$t0 t1=$t1"; '
        f'{gw} run watch "$id" | while IFS= read -r l; do echo "@@line $(date +%s.%N) $l"; done; '
        'echo "@@end $(date +%s.%N)"'
    )


def act_containers(engine_socket: str, name_part: str) -> list[dict]:
    """The engine's record of each running act container whose name contains *name_part*.

    Empty when the engine will not say.
    """
    try:
        status, body = engine_request(engine_socket, "GET", "/containers/json")
        if status != 200:
            return []
        found = []
        for c in json.loads(body):
            names = c.get("Names") or []
            if any(n.startswith("/act-") and name_part in n for n in names):
                status, detail = engine_request(engine_socket, "GET", f"/containers/{c['Id']}/json")
                if status == 200:
                    found.append(json.loads(detail))
        return found
    except (OSError, ValueError, http.client.HTTPException):
        return []


class NotTheRunnerEngine(Exception):
    """The socket is not the dedicated runner engine's, so nothing may be listed or removed through it."""


def require_runner_engine(engine_socket: str) -> None:
    if str(engine_socket) != ENGINE_SOCKET_PATH:
        raise NotTheRunnerEngine(
            f"refusing to touch containers through {engine_socket!r}: only the dedicated runner engine "
            f"{ENGINE_SOCKET_PATH} may be listed or cleared, whatever the operator's runs config names"
        )


class EngineUnavailable(Exception):
    """The container engine would not say what it holds."""


def act_container_names(engine_socket: str, *, request=engine_request) -> list[str]:
    """The names of every act container the engine holds, stopped ones included.

    Raises ``EngineUnavailable`` when the engine will not say, so "no containers" is only ever a real answer.
    """
    require_runner_engine(engine_socket)
    try:
        status, body = request(engine_socket, "GET", "/containers/json?all=1")
        if status != 200:
            raise EngineUnavailable(f"the engine answered {status} to the container listing")
        return [n for c in json.loads(body) for n in (c.get("Names") or []) if n.startswith("/act-")]
    except (OSError, ValueError, http.client.HTTPException) as err:
        raise EngineUnavailable(f"the engine could not be asked for its containers: {err!r}") from err


def remove_act_containers(engine_socket: str, *, request=engine_request) -> None:
    """Force-remove every act container, so a run that was never cancelled does not leave a job sleeping.

    Raises ``NotTheRunnerEngine`` for any socket but the dedicated runner engine's, before any request is sent.
    """
    require_runner_engine(engine_socket)
    try:
        status, body = request(engine_socket, "GET", "/containers/json?all=1")
        if status != 200:
            return
        for c in json.loads(body):
            if any(n.startswith("/act-") for n in c.get("Names") or []):
                request(engine_socket, "DELETE", f"/containers/{c['Id']}?force=1")
    except (OSError, ValueError, http.client.HTTPException):
        pass


ACT_COMMS = ("act", "act-run")


def act_process_lines(ps_output: str) -> list[str]:
    """The lines of ``ps -o pid,comm,args`` output whose process is act or its wrapper, not the engine beside them."""
    return [line for line in ps_output.splitlines() if len(line.split()) >= 2 and line.split()[1] in ACT_COMMS]


def runner_act_processes(user: str = "outpost-runner") -> list[str]:
    """The act processes *user* owns right now."""
    done = subprocess.run(["ps", "-u", user, "-o", "pid,comm,args"], capture_output=True, text=True, timeout=20)
    return act_process_lines(done.stdout)


def split_image_ref(ref: str) -> tuple[str, str]:
    """``(name, tag or digest)`` as the engine's pull endpoint takes them."""
    if "@" in ref:
        name, digest = ref.split("@", 1)
        last = name.rsplit("/", 1)[-1]
        if ":" in last:
            name = name[: -(len(last) - last.index(":"))]
        return name, digest
    last = ref.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = ref.rsplit(":", 1)
        return name, tag
    return ref, "latest"


def pull_stream_error(body: bytes) -> str | None:
    """The first error in an engine pull's progress stream (one JSON object per line), or None."""
    for line in body.decode(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("error"), str):
            return event["error"]
    return None


def prepull_image(engine_socket: str, ref: str) -> None:
    """Pull the pinned job image through the run engine, so no job's time goes on the first download."""
    name, tag = split_image_ref(ref)
    status, body = engine_request(
        engine_socket,
        "POST",
        f"/images/create?fromImage={urllib.parse.quote(name, safe='')}&tag={urllib.parse.quote(tag, safe='')}",
        timeout=900,
    )
    error = pull_stream_error(body)
    if status != 200 or error is not None:
        raise AssertionError(f"the run engine could not pull {ref}: status {status}, {error or body[-300:]!r}")


def probe_problems(log: str, ports: tuple[int, int], marker: str) -> list[str]:
    """What is wrong with the ``probe`` job's log.

    A control that did not run, a probe that got in, or a line that is missing.
    """
    problems = []

    def line(pattern: str) -> re.Match | None:
        return re.search(pattern, log)

    control = line(r"probe control: (\d+)")
    if control is None or int(control.group(1)) <= 0:
        problems.append("the control (a listing of / that must be non-empty) did not run or listed nothing")
    if line(r"probe control public: reached") is None:
        problems.append(
            "the public-host control did not print reached, "
            "so the connect probe cannot be trusted to detect a connection"
        )
    if line(r"probe home: ls: .*(No such file|Permission denied)") is None:
        problems.append("the home read did not print a refusal from ls")
    if line(r"probe sentinel read: cat: .*(No such file|Permission denied)") is None:
        problems.append("the sentinel read did not print a refusal from cat")
    for sock in SOCKETS_PROBED:
        if line(rf"probe engine socket {re.escape(sock)}: absent") is None:
            problems.append(f"engine socket {sock} was not reported absent")
    if line(r"probe docker env: 0\b") is None:
        problems.append("the docker env count was not 0 (or not printed)")
    gateway = line(r"probe gateway: (\d+\.\d+\.\d+\.\d+)")
    targets = list(TARGETS_PROBED)
    if gateway is None:
        problems.append("the job printed no gateway address to probe")
    elif gateway.group(1) not in targets:
        targets.append(gateway.group(1))
    for host in targets:
        for port in ports:
            if line(rf"probe loopback {re.escape(host)}:{port}: refused") is None:
                problems.append(f"{host}:{port} was not reported refused")
    if marker in log:
        problems.append("text from the operator's home appears in the log")
    return problems


def check_container_inspect(inspect: dict, secrets: list[str]) -> list[str]:
    """What a container's engine record shows reaching it from the host; secrets are named by variable, never printed.

    A record that is cut down proves nothing, so one without ``HostConfig`` or ``Config.Env``, without a volume
    mount (act's job containers always have some), or with no secrets to look for, is itself a problem.
    """
    problems = []
    host = inspect.get("HostConfig")
    env = (inspect.get("Config") or {}).get("Env")
    if not isinstance(host, dict):
        problems.append("the record has no HostConfig")
        host = {}
    if not isinstance(env, list):
        problems.append("the record has no Config.Env")
        env = []
    if not secrets:
        problems.append("there are no secrets to look for in the environment")
    mounts = [*(inspect.get("Mounts") or []), *(host.get("Mounts") or [])]
    if not any(m.get("Type") == "volume" for m in mounts):
        problems.append("the record shows no volume mount, so it may not be the whole record")
    for m in mounts:
        if m.get("Type") not in ("volume", "tmpfs"):
            problems.append(f"{m.get('Type')} mount of {m.get('Source')} at {m.get('Destination')}")
        if ".sock" in f"{m.get('Source', '')}{m.get('Destination', '')}":
            problems.append(f"an engine or other socket is mounted at {m.get('Destination')}")
    if host.get("Binds"):
        problems.append(f"HostConfig.Binds is {host['Binds']}")
    if host.get("Privileged"):
        problems.append("the container is privileged")
    for entry in env:
        name, _, value = entry.partition("=")
        if name in ("GITHUB_TOKEN", "SSH_AUTH_SOCK") and value:
            problems.append(f"{name} is set in the container")
        elif any(x and x in value for x in secrets):
            problems.append(f"{name} holds a secret Outpost minted")
    return problems


# --- the credential scan ----------------------------------------------------------------------------
#
# A problem names the secret by its label and where it was seen, never by its value, so a failing
# run does not print the credential it is reporting.


def process_arguments(proc: Path = Path("/proc")) -> list[tuple[int, list[str]]]:
    """``(pid, argv)`` of every process whose command line can be read now."""
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if raw:
            found.append((int(entry.name), raw.decode(errors="replace").split("\0")[:-1]))
    return found


def arguments_with_secrets(secrets: dict[str, str], snapshots: list[tuple[int, list[str]]]) -> list[str]:
    """One problem per ``(label, program, pid)`` whose arguments carry a secret's bytes."""
    problems: dict[tuple[str, str, int], str] = {}
    for pid, argv in snapshots:
        for label, secret in secrets.items():
            if secret and any(secret in arg for arg in argv):
                program = os.path.basename(argv[0]) if argv else "?"
                problems[(label, program, pid)] = f"{label} is in the arguments of {program} (pid {pid})"
    return list(problems.values())


def secrets_in_text(secrets: dict[str, str], sources: dict[str, str]) -> list[str]:
    return [
        f"{label} is in the {source}"
        for source, text in sources.items()
        for label, secret in secrets.items()
        if secret and secret in text
    ]


class ArgumentSampler:
    """Reads every process's arguments every *interval* seconds while it is open, and keeps what it saw."""

    def __init__(self, interval: float = 0.02, proc: Path = Path("/proc")) -> None:
        self.interval = interval
        self.proc = proc
        self.snapshots: list[tuple[int, list[str]]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.snapshots.extend(process_arguments(self.proc))
            self._stop.wait(self.interval)

    def __enter__(self) -> ArgumentSampler:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=10)
        self.snapshots.extend(process_arguments(self.proc))

    def problems(self, secrets: dict[str, str]) -> list[str]:
        return arguments_with_secrets(secrets, self.snapshots)


_BEARER = re.compile(rb"[Aa]uthorization:\s*Bearer\s+([A-Za-z0-9._~+/=-]+)")
_ACCESS_TOKEN = re.compile(rb'"access_token"\s*:\s*"([^"]+)"')


class TokenRecorder:
    """A relay in front of Outpost's gateway socket that learns every access token the gateway command sends or is
    given.

    The tokens live only in the command's memory and Outpost stores their hash, so the one place they can be
    read to be looked for elsewhere is the wire. Entered on Outpost's state folder, it moves the running Outpost's
    socket aside (a bound socket keeps accepting under its new name) and listens at the socket's place, so the
    command, which finds the socket by that place, talks to the relay; ``tokens`` holds each
    ``Authorization: Bearer`` value and each ``access_token`` answer. Leaving puts the socket back.
    """

    def __init__(self, state: Path) -> None:
        self.socket_path = state / "gateway" / "gateway.sock"
        self.upstream_path = self.socket_path.with_name("gateway.upstream.sock")
        self.tokens: set[str] = set()
        self._server: socket.socket | None = None
        self._open: list[socket.socket] = []
        self._thread = threading.Thread(target=self._accept, daemon=True)

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._relay, args=(client,), daemon=True).start()

    def _relay(self, client: socket.socket) -> None:
        upstream = socket.socket(socket.AF_UNIX)
        try:
            upstream.settimeout(30)
            upstream.connect(str(self.upstream_path))
            upstream.settimeout(None)
        except OSError:
            upstream.close()
            client.close()
            return
        self._open += [client, upstream]
        threading.Thread(target=self._pump, args=(client, upstream, _BEARER), daemon=True).start()
        self._pump(upstream, client, _ACCESS_TOKEN)

    def _pump(self, source: socket.socket, sink: socket.socket, pattern: re.Pattern[bytes]) -> None:
        tail = b""
        try:
            while chunk := source.recv(65536):
                window = tail + chunk
                self.tokens.update(m.decode() for m in pattern.findall(window))
                tail = window[-512:]
                sink.sendall(chunk)
        except OSError:
            pass
        finally:
            for sock in (source, sink):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def __enter__(self) -> TokenRecorder:
        os.rename(self.socket_path, self.upstream_path)
        self._server = socket.socket(socket.AF_UNIX)
        self._server.bind(str(self.socket_path))
        self.socket_path.chmod(self.upstream_path.stat().st_mode & 0o777)
        self._server.listen(32)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.close()
        for sock in self._open:
            sock.close()
        self.socket_path.unlink(missing_ok=True)
        os.rename(self.upstream_path, self.socket_path)


def known_secrets(outpost: RealOutpost, tokens: set[str]) -> dict[str, str]:
    """Every secret this Outpost has handed out, labelled: client secrets, the registration authority, the operator
    credential, tokens."""
    found: dict[str, str] = {}
    for n, path in enumerate(outpost.credential_files(), 1):
        found[f"client secret ({n})"] = json.loads(path.read_text())["client_secret"]
    for label, name in (("registration authority", "registration"), ("operator credential", "operator")):
        path = outpost.state / name / "credential"
        if path.exists():
            found[label] = path.read_text().strip()
    for n, token in enumerate(sorted(tokens), 1):
        found[f"access token ({n})"] = token
    return found


def outpost_request_records(outpost: RealOutpost) -> dict[str, str]:
    """What Outpost keeps of the requests it served: every timeline event row, and its log."""
    db = sqlite3.connect(f"file:{outpost.db_path}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT * FROM timeline_events ORDER BY id").fetchall()
    finally:
        db.close()
    return {
        "timeline events": "\n".join(" | ".join(map(str, row)) for row in rows),
        "Outpost's log": outpost.log.read_text(),
    }


def stored_run_logs(runs_folder: Path) -> dict[str, str]:
    """A run's stored log is ``<copy>.log`` in the runs folder; each, by its copy name."""
    return {f"run log {path.stem}": path.read_text(errors="replace") for path in sorted(runs_folder.glob("*.log"))}


# --- the denial probes: what a sandboxed session must not read, run, or be handed ----------------------

LATE_FILE = "late.db-wal"
READ_CONTROL = "read-control.txt"
READ_CONTROL_TEXT = "READ-CONTROL-OK"

# Run by one sandboxed Bash call. It reports it is up, waits for the test to create LATE_FILE in the database
# folder after that (the way SQLite makes a -wal after a command has started), and then tries to read it.
_LATE_FILE_PROBE = """\
import os, sys, time
folder, rendezvous = sys.argv[1], sys.argv[2]
open(os.path.join(rendezvous, "ready"), "w").close()
deadline = time.time() + 120
while not os.path.exists(os.path.join(rendezvous, "go")) and time.time() < deadline:
    time.sleep(0.05)
try:
    with open(os.path.join(folder, "%s"), "rb") as f:
        f.read(1)
    print("late_file=readable")
except OSError as err:
    print("late_file=refused:" + (err.strerror or "?"))
try:
    print("listing=" + ",".join(sorted(os.listdir(folder))))
except OSError as err:
    print("listing=refused:" + (err.strerror or "?"))
""" % LATE_FILE


def meeting_folder(workspace: Path) -> Path:
    """A fresh folder inside *workspace*, resolved through any symlink, where the probe and its actor meet.

    Both sessions can write the workspace; ``/tmp`` is a path the sandbox may give each session its own copy of.
    """
    return Path(tempfile.mkdtemp(prefix=".gw-denial-", dir=Path(workspace).resolve()))


def write_late_file_probe(rendezvous: Path) -> Path:
    rendezvous.mkdir(parents=True, exist_ok=True)
    script = rendezvous / "late_file_probe.py"
    script.write_text(_LATE_FILE_PROBE)
    return script


class LateFileActor:
    """Run beside a sandboxed session: when the probe says it is up, create ``LATE_FILE`` in *db_dir*, then say go."""

    def __init__(self, db_dir: Path, rendezvous: Path, timeout: float = 300) -> None:
        self.db_dir, self.rendezvous, self.timeout = db_dir, rendezvous, timeout
        self.created = False

    def __call__(self) -> None:
        deadline = time.monotonic() + self.timeout
        while not (self.rendezvous / "ready").exists():
            if time.monotonic() > deadline:
                return
            time.sleep(0.05)
        (self.db_dir / LATE_FILE).write_bytes(b"LATE-WAL-BYTES")
        self.created = True
        (self.rendezvous / "go").write_text("")


def write_path_shadow(directory: Path, state: Path) -> Path:
    """A script named ``outpost-gateway`` that, if it ever runs outside the sandbox, can read the credential."""
    directory.mkdir(parents=True, exist_ok=True)
    shadow = directory / "outpost-gateway"
    shadow.write_text(
        "#!/bin/sh\necho shadow-ran\n"
        f'if cat "{state}"/sessions/*/gateway-client.json > /dev/null 2>&1; then echo credential=readable; '
        "else echo credential=refused; fi\n"
    )
    shadow.chmod(0o755)
    return shadow


def denial_steps(state: Path, other_key: str, rendezvous: Path, port: int) -> dict[str, str]:
    """The Bash commands that probe what a session must not reach, one tool call each, none printing a credential."""
    return {
        "operator_read": f'cat "{state}/operator/credential" > /dev/null',
        "registration_read": f'cat "{state}/registration/credential" > /dev/null',
        "other_session_read": f'cat "{state}/sessions/{other_key}/gateway-client.json" > /dev/null',
        "db_read": f'cat "{state}/db/state.db" > /dev/null',
        "late_file": f'python3 "{rendezvous / "late_file_probe.py"}" "{state}/db" "{rendezvous}"',
        "path_shadow": "outpost-gateway run list",
        "auth_header": "trailhead-gateway-auth-header",
        "cookie_bootstrap": (
            'curl -sS --max-time 10 --noproxy "" --proxy "$HTTP_PROXY" -D - -o /dev/null '
            f"-H 'Accept: text/html' -w 'http=%{{http_code}}\\n' http://127.0.0.1:{port}/"
        ),
    }


def read_tool_paths(state: Path, other_key: str, control_dir: Path) -> dict[str, str]:
    """What the Read tool is asked for: the denied files, and a control in the session's own directory."""
    return {
        "operator_credential": f"{state}/operator/credential",
        "registration_authority": f"{state}/registration/credential",
        "other_session_credential": f"{state}/sessions/{other_key}/gateway-client.json",
        "database": f"{state}/db/state.db",
        "control": f"{control_dir}/{READ_CONTROL}",
    }


@dataclass(frozen=True)
class DenialFacts:
    """What exists outside the sandbox, so a refusal inside it proves something; and whether the late file was made."""

    exists: dict[str, bool]
    late_file_created: bool


def denial_facts(state: Path, other_key: str, actor: LateFileActor) -> DenialFacts:
    return DenialFacts(
        {
            "operator_read": (state / "operator" / "credential").is_file(),
            "registration_read": (state / "registration" / "credential").is_file(),
            "other_session_read": (state / "sessions" / other_key / "gateway-client.json").is_file(),
            "db_read": (state / "db" / "state.db").is_file(),
        },
        actor.created,
    )


def check_read_is_refused(step: str):
    """A check that the sandboxed read named *step* failed as the sandbox's mask makes a read fail: the file is
    absent."""

    def check(facts: DenialFacts, session: Session) -> None:
        assert facts.exists.get(step), (
            f"{step}: the file does not exist outside the sandbox, so a refusal proves nothing"
        )
        out = _out(session, step)
        assert out.startswith("Exit code") and "No such file or directory" in out, (
            f"{step}: the read was not refused: {out!r}"
        )

    check.__name__ = f"check_{step}_is_refused"
    return check


def check_the_late_side_file_is_refused(
    facts: DenialFacts, session: Session, platform: str = sys.platform
) -> None:
    assert facts.late_file_created, "the late side file was never created, so a refusal proves nothing"
    out = _out(session, "late_file")
    assert "late_file=refused" in out and "late_file=readable" not in out, f"the late file was not refused: {out!r}"
    listing = out.split("listing=", 1)[1].splitlines()[0] if "listing=" in out else None
    assert listing is not None, f"the probe printed no listing line: {out!r}"
    bare = listing == "" and platform.startswith("linux")
    assert listing.startswith("refused") or bare, f"the database folder could be listed: {out!r}"
    assert "state.db" not in out and LATE_FILE not in out, f"the database folder listed a file: {out!r}"


def check_a_shadowed_gateway_cannot_read_the_credential(facts: DenialFacts, session: Session) -> None:
    out = _out(session, "path_shadow")
    assert "shadow-ran" in out, f"the shadowing script did not run, so nothing was proven: {out!r}"
    assert "credential=readable" not in out, f"the shadowing script read the credential: {out!r}"
    assert "credential=refused" in out, f"the shadowing script did not report its read: {out!r}"


def check_no_cookie_is_handed_to_a_page_request(facts: DenialFacts, session: Session) -> None:
    out = _out(session, "cookie_bootstrap")
    assert "http=" in out, f"the page request did not complete: {out!r}"
    assert "set-cookie" not in out.lower(), "a page request was handed a cookie"


def check_the_auth_header_helper_prints_no_token(facts: DenialFacts, session: Session) -> None:
    """The tool server's header helper, run by name from the sandboxed shell, cannot read the credential, so it prints
    the empty object and no ``Bearer`` token."""
    out = _out(session, "auth_header")
    assert "Bearer" not in out, f"the header helper printed a token: {out!r}"
    assert any(line.strip() == "{}" for line in out.splitlines()), (
        f"the header helper did not run, or did not print an empty object: {out!r}"
    )


def check_the_launched_session_reaches_its_own_workspace_runs(facts: DenialFacts, session: Session) -> None:
    """The ritual-profile session ran the pinned gateway shim it may run, and Outpost answered for its workspace."""
    out = _out(session, "gateway_run_list")
    assert f"No runs in workspace {GROUP}/{W1}" in out.splitlines(), (
        f"the launched session's gateway shim did not answer for its own workspace: {out!r}"
    )


def read_results(stream_jsonl: str) -> dict[str, tuple[str, bool]]:
    """Map each Read tool call's file path to ``(result text, is_error)``, from ``claude -p --output-format
    stream-json``."""
    paths: dict[str, str] = {}
    results: dict[str, tuple[str, bool]] = {}
    for line in stream_jsonl.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use" and block.get("name") == "Read":
                paths[block["id"]] = block["input"].get("file_path", "")
            elif block.get("type") == "tool_result":
                body = block.get("content")
                if isinstance(body, list):
                    body = "".join(part.get("text", "") for part in body if isinstance(part, dict))
                results[block["tool_use_id"]] = (body if isinstance(body, str) else "", bool(block.get("is_error")))
    return {paths[i]: result for i, result in results.items() if i in paths}


def check_the_read_tool_is_denied(name: str):
    def check(results: dict[str, tuple[str, bool]], paths: dict[str, str]) -> None:
        path = paths[name]
        assert path in results, f"{name}: the Read tool was not asked for {path}"
        text, is_error = results[path]
        assert is_error or "denied" in text.lower(), f"{name}: the file was read: {text[:80]!r}"
        assert "denied by your permission settings" in text, (
            f"{name}: refused, but not by a permission denial: {text!r}"
        )

    check.__name__ = f"check_the_read_tool_is_denied_{name}"
    return check


def check_the_read_control_is_readable(results: dict[str, tuple[str, bool]], paths: dict[str, str]) -> None:
    path = paths["control"]
    assert path in results, f"the Read tool was not asked for the control {path}"
    text, is_error = results[path]
    assert not is_error and READ_CONTROL_TEXT in text, (
        f"the control file could not be read ({text[:80]!r}), so a denial of the others may be a missing grant"
    )


def launched_session_settings(root: Path, outpost_state: Path, workspace: Path) -> dict:
    """The settings Outpost's drive launches a ritual session under, for *workspace*, from the real launch profile.

    ``ClaudeCodeHarness.session_start`` renders ``RITUAL_PROFILE`` exactly as ``trailhead session-start --role
    ritual`` does for a launch. The fence names a throwaway home, lore, cache and camp state, and Outpost's
    state is *outpost_state*, so the whole-state-folder deny lands on the temp Outpost under test. The user's own
    settings exclude nothing, and the e2e session reads only these launch settings (``--setting-sources project``),
    so the exclusion of the pinned ``outpost-gateway`` shim ``install_shims`` writes under *root* is the one the
    launch itself carries.
    """
    from trailhead.harness.claude_code import ClaudeCodeHarness
    from trailhead.launch_profile import RITUAL_PROFILE, resolve_profile
    from trailhead.ranger.config import HubConfig, write_hub_config

    fence = root.resolve() / "launch-fence"
    env = {
        "HOME": str(fence / "home"),
        "XDG_CONFIG_HOME": str(fence / "config"),
        "XDG_STATE_HOME": str(fence / "state"),
        "XDG_CACHE_HOME": str(fence / "cache"),
        "LORE_STATE_DIR": str(fence / "lore-state"),
        "OUTPOST_STATE_DIR": str(outpost_state),
        "CAMP_STATE_DIR": str(workspace.resolve().parents[2]),
        "TRAILHEAD_STATE_DIR": str(root / "trailhead-state"),
        "PATH": os.environ.get("PATH", ""),
    }
    user_settings = Path(env["HOME"]) / ".claude"
    user_settings.mkdir(parents=True, exist_ok=True)
    write_hub_config(HubConfig(name="Hub A", account="a", launch_account="default"), env=env, platform="linux")
    (Path(env["LORE_STATE_DIR"]) / "vaults" / "work").mkdir(parents=True, exist_ok=True)
    resolved = resolve_profile(
        RITUAL_PROFILE, "a", env=env, platform="linux", workspace=str(workspace.resolve()), vault="work"
    )
    (user_settings / "settings.json").write_text(
        json.dumps(
            {
                "sandbox": {
                    "enabled": True,
                    "filesystem": {"allowWrite": [".", *(str(p) for p in resolved.writable[1:])]},
                    "network": {"allowedDomains": ["api.anthropic.com"]},
                }
            }
        )
    )
    start = ClaudeCodeHarness().session_start(
        resolved, workspace.resolve(), None, interactive=False, account=None, env=env, platform="linux",
        session_dir=outpost_state.resolve() / "sessions" / "launch-probe",
    )
    value = start.argv[start.argv.index("--settings") + 1]
    named = [f for f in start.files if f.path == value]
    return json.loads(named[0].content if named else value)
