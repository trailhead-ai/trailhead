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

    if argv[:2] == ["sync", "--json"]:
        sync = fixture.get("sync")
        if sync is None:
            print("fake camp: no sync fixture", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(sync))
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
    drain.set_lore_fixture(
        vault_resolve=_BOUND_RESOLUTION,
        tasks=[_task("t1", status="open")],
        bodies={"t1": _BUILDABLE_BODY},
    )
    sweep_res = drain.run("sweep", "start", "--holder-pid", str(os.getpid()))
    assert sweep_res.returncode == 0, sweep_res.stderr

    res = drain.start()

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")
    assert "already running" in res.stderr


def test_drain_start_reports_a_stale_refine_lock_with_the_rm_ritual(tmp_path):
    drain = _drain(tmp_path)
    drain.install_refine()
    drain.set_lore_fixture(
        vault_resolve=_BOUND_RESOLUTION,
        tasks=[_task("t1", status="open")],
        bodies={"t1": _BUILDABLE_BODY},
    )
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


@pytest.mark.parametrize(
    "outcome",
    ["PUSHED", "PROMOTED nope", "all done!", "", "PUSHED branch-only"],
    ids=["no-argument", "unknown-token", "free-text", "empty", "pushed-missing-fields"],
)
def test_record_buckets_an_unparseable_outcome_as_failed(tmp_path, outcome):
    # The agent doc promises this: "the recording verb parses the file's first
    # line and buckets anything else `failed`". A refusal instead would leave
    # the finished-but-unrecordable run with no line in the report at all, and
    # the coordinator with a nonzero exit and no bucket to write.
    drain = _drain(tmp_path)
    report_path = json.loads(drain.start().stdout)["report_path"]

    res = drain.run(
        "drain", "record", "--report", report_path,
        "--task", "task/t1", "--outcome", outcome,
    )

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["token"] == "FAILED"
    assert payload["unparseable"] is True
    text = Path(report_path).read_text()
    failed_section = text.split("## Failed\n", 1)[1].split("## ", 1)[0]
    assert "task/t1" in failed_section


def test_record_pushed_appends_the_pushed_entry_with_its_diffstat(tmp_path):
    # Without this the whole `## Pushed` section of a successful drain renders
    # empty: `record` was the only verb the loop ever called for a PUSHED task.
    drain = _drain(tmp_path)
    report_path = json.loads(drain.start().stdout)["report_path"]

    res = drain.run(
        "drain", "record", "--report", report_path, "--task", "task/t1",
        "--outcome", "PUSHED worktree-t1 a1b2c3d 3 files changed, 45 insertions(+)",
    )

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["token"] == "PUSHED"
    assert payload["branch"] == "worktree-t1"
    assert payload["sha"] == "a1b2c3d"
    text = Path(report_path).read_text()
    assert "## Pushed" in text
    assert "worktree-t1" in text
    assert "a1b2c3d" in text
    assert "3 files changed, 45 insertions(+)" in text


def test_record_pushed_carries_the_pr_link_from_the_prs_sidecar(tmp_path):
    drain = _drain(tmp_path)
    report_path = json.loads(drain.start().stdout)["report_path"]
    sidecar = tmp_path / "prs.json"
    sidecar.write_text(
        json.dumps({
            "schema_version": 1,
            "prs": [{"branch": "worktree-t1", "url": "https://github.com/org/repo/pull/7",
                     "pr_number": "7"}],
            "external_tracker": None,
        }),
        encoding="utf-8",
    )

    res = drain.run(
        "drain", "record", "--report", report_path, "--task", "task/t1",
        "--outcome", "PUSHED worktree-t1 a1b2c3d 1 file changed",
        "--prs-json", str(sidecar),
    )

    assert res.returncode == 0, res.stderr
    assert "https://github.com/org/repo/pull/7" in Path(report_path).read_text()


