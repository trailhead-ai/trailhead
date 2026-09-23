"""CLI surface: camp sessions, camp kill, camp attach, camp new.

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
- `_launched_session` registers a live session directly against these doubles,
  in the shape `camp.launch.stop._owning_commands` still recognizes (the
  harness's resume composition); no camp verb composes that shape on its
  own, so the fixture registers it directly.

Test contract:
- camp sessions: empty → empty stdout, exit 0; degraded (enumeration error /
  missing enumerate binary / unknown harness) → stderr notice, empty stdout list,
  exit 0; --json carries only normalized SessionRecord fields. The default and
  slug-scoped forms are pinned byte-for-byte, so widening the verb cannot
  perturb what an existing caller parses. `--dir <path>` scopes the live listing
  to a directory subtree.
- camp sessions --recoverable: dead = enumerated transcripts − live, scoped by
  the same argument on both sides (a session in a SUBDIRECTORY of the scope is
  in scope). An undeterminable live set degrades to a notice and an EMPTY list,
  never an unsubtracted pool. A torn-down root is listed and marked, and a
  transcript with no extractable cwd is listed as a uuid-and-age row. Capped at
  the newest 20 with the total named; --limit/--all widen it and an unusable
  --limit refuses. An empty result names itself, distinctly from the refusal a
  harness that keeps no transcripts gets. --json rows carry EXACTLY six keys,
  never the live form's harness-native `name`. Ordering is newest-first with a
  uuid tiebreak, and the scan's cost does not grow with transcript size.
- camp kill: a launched session is signalled under its exact derived name and
  confirmed gone (exit 0, session id on stdout); an already-down one is exit 0
  saying so; an ambiguous ref is exit 2 with candidates on stdout. Every other
  outcome is one `camp kill: …` stderr line with empty stdout and a non-zero
  exit — a zero-match ref, a live session owning no tmux session, a foreign pane
  holding the name, a session still there after the kill, and a tmux that will
  not answer, each with its own wording. `--json` distinguishes the two exit-0
  outcomes. It routes with no group resolvable, past a malformed sibling config,
  and writes nothing under CAMP_STATE_DIR.
- camp new: bare `camp new` and `camp new --json` go through the door; the
  `--no-session` escape hatch skips it and prints only the workspace path
  (or `{"workspace": ...}` with `--json`) — there is no launch flavor left
  behind either flag.
- --no-wait (read only by --activate now) skips its boot-readiness wait and
  names `camp status <slug>`; --activate's work still runs to completion.
- Bare `camp new` output is byte-identical to the pre-door-key surface, minus
  the one intentional new line the window-creation-key install notice adds.
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
    # `camp kill` carries two budgets of its own, both sized for a busy tmux
    # server. The fake tmux here is a local script, so the only tests that
    # spend either budget are the ones deliberately driving a tmux that hangs
    # or a session that never goes — each sitting out the full budget to
    # observe the outcome it asserts. Both are margins over what the fake
    # needs, not measured floors: raise them first if these ever go flaky
    # under heavy parallelism.
    env["CAMP_TEST_TMUX_TIMEOUT_SECONDS"] = "2"
    env["CAMP_TEST_STOP_POLL_TIMEOUT_SECONDS"] = "1"
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
    launch`, `camp sessions`, `camp kill`, `camp list`) then drives its own
    session lifecycle against. `--no-session` keeps this helper from also
    creating the workspace's tmux session, which would otherwise show up as
    an extra, untracked session alongside whatever the calling test starts
    itself."""
    result = _camp(cli_env, "new", slug, "--group", group, "--no-session")
    assert result.returncode == 0, result.stderr
    _wait_for_workspace_ready(cli_env, slug, group=group)
    return result.stdout.strip()


def _state_tree(cli_env) -> list[str]:
    """Every path under CAMP_STATE_DIR, relative and sorted.

    A directory-rooted launch persists nothing, so this is the whole statelessness
    assertion: the tree before a command and after it must be identical.
    """
    root = cli_env["state_dir"]
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _tmux_argv(cli_env) -> list[list[str]]:
    """The argv of every tmux invocation camp made, in order."""
    lines = cli_env["tmux_argv_file"].read_text(encoding="utf-8").splitlines()
    return [line.split("\t") for line in lines if line]


def _assert_clean_refusal(result, *, needle: str, verb: str) -> None:
    """One `camp <verb>: ` stderr line, empty stdout, non-zero exit."""
    assert result.returncode != 0, result.stdout
    assert result.stdout == ""
    lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith(f"camp {verb}: "), lines[0]
    assert needle in lines[0], lines[0]


#: Two distinct, well-formed session ids. Real uuids, because the 8-character
#: prefix form is part of the addressing contract and a short id would let a
#: prefix test pass without exercising it.
_UUID_A = "aaaaaaaa-1111-4111-8111-111111111111"
_UUID_B = "bbbbbbbb-2222-4222-8222-222222222222"


def _seed_transcript(
    cli_env, session_id: str, cwd: Path | None, *, age_seconds: float = 60.0
) -> Path:
    """Author one harness transcript in the hermetic TRAILHEAD_CLAUDE_DIR store.

    `cwd=None` authors the uuid-only case the seam degrades to: a transcript
    carrying no extractable start directory at all.
    """
    projects = Path(cli_env["env"]["TRAILHEAD_CLAUDE_DIR"]) / "projects"
    if cwd is None:
        munged = f"-unreadable-{session_id[:8]}"
        body: dict = {"type": "user"}
    else:
        munged = str(cwd).replace("/", "-").replace(".", "-")
        body = {"type": "user", "cwd": str(cwd)}
    directory = projects / munged
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.jsonl"
    path.write_text(json.dumps(body) + "\n", encoding="utf-8")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return path


def _register_live(cli_env, session_id: str, cwd: Path) -> None:
    """Add a row to the file the fake harness enumerates as live sessions."""
    with cli_env["sessions_file"].open("a", encoding="utf-8") as handle:
        handle.write(f"{session_id}\t{cwd}\n")


def _register_live_at(account: Path, session_id: str, cwd: Path) -> None:
    """Add a row to a per-account fake sessions table — a credential store
    OTHER than the fixture's shared default, for tests spanning more than
    one store. Mirrors `_register_live`, at the file
    `FakeHarness.session_launch_env_set` binds a declared account to.
    """
    account.mkdir(parents=True, exist_ok=True)
    with (account / "sessions.tsv").open("a", encoding="utf-8") as handle:
        handle.write(f"{session_id}\t{cwd}\n")


def _poison_enumerate_at(account: Path) -> None:
    """Make the fake harness's enumeration fail for just THIS account's
    store, without affecting any other store's answer.
    """
    account.mkdir(parents=True, exist_ok=True)
    (account / "ENUMERATE_FAIL").touch()


def _seed_transcript_at(claude_dir: Path, session_id: str, cwd: Path) -> Path:
    """Author one harness transcript directly under *claude_dir* — a credential
    store OTHER than the fixture's shared TRAILHEAD_CLAUDE_DIR, for tests
    spanning more than one store.
    """
    munged = str(cwd).replace("/", "-").replace(".", "-")
    directory = claude_dir / "projects" / munged
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.jsonl"
    path.write_text(json.dumps({"type": "user", "cwd": str(cwd)}) + "\n", encoding="utf-8")
    return path


def _add_account_group(
    cli_env, name: str, account: Path, repo: Path, *, declared: str | None = None
) -> None:
    """Author a fresh fakeharness group declaring `[launch] account`.

    `declared` overrides the literal string written into the config when the
    test needs the declaration to differ textually from the directory it names
    — a non-canonical spelling of the same path, used to prove camp carries the
    declaration through rather than a canonicalized form of it.
    """
    init_git_repo(repo, origin=True)
    result = _camp(cli_env, "group", name, "--member", f"member={repo}")
    assert result.returncode == 0, result.stderr
    _set_harness_binary(cli_env["config_dir"], name, "fakeharness")
    path = cli_env["config_dir"] / "groups" / f"{name}.toml"
    written = str(account) if declared is None else declared
    path.write_text(
        path.read_text(encoding="utf-8") + f'\n[launch]\naccount = "{written}"\n',
        encoding="utf-8",
    )


def _env_without_shared_claude_dir(cli_env) -> dict[str, str]:
    """The fixture's env, minus TRAILHEAD_CLAUDE_DIR and with a safe HOME.

    Every OTHER test in this file relies on TRAILHEAD_CLAUDE_DIR pinning every
    declared-nothing store to one shared, hermetic directory. Proving that two
    DECLARED accounts resolve to two DIFFERENT stores needs the opposite: each
    account's own `CLAUDE_CONFIG_DIR` binding, uncontested by that override —
    Axiom 6 is kept instead by pointing HOME at a tmp_path directory nothing
    else in the suite reads or writes.
    """
    env = {k: v for k, v in cli_env["env"].items() if k != "TRAILHEAD_CLAUDE_DIR"}
    env["HOME"] = str(cli_env["tmp_path"] / "safe-home")
    return env


def _workspace_launch_dir(cli_env, slug: str) -> Path:
    """The directory a slug launch resolves to — the transcript's recorded cwd."""
    return Path(_new_workspace(cli_env, slug)).resolve()


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
# camp sessions
# ---------------------------------------------------------------------------


def test_camp_sessions_default_output_is_byte_identical(cli_env) -> None:
    """Regression pin: the live listing's bytes, global form.

    Written against the surface as it stands so that widening `camp sessions`
    with new flags cannot perturb the answer an existing caller already parses.
    """
    launch_dir = _workspace_launch_dir(cli_env, "feat-pin")
    session_id = _launched_session(cli_env, "feat-pin")

    result = _camp(cli_env, "sessions", "--group", "mygroup")

    assert result.returncode == 0
    assert result.stdout == f"{session_id}  agent  {launch_dir}\n"
    assert result.stderr == ""


