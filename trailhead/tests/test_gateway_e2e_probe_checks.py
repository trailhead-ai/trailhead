"""The denial probes of the gateway end-to-end test, run without a real sandbox.

The real probes need Claude Code's sandbox and run from a host terminal (see
``test_gateway_e2e_andromeda.py``). Here each probe command is run as written, once where the thing it reaches
is readable (as it would be with no sandbox) and once where it is hidden the way the sandbox hides a denied
folder (absent), and each check must fail on the first and pass on the second. A check that cannot fail would
let a session that read the credential look refused.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ._gateway_e2e_support import (
    GROUP,
    LATE_FILE,
    W1,
    READ_CONTROL,
    READ_CONTROL_TEXT,
    DenialFacts,
    Facts,
    LateFileActor,
    RealOutpost,
    Session,
    check_a_shadowed_gateway_cannot_read_the_credential,
    check_nothing_is_registered_before_the_first_gateway_call,
    check_the_auth_header_helper_prints_no_token,
    check_the_launched_session_reaches_its_own_workspace_runs,
    check_no_cookie_is_handed_to_a_page_request,
    check_read_is_refused,
    check_the_late_side_file_is_refused,
    check_the_read_control_is_readable,
    check_the_read_tool_is_denied,
    denial_facts,
    denial_steps,
    dotfiles_checkout,
    launched_session_settings,
    meeting_folder,
    read_results,
    read_tool_paths,
    sandbox_steps,
    write_late_file_probe,
    write_path_shadow,
)

OTHER = "ws-4242-99"
SECRET_BYTES = "client-secret-VISIBLE-IF-READ"


def _state(root: Path, *, hidden: bool) -> Path:
    """Outpost's state with a credential in each folder; *hidden* leaves the folders out, as the sandbox's mask does."""
    state = root / ("hidden" if hidden else "visible") / "outpost-state"
    state.mkdir(parents=True)
    if not hidden:
        for rel in (
            "operator/credential",
            "registration/credential",
            f"sessions/{OTHER}/gateway-client.json",
            "db/state.db",
        ):
            (state / rel).parent.mkdir(parents=True, exist_ok=True)
            (state / rel).write_text(SECRET_BYTES)
    return state


def _as_a_bash_tool_result(done: subprocess.CompletedProcess[str]) -> str:
    return done.stdout if done.returncode == 0 else f"Exit code {done.returncode}\n{done.stderr}"


def _run(command: str, *, env: dict[str, str] | None = None, cwd: Path | None = None) -> str:
    done = subprocess.run(["sh", "-c", command], capture_output=True, text=True, timeout=60, env=env, cwd=cwd)
    return _as_a_bash_tool_result(done)


def _session(step: str, command: str, *, env: dict[str, str] | None = None) -> Session:
    return Session({step: _run(command, env=env)}, 0, Path("/dev/null"))


def _idle_actor(state: Path) -> LateFileActor:
    return LateFileActor(state / "db", state / "rendezvous")


@pytest.mark.parametrize("step", ["operator_read", "registration_read", "other_session_read", "db_read"])
def test_a_denied_read_check_fails_when_the_file_can_be_read_and_passes_when_it_is_hidden(tmp_path, step):
    check = check_read_is_refused(step)
    visible, hidden = _state(tmp_path, hidden=False), _state(tmp_path, hidden=True)
    steps_visible = denial_steps(visible, OTHER, visible / "rd", 1)
    steps_hidden = denial_steps(hidden, OTHER, hidden / "rd", 1)
    facts = denial_facts(visible, OTHER, _idle_actor(visible))

    with pytest.raises(AssertionError, match="was not refused"):
        check(facts, _session(step, steps_visible[step]))
    check(facts, _session(step, steps_hidden[step]))


def test_a_denied_read_check_does_not_take_a_permission_error_for_the_sandbox_hiding_the_file(tmp_path):
    visible = _state(tmp_path, hidden=False)
    facts = denial_facts(visible, OTHER, _idle_actor(visible))
    with pytest.raises(AssertionError, match="was not refused"):
        check_read_is_refused("db_read")(
            facts, Session({"db_read": "Exit code 1\ncat: state.db: Permission denied"}, 0, Path("/dev/null"))
        )


def test_a_denied_read_check_says_a_refusal_proves_nothing_when_the_file_is_not_there_outside_the_sandbox(tmp_path):
    hidden = _state(tmp_path, hidden=True)
    steps = denial_steps(hidden, OTHER, hidden / "rd", 1)
    facts = denial_facts(hidden, OTHER, _idle_actor(hidden))
    with pytest.raises(AssertionError, match="proves nothing"):
        check_read_is_refused("operator_read")(facts, _session("operator_read", steps["operator_read"]))


def _run_late_file_probe(
    state_for_probe: Path, state_for_actor: Path, rendezvous: Path
) -> tuple[Session, LateFileActor]:
    write_late_file_probe(rendezvous)
    actor = LateFileActor(state_for_actor / "db", rendezvous, timeout=30)
    thread = threading.Thread(target=actor)
    thread.start()
    command = denial_steps(state_for_probe, OTHER, rendezvous, 1)["late_file"]
    out = _run(command)
    thread.join()
    return Session({"late_file": out}, 0, Path("/dev/null")), actor


def test_the_late_side_file_probe_reads_a_file_created_after_it_started_unless_its_folder_is_hidden(tmp_path):
    visible, hidden = _state(tmp_path, hidden=False), _state(tmp_path, hidden=True)
    readable, actor = _run_late_file_probe(visible, visible, tmp_path / "rd-visible")
    assert actor.created and (visible / "db" / LATE_FILE).read_bytes() == b"LATE-WAL-BYTES"
    assert "late_file=readable" in readable.outputs["late_file"]
    with pytest.raises(AssertionError, match="was not refused"):
        check_the_late_side_file_is_refused(denial_facts(visible, OTHER, actor), readable)

    # The sandbox hides the folder from the probe while the test, outside it, still creates the file there.
    (hidden / "db").mkdir()
    masked_state = hidden.parent / "masked-state"
    masked_state.mkdir()
    masked, actor = _run_late_file_probe(masked_state, hidden, tmp_path / "rd-masked")
    assert actor.created and (hidden / "db" / LATE_FILE).exists()
    assert "late_file=refused" in masked.outputs["late_file"]
    check_the_late_side_file_is_refused(denial_facts(hidden, OTHER, actor), masked)


def test_the_late_side_file_check_proves_nothing_when_the_file_was_never_created(tmp_path):
    absent = tmp_path / "masked"
    absent.mkdir()
    out = "late_file=refused:No such file or directory\nlisting=refused:No such file or directory\n"
    with pytest.raises(AssertionError, match="proves nothing"):
        check_the_late_side_file_is_refused(DenialFacts({}, False), Session({"late_file": out}, 0, Path("/dev/null")))


def _listing_session(listing: str) -> Session:
    out = f"late_file=refused:No such file or directory\n{listing}\n"
    return Session({"late_file": out}, 0, Path("/dev/null"))


def test_an_empty_listing_of_the_masked_folder_passes_on_linux_and_fails_elsewhere():
    facts = DenialFacts({}, True)
    check_the_late_side_file_is_refused(facts, _listing_session("listing="), platform="linux")
    with pytest.raises(AssertionError, match="listed"):
        check_the_late_side_file_is_refused(facts, _listing_session("listing="), platform="darwin")


@pytest.mark.parametrize("platform", ["linux", "darwin"])
@pytest.mark.parametrize("listing", ["listing=state.db", f"listing={LATE_FILE}", f"listing=a,state.db,{LATE_FILE}", "listing=refused:state.db"])
def test_a_listing_that_names_the_database_or_the_late_file_fails_on_every_platform(platform, listing):
    with pytest.raises(AssertionError, match="listed"):
        check_the_late_side_file_is_refused(DenialFacts({}, True), _listing_session(listing), platform=platform)


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_a_refused_listing_passes_on_every_platform(platform):
    session = _listing_session("listing=refused:Permission denied")
    check_the_late_side_file_is_refused(DenialFacts({}, True), session, platform=platform)


def test_the_meeting_folder_is_a_fresh_writable_folder_inside_the_workspace_even_through_a_symlink(tmp_path):
    real = tmp_path / "real-ws"
    real.mkdir()
    link = tmp_path / "link-ws"
    link.symlink_to(real)
    first, second = meeting_folder(link), meeting_folder(link)
    assert first != second
    for folder in (first, second):
        assert folder.is_dir() and folder == folder.resolve() and folder.is_relative_to(real.resolve())
        (folder / "probe").write_text("x")


def test_a_path_shadowed_gateway_check_fails_when_the_shadow_read_the_credential_and_passes_when_it_could_not(tmp_path):
    visible, hidden = _state(tmp_path, hidden=False), _state(tmp_path, hidden=True)
    facts = DenialFacts({}, False)
    shadow_dir = write_path_shadow(tmp_path / "shadow", visible).parent
    env = {**os.environ, "PATH": f"{shadow_dir}:{os.environ['PATH']}"}
    ran_readable = _session("path_shadow", denial_steps(visible, OTHER, tmp_path, 1)["path_shadow"], env=env)
    assert "shadow-ran" in ran_readable.outputs["path_shadow"]
    with pytest.raises(AssertionError, match="read the credential"):
        check_a_shadowed_gateway_cannot_read_the_credential(facts, ran_readable)

    shadow_dir = write_path_shadow(tmp_path / "shadow-hidden", hidden).parent
    env = {**os.environ, "PATH": f"{shadow_dir}:{os.environ['PATH']}"}
    check_a_shadowed_gateway_cannot_read_the_credential(
        facts, _session("path_shadow", denial_steps(hidden, OTHER, tmp_path, 1)["path_shadow"], env=env)
    )

    with pytest.raises(AssertionError, match="did not run"):
        check_a_shadowed_gateway_cannot_read_the_credential(
            facts, Session({"path_shadow": "outpost-gateway: not found"}, 0, Path("/dev/null"))
        )


class _Page(BaseHTTPRequestHandler):
    sets_cookie = False

    def do_GET(self):
        self.send_response(200)
        if type(self).sets_cookie:
            self.send_header("Set-Cookie", "outpost_session=abc; HttpOnly; SameSite=Strict; Path=/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.mark.parametrize(("sets_cookie", "handed_out"), [(True, True), (False, False)])
def test_the_cookie_bootstrap_check_fails_only_when_a_page_request_is_handed_a_cookie(
    tmp_path, sets_cookie, handed_out
):
    handler = type("Page", (_Page,), {"sets_cookie": sets_cookie})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        command = denial_steps(tmp_path, OTHER, tmp_path, server.server_address[1])["cookie_bootstrap"]
        env = {**os.environ, "HTTP_PROXY": f"http://127.0.0.1:{server.server_address[1]}"}
        session = _session("cookie_bootstrap", command, env=env)
    finally:
        server.shutdown()
        server.server_close()
    assert "http=200" in session.outputs["cookie_bootstrap"], session.outputs
    if handed_out:
        with pytest.raises(AssertionError, match="cookie"):
            check_no_cookie_is_handed_to_a_page_request(DenialFacts({}, False), session)
    else:
        check_no_cookie_is_handed_to_a_page_request(DenialFacts({}, False), session)


def _stream(*calls: tuple[str, str, str, bool]) -> str:
    """A ``claude -p`` stream with one Read tool call per ``(id, path, result text, is_error)``."""
    lines = []
    for tool_id, path, text, is_error in calls:
        call = {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": path}}
        lines.append(json.dumps({"message": {"content": [call]}}))
        result = {"type": "tool_result", "tool_use_id": tool_id, "content": text, "is_error": is_error}
        lines.append(json.dumps({"message": {"content": [result]}}))
    return "\n".join(lines)


DENIED = "File is in a directory that is denied by your permission settings."


def test_the_read_results_are_keyed_by_path_with_the_error_flag():
    results = read_results(
        _stream(("a", "/s/operator/credential", DENIED, True), ("b", f"/s/{READ_CONTROL}", READ_CONTROL_TEXT, False))
    )
    assert results == {"/s/operator/credential": (DENIED, True), f"/s/{READ_CONTROL}": (READ_CONTROL_TEXT, False)}


def test_the_read_tool_check_passes_a_denial_and_fails_a_file_that_was_read_or_a_call_that_was_not_made(tmp_path):
    paths = read_tool_paths(tmp_path, OTHER, tmp_path / 'ws')
    check = check_the_read_tool_is_denied("operator_credential")
    check(read_results(_stream(("a", paths["operator_credential"], DENIED, True))), paths)

    with pytest.raises(AssertionError, match="was read"):
        check(read_results(_stream(("a", paths["operator_credential"], SECRET_BYTES, False))), paths)
    with pytest.raises(AssertionError, match="was not asked"):
        check({}, paths)
    with pytest.raises(AssertionError, match="not by a permission denial"):
        check(read_results(_stream(("a", paths["operator_credential"], "File does not exist.", True))), paths)


def test_the_read_control_check_needs_the_permitted_file_to_come_back_so_a_denial_means_a_rule_not_a_missing_grant(
    tmp_path
):
    paths = read_tool_paths(tmp_path, OTHER, tmp_path / 'ws')
    check_the_read_control_is_readable(read_results(_stream(("c", paths["control"], READ_CONTROL_TEXT, False))), paths)
    with pytest.raises(AssertionError, match="control"):
        check_the_read_control_is_readable(
            read_results(_stream(("c", paths["control"], "requires permission", True))), paths
        )


@pytest.mark.parametrize(
    "rel",
    [
        "operator/credential",
        "registration/credential",
        f"sessions/{OTHER}/gateway-client.json",
        "db/state.db",
        f"db/{LATE_FILE}",
    ],
)
def test_the_launched_session_settings_the_end_to_end_runs_under_deny_each_probed_path_to_bash_and_the_read_tool(
    tmp_path, rel
):
    state = tmp_path / "outpost-state"
    workspace = tmp_path / "camp" / "grp" / "worktrees" / "ws1"
    workspace.mkdir(parents=True)
    (workspace / "manifest.json").write_text("{}")
    settings = launched_session_settings(tmp_path / "launch", state, workspace)
    denied = settings["sandbox"]["filesystem"]["denyRead"]
    path = state.resolve() / rel
    assert any(path == Path(d) or Path(d) in path.parents for d in denied), denied
    assert f"Read(/{state.resolve()}/**)" in settings["permissions"]["deny"]
    assert not any(workspace / READ_CONTROL == Path(d) or Path(d) in (workspace / READ_CONTROL).parents for d in denied)


def test_a_real_outposts_database_is_found_where_that_outpost_keeps_it(tmp_path):
    """An Outpost from before the database folder keeps state.db beside its credential folders; the red runs use one."""
    folder_state, legacy_state = tmp_path / "folder", tmp_path / "legacy"
    (folder_state / "db").mkdir(parents=True)
    (folder_state / "db" / "state.db").write_text("")
    legacy_state.mkdir()
    (legacy_state / "state.db").write_text("")
    assert RealOutpost(1, folder_state, tmp_path / "log").db_path == folder_state / "db" / "state.db"
    assert RealOutpost(1, legacy_state, tmp_path / "log").db_path == legacy_state / "state.db"


def _fake_header_shim(directory: Path, body: str) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    shim = directory / "trailhead-gateway-auth-header"
    shim.write_text(f"#!/bin/sh\n{body}\n")
    shim.chmod(0o755)
    return {**os.environ, "PATH": f"{directory}:{os.environ['PATH']}"}


@pytest.mark.parametrize(
    ("shim_body", "passes"),
    [
        ("echo '{}'", True),
        ("echo 'outpost-gateway: no credential' >&2; echo '{}'", True),
        ("echo '{\"Authorization\": \"Bearer abc.def\"}'", False),
        ("echo '{}'; echo 'Bearer abc.def'", False),
        ("exit 0", False),
    ],
)
def test_the_auth_header_probe_runs_the_helper_by_name_and_its_check_passes_only_when_it_prints_an_empty_object(
    tmp_path, shim_body, passes
):
    env = _fake_header_shim(tmp_path / "bin", shim_body)
    session = _session("auth_header", denial_steps(tmp_path, OTHER, tmp_path, 1)["auth_header"], env=env)
    if passes:
        check_the_auth_header_helper_prints_no_token(DenialFacts({}, False), session)
    else:
        with pytest.raises(AssertionError):
            check_the_auth_header_helper_prints_no_token(DenialFacts({}, False), session)


def test_the_auth_header_check_proves_nothing_when_the_helper_did_not_run(tmp_path):
    not_found = "Exit code 127\nsh: trailhead-gateway-auth-header: not found"
    session = Session({"auth_header": not_found}, 0, Path("/dev/null"))
    with pytest.raises(AssertionError, match="did not run"):
        check_the_auth_header_helper_prints_no_token(DenialFacts({}, False), session)
    with pytest.raises(AssertionError, match="was not run"):
        check_the_auth_header_helper_prints_no_token(DenialFacts({}, False), Session({}, 0, Path("/dev/null")))


@pytest.mark.parametrize(
    ("out", "passes"),
    [
        (f"No runs in workspace {GROUP}/{W1}\n", True),
        (f"No runs in workspace {GROUP}/{W1}", True),
        (f"No runs in workspace {GROUP}/ws-two\n", False),
        (f"No runs in workspace {GROUP}/{W1}-other\n", False),
        ("Exit code 2\noutpost-gateway: outpost-gateway is running inside the sandbox; run it on its own", False),
        ("Exit code 1\nBash command permission check failed", False),
        ("", False),
    ],
)
def test_the_launched_session_gateway_check_passes_only_when_the_shim_answers_for_its_own_workspace(out, passes):
    session = Session({"gateway_run_list": out}, 0, Path("/dev/null"))
    if passes:
        check_the_launched_session_reaches_its_own_workspace_runs(DenialFacts({}, False), session)
    else:
        with pytest.raises(AssertionError):
            check_the_launched_session_reaches_its_own_workspace_runs(DenialFacts({}, False), session)


def _launched(tmp_path):
    state = tmp_path / "outpost-state"
    workspace = tmp_path / "camp" / "grp" / "worktrees" / "ws1"
    workspace.mkdir(parents=True)
    (workspace / "manifest.json").write_text("{}")
    return launched_session_settings(tmp_path, state, workspace)


def test_the_launched_session_settings_let_the_ritual_run_the_pinned_gateway_shim_the_user_excludes(tmp_path):
    settings = _launched(tmp_path)
    shim = str(tmp_path / "trailhead-state" / "bin" / "outpost-gateway")
    header = str(tmp_path / "trailhead-state" / "bin" / "trailhead-gateway-auth-header")
    assert f"Bash({shim}:*)" in settings["permissions"]["allow"]
    denied = settings["permissions"]["deny"]
    assert f"Bash({shim})" not in denied and f"Bash({shim} *)" not in denied
    assert {f"Bash({header})", f"Bash({header} *)"} <= set(denied)


@pytest.mark.parametrize(
    "facts, says",
    [
        (Facts([("ws-1", 0o600)], []), "credential"),
        (Facts([], [("ws-1", f"{GROUP}/{W1}")]), "registry"),
    ],
    ids=["a-credential-file", "a-registry-entry"],
)
def test_the_before_the_first_call_check_fails_when_anything_is_registered(facts, says):
    with pytest.raises(AssertionError, match=says):
        check_nothing_is_registered_before_the_first_gateway_call(facts)


def test_the_before_the_first_call_check_passes_when_nothing_is_registered():
    check_nothing_is_registered_before_the_first_gateway_call(Facts([], []))


def test_an_empty_listing_that_ends_the_probe_output_passes_on_linux():
    """The session hands back a step's output without its final newline, so the listing line can be the last text."""
    session = Session({"late_file": "late_file=refused:No such file or directory\nlisting="}, 0, Path("/dev/null"))
    check_the_late_side_file_is_refused(DenialFacts({}, True), session, platform="linux")


