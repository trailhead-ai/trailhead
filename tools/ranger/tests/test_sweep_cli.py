"""Tests for the ``ranger sweep start|derive|record|finish`` CLI verbs.

These drive the real ``ranger`` CLI shim as a subprocess (matching
``test_queue_cli.py``), with a fake ``lore`` executable first on ``PATH`` and
every path resolver redirected through per-app override env vars
(``RANGER_STATE_DIR``, ``TRAILHEAD_STATE_DIR``, ``CAMP_CONFIG_DIR``,
``CAMP_STATE_DIR``) so no real state, config, or vault is ever touched.

Test contract:
- Each of the three ``start`` preconditions fails with its own one-line
  message carrying remediation text, a nonzero exit, and **nothing created**
  — no lock file, no report.
- A floor election refuses for each of its three shapes (no binding at all,
  a binding whose vault fell through an allowlist, a binding naming a vault
  absent from lore's config), and the message names the failing binding.
- The happy path start→record×N→finish holds the lock for the sweep's
  duration, releases it at finish, writes a complete report, and prints
  exactly the contracted JSON keys plus a report-path breadcrumb on stderr.
- ``record`` with an unparseable outcome line buckets ``failed`` and exits 0,
  so a bad agent return never stops the sweep; likewise a record body whose
  unresolved section carries no parseable question renders a placeholder and
  exits 0.
- An ``ESCALATED`` outcome renders the question and its answer command
  whatever bucket the task was derived into, so a mid-sweep escalation out of
  ``blocked-answered`` is never reported as a bare id.
- ``--task`` is validated before it reaches the report, because the report
  embeds it in a shell command the operator is told to paste.
- Every ``lore`` read names the elected vault: the fake ``lore`` below exits
  nonzero on a ``task list`` or ``record show`` that omits ``--vault``.
- Lock contention during ``start`` surfaces the lock module's own message and
  writes no report — and distinguishes the two cases that matter: a sweep
  whose holder is alive refuses as ALREADY RUNNING (never as stale, which
  would invite removing a live sweep's lock), while a dead holder is reported
  as stale with the exact ``rm`` command and the file left in place.
- The lock records the supplied holder pid, not the ephemeral ``start``
  subprocess's, and ``finish`` releases only for the token ``start`` returned.
- Crash recovery: a sweep killed mid-task leaves a partial report and a held
  lock; clearing the stale lock with the command the refusal prints and
  re-running start→record×N→finish reaches the same net state as an
  uninterrupted sweep, including re-recording an already-recorded task.
"""

from __future__ import annotations

import json
import os
import re
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

_QUESTION_BODY = textwrap.dedent(
    """\
    # A task

    Some prose.

    ## Refine — unresolved

    **Question:** Which queue should this drain?
    """
)

_ANSWERED_BODY = _QUESTION_BODY + "**Answer:** the shaping queue.\n"

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

    # Every read names the vault the sweep elected. A bare `task list` or
    # `record show` is located by a cwd-blind scan across configured vaults in
    # declaration order, so a colliding task name would be listed, classified,
    # and quoted from another camp group's vault entirely.
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
        if elected_vault(argv) != fixture["vault_resolve"]["vault"]:
            print(f"fake lore: wrong vault: {argv!r}", file=sys.stderr)
            sys.exit(2)
        name = argv[2].split("/", 1)[1]
        # A record that left the elected vault between derivation and the read.
        if name in fixture.get("unreadable", []):
            print(f"fake lore: no record {name!r} in this vault", file=sys.stderr)
            sys.exit(1)
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


def _task(name: str, *, status: str = "open", created: str = "2026-01-01T00:00:00Z") -> dict:
    return {
        "name": name,
        "status": status,
        "created-at": created,
        "updated-at": created,
        "parent": None,
        "depends-on": [],
        "children": [],
    }