def test_camp_sessions_slug_scoped_output_is_byte_identical(cli_env) -> None:
    """Regression pin: the live listing's bytes, workspace-scoped form."""
    launch_dir = _workspace_launch_dir(cli_env, "feat-pin")
    session_id = _launched_session(cli_env, "feat-pin")

    result = _camp(cli_env, "sessions", "feat-pin", "--group", "mygroup")

    assert result.returncode == 0
    assert result.stdout == f"{session_id}  agent  {launch_dir}\n"
    assert result.stderr == ""


def test_camp_sessions_empty_prints_nothing_and_exits_zero(cli_env) -> None:
    result = _camp(cli_env, "sessions", "--group", "mygroup")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "could not determine" not in result.stderr


def test_camp_sessions_lists_a_launched_session(cli_env) -> None:
    session_id = _launched_session(cli_env, "feat-f")

    result = _camp(cli_env, "sessions", "--group", "mygroup")

    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip().splitlines()) == 1
    assert session_id in result.stdout


def test_camp_sessions_json_uses_normalized_fields_only(cli_env) -> None:
    _launched_session(cli_env, "feat-g")

    result = _camp(cli_env, "sessions", "--group", "mygroup", "--json")

    assert result.returncode == 0, result.stderr
    records = json.loads(result.stdout)
    assert len(records) == 1
    assert set(records[0]) == {
        "ok",
        "session_id",
        "cwd",
        "kind",
        "controllable",
        "name",
        "pid",
        "started_at",
        "group",
        "account",
    }
    assert records[0]["ok"] is True
    assert isinstance(records[0]["session_id"], str)
    assert isinstance(records[0]["cwd"], str)
    assert isinstance(records[0]["kind"], str)
    assert isinstance(records[0]["controllable"], bool)
    assert isinstance(records[0]["pid"], (int, type(None)))
    # mygroup declares no [launch] account — the row states the group it
    # resolved into and a null account, never a blank string.
    assert records[0]["group"] == "mygroup"
    assert records[0]["account"] is None


def test_camp_sessions_json_never_carries_a_host_key(cli_env) -> None:
    """AC6-shaped non-regression pin (mirrors `test_camp_list.py`'s): a plain
    local `camp sessions --json` — no `--host` in play — carries EXACTLY the
    pre-existing ten-key payload and no `host` key. The `--host` axis stamps
    `host` only on a relayed row (camp.host.relay); this path must never gain
    it, unconditionally or otherwise. Expected keys are spelled out literally
    here rather than imported from the module under test, so a mutation to
    the module's own key set cannot also mutate this test's expectation."""
    _launched_session(cli_env, "feat-h")

    result = _camp(cli_env, "sessions", "--group", "mygroup", "--json")

    assert result.returncode == 0, result.stderr
    records = json.loads(result.stdout)
    assert len(records) == 1
    assert set(records[0].keys()) == {
        "ok",
        "session_id",
        "cwd",
        "kind",
        "controllable",
        "name",
        "pid",
        "started_at",
        "group",
        "account",
    }
    assert "host" not in records[0]


def test_camp_sessions_json_carries_a_declared_account_verbatim(cli_env) -> None:
    """A session whose cwd resolves into a group declaring an account carries
    that exact declared string — byte for byte, not expanded or normalized.
    """
    account = cli_env["tmp_path"] / "verbatim-account"
    # Declared in a deliberately NON-canonical spelling of the very same
    # directory: absolute, so the harness still binds it, but carrying a
    # redundant "/." segment that `Path.resolve()` would silently collapse.
    # A canonical probe would read identically before and after normalization
    # and so could never show whether camp normalizes what it carries.
    declared = f"{account}/."
    repo = cli_env["tmp_path"] / "repo-verbatim"
    _add_account_group(cli_env, "verbatim", account, repo, declared=declared)
    _register_live_at(account, "verbatim-sess", repo)

    env = _env_without_shared_claude_dir(cli_env)
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "--group", "verbatim", "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cli_env["tmp_path"]),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["group"] == "verbatim"
    assert payload[0]["account"] == declared
    # The declaration is carried, not canonicalized: the collapsed form is what
    # any expansion/resolution step would have produced instead.
    assert payload[0]["account"] != str(account)


def test_camp_sessions_two_spellings_of_the_same_account_dedupe_to_one_store(
    cli_env,
) -> None:
    """Two groups declaring the SAME credential-store directory under two
    different spellings must be one pool entry, not two — a session sitting
    in that shared store must appear exactly once, not once per spelling.
    """
    account = cli_env["tmp_path"] / "shared-account"
    repo_a = cli_env["tmp_path"] / "repo-dedupe-a"
    repo_b = cli_env["tmp_path"] / "repo-dedupe-b"
    _add_account_group(cli_env, "dedupea", account, repo_a, declared=str(account))
    # Deliberately a non-canonical spelling of the identical directory a
    # different group already declared canonically — the same collapsing
    # form `test_camp_sessions_json_carries_a_declared_account_verbatim`
    # uses to prove the verbatim pin, reused here to prove two spellings of
    # one directory still bind to one physical store.
    _add_account_group(cli_env, "dedupeb", account, repo_b, declared=f"{account}/.")
    _register_live_at(account, "dedupe-sess", repo_a)

    env = _env_without_shared_claude_dir(cli_env)
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "--group", "dedupea", "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cli_env["tmp_path"]),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [row["session_id"] for row in payload] == ["dedupe-sess"]
    # The surviving pool entry keeps the FIRST-declared spelling — "dedupea"
    # loads before "dedupeb" (config files are read in filename order) and
    # declared the canonical form, so that is what the row carries, not the
    # collapsing "/." spelling declared second.
    assert payload[0]["account"] == str(account)


def test_camp_sessions_json_nulls_group_and_account_outside_every_group(cli_env) -> None:
    """A session whose cwd resolves into no configured group is still listed,
    with a null group and a null account — never dropped, never a raised error.

    Scoped by `--dir`, not by a bare group name: `--dir` narrows the pool by
    path, not by group attribution, so this listing is not subject to the
    group-name filter a group-named query applies — the one context where an
    unattributable row is still observable on the live listing.
    """
    outside = cli_env["tmp_path"] / "not-a-member-repo"
    outside.mkdir()
    _register_live(cli_env, "outside-sess", outside)

    result = _camp(
        cli_env, "sessions", "--group", "mygroup", "--dir", str(outside), "--json"
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    row = next(r for r in payload if r["session_id"] == "outside-sess")
    assert row["group"] is None
    assert row["account"] is None


def test_camp_sessions_json_attributes_member_repo_cwd_same_as_workspace_cwd(
    cli_env,
) -> None:
    """Group attribution uses the SAME resolver the dispatcher uses: a session
    rooted in a member repository checkout (the resolver's step-2 fallback)
    attributes to its group exactly like one rooted in a workspace (step 1).
    """
    launch_dir = _workspace_launch_dir(cli_env, "feat-attr")
    _register_live(cli_env, "workspace-sess", launch_dir)
    # repo_a is mygroup's configured member repo root (see cli_env fixture).
    _register_live(cli_env, "repo-sess", cli_env["tmp_path"] / "repo_a")

    result = _camp(cli_env, "sessions", "--group", "mygroup", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    by_id = {row["session_id"]: row for row in payload}
    assert by_id["workspace-sess"]["group"] == "mygroup"
    assert by_id["repo-sess"]["group"] == "mygroup"


def test_camp_sessions_group_named_answer_excludes_the_default_stores_session(
    cli_env,
) -> None:
    """The recorded reproduction, pinned: naming a group that declares its own
    account must return that group's sessions and NONE of the sessions running
    under the shell's ambient (default) credential store — the measured defect
    where `camp sessions --group <name>` answered with another group's
    sessions because the named group was consumed to pick a config and then
    discarded rather than narrowing the answer.
    """
    account = cli_env["tmp_path"] / "repro-account"
    _add_account_group(cli_env, "repro", account, cli_env["tmp_path"] / "repo-repro")
    _register_live_at(account, "repro-session", cli_env["tmp_path"] / "repo-repro")
    # mygroup declares no account, so its session is read from the DEFAULT
    # store this ambient env resolves to — the store the shell is "bound to".
    _register_live(cli_env, "default-store-session", cli_env["tmp_path"] / "repo_a")

    env = _env_without_shared_claude_dir(cli_env)
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "--group", "repro"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cli_env["tmp_path"]),
    )

    assert result.returncode == 0, result.stderr
    assert "repro-session" in result.stdout
    assert "default-store-session" not in result.stdout


def test_camp_sessions_group_named_answer_is_empty_when_nothing_runs_there(
    cli_env,
) -> None:
    """Naming a group with nothing running returns an empty answer — the SAME
    store's session, running under a DIFFERENT configured group, must not
    fill in for it.
    """
    repo_empty = cli_env["tmp_path"] / "repo-emptygroup"
    init_git_repo(repo_empty, origin=True)
    result = _camp(cli_env, "group", "emptygroup", "--member", f"member={repo_empty}")
    assert result.returncode == 0, result.stderr
    _set_harness_binary(cli_env["config_dir"], "emptygroup", "fakeharness")
    # emptygroup declares no account, so it shares mygroup's default store —
    # the session below is real and enumerable, just attributed elsewhere.
    _register_live(cli_env, "mygroup-sess", cli_env["tmp_path"] / "repo_a")

    result = _camp(cli_env, "sessions", "--group", "emptygroup")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_camp_sessions_json_group_named_answer_carries_no_foreign_group_attribution(
    cli_env,
) -> None:
    """Every row a group-named JSON answer carries is attributed to the NAMED
    group — checked over the WHOLE returned set, not by the absence of one
    known-foreign row, so a filter that drops everything cannot pass this
    vacuously.
    """
    account = cli_env["tmp_path"] / "foreign-account"
    _add_account_group(cli_env, "foreign", account, cli_env["tmp_path"] / "repo-foreign")
    _register_live_at(account, "foreign-sess", cli_env["tmp_path"] / "repo-foreign")
    # Same pool, DIFFERENT group's store.
    _register_live(cli_env, "mygroup-sess", cli_env["tmp_path"] / "repo_a")

    env = _env_without_shared_claude_dir(cli_env)
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "--group", "mygroup", "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cli_env["tmp_path"]),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    ok_rows = [row for row in payload if row["ok"] is True]
    # A vacuous (empty) filtered answer would satisfy "no foreign row" too —
    # guard against that by requiring the named group's own row to be present.
    assert ok_rows
    assert all(row["group"] == "mygroup" for row in ok_rows)
    assert {row["session_id"] for row in ok_rows} == {"mygroup-sess"}