class TestRecordMarksInFlightInProcess:
    """A ``PUSHED`` outcome opens its own cap slot, without a second command.

    The coordinator used to read `record`'s branch/sha/diffstat back out and
    retype them into `drain inflight mark` — agent-authored free text
    re-interpolated into a shell command string, which is the one thing the
    drain's ground rules forbid. With `--mark-inflight` the values never
    leave this process: they are parsed from the outcome file and handed
    straight to the same substrate the standalone verb drives.
    """

    def test_pushed_with_mark_inflight_opens_the_slot(self, tmp_path):
        drain, payload = _started(tmp_path)
        report_path = payload["report_path"]

        res = drain.run(
            "drain", "record", "--report", report_path, "--task", "task/t1",
            "--outcome", "PUSHED worktree-t1 a1b2c3d 3 files changed",
            "--mark-inflight", "--workspace", "t1",
        )

        assert res.returncode == 0, res.stderr
        assert json.loads(res.stdout)["in_flight"] == 1
        counted = json.loads(
            drain.run("drain", "inflight", "count", "--report", report_path).stdout
        )
        assert counted["in_flight"] == 1
        text = Path(report_path).read_text()
        assert "holding the concurrency cap" in text
        assert "3 files changed" in text

    def test_pushed_without_mark_inflight_holds_no_slot(self, tmp_path):
        drain, payload = _started(tmp_path)
        report_path = payload["report_path"]

        drain.run(
            "drain", "record", "--report", report_path, "--task", "task/t1",
            "--outcome", "PUSHED worktree-t1 a1b2c3d 3 files changed",
        )

        counted = json.loads(
            drain.run("drain", "inflight", "count", "--report", report_path).stdout
        )
        assert counted["in_flight"] == 0

    def test_mark_inflight_requires_the_workspace_it_marks(self, tmp_path):
        drain, payload = _started(tmp_path)

        res = drain.run(
            "drain", "record", "--report", payload["report_path"], "--task", "task/t1",
            "--outcome", "PUSHED worktree-t1 a1b2c3d 3 files changed",
            "--mark-inflight",
        )

        assert res.returncode != 0
        assert "--workspace" in res.stderr


class TestRecordRefusesUnsafePushedRefs:
    """Shape validation at the CLI boundary — the option-(b) backstop.

    A refusal never exits nonzero: it buckets the task ``failed`` with a
    named reason, for the same reason an unparseable line does — a finished
    run with no line in the report is the one outcome an unattended operator
    cannot recover from.
    """

    @pytest.mark.parametrize(
        "branch",
        ["worktree-t1;rm", "worktree-t1`id`", "../../etc", "worktree-t1.lock"],
        ids=["semicolon", "backtick", "traversal", "reserved-suffix"],
    )
    def test_an_unsafe_branch_buckets_failed(self, tmp_path, branch):
        drain, payload = _started(tmp_path)
        report_path = payload["report_path"]

        res = drain.run(
            "drain", "record", "--report", report_path, "--task", "task/t1",
            "--outcome", f"PUSHED {branch} a1b2c3d 1 file changed",
        )

        assert res.returncode == 0, res.stderr
        assert json.loads(res.stdout)["bucket"] == "failed"
        text = Path(report_path).read_text()
        assert "refused unsafe PUSHED branch" in text
        assert "## Pushed" not in text.split("## Failed", 1)[1]

    def test_a_non_hex_sha_buckets_failed(self, tmp_path):
        drain, payload = _started(tmp_path)
        report_path = payload["report_path"]

        res = drain.run(
            "drain", "record", "--report", report_path, "--task", "task/t1",
            "--outcome", "PUSHED worktree-t1 $(whoami) 1 file changed",
        )

        assert res.returncode == 0, res.stderr
        assert json.loads(res.stdout)["bucket"] == "failed"
        assert "refused unsafe PUSHED sha" in Path(report_path).read_text()

    def test_a_branch_that_is_not_this_workspaces_worktree_buckets_failed(self, tmp_path):
        drain, payload = _started(tmp_path)
        report_path = payload["report_path"]

        res = drain.run(
            "drain", "record", "--report", report_path, "--task", "task/t1",
            "--outcome", "PUSHED worktree-somewhere-else a1b2c3d 1 file changed",
            "--mark-inflight", "--workspace", "t1",
        )

        assert res.returncode == 0, res.stderr
        assert json.loads(res.stdout)["bucket"] == "failed"
        assert "expected `worktree-t1`" in Path(report_path).read_text()

    def test_the_standalone_mark_verb_refuses_an_unsafe_branch_loudly(self, tmp_path):
        # `inflight mark` has no outcome to bucket — it is driven by hand, so
        # a bad value there is a usage error, not a task result.
        drain, payload = _started(tmp_path)

        res = drain.run(
            "drain", "inflight", "mark", "--report", payload["report_path"],
            "--task", "task/t1", "--branch", "worktree-t1;rm", "--sha", "a1b2c3d",
            "--diffstat", "1 file changed", "--workspace", "t1",
        )

        assert res.returncode != 0
        assert "refused unsafe PUSHED branch" in res.stderr


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