class Sweep:
    """A fully isolated ranger sweep environment: fake lore, fake camp group,
    fake composed craft install, and redirected state/config dirs."""

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
        self.fixture_path = tmp_path / "lore-fixture.json"
        self.cwd = self.repo

        self._write_group_config()
        self._write_fake_lore()
        self.set_fixture(
            vault_resolve=_BOUND_RESOLUTION,
            tasks=[_task("t1"), _task("t2"), _task("t3")],
            bodies={},
        )

    # --- fixture setup -------------------------------------------------

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

    def _write_fake_lore(self) -> None:
        self.bin_dir = self.tmp / "bin"
        self.bin_dir.mkdir()
        lore = self.bin_dir / "lore"
        lore.write_text(_FAKE_LORE_SCRIPT, encoding="utf-8")
        lore.chmod(lore.stat().st_mode | stat.S_IEXEC)

    def install_craft(self, harness: str = "harness-a") -> Path:
        procedure = (
            self.trailhead_state
            / "composed"
            / harness
            / "plugins"
            / "craft"
            / "skills"
            / "_shared"
            / "refine.md"
        )
        procedure.parent.mkdir(parents=True, exist_ok=True)
        procedure.write_text("# refine procedure\n", encoding="utf-8")
        return procedure

    def set_fixture(self, **fixture) -> None:
        self.fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    def set_resolution(self, **overrides) -> None:
        fixture = json.loads(self.fixture_path.read_text())
        fixture["vault_resolve"] = {**_BOUND_RESOLUTION, **overrides}
        self.fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    def set_bodies(self, bodies: dict[str, str]) -> None:
        fixture = json.loads(self.fixture_path.read_text())
        fixture["bodies"] = bodies
        self.fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    def set_tasks(self, tasks: list[dict]) -> None:
        fixture = json.loads(self.fixture_path.read_text())
        fixture["tasks"] = tasks
        self.fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    def set_unreadable(self, names: list[str]) -> None:
        """Make `record show` fail for *names* — the record left the vault."""
        fixture = json.loads(self.fixture_path.read_text())
        fixture["unreadable"] = names
        self.fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    # --- running -------------------------------------------------------

    @property
    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["RANGER_STATE_DIR"] = str(self.ranger_state)
        env["TRAILHEAD_STATE_DIR"] = str(self.trailhead_state)
        env["CAMP_STATE_DIR"] = str(self.camp_state)
        env["CAMP_CONFIG_DIR"] = str(self.camp_config)
        env["FAKE_LORE_FIXTURE"] = str(self.fixture_path)
        env["PATH"] = f"{self.bin_dir}{os.pathsep}{env.get('PATH', '')}"
        return env

    def run(
        self, *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CLI_PATH), *args],
            capture_output=True,
            text=True,
            env=env or self.env,
            cwd=str(cwd or self.cwd),
        )

    def run_without_lore(self, *args: str) -> subprocess.CompletedProcess:
        """Run a verb with a PATH that carries no `lore` executable at all.

        The CLI is invoked by absolute path, so an empty PATH changes exactly
        one thing: whether the `lore` the sweep shells out to can be found.
        """
        empty_bin = self.tmp / "empty-bin"
        empty_bin.mkdir(exist_ok=True)
        env = self.env
        env["PATH"] = str(empty_bin)
        return self.run(*args, env=env)

    def start(self, *extra: str, holder_pid: int | None = None) -> subprocess.CompletedProcess:
        """Start a sweep held by *holder_pid*.

        Defaults to this pytest process, which stands in for the coordinator:
        a long-lived process distinct from the ephemeral `ranger sweep start`
        subprocess — the real architecture, where the acquiring process is
        always already gone by the time anyone reads the lock.
        """
        pid = os.getpid() if holder_pid is None else holder_pid
        return self.run("sweep", "start", "--holder-pid", str(pid), *extra)

    # --- inspection ----------------------------------------------------

    @property
    def lock_file(self) -> Path:
        return self.ranger_state / "locks" / f"{_VAULT}.lock"

    def reports(self) -> list[Path]:
        return sorted((self.ranger_state / "reports" / _GROUP).glob("*.md"))

    def assert_nothing_created(self) -> None:
        assert not (self.ranger_state / "locks").exists()
        assert not (self.ranger_state / "reports").exists()


def _sweep(tmp_path: Path, *, with_craft: bool = True) -> Sweep:
    sweep = Sweep(tmp_path)
    if with_craft:
        sweep.install_craft()
    return sweep