def test_camp_sessions_json_excludes_a_session_outside_every_group(cli_env) -> None:
    """A session outside every group is excluded from a group-named answer —
    naming a group narrows the answer to that group's rows, it does not add
    an unattributable row alongside them.
    """
    outside = cli_env["tmp_path"] / "not-a-member-repo"
    outside.mkdir()
    _register_live(cli_env, "outside-sess", outside)
    _register_live(cli_env, "inside-sess", cli_env["tmp_path"] / "repo_a")

    result = _camp(cli_env, "sessions", "--group", "mygroup", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    ids = {row["session_id"] for row in payload}
    assert "outside-sess" not in ids
    assert "inside-sess" in ids


def test_camp_sessions_group_named_via_cwd_refuses_unconfigured_group(cli_env) -> None:
    """Naming an unconfigured group refuses with a stated reason rather than
    answering from anywhere — exercised through the real CLI dispatch path
    (`cli/dispatch.py`'s group resolution), not a direct call into this
    module.
    """
    result = _camp(
        cli_env, "sessions", "--group", "totallyunconfigured", cwd=cli_env["tmp_path"]
    )

    _assert_clean_refusal(result, needle="no group resolved", verb="sessions")


@pytest.mark.parametrize("mode", ["fail", "missing", "none"])
def test_camp_sessions_every_store_failing_exits_nonzero_with_no_rows(
    cli_env, mode: str
) -> None:
    """Every addressable store failing is a REFUSAL, not an empty answer.

    `--group mygroup` puts exactly one store in the pool (badgroup's harness is
    unnameable and contributes nothing), so making that one store's enumeration
    fail — however it fails — is the total-failure case: no rows, a stated
    reason, and a non-zero exit, so a script cannot read "nothing running" off
    what is actually "camp could not tell".
    """
    result = _camp(
        cli_env, "sessions", "--group", "mygroup", extra_env={"CAMP_FAKE_ENUMERATE": mode}
    )

    _assert_clean_refusal(result, needle="every credential store failed", verb="sessions")


def test_camp_sessions_total_failure_json_emits_no_array_at_all(cli_env) -> None:
    result = _camp(
        cli_env,
        "sessions",
        "--group",
        "mygroup",
        "--json",
        extra_env={"CAMP_FAKE_ENUMERATE": "fail"},
    )

    assert result.returncode != 0, result.stdout
    assert result.stdout == ""
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)
    assert "camp sessions: could not enumerate" in result.stderr


def test_camp_sessions_survives_a_group_whose_harness_camp_cannot_name(cli_env) -> None:
    """`badgroup`'s harness never becomes a pool candidate at all — it is not
    a failing store, it is one that was never addressable. This is a
    corrected pin: an earlier version of this test asserted the listing
    answered silently in this case, which reads as "nothing is running" when
    the true answer is "camp could not tell" — exactly the confident-wrong
    answer this whole listing exists to remove. The corrected contract is a
    notice naming the group, an empty stdout, and a clean (zero) exit — a
    degrade, not a refusal, because a named group whose sessions cannot be
    determined is still a question the caller can move past.
    """
    result = _camp(cli_env, "sessions", "--group", "badgroup")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "camp sessions: could not determine the live sessions for group 'badgroup'" in result.stderr


def test_camp_sessions_with_no_addressable_store_at_all_degrades(cli_env) -> None:
    """Every configured group's harness is unnameable → the pool itself is
    empty, distinct from every store in a non-empty pool failing to answer.
    """
    mygroup_toml = cli_env["config_dir"] / "groups" / "mygroup.toml"
    mygroup_toml.write_text(
        mygroup_toml.read_text(encoding="utf-8").replace("fakeharness", "alsonosuchharness"),
        encoding="utf-8",
    )

    result = _camp(cli_env, "sessions", "--group", "mygroup")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "camp sessions: could not determine" in result.stderr


def test_camp_sessions_lists_a_session_running_under_a_non_default_credential_store(
    cli_env,
) -> None:
    """A shell bound to the default credential store still sees a session
    running under a group's OWN declared account: the listing asks every
    declared store, not whichever one the shell happens to carry.
    """
    account = cli_env["tmp_path"] / "nondefault-account"
    launch_dir = cli_env["tmp_path"] / "repo-nondefault"
    _add_account_group(cli_env, "nondefault", account, launch_dir)
    _register_live_at(account, "nondefault-session", launch_dir)

    env = _env_without_shared_claude_dir(cli_env)
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "--group", "nondefault"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cli_env["tmp_path"]),
    )

    assert result.returncode == 0, result.stderr
    assert "nondefault-session" in result.stdout
    assert str(launch_dir) in result.stdout


def test_camp_sessions_merges_two_stores_each_holding_a_session(cli_env) -> None:
    """Two stores, each running a session under a DIFFERENT declared account,
    produce ONE merged answer containing both.

    Scoped by `--dir` over their shared parent rather than by a group name:
    the two sessions belong to two DIFFERENT groups, so a group-named query
    would correctly narrow to one of them — this test's subject is the
    merge across stores, which `--dir` observes without that narrowing.
    """
    account_a = cli_env["tmp_path"] / "merge-account-a"
    account_b = cli_env["tmp_path"] / "merge-account-b"
    _add_account_group(cli_env, "mergea", account_a, cli_env["tmp_path"] / "repo-mergea")
    _add_account_group(cli_env, "mergeb", account_b, cli_env["tmp_path"] / "repo-mergeb")
    root_a = cli_env["tmp_path"] / "merge-root-a"
    root_b = cli_env["tmp_path"] / "merge-root-b"
    root_a.mkdir()
    root_b.mkdir()
    _register_live_at(account_a, "merge-sess-aaa", root_a)
    _register_live_at(account_b, "merge-sess-bbb", root_b)

    env = _env_without_shared_claude_dir(cli_env)
    result = subprocess.run(
        [
            sys.executable, str(_CLI_CAMP), "sessions",
            "--group", "mergea", "--dir", str(cli_env["tmp_path"]), "--json",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cli_env["tmp_path"]),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    ids = {row["session_id"] for row in payload}
    assert ids == {"merge-sess-aaa", "merge-sess-bbb"}
    assert all(row["ok"] is True for row in payload)


def test_camp_sessions_all_groups_merges_every_configured_groups_sessions(cli_env) -> None:
    """`--all-groups` widens rather than narrows: two DIFFERENT groups' sessions
    both come back in ONE answer, with no `--group` and no cwd that resolves
    to either — the opposite of the ordinary bare-group-named query, which
    would narrow to one of them.
    """
    account_a = cli_env["tmp_path"] / "allgroups-account-a"
    account_b = cli_env["tmp_path"] / "allgroups-account-b"
    _add_account_group(cli_env, "allgroupsa", account_a, cli_env["tmp_path"] / "repo-agsa")
    _add_account_group(cli_env, "allgroupsb", account_b, cli_env["tmp_path"] / "repo-agsb")
    _register_live_at(account_a, "ag-sess-aaa", cli_env["tmp_path"] / "repo-agsa")
    _register_live_at(account_b, "ag-sess-bbb", cli_env["tmp_path"] / "repo-agsb")

    env = _env_without_shared_claude_dir(cli_env)
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "--all-groups", "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cli_env["tmp_path"]),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    ok_rows = [row for row in payload if row["ok"] is True]
    ids = {row["session_id"] for row in ok_rows}
    assert ids == {"ag-sess-aaa", "ag-sess-bbb"}
    groups = {row["group"] for row in ok_rows}
    assert groups == {"allgroupsa", "allgroupsb"}


def test_camp_sessions_all_groups_short_and_long_spellings_are_byte_identical(
    cli_env,
) -> None:
    """Compares stdout from TWO REAL invocations, not two calls into the same
    formatting helper."""
    account_a = cli_env["tmp_path"] / "spelling-account-a"
    _add_account_group(cli_env, "spellinga", account_a, cli_env["tmp_path"] / "repo-spa")
    _register_live_at(account_a, "spelling-sess", cli_env["tmp_path"] / "repo-spa")

    env = _env_without_shared_claude_dir(cli_env)
    long_form = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "--all-groups"],
        capture_output=True, text=True, env=env, cwd=str(cli_env["tmp_path"]),
    )
    short_form = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "-g"],
        capture_output=True, text=True, env=env, cwd=str(cli_env["tmp_path"]),
    )

    assert long_form.returncode == 0, long_form.stderr
    assert short_form.returncode == 0, short_form.stderr
    assert long_form.stdout == short_form.stdout
    assert "spelling-sess" in long_form.stdout