# ---------------------------------------------------------------------------
# record/finish wired to the report substrate
# ---------------------------------------------------------------------------


def test_start_seeds_a_report_and_carries_its_path(tmp_path):
    drain = _drain(tmp_path)

    res = drain.start()

    payload = json.loads(res.stdout)
    report_path = Path(payload["report_path"])
    assert report_path.exists()
    assert "**Group:** testgroup" in report_path.read_text()


def test_record_with_report_appends_the_matching_bucket_line(tmp_path):
    drain = _drain(tmp_path)
    start_res = drain.start()
    report_path = json.loads(start_res.stdout)["report_path"]

    res = drain.run(
        "drain", "record", "--report", report_path,
        "--task", "task/t1", "--outcome", "BLOCKED needs a human",
    )

    assert res.returncode == 0, res.stderr
    text = Path(report_path).read_text()
    assert "task/t1" in text
    assert "needs a human" in text


def test_record_without_report_only_validates(tmp_path):
    drain = _drain(tmp_path)

    res = drain.run("drain", "record", "--task", "task/t1", "--outcome", "SKIPPED not buildable")

    assert res.returncode == 0, res.stderr


def test_finish_with_report_writes_the_footer_and_still_standing_workspaces(tmp_path):
    drain = _drain(tmp_path)
    start_res = drain.start()
    payload = json.loads(start_res.stdout)
    report_path = payload["report_path"]
    token = payload["lock_token"]

    res = drain.run(
        "drain", "finish", "--report", report_path, "--still-standing", "ws-a",
        "--vault", _VAULT, "--token", token,
    )

    assert res.returncode == 0, res.stderr
    text = Path(report_path).read_text()
    assert "Report written to" in text
    assert "camp remove ws-a" in text


# ---------------------------------------------------------------------------
# inflight — the cap substrate the loop drives between record and finish
# ---------------------------------------------------------------------------


def _started(tmp_path, *extra: str) -> tuple:
    drain = _drain(tmp_path)
    drain.install_portage()
    payload = json.loads(drain.start(*extra).stdout)
    return drain, payload


def test_inflight_mark_then_count_reports_the_occupied_cap(tmp_path):
    drain, payload = _started(tmp_path)
    report_path = payload["report_path"]

    mark = drain.run(
        "drain", "inflight", "mark", "--report", report_path, "--task", "task/t1",
        "--branch", "worktree-t1", "--sha", "a1b2c3d", "--diffstat", "1 file changed",
        "--workspace", "t1",
    )
    assert mark.returncode == 0, mark.stderr

    count = drain.run("drain", "inflight", "count", "--report", report_path)
    assert count.returncode == 0, count.stderr
    counted = json.loads(count.stdout)
    assert counted["in_flight"] == 1
    assert counted["inflight_cap"] == 3
    assert counted["at_cap"] is False


def test_inflight_count_reports_at_cap_so_the_loop_pauses_dispatch(tmp_path):
    drain, payload = _started(tmp_path, "--inflight-cap", "1")
    report_path = payload["report_path"]
    drain.run(
        "drain", "inflight", "mark", "--report", report_path, "--task", "task/t1",
        "--branch", "worktree-t1", "--sha", "a1b2c3d", "--diffstat", "d", "--workspace", "t1",
    )

    counted = json.loads(drain.run("drain", "inflight", "count", "--report", report_path).stdout)

    assert counted["at_cap"] is True


def test_inflight_mark_is_refused_in_degraded_mode(tmp_path):
    drain = _drain(tmp_path)  # no portage installed -> degraded
    report_path = json.loads(drain.start().stdout)["report_path"]

    res = drain.run(
        "drain", "inflight", "mark", "--report", report_path, "--task", "task/t1",
        "--branch", "worktree-t1", "--sha", "a1b2c3d", "--diffstat", "d", "--workspace", "t1",
    )

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")
    assert "degraded" in res.stderr


