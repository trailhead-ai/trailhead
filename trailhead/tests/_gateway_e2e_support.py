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

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from trailhead.install import build_cli_shims, resolve_cli_tools

from ._gateway_support import (
    REPO_ROOT,
    free_port,
    gateway_runner_cmd,
    register_port_interpreter,
    register_runner_cmd,
    runner_env,
)
from ._harness_process_support import make_fake_harness

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

    def registry_workspaces(self) -> list[tuple[str, str | None]]:
        """``(session_id, workspace)`` for every live workspace-session client."""
        db = sqlite3.connect(f"file:{self.state / 'state.db'}?mode=ro", uri=True)
        try:
            return db.execute(
                "SELECT session_id, workspace FROM broker_clients "
                "WHERE principal_kind = 'workspace-session' AND revoked_at IS NULL ORDER BY session_id"
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
def real_outpost(root: Path, camp: Camp) -> Iterator[RealOutpost]:
    """A real Outpost: ``node --import tsx server/index.ts`` on a free port, temp state, temp camp layout.

    Waits for ``/health`` and the registration authority file, failing early if the process exits.
    """
    checkout = outpost_checkout()
    port, sites_port = free_port(), free_port()
    state = root / "outpost-state"
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
    }
    with log.open("wb") as sink:
        proc = subprocess.Popen(
            ["node", "--import", "tsx", "server/index.ts"], cwd=checkout, env=env, stdout=sink, stderr=sink
        )
    try:
        deadline = time.monotonic() + 60
        while not (_health_ok(port) and (state / "registration" / "credential").exists()):
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


def step_env(root: Path, outpost: RealOutpost) -> dict[str, str]:
    """The environment the trailhead side runs in: where Outpost's state is, and a scratch trailhead state."""
    return runner_env(outpost.state, trailhead_state=root / "trailhead-state")


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


def register_cmd(port: int) -> str:
    return register_runner_cmd(port, ["gateway", "register"])


def list_cmd(port: int, scratch: Path) -> str:
    return gateway_runner_cmd(port, scratch / "env-after.json", ["run", "list"])


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
    """The bin dir holding an ``outpost-gateway`` aimed at *outpost*.

    The trailhead state dir is what the real install writes (``build_cli_shims`` with the Outpost
    tool enabled): the real ``hooks/gateway-register`` finds the ``trailhead-gateway-register`` shim
    there. Both it and the production gateway command fix Outpost's port, so the registration shim
    is pinned to ``register_port_interpreter`` and the gateway command is this test's own wrapper
    around ``main(port=...)``.
    """
    env = {"TRAILHEAD_STATE_DIR": str(root / "trailhead-state"), "HOME": str(root / "outpost-home")}
    with mock.patch("trailhead.pathint.sys.executable", str(register_port_interpreter(root / "interp", outpost.port))):
        build_cli_shims(
            resolve_cli_tools({"camp": True, "lore": True, "portage": True}), env=env, outpost_enabled=True
        )
    bin_dir = root / "shims"
    bin_dir.mkdir()
    gateway = bin_dir / "outpost-gateway"
    gateway.write_text(
        "#!/bin/sh\n"
        f'PYTHONPATH="{REPO_ROOT}" exec "{sys.executable}" -c '
        "'import sys; from trailhead.gateway.command import main; "
        f'sys.exit(main(sys.argv[1:], port={outpost.port}))\' "$@"\n'
    )
    gateway.chmod(0o755)
    return bin_dir


def sandbox_steps(state: Path, port: int, other_port: int, gateway: Path) -> dict[str, str]:
    """The Bash commands the sandboxed session runs, one tool call each, exactly as written.

    None prints a credential: the credential read is redirected to /dev/null, so only its exit status
    and error reach the result, and no step echoes the proxy variable. Each is a single simple command:
    a chained one would need an approval a ``-p`` run cannot give.
    """
    probe = 'curl -sS --max-time 10 --noproxy "" --proxy "$HTTP_PROXY" -w "\\nhttp=%{http_code}\\n"'
    post = "-X POST -H 'content-type: application/json' -d '{\"pid\":1,\"start_time\":1}'"
    return {
        "credential_read": f'cat "{state}"/sessions/*/gateway-client.json > /dev/null',
        "run_list": f"{gateway} run list",
        "command_route": f"{probe} http://127.0.0.1:{port}/gateway/runs",
        "other_loopback_port": f"{probe} http://127.0.0.1:{other_port}/",
        "register_without_authority": f"{probe} {post} http://127.0.0.1:{port}/gateway/sessions",
    }


def session_prompt(steps: dict[str, str]) -> str:
    numbered = "\n".join(f"{i}. {cmd}" for i, cmd in enumerate(steps.values(), 1))
    return (
        "You are running a scripted sandbox probe. Run each numbered Bash command below EXACTLY as written, "
        "one Bash tool call per item, in order. Do not modify, combine, retry or work around any command, "
        "never ask to run anything outside the sandbox, and do not investigate failures. When done, reply "
        f"with the single word done.\n\n{numbered}\n"
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
) -> Session:
    """One real ``claude -p`` session in *cwd*, its Bash sandboxed by *settings*; waits for the process to exit."""
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
    cmd.append(session_prompt(steps))
    stream = root / f"session-{label}.jsonl"
    session_env = {**env, "PATH": f"{shim_bin}{os.pathsep}{env['PATH']}"}
    with stream.open("w") as out:
        done = subprocess.run(
            cmd,
            cwd=cwd,
            env=session_env,
            stdout=out,
            stderr=subprocess.PIPE,
            text=True,
            timeout=SESSION_TIMEOUT_SECONDS,
        )
    results = tool_results(stream.read_text())
    return Session({name: results[cmd] for name, cmd in steps.items() if cmd in results}, done.returncode, stream)


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


def check_credential_file_read_is_refused(facts: Facts, session: Session) -> None:  # AC22
    assert facts.credentials, "no credential file exists outside the sandbox, so a refusal proves nothing"
    out = _out(session, "credential_read")
    assert out.startswith("Exit code") and "No such file or directory" in out, f"the read was not refused: {out!r}"


def check_run_list_prints_the_empty_list(facts: Facts, session: Session) -> None:  # AC23, AC40
    out = _out(session, "run_list").strip()
    assert out == f"No runs in workspace {GROUP}/{W1}", f"run list printed {out!r}"


def check_proxied_curl_reaches_the_command_route(facts: Facts, session: Session) -> None:  # AC100
    out = _out(session, "command_route")
    assert "http=401" in out and "credential_missing" in out, (
        f"the route did not answer 401 credential_missing: {out!r}"
    )


def check_proxied_curl_to_another_loopback_port_is_refused(facts: Facts, session: Session) -> None:  # AC100
    out = _out(session, "other_loopback_port")
    assert "http=403" in out, f"the proxy did not refuse the other port: {out!r}"


def check_registering_without_the_authority_is_refused(facts: Facts, session: Session) -> None:  # AC16, AC17, AC18
    out = _out(session, "register_without_authority")
    assert "http=401" in out and "registration_authority_required" in out, f"registration was not refused: {out!r}"


CHECKS = [
    check_hook_registered_the_session_for_w1,
    check_credential_file_read_is_refused,
    check_run_list_prints_the_empty_list,
    check_proxied_curl_reaches_the_command_route,
    check_proxied_curl_to_another_loopback_port_is_refused,
    check_registering_without_the_authority_is_refused,
]
