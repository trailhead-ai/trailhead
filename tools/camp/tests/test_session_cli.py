"""CLI surface: camp launch, camp sessions, camp new --launch.

Exercised end-to-end through the REAL `cli/camp` binary (house convention), with
the harness seam and tmux replaced by stand-ins rather than by monkeypatching
camp's own code:

- a `FakeHarness` registered into trailhead's harness registry from a
  `sitecustomize.py` on PYTHONPATH, selected by `[harness] binary = "fakeharness"`
  in the group config. Its `session_launch` argv ends in the session id and its
  `session_enumerate` argv `cat`s a file, so enumeration is a real subprocess
  reading real state.
- a `tmux` stub earlier on PATH that appends the launched session id to that same
  file — so a launch really does become visible to a really-executed enumeration,
  and suppressing the append (CAMP_FAKE_TMUX_NO_REGISTER) reproduces the
  never-confirms failure without any in-process patching.

Test contract:
- camp launch success: stdout is exactly the session id + newline; stderr carries
  the paste-ready `tmux attach -t <name>` line; --json carries the session id.
- camp launch refusals (unknown harness, unconfirmed launch): empty stdout, a
  single `camp launch: …` stderr line, non-zero exit.
- camp sessions: empty → empty stdout, exit 0; degraded (enumeration error /
  missing enumerate binary / unknown harness) → stderr notice, empty stdout list,
  exit 0; --json carries only normalized SessionRecord fields.
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
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"

_SITECUSTOMIZE = '''
"""Registers a fake harness so a camp CLI subprocess can launch and enumerate."""
import os
from datetime import datetime, timezone
from pathlib import Path

import trailhead.harness as _registry
from trailhead.harness.base import SessionRecord
from trailhead.harness.claude_code import ClaudeCodeHarness


class FakeHarness(ClaudeCodeHarness):
    """Concrete only where this slice's surface reads it."""

    name = "fakeharness"

    def session_launch(self, workspace, session_id, *, session_name=None):
        return ["fake-launch", session_id]

    def session_launch_modality(self):
        return "detached"

    def session_launch_env_unset(self):
        return []

    def session_enumerate(self, workspace=None):
        mode = os.environ.get("CAMP_FAKE_ENUMERATE", "ok")
        if mode == "none":
            return None
        if mode == "fail":
            return ["false"]
        if mode == "missing":
            return ["camp-fake-absent-binary"]
        return ["cat", os.environ["CAMP_FAKE_SESSIONS_FILE"]]

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

    env = {**os.environ}
    env["CAMP_CONFIG_DIR"] = str(config_dir)
    env["CAMP_STATE_DIR"] = str(state_dir)
    env["CAMP_FAKE_SESSIONS_FILE"] = str(sessions_file)
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

    return {"env": env, "sessions_file": sessions_file, "state_dir": state_dir}


def _camp(cli_env, *args, extra_env=None):
    env = {**cli_env["env"]}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=env,
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
# camp sessions
# ---------------------------------------------------------------------------


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