def _dead_pid() -> int:
    """A pid guaranteed to be dead: spawn a trivial subprocess and reap it."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# ---------------------------------------------------------------------------
# Precondition 1 — craft's refine procedure
# ---------------------------------------------------------------------------


def test_start_refuses_when_the_refine_procedure_is_absent(tmp_path):
    sweep = _sweep(tmp_path, with_craft=False)

    res = sweep.start()

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")
    assert "refine.md" in res.stderr
    assert "plugins/craft/skills/_shared/refine.md" in res.stderr
    assert "trailhead install" in res.stderr
    sweep.assert_nothing_created()


def test_start_finds_the_procedure_under_any_harness(tmp_path):
    sweep = _sweep(tmp_path, with_craft=False)
    procedure = sweep.install_craft(harness="some-other-harness")

    res = sweep.start()

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["procedure_path"] == str(procedure)


# ---------------------------------------------------------------------------
# Precondition 2 — camp group resolution
# ---------------------------------------------------------------------------


def test_start_refuses_when_cwd_resolves_to_no_camp_group(tmp_path):
    sweep = _sweep(tmp_path)

    res = sweep.run("sweep", "start", "--holder-pid", str(os.getpid()), cwd=sweep.outside)

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")
    assert str(sweep.outside) in res.stderr
    assert "member repos" in res.stderr, "the remediation is to move, not to pass a flag"
    sweep.assert_nothing_created()


def test_start_offers_no_group_override(tmp_path):
    """A --group flag could only relabel the report — the vault election
    still follows cwd — so the flag must not exist to be reached for."""
    sweep = _sweep(tmp_path)

    res = sweep.run("sweep", "start", "--group", _GROUP, cwd=sweep.outside)

    assert res.returncode != 0
    assert "unrecognized arguments: --group" in res.stderr
    sweep.assert_nothing_created()


# ---------------------------------------------------------------------------
# Precondition 3 — the vault election, and its three floor shapes
# ---------------------------------------------------------------------------


def test_start_refuses_an_unbound_floor_election(tmp_path):
    sweep = _sweep(tmp_path)
    sweep.set_resolution(vault=None, scope="default", source={})

    res = sweep.start()

    assert res.returncode != 0
    assert _GROUP in res.stderr
    assert "lore_scopes" in res.stderr
    sweep.assert_nothing_created()


def test_start_refuses_an_allowlist_fall_through_naming_the_binding(tmp_path):
    sweep = _sweep(tmp_path)
    sweep.set_resolution(
        vault=None,
        scope="default",
        source={"team": "narrow-vault"},
        skipped="narrow-vault",
        skipped_reason="kind not in allowlist",
    )

    res = sweep.start()

    assert res.returncode != 0
    assert "team:narrow-vault" in res.stderr
    assert "kind not in allowlist" in res.stderr
    sweep.assert_nothing_created()


def test_start_refuses_a_dangling_binding_naming_the_binding(tmp_path):
    sweep = _sweep(tmp_path)
    sweep.set_resolution(
        vault=None,
        scope="default",
        source={"team": "ghost-vault"},
        unmatched_scopes=["team:ghost-vault"],
    )

    res = sweep.start()

    assert res.returncode != 0
    assert "team:ghost-vault" in res.stderr
    sweep.assert_nothing_created()


# ---------------------------------------------------------------------------
# start — the machine contract
# ---------------------------------------------------------------------------


def test_start_emits_exactly_the_contracted_json_keys(tmp_path):
    sweep = _sweep(tmp_path)

    res = sweep.start()

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert set(payload) == {
        "group",
        "vault",
        "vault_path",
        "procedure_path",
        "templates_root",
        "report_path",
        "lock_token",
        "queue",
    }
    assert payload["lock_token"], "finish needs a token to prove the lock is this run's"
    assert payload["group"] == _GROUP
    assert payload["vault"] == _VAULT
    assert payload["vault_path"] == "/vaults/testvault"
    assert payload["templates_root"].endswith("/plugins/craft/templates")
    assert [e["name"] for e in payload["queue"]] == ["t1", "t2", "t3"]
    assert all(e["bucket"] == "dispatchable" for e in payload["queue"])


def test_start_names_a_missing_lore_cli_instead_of_tracebacking(tmp_path):
    """`lore` absent from PATH is a refusal, not a stack trace.

    Runtime startup checks are the sweep's only dependency guard — nothing
    enforces plugin dependencies at install time — so the one dependency every
    check itself shells out to must fail with the same one-line, remediable
    message shape as the checks it powers.
    """
    sweep = _sweep(tmp_path)

    res = sweep.run_without_lore("sweep", "start", "--holder-pid", str(os.getpid()))

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")
    assert "Traceback" not in res.stderr
    assert "lore CLI not found on PATH" in res.stderr
    assert "install lore or adjust PATH" in res.stderr
    sweep.assert_nothing_created()


def test_start_echoes_the_report_path_on_stderr(tmp_path):
    sweep = _sweep(tmp_path)

    res = sweep.start()

    assert res.returncode == 0, res.stderr
    report_path = json.loads(res.stdout)["report_path"]
    assert report_path in res.stderr
    assert Path(report_path).exists()


def test_start_holds_the_lock_and_seeds_the_report(tmp_path):
    sweep = _sweep(tmp_path)

    res = sweep.start()

    assert res.returncode == 0, res.stderr
    assert sweep.lock_file.exists()
    payload = json.loads(sweep.lock_file.read_text())
    assert payload["group"] == _GROUP
    report = Path(json.loads(res.stdout)["report_path"]).read_text()
    assert "**Queue size:** 3 tasks derived" in report


def test_start_records_the_holder_pid_not_the_starting_subprocess(tmp_path):
    """The `ranger sweep start` process exits immediately; if the lock named
    it, every live sweep would read as stale and invite its own removal."""
    sweep = _sweep(tmp_path)

    res = sweep.start()

    assert res.returncode == 0, res.stderr
    assert json.loads(sweep.lock_file.read_text())["pid"] == os.getpid()


def test_start_defaults_the_holder_to_its_parent_process(tmp_path):
    sweep = _sweep(tmp_path)

    res = sweep.run("sweep", "start")

    assert res.returncode == 0, res.stderr
    # This test process spawned the CLI directly, so its parent is us.
    assert json.loads(sweep.lock_file.read_text())["pid"] == os.getpid()


def test_a_second_start_during_a_live_sweep_refuses_as_already_running(tmp_path):
    """The defining contention case: the first sweep's `start` subprocess is
    long gone, but its holder is alive — that must read as ALREADY RUNNING,
    never as stale, or an operator is invited to clear a live sweep's lock."""
    sweep = _sweep(tmp_path)
    first = sweep.start()
    assert first.returncode == 0, first.stderr
    reports_before = sweep.reports()

    second = sweep.start()

    assert second.returncode != 0
    assert f"a sweep is already running for group {_GROUP!r}" in second.stderr
    assert f"pid {os.getpid()}" in second.stderr
    assert "stale" not in second.stderr
    assert "rm " not in second.stderr, "a live sweep's lock must never be offered for removal"
    assert sweep.reports() == reports_before, "a refused start must not create a report"


