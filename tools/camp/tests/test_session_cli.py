"""CLI surface: camp list, camp new.

Exercised end-to-end through the REAL `cli/camp` binary (house convention), with
the harness seam and tmux replaced by stand-ins rather than by monkeypatching
camp's own code:

- a `FakeHarness` registered into trailhead's harness registry from a
  `sitecustomize.py` on PYTHONPATH, selected by `[harness] binary = "fakeharness"`
  in the group config. Its `session_enumerate` argv filters a file by the scope
  it was handed, so enumeration is a real subprocess reading real state and
  honoring the seam's subtree scoping.
- a `tmux` stub earlier on PATH that appends a registered session's id to that
  same file, so a session really does become visible to a really-executed
  enumeration, and suppressing the append (CAMP_FAKE_TMUX_NO_REGISTER)
  reproduces the never-registers failure without any in-process patching. It
  also logs every argv it was handed, so a test can read the exact `-s` name
  and `-c` directory camp asked tmux for.

Test contract:
- camp list: an unmanaged session present alongside a managed one is left
  alone; the state column needs no credential store; an enumeration outage is
  distinguished from a genuinely empty result; window counts differ between
  two running workspaces.
- camp new: bare `camp new` and `camp new --json` go through the door; the
  `--no-session` escape hatch skips it and prints only the workspace path
  (or `{"workspace": ...}` with `--json`) — there is no launch flavor left
  behind either flag.
- --no-wait (read only by --activate now) skips its boot-readiness wait and
  names `camp status <slug>`; --activate's work still runs to completion.
- Bare `camp new` output is the workspace path on stdout, with the door's
  outcome line on stderr.

`camp sessions` and `camp kill` redirect to `camp list` and `camp stop`
(`tools/camp/tests/test_verb_aliases.py`, `test_cli_surface.py`) — their live
dispatch is no longer reachable through this binary, so their coverage moved
there.
"""

from __future__ import annotations