def test_camp_sessions_all_groups_orders_rows_by_group_then_within_group_order(
    cli_env,
) -> None:
    """The pool is walked in config-FILE load order (alphabetical by filename),
    while the answer must be ordered by declared group NAME — so this test
    names the group whose config file loads FIRST such that its group NAME
    sorts LAST, and vice versa, and asserts the printed order follows the
    NAME, not the file load order.
    """
    account_first_file = cli_env["tmp_path"] / "order-account-aaa-file"
    account_second_file = cli_env["tmp_path"] / "order-account-zzz-file"
    # File "aaa-order.toml" loads FIRST but declares group "zzzorder".
    _add_account_group(
        cli_env, "aaa-order", account_first_file, cli_env["tmp_path"] / "repo-order-1"
    )
    _add_account_group(
        cli_env, "zzz-order", account_second_file, cli_env["tmp_path"] / "repo-order-2"
    )
    # Rewrite the two just-authored configs so their FILE STEM and their
    # declared [group].name diverge from each other and from one another's
    # original names, without touching how `_add_account_group` wired them.
    aaa_path = cli_env["config_dir"] / "groups" / "aaa-order.toml"
    zzz_path = cli_env["config_dir"] / "groups" / "zzz-order.toml"
    aaa_path.write_text(
        aaa_path.read_text(encoding="utf-8").replace(
            'name = "aaa-order"', 'name = "zzzorder"'
        ),
        encoding="utf-8",
    )
    zzz_path.write_text(
        zzz_path.read_text(encoding="utf-8").replace(
            'name = "zzz-order"', 'name = "aaaorder"'
        ),
        encoding="utf-8",
    )
    _register_live_at(account_first_file, "order-sess-in-zzzorder", cli_env["tmp_path"] / "repo-order-1")
    _register_live_at(account_second_file, "order-sess-in-aaaorder", cli_env["tmp_path"] / "repo-order-2")

    env = _env_without_shared_claude_dir(cli_env)
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "--all-groups", "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cli_env["tmp_path"]),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    ok_rows = [row for row in payload if row["ok"] is True]
    assert [row["group"] for row in ok_rows] == ["aaaorder", "zzzorder"]


def test_camp_sessions_all_groups_with_a_named_group_refuses_with_zero_enumerations(
    cli_env,
) -> None:
    """Naming a single group alongside `--all-groups` refuses before a single
    harness is ever asked anything — proven by a counter the fake harness's
    enumeration subprocess itself increments on every invocation, which must
    stay at zero.
    """
    calls_file = cli_env["tmp_path"] / "enumerate-calls.tsv"

    result = _camp(
        cli_env,
        "sessions",
        "--all-groups",
        "--group",
        "mygroup",
        extra_env={"CAMP_FAKE_ENUMERATE_CALLS_FILE": str(calls_file)},
    )

    _assert_clean_refusal(result, needle="--all-groups", verb="sessions")
    assert not calls_file.exists() or calls_file.read_text() == ""


def test_camp_sessions_all_groups_with_a_positional_slug_refuses_with_zero_enumerations(
    cli_env,
) -> None:
    """`camp sessions -g <slug>` names one workspace alongside the option that
    widens to every group — the same narrow-vs-widen contradiction as naming
    `--group`, refused the same way and before a single harness is asked
    anything, proven by the same enumeration-call counter staying at zero.
    """
    calls_file = cli_env["tmp_path"] / "enumerate-calls-slug.tsv"

    result = _camp(
        cli_env,
        "sessions",
        "-g",
        "somelug",
        extra_env={"CAMP_FAKE_ENUMERATE_CALLS_FILE": str(calls_file)},
    )

    _assert_clean_refusal(result, needle="--all-groups", verb="sessions")
    assert not calls_file.exists() or calls_file.read_text() == ""


def test_camp_sessions_absent_all_groups_output_is_unchanged(cli_env) -> None:
    """No `--all-groups`/`-g` anywhere → the pre-existing default surface,
    unaffected by the new option's presence in the CLI."""
    result = _camp(cli_env, "sessions", "--group", "mygroup")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# camp sessions --all-groups — narrowed answers say why.
# ---------------------------------------------------------------------------


def test_camp_sessions_all_groups_no_groups_configured_states_that(tmp_path) -> None:
    config_dir = tmp_path / "camp-config"
    (config_dir / "groups").mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "camp-state"
    state_dir.mkdir(parents=True, exist_ok=True)

    env = {**os.environ}
    env["CAMP_CONFIG_DIR"] = str(config_dir)
    env["CAMP_STATE_DIR"] = str(state_dir)

    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "--all-groups", "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert json.loads(result.stdout) == []
    assert "no groups configured" in result.stderr


def test_camp_sessions_all_groups_one_unparsable_group_the_others_still_answer(
    cli_env,
) -> None:
    broken = cli_env["config_dir"] / "groups" / "brokengroup.toml"
    broken.write_text("this is not [ valid toml")
    _register_live(cli_env, "mygroup-sess", cli_env["tmp_path"] / "repo_a")

    result = _camp(cli_env, "sessions", "--all-groups", "--json")

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    payload = json.loads(result.stdout)
    ok_rows = [row for row in payload if row["ok"] is True]
    assert {row["session_id"] for row in ok_rows} == {"mygroup-sess"}
    assert {row["group"] for row in ok_rows} == {"mygroup"}
    assert str(broken) in result.stderr
    assert "camp sessions: " in result.stderr

    # The parser-visible half of the same defect: the unparsable group must
    # not vanish from a --json array that otherwise looks complete — it gets
    # an in-band ok:false row naming the config file, the same discriminator
    # a failed credential store's row already carries.
    failure_rows = [row for row in payload if row["ok"] is False]
    assert len(failure_rows) == 1
    assert str(broken) in failure_rows[0]["reason"]


def test_camp_sessions_all_groups_recoverable_with_an_unparsable_sibling_does_not_promise_then_refuse(
    cli_env,
) -> None:
    """`--recoverable` never reads `answerable_groups_or_refuse`'s degraded
    answer (see `_list_recoverable`'s own strict `load_all_groups` call), so
    a broken sibling config must not ALSO get the "— skipping" notice that
    promises the other groups will still answer — that promise is what the
    following refusal on the very same config would contradict.
    """
    broken = cli_env["config_dir"] / "groups" / "brokengroup.toml"
    broken.write_text("this is not [ valid toml")

    result = _camp(cli_env, "sessions", "--all-groups", "--recoverable")

    assert result.returncode != 0
    assert result.stdout == ""
    assert "skipping" not in result.stderr, result.stderr
    assert "camp: config error:" in result.stderr
    assert str(broken) in result.stderr


def test_camp_sessions_all_groups_every_group_unparsable_states_reason_nonzero(
    tmp_path,
) -> None:
    config_dir = tmp_path / "camp-config"
    groups_dir = config_dir / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "camp-state"
    state_dir.mkdir(parents=True, exist_ok=True)

    broken_a = groups_dir / "brokena.toml"
    broken_a.write_text("not [ valid")
    broken_b = groups_dir / "brokenb.toml"
    broken_b.write_text("also not ] valid")

    env = {**os.environ}
    env["CAMP_CONFIG_DIR"] = str(config_dir)
    env["CAMP_STATE_DIR"] = str(state_dir)

    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "--all-groups", "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "camp sessions: " in result.stderr
    assert str(broken_a) in result.stderr
    assert str(broken_b) in result.stderr


def test_camp_sessions_all_groups_group_with_no_sessions_contributes_no_rows_no_notice(
    cli_env,
) -> None:
    result = _camp(cli_env, "sessions", "--all-groups", "--json")

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert json.loads(result.stdout) == []
    assert result.stderr == ""


def test_camp_sessions_one_store_failing_still_returns_the_others_rows(cli_env) -> None:
    """One store failing degrades to a stderr notice naming the ACCOUNT that
    failed — not the group — while the other store's rows still come back on
    stdout, exit 0.
    """
    account_a = cli_env["tmp_path"] / "partial-account-a"
    account_b = cli_env["tmp_path"] / "partial-account-b"
    root_a = cli_env["tmp_path"] / "repo-partiala"
    _add_account_group(cli_env, "partiala", account_a, root_a)
    _add_account_group(cli_env, "partialb", account_b, cli_env["tmp_path"] / "repo-partialb")
    _register_live_at(account_a, "partial-sess-aaa", root_a)
    _poison_enumerate_at(account_b)

    env = _env_without_shared_claude_dir(cli_env)
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "--group", "partiala"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cli_env["tmp_path"]),
    )

    assert result.returncode == 0, result.stderr
    assert "partial-sess-aaa" in result.stdout
    assert str(account_b) in result.stderr
    assert "camp sessions: could not enumerate sessions for" in result.stderr
    assert "partialb" not in result.stderr


def test_camp_sessions_one_store_failing_json_carries_the_failure_in_band(cli_env) -> None:
    """The `--json` form of the partial-failure case: the failing store
    contributes one row distinguishable from a session row by `ok`, never a
    silent omission a parser could mistake for a complete answer.
    """
    account_a = cli_env["tmp_path"] / "partial-json-account-a"
    account_b = cli_env["tmp_path"] / "partial-json-account-b"
    root_a = cli_env["tmp_path"] / "repo-partialjsona"
    _add_account_group(cli_env, "partialjsona", account_a, root_a)
    _add_account_group(
        cli_env, "partialjsonb", account_b, cli_env["tmp_path"] / "repo-partialjsonb"
    )
    _register_live_at(account_a, "partial-json-sess-aaa", root_a)
    _poison_enumerate_at(account_b)

    env = _env_without_shared_claude_dir(cli_env)
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "sessions", "--group", "partialjsona", "--json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cli_env["tmp_path"]),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    ok_rows = [row for row in payload if row["ok"] is True]
    failed_rows = [row for row in payload if row["ok"] is False]
    assert [row["session_id"] for row in ok_rows] == ["partial-json-sess-aaa"]
    assert len(failed_rows) == 1
    assert failed_rows[0]["account"] == str(account_b)
    assert failed_rows[0]["reason"]
    assert set(ok_rows[0]) == {
        "ok",
        "session_id",
        "cwd",
        "kind",
        "controllable",
        "name",
        "pid",
        "started_at",
        "group",
        "account",
    }
    # A partial-failure row is distinguishable from a session row by testing
    # ONE field (`ok`), and never grows a `group` key — it has no cwd to
    # resolve a group from, so it must not invent session attribution.
    assert set(failed_rows[0]) == {"ok", "account", "reason"}