def _template(checkout: Path) -> Path:
    template = checkout / "dot_claude" / "private_settings.json.tmpl"
    template.parent.mkdir(parents=True)
    template.write_text("{}")
    return checkout


def test_the_dotfiles_checkout_is_the_workspace_sibling_unless_one_is_named(tmp_path):
    repo = tmp_path / "ws" / "trailhead"
    sibling = _template(tmp_path / "ws" / "dotfiles")
    home = tmp_path / "home"
    _template(home / ".local/share/chezmoi")
    assert dotfiles_checkout(env={"HOME": str(home)}, repo_root=repo) == sibling
    named = _template(tmp_path / "named")
    assert dotfiles_checkout(env={"HOME": str(home), "TRAILHEAD_GATEWAY_E2E_DOTFILES": str(named)}, repo_root=repo) == named


def test_the_dotfiles_checkout_is_the_operators_chezmoi_source_outside_a_workspace(tmp_path):
    home = tmp_path / "home"
    chezmoi = _template(home / ".local/share/chezmoi")
    assert dotfiles_checkout(env={"HOME": str(home)}, repo_root=tmp_path / "lone" / "trailhead") == chezmoi


@pytest.mark.parametrize("step", ["command_route", "register_without_authority"])
def test_a_socket_probe_with_nothing_to_reach_exits_nonzero_and_reports_no_http_status(tmp_path, step):
    state = tmp_path / "state"
    (state / "gateway").mkdir(parents=True)
    command = sandbox_steps(state, 7001, tmp_path / "shims" / "outpost-gateway", 7000)[step]
    done = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=30)
    assert done.returncode != 0, done.stdout
    assert re.findall(r"http=(\d+)", done.stdout + done.stderr) == ["000"], done.stdout + done.stderr


def test_a_socket_probe_reports_the_status_and_body_the_gateway_answers(tmp_path):
    """Needs a unix socket, so like every socket test it runs from a host terminal, not a sandboxed session."""
    state = Path(tempfile.mkdtemp(prefix="gw-probe-"))
    try:
        (state / "gateway").mkdir()
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(state / "gateway" / "gateway.sock"))
        server.listen(1)
        seen: list[bytes] = []

        def answer() -> None:
            conn, _ = server.accept()
            seen.append(conn.recv(65536))
            conn.sendall(b'HTTP/1.1 401 Unauthorized\r\nContent-Length: 17\r\n\r\n{"reason":"nope"}')
            conn.close()

        threading.Thread(target=answer, daemon=True).start()
        command = sandbox_steps(state, 7001, tmp_path / "shims" / "outpost-gateway", 7000)["register_without_authority"]
        done = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=30)
        server.close()
    finally:
        shutil.rmtree(state, ignore_errors=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert '{"reason":"nope"}' in done.stdout and done.stdout.rstrip().endswith("http=401"), done.stdout
    assert seen and seen[0].startswith(b"POST /gateway/sessions HTTP/1.1\r\n"), seen
    assert b'{"pid":1,"start_time":1}' in seen[0], seen