import importlib.util

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
# This module is itself exec'd by path from test_statelessness.py, under a
# module name of its own, so it has no parent package and cannot use a relative
# import. Address the helpers by path for the same reason.
_HELPERS_SOURCE = Path(__file__).resolve().parent / "_helpers.py"
_helpers_spec = importlib.util.spec_from_file_location("camp_tests_helpers", _HELPERS_SOURCE)
assert _helpers_spec and _helpers_spec.loader, _HELPERS_SOURCE
_helpers = importlib.util.module_from_spec(_helpers_spec)
_helpers_spec.loader.exec_module(_helpers)
init_git_repo = _helpers.init_git_repo
run_camp = _helpers.run_camp

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_SITECUSTOMIZE = '''
"""Registers a fake harness so a camp CLI subprocess can launch and enumerate."""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import trailhead.harness as _registry
from trailhead.harness.base import SessionRecord
from trailhead.harness.claude_code import ClaudeCodeHarness

#: The enumeration subprocess. Prints the registered session rows, honoring the
#: seam's subtree scoping (a row is in scope when its cwd is EQUAL TO or UNDER
#: the scope path, on resolved paths) so the fake answers a scoped enumeration
#: the way the contract says a harness must.
#:
#: The sessions file is read from THIS PROCESS's own environment
#: (CAMP_FAKE_SESSIONS_FILE), not from argv — so the SAME script, run under
#: two different stores' bound environments, reads two different files. That
#: is what lets a store-scoped enumeration really differ per store rather
#: than every invocation reading whichever file the parent process happened
#: to have ambient. An ENUMERATE_FAIL marker beside that file makes just THIS
#: store's enumeration fail, without touching CAMP_FAKE_ENUMERATE (which is
#: read once, in the parent process, and so is the same for every store).
_ENUMERATE_FILTER = """
import os
import sys
from pathlib import Path

calls_file = os.environ.get("CAMP_FAKE_ENUMERATE_CALLS_FILE")
if calls_file:
    with open(calls_file, "a") as _calls_handle:
        print("1", file=_calls_handle)

rows_path = os.environ.get("CAMP_FAKE_SESSIONS_FILE")
scope = sys.argv[1]
scope_path = Path(scope).resolve() if scope else None
marker_dir = Path(rows_path).parent if rows_path else None
if marker_dir is not None and (marker_dir / "ENUMERATE_FAIL").exists():
    sys.exit(1)
if rows_path and Path(rows_path).exists():
    for line in Path(rows_path).read_text().splitlines():
        if not line.strip():
            continue
        session_id, cwd = line.split("\\t", 1)
        if scope_path is not None:
            resolved = Path(cwd).resolve()
            if resolved != scope_path and scope_path not in resolved.parents:
                continue
        print(line)
"""


class FakeHarness(ClaudeCodeHarness):
    """Concrete only where this slice's surface reads it."""

    name = "fakeharness"

    def session_launch_env_unset(self):
        return []

    def session_launch_env_set(self, account, *, env=None):
        """Bind CAMP_FAKE_SESSIONS_FILE to a per-account file alongside the
        real CLAUDE_CONFIG_DIR binding, so two groups declaring different
        accounts really do read and write two different fake session tables
        — the same way two accounts read and write two different real Claude
        Code config dirs.
        """
        binding = dict(super().session_launch_env_set(account, env=env))
        config_dir = binding.get("CLAUDE_CONFIG_DIR")
        if config_dir is not None:
            account_dir = Path(config_dir)
            account_dir.mkdir(parents=True, exist_ok=True)
            sessions_file = account_dir / "sessions.tsv"
            if not sessions_file.exists():
                sessions_file.write_text("", encoding="utf-8")
            binding["CAMP_FAKE_SESSIONS_FILE"] = str(sessions_file)
        return binding

    def session_transcripts(self, workspace=None, *, env=None):
        mode = os.environ.get("CAMP_FAKE_TRANSCRIPTS")
        if mode == "none":
            return None
        if mode == "raise":
            # The seam contract forbids this, which is exactly why camp is
            # tested against a harness that does it anyway.
            raise OSError("transcript store is unreadable")
        return super().session_transcripts(workspace, env=env)

    def session_resume(self, session_id):
        if os.environ.get("CAMP_FAKE_RESUME") == "none":
            return None
        return ["fake-resume", session_id]

    def session_enumerate(self, workspace=None):
        mode = os.environ.get("CAMP_FAKE_ENUMERATE", "ok")
        if mode == "none":
            return None
        if mode == "fail":
            return ["false"]
        if mode == "missing":
            return ["camp-fake-absent-binary"]
        return [
            sys.executable,
            "-c",
            _ENUMERATE_FILTER,
            str(workspace) if workspace is not None else "",
        ]

    def parse_session_list(self, output):
        records = []
        for line in output.splitlines():
            if not line.strip():
                continue
            session_id, cwd = line.split("\\t", 1)
            records.append(
                SessionRecord(
                    session_id=session_id,
                    cwd=Path(cwd),
                    kind="agent",
                    controllable=True,
                    name=None,
                    pid=None,
                    started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            )
        return records


_registry._HARNESSES[FakeHarness.name] = FakeHarness
# `_addressable_harnesses` always probes camp's baked-in default binary
# ("claude") alongside every configured group's own store — registering the
# fake under that canonical name too means the always-present default store
# resolves to the SAME controllable stand-in every declared no-account group
# already uses, so it dedupes against a no-account fakeharness group exactly
# as camp's own keying intends, rather than silently substituting a real,
# environment-dependent `claude` binary into this suite's total-failure
# scenarios.
_registry._HARNESSES["claude_code"] = FakeHarness
'''