# ---------------------------------------------------------------------------
# camp sessions --recoverable / --dir
# ---------------------------------------------------------------------------

#: The six keys a recoverable `--json` row carries, and the whole of them. Held
#: as a literal set so a key that is added, dropped, or renamed fails the
#: comparison — a listing whose invocation still works while its output shape
#: has drifted is exactly the failure a verb-and-flag check does not catch.
_RECOVERABLE_ROW_KEYS = {
    "session_id",
    "tmux_name",
    "root",
    "age_seconds",
    "root_missing",
    "unreadable",
}


def _assert_recoverable_row_keys(row: dict) -> None:
    """Assert *row* carries EXACTLY the recoverable row keys, and no more."""
    assert set(row) == _RECOVERABLE_ROW_KEYS, sorted(row)


def _rows(result) -> list[str]:
    """The non-blank stdout lines of a listing."""
    return [line for line in result.stdout.splitlines() if line.strip()]


def _recoverable(cli_env, *args, extra_env=None, cwd=None):
    """Run `camp sessions --recoverable` in the group under test."""
    return _camp(
        cli_env,
        "sessions",
        "--recoverable",
        *args,
        "--group",
        "mygroup",
        extra_env=extra_env,
        cwd=cwd if cwd is not None else cli_env["tmp_path"],
    )


def test_camp_sessions_recoverable_lists_the_dead_ones_newest_first(cli_env) -> None:
    """dead = enumerated − live, and the live one is the one that is missing."""
    third = "cccccccc-3333-4333-8333-333333333333"
    roots = {}
    for session_id, age in ((_UUID_A, 30.0), (_UUID_B, 90.0), (third, 60.0)):
        root = cli_env["tmp_path"] / f"proj-{session_id[:8]}"
        root.mkdir()
        roots[session_id] = root
        _seed_transcript(cli_env, session_id, root, age_seconds=age)
    _register_live(cli_env, third, roots[third])
    before = _state_tree(cli_env)

    result = _recoverable(cli_env)

    assert result.returncode == 0, result.stderr
    rows = _rows(result)
    assert len(rows) == 2, result.stdout
    assert _UUID_A in rows[0]
    assert _UUID_B in rows[1]
    assert third not in result.stdout
    assert _state_tree(cli_env) == before


def test_a_transcript_cwd_cannot_forge_a_row_in_the_listing(cli_env) -> None:
    """The recorded cwd is data from a file, and the listing renders it as data.

    A path is whatever the transcript's author put there. Left raw, an embedded
    newline emits a second row indistinguishable from a real one and a carriage
    return plus an erase sequence rewrites the row already printed — either way
    the operator is shown a session that does not exist and invited to resume a
    reference somebody else chose. One row per candidate, always.
    """
    projects = Path(cli_env["env"]["TRAILHEAD_CLAUDE_DIR"]) / "projects"
    directory = projects / "-forged"
    directory.mkdir(parents=True, exist_ok=True)
    forged = (
        "/tmp/\x1b[2K\rcamp-prod-deadbeef  "
        "99999999-9999-4999-8999-999999999999  /Users/victim/code  2m\nspoofed"
    )
    path = directory / f"{_UUID_A}.jsonl"
    path.write_text(json.dumps({"type": "user", "cwd": forged}) + "\n", encoding="utf-8")

    result = _recoverable(cli_env)

    assert result.returncode == 0, result.stderr
    # One candidate, one row: the embedded newline did not become a row break,
    # and the escape sequence reached the terminal as text rather than as a
    # cursor movement. The forged text still appears — escaped, inside the one
    # row it was always part of — which is the honest rendering of what the
    # transcript actually says.
    assert len(_rows(result)) == 1, result.stdout
    assert not any(c in result.stdout[:-1] for c in "\x1b\r\n")
    assert "\\x1b" in result.stdout and "\\x0a" in result.stdout




def test_camp_sessions_recoverable_omits_a_live_session_absent_from_the_store(
    cli_env,
) -> None:
    """The other half of the subtraction: a live id with no transcript is not dead."""
    root = cli_env["tmp_path"] / "proj-a"
    root.mkdir()
    _seed_transcript(cli_env, _UUID_A, root)
    _register_live(cli_env, _UUID_B, root)
    before = _state_tree(cli_env)

    result = _recoverable(cli_env)

    assert result.returncode == 0, result.stderr
    assert len(_rows(result)) == 1
    assert _UUID_A in result.stdout
    assert _UUID_B not in result.stdout
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_scopes_to_the_workspace_subtree(cli_env) -> None:
    """Subtree, not exact: a session in a SUBDIRECTORY of the workspace is in scope.

    This is the pin that stops the two halves of the subtraction disagreeing —
    an exact-match scope on one side and a prefix scope on the other would list
    a live session as dead.
    """
    workspace = _workspace_launch_dir(cli_env, "feat-scope")
    nested = workspace / "member" / "src"
    nested.mkdir(parents=True)
    outside = cli_env["tmp_path"] / "elsewhere"
    outside.mkdir()
    _seed_transcript(cli_env, _UUID_A, nested, age_seconds=30.0)
    _seed_transcript(cli_env, _UUID_B, outside, age_seconds=60.0)
    before = _state_tree(cli_env)

    result = _camp(
        cli_env, "sessions", "--recoverable", "feat-scope", "--group", "mygroup",
        cwd=cli_env["tmp_path"],
    )

    assert result.returncode == 0, result.stderr
    assert len(_rows(result)) == 1, result.stdout
    assert _UUID_A in result.stdout
    assert _UUID_B not in result.stdout
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_dir_scopes_the_same_way(cli_env) -> None:
    scope = cli_env["tmp_path"] / "scoped"
    nested = scope / "deep" / "deeper"
    nested.mkdir(parents=True)
    outside = cli_env["tmp_path"] / "unscoped"
    outside.mkdir()
    _seed_transcript(cli_env, _UUID_A, nested, age_seconds=30.0)
    _seed_transcript(cli_env, _UUID_B, outside, age_seconds=60.0)
    before = _state_tree(cli_env)

    result = _recoverable(cli_env, "--dir", str(scope))

    assert result.returncode == 0, result.stderr
    assert len(_rows(result)) == 1, result.stdout
    assert _UUID_A in result.stdout
    assert _UUID_B not in result.stdout
    assert _state_tree(cli_env) == before


def test_camp_sessions_dir_scopes_the_live_listing(cli_env) -> None:
    """`--dir` on the LIVE form scopes it the same way, so both halves agree."""
    scope = cli_env["tmp_path"] / "livescope"
    nested = scope / "member"
    nested.mkdir(parents=True)
    outside = cli_env["tmp_path"] / "liveelsewhere"
    outside.mkdir()
    _register_live(cli_env, _UUID_A, nested)
    _register_live(cli_env, _UUID_B, outside)
    before = _state_tree(cli_env)

    result = _camp(
        cli_env, "sessions", "--dir", str(scope), "--group", "mygroup",
        cwd=cli_env["tmp_path"],
    )

    assert result.returncode == 0, result.stderr
    assert len(_rows(result)) == 1, result.stdout
    assert _UUID_A in result.stdout
    assert _UUID_B not in result.stdout
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_undeterminable_live_set_degrades_to_empty(
    cli_env,
) -> None:
    """Never an unsubtracted pool presented as dead — a notice and nothing else."""
    root = cli_env["tmp_path"] / "proj-a"
    root.mkdir()
    _seed_transcript(cli_env, _UUID_A, root)
    _seed_transcript(cli_env, _UUID_B, root)
    before = _state_tree(cli_env)

    result = _recoverable(cli_env, extra_env={"CAMP_FAKE_ENUMERATE": "fail"})

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert _UUID_A not in result.stdout
    assert _UUID_B not in result.stdout
    assert "camp sessions: could not determine" in result.stderr
    assert "no recoverable sessions" not in result.stderr
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_degraded_json_is_an_empty_list(cli_env) -> None:
    root = cli_env["tmp_path"] / "proj-a"
    root.mkdir()
    _seed_transcript(cli_env, _UUID_A, root)
    before = _state_tree(cli_env)

    result = _recoverable(cli_env, "--json", extra_env={"CAMP_FAKE_ENUMERATE": "fail"})

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []
    assert "camp sessions: could not determine" in result.stderr
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_marks_a_root_that_no_longer_exists(cli_env) -> None:
    """A torn-down root is listed and MARKED, never hidden."""
    gone = cli_env["tmp_path"] / "torn-down"
    _seed_transcript(cli_env, _UUID_A, gone)
    before = _state_tree(cli_env)

    human = _recoverable(cli_env)
    payload = json.loads(_recoverable(cli_env, "--json").stdout)

    assert human.returncode == 0, human.stderr
    rows = _rows(human)
    assert len(rows) == 1
    assert str(gone) in rows[0]
    assert "(gone)" in rows[0]
    assert len(payload) == 1
    assert payload[0]["root"] == str(gone)
    assert payload[0]["root_missing"] is True
    assert payload[0]["unreadable"] is False
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_lists_an_unreadable_transcript(cli_env) -> None:
    """No extractable cwd: a uuid-and-age row, never a guessed location."""
    _seed_transcript(cli_env, _UUID_A, None, age_seconds=120.0)
    before = _state_tree(cli_env)

    human = _recoverable(cli_env)
    payload = json.loads(_recoverable(cli_env, "--json").stdout)

    assert human.returncode == 0, human.stderr
    rows = _rows(human)
    assert len(rows) == 1
    assert _UUID_A in rows[0]
    assert "2m" in rows[0]
    assert len(payload) == 1
    assert payload[0]["unreadable"] is True
    assert payload[0]["root"] is None
    assert payload[0]["root_missing"] is False
    assert payload[0]["tmux_name"] == f"camp-{_UUID_A[:8]}"
    assert _state_tree(cli_env) == before