def test_a_second_start_after_the_holder_died_reports_a_stale_lock(tmp_path):
    sweep = _sweep(tmp_path)
    first = sweep.start(holder_pid=_dead_pid())
    assert first.returncode == 0, first.stderr
    before = sweep.lock_file.read_text()
    reports_before = sweep.reports()

    second = sweep.start()

    assert second.returncode != 0
    assert "stale lock" in second.stderr
    assert f"rm {sweep.lock_file}" in second.stderr
    assert sweep.lock_file.read_text() == before, "a stale lock is reported, never auto-reaped"
    assert sweep.reports() == reports_before


def test_finish_with_a_token_from_another_run_refuses_and_leaves_the_lock(tmp_path):
    sweep = _sweep(tmp_path)
    res = sweep.start()
    assert res.returncode == 0, res.stderr
    report = json.loads(res.stdout)["report_path"]
    before = sweep.lock_file.read_text()

    finished = sweep.run("sweep", "finish", "--report", report, "--vault", _VAULT,
                         "--token", "0" * 32)

    assert finished.returncode != 0
    assert sweep.lock_file.exists()
    assert sweep.lock_file.read_text() == before


# ---------------------------------------------------------------------------
# derive
# ---------------------------------------------------------------------------


def test_derive_prints_the_current_classification(tmp_path):
    sweep = _sweep(tmp_path)
    sweep.set_tasks([_task("t1"), _task("t2", status="blocked")])
    sweep.set_bodies({"t2": _QUESTION_BODY})

    res = sweep.run("sweep", "derive", "--json")

    assert res.returncode == 0, res.stderr
    entries = json.loads(res.stdout)
    assert [(e["name"], e["bucket"]) for e in entries] == [
        ("t1", "dispatchable"),
        ("t2", "blocked-still-waiting"),
    ]