def test_inflight_resolve_frees_the_slot_and_reports_the_bucket(tmp_path):
    drain, payload = _started(tmp_path)
    report_path = payload["report_path"]
    drain.run(
        "drain", "inflight", "mark", "--report", report_path, "--task", "task/t1",
        "--branch", "worktree-t1", "--sha", "a1b2c3d", "--diffstat", "1 file changed",
        "--workspace", "t1",
    )
    monitor_outcome = tmp_path / "monitor.outcome"
    monitor_outcome.write_text("MERGED\n", encoding="utf-8")

    res = drain.run(
        "drain", "inflight", "resolve", "--report", report_path, "--task", "task/t1",
        "--monitor-outcome-file", str(monitor_outcome),
    )

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["bucket"] == "pushed"
    counted = json.loads(drain.run("drain", "inflight", "count", "--report", report_path).stdout)
    assert counted["in_flight"] == 0
    assert "### Merged" in Path(report_path).read_text()


def test_inflight_resolve_reads_a_missing_monitor_outcome_file_as_crashed(tmp_path):
    drain, payload = _started(tmp_path)
    report_path = payload["report_path"]
    drain.run(
        "drain", "inflight", "mark", "--report", report_path, "--task", "task/t1",
        "--branch", "worktree-t1", "--sha", "a1b2c3d", "--diffstat", "d", "--workspace", "t1",
    )

    res = drain.run(
        "drain", "inflight", "resolve", "--report", report_path, "--task", "task/t1",
        "--monitor-outcome-file", str(tmp_path / "never-written.outcome"),
    )

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["bucket"] == "crashed"
    assert "## Crashed" in Path(report_path).read_text()


def test_inflight_resolve_takes_the_approval_pr_link_from_the_sidecar(tmp_path):
    drain, payload = _started(tmp_path)
    report_path = payload["report_path"]
    drain.run(
        "drain", "inflight", "mark", "--report", report_path, "--task", "task/t1",
        "--branch", "worktree-t1", "--sha", "a1b2c3d", "--diffstat", "d", "--workspace", "t1",
    )
    monitor_outcome = tmp_path / "monitor.outcome"
    monitor_outcome.write_text("READY awaiting the human-approval label\n", encoding="utf-8")
    sidecar = tmp_path / "prs.json"
    sidecar.write_text(
        json.dumps({"schema_version": 1, "external_tracker": None, "prs": [
            {"branch": "worktree-t1", "url": "https://github.com/org/repo/pull/9", "pr_number": "9"}
        ]}),
        encoding="utf-8",
    )

    res = drain.run(
        "drain", "inflight", "resolve", "--report", report_path, "--task", "task/t1",
        "--monitor-outcome-file", str(monitor_outcome), "--prs-json", str(sidecar),
    )

    assert res.returncode == 0, res.stderr
    text = Path(report_path).read_text()
    assert "gh pr edit https://github.com/org/repo/pull/9 --add-label human-approved" in text


def test_inflight_expire_reclaims_a_slot_past_its_deadline(tmp_path):
    drain, payload = _started(tmp_path, "--monitor-deadline", "0.0001")
    report_path = payload["report_path"]
    drain.run(
        "drain", "inflight", "mark", "--report", report_path, "--task", "task/t1",
        "--branch", "worktree-t1", "--sha", "a1b2c3d", "--diffstat", "d", "--workspace", "t1",
        "--deadline-hours", "-1",
    )

    res = drain.run("drain", "inflight", "expire", "--report", report_path)

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["reclaimed"] == ["task/t1"]
    text = Path(report_path).read_text()
    assert "### Monitor timeout" in text
    counted = json.loads(drain.run("drain", "inflight", "count", "--report", report_path).stdout)
    assert counted["in_flight"] == 0


# ---------------------------------------------------------------------------
# crashed / dropped — the two buckets no outcome file ever produces
# ---------------------------------------------------------------------------


def test_crashed_appends_the_crashed_bucket_line(tmp_path):
    drain = _drain(tmp_path)
    report_path = json.loads(drain.start().stdout)["report_path"]

    res = drain.run(
        "drain", "crashed", "--report", report_path, "--task", "task/t1",
        "--reason", "monitor left no readable outcome file",
    )

    assert res.returncode == 0, res.stderr
    text = Path(report_path).read_text()
    crashed_section = text.split("## Crashed\n", 1)[1].split("## ", 1)[0]
    assert "task/t1" in crashed_section