def _seed_many(cli_env, count: int) -> list[str]:
    """Seed *count* transcripts, newest first; return their ids in that order."""
    ids = []
    for index in range(count):
        session_id = f"{index:08d}-1111-4111-8111-111111111111"
        root = cli_env["tmp_path"] / "many" / f"p{index}"
        root.mkdir(parents=True)
        _seed_transcript(cli_env, session_id, root, age_seconds=60.0 + index)
        ids.append(session_id)
    return ids


def test_camp_sessions_recoverable_caps_at_twenty_and_names_the_total(cli_env) -> None:
    ids = _seed_many(cli_env, 25)
    before = _state_tree(cli_env)

    result = _recoverable(cli_env)

    assert result.returncode == 0, result.stderr
    rows = _rows(result)
    assert len(rows) == 20
    assert [row.split()[1] for row in rows] == ids[:20]
    assert "25" in result.stderr
    assert "--all" in result.stderr
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_limit_narrows_the_listing(cli_env) -> None:
    ids = _seed_many(cli_env, 25)
    before = _state_tree(cli_env)

    result = _recoverable(cli_env, "--limit", "5")

    assert result.returncode == 0, result.stderr
    rows = _rows(result)
    assert len(rows) == 5
    assert [row.split()[1] for row in rows] == ids[:5]
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_all_lists_every_candidate(cli_env) -> None:
    ids = _seed_many(cli_env, 25)
    before = _state_tree(cli_env)

    result = _recoverable(cli_env, "--all")

    assert result.returncode == 0, result.stderr
    rows = _rows(result)
    assert len(rows) == 25
    assert [row.split()[1] for row in rows] == ids
    assert _state_tree(cli_env) == before


@pytest.mark.parametrize("value", ["0", "-1", "notanumber"])
def test_camp_sessions_recoverable_unusable_limit_is_a_clean_refusal(
    cli_env, value: str
) -> None:
    """A limit that cannot mean anything is a refusal, never a silently empty list."""
    _seed_many(cli_env, 3)
    before = _state_tree(cli_env)

    result = _recoverable(cli_env, "--limit", value)

    _assert_clean_refusal(result, needle="--limit", verb="sessions")
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_limit_and_all_together_refuse(cli_env) -> None:
    before = _state_tree(cli_env)

    result = _recoverable(cli_env, "--limit", "5", "--all")

    _assert_clean_refusal(result, needle="mutually exclusive", verb="sessions")
    assert _state_tree(cli_env) == before