def test_derive_does_not_touch_the_lock_or_the_report(tmp_path):
    sweep = _sweep(tmp_path)

    res = sweep.run("sweep", "derive")

    assert res.returncode == 0, res.stderr
    sweep.assert_nothing_created()


# ---------------------------------------------------------------------------
# record — outcome-token bucketing
# ---------------------------------------------------------------------------


def _start_sweep(sweep: Sweep, **kwargs) -> tuple[str, str]:
    """Start a sweep, returning ``(report_path, lock_token)``."""
    res = sweep.start(**kwargs)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    return payload["report_path"], payload["lock_token"]


def _start_and_report(sweep: Sweep, **kwargs) -> str:
    """Start a sweep whose token the caller won't need (it never finishes)."""
    return _start_sweep(sweep, **kwargs)[0]


def test_record_buckets_promoted_routed_and_skipped(tmp_path):
    sweep = _sweep(tmp_path)
    report = _start_and_report(sweep)

    assert sweep.run("sweep", "record", "--report", report, "--task", "task/t1",
                     "--outcome", "PROMOTED").returncode == 0
    assert sweep.run("sweep", "record", "--report", report, "--task", "task/t2",
                     "--outcome", "ROUTED /craft:plan").returncode == 0
    assert sweep.run("sweep", "record", "--report", report, "--task", "task/t3",
                     "--outcome", "SKIPPED not a standalone task").returncode == 0

    text = Path(report).read_text()
    assert "## Promoted\n\n- `task/t1`\n" in text
    assert "- `task/t2` — routed to /craft:plan\n" in text
    assert "- `task/t3` — not a standalone task\n" in text


def test_record_escalated_carries_the_question_and_answer_command(tmp_path):
    sweep = _sweep(tmp_path)
    sweep.set_bodies({"t1": _QUESTION_BODY})
    report = _start_and_report(sweep)

    res = sweep.run("sweep", "record", "--report", report, "--task", "task/t1",
                    "--outcome", "ESCALATED")

    assert res.returncode == 0, res.stderr
    text = Path(report).read_text()
    assert "Which queue should this drain?" in text
    assert "lore record update task/t1 --vault testvault --diff" in text


def test_record_scrubs_credentials_out_of_the_question(tmp_path):
    sweep = _sweep(tmp_path)
    sweep.set_bodies(
        {"t1": _QUESTION_BODY.replace("Which queue should this drain?", "Use AWS_SECRET_KEY=hunter2?")}
    )
    report = _start_and_report(sweep)

    sweep.run("sweep", "record", "--report", report, "--task", "task/t1", "--outcome", "ESCALATED")

    text = Path(report).read_text()
    assert "hunter2" not in text
    assert "[REDACTED]" in text


def test_record_with_an_unparseable_outcome_buckets_failed_and_exits_zero(tmp_path):
    sweep = _sweep(tmp_path)
    report = _start_and_report(sweep)

    res = sweep.run("sweep", "record", "--report", report, "--task", "task/t1",
                    "--outcome", "I had a bit of trouble, sorry")

    assert res.returncode == 0, res.stderr
    text = Path(report).read_text()
    assert "## Failed\n\n- `task/t1` — I had a bit of trouble, sorry" in text
    assert "retried automatically next sweep" in text


def test_record_truncates_a_multi_line_failure_to_one_line(tmp_path):
    sweep = _sweep(tmp_path)
    report = _start_and_report(sweep)

    sweep.run("sweep", "record", "--report", report, "--task", "task/t1",
              "--outcome", "boom\nstack frame one\nstack frame two")

    lines = [ln for ln in Path(report).read_text().splitlines() if ln.startswith("- `task/t1`")]
    assert len(lines) == 1
    assert "stack frame two" not in Path(report).read_text()


def test_record_buckets_a_never_dispatched_blocked_task_as_still_waiting(tmp_path):
    sweep = _sweep(tmp_path)
    sweep.set_tasks([_task("t1", status="blocked")])
    sweep.set_bodies({"t1": _QUESTION_BODY})
    report = _start_and_report(sweep)

    res = sweep.run("sweep", "record", "--report", report, "--task", "task/t1",
                    "--queue-bucket", "blocked-still-waiting")

    assert res.returncode == 0, res.stderr
    text = Path(report).read_text()
    blocked_section = text.split("## Blocked — still waiting")[1].split("## Skipped")[0]
    assert "Which queue should this drain?" in blocked_section


