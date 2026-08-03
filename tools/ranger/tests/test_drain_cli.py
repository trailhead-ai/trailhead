"""Tests for the ``ranger drain start|derive|record|finish`` CLI verbs.

Drives the real ``ranger`` CLI shim as a subprocess (matching
``test_sweep_cli.py``'s pattern), with fake ``lore`` and ``camp``
executables first on ``PATH`` and every path resolver redirected through
per-app override env vars so no real state, config, or vault is ever
touched.

Test contract:
- ``start`` runs every precondition (craft's execute procedure, provenance,
  group, vault, portage) before acquiring the lock, and portage's absence
  sets ``degraded`` rather than refusing.
- ``drain start`` and ``sweep start`` contend on the identical
  ``<vault>.lock`` path: a drain refuses to start while a refine sweep holds
  the lock, with the operator ``rm`` ritual for a stale one.
- ``derive --json`` carries ``bucket``/``slug`` per entry.
- ``record`` accepts a well-formed drain outcome and refuses a malformed one
  or an unsafe ``--task``, without ever needing a report to exist.
- ``finish`` releases the lock for the token ``start`` returned.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "ranger" / "plugins" / "ranger"
CLI_PATH = _PLUGIN_DIR / "cli" / "ranger"

_GROUP = "testgroup"
_VAULT = "testvault"
_PROVENANCE_ENV = "LORE_EMAIL"

_BOUND_RESOLUTION = {
    "kind": "task",
    "vault": _VAULT,
    "path": "/vaults/testvault",
    "scope": "team",
    "source": {"team": _VAULT},
    "skipped": None,
    "skipped_reason": None,
    "unmatched_scopes": [],
}

_FAKE_LORE_SCRIPT = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json
    import os
    import sys

    argv = sys.argv[1:]
    fixture = json.loads(open(os.environ["FAKE_LORE_FIXTURE"]).read())

    if argv[:2] == ["vault", "resolve"]:
        print(json.dumps(fixture["vault_resolve"]))
        sys.exit(0)

    def elected_vault(argv):
        if "--vault" not in argv:
            print(f"fake lore: read without --vault: {argv!r}", file=sys.stderr)
            sys.exit(2)
        return argv[argv.index("--vault") + 1]

    if argv[:2] == ["task", "list"]:
        elected_vault(argv)
        print(json.dumps(fixture["tasks"]))
        sys.exit(0)

    if argv[:2] == ["record", "show"]:
        elected_vault(argv)
        name = argv[2].split("/", 1)[1]
        print(json.dumps({
            "record_id": argv[2],
            "kind": "task",
            "name": name,
            "sidecar": {},
            "body": fixture["bodies"].get(name, ""),
        }))
        sys.exit(0)

    print(f"fake lore: unexpected argv {argv!r}", file=sys.stderr)
    sys.exit(2)
    """
)

