"""CLI surface: camp launch, camp sessions, camp new --launch.

Exercised end-to-end through the REAL `cli/camp` binary (house convention), with
the harness seam and tmux replaced by stand-ins rather than by monkeypatching
camp's own code:

- a `FakeHarness` registered into trailhead's harness registry from a
  `sitecustomize.py` on PYTHONPATH, selected by `[harness] binary = "fakeharness"`
  in the group config. Its `session_launch` argv ends in the session id and its
  `session_enumerate` argv filters a file by the scope it was handed, so
  enumeration is a real subprocess reading real state and honoring the seam's
  subtree scoping.
- a `tmux` stub earlier on PATH that appends the launched session id to that same
  file — so a launch really does become visible to a really-executed enumeration,
  and suppressing the append (CAMP_FAKE_TMUX_NO_REGISTER) reproduces the
  never-confirms failure without any in-process patching. It also logs every argv
  it was handed, so a test can read the exact `-s` name and `-c` directory camp
  asked tmux for.

Test contract:
- camp launch success: stdout is exactly the session id + newline; stderr carries
  the paste-ready `tmux attach -t <name>` line; --json carries the session id.
- camp launch refusals (unknown harness, unconfirmed launch): empty stdout, a
  single `camp launch: …` stderr line, non-zero exit.
- camp launch --dir: rooted at the named directory, named `camp-<basename>-<uuid8>`,
  spawned with `-c <dir>`; `--json` carries EXACTLY the slug launch's key set.
  Every refusal — a slug or --resume alongside it, a missing explicit --group, a
  path that is not an existing directory, an ineligible directory — leaves stdout
  empty, spawns nothing, and writes nothing under CAMP_STATE_DIR.
- camp launch --resume: a session addressed by full id, id prefix, or derived-name
  prefix reaches the identical spawn, rooted where its transcript records it
  started. A workspace-rooted resume takes no --group; every other root demands
  one and then clears the eligibility gate against CURRENT config. Each refusal is
  its own situation and its own wording — already running, directory unknowable,
  directory gone, root ineligible, ref matched nothing (against a populated store
  vs. an empty one), harness cannot enumerate or re-enter — and an ambiguous ref
  is exit 2 with the candidates on stdout. Nothing is written under CAMP_STATE_DIR
  on any of them, and `camp resume` (the bookmark verb) still answers about
  bookmarks.
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
- camp new --launch: stdout is the workspace path alone on BOTH success and
  launch failure, exit 0 in both; --json replaces that with
  {"workspace", "session_id", "tmux_name"} /
  {"workspace", "session_id": null, "tmux_name": null}.
- --no-wait skips the provisioning wait and names `camp status <slug>`; the
  default path runs the provisioning wait and the confirmation wait back to back.
- Bare `camp new` output is byte-identical to the pre-`--launch` surface.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"

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
_ENUMERATE_FILTER = """
import sys
from pathlib import Path

rows_path = sys.argv[1]
scope = sys.argv[2]
scope_path = Path(scope).resolve() if scope else None
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

    def session_launch(self, workspace, session_id):
        return ["fake-launch", session_id]

    def session_launch_modality(self):
        return "detached"

    def session_launch_env_unset(self):
        return []

    def session_transcripts(self, workspace=None, *, env=None):
        if os.environ.get("CAMP_FAKE_TRANSCRIPTS") == "none":
            return None
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
            os.environ["CAMP_FAKE_SESSIONS_FILE"],
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
'''

_TMUX_STUB = '''#!/usr/bin/env python3
"""tmux stand-in: registers a new-session's session id for enumeration."""
import os
import sys

args = sys.argv[1:]
argv_log = os.environ.get("CAMP_FAKE_TMUX_ARGV_FILE")
if argv_log:
    with open(argv_log, "a") as handle:
        handle.write("\\t".join(args) + "\\n")
if args and args[0] == "new-session" and not os.environ.get("CAMP_FAKE_TMUX_NO_REGISTER"):
    launch_dir = ""
    for i, arg in enumerate(args):
        if arg == "-c" and i + 1 < len(args):
            launch_dir = args[i + 1]
    with open(os.environ["CAMP_FAKE_SESSIONS_FILE"], "a") as handle:
        handle.write(f"{args[-1]}\\t{launch_dir}\\n")