def test_record_buckets_a_dispatched_blocked_answered_task_without_a_question(tmp_path):
    sweep = _sweep(tmp_path)
    sweep.set_tasks([_task("t1", status="blocked")])
    sweep.set_bodies({"t1": _ANSWERED_BODY})
    report = _start_and_report(sweep)

    res = sweep.run("sweep", "record", "--report", report, "--task", "task/t1",
                    "--queue-bucket", "blocked-answered", "--outcome", "PROMOTED")

    assert res.returncode == 0, res.stderr
    text = Path(report).read_text()
    section = text.split("## Blocked — answered")[1].split("## Blocked — still waiting")[0]
    assert "- `task/t1`" in section
    assert "Which queue should this drain?" not in section


def test_record_reports_an_unrecognized_answer_as_a_near_miss(tmp_path):
    sweep = _sweep(tmp_path)
    sweep.set_tasks([_task("t1")])
    sweep.set_bodies({"t1": _QUESTION_BODY + "Answer: I meant to bold this.\n"})
    report = _start_and_report(sweep)

    sweep.run("sweep", "record", "--report", report, "--task", "task/t1",
              "--queue-bucket", "escalated-awaiting-operator")

    assert "answer detected but not recognized" in Path(report).read_text()


def test_record_of_a_body_without_a_parseable_question_still_exits_zero(tmp_path):
    """A record whose section carries no `**Question:**` must not end the sweep.

    An outcome that doesn't parse buckets `failed` and exits 0; a record body
    that doesn't parse is the same case one layer down. Raising here would
    abandon every task still queued behind this one.
    """
    sweep = _sweep(tmp_path)
    sweep.set_tasks([_task("t1")])
    sweep.set_bodies({"t1": "# A task\n\n## Refine — unresolved\n\nProse, but no question.\n"})
    report = _start_and_report(sweep)

    res = sweep.run("sweep", "record", "--report", report, "--task", "task/t1",
                    "--queue-bucket", "escalated-awaiting-operator")

    assert res.returncode == 0, res.stderr
    text = Path(report).read_text()
    assert "- `task/t1` — question could not be extracted — open the record" in text


def test_record_escalated_out_of_the_blocked_answered_bucket_carries_the_question(tmp_path):
    """A mid-sweep escalation is an escalation, whatever the task's history.

    The dispatched ritual just wrote a fresh question into a task that entered
    the queue `blocked-answered`. Reporting it as a bare id under "Blocked —
    answered" strands the operator: the new question is not in the report, and
    neither is the command that answers it.
    """
    sweep = _sweep(tmp_path)
    sweep.set_tasks([_task("t1", status="blocked")])
    sweep.set_bodies({"t1": _QUESTION_BODY})
    report = _start_and_report(sweep)

    res = sweep.run("sweep", "record", "--report", report, "--task", "task/t1",
                    "--queue-bucket", "blocked-answered", "--outcome", "ESCALATED")

    assert res.returncode == 0, res.stderr
    text = Path(report).read_text()
    escalated = text.split("## Escalated — awaiting operator")[1].split("## Routed")[0]
    assert "Which queue should this drain?" in escalated
    assert "lore record update task/t1 --vault testvault --diff" in escalated
    answered = text.split("## Blocked — answered")[1].split("## Blocked — still waiting")[0]
    assert "task/t1" not in answered


@pytest.mark.parametrize(
    "outcome,heading,line",
    [
        ("FAILED dispatch timed out after 10 minutes", "## Failed", "dispatch timed out"),
        ("SKIPPED not a standalone task", "## Skipped", "not a standalone task"),
    ],
    ids=["failed", "skipped"],
)
def test_record_failure_tokens_outrank_the_blocked_answered_bucket(
    tmp_path, outcome, heading, line
):
    """A `FAILED`/`SKIPPED` return out of `blocked-answered` keeps its reason.

    The loop synthesizes `FAILED <reason>` for a dispatch that times out, and
    that dispatch is just as likely to have been of a `blocked-answered` task
    as of any other. Bucketing it by the task's history instead of its outcome
    renders a bare id under "Blocked — answered" and drops the reason on the
    floor — the failure never reaches the bucket an operator reads for
    failures, and the report claims the task was handled.
    """
    sweep = _sweep(tmp_path)
    sweep.set_tasks([_task("t1", status="blocked")])
    sweep.set_bodies({"t1": _ANSWERED_BODY})
    report = _start_and_report(sweep)

    res = sweep.run("sweep", "record", "--report", report, "--task", "task/t1",
                    "--queue-bucket", "blocked-answered", "--outcome", outcome)

    assert res.returncode == 0, res.stderr
    text = Path(report).read_text()
    section = text.split(heading)[1]
    assert f"- `task/t1` — {line}" in section
    answered = text.split("## Blocked — answered")[1].split("## Blocked — still waiting")[0]
    assert "task/t1" not in answered