_FAKE_CAMP_SCRIPT = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json
    import os
    import sys

    argv = sys.argv[1:]
    fixture = json.loads(open(os.environ["FAKE_CAMP_FIXTURE"]).read())

    if argv[:2] == ["list", "--json"]:
        print(json.dumps(fixture["workspaces"]))
        sys.exit(0)

    print(f"fake camp: unexpected argv {argv!r}", file=sys.stderr)
    sys.exit(2)
    """
)


def _task(name: str, *, status: str = "ready") -> dict:
    return {
        "name": name,
        "status": status,
        "created-at": "2026-01-01T00:00:00Z",
        "updated-at": "2026-01-01T00:00:00Z",
        "parent": None,
        "depends-on": [],
        "children": [],
    }


_BUILDABLE_BODY = "# t\n\n**Files:** `tools/ranger/plugins/ranger/ranger/x.py` (new).\n"


class Drain:
    """A fully isolated ranger drain environment: fake lore, fake camp, a
    fake camp group config, and redirected state/config dirs."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.ranger_state = tmp_path / "state" / "ranger"
        self.trailhead_state = tmp_path / "state" / "trailhead"
        self.camp_state = tmp_path / "state" / "camp"
        self.camp_config = tmp_path / "config" / "camp"
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True)
        self.outside = tmp_path / "outside"
        self.outside.mkdir(parents=True)
        self.lore_fixture_path = tmp_path / "lore-fixture.json"
        self.camp_fixture_path = tmp_path / "camp-fixture.json"
        self.cwd = self.repo

        self._write_group_config()
        self._write_fake_bins()
        self.set_lore_fixture(vault_resolve=_BOUND_RESOLUTION, tasks=[_task("t1")], bodies={"t1": _BUILDABLE_BODY})
        self.set_camp_fixture(workspaces=[])

    def _write_group_config(self) -> None:
        groups = self.camp_config / "groups"
        groups.mkdir(parents=True)
        (groups / f"{_GROUP}.toml").write_text(
            textwrap.dedent(
                f"""\
                [group]
                name = "{_GROUP}"

                [[members]]
                name = "member"
                repo_root = "{self.repo}"
                """
            ),
            encoding="utf-8",
        )

    def _write_fake_bins(self) -> None:
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        lore = self.bin_dir / "lore"
        lore.write_text(_FAKE_LORE_SCRIPT, encoding="utf-8")
        lore.chmod(lore.stat().st_mode | stat.S_IEXEC)
        camp = self.bin_dir / "camp"
        camp.write_text(_FAKE_CAMP_SCRIPT, encoding="utf-8")
        camp.chmod(camp.stat().st_mode | stat.S_IEXEC)

    def install_craft(self, harness: str = "harness-a") -> Path:
        procedure = (
            self.trailhead_state / "composed" / harness / "plugins" / "craft"
            / "skills" / "_shared" / "execute.md"
        )
        procedure.parent.mkdir(parents=True, exist_ok=True)
        procedure.write_text("# execute procedure\n", encoding="utf-8")
        return procedure

    def install_refine(self, harness: str = "harness-a") -> Path:
        procedure = (
            self.trailhead_state / "composed" / harness / "plugins" / "craft"
            / "skills" / "_shared" / "refine.md"
        )
        procedure.parent.mkdir(parents=True, exist_ok=True)
        procedure.write_text("# refine procedure\n", encoding="utf-8")
        return procedure

    def install_portage(self, harness: str = "harness-a") -> Path:
        marker = (
            self.trailhead_state / "composed" / harness / "plugins" / "portage"
            / ".claude-plugin" / "plugin.json"
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}", encoding="utf-8")
        return marker

    def set_lore_fixture(self, **fixture) -> None:
        self.lore_fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    def set_camp_fixture(self, **fixture) -> None:
        self.camp_fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    @property
    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["RANGER_STATE_DIR"] = str(self.ranger_state)
        env["TRAILHEAD_STATE_DIR"] = str(self.trailhead_state)
        env["CAMP_STATE_DIR"] = str(self.camp_state)
        env["CAMP_CONFIG_DIR"] = str(self.camp_config)
        env["FAKE_LORE_FIXTURE"] = str(self.lore_fixture_path)
        env["FAKE_CAMP_FIXTURE"] = str(self.camp_fixture_path)
        env[_PROVENANCE_ENV] = "drain-tests@example.invalid"
        env["PATH"] = f"{self.bin_dir}{os.pathsep}{env.get('PATH', '')}"
        return env

    def run(self, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None):
        return subprocess.run(
            [sys.executable, str(CLI_PATH), *args],
            capture_output=True,
            text=True,
            env=env or self.env,
            cwd=str(cwd or self.cwd),
        )

    def start(self, *extra: str, holder_pid: int | None = None):
        pid = os.getpid() if holder_pid is None else holder_pid
        return self.run("drain", "start", "--holder-pid", str(pid), *extra)

    @property
    def lock_file(self) -> Path:
        return self.ranger_state / "locks" / f"{_VAULT}.lock"

    def assert_nothing_created(self) -> None:
        assert not (self.ranger_state / "locks").exists()


def _drain(tmp_path: Path, *, with_craft: bool = True) -> Drain:
    drain = Drain(tmp_path)
    if with_craft:
        drain.install_craft()
    return drain


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# ---------------------------------------------------------------------------
# start — preconditions
# ---------------------------------------------------------------------------


def test_start_refuses_when_the_execute_procedure_is_absent(tmp_path):
    drain = _drain(tmp_path, with_craft=False)

    res = drain.start()

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")
    assert "execute.md" in res.stderr
    assert "trailhead install" in res.stderr
    drain.assert_nothing_created()


def test_start_refuses_when_cwd_resolves_to_no_camp_group(tmp_path):
    drain = _drain(tmp_path)

    res = drain.run("drain", "start", "--holder-pid", str(os.getpid()), cwd=drain.outside)

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")
    drain.assert_nothing_created()


def test_start_refuses_without_provenance(tmp_path):
    drain = _drain(tmp_path)
    env = drain.env
    env.pop(_PROVENANCE_ENV, None)
    env["GIT_CONFIG_GLOBAL"] = str(tmp_path / "no-such-gitconfig")

    res = drain.run("drain", "start", "--holder-pid", str(os.getpid()), env=env)

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")
    assert "committer email" in res.stderr
    drain.assert_nothing_created()


def test_start_succeeds_with_degraded_true_when_portage_absent(tmp_path):
    drain = _drain(tmp_path)

    res = drain.start()

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["degraded"] is True
    assert payload["group"] == _GROUP
    assert payload["vault"] == _VAULT


def test_start_succeeds_with_degraded_false_when_portage_present(tmp_path):
    drain = _drain(tmp_path)
    drain.install_portage()

    res = drain.start()

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["degraded"] is False


# ---------------------------------------------------------------------------
# start — the queue is derived and carried in the JSON
# ---------------------------------------------------------------------------