def test_dropped_appends_the_dropped_bucket_line(tmp_path):
    drain = _drain(tmp_path)
    report_path = json.loads(drain.start().stdout)["report_path"]

    res = drain.run(
        "drain", "dropped", "--report", report_path, "--task", "task/t1",
        "--reason", "in-flight cap full at drain end",
    )

    assert res.returncode == 0, res.stderr
    text = Path(report_path).read_text()
    dropped_section = text.split("## Dropped\n", 1)[1].split("## ", 1)[0]
    assert "task/t1" in dropped_section
    assert "cap full" in dropped_section


# ---------------------------------------------------------------------------
# sync-gate / teardown-check — the two classifications the loop never re-derives
# ---------------------------------------------------------------------------


def test_sync_gate_passes_when_every_member_is_at_origin_main(tmp_path):
    drain = _drain(tmp_path)
    drain.set_camp_fixture(workspaces=[], sync={"status": "ok", "members": {"member": {"action": "ff"}}})

    res = drain.run("drain", "sync-gate", "--json")

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["ok"] is True


@pytest.mark.parametrize("action", ["skip-dirty", "skip-off-main", "absent"])
def test_sync_gate_blocks_on_a_skip_hiding_under_a_top_level_ok(tmp_path, action):
    # The whole reason this is a verb: `camp sync` reports a top-level "ok"
    # while a member sat out, and prose that re-derives the classification
    # drifts from the JSON silently.
    drain = _drain(tmp_path)
    drain.set_camp_fixture(
        workspaces=[], sync={"status": "ok", "members": {"member": {"action": action}}},
    )

    res = drain.run("drain", "sync-gate", "--json")

    assert res.returncode == 1
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["blocking"] == [["member", action]]
    assert action in payload["reason"]


def test_teardown_check_licenses_removal_only_on_merged(tmp_path):
    drain = _drain(tmp_path)
    outcome = tmp_path / "monitor.outcome"
    outcome.write_text("MERGED\n", encoding="utf-8")

    res = drain.run("drain", "teardown-check", "--monitor-outcome-file", str(outcome))

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["teardown"] is True


def test_teardown_check_preserves_the_workspace_on_a_terminal_state_needing_a_human(tmp_path):
    drain = _drain(tmp_path)
    outcome = tmp_path / "monitor.outcome"
    outcome.write_text("READY awaiting human approval\n", encoding="utf-8")

    payload = json.loads(
        drain.run("drain", "teardown-check", "--monitor-outcome-file", str(outcome)).stdout
    )

    assert payload["teardown"] is False
    assert payload["crashed"] is False


def test_teardown_check_reads_a_missing_outcome_file_as_a_crash(tmp_path):
    drain = _drain(tmp_path)

    payload = json.loads(
        drain.run(
            "drain", "teardown-check",
            "--monitor-outcome-file", str(tmp_path / "never-written.outcome"),
        ).stdout
    )

    assert payload["teardown"] is False
    assert payload["crashed"] is True


def test_teardown_check_tears_down_at_push_in_degraded_mode(tmp_path):
    drain = _drain(tmp_path)

    payload = json.loads(drain.run("drain", "teardown-check", "--degraded").stdout)

    assert payload["teardown"] is True


def test_teardown_check_never_tears_down_an_expired_slot(tmp_path):
    drain = _drain(tmp_path)
    outcome = tmp_path / "monitor.outcome"
    outcome.write_text("MERGED\n", encoding="utf-8")

    payload = json.loads(
        drain.run(
            "drain", "teardown-check", "--monitor-outcome-file", str(outcome), "--expired",
        ).stdout
    )

    assert payload["teardown"] is False


# ---------------------------------------------------------------------------
# start — the loop's three bounds are flags, defaulted, and durable
# ---------------------------------------------------------------------------


def test_start_carries_the_default_loop_bounds(tmp_path):
    drain = _drain(tmp_path)

    payload = json.loads(drain.start().stdout)

    assert payload["concurrency"] == 2
    assert payload["inflight_cap"] == 3
    assert payload["monitor_deadline_hours"] == 2.0


def test_start_bounds_are_overridable_and_persisted_in_the_report_state(tmp_path):
    drain = _drain(tmp_path)

    payload = json.loads(
        drain.start(
            "--concurrency", "4", "--inflight-cap", "1", "--monitor-deadline", "0.5",
        ).stdout
    )

    assert payload["concurrency"] == 4
    assert payload["inflight_cap"] == 1
    assert payload["monitor_deadline_hours"] == 0.5

    # Persisted, so the bounds survive the coordinator that read them once.
    state = json.loads(Path(payload["report_path"]).with_suffix(".state.json").read_text())
    assert state["concurrency"] == 4
    assert state["inflight_cap"] == 1
    assert state["monitor_deadline_hours"] == 0.5