@pytest.mark.parametrize("flags", [("--limit", "5"), ("--all",)])
def test_camp_sessions_cap_flags_without_recoverable_refuse(cli_env, flags) -> None:
    """The cap belongs to the recoverable listing; the live form has no cap to widen."""
    before = _state_tree(cli_env)

    result = _camp(
        cli_env, "sessions", *flags, "--group", "mygroup", cwd=cli_env["tmp_path"]
    )

    _assert_clean_refusal(result, needle="--recoverable", verb="sessions")
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_empty_names_itself_and_exits_zero(cli_env) -> None:
    before = _state_tree(cli_env)

    result = _recoverable(cli_env)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "no recoverable sessions" in result.stderr
    assert "keeps no session transcripts" not in result.stderr
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_json_with_no_rows_is_an_empty_list(cli_env) -> None:
    before = _state_tree(cli_env)

    result = _recoverable(cli_env, "--json")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_refuses_when_the_harness_keeps_no_transcripts(
    cli_env,
) -> None:
    """The one non-degrading path on a question verb, and it names the harness."""
    before = _state_tree(cli_env)

    unsupported = _recoverable(cli_env, extra_env={"CAMP_FAKE_TRANSCRIPTS": "none"})
    empty = _recoverable(cli_env)

    _assert_clean_refusal(unsupported, needle="fakeharness", verb="sessions")
    assert "no recoverable sessions" not in unsupported.stderr
    assert unsupported.stderr != empty.stderr
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_json_rows_carry_exactly_the_pinned_keys(
    cli_env,
) -> None:
    root = cli_env["tmp_path"] / "proj-a"
    root.mkdir()
    _seed_transcript(cli_env, _UUID_A, root, age_seconds=45.0)
    before = _state_tree(cli_env)

    result = _recoverable(cli_env, "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    _assert_recoverable_row_keys(payload[0])
    # The live form's harness-native display name never reaches a caller here.
    assert "name" not in payload[0]
    assert payload[0]["session_id"] == _UUID_A
    assert payload[0]["tmux_name"] == f"camp-proj-a-{_UUID_A[:8]}"
    assert payload[0]["root"] == str(root)
    # Whole seconds, and the transcript's own age rather than a constant — but
    # not pinned to the exact integer: the clock keeps running between seeding
    # the transcript and the CLI reading it, so an equality check here fails
    # whenever start-up straddles a second boundary.
    assert isinstance(payload[0]["age_seconds"], int)
    assert 45 <= payload[0]["age_seconds"] <= 75
    assert _state_tree(cli_env) == before


def test_the_row_key_assertion_fails_on_a_renamed_key() -> None:
    """The shape check above is only worth having if a renamed key breaks it."""
    renamed = {key: None for key in _RECOVERABLE_ROW_KEYS}
    renamed["name"] = renamed.pop("tmux_name")

    with pytest.raises(AssertionError):
        _assert_recoverable_row_keys(renamed)

    added = {key: None for key in _RECOVERABLE_ROW_KEYS}
    added["kind"] = "agent"

    with pytest.raises(AssertionError):
        _assert_recoverable_row_keys(added)


def test_camp_sessions_recoverable_breaks_an_mtime_tie_by_uuid(cli_env) -> None:
    """Two transcripts written at the same instant still order deterministically."""
    root = cli_env["tmp_path"] / "tied"
    root.mkdir()
    seeded = [
        _seed_transcript(cli_env, _UUID_B, root, age_seconds=300.0),
        _seed_transcript(cli_env, _UUID_A, root, age_seconds=300.0),
    ]
    # Stamped from ONE clock reading, so the tiebreak is genuinely exercised —
    # two `time.time()` calls differ by microseconds and would order themselves.
    stamp = time.time() - 300.0
    for path in seeded:
        os.utime(path, (stamp, stamp))
    before = _state_tree(cli_env)

    result = _recoverable(cli_env, "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [row["session_id"] for row in payload] == [_UUID_A, _UUID_B]
    assert payload[0]["age_seconds"] == payload[1]["age_seconds"]
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_ages_are_compact_durations(cli_env) -> None:
    """`2d`, `4h`, `9m` — readable at a glance on a phone, not raw seconds."""
    root = cli_env["tmp_path"] / "aged"
    root.mkdir()
    expected = {}
    for session_id, age, rendered in (
        ("11111111-1111-4111-8111-111111111111", 9 * 60.0, "9m"),
        ("22222222-2222-4222-8222-222222222222", 4 * 3600.0, "4h"),
        ("33333333-3333-4333-8333-333333333333", 2 * 86400.0, "2d"),
    ):
        _seed_transcript(cli_env, session_id, root, age_seconds=age)
        expected[session_id] = rendered
    before = _state_tree(cli_env)

    result = _recoverable(cli_env)

    assert result.returncode == 0, result.stderr
    rows = _rows(result)
    assert len(rows) == 3
    for row in rows:
        session_id = row.split()[1]
        assert row.split()[-1] == expected[session_id], row
    assert _state_tree(cli_env) == before


#: Transcripts in the synthetic store the scan budget is measured against.
_SCAN_BUDGET_TRANSCRIPTS = 500

#: Body lines appended to each transcript in the store's FAT form. Real
#: transcripts run to hundreds of megabytes of short records; this many short
#: lines reproduces that shape at roughly 400KB apiece.
_SCAN_BUDGET_BODY_LINES = 3000

#: How much longer the FAT store's listing may take than the LEAN store's. The
#: two runs differ in exactly one thing — bytes of transcript body — so a
#: head scan bounded to a fixed number of leading records is near-invariant
#: across them (measured: ~10ms over ~200MB of added body) while a scan that
#: runs to end-of-file is not (measured: ~1.7s). The allowance sits between
#: those two by a wide margin in both directions, and because it compares two
#: runs on the SAME machine seconds apart, a slow or loaded machine moves both
#: halves together instead of tripping it.
_SCAN_BODY_ALLOWANCE_SECONDS = 0.75

#: Absolute ceiling on the fat-store listing, interpreter start-up included.
#: Many times the measured cost, so it catches a gross slowdown without ever
#: being reachable by ordinary machine variance.
_SCAN_CEILING_SECONDS = 10.0


def _build_scan_store(cli_env, *, body: str) -> None:
    """Author the synthetic scan store, each transcript carrying *body* after its head.

    HALF the transcripts record a `cwd` in their head and half record none. The
    cwd-less half is the load-bearing one: a scan that finds a cwd stops there
    whatever follows it, so only a transcript with NO cwd to find can show
    whether the search gives up after a bounded number of records or reads to
    end-of-file. Both halves are equally common in a real store.

    A nested `<uuid>/subagents/` transcript sits beside each one, so a recursive
    glob would double the row count.
    """
    projects = Path(cli_env["env"]["TRAILHEAD_CLAUDE_DIR"]) / "projects"
    for index in range(_SCAN_BUDGET_TRANSCRIPTS):
        session_id = f"{index:08d}-4444-4444-8444-444444444444"
        cwd = cli_env["tmp_path"] / "bulk" / f"p{index}"
        directory = projects / str(cwd).replace("/", "-").replace(".", "-")
        directory.mkdir(parents=True, exist_ok=True)
        second = (
            {"type": "user", "cwd": str(cwd)}
            if index % 2 == 0
            else {"type": "user", "text": "this record carries no cwd"}
        )
        (directory / f"{session_id}.jsonl").write_text(
            json.dumps({"type": "summary"}) + "\n" + json.dumps(second) + "\n" + body,
            encoding="utf-8",
        )
        nested = directory / session_id / "subagents"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / f"{index:08d}-5555-4555-8555-555555555555.jsonl").write_text(
            json.dumps({"type": "user", "cwd": str(cwd)}) + "\n", encoding="utf-8"
        )


def _time_scan(cli_env) -> float:
    """The best of two global recoverable listings, in seconds.

    Best-of-two rather than a single reading: one scheduling hiccup during a
    single run would otherwise be indistinguishable from a real regression.
    """
    best = None
    for _ in range(2):
        started = time.monotonic()
        result = _recoverable(cli_env, "--all", "--json")
        elapsed = time.monotonic() - started
        assert result.returncode == 0, result.stderr
        assert len(json.loads(result.stdout)) == _SCAN_BUDGET_TRANSCRIPTS, result.stdout
        best = elapsed if best is None else min(best, elapsed)
    return best


def test_camp_sessions_recoverable_scan_stays_within_its_budget(cli_env) -> None:
    """A silent 10x slowdown is otherwise invisible until it is felt on a phone.

    Two regressions are in scope, and each has its own assertion:

    * A RECURSIVE glob would sweep in the nested subagent transcripts every real
      store is full of. Caught by the row count, with no clock involved.
    * SEARCHING A WHOLE TRANSCRIPT for its cwd, rather than a bounded head of
      it, would make the scan scale with transcript size. Caught by running the
      identical listing over the identical 500 transcripts twice — once with
      empty bodies, once with large ones — and requiring the difference to stay
      small. The comparison is against the same machine seconds earlier, so it
      measures the scan's dependence on body size rather than the machine's
      speed.
    """
    _build_scan_store(cli_env, body="")
    lean = _time_scan(cli_env)

    body = (
        "\n".join(
            json.dumps({"type": "assistant", "text": "x" * 100})
            for _ in range(_SCAN_BUDGET_BODY_LINES)
        )
        + "\n"
    )
    _build_scan_store(cli_env, body=body)
    fat = _time_scan(cli_env)

    assert fat < _SCAN_CEILING_SECONDS, f"took {fat:.2f}s"
    assert fat - lean < _SCAN_BODY_ALLOWANCE_SECONDS, (
        f"lean {lean:.2f}s, fat {fat:.2f}s — the scan is reading transcript bodies"
    )


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
    line is appended to stderr instead of perturbing it.

    Carries one INTENTIONAL new line since
    task/the-binding-the-operator-s-ordinary-window-creation-key-opens-a-camp-composed-window:
    `create_workspace_session` now installs the window-creation-key binding
    on every session it CREATES and prints a one-line notice the first time
    a given tmux server gets it (plan bullet: "print one line on first
    install... not on every keypress" — mirroring
    `camp.attach.prefix_warning`'s notice for the same class of surprise).
    This test's `cli_env` starts a fresh stub-tmux table per test, so this
    IS that server's first install; a second `camp new` against the SAME
    stub-tmux state (not exercised here) would not repeat it — see
    `test_launch_binding.py` / `test_window_binding_end_to_end.py` for that
    per-server, not per-`camp new`, behaviour pinned directly."""
    result = _camp(cli_env, "new", "feat-o", "--group", "mygroup")

    assert result.returncode == 0, result.stderr
    ws_dir = Path(cli_env["state_dir"]) / "mygroup" / "worktrees" / "feat-o"
    assert result.stdout == f"{ws_dir}\n"
    assert result.stderr == (
        "camp new: created workspace 'feat-o' — provisioning in the background\n"
        "  check provisioning: camp status feat-o\n"
        "  activates when ready, or run: camp activate feat-o\n"
        "camp: installed a tmux key binding — prefix+c now opens a camp-composed window\n"
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
# The exit-code contract in `camp help` against the codes the binary returns.
#
# A help text that documents a contract the binary does not honor is worse than
# no help text: it teaches a reader to trust a number that means something else.
# So the codes come out of the emitter rather than out of a literal here, and
# each one is then provoked for real.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# camp kill
# ---------------------------------------------------------------------------


def _tmux_calls(cli_env, verb: str) -> list[list[str]]:
    """Every tmux invocation of *verb* camp made, as argv lists."""
    return [
        line.split("\t")
        for line in cli_env["tmux_argv_file"].read_text(encoding="utf-8").splitlines()
        if line.split("\t")[:1] == [verb]
    ]


def _launched_session(cli_env, slug: str = "feat-kill") -> str:
    """Create a workspace and register a live, camp-kill-recognizable session
    for it, and return the session id.

    No camp verb composes a live session in this shape, so this registers
    directly against the fake tmux double — in the SAME shape `camp kill`
    recognizes:
    the harness's resume composition (`FakeHarness.session_resume`), scrubbed
    exactly as `camp.launch.stop._owning_commands` composes it.
    """
    import uuid

    from camp.group.config import load_all_groups
    from camp.launch.recovery import derive_name_component

    ws_dir = Path(_new_workspace(cli_env, slug)).resolve()
    groups = load_all_groups(cli_env["config_dir"] / "groups")
    component = derive_name_component(ws_dir, groups, env=cli_env["env"])
    session_id = str(uuid.uuid4())
    tmux_name = f"camp-{component}-{session_id[:8]}"

    result = subprocess.run(
        ["tmux", "new-session", "-d", "-s", tmux_name, "-c", str(ws_dir),
         "env", "fake-resume", session_id],
        env=cli_env["env"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return session_id


def test_camp_kill_stops_a_launched_session(cli_env) -> None:
    """The whole verb: a ref to a camp-owned session is signalled and confirmed gone."""
    session_id = _launched_session(cli_env)

    result = _camp(cli_env, "kill", session_id[:8], cwd=cli_env["tmp_path"])

    assert result.returncode == 0, result.stderr
    assert _tmux_calls(cli_env, "kill-session") == [
        ["kill-session", "-t", f"=camp-feat-kill-{session_id[:8]}"]
    ]
    assert session_id in result.stderr
    assert "stopped" in result.stderr
    assert "camp sessions --recoverable" in result.stderr
    assert "camp attach" in result.stderr
    assert result.stdout == f"{session_id}\n"  # stdout is the session id alone


def test_camp_kill_of_a_name_that_prefixes_another_live_session_never_touches_it(
    cli_env, monkeypatch
) -> None:
    """`camp kill` targets `-t` `=`-exact. The default `_TMUX_STUB` already
    resolves a target by exact dict-key lookup (it strips `=` and looks the
    result up directly), so it cannot distinguish "qualified" from "not" —
    only a stub that reproduces tmux's OWN bare-target prefix fallback can.
    This swaps one in for this test alone: a bare `-t` resolves by prefix
    when there is no exact match; an `=`-qualified one never does.

    Fails against a naive "strip the `=` and match exactly, whether or not
    one was given" reading of the property, and against the pre-task code
    (bare `-t` throughout), which would resolve the kill against whichever
    live session the short name happens to prefix.
    """
    session_id = _launched_session(cli_env)
    tmux_name = f"camp-feat-kill-{session_id[:8]}"

    # A second, unrelated live session whose name the one being killed is a
    # STRICT PREFIX of. Seeded directly into the table the stub reads, the
    # same mechanism `test_camp_list.py`'s real-parser test uses.
    table = json.loads(cli_env["tmux_table_file"].read_text(encoding="utf-8"))
    survivor = f"{tmux_name}-longer"
    table[survivor] = "sleep 100000"
    cli_env["tmux_table_file"].write_text(json.dumps(table), encoding="utf-8")

    prefix_matching_stub = _TMUX_STUB.replace(
        "def _target():\n"
        "    for i, arg in enumerate(args):\n"
        '        if arg == "-t" and i + 1 < len(args):\n'
        '            return args[i + 1].lstrip("=")\n'
        "    return None\n",
        "def _target():\n"
        "    for i, arg in enumerate(args):\n"
        '        if arg == "-t" and i + 1 < len(args):\n'
        "            raw = args[i + 1]\n"
        '            if raw.startswith("="):\n'
        "                return raw[1:]\n"
        "            table = _table()\n"
        "            if raw in table:\n"
        "                return raw\n"
        "            matches = [n for n in table if n.startswith(raw)]\n"
        "            return matches[0] if len(matches) == 1 else None\n"
        "    return None\n",
    )
    assert prefix_matching_stub != _TMUX_STUB, "the stub's _target() shape moved"
    stub_dir = cli_env["tmp_path"] / "prefix-matching-tmux"
    stub_dir.mkdir()
    stub_path = stub_dir / "tmux"
    stub_path.write_text(prefix_matching_stub, encoding="utf-8")
    stub_path.chmod(0o755)

    result = _camp(
        cli_env,
        "kill",
        session_id[:8],
        cwd=cli_env["tmp_path"],
        extra_env={"PATH": f"{stub_dir}{os.pathsep}{cli_env['env']['PATH']}"},
    )

    assert result.returncode == 0, result.stderr
    table_after = json.loads(cli_env["tmux_table_file"].read_text(encoding="utf-8"))
    assert survivor in table_after, "the colliding, unrelated session was killed"
    assert tmux_name not in table_after


def test_camp_kill_is_reachable_with_no_group_resolvable(cli_env) -> None:
    """Run from a directory no group resolves from, alongside a group config
    camp cannot parse — the phone-side situation this verb exists for. A
    sibling group's broken toml must not take the stop surface down with it."""
    session_id = _launched_session(cli_env)
    (cli_env["config_dir"] / "groups" / "brokengroup.toml").write_text(
        "this is not = valid = toml\n", encoding="utf-8"
    )

    result = _camp(cli_env, "kill", session_id[:8], cwd=cli_env["tmp_path"])

    assert result.returncode == 0, result.stderr
    assert "config error" not in result.stderr


def test_camp_kill_of_an_already_down_session_is_success(cli_env) -> None:
    """Idempotence: re-running a stop after a dropped connection is not an error."""
    launch_dir = _workspace_launch_dir(cli_env, "feat-down")
    _seed_transcript(cli_env, _UUID_A, launch_dir)

    result = _camp(cli_env, "kill", _UUID_A, cwd=cli_env["tmp_path"])

    assert result.returncode == 0, result.stderr
    assert "already down" in result.stderr
    assert _tmux_calls(cli_env, "kill-session") == []


def test_camp_kill_will_not_call_a_session_already_down_when_it_cannot_see_live_ones(
    cli_env,
) -> None:
    """The already-down oracle needs BOTH halves of the answer: not live AND no
    tmux session. A live enumeration that failed does not say "not live" — it
    says nothing — and folding it into an empty live set would report a running
    session, still holding its memory, as reclaimed."""
    launch_dir = _workspace_launch_dir(cli_env, "feat-blind")
    _seed_transcript(cli_env, _UUID_A, launch_dir)

    result = _camp(
        cli_env,
        "kill",
        _UUID_A,
        cwd=cli_env["tmp_path"],
        extra_env={"CAMP_FAKE_ENUMERATE": "fail"},
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "was already down" not in result.stderr
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("camp kill: ")
    assert "live" in lines[0]
    assert _tmux_calls(cli_env, "kill-session") == []


def test_camp_kill_with_an_ambiguous_ref_exits_two_and_lists_candidates(cli_env) -> None:
    first = _workspace_launch_dir(cli_env, "feat-one")
    second = _workspace_launch_dir(cli_env, "feat-two")
    _seed_transcript(cli_env, _UUID_A, first)
    _seed_transcript(cli_env, _UUID_B, second)

    result = _camp(cli_env, "kill", "camp-feat-", cwd=cli_env["tmp_path"])

    assert result.returncode == 2
    rows = result.stdout.strip().splitlines()
    assert len(rows) == 2
    assert {row.split()[1] for row in rows} == {_UUID_A, _UUID_B}
    assert result.stderr.strip().splitlines() == [
        "camp kill: 'camp-feat-' matches 2 sessions (listed above) — re-run with "
        "a longer prefix naming exactly one"
    ]
    assert _tmux_calls(cli_env, "kill-session") == []


def test_camp_kill_refuses_a_ref_matching_under_two_credential_stores(cli_env) -> None:
    """The pinned regression: before the pool was keyed by (harness, store), two
    groups sharing a harness with different accounts collapsed to ONE queried
    store, so a session sitting in the unqueried account was invisible and a
    ref could resolve straight to the queried one. Re-keying makes both stores
    answer, so the match is ambiguous — never a silent resolve.
    """
    account_a = cli_env["tmp_path"] / "account-a"
    account_b = cli_env["tmp_path"] / "account-b"
    _add_account_group(cli_env, "accta", account_a, cli_env["tmp_path"] / "repo-accta")
    _add_account_group(cli_env, "acctb", account_b, cli_env["tmp_path"] / "repo-acctb")

    root_a = cli_env["tmp_path"] / "root-a"
    root_b = cli_env["tmp_path"] / "root-b"
    root_a.mkdir()
    root_b.mkdir()
    _seed_transcript_at(account_a, "sess-shared-aaa", root_a)
    _seed_transcript_at(account_b, "sess-shared-bbb", root_b)

    env = _env_without_shared_claude_dir(cli_env)
    result = subprocess.run(
        [sys.executable, str(_CLI_CAMP), "kill", "sess-shared-"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cli_env["tmp_path"]),
    )

    assert result.returncode == 2, result.stderr
    rows = result.stdout.strip().splitlines()
    assert len(rows) == 2, result.stdout
    assert {row.split()[1] for row in rows} == {"sess-shared-aaa", "sess-shared-bbb"}
    message = result.stderr.strip()
    assert message.startswith(
        "camp kill: 'sess-shared-' matches 2 sessions (listed above) — "
    )
    assert str(account_a) in message
    assert str(account_b) in message
    assert message.endswith("re-run with a longer prefix naming exactly one")


def test_camp_kill_of_a_session_still_present_after_the_kill_fails(cli_env) -> None:
    """The memory was not reclaimed, so the command failed and says so — never a
    success with a caveat."""
    session_id = _launched_session(cli_env)

    result = _camp(
        cli_env,
        "kill",
        session_id[:8],
        cwd=cli_env["tmp_path"],
        extra_env={"CAMP_FAKE_TMUX_UNDEAD": "1"},
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip().splitlines() == [
        f"camp kill: session {session_id} is still running as "
        f"camp-feat-kill-{session_id[:8]} after the stop — its memory was not "
        "reclaimed"
    ]


def test_camp_kill_of_a_ref_matching_nothing_is_not_already_down(cli_env) -> None:
    """A zero-match ref is a ref problem and gets its own wording; reporting it
    as already-down would tell the operator their session is gone."""
    _seed_transcript(cli_env, _UUID_A, _workspace_launch_dir(cli_env, "feat-one"))

    result = _camp(cli_env, "kill", "no-such-ref", cwd=cli_env["tmp_path"])

    assert result.returncode == 1
    assert result.stdout == ""
    assert "already down" not in result.stderr
    assert result.stderr.strip().splitlines() == [
        "camp kill: no candidate matched `no-such-ref`; run "
        "`camp sessions --recoverable` to see what camp can address"
    ]


def test_camp_kill_of_a_live_session_with_no_tmux_session_is_its_own_refusal(
    cli_env,
) -> None:
    """Live but holding no tmux session: there is nothing here for camp to
    signal, which is a different situation — and a different next move — from a
    foreign pane sitting on the name."""
    launch_dir = _workspace_launch_dir(cli_env, "feat-orphan")
    _seed_transcript(cli_env, _UUID_A, launch_dir)
    _register_live(cli_env, _UUID_A, launch_dir)

    result = _camp(cli_env, "kill", _UUID_A, cwd=cli_env["tmp_path"])

    assert result.returncode == 1
    assert result.stdout == ""
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1
    assert "no tmux session" in lines[0]
    assert "did not launch" not in lines[0]


def test_camp_kill_of_a_foreign_pane_says_the_name_is_held_by_something_else(
    cli_env,
) -> None:
    """A name match is not ownership. The copy names the pane, not the session,
    because the operator's next move is to look at what is holding the name."""
    launch_dir = _workspace_launch_dir(cli_env, "feat-squat")
    _seed_transcript(cli_env, _UUID_A, launch_dir)
    _register_live(cli_env, _UUID_A, launch_dir)
    cli_env["tmux_table_file"].write_text(
        json.dumps({f"camp-feat-squat-{_UUID_A[:8]}": "sleep 9999"}), encoding="utf-8"
    )

    result = _camp(cli_env, "kill", _UUID_A, cwd=cli_env["tmp_path"])

    assert result.returncode == 1
    assert result.stdout == ""
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1
    assert "camp did not launch" in lines[0]
    assert _tmux_calls(cli_env, "kill-session") == []


def test_camp_kill_against_an_unanswering_tmux_fails_distinctly_and_promptly(
    cli_env,
) -> None:
    """A tmux that hangs must not read as either a slow-but-working stop or a
    completed one: camp bounds the wait, then says it could not tell."""
    session_id = _launched_session(cli_env)

    started = time.monotonic()
    result = _camp(
        cli_env,
        "kill",
        session_id[:8],
        cwd=cli_env["tmp_path"],
        extra_env={"CAMP_FAKE_TMUX_HANG": "120"},
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 1
    assert result.stdout == ""
    assert elapsed < 60  # bounded: nowhere near the 120s tmux was told to sleep
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1
    assert "tmux did not answer" in lines[0]


def test_camp_kill_json_distinguishes_stopped_from_already_down(cli_env) -> None:
    """The two outcomes share exit 0, so the payload is what tells them apart."""
    launch_dir = _workspace_launch_dir(cli_env, "feat-kill")
    session_id = _launched_session(cli_env, "feat-kill")
    # The transcript is what keeps the session addressable once it is down —
    # without it the second stop would have no candidate to answer about.
    _seed_transcript(cli_env, session_id, launch_dir)

    stopped = _camp(cli_env, "kill", session_id[:8], "--json", cwd=cli_env["tmp_path"])
    assert stopped.returncode == 0, stopped.stderr

    down = _camp(cli_env, "kill", session_id[:8], "--json", cwd=cli_env["tmp_path"])
    assert down.returncode == 0, down.stderr

    first = json.loads(stopped.stdout)
    second = json.loads(down.stdout)
    assert set(first) == set(second)
    assert first["outcome"] == "stopped"
    assert second["outcome"] == "already-down"
    assert first["session_id"] == second["session_id"] == session_id
    assert first["tmux_name"] == f"camp-feat-kill-{session_id[:8]}"


def test_camp_kill_without_a_reference_refuses_on_one_line(cli_env) -> None:
    result = _camp(cli_env, "kill", cwd=cli_env["tmp_path"])

    assert result.returncode == 1
    assert result.stdout == ""
    assert len(result.stderr.strip().splitlines()) == 1
    assert result.stderr.startswith("camp kill: ")
    # The reference-shape hint names verbs camp still dispatches.
    assert "camp sessions" in result.stderr
    assert "camp attach" in result.stderr


def test_camp_kill_writes_nothing_under_camp_state_dir(cli_env) -> None:
    session_id = _launched_session(cli_env)
    before = _state_tree(cli_env)

    result = _camp(cli_env, "kill", session_id[:8], cwd=cli_env["tmp_path"])

    assert result.returncode == 0, result.stderr
    assert _state_tree(cli_env) == before


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