def test_record_degrades_when_the_record_left_the_elected_vault(tmp_path):
    """A record that vanished between derivation and recording is never fatal.

    `record` reads the body to lift the question; a record deleted, renamed, or
    moved out of the elected vault mid-sweep makes that read fail. Exiting
    nonzero there loses the task's report line *and* stops a sweep that still
    has tasks to drain — the same never-fatal rule the malformed-question path
    already follows.
    """
    sweep = _sweep(tmp_path)
    sweep.set_tasks([_task("t1"), _task("t2")])
    sweep.set_bodies({"t1": _QUESTION_BODY, "t2": _QUESTION_BODY})
    report = _start_and_report(sweep)
    sweep.set_unreadable(["t1"])

    res = sweep.run("sweep", "record", "--report", report, "--task", "task/t1",
                    "--queue-bucket", "escalated-awaiting-operator")

    assert res.returncode == 0, res.stderr
    text = Path(report).read_text()
    escalated = text.split("## Escalated — awaiting operator")[1].split("## Routed")[0]
    assert "- `task/t1` — record could not be read from the elected vault" in escalated
    assert "lore record update task/t1" not in escalated

    # The sweep continues: the next task records normally.
    nxt = sweep.run("sweep", "record", "--report", report, "--task", "task/t2",
                    "--queue-bucket", "escalated-awaiting-operator")
    assert nxt.returncode == 0, nxt.stderr
    assert "Which queue should this drain?" in Path(report).read_text()


def test_record_degrades_for_a_still_waiting_record_that_left_the_vault(tmp_path):
    sweep = _sweep(tmp_path)
    sweep.set_tasks([_task("t1", status="blocked")])
    sweep.set_bodies({"t1": _QUESTION_BODY})
    report = _start_and_report(sweep)
    sweep.set_unreadable(["t1"])

    res = sweep.run("sweep", "record", "--report", report, "--task", "task/t1",
                    "--queue-bucket", "blocked-still-waiting")

    assert res.returncode == 0, res.stderr
    waiting = Path(report).read_text().split("## Blocked — still waiting")[1].split("## Skipped")[0]
    assert "- `task/t1` — record could not be read from the elected vault" in waiting


@pytest.mark.parametrize(
    "task",
    ["task/t1; rm -rf /", "task/$(whoami)", "t1 && echo pwned", "task/`id`", "task/../escape"],
)
def test_record_refuses_a_task_id_carrying_shell_metacharacters(tmp_path, task):
    """The id is embedded verbatim into a copy-pasteable shell command.

    The report tells the operator to run `lore record update <id> --diff …`;
    an id carrying shell metacharacters turns that instruction into arbitrary
    command execution in the operator's own shell.
    """
    sweep = _sweep(tmp_path)
    report = _start_and_report(sweep)

    res = sweep.run("sweep", "record", "--report", report, "--task", task, "--outcome", "PROMOTED")

    assert res.returncode != 0
    assert res.stderr.startswith("ranger: ")
    assert task not in Path(report).read_text()


def test_record_accepts_ordinary_record_id_shapes(tmp_path):
    sweep = _sweep(tmp_path)
    report = _start_and_report(sweep)

    for task in ("task/a-b_c.d", "plain-name", "task/nested/name"):
        res = sweep.run("sweep", "record", "--report", report, "--task", task,
                        "--outcome", "PROMOTED")
        assert res.returncode == 0, res.stderr


def test_record_requires_an_outcome_for_a_dispatched_task(tmp_path):
    sweep = _sweep(tmp_path)
    report = _start_and_report(sweep)

    res = sweep.run("sweep", "record", "--report", report, "--task", "task/t1")

    assert res.returncode != 0
    assert "--outcome" in res.stderr