'''


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"], check=True, capture_output=True
    )
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init", "--no-gpg-sign"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "remote", "add", "origin", str(path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "fetch", "origin", "--quiet"], check=True, capture_output=True
    )


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
    _init_git_repo(repo_a)
    _init_git_repo(repo_b)

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

    env = {**os.environ}
    env["CAMP_CONFIG_DIR"] = str(config_dir)
    env["CAMP_STATE_DIR"] = str(state_dir)
    env["CAMP_FAKE_SESSIONS_FILE"] = str(sessions_file)
    env["TRAILHEAD_CLAUDE_DIR"] = str(tmp_path / "claude")
    env["CAMP_FAKE_TMUX_ARGV_FILE"] = str(tmux_argv_file)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(shim_dir), str(_REPO_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    env.pop("CAMP_SHELL_INTEGRATION", None)

    for name, binary, repo in (
        ("mygroup", "fakeharness", repo_a),
        ("badgroup", "nosuchharness", repo_b),
    ):
        result = subprocess.run(
            [sys.executable, str(_CLI_CAMP), "group", name, "--member", f"member={repo}"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, f"group authoring failed: {result.stderr}"
        _set_harness_binary(config_dir, name, binary)

    return {
        "env": env,
        "config_dir": config_dir,
        "sessions_file": sessions_file,
        "state_dir": state_dir,
        "tmux_argv_file": tmux_argv_file,
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
    result = _camp(cli_env, "new", slug, "--group", group)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# camp launch
# ---------------------------------------------------------------------------


def test_camp_launch_stdout_is_only_the_session_id(cli_env) -> None:
    _new_workspace(cli_env, "feat-a")
    result = _camp(cli_env, "launch", "feat-a", "--group", "mygroup")

    assert result.returncode == 0, result.stderr
    session_id = result.stdout.rstrip("\n")
    assert result.stdout == f"{session_id}\n"
    assert len(session_id) == 36  # a uuid4, nothing else on stdout


def test_camp_launch_stderr_carries_paste_ready_attach_line(cli_env) -> None:
    _new_workspace(cli_env, "feat-b")
    result = _camp(cli_env, "launch", "feat-b", "--group", "mygroup")

    assert result.returncode == 0, result.stderr
    session_id = result.stdout.strip()
    assert f"tmux attach -t camp-feat-b-{session_id[:8]}" in result.stderr


def test_camp_launch_json_carries_the_session_id(cli_env) -> None:
    _new_workspace(cli_env, "feat-c")
    result = _camp(cli_env, "launch", "feat-c", "--group", "mygroup", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["session_id"]
    # The name is the launch engine's own — checked against the attach line the
    # engine printed, not against a name reassembled here. The derived-name
    # FORMAT is pinned where it is produced (test_launch_session.py).
    assert payload["tmux_name"]
    assert f"tmux attach -t {payload['tmux_name']}" in result.stderr


def test_camp_launch_unknown_harness_is_a_one_line_refusal(cli_env) -> None:
    _new_workspace(cli_env, "feat-d", group="badgroup")
    result = _camp(cli_env, "launch", "feat-d", "--group", "badgroup")

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip().splitlines() == [
        "camp launch: refusing to launch — no harness named 'nosuchharness' is registered"
    ]


def test_camp_launch_unconfirmed_session_refuses_with_empty_stdout(cli_env) -> None:
    """A session that never registers is a refusal, not a success."""
    _new_workspace(cli_env, "feat-e")
    result = _camp(
        cli_env,
        "launch",
        "feat-e",
        "--group",
        "mygroup",
        extra_env={"CAMP_FAKE_TMUX_NO_REGISTER": "1"},
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "camp launch: launch of session" in result.stderr
    assert "could not be confirmed" in result.stderr


# ---------------------------------------------------------------------------
# camp launch --dir
# ---------------------------------------------------------------------------


def _set_launch_roots(cli_env, *roots, group: str = "mygroup") -> None:
    """Append a `[launch] roots` allowlist to an authored group config."""
    path = cli_env["config_dir"] / "groups" / f"{group}.toml"
    entries = ", ".join(json.dumps(str(root)) for root in roots)
    path.write_text(
        path.read_text(encoding="utf-8") + f"\n[launch]\nroots = [{entries}]\n",
        encoding="utf-8",
    )


def _state_tree(cli_env) -> list[str]:
    """Every path under CAMP_STATE_DIR, relative and sorted.

    A directory-rooted launch persists nothing, so this is the whole statelessness
    assertion: the tree before a command and after it must be identical.
    """
    root = cli_env["state_dir"]
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _settled_state_tree(cli_env, *, quiet_for: float = 0.3, timeout: float = 20.0) -> list[str]:
    """`_state_tree` once `camp new`'s background provisioner has stopped writing.

    Workspace creation provisions in the background, so a snapshot taken right
    after it is still moving — and a statelessness assertion against a moving
    baseline reports the provisioner's writes as the command-under-test's.
    """
    deadline = time.monotonic() + timeout
    previous = _state_tree(cli_env)
    while time.monotonic() < deadline:
        time.sleep(quiet_for)
        current = _state_tree(cli_env)
        if current == previous:
            return current
        previous = current
    return previous


def _tmux_new_session_argv(cli_env) -> list[list[str]]:
    """The argv of every `tmux new-session` camp actually spawned."""
    lines = cli_env["tmux_argv_file"].read_text(encoding="utf-8").splitlines()
    return [line.split("\t") for line in lines if line.startswith("new-session\t")]


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _assert_clean_refusal(result, *, needle: str) -> None:
    """One `camp launch: ` stderr line, empty stdout, non-zero exit."""
    assert result.returncode != 0, result.stdout
    assert result.stdout == ""
    lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith("camp launch: "), lines[0]
    assert needle in lines[0], lines[0]


def test_camp_launch_dir_reports_the_session_like_a_slug_launch(cli_env) -> None:
    target = cli_env["tmp_path"] / "roots" / "myproject"
    target.mkdir(parents=True)
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    before = _state_tree(cli_env)

    result = _camp(cli_env, "launch", "--dir", str(target), "--group", "mygroup")

    assert result.returncode == 0, result.stderr
    session_id = result.stdout.rstrip("\n")
    assert result.stdout == f"{session_id}\n"
    assert len(session_id) == 36
    assert str(target.resolve()) in result.stderr
    assert f"tmux attach -t camp-myproject-{session_id[:8]}" in result.stderr
    assert f"camp launch: confirmed session {session_id}" in result.stderr
    assert _state_tree(cli_env) == before


def test_camp_launch_dir_spawns_tmux_at_the_directory_under_a_derived_name(cli_env) -> None:
    target = cli_env["tmp_path"] / "roots" / "myproject"
    target.mkdir(parents=True)
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    before = _state_tree(cli_env)

    result = _camp(cli_env, "launch", "--dir", str(target), "--group", "mygroup")

    assert result.returncode == 0, result.stderr
    assert _state_tree(cli_env) == before
    session_id = result.stdout.strip()
    spawned = _tmux_new_session_argv(cli_env)
    assert len(spawned) == 1
    assert _flag_value(spawned[0], "-c") == str(target.resolve())
    assert _flag_value(spawned[0], "-s") == f"camp-myproject-{session_id[:8]}"


def test_camp_launch_dir_json_key_set_matches_a_slug_launch_exactly(cli_env) -> None:
    _new_workspace(cli_env, "feat-dir-json")
    slug_launch = _camp(cli_env, "launch", "feat-dir-json", "--group", "mygroup", "--json")
    assert slug_launch.returncode == 0, slug_launch.stderr

    target = cli_env["tmp_path"] / "roots" / "myproject"
    target.mkdir(parents=True)
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    before = _state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "--dir", str(target), "--group", "mygroup", "--json"
    )

    assert result.returncode == 0, result.stderr
    assert _state_tree(cli_env) == before
    payload = json.loads(result.stdout)
    assert set(payload) == {"workspace", "session_id", "tmux_name"}
    assert set(payload) == set(json.loads(slug_launch.stdout))
    assert payload["workspace"] == str(target.resolve())
    assert payload["tmux_name"] == f"camp-myproject-{payload['session_id'][:8]}"


def test_camp_launch_dir_with_a_positional_slug_refuses(cli_env) -> None:
    target = cli_env["tmp_path"] / "roots" / "myproject"
    target.mkdir(parents=True)
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    before = _state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "feat-a", "--dir", str(target), "--group", "mygroup"
    )

    _assert_clean_refusal(result, needle="--dir")
    assert "slug" in result.stderr
    assert cli_env["sessions_file"].read_text() == ""
    assert _state_tree(cli_env) == before


def test_camp_launch_dir_with_a_name_flagged_slug_refuses(cli_env) -> None:
    """`--name` is a slug spelling, so it collides with `--dir` like a positional."""
    target = cli_env["tmp_path"] / "roots" / "myproject"
    target.mkdir(parents=True)
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    before = _state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "--name", "feat-a", "--dir", str(target), "--group", "mygroup"
    )

    _assert_clean_refusal(result, needle="--dir")
    assert "slug" in result.stderr
    assert cli_env["sessions_file"].read_text() == ""
    assert _state_tree(cli_env) == before


def test_camp_launch_dir_with_resume_refuses(cli_env) -> None:
    target = cli_env["tmp_path"] / "roots" / "myproject"
    target.mkdir(parents=True)
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    before = _state_tree(cli_env)

    result = _camp(
        cli_env,
        "launch",
        "--dir",
        str(target),
        "--resume",
        "deadbeef",
        "--group",
        "mygroup",
    )

    _assert_clean_refusal(result, needle="--dir")
    assert "--resume" in result.stderr
    assert cli_env["sessions_file"].read_text() == ""
    assert _state_tree(cli_env) == before


def test_camp_launch_dir_inside_a_workspace_still_requires_an_explicit_group(cli_env) -> None:
    """The allowlist is the containment boundary, so it may never come from cwd.

    Run from INSIDE a workspace, where the group resolves perfectly well from the
    directory camp was invoked in — and refuse anyway. Resolvability was never the
    question.
    """
    workspace = Path(_new_workspace(cli_env, "feat-inside"))
    target = cli_env["tmp_path"] / "roots" / "myproject"
    target.mkdir(parents=True)
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    before = _state_tree(cli_env)

    result = _camp(cli_env, "launch", "--dir", str(target), cwd=workspace)

    _assert_clean_refusal(result, needle="--group")
    assert cli_env["sessions_file"].read_text() == ""
    assert _state_tree(cli_env) == before


def test_camp_launch_dir_outside_any_group_refuses_on_the_group(cli_env) -> None:
    """Outside every group the spine's needs-group fallback answers — pinned here.

    It is already a `camp launch: ` one-liner on stderr with empty stdout, so the
    refusal shape holds without the group-aware handler ever being reached.
    """
    target = cli_env["tmp_path"] / "roots" / "myproject"
    target.mkdir(parents=True)
    before = _state_tree(cli_env)

    result = _camp(cli_env, "launch", "--dir", str(target), cwd=cli_env["tmp_path"])

    _assert_clean_refusal(result, needle="--group")
    assert cli_env["sessions_file"].read_text() == ""
    assert _state_tree(cli_env) == before


def test_camp_launch_dir_with_no_value_refuses(cli_env) -> None:
    _set_launch_roots(cli_env, cli_env["tmp_path"])

    result = _camp(cli_env, "launch", "--dir=", "--group", "mygroup")

    _assert_clean_refusal(result, needle="--dir")
    assert cli_env["sessions_file"].read_text() == ""


def test_camp_launch_dir_that_does_not_exist_refuses_naming_the_path(cli_env) -> None:
    target = cli_env["tmp_path"] / "roots" / "gone"
    (cli_env["tmp_path"] / "roots").mkdir()
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    before = _state_tree(cli_env)

    result = _camp(cli_env, "launch", "--dir", str(target), "--group", "mygroup")

    _assert_clean_refusal(result, needle=str(target.resolve()))
    assert cli_env["sessions_file"].read_text() == ""
    assert _state_tree(cli_env) == before


def test_camp_launch_dir_naming_a_regular_file_refuses_naming_the_path(cli_env) -> None:
    roots = cli_env["tmp_path"] / "roots"
    roots.mkdir()
    target = roots / "notes.txt"
    target.write_text("not a directory\n", encoding="utf-8")
    _set_launch_roots(cli_env, roots)
    before = _state_tree(cli_env)

    result = _camp(cli_env, "launch", "--dir", str(target), "--group", "mygroup")

    _assert_clean_refusal(result, needle=str(target.resolve()))
    assert cli_env["sessions_file"].read_text() == ""
    assert _state_tree(cli_env) == before


def test_camp_launch_dir_outside_the_allowlist_refuses_naming_it(cli_env) -> None:
    allowed = cli_env["tmp_path"] / "roots"
    allowed.mkdir()
    target = cli_env["tmp_path"] / "elsewhere"
    target.mkdir()
    _set_launch_roots(cli_env, allowed)
    before = _state_tree(cli_env)

    result = _camp(cli_env, "launch", "--dir", str(target), "--group", "mygroup")

    _assert_clean_refusal(result, needle="[launch] roots")
    assert str(target.resolve()) in result.stderr
    assert cli_env["sessions_file"].read_text() == ""
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_dir_with_no_allowlist_configured_refuses(cli_env) -> None:
    target = cli_env["tmp_path"] / "roots" / "myproject"
    target.mkdir(parents=True)
    before = _state_tree(cli_env)

    result = _camp(cli_env, "launch", "--dir", str(target), "--group", "mygroup")

    _assert_clean_refusal(result, needle="[launch] roots")
    assert cli_env["sessions_file"].read_text() == ""
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_dir_at_a_credential_store_refuses_on_the_credential_rule(cli_env) -> None:
    """`roots = ["~"]` cannot launder a credential directory past the gate."""
    home = cli_env["tmp_path"] / "fakehome"
    (home / ".ssh").mkdir(parents=True)
    _set_launch_roots(cli_env, "~")
    before = _state_tree(cli_env)

    result = _camp(
        cli_env,
        "launch",
        "--dir",
        str(home / ".ssh"),
        "--group",
        "mygroup",
        extra_env={"HOME": str(home)},
    )

    _assert_clean_refusal(result, needle="credential store")
    assert "[launch] roots" not in result.stderr
    assert cli_env["sessions_file"].read_text() == ""
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


# ---------------------------------------------------------------------------
# camp launch --resume
# ---------------------------------------------------------------------------

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


def _tmux_argv(cli_env) -> list[list[str]]:
    """The argv of every tmux invocation camp made, in order."""
    lines = cli_env["tmux_argv_file"].read_text(encoding="utf-8").splitlines()
    return [line.split("\t") for line in lines if line]


def _workspace_launch_dir(cli_env, slug: str) -> Path:
    """The directory a slug launch resolves to — the transcript's recorded cwd."""
    return Path(_new_workspace(cli_env, slug)).resolve()


def test_camp_launch_resume_reenters_a_workspace_session_without_a_group_flag(
    cli_env,
) -> None:
    """The whole point of the workspace flavor: a ref, from anywhere, no flag.

    Run from a directory no group resolves from, so nothing but the ref and the
    transcript decides where the session comes back up.
    """
    launch_dir = _workspace_launch_dir(cli_env, "feat-resume")
    _seed_transcript(cli_env, _UUID_A, launch_dir)
    before = _settled_state_tree(cli_env)

    result = _camp(cli_env, "launch", "--resume", _UUID_A, cwd=cli_env["tmp_path"])

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{_UUID_A}\n"
    spawned = _tmux_new_session_argv(cli_env)
    assert len(spawned) == 1
    assert _flag_value(spawned[0], "-s") == f"camp-feat-resume-{_UUID_A[:8]}"
    assert _flag_value(spawned[0], "-c") == str(launch_dir)
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_by_uuid_prefix_reaches_the_identical_spawn(cli_env) -> None:
    launch_dir = _workspace_launch_dir(cli_env, "feat-resume")
    _seed_transcript(cli_env, _UUID_A, launch_dir)
    before = _settled_state_tree(cli_env)

    result = _camp(cli_env, "launch", "--resume", _UUID_A[:8], cwd=cli_env["tmp_path"])

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{_UUID_A}\n"
    spawned = _tmux_new_session_argv(cli_env)
    assert _flag_value(spawned[0], "-s") == f"camp-feat-resume-{_UUID_A[:8]}"
    assert _flag_value(spawned[0], "-c") == str(launch_dir)
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_by_derived_name_prefix_reaches_the_identical_spawn(
    cli_env,
) -> None:
    launch_dir = _workspace_launch_dir(cli_env, "feat-resume")
    _seed_transcript(cli_env, _UUID_A, launch_dir)
    before = _settled_state_tree(cli_env)

    result = _camp(cli_env, "launch", "--resume", "camp-feat-resume-", cwd=cli_env["tmp_path"])

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{_UUID_A}\n"
    spawned = _tmux_new_session_argv(cli_env)
    assert _flag_value(spawned[0], "-s") == f"camp-feat-resume-{_UUID_A[:8]}"
    assert _flag_value(spawned[0], "-c") == str(launch_dir)
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_json_key_set_matches_a_slug_launch_exactly(cli_env) -> None:
    _new_workspace(cli_env, "feat-json")
    slug_launch = _camp(cli_env, "launch", "feat-json", "--group", "mygroup", "--json")
    assert slug_launch.returncode == 0, slug_launch.stderr

    launch_dir = _workspace_launch_dir(cli_env, "feat-resume")
    _seed_transcript(cli_env, _UUID_A, launch_dir)
    before = _settled_state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "--resume", _UUID_A, "--json", cwd=cli_env["tmp_path"]
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == set(json.loads(slug_launch.stdout))
    assert payload["session_id"] == _UUID_A
    assert payload["tmux_name"] == f"camp-feat-resume-{_UUID_A[:8]}"
    assert payload["workspace"] == str(launch_dir)
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_at_a_non_workspace_root_resumes_with_an_explicit_group(
    cli_env,
) -> None:
    """A dotted basename is ordinary input, and the name it yields must address."""
    target = cli_env["tmp_path"] / "roots" / "my.project"
    target.mkdir(parents=True)
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    _seed_transcript(cli_env, _UUID_A, target)
    before = _settled_state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "--resume", _UUID_A, "--group", "mygroup",
        cwd=cli_env["tmp_path"],
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{_UUID_A}\n"
    spawned = _tmux_new_session_argv(cli_env)
    assert _flag_value(spawned[0], "-s") == f"camp-my-project-{_UUID_A[:8]}"
    assert _flag_value(spawned[0], "-c") == str(target.resolve())
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_at_a_non_workspace_root_without_a_group_refuses(
    cli_env,
) -> None:
    """The allowlist fences this root, so the group must be named, never inferred."""
    target = cli_env["tmp_path"] / "roots" / "myproject"
    target.mkdir(parents=True)
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    _seed_transcript(cli_env, _UUID_A, target)
    before = _settled_state_tree(cli_env)

    result = _camp(cli_env, "launch", "--resume", _UUID_A, cwd=cli_env["tmp_path"])

    _assert_clean_refusal(result, needle="--group")
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_inside_a_group_still_requires_an_explicit_group(
    cli_env,
) -> None:
    """A resolvable cwd is not consent: the boundary may never move with the caller."""
    workspace = Path(_new_workspace(cli_env, "feat-standing"))
    target = cli_env["tmp_path"] / "roots" / "myproject"
    target.mkdir(parents=True)
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    _seed_transcript(cli_env, _UUID_A, target)
    before = _settled_state_tree(cli_env)

    result = _camp(cli_env, "launch", "--resume", _UUID_A, cwd=workspace)

    _assert_clean_refusal(result, needle="--group")
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_at_a_root_outside_the_allowlist_refuses_naming_it(
    cli_env,
) -> None:
    """The gate is re-checked against CURRENT config, not the config at launch time."""
    allowed = cli_env["tmp_path"] / "roots"
    allowed.mkdir()
    target = cli_env["tmp_path"] / "elsewhere"
    target.mkdir()
    _set_launch_roots(cli_env, allowed)
    _seed_transcript(cli_env, _UUID_A, target)
    before = _settled_state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "--resume", _UUID_A, "--group", "mygroup",
        cwd=cli_env["tmp_path"],
    )

    _assert_clean_refusal(result, needle="[launch] roots")
    assert str(target.resolve()) in result.stderr
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_at_a_credential_store_refuses_on_the_credential_rule(
    cli_env,
) -> None:
    """`roots = ["~"]` cannot launder a session rooted at ~/.ssh back to life."""
    home = cli_env["tmp_path"] / "fakehome"
    ssh = home / ".ssh"
    ssh.mkdir(parents=True)
    _set_launch_roots(cli_env, "~")
    _seed_transcript(cli_env, _UUID_A, ssh)
    before = _settled_state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "--resume", _UUID_A, "--group", "mygroup",
        cwd=cli_env["tmp_path"], extra_env={"HOME": str(home)},
    )

    _assert_clean_refusal(result, needle="credential store")
    assert "[launch] roots" not in result.stderr
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_with_a_missing_root_refuses_without_recreating_it(
    cli_env,
) -> None:
    target = cli_env["tmp_path"] / "roots" / "torn-down"
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    _seed_transcript(cli_env, _UUID_A, target)
    before = _settled_state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "--resume", _UUID_A, "--group", "mygroup",
        cwd=cli_env["tmp_path"],
    )

    _assert_clean_refusal(result, needle=str(target))
    assert not target.exists()
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_of_an_unreadable_candidate_carries_no_sentinel(
    cli_env,
) -> None:
    """The concierge relays this line to a phone verbatim; it must read as English.

    A uuid-only candidate has no directory to name at all, which is a different
    situation from a directory that is named and gone — so it gets its own
    wording, and that wording may never leak the internal absence marker.
    """
    torn_down = cli_env["tmp_path"] / "roots" / "torn-down"
    _set_launch_roots(cli_env, cli_env["tmp_path"] / "roots")
    _seed_transcript(cli_env, _UUID_B, torn_down)
    _seed_transcript(cli_env, _UUID_A, None)
    before = _settled_state_tree(cli_env)

    unreadable = _camp(
        cli_env, "launch", "--resume", _UUID_A, "--group", "mygroup",
        cwd=cli_env["tmp_path"],
    )
    missing_root = _camp(
        cli_env, "launch", "--resume", _UUID_B, "--group", "mygroup",
        cwd=cli_env["tmp_path"],
    )

    _assert_clean_refusal(unreadable, needle=_UUID_A)
    assert unreadable.stderr != missing_root.stderr
    assert "None" not in unreadable.stderr
    assert "''" not in unreadable.stderr
    assert '""' not in unreadable.stderr
    assert "``" not in unreadable.stderr
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_of_a_live_session_says_already_running(cli_env) -> None:
    launch_dir = _workspace_launch_dir(cli_env, "feat-live")
    _seed_transcript(cli_env, _UUID_A, launch_dir)
    _register_live(cli_env, _UUID_A, launch_dir)
    before = _settled_state_tree(cli_env)

    result = _camp(cli_env, "launch", "--resume", _UUID_A, cwd=cli_env["tmp_path"])

    _assert_clean_refusal(result, needle="already running")
    assert f"camp-feat-live-{_UUID_A[:8]}" in result.stderr
    assert "no candidate matched" not in result.stderr
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_of_a_live_session_with_no_transcript_says_already_running(
    cli_env,
) -> None:
    """The union is what makes this answerable — transcripts alone say "not found"."""
    launch_dir = _workspace_launch_dir(cli_env, "feat-live")
    _register_live(cli_env, _UUID_A, launch_dir)
    before = _settled_state_tree(cli_env)

    result = _camp(cli_env, "launch", "--resume", _UUID_A, cwd=cli_env["tmp_path"])

    _assert_clean_refusal(result, needle="already running")
    assert "no candidate matched" not in result.stderr
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_with_an_ambiguous_ref_exits_two_and_lists_candidates(
    cli_env,
) -> None:
    """Ambiguity is information, not failure — and camp never guesses between them."""
    first = _workspace_launch_dir(cli_env, "feat-one")
    second = _workspace_launch_dir(cli_env, "feat-two")
    _seed_transcript(cli_env, _UUID_A, first, age_seconds=30.0)
    _seed_transcript(cli_env, _UUID_B, second, age_seconds=90.0)
    before = _settled_state_tree(cli_env)

    result = _camp(cli_env, "launch", "--resume", "camp-feat-", cwd=cli_env["tmp_path"])

    assert result.returncode == 2, result.stderr
    rows = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(rows) == 2, result.stdout
    assert f"camp-feat-one-{_UUID_A[:8]}" in rows[0]
    assert _UUID_A in rows[0]
    assert str(first) in rows[0]
    assert f"camp-feat-two-{_UUID_B[:8]}" in rows[1]
    stderr_lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
    assert len(stderr_lines) == 1, result.stderr
    assert stderr_lines[0].startswith("camp launch: ")
    assert "camp-feat-" in stderr_lines[0]
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_ambiguous_json_rows_carry_the_candidate_key_set(
    cli_env,
) -> None:
    first = _workspace_launch_dir(cli_env, "feat-one")
    second = _workspace_launch_dir(cli_env, "feat-two")
    _seed_transcript(cli_env, _UUID_A, first, age_seconds=30.0)
    _seed_transcript(cli_env, _UUID_B, second, age_seconds=90.0)
    before = _settled_state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "--resume", "camp-feat-", "--json", cwd=cli_env["tmp_path"]
    )

    assert result.returncode == 2, result.stderr
    payload = json.loads(result.stdout)
    assert [row["session_id"] for row in payload] == [_UUID_A, _UUID_B]
    for row in payload:
        assert set(row) == {
            "session_id",
            "tmux_name",
            "root",
            "age_seconds",
            "root_missing",
            "unreadable",
        }
    assert payload[0]["tmux_name"] == f"camp-feat-one-{_UUID_A[:8]}"
    assert payload[0]["root"] == str(first)
    assert payload[0]["root_missing"] is False
    assert payload[0]["unreadable"] is False
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_zero_match_messages_differ_by_whether_the_pool_is_empty(
    cli_env,
) -> None:
    """The two "nothing matched" situations are not the same problem to the operator.

    An empty store means the transcript aged out; a non-empty one means the ref
    was wrong. Naming retention for a mistyped ref sends the operator looking for
    a session that is sitting right there.
    """
    empty_store = _camp(
        cli_env, "launch", "--resume", "deadbeef", "--group", "mygroup",
        cwd=cli_env["tmp_path"],
    )

    _seed_transcript(cli_env, _UUID_A, _workspace_launch_dir(cli_env, "feat-one"))
    before = _settled_state_tree(cli_env)
    populated_store = _camp(
        cli_env, "launch", "--resume", "deadbeef", "--group", "mygroup",
        cwd=cli_env["tmp_path"],
    )

    _assert_clean_refusal(empty_store, needle="retention")
    assert "no candidate matched" not in empty_store.stderr

    _assert_clean_refusal(populated_store, needle="no candidate matched")
    assert "`deadbeef`" in populated_store.stderr
    assert "camp sessions --recoverable" in populated_store.stderr
    assert "retention" not in populated_store.stderr

    assert empty_store.stderr != populated_store.stderr
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_with_a_positional_slug_refuses(cli_env) -> None:
    _seed_transcript(cli_env, _UUID_A, _workspace_launch_dir(cli_env, "feat-one"))
    before = _settled_state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "feat-one", "--resume", _UUID_A, "--group", "mygroup",
        cwd=cli_env["tmp_path"],
    )

    _assert_clean_refusal(result, needle="--resume")
    assert "slug" in result.stderr
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_with_no_value_refuses(cli_env) -> None:
    before = _settled_state_tree(cli_env)

    result = _camp(cli_env, "launch", "--resume", "--group", "mygroup")

    _assert_clean_refusal(result, needle="--resume")
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_with_a_flag_shaped_ref_refuses_on_the_flag(cli_env) -> None:
    """An unconsumed flag swallowed as the ref must report the flag, not a miss."""
    before = _settled_state_tree(cli_env)

    result = _camp(cli_env, "launch", "--resume", "--json", "--group", "mygroup")

    _assert_clean_refusal(result, needle="--resume")
    assert "no candidate matched" not in result.stderr
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_on_a_harness_without_transcripts_refuses_naming_it(
    cli_env,
) -> None:
    before = _settled_state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "--resume", _UUID_A, "--group", "mygroup",
        cwd=cli_env["tmp_path"], extra_env={"CAMP_FAKE_TRANSCRIPTS": "none"},
    )

    _assert_clean_refusal(result, needle="fakeharness")
    assert "Traceback" not in result.stderr
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_on_a_harness_that_cannot_reenter_refuses_naming_it(
    cli_env,
) -> None:
    _seed_transcript(cli_env, _UUID_A, _workspace_launch_dir(cli_env, "feat-one"))
    before = _settled_state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "--resume", _UUID_A,
        cwd=cli_env["tmp_path"], extra_env={"CAMP_FAKE_RESUME": "none"},
    )

    _assert_clean_refusal(result, needle="fakeharness")
    assert _tmux_new_session_argv(cli_env) == []
    assert _state_tree(cli_env) == before


def test_camp_launch_resume_that_never_confirms_is_killed_by_its_exact_name(
    cli_env,
) -> None:
    _seed_transcript(cli_env, _UUID_A, _workspace_launch_dir(cli_env, "feat-one"))
    before = _settled_state_tree(cli_env)

    result = _camp(
        cli_env, "launch", "--resume", _UUID_A,
        cwd=cli_env["tmp_path"], extra_env={"CAMP_FAKE_TMUX_NO_REGISTER": "1"},
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "could not be confirmed" in result.stderr
    tmux_name = f"camp-feat-one-{_UUID_A[:8]}"
    kills = [argv for argv in _tmux_argv(cli_env) if argv[0] == "kill-session"]
    assert kills == [["kill-session", "-t", tmux_name]]
    assert _state_tree(cli_env) == before


def test_camp_resume_the_bookmark_verb_is_untouched_by_the_launch_flavor(
    cli_env,
) -> None:
    """`camp resume` addresses BOOKMARKS. A transcript is not a bookmark.

    Seeded so a session reference that `camp launch --resume` would resolve is
    sitting in the store while `camp resume` is asked for it — and the bookmark
    verb must still answer about bookmarks.
    """
    _seed_transcript(cli_env, _UUID_A, _workspace_launch_dir(cli_env, "feat-one"))

    result = _camp(
        cli_env, "resume", "camp-feat-one-", cwd=cli_env["tmp_path"],
        extra_env={"CAMP_SHELL_INTEGRATION": "1"},
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "camp resume: no bookmark named 'camp-feat-one-'" in result.stderr
    assert "camp bookmark ls" in result.stderr


# ---------------------------------------------------------------------------
# camp sessions
# ---------------------------------------------------------------------------


def test_camp_sessions_default_output_is_byte_identical(cli_env) -> None:
    """Regression pin: the live listing's bytes, global form.

    Written against the surface as it stands so that widening `camp sessions`
    with new flags cannot perturb the answer an existing caller already parses.
    """
    launch_dir = _workspace_launch_dir(cli_env, "feat-pin")
    launched = _camp(cli_env, "launch", "feat-pin", "--group", "mygroup")
    assert launched.returncode == 0, launched.stderr
    session_id = launched.stdout.strip()

    result = _camp(cli_env, "sessions", "--group", "mygroup")

    assert result.returncode == 0
    assert result.stdout == f"{session_id}  agent  {launch_dir}\n"
    assert result.stderr == ""


def test_camp_sessions_slug_scoped_output_is_byte_identical(cli_env) -> None:
    """Regression pin: the live listing's bytes, workspace-scoped form."""
    launch_dir = _workspace_launch_dir(cli_env, "feat-pin")
    launched = _camp(cli_env, "launch", "feat-pin", "--group", "mygroup")
    assert launched.returncode == 0, launched.stderr
    session_id = launched.stdout.strip()

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
    _new_workspace(cli_env, "feat-f")
    launched = _camp(cli_env, "launch", "feat-f", "--group", "mygroup")
    session_id = launched.stdout.strip()

    result = _camp(cli_env, "sessions", "--group", "mygroup")

    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip().splitlines()) == 1
    assert session_id in result.stdout


def test_camp_sessions_json_uses_normalized_fields_only(cli_env) -> None:
    _new_workspace(cli_env, "feat-g")
    _camp(cli_env, "launch", "feat-g", "--group", "mygroup")

    result = _camp(cli_env, "sessions", "--group", "mygroup", "--json")

    assert result.returncode == 0, result.stderr
    records = json.loads(result.stdout)
    assert len(records) == 1
    assert set(records[0]) == {
        "session_id",
        "cwd",
        "kind",
        "controllable",
        "name",
        "pid",
        "started_at",
    }


@pytest.mark.parametrize("mode", ["fail", "missing", "none"])
def test_camp_sessions_degrades_to_a_stderr_notice_and_exit_zero(cli_env, mode: str) -> None:
    result = _camp(
        cli_env, "sessions", "--group", "mygroup", extra_env={"CAMP_FAKE_ENUMERATE": mode}
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "camp sessions: could not determine" in result.stderr


def test_camp_sessions_degraded_json_still_prints_an_empty_list(cli_env) -> None:
    result = _camp(
        cli_env,
        "sessions",
        "--group",
        "mygroup",
        "--json",
        extra_env={"CAMP_FAKE_ENUMERATE": "fail"},
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []
    assert "camp sessions: could not determine" in result.stderr


def test_camp_sessions_unknown_harness_degrades(cli_env) -> None:
    result = _camp(cli_env, "sessions", "--group", "badgroup")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "camp sessions: could not determine" in result.stderr


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


def _assert_sessions_refusal(result, *, needle: str) -> None:
    """One `camp sessions: ` stderr line, empty stdout, non-zero exit."""
    assert result.returncode != 0, result.stdout
    assert result.stdout == ""
    lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
    assert len(lines) == 1, result.stderr
    assert lines[0].startswith("camp sessions: "), lines[0]
    assert needle in lines[0], lines[0]


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
    before = _settled_state_tree(cli_env)

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

    _assert_sessions_refusal(result, needle="--limit")
    assert _state_tree(cli_env) == before


def test_camp_sessions_recoverable_limit_and_all_together_refuse(cli_env) -> None:
    before = _state_tree(cli_env)

    result = _recoverable(cli_env, "--limit", "5", "--all")

    _assert_sessions_refusal(result, needle="mutually exclusive")
    assert _state_tree(cli_env) == before


@pytest.mark.parametrize("flags", [("--limit", "5"), ("--all",)])
def test_camp_sessions_cap_flags_without_recoverable_refuse(cli_env, flags) -> None:
    """The cap belongs to the recoverable listing; the live form has no cap to widen."""
    before = _state_tree(cli_env)

    result = _camp(
        cli_env, "sessions", *flags, "--group", "mygroup", cwd=cli_env["tmp_path"]
    )

    _assert_sessions_refusal(result, needle="--recoverable")
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

    _assert_sessions_refusal(unsupported, needle="fakeharness")
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
# camp new --launch
# ---------------------------------------------------------------------------


def test_camp_new_launch_stdout_is_the_workspace_path_alone(cli_env) -> None:
    result = _camp(cli_env, "new", "feat-h", "--group", "mygroup", "--launch")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("/feat-h")
    assert len(result.stdout.strip().splitlines()) == 1
    assert "camp launch: confirmed session" in result.stderr
    assert cli_env["sessions_file"].read_text().strip() != ""


def test_camp_new_launch_failure_keeps_the_path_and_exit_zero(cli_env) -> None:
    result = _camp(cli_env, "new", "feat-i", "--group", "badgroup", "--launch")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("/feat-i")
    assert len(result.stdout.strip().splitlines()) == 1
    assert "camp launch: refusing to launch" in result.stderr


def test_camp_new_launch_json_emits_workspace_and_session_id(cli_env) -> None:
    result = _camp(cli_env, "new", "feat-j", "--group", "mygroup", "--launch", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {"workspace", "session_id", "tmux_name"}
    assert payload["workspace"].endswith("/feat-j")
    assert payload["session_id"]
    # Same rule as the reuse path: the emitted name is the one the launch
    # engine reported, checked against its own attach line rather than rebuilt.
    assert payload["tmux_name"]
    assert f"tmux attach -t {payload['tmux_name']}" in result.stderr


def test_camp_new_launch_json_failure_nulls_the_session_id(cli_env) -> None:
    result = _camp(cli_env, "new", "feat-k", "--group", "badgroup", "--launch", "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {"workspace", "session_id", "tmux_name"}
    assert payload["session_id"] is None
    assert payload["tmux_name"] is None
    assert payload["workspace"].endswith("/feat-k")
    assert "camp launch: refusing to launch" in result.stderr


def test_camp_new_no_wait_skips_the_wait_and_names_camp_status(cli_env) -> None:
    result = _camp(cli_env, "new", "feat-l", "--group", "mygroup", "--launch", "--no-wait")

    assert result.returncode == 0, result.stderr
    assert "camp new: --no-wait" in result.stderr
    assert "camp status feat-l" in result.stderr.split("camp new: --no-wait", 1)[1]
    assert "camp new: waiting for provisioning" not in result.stderr


def test_camp_new_launch_waits_for_provisioning_then_confirms(cli_env) -> None:
    """The two bounded waits run back to back, in that order."""
    result = _camp(cli_env, "new", "feat-m", "--group", "mygroup", "--launch")

    assert result.returncode == 0, result.stderr
    waited = result.stderr.index("camp new: waiting for provisioning")
    confirmed = result.stderr.index("camp launch: confirmed session")
    assert waited < confirmed


def test_camp_new_json_without_launch_is_refused(cli_env) -> None:
    result = _camp(cli_env, "new", "feat-n", "--group", "mygroup", "--json")

    assert result.returncode != 0
    assert result.stdout == ""
    assert "camp new: --json requires --launch" in result.stderr


def test_bare_camp_new_output_is_unchanged(cli_env) -> None:
    """Regression pin: adding --launch must not perturb the bare surface."""
    result = _camp(cli_env, "new", "feat-o", "--group", "mygroup")

    assert result.returncode == 0, result.stderr
    ws_dir = Path(cli_env["state_dir"]) / "mygroup" / "worktrees" / "feat-o"
    assert result.stdout == f"{ws_dir}\n"
    assert result.stderr == (
        "camp new: created workspace 'feat-o' — provisioning in the background\n"
        "  check provisioning: camp status feat-o\n"
        "  activates when ready, or run: camp activate feat-o\n"
        '  tip: run eval "$(trailhead shellenv)" so `camp new` cd\'s you in '
        "automatically\n"
    )