@pytest.mark.parametrize(
    "flag,value",
    [("--concurrency", "0"), ("--inflight-cap", "0"), ("--monitor-deadline", "0")],
)
def test_start_refuses_a_non_positive_bound(tmp_path, flag, value):
    drain = _drain(tmp_path)

    res = drain.start(flag, value)

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")
    drain.assert_nothing_created()


# ---------------------------------------------------------------------------
# record — the outcome file, and the agent-crash bucket
# ---------------------------------------------------------------------------


def _outcomes_dir(start_payload: dict) -> Path:
    return Path(start_payload["outcomes_dir"])


def test_record_reads_the_outcome_from_the_task_s_own_outcome_file(tmp_path):
    # The preferred form: the coordinator never interpolates agent-written
    # text into a command string, and the path is recomputed here rather than
    # formed by hand twice.
    drain = _drain(tmp_path)
    payload = json.loads(drain.start().stdout)
    (_outcomes_dir(payload) / "t1.outcome").write_text(
        "PUSHED worktree-t1 a1b2c3d 2 files changed\n", encoding="utf-8"
    )

    res = drain.run(
        "drain", "record", "--report", payload["report_path"],
        "--task", "task/t1", "--outcome-file",
    )

    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["token"] == "PUSHED"
    assert out["bucket"] == "pushed"
    assert "worktree-t1" in Path(payload["report_path"]).read_text()


def test_record_reads_an_explicitly_named_outcome_file(tmp_path):
    drain = _drain(tmp_path)
    payload = json.loads(drain.start().stdout)
    elsewhere = tmp_path / "elsewhere.outcome"
    elsewhere.write_text("BLOCKED needs a human\n", encoding="utf-8")

    res = drain.run(
        "drain", "record", "--report", payload["report_path"],
        "--task", "task/t1", "--outcome-file", str(elsewhere),
    )

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["bucket"] == "blocked"


@pytest.mark.parametrize("content", [None, "", "   \n"], ids=["absent", "empty", "blank"])
def test_a_missing_or_empty_agent_outcome_file_buckets_crashed(tmp_path, content):
    # An agent that wrote nothing died, timed out, or never ran — its
    # workspace is preserved and its run claim still stands, which is the
    # crashed ritual, not the failed one (whose recovery assumes an outcome
    # line to read).
    drain = _drain(tmp_path)
    payload = json.loads(drain.start().stdout)
    if content is not None:
        (_outcomes_dir(payload) / "t1.outcome").write_text(content, encoding="utf-8")

    res = drain.run(
        "drain", "record", "--report", payload["report_path"],
        "--task", "task/t1", "--outcome-file",
    )

    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["bucket"] == "crashed"
    text = Path(payload["report_path"]).read_text()
    crashed_section = text.split("## Crashed\n", 1)[1].split("## ", 1)[0]
    assert "task/t1" in crashed_section
    failed_section = text.split("## Failed\n", 1)[1].split("## ", 1)[0]
    assert "task/t1" not in failed_section


def test_record_requires_an_outcome_or_an_outcome_file(tmp_path):
    drain = _drain(tmp_path)

    res = drain.run("drain", "record", "--task", "task/t1")

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")


def test_the_bare_outcome_file_form_needs_a_report_to_recompute_the_path_from(tmp_path):
    drain = _drain(tmp_path)

    res = drain.run("drain", "record", "--task", "task/t1", "--outcome-file")

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")


# ---------------------------------------------------------------------------
# inflight resolve — a slot that is not held
# ---------------------------------------------------------------------------


def test_inflight_resolve_refuses_a_task_that_holds_no_slot(tmp_path):
    drain = _drain(tmp_path)
    payload = json.loads(drain.start().stdout)
    monitor_outcome = tmp_path / "monitor.outcome"
    monitor_outcome.write_text("MERGED\n", encoding="utf-8")

    res = drain.run(
        "drain", "inflight", "resolve", "--report", payload["report_path"],
        "--task", "task/t1", "--monitor-outcome-file", str(monitor_outcome),
    )

    assert res.returncode != 0
    assert "holds no in-flight slot" in res.stderr