def test_start_carries_the_derived_queue(tmp_path):
    drain = _drain(tmp_path)

    res = drain.start()

    assert res.returncode == 0, res.stderr
    queue = json.loads(res.stdout)["queue"]
    assert [e["name"] for e in queue] == ["t1"]
    assert queue[0]["bucket"] == "buildable"


# ---------------------------------------------------------------------------
# Lock contention — drain and sweep on the same vault
# ---------------------------------------------------------------------------


def test_drain_start_refuses_while_a_refine_sweep_holds_the_lock(tmp_path):
    drain = _drain(tmp_path)
    drain.install_refine()
    drain.set_lore_fixture(vault_resolve=_BOUND_RESOLUTION, tasks=[_task("t1", status="open")], bodies={"t1": _BUILDABLE_BODY})
    sweep_res = drain.run("sweep", "start", "--holder-pid", str(os.getpid()))
    assert sweep_res.returncode == 0, sweep_res.stderr

    res = drain.start()

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")
    assert "already running" in res.stderr


def test_drain_start_reports_a_stale_refine_lock_with_the_rm_ritual(tmp_path):
    drain = _drain(tmp_path)
    drain.install_refine()
    drain.set_lore_fixture(vault_resolve=_BOUND_RESOLUTION, tasks=[_task("t1", status="open")], bodies={"t1": _BUILDABLE_BODY})
    dead = _dead_pid()
    sweep_res = drain.run("sweep", "start", "--holder-pid", str(dead))
    assert sweep_res.returncode == 0, sweep_res.stderr

    res = drain.start()

    assert res.returncode != 0
    assert f"rm {drain.lock_file}" in res.stderr
    assert drain.lock_file.exists(), "a stale lock is never auto-removed"


def test_refine_sweep_start_refuses_while_a_drain_holds_the_lock(tmp_path):
    drain = _drain(tmp_path)
    drain.install_refine()
    drain_res = drain.start()
    assert drain_res.returncode == 0, drain_res.stderr

    res = drain.run("sweep", "start", "--holder-pid", str(os.getpid()))

    assert res.returncode != 0
    assert "already running" in res.stderr


# ---------------------------------------------------------------------------
# derive
# ---------------------------------------------------------------------------


def test_derive_json_carries_bucket_and_slug(tmp_path):
    drain = _drain(tmp_path)

    res = drain.run("drain", "derive", "--json")

    assert res.returncode == 0, res.stderr
    entries = json.loads(res.stdout)
    assert entries[0]["bucket"] == "buildable"
    assert entries[0]["slug"] == "t1"


def test_derive_human_rendering_names_the_bucket_and_slug(tmp_path):
    drain = _drain(tmp_path)

    res = drain.run("drain", "derive")

    assert res.returncode == 0, res.stderr
    assert "bucket=buildable" in res.stdout
    assert "slug=t1" in res.stdout


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


def test_record_accepts_each_grammar_token(tmp_path):
    drain = _drain(tmp_path)

    for outcome in ["PUSHED branch sha diffstat", "BLOCKED some reason", "FAILED oops", "SKIPPED not runnable"]:
        res = drain.run("drain", "record", "--task", "task/t1", "--outcome", outcome)
        assert res.returncode == 0, res.stderr
        payload = json.loads(res.stdout)
        assert payload["task"] == "task/t1"


def test_record_refuses_an_outcome_with_no_argument(tmp_path):
    drain = _drain(tmp_path)

    res = drain.run("drain", "record", "--task", "task/t1", "--outcome", "PUSHED")

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")


def test_record_refuses_an_unrecognized_token(tmp_path):
    drain = _drain(tmp_path)

    res = drain.run("drain", "record", "--task", "task/t1", "--outcome", "PROMOTED nope")

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")


@pytest.mark.parametrize(
    "bad_task",
    ["task/foo`touch pwn`", "task/foo;rm -rf", "task/foo bar", "task/foo\nbar"],
    ids=["backtick", "semicolon", "space", "newline"],
)
def test_record_refuses_shell_unsafe_task_ids(tmp_path, bad_task):
    drain = _drain(tmp_path)

    res = drain.run("drain", "record", "--task", bad_task, "--outcome", "FAILED oops")

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")


# ---------------------------------------------------------------------------
# finish
# ---------------------------------------------------------------------------


def test_finish_releases_the_lock_for_the_returned_token(tmp_path):
    drain = _drain(tmp_path)
    start_res = drain.start()
    token = json.loads(start_res.stdout)["lock_token"]

    finish_res = drain.run("drain", "finish", "--vault", _VAULT, "--token", token)

    assert finish_res.returncode == 0, finish_res.stderr
    assert not drain.lock_file.exists()


def test_finish_refuses_a_mismatched_token(tmp_path):
    drain = _drain(tmp_path)
    drain.start()

    res = drain.run("drain", "finish", "--vault", _VAULT, "--token", "not-the-right-token")

    assert res.returncode != 0
    assert drain.lock_file.exists()