_TMUX_STUB = '''#!/usr/bin/env python3
"""tmux stand-in: a tiny session table backed by two files.

`new-session` registers the session id for harness enumeration AND records the
pane's start command under the tmux name, so the ownership check `camp kill`
performs reads a command a real launch actually composed. `has-session`,
`list-panes`, and `kill-session` answer from that same table, which is what
lets a launch/kill pair be exercised end to end without a real tmux.

Three failure modes are reachable by environment variable, each reproducing one
thing a real tmux does: never registering a launch (CAMP_FAKE_TMUX_NO_REGISTER),
surviving its own kill (CAMP_FAKE_TMUX_UNDEAD), and not answering at all
(CAMP_FAKE_TMUX_HANG, which sleeps past every caller's timeout).
"""
import json
import os
import sys
import time

args = sys.argv[1:]
argv_log = os.environ.get("CAMP_FAKE_TMUX_ARGV_FILE")
if argv_log:
    with open(argv_log, "a") as handle:
        handle.write("\\t".join(args) + "\\n")

if os.environ.get("CAMP_FAKE_TMUX_HANG") and args and args[0] != "new-session":
    time.sleep(float(os.environ["CAMP_FAKE_TMUX_HANG"]))
    sys.exit(0)

_TABLE = os.environ.get("CAMP_FAKE_TMUX_TABLE_FILE")


def _table():
    if not _TABLE or not os.path.exists(_TABLE):
        return {}
    with open(_TABLE) as handle:
        return json.load(handle)


def _write(table):
    with open(_TABLE, "w") as handle:
        json.dump(table, handle)


def _target():
    for i, arg in enumerate(args):
        if arg == "-t" and i + 1 < len(args):
            return args[i + 1].lstrip("=")
    return None


if args and args[0] == "new-session" and not os.environ.get("CAMP_FAKE_TMUX_NO_REGISTER"):
    launch_dir = ""
    name = ""
    pane_at = len(args)
    for i, arg in enumerate(args):
        if arg == "-c" and i + 1 < len(args):
            launch_dir = args[i + 1]
            pane_at = i + 2
        if arg == "-s" and i + 1 < len(args):
            name = args[i + 1]
    with open(os.environ["CAMP_FAKE_SESSIONS_FILE"], "a") as handle:
        handle.write(f"{args[-1]}\\t{launch_dir}\\n")
    if _TABLE:
        table = _table()
        table[name] = " ".join(args[pane_at:])
        _write(table)
elif args and args[0] == "has-session":
    sys.exit(0 if _target() in _table() else 1)
elif args and args[0] == "list-panes":
    table = _table()
    name = _target()
    if name not in table:
        sys.exit(1)
    print(table[name])
elif args and args[0] == "kill-session":
    table = _table()
    if not os.environ.get("CAMP_FAKE_TMUX_UNDEAD"):
        pane = table.pop(_target(), None)
        _write(table)
        # Killing the tmux session hangs up the pane, so the harness process it
        # was running stops enumerating too. The pane command ends in the
        # session id, which is what ties the two files together.
        if pane:
            dead = pane.split()[-1]
            rows_path = os.environ["CAMP_FAKE_SESSIONS_FILE"]
            with open(rows_path) as handle:
                rows = [r for r in handle.read().splitlines() if r.split("\\t")[0] != dead]
            with open(rows_path, "w") as handle:
                handle.write("".join(f"{r}\\n" for r in rows))
elif args and args[0] == "list-sessions":
    fail_mode = os.environ.get("CAMP_FAKE_TMUX_LIST_SESSIONS_FAIL")
    if fail_mode == "no-server":
        sys.stderr.write("error connecting to /tmp/fake (No such file or directory)\\n")
        sys.exit(1)
    if fail_mode == "unsafe-permissions":
        sys.stderr.write("directory /tmp/fake has unsafe permissions\\n")
        sys.exit(1)
    table = _table()
    windows_path = os.environ.get("CAMP_FAKE_TMUX_WINDOWS_FILE")
    windows = {}
    if windows_path and os.path.exists(windows_path):
        with open(windows_path) as handle:
            windows = json.load(handle)
    for name in table:
        count = windows.get(name, 1)
        # Window count then activity first, name as the remainder,
        # "|"-delimited to match what the real Tmux.list_sessions() seam
        # asks tmux to format
        # (`#{session_windows}|#{session_activity}|#{session_name}`) and
        # parses on the FIRST TWO "|"s — a session name may legally carry
        # one of its own, so neither leading field is ever read out of the
        # name's tail. The activity value itself is a fixed stand-in
        # (nothing in this stub tracks real per-session activity).
        print(f"{count}|1700000000|{name}")
'''


def _set_harness_binary(config_dir: Path, group_name: str, binary: str) -> None:
    """Append a [harness] block naming *binary* to an authored group config."""
    path = config_dir / "groups" / f"{group_name}.toml"
    path.write_text(
        path.read_text(encoding="utf-8") + f'\n[harness]\nbinary = "{binary}"\n',
        encoding="utf-8",
    )