# ---------------------------------------------------------------------------
# The full sweep
# ---------------------------------------------------------------------------


def _uninterrupted_sweep(sweep: Sweep) -> Path:
    report, token = _start_sweep(sweep)
    assert sweep.lock_file.exists(), "the lock must be held for the sweep's duration"
    for name, outcome in (("t1", "PROMOTED"), ("t2", "ESCALATED"), ("t3", "ROUTED /craft:plan")):
        res = sweep.run("sweep", "record", "--report", report, "--task", f"task/{name}",
                        "--outcome", outcome)
        assert res.returncode == 0, res.stderr
    res = sweep.run("sweep", "finish", "--report", report, "--vault", _VAULT, "--token", token)
    assert res.returncode == 0, res.stderr
    return Path(report)


def test_full_sweep_holds_then_releases_the_lock_and_completes_the_report(tmp_path):
    sweep = _sweep(tmp_path)
    sweep.set_bodies({"t2": _QUESTION_BODY})

    report = _uninterrupted_sweep(sweep)

    assert not sweep.lock_file.exists(), "finish must release the lock"
    text = report.read_text()
    for heading in (
        "## Promoted",
        "## Escalated — awaiting operator",
        "## Routed",
        "## Blocked — answered",
        "## Blocked — still waiting",
        "## Skipped",
        "## Failed",
    ):
        assert heading in text
    assert "- `task/t1`" in text
    assert f"Report written to `{report}`." in text


def test_a_second_sweep_can_start_once_the_first_has_finished(tmp_path):
    sweep = _sweep(tmp_path)
    sweep.set_bodies({"t2": _QUESTION_BODY})
    _uninterrupted_sweep(sweep)

    res = sweep.start()

    assert res.returncode == 0, res.stderr


def _normalized(report: Path) -> str:
    """Report text with its self-naming footer path elided, so two sweeps'
    reports compare on content rather than on their timestamped filenames."""
    return report.read_text().replace(str(report), "<REPORT>")


def test_crash_recovery_rerun_reaches_the_same_net_state(tmp_path):
    baseline_env = _sweep(tmp_path / "baseline")
    baseline_env.set_bodies({"t2": _QUESTION_BODY})
    baseline = _normalized(_uninterrupted_sweep(baseline_env))

    sweep = _sweep(tmp_path / "crashed")
    sweep.set_bodies({"t2": _QUESTION_BODY})

    # A sweep killed mid-task: the first task was recorded, then the whole
    # sweep — coordinator included — died before `finish`. The lock is still
    # held by a pid that no longer exists, and the report is partial.
    partial = Path(_start_and_report(sweep, holder_pid=_dead_pid()))
    sweep.run("sweep", "record", "--report", str(partial), "--task", "task/t1",
              "--outcome", "PROMOTED")
    assert sweep.lock_file.exists()
    assert "- `task/t1`" in partial.read_text()
    assert "Report written to" not in partial.read_text()

    # Re-running refuses, reporting the lock as stale with a removal command.
    refused = sweep.start()
    assert refused.returncode != 0
    assert "stale lock" in refused.stderr
    assert len(sweep.reports()) == 1, "a refused start must not create a second report"

    # Clear the stale lock exactly as the refusal instructs, then re-run.
    match = re.search(r"rm (\S+)", refused.stderr)
    assert match, refused.stderr
    Path(match.group(1)).unlink()

    rerun_path, rerun_token = _start_sweep(sweep)
    rerun = Path(rerun_path)
    for name, outcome in (("t1", "PROMOTED"), ("t2", "ESCALATED"), ("t3", "ROUTED /craft:plan")):
        assert sweep.run("sweep", "record", "--report", str(rerun), "--task", f"task/{name}",
                         "--outcome", outcome).returncode == 0
    # The task that was already recorded before the crash is retried; the
    # report's dedupe keeps the net state identical to an uninterrupted sweep.
    assert sweep.run("sweep", "record", "--report", str(rerun), "--task", "task/t1",
                     "--outcome", "PROMOTED").returncode == 0
    assert sweep.run("sweep", "finish", "--report", str(rerun), "--vault", _VAULT,
                     "--token", rerun_token).returncode == 0

    assert _normalized(rerun) == baseline
    assert not sweep.lock_file.exists()
    assert partial.exists(), "the crashed sweep's partial report stays on disk"