@pytest.fixture()
def cli_env(tmp_path: Path):
    config_dir = tmp_path / "camp-config"
    (config_dir / "groups").mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "camp-state"
    state_dir.mkdir(parents=True, exist_ok=True)

    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    init_git_repo(repo_a, origin=True)
    init_git_repo(repo_b, origin=True)

    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    (shim_dir / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    tmux = bin_dir / "tmux"
    tmux.write_text(_TMUX_STUB, encoding="utf-8")
    tmux.chmod(0o755)

    sessions_file = tmp_path / "sessions.tsv"
    sessions_file.write_text("", encoding="utf-8")
    tmux_argv_file = tmp_path / "tmux-argv.tsv"
    tmux_argv_file.write_text("", encoding="utf-8")
    tmux_table_file = tmp_path / "tmux-table.json"
    tmux_table_file.write_text("{}", encoding="utf-8")

    env = {**os.environ}
    env["CAMP_CONFIG_DIR"] = str(config_dir)
    env["CAMP_STATE_DIR"] = str(state_dir)
    # The shipped confirmation budget is sized for a cold `claude` boot on a
    # loaded machine. The harness here is a stub that answers immediately, so
    # a launch that is going to confirm does so on the first poll or two, and
    # every test that drives one which CANNOT confirm would otherwise sit out
    # the full budget in real seconds to observe a refusal it is asserting.
    # Well above what the stub needs, far below what waiting costs.
    env["CAMP_TEST_CONFIRM_TIMEOUT_SECONDS"] = "5"
    # The tmux confirmation budget is sized for a busy tmux server. The fake
    # tmux here is a local script, so the only tests that spend it are the
    # ones deliberately driving a tmux that hangs — sitting out the full
    # budget to observe the outcome it asserts. A margin over what the fake
    # needs, not a measured floor: raise it first if these ever go flaky
    # under heavy parallelism.
    env["CAMP_TEST_TMUX_TIMEOUT_SECONDS"] = "2"
    env["CAMP_FAKE_SESSIONS_FILE"] = str(sessions_file)
    env["TRAILHEAD_CLAUDE_DIR"] = str(tmp_path / "claude")
    env["CAMP_FAKE_TMUX_ARGV_FILE"] = str(tmux_argv_file)
    env["CAMP_FAKE_TMUX_TABLE_FILE"] = str(tmux_table_file)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(shim_dir), str(_REPO_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    env.pop("CAMP_SHELL_INTEGRATION", None)

    for name, binary, repo in (
        ("mygroup", "fakeharness", repo_a),
        ("badgroup", "nosuchharness", repo_b),
    ):
        # Authored in-process: the fixture wants the group on disk, not the
        # fact that a separate interpreter wrote it. Every camp call a test
        # then makes — the thing under test here — is still a real subprocess.
        result = run_camp(["group", name, "--member", f"member={repo}"], env=env)
        assert result.returncode == 0, f"group authoring failed: {result.stderr}"
        _set_harness_binary(config_dir, name, binary)

    return {
        "env": env,
        "config_dir": config_dir,
        "sessions_file": sessions_file,
        "state_dir": state_dir,
        "tmux_argv_file": tmux_argv_file,
        "tmux_table_file": tmux_table_file,
        "tmp_path": tmp_path,
    }


def _camp(cli_env, *args, extra_env=None, cwd=None):
    env = {**cli_env["env"]}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
    )


def _new_workspace(cli_env, slug: str, group: str = "mygroup"):
    """Seed a workspace for a test that needs one to exist — not `camp new`'s
    own door dispatch, which every other caller of this helper (`camp
    list`, `test_statelessness.py`'s transfer-probe walk) then drives its
    own flow against. `--no-session` keeps this helper from also
    creating the workspace's tmux session, which would otherwise show up as
    an extra, untracked session alongside whatever the calling test starts
    itself."""
    result = _camp(cli_env, "new", slug, "--group", group, "--no-session")
    assert result.returncode == 0, result.stderr
    _wait_for_workspace_ready(cli_env, slug, group=group)
    return result.stdout.strip()


def _workspace_launch_dir(cli_env, slug: str) -> Path:
    """The directory a slug launch resolves to.

    Used by `test_statelessness.py`'s transfer-probe walk, which needs a real
    workspace on disk for `camp transfer-probe --slug <name>` to answer about.
    """
    return Path(_new_workspace(cli_env, slug)).resolve()




def _tmux_argv(cli_env) -> list[list[str]]:
    """The argv of every tmux invocation camp made, in order."""
    lines = cli_env["tmux_argv_file"].read_text(encoding="utf-8").splitlines()
    return [line.split("\t") for line in lines if line]




#: Two distinct, well-formed session ids. Real uuids, because the 8-character
#: prefix form is part of the addressing contract and a short id would let a
#: prefix test pass without exercising it.
_UUID_A = "aaaaaaaa-1111-4111-8111-111111111111"
_UUID_B = "bbbbbbbb-2222-4222-8222-222222222222"


















def _wait_for_workspace_ready(
    cli_env, slug: str, *, group: str = "mygroup", timeout: float = 20.0
) -> None:
    """Block until `camp status` reports every member of `slug` boot-ready.

    `camp new` seeds a workspace synchronously but provisions it via a detached
    background process — its own docstring says as much: "provisioning is
    async (check it with `camp status <slug>`)". A statelessness assertion
    that snapshots state right after `camp new` returns can still have that
    provisioner's writes land later, attributed to whatever runs next. Polling
    `camp status` — which reads the same persisted manifest the provisioner
    itself writes (exit 0 all ready, 2 some pending, 3 any failed) — can't be
    fooled by a provisioner merely descheduled under load the way a
    filesystem quiet-period heuristic can: a descheduled provisioner still
    reports pending, never ready-by-omission.
    """
    deadline = time.monotonic() + timeout
    while True:
        result = _camp(cli_env, "status", "--group", group, "--name", slug)
        if result.returncode == 0:
            return
        assert result.returncode == 2, (
            f"workspace {slug!r} failed to provision: {result.stdout}{result.stderr}"
        )
        assert time.monotonic() < deadline, (
            f"workspace {slug!r} did not finish provisioning within {timeout}s"
        )
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# camp new — the door path's own JSON, with no launch flavor behind it
# ---------------------------------------------------------------------------


def test_camp_new_json_without_launch_succeeds(cli_env) -> None:
    """`--json` no longer requires `--launch` — the door's own object is the
    machine answer for `camp new` itself, on the default (door) path."""
    result = _camp(cli_env, "new", "feat-n", "--group", "mygroup", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["outcome"] == "created"
    assert payload["slug"] == "feat-n"
    assert payload["workspace_path"].endswith("/feat-n")


def test_bare_camp_new_output_is_unchanged(cli_env) -> None:
    """Regression pin: stdout stays exactly the path line — the door's outcome
    line is appended to stderr instead of perturbing it."""
    result = _camp(cli_env, "new", "feat-o", "--group", "mygroup")

    assert result.returncode == 0, result.stderr
    ws_dir = Path(cli_env["state_dir"]) / "mygroup" / "worktrees" / "feat-o"
    assert result.stdout == f"{ws_dir}\n"
    assert result.stderr == (
        "camp new: created workspace 'feat-o' — provisioning in the background\n"
        "  check provisioning: camp status feat-o\n"
        "  activates when ready, or run: camp activate feat-o\n"
        "created camp-mygroup-feat-o\n"
    )


# ---------------------------------------------------------------------------
# camp new --activate
# ---------------------------------------------------------------------------


def _author_activate_group(cli_env, group_name: str, member_name: str = "member") -> None:
    """Author a fresh single-member group whose member declares one
    activate-phase task ("mark") — a plain marker-file write so the test can
    observe whether the task actually ran, without needing a real dependency
    install. A dedicated repo keeps this out of the no-overlap constraint the
    fixture's mygroup/badgroup repos are already under."""
    repo = cli_env["tmp_path"] / f"repo_{group_name}"
    init_git_repo(repo, origin=True)
    config_dir = cli_env["config_dir"]
    path = config_dir / "groups" / f"{group_name}.toml"
    path.write_text(
        f'[group]\nname = "{group_name}"\n\n'
        f"[[members]]\n"
        f'name = "{member_name}"\n'
        f"repo_root = {json.dumps(str(repo))}\n"
        f"bootstrap = []\n"
        f'tasks = ["mark"]\n\n'
        f"[branch]\npattern = \"worktree-{{slug}}\"\n\n"
        f"[tasks.mark]\n"
        f'phase = "activate"\n'
        f"required = true\n\n"
        f"[[tasks.mark.steps]]\n"
        f'name = "run"\n'
        f'cmd = ["python3", "-c", "import pathlib; pathlib.Path(\\"{{worktree}}/marker\\").write_text(\\"done\\")"]\n\n'
        f"[harness]\n"
        f'binary = "fakeharness"\n',
        encoding="utf-8",
    )


def _status_json(cli_env, slug: str, *, group: str) -> dict:
    result = _camp(cli_env, "status", "--name", slug, "--group", group, "--json")
    assert result.returncode in (0, 2, 3), result.stderr
    return json.loads(result.stdout)


def _member_work_state(report: dict, member_name: str) -> str:
    (member,) = [m for m in report["members"] if m["name"] == member_name]
    return member["work_state"]


def _poll_until_work_ready(
    cli_env, slug: str, member_name: str, *, group: str, timeout: float = 15.0
) -> dict:
    deadline = time.monotonic() + timeout
    report = _status_json(cli_env, slug, group=group)
    while _member_work_state(report, member_name) == "pending" and time.monotonic() < deadline:
        time.sleep(0.2)
        report = _status_json(cli_env, slug, group=group)
    return report


def test_camp_new_activate_triggers_activate_phase_work_for_a_declaring_member(cli_env) -> None:
    _author_activate_group(cli_env, "actgroup")

    result = _camp(cli_env, "new", "feat-act", "--group", "actgroup", "--activate")

    assert result.returncode == 0, result.stderr
    report = _poll_until_work_ready(cli_env, "feat-act", "member", group="actgroup")
    assert _member_work_state(report, "member") == "ready", report
    ws_dir = Path(cli_env["state_dir"]) / "actgroup" / "worktrees" / "feat-act" / "member"
    _wait_for(lambda: (ws_dir / "marker").is_file())
    assert (ws_dir / "marker").read_text() == "done"


def test_camp_new_without_activate_never_triggers_activate_phase_work(cli_env) -> None:
    """The criterion that keeps an expensive activate-phase task from firing
    for every member of every new workspace: no --activate, no activate-phase
    task execution, even once the member reaches boot-readiness."""
    _author_activate_group(cli_env, "actgroup2")

    result = _camp(
        cli_env, "new", "feat-noact", "--group", "actgroup2", "--no-session"
    )

    assert result.returncode == 0, result.stderr
    _wait_for_workspace_ready(cli_env, "feat-noact", group="actgroup2")
    report = _status_json(cli_env, "feat-noact", group="actgroup2")
    assert report["members"][0]["provision_state"] == "ready", report
    assert _member_work_state(report, "member") == "pending", (
        "a member's activate-phase task must not run at creation without --activate",
        report,
    )
    ws_dir = Path(cli_env["state_dir"]) / "actgroup2" / "worktrees" / "feat-noact" / "member"
    assert not (ws_dir / "marker").is_file()


def test_camp_new_activate_on_a_group_with_no_activate_tasks_is_a_clean_no_op(cli_env) -> None:
    result = _camp(cli_env, "new", "feat-noop", "--group", "mygroup", "--activate")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("/feat-noop")


def test_camp_new_activate_composes_with_no_wait(cli_env) -> None:
    """--activate still triggers its work with --no-wait — it only skips the
    bounded wait for boot-readiness first, racing the still-running
    provisioner (an accepted risk on that flag), so the outcome of the race
    is not itself pinned here — only that the work was actually triggered
    rather than skipped."""
    _author_activate_group(cli_env, "actgroup4")

    result = _camp(
        cli_env,
        "new",
        "feat-anw",
        "--group",
        "actgroup4",
        "--no-session",
        "--no-wait",
        "--activate",
    )

    assert result.returncode == 0, result.stderr
    deadline = time.monotonic() + 15.0
    report = _status_json(cli_env, "feat-anw", group="actgroup4")
    while _member_work_state(report, "member") == "pending" and time.monotonic() < deadline:
        time.sleep(0.2)
        report = _status_json(cli_env, "feat-anw", group="actgroup4")
    assert _member_work_state(report, "member") in ("ready", "failed"), (
        "the activate trigger must fire even under the --no-wait race, "
        f"report={report}"
    )


def _wait_for(predicate, *, timeout: float = 15.0, interval: float = 0.2) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(interval)


# ---------------------------------------------------------------------------
# camp list — the tmux session state column, end to end through the REAL
# `cli/camp` binary and the `_TMUX_STUB`'s `list-sessions` arm. The bulk of
# the state-column contract is pinned in test_camp_list.py via the injectable
# `_FakeTmux` seam; these four pin the properties that need a real subprocess
# boundary (an unmanaged session's presence must never provoke a kill, a
# real non-zero tmux exit's stderr text distinguishes outage from empty, and
# the fake's own `list-sessions` arm is what the window-count/credential-store
# pins exercise).
# ---------------------------------------------------------------------------


def test_list_kills_nothing_with_an_unmanaged_session_present(cli_env) -> None:
    """AC54: `camp list` never kills a session — including a leftover
    (unmanaged) one it merely counts or names. Run the subject end to end and
    assert the observable consequence in the tmux argv log, not the absence
    of a code path."""
    _new_workspace(cli_env, "feat-safe")
    cli_env["tmux_table_file"].write_text(
        json.dumps({"camp-oldproj-a1b2c3d4": "sleep 1"}), encoding="utf-8"
    )

    result = _camp(cli_env, "list", "--group", "mygroup")

    assert result.returncode == 0, result.stderr
    kills = [argv for argv in _tmux_argv(cli_env) if argv[0] == "kill-session"]
    assert kills == [], f"camp list must issue no kill-session, got {kills!r}"


def test_list_state_column_needs_no_credential_store(cli_env, tmp_path) -> None:
    """The state column is a pure tmux read: with no harness account
    configured on the group (mygroup declares none) AND `CLAUDE_CONFIG_DIR`
    pointed at an empty directory, the running workspace's state still comes
    back correctly. This pins the retired account-scoping defect: nothing in
    the state derivation path may consult a credential store."""
    from camp.launch.naming import workspace_session_name

    empty_claude_dir = tmp_path / "empty-claude-config"
    empty_claude_dir.mkdir()
    _new_workspace(cli_env, "feat-cred")
    name = workspace_session_name("mygroup", "feat-cred")
    cli_env["tmux_table_file"].write_text(
        json.dumps({name: "sleep 1"}), encoding="utf-8"
    )

    result = _camp(
        cli_env, "list", "--group", "mygroup", "--json",
        extra_env={"CLAUDE_CONFIG_DIR": str(empty_claude_dir)},
    )

    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    row = next(r for r in rows if r["slug"] == "feat-cred")
    assert row["state"] == "running"


def test_list_distinguishes_outage_from_empty(cli_env) -> None:
    """Same non-zero exit status, two different tmux stderr texts, two
    different listings. UNSAFE-PERMISSIONS is an outage tmux never actually
    answered: `unknown` rows plus a stderr notice. NO-SERVER is tmux
    genuinely answering "nothing is running": `none` rows and silence on
    stderr. The outage case must never be read as the empty case."""
    _new_workspace(cli_env, "feat-outage")

    outage = _camp(
        cli_env, "list", "--group", "mygroup",
        extra_env={"CAMP_FAKE_TMUX_LIST_SESSIONS_FAIL": "unsafe-permissions"},
    )
    assert outage.returncode == 0, outage.stderr
    outage_sessions = outage.stdout.strip().splitlines()[-1].split()[1]
    assert outage_sessions == "?"
    assert outage.stderr.strip() != ""

    empty = _camp(
        cli_env, "list", "--group", "mygroup",
        extra_env={"CAMP_FAKE_TMUX_LIST_SESSIONS_FAIL": "no-server"},
    )
    assert empty.returncode == 0, empty.stderr
    empty_sessions = empty.stdout.strip().splitlines()[-1].split()[1]
    assert empty_sessions == "0"
    assert empty.stderr == ""


def test_list_window_counts_differ_between_two_running_workspaces(cli_env) -> None:
    """Two running workspaces in ONE listing carry DIFFERENT window counts,
    driven by the fake's per-session windows data — showing the count is
    per-session, not per-listing."""
    from camp.launch.naming import workspace_session_name

    _new_workspace(cli_env, "feat-w1")
    _new_workspace(cli_env, "feat-w2")
    name1 = workspace_session_name("mygroup", "feat-w1")
    name2 = workspace_session_name("mygroup", "feat-w2")
    cli_env["tmux_table_file"].write_text(
        json.dumps({name1: "sleep 1", name2: "sleep 1"}), encoding="utf-8"
    )
    windows_file = cli_env["tmp_path"] / "windows.json"
    windows_file.write_text(json.dumps({name1: 2, name2: 5}), encoding="utf-8")

    result = _camp(
        cli_env, "list", "--group", "mygroup", "--json",
        extra_env={"CAMP_FAKE_TMUX_WINDOWS_FILE": str(windows_file)},
    )

    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    counts = {row["slug"]: row["window_count"] for row in rows}
    assert counts["feat-w1"] == 2
    assert counts["feat-w2"] == 5
    assert counts["feat-w1"] != counts["feat-w2"]
