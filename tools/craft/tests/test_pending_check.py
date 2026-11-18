"""pending_check.py: closing a slice whose manual check waits on the operator.

The writer's contract is driven through `main` with the in-process lore runner
(`_lore_runner.LoreRunner`) over real records, so revisions and `--base` are real lore's,
and every body it produces is run through the real gate scripts as subprocesses.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location("pending_check", _SCRIPTS / "pending_check.py")
pc = importlib.util.module_from_spec(_spec)
sys.modules["pending_check"] = pc
_spec.loader.exec_module(pc)

from _lore_runner import LoreRunner, seam_env  # noqa: E402
import waiting  # noqa: E402
import waiting_gate  # noqa: E402
from gate_plumbing import Refusal  # noqa: E402

TODAY = "2026-11-08"
SPEC = "the-spec"
PARENT = "the-parent"
VAULT = "v"
SECRET = "sk_live_" + "a1b2c3d4e5f6"

SPEC_BODY = """# The spec

Prose.

## Acceptance Criteria

- **AC1.** The first thing works.
- **AC2.** The banner shows the count.
- **AC5.** The report opens in the app.

## Slices

- **First** — a claim. (`task/first`, closed 2026-01-01, covers AC1)
"""

PARENT_BODY = """**Value claim:** you can close with a check waiting.

**Covers:** AC2, AC5

## Criterion observations

- **AC2** — manual-check — looked at the banner in the app
- **AC5** — manual-check — opened the report by hand

## Operator attestations

- **AC5** — the operator opened the report and it was fine
"""

PARENT_UNATTESTED = PARENT_BODY.split("\n## Operator attestations")[0].rstrip("\n") + "\n"

PARENT_ONE_COVER = PARENT_BODY.replace("**Covers:** AC2, AC5", "**Covers:** AC2").replace(
    "- **AC5** — manual-check — opened the report by hand\n", ""
).replace("- **AC5** — the operator opened the report and it was fine\n", "")


def entry(criterion="AC2", check="the banner shows 3", how="open the app", changed=True):
    return {"criterion": criterion, "check": check, "how": how, "changed_behaviour": changed}


def waiting_text(criterion="AC2", status="waiting", parent=PARENT, check="the banner shows 3"):
    lines = [
        f"- **{criterion}.** pending check — task/{parent}",
        f"  - check: {check}",
        "  - how: open the app",
        "  - changed behaviour: yes",
        "  - 2026-11-01 waiting: closed with this check not yet carried out",
    ]
    if status != "waiting":
        lines.append(f"  - 2026-11-02 {status}: the operator's words")
    return "\n".join(lines) + "\n"


def spec_with(*entries: str, body=SPEC_BODY) -> str:
    return body.rstrip("\n") + "\n\n## Waiting\n\n" + "\n".join(entries)


class Closing:
    """A real spec and slice parent, created through the in-process lore runner.

    Every answer the script reads comes from real lore; `fail_update` and the
    runner's other injections make a write fail the way a failing `lore` would.
    """

    def __init__(self, root, spec_body=SPEC_BODY, parent_body=PARENT_BODY, *, parent_status="in-progress",
                 parent_labels=None, parent_related=None, layer="personal", with_spec=True, children=()):
        # lore refuses a shared default-scope vault, so a shared layer needs a second vault
        self.runner = LoreRunner(root, vaults=(VAULT,) if layer == "personal" else ("home", VAULT))
        self._quiet = False
        if with_spec:
            self._create("spec", "The spec", "ready", spec_body, [])
        labels = {"craft/slice-parent": ""} if parent_labels is None else parent_labels
        related = {"spec": [SPEC]} if parent_related is None else parent_related
        extras = [f"--label={k}={v}" for k, v in labels.items()]
        extras += [f"--related={kind}={name}" for kind, names in related.items() for name in names]
        self._create("task", "The parent", parent_status, parent_body, extras)
        for name, status in children:
            self._create("task", name, "ready" if status == "superseded" else status, "A child.",
                         [f"--parent={PARENT}"])
        for name, status in children:
            if status == "superseded":  # lore only records a supersession against a successor
                successor = next(n for n, st in children if st != "superseded")
                r = self.runner(["record", "supersede", f"task/{name}", "--by", f"task/{successor}",
                                 "--vault", VAULT])
                assert r.returncode == 0, r.stderr
        if layer == "shared":
            config = self.runner.config_home / "lore" / "config.json"
            doc = json.loads(config.read_text(encoding="utf-8"))
            for vault in doc["vaults"]:
                vault["shared"] = vault["name"] == VAULT
            config.write_text(json.dumps(doc), encoding="utf-8")
        self.runner.calls.clear()

    def _create(self, kind, title, status, body, extras):
        return self._create_in(VAULT, kind, title, status, body, extras)

    def _create_in(self, vault, kind, title, status, body, extras=()):
        r = self.runner(["record", "create", "--kind", kind, "--title", title, "--status", status,
                         "--vault", vault, *extras], body)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    @property
    def calls(self):
        return self.runner.calls

    def updates(self):
        return [(a, s) for a, s in self.calls if a[:2] == ["record", "update"]]

    def update_ids(self):
        return [a[2] for a, _ in self.updates()]

    def show(self, rid):
        """The real `record show --json` of `rid`, without recording the call."""
        r = self.runner(["record", "show", "--json", "--vault", VAULT, rid])
        self.runner.calls.pop()
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def body(self, rid):
        return self.show(rid)["body"]

    def revision(self, rid):
        return self.show(rid)["revision"]

    def fail_update(self, rid):
        """Make the next `record update` of `rid` report failure."""
        self.runner.inject(
            lambda argv, _stdin: argv[:3] == ["record", "update", rid] and not self._quiet,
            returncode=1, stderr="boom", times=1,
        )

    def reject_base(self):
        """Answer `record update --base` the way a lore that predates `--base` does
        (hostile input: this lore accepts it)."""
        self.runner.inject(
            lambda argv, _stdin: argv[:2] == ["record", "update"] and "--base" in argv,
            returncode=2, stderr="lore: error: unrecognized arguments: --base abc",
        )

    def edited_by_another_writer_before_update(self, rid):
        """Another writer appends a note to `rid` just before the first `record update` of it
        is carried out, so its revision has moved by the time that write is answered."""
        def edit():
            self._quiet = True
            r = self.runner(["record", "update", rid, "--vault", VAULT],
                            self.body(rid) + "\nA note from another writer.\n")
            self._quiet = False
            assert r.returncode == 0, r.stderr

        self.before_update(rid, edit)

    def before_update(self, rid, action):
        """Run `action()` (real writes through the runner, unrecorded) once, just before the
        first `record update` of `rid` is carried out."""
        fired = []

        def watch(argv, _stdin):
            if argv[:3] == ["record", "update", rid] and not fired:
                fired.append(True)
                action()
                self.runner.calls.pop()
            return False

        self.runner.inject(watch)

    def update_then_time_out(self, rid):
        """Carry out the next `record update` of `rid`, then raise a timeout as a hung
        `lore` that had already written would."""
        busy = []

        def land(argv, stdin):
            if busy or argv[:3] != ["record", "update", rid]:
                return False
            busy.append(True)
            try:
                self.runner(argv, stdin)
                self.runner.calls.pop()
            finally:
                busy.clear()
            return True

        self.runner.inject(land, raises=subprocess.TimeoutExpired(["lore"], 1), times=1)


@pytest.fixture
def closing(tmp_path):
    made = []

    def make(*args, **kwargs):
        made.append(None)
        return Closing(tmp_path / f"lore{len(made)}", *args, **kwargs)

    return make


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setenv("CRAFT_WAITING_GATE_TODAY", TODAY)


@pytest.fixture
def entries_file(tmp_path):
    def make(entries):
        path = tmp_path / "entries.json"
        path.write_text(json.dumps(entries), encoding="utf-8")
        return path

    return make


def run(fake, entries_path, capsys, *, spec=SPEC, parent=PARENT, vault=VAULT, extra=()):
    argv = [f"--spec={spec}", f"--parent={parent}", "--entries", str(entries_path)]
    if vault is not None:
        argv += [f"--vault={vault}"]
    rc = pc.main(argv + list(extra), runner=fake.runner)
    out = capsys.readouterr()
    return rc, out.out, out.err


def code_of(err: str) -> str:
    lines = [ln for ln in err.splitlines() if "reason-code:" in ln]
    assert lines, err
    return lines[-1].split("reason-code:")[1].strip()


def gate(script, *args, stdin=""):
    return subprocess.run([sys.executable, str(_SCRIPTS / script), *args], input=stdin,
                          capture_output=True, text=True, env=dict(os.environ))


def test_happy_path_writes_spec_first_then_parent_both_with_their_read_revisions(closing, entries_file, capsys):
    fake = closing()
    spec_rev = fake.revision(f"spec/{SPEC}")
    parent_rev = fake.revision(f"task/{PARENT}")

    rc, out, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 0, err
    assert out.splitlines() == ["pending: AC2", "spec: written", "parent: updated"]
    assert fake.update_ids() == [f"spec/{SPEC}", f"task/{PARENT}"]
    (spec_argv, spec_body), (parent_argv, parent_body) = fake.updates()
    assert spec_argv[spec_argv.index("--base") + 1] == spec_rev
    assert parent_argv[parent_argv.index("--base") + 1] == parent_rev
    parsed = waiting.parse_waiting(spec_body)
    assert [(e.criterion_id, e.building_slice, e.status, e.check, e.how, e.changed_behaviour)
            for e in parsed] == [("AC2", PARENT, "waiting", "the banner shows 3", "open the app", True)]
    assert parsed[0].history == ((TODAY, "waiting", "closed with this check not yet carried out"),)
    assert "**Covers:** AC5\n" in parent_body
    assert "**Partially covers:** AC2\n" in parent_body
    assert (f"- **AC2** — pending-check — waiting entry on spec/{SPEC}; "
            "looked at the banner in the app\n") in parent_body
    assert "- **AC5** — manual-check — opened the report by hand\n" in parent_body


def test_a_new_waiting_entry_is_set_off_from_the_entry_before_it_by_a_blank_line(closing, entries_file, capsys):
    fake = closing(spec_body=spec_with(waiting_text("AC5")))

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 0, err
    spec_body = fake.updates()[0][1]
    assert ("closed with this check not yet carried out\n\n- **AC2.** pending check") in spec_body


NO_OBS_SPEC_MANUAL = SPEC_BODY.replace(
    "- **AC2.** The banner shows the count.", "- **AC2.** The banner shows the count. *Verified by: manual check.*"
)
NO_OBS_SPEC_AUTOMATED = SPEC_BODY.replace(
    "- **AC2.** The banner shows the count.",
    "- **AC2.** The banner shows the count. *Verified by: automated assertion.*",
)
NO_OBS_PARENT = """**Value claim:** you can close with a check waiting.

**Covers:** AC2
"""
NO_OBS_PARENT_WITH_SECTION = """**Value claim:** you can close with a check waiting.

**Covers:** AC1, AC2

## Criterion observations

- **AC1** — automated-assertion — tests/test_first.py::test_first

## Other

Prose.
"""
NO_OBSERVATION_EVIDENCE = f"waiting entry on spec/{SPEC}; no observation recorded before close"


@pytest.mark.parametrize("parent_body", [NO_OBS_PARENT, NO_OBS_PARENT_WITH_SECTION], ids=["no-section", "section"])
def test_a_covered_manual_check_with_no_observation_moves_to_partially_covers_with_the_no_observation_evidence(
    parent_body, closing, entries_file, capsys
):
    fake = closing(spec_body=NO_OBS_SPEC_MANUAL, parent_body=parent_body)

    rc, out, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 0, err
    assert out.splitlines() == ["pending: AC2", "spec: written", "parent: updated"]
    assert fake.update_ids() == [f"spec/{SPEC}", f"task/{PARENT}"]
    body = fake.body(f"task/{PARENT}")
    assert "**Partially covers:** AC2\n" in body
    assert f"- **AC2** — pending-check — {NO_OBSERVATION_EVIDENCE}\n" in body
    assert body.count("pending-check") == 1
    if "AC1" in parent_body:
        assert "**Covers:** AC1\n" in body
        assert body.index("- **AC2** — pending-check") < body.index("## Other")
    g = gate("observation_gate.py", stdin=body)
    assert g.returncode == 0, g.stderr
    assert "AC2: pending-check" in g.stdout
    assert pc.main(["verify", f"--spec={SPEC}", f"--parent={PARENT}", f"--vault={VAULT}"], runner=fake.runner) == 0
    assert capsys.readouterr().out == "verified: AC2\n"


def test_a_covered_criterion_with_no_observation_and_a_non_manual_trailer_is_refused_with_no_writes(
    closing, entries_file, capsys
):
    fake = closing(spec_body=NO_OBS_SPEC_AUTOMATED, parent_body=NO_OBS_PARENT)

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 1 and code_of(err) == "not-an-unconfirmed-manual-check"
    assert fake.updates() == []


def test_a_rerun_after_closing_a_criterion_with_no_observation_writes_nothing(closing, entries_file, capsys):
    fake = closing(spec_body=NO_OBS_SPEC_MANUAL, parent_body=NO_OBS_PARENT)
    path = entries_file([entry("AC2")])
    assert run(fake, path, capsys)[0] == 0
    before = len(fake.updates())

    rc, out, err = run(fake, path, capsys)

    assert rc == 0, err
    assert out.splitlines() == ["pending: AC2", "spec: already-present", "parent: already-updated"]
    assert len(fake.updates()) == before


def test_a_lore_that_rejects_base_on_record_update_is_lore_too_old_not_spec_write_failed(closing, entries_file, capsys):
    fake = closing(spec_body=NO_OBS_SPEC_MANUAL, parent_body=NO_OBS_PARENT)
    fake.reject_base()
    parent_before = fake.body(f"task/{PARENT}")

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 2 and code_of(err) == "lore-too-old"
    assert fake.body(f"task/{PARENT}") == parent_before


def test_a_failed_spec_write_that_is_not_a_base_rejection_stays_spec_write_failed(closing, entries_file, capsys):
    fake = closing()
    fake.fail_update(f"spec/{SPEC}")

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 2 and code_of(err) == "spec-write-failed"


def test_produced_bodies_pass_the_real_gates(closing, entries_file, capsys, tmp_path):
    fake = closing()
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"body": SPEC_BODY, "revision": "a" * 64, "layer": "personal"}))

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)
    assert rc == 0, err
    spec_body, parent_body = fake.body(f"spec/{SPEC}"), fake.body(f"task/{PARENT}")

    g = gate("waiting_gate.py", "--prior", str(prior), stdin=spec_body)
    assert g.returncode == 0, g.stderr
    g = gate("observation_gate.py", stdin=parent_body)
    assert g.returncode == 0, g.stderr
    assert "AC2: pending-check" in g.stdout
    g = gate("covers_gate.py", "--covers", "AC5", "--partial-covers", "AC2", stdin=spec_body)
    assert g.returncode == 0, g.stderr


def test_moving_every_covered_criterion_drops_the_covers_line_and_the_real_observation_gate_passes(
    closing, entries_file, capsys
):
    fake = closing(parent_body=PARENT_ONE_COVER)

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 0, err
    parent_body = fake.body(f"task/{PARENT}")
    assert "**Covers:**" not in parent_body
    assert "**Partially covers:** AC2\n" in parent_body
    g = gate("observation_gate.py", stdin=parent_body)
    assert g.returncode == 0, g.stderr
    assert g.stdout.splitlines()[0] == "covers: none"


def test_existing_partially_covers_line_is_extended_not_duplicated(closing, entries_file, capsys):
    parent = PARENT_BODY.replace("**Covers:** AC2, AC5\n", "**Covers:** AC2, AC5\n\n**Partially covers:** AC1\n")
    fake = closing(parent_body=parent)

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 0, err
    body = fake.body(f"task/{PARENT}")
    assert body.count("**Partially covers:**") == 1
    assert "**Partially covers:** AC1, AC2\n" in body
    assert gate("observation_gate.py", stdin=body).returncode == 0


def test_covers_line_is_dropped_when_it_empties_and_a_partially_covers_line_already_exists(
    closing, entries_file, capsys
):
    parent = PARENT_ONE_COVER.replace("**Covers:** AC2\n", "**Covers:** AC2\n\n**Partially covers:** AC1\n")
    fake = closing(parent_body=parent)

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 0, err
    body = fake.body(f"task/{PARENT}")
    assert "**Covers:**" not in body
    assert "**Partially covers:** AC1, AC2\n" in body
    assert gate("observation_gate.py", stdin=body).returncode == 0


def test_two_criteria_one_already_on_the_list_appends_one_entry_and_no_duplicate(closing, entries_file, capsys):
    fake = closing(spec_body=spec_with(waiting_text("AC2")), parent_body=PARENT_UNATTESTED)

    rc, out, err = run(fake, entries_file([entry("AC2"), entry("AC5", check="the report opens")]), capsys)

    assert rc == 0, err
    assert out.splitlines() == ["pending: AC2", "pending: AC5", "spec: written", "parent: updated"]
    parsed = waiting.parse_waiting(fake.body(f"spec/{SPEC}"))
    assert [(e.criterion_id, e.building_slice) for e in parsed] == [("AC2", PARENT), ("AC5", PARENT)]
    body = fake.body(f"task/{PARENT}")
    assert "**Partially covers:** AC2, AC5" in body and "**Covers:**" not in body
    assert gate("observation_gate.py", stdin=body).returncode == 0


def test_entry_is_inserted_inside_an_existing_section_that_is_not_last(closing, entries_file, capsys):
    spec = SPEC_BODY + "\n## Waiting\n\n" + waiting_text("AC9", parent="other") + "\n## Follow-ups\n\n- a note\n"
    fake = closing(spec_body=spec)

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 0, err
    new = fake.body(f"spec/{SPEC}")
    assert [(e.criterion_id, e.building_slice) for e in waiting.parse_waiting(new)] == [
        ("AC9", "other"), ("AC2", PARENT)]
    assert new.index("## Follow-ups") > new.index("**AC2.**")


def test_rerun_after_full_success_writes_nothing(closing, entries_file, capsys):
    fake = closing()
    path = entries_file([entry("AC2")])
    assert run(fake, path, capsys)[0] == 0
    before = len(fake.updates())

    rc, out, err = run(fake, path, capsys)

    assert rc == 0, err
    assert len(fake.updates()) == before
    assert out.splitlines() == ["pending: AC2", "spec: already-present", "parent: already-updated"]


def test_rerun_after_spec_landed_and_parent_failed_skips_the_spec_write_and_writes_the_parent(
    closing, entries_file, capsys
):
    fake = closing()
    fake.fail_update(f"task/{PARENT}")
    path = entries_file([entry("AC2")])
    rc, _, err = run(fake, path, capsys)
    assert rc == 2 and code_of(err) == "parent-write-failed"
    assert "spec entry already landed" in err
    fake.calls.clear()

    rc, out, err = run(fake, path, capsys)

    assert rc == 0, err
    assert fake.update_ids() == [f"task/{PARENT}"]
    assert out.splitlines() == ["pending: AC2", "spec: already-present", "parent: updated"]


def test_gate_refusal_exits_with_the_gates_code_and_never_writes(closing, entries_file, capsys):
    fake = closing()

    rc, out, err = run(fake, entries_file([entry("AC2", check=f"token {SECRET}")]), capsys)

    assert rc == 1
    assert code_of(err) == "credential-shaped-text"
    assert fake.updates() == []
    assert SECRET not in out + err


def test_spec_write_failure_with_unchanged_revision_is_spec_write_failed_and_parent_untouched(
    closing, entries_file, capsys
):
    fake = closing()
    fake.fail_update(f"spec/{SPEC}")

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 2 and code_of(err) == "spec-write-failed"
    assert fake.update_ids() == [f"spec/{SPEC}"]
    assert fake.body(f"task/{PARENT}") == PARENT_BODY


def test_spec_write_failure_with_changed_revision_is_stale_spec_and_parent_untouched(closing, entries_file, capsys):
    fake = closing()
    fake.edited_by_another_writer_before_update(f"spec/{SPEC}")
    fake.fail_update(f"spec/{SPEC}")

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 2 and code_of(err) == "stale-spec"
    assert fake.update_ids() == [f"spec/{SPEC}"]
    assert fake.body(f"task/{PARENT}") == PARENT_BODY


def test_a_failed_spec_write_is_not_landed_by_another_slices_waiting_entry_for_the_same_criterion(
    closing, entries_file, capsys
):
    fake = closing(spec_body=spec_with(waiting_text("AC2", parent="other-parent")))
    fake.fail_update(f"spec/{SPEC}")

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 2 and code_of(err) in ("spec-write-failed", "stale-spec"), err
    assert fake.update_ids() == [f"spec/{SPEC}"]
    assert fake.body(f"task/{PARENT}") == PARENT_BODY


def test_parent_write_failure_with_unchanged_revision_is_parent_write_failed(closing, entries_file, capsys):
    fake = closing()
    fake.fail_update(f"task/{PARENT}")

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 2 and code_of(err) == "parent-write-failed"
    assert "re-run" in err
    assert len(waiting.parse_waiting(fake.body(f"spec/{SPEC}"))) == 1


def test_parent_write_failure_with_changed_revision_is_stale_parent(closing, entries_file, capsys):
    fake = closing()
    fake.edited_by_another_writer_before_update(f"task/{PARENT}")
    fake.fail_update(f"task/{PARENT}")

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 2 and code_of(err) == "stale-parent"
    assert "re-run" in err


def test_a_parent_that_stops_qualifying_after_the_spec_write_is_parent_not_finished_exit_2(
    closing, entries_file, capsys
):
    fake = closing()

    def parent_closed_elsewhere():
        r = fake.runner(["record", "update", f"task/{PARENT}", "--vault", VAULT, "--status", "done"])
        assert r.returncode == 0, r.stderr

    fake.before_update(f"spec/{SPEC}", parent_closed_elsewhere)

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 2 and code_of(err) == "parent-not-finished"
    assert "spec entry already landed" in err and "re-run" in err
    assert fake.update_ids() == [f"spec/{SPEC}"]
    assert len(waiting.parse_waiting(fake.body(f"spec/{SPEC}"))) == 1
    assert fake.body(f"task/{PARENT}") == PARENT_BODY


def test_a_spec_write_that_timed_out_but_landed_continues_to_the_parent(closing, entries_file, capsys):
    fake = closing()
    fake.update_then_time_out(f"spec/{SPEC}")

    rc, out, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 0, err
    assert out.splitlines() == ["pending: AC2", "spec: written", "parent: updated"]
    assert fake.update_ids() == [f"spec/{SPEC}", f"task/{PARENT}"]


@pytest.mark.parametrize(
    "parent_body",
    [
        PARENT_BODY + "\n**Partially covers:** AC1\n**Partially covers:** AC2\n",
        PARENT_BODY.replace("## Criterion observations", "```\n## Criterion observations"),
    ],
    ids=["duplicate-partially-covers", "unterminated-fence"],
)
def test_an_unreadable_parent_is_refused_exit_2_with_no_writes(closing, parent_body, entries_file, capsys):
    fake = closing(parent_body=parent_body)

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 2 and code_of(err) == "unreadable-parent"
    assert fake.updates() == []


def test_parent_revision_is_read_again_after_the_spec_write_and_that_one_is_the_base(
    closing, entries_file, capsys
):
    fake = closing()

    edited_rev = []

    def parent_edited_elsewhere():
        rid = f"task/{PARENT}"
        r = fake.runner(["record", "update", rid, "--vault", VAULT],
                        fake.body(rid) + "\nA note added in the meantime.\n")
        assert r.returncode == 0, r.stderr
        edited_rev.append(fake.revision(rid))

    fake.before_update(f"spec/{SPEC}", parent_edited_elsewhere)

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 0, err
    parent_argv, parent_body = fake.updates()[1]
    assert parent_argv[parent_argv.index("--base") + 1] == edited_rev[0]
    assert "A note added in the meantime." in parent_body
    assert fake.body(f"task/{PARENT}") == parent_body


@pytest.mark.parametrize(
    "parent_body",
    [
        PARENT_BODY.replace("**Covers:** AC2, AC5", "**Covers:** AC5"),
        PARENT_BODY + "- **AC2** — the operator saw it\n",
        PARENT_BODY.replace("AC2** — manual-check", "AC2** — automated-assertion"),
    ],
    ids=["not-on-covers", "already-attested", "other-method"],
)
def test_a_criterion_that_is_not_an_unconfirmed_manual_check_is_refused_with_no_writes(
    closing, parent_body, entries_file, capsys
):
    fake = closing(parent_body=parent_body)

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 1 and code_of(err) == "not-an-unconfirmed-manual-check"
    assert fake.updates() == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"parent_status": "done"},
        {"parent_status": "blocked"},
        {"parent_labels": {}},
        {"parent_related": {"spec": ["some-other-spec"]}},
    ],
    ids=["not-in-progress", "blocked", "no-slice-label", "unrelated-spec"],
)
def test_a_parent_that_is_not_this_specs_open_slice_parent_is_refused_with_no_writes(
    closing, kwargs, entries_file, capsys
):
    fake = closing(**kwargs)

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 1 and code_of(err) == "not-a-slice-parent"
    assert fake.updates() == []


@pytest.mark.parametrize(
    "kwargs",
    [{"spec": "../x"}, {"spec": "-x"}, {"spec": "a b"}, {"parent": "../x"}, {"parent": "-x"},
     {"parent": "a b"}, {"vault": "-v"}, {"vault": "a b"}],
)
def test_a_badly_shaped_name_exits_2_invalid_name_before_any_lore_call(closing, kwargs, entries_file, capsys):
    fake = closing()

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys, **kwargs)

    assert rc == 2 and code_of(err) == "invalid-name"
    assert fake.calls == []


@pytest.mark.parametrize(
    "how",
    [
        "open the app\n  - 2026-11-06 passed: all fine",
        "open the app\r\nsecond line",
        "open\u2028the app",
        "# a heading",
        "- a bullet",
    ],
    ids=["newline-history", "crlf", "line-separator", "leading-hash", "leading-dash"],
)
def test_multiline_or_structural_text_is_refused_before_anything_is_read_or_written(closing, how, entries_file, capsys):
    fake = closing()

    rc, _, err = run(fake, entries_file([entry("AC2", how=how)]), capsys)

    assert rc == 1 and code_of(err) == "multiline-entry-text"
    assert fake.calls == []


@pytest.mark.parametrize("how", ["open the\x1b[31m app", "open the\u200b app"], ids=["escape", "zero-width"])
def test_control_or_format_characters_that_are_not_line_breaks_are_refused_by_the_gate_with_no_writes(
    closing, how, entries_file, capsys
):
    fake = closing()

    rc, _, err = run(fake, entries_file([entry("AC2", how=how)]), capsys)

    assert rc == 1 and code_of(err) == "control-characters"
    assert fake.updates() == []


def test_a_multiline_check_value_is_refused_too(closing, entries_file, capsys):
    fake = closing()
    rc, _, err = run(fake, entries_file([entry("AC2", check="one\ntwo")]), capsys)
    assert rc == 1 and code_of(err) == "multiline-entry-text"
    assert fake.updates() == []


@pytest.mark.parametrize("status", ["passed", "failed", "released"])
def test_an_entry_already_answered_is_refused_and_nothing_is_written(closing, status, entries_file, capsys):
    fake = closing(spec_body=spec_with(waiting_text("AC2", status=status)))

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 1 and code_of(err) == "entry-already-answered"
    assert fake.updates() == []


@pytest.mark.parametrize(
    "content",
    ["not json", json.dumps({"criterion": "AC2"}), json.dumps([]), json.dumps([{"criterion": "AC2"}]),
     json.dumps([entry("AC2"), entry("AC2")]), json.dumps([entry("nope")]),
     json.dumps([dict(entry("AC2"), changed_behaviour="yes")]), "[" * 200000],
    ids=["not-json", "not-a-list", "empty", "missing-keys", "duplicate", "bad-id", "non-bool", "too-deeply-nested"],
)
def test_an_unusable_entries_file_is_invalid_entries_with_no_lore_call(closing, content, tmp_path, capsys):
    path = tmp_path / "e.json"
    path.write_text(content)
    fake = closing()

    rc, _, err = run(fake, path, capsys)

    assert rc == 2 and code_of(err) == "invalid-entries"
    assert fake.calls == []


@pytest.mark.parametrize("field", ["check", "how"])
def test_a_lone_surrogate_in_check_or_how_is_invalid_entries_with_no_lore_call(closing, field, entries_file, capsys):
    fake = closing()
    rc, _, err = run(fake, entries_file([entry("AC2", **{field: "see \ud800 here"})]), capsys)
    assert rc == 2 and code_of(err) == "invalid-entries"
    assert fake.calls == []


def test_the_entries_file_is_never_deleted(closing, entries_file, capsys):
    path = entries_file([entry("AC2")])
    run(closing(), path, capsys)
    assert path.exists()
    path2 = entries_file([entry("AC2", check="x\ny")])
    run(closing(), path2, capsys)
    assert path2.exists()


def test_vault_is_found_through_lore_when_not_given(closing, entries_file, capsys):
    fake = closing()
    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys, vault=None)
    assert rc == 0, err
    assert all("--vault" in a and a[a.index("--vault") + 1] == VAULT
               for a, _ in fake.calls if a[:2] in (["record", "update"],))


def test_a_shared_layer_record_is_refused_because_its_body_is_escaped(closing, entries_file, capsys):
    fake = closing(layer="shared")
    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)
    assert rc == 2 and code_of(err) == "shared-layer-prior"
    assert fake.updates() == []


def test_a_criterion_the_spec_does_not_declare_is_refused_by_the_covers_gate_before_any_write(
    closing, entries_file, capsys
):
    fake = closing(spec_body=SPEC_BODY.replace("- **AC2.** The banner shows the count.\n", ""))

    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)

    assert rc == 1 and code_of(err) == "covers-refused"
    assert fake.updates() == []


def test_the_spec_must_exist(closing, entries_file, capsys):
    fake = closing(with_spec=False)
    rc, _, err = run(fake, entries_file([entry("AC2")]), capsys)
    assert rc == 1 and code_of(err) == "spec-not-found"
    assert fake.updates() == []


def test_waiting_gate_certify_is_public_and_raises_the_gates_refusal():
    waiting_gate.certify(SPEC_BODY, spec_with(waiting_text("AC2")))
    with pytest.raises(Refusal) as exc:
        waiting_gate.certify(SPEC_BODY, spec_with(waiting_text("AC2", check=f"t {SECRET}")))
    assert exc.value.reason_code == "credential-shaped-text"


def pending_parent(extra=""):
    return PARENT_BODY.replace("**Covers:** AC2, AC5", "**Covers:** AC5\n\n**Partially covers:** AC2").replace(
        "AC2** — manual-check — looked", "AC2** — pending-check — waiting entry on spec/s; looked") + extra


def verify(fake, capsys, **kw):
    argv = ["verify", "--spec", kw.get("spec", SPEC), "--parent", kw.get("parent", PARENT), "--vault", VAULT]
    rc = pc.main(argv, runner=fake.runner)
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_verify_refuses_a_pending_check_line_with_no_waiting_entry_and_names_the_criterion(closing, capsys):
    fake = closing(parent_body=pending_parent())
    rc, out, err = verify(fake, capsys)
    assert rc == 1 and code_of(err) == "pending-check-without-entry"
    assert "AC2" in err
    assert fake.updates() == []


def test_verify_passes_when_the_waiting_entry_exists(closing, capsys):
    fake = closing(spec_body=spec_with(waiting_text("AC2")), parent_body=pending_parent())
    rc, out, err = verify(fake, capsys)
    assert rc == 0, err
    assert out.splitlines() == ["verified: AC2"]


def test_verify_refuses_an_entry_that_is_not_waiting_or_keyed_to_another_slice(closing, capsys):
    for spec in (spec_with(waiting_text("AC2", status="passed")),
                 spec_with(waiting_text("AC2", parent="elsewhere"))):
        fake = closing(spec_body=spec, parent_body=pending_parent())
        rc, _, err = verify(fake, capsys)
        assert rc == 1 and code_of(err) == "pending-check-without-entry"


def test_verify_with_no_pending_check_observations_passes(closing, capsys):
    fake = closing()
    rc, out, _ = verify(fake, capsys)
    assert rc == 0 and out.splitlines() == ["verified: none"]


def test_describe_prints_each_criterion_cleaned_of_control_characters(closing, capsys):
    spec = SPEC_BODY.replace("The banner shows the count.", "The \x1b[31mbanner\x1b[0m shows the count.")
    fake = closing(spec_body=spec)
    rc = pc.main(["describe", "--spec", SPEC, "--criteria", "AC2,AC5", "--vault", VAULT], runner=fake.runner)
    out = capsys.readouterr().out
    assert rc == 0
    assert "\x1b" not in out
    assert out.splitlines() == ["AC2: The [31mbanner[0m shows the count.",
                                "AC5: The report opens in the app."]


def test_describe_redacts_credential_shaped_spans_and_bounds_length(closing, capsys):
    spec = SPEC_BODY.replace("The banner shows the count.", f"Banner {SECRET} " + "x " * 3000)
    fake = closing(spec_body=spec)
    rc = pc.main(["describe", "--spec", SPEC, "--criteria", "AC2", "--vault", VAULT], runner=fake.runner)
    out = capsys.readouterr().out
    assert rc == 0 and SECRET not in out and len(out) < 1200


def test_describe_marks_an_undeclared_criterion_and_refuses_a_malformed_list(closing, capsys):
    fake = closing()
    rc = pc.main(["describe", "--spec", SPEC, "--criteria", "AC9", "--vault", VAULT], runner=fake.runner)
    assert rc == 0 and capsys.readouterr().out.strip() == "AC9: (criterion text not found)"
    rc = pc.main(["describe", "--spec", SPEC, "--criteria", "AC2;rm", "--vault", VAULT], runner=fake.runner)
    assert rc == 2 and code_of(capsys.readouterr().err) == "invalid-criteria"


# --- seam: the script as a subprocess against the real lore CLI ---------------

_LORE_TESTS = Path(__file__).resolve().parents[2] / "lore" / "tests"
_conftest_spec = importlib.util.spec_from_file_location(
    "lore_tests_conftest_for_pending_check", _LORE_TESTS / "conftest.py"
)
_lore_conftest = importlib.util.module_from_spec(_conftest_spec)
sys.modules["lore_tests_conftest_for_pending_check"] = _lore_conftest
_conftest_spec.loader.exec_module(_lore_conftest)

from lore_tests_conftest_for_pending_check import (  # noqa: E402,F401
    _allow_real_publish_spawn,
    _isolate_ambient_env,
    _publish_spawn_guard,
)


def _seam_env(runner, tmp_path):
    return {**seam_env(runner, tmp_path), "CRAFT_WAITING_GATE_TODAY": TODAY}


def test_the_script_closes_a_manual_check_through_the_real_lore_cli(  # inert-gate: allow seam smoke
    tmp_path,
):
    runner = LoreRunner(tmp_path)
    env = _seam_env(runner, tmp_path)

    def lore(*args, stdin=None):
        r = runner(list(args), stdin)
        assert r.returncode == 0, r.stderr
        return r.stdout

    spec = lore("record", "create", "--kind", "spec", "--title", "Seam spec", "--status", "ready",
                "--vault", "default", stdin=SPEC_BODY).strip().split("/", 1)[1]
    parent = lore("record", "create", "--kind", "task", "--title", "Seam parent", "--status",
                  "in-progress", "--label", "craft/slice-parent=", "--related", f"spec={spec}",
                  "--vault", "default", stdin=PARENT_BODY).strip().split("/", 1)[1]
    entries = tmp_path / "entries.json"
    entries.write_text(json.dumps([entry("AC2")]))
    script = [sys.executable, str(_SCRIPTS / "pending_check.py")]

    def run_script(*args):
        return subprocess.run(script + list(args), capture_output=True, text=True, env=env)

    done = run_script("--spec", spec, "--parent", parent, "--entries", str(entries), "--vault", "default")
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == ["pending: AC2", "spec: written", "parent: updated"]
    shown_spec = json.loads(lore("record", "show", f"spec/{spec}", "--json"))["body"]
    assert [(e.criterion_id, e.building_slice) for e in waiting.parse_waiting(shown_spec)] == [("AC2", parent)]
    shown_parent = json.loads(lore("record", "show", f"task/{parent}", "--json"))["body"]
    assert gate("observation_gate.py", stdin=shown_parent).returncode == 0

    again = run_script("--spec", spec, "--parent", parent, "--entries", str(entries))
    assert again.stdout.splitlines() == ["pending: AC2", "spec: already-present", "parent: already-updated"]
    ok = run_script("verify", "--spec", spec, "--parent", parent)
    assert ok.returncode == 0 and ok.stdout == "verified: AC2\n"
    text = run_script("describe", "--spec", spec, "--criteria", "AC2")
    assert text.stdout == "AC2: The banner shows the count.\n"


# ---------------------------------------------------------------------------
# stuck mode
# ---------------------------------------------------------------------------

STUCK_SPEC = """# The spec

## Acceptance Criteria

- **AC1.** The first thing works. *Verified by: automated assertion.*
- **AC2.** The banner shows the count. *Verified by: manual check.*
- **AC3.** The export is correct. *Verified by: automated assertion.*

## Slices
"""

ALL_PHASES = (1, 2, 3, 4, 5)


def stuck_parent(covers="AC2", observations=("AC2 — manual-check — looked at the banner",),
                 attestations=(), ticked=ALL_PHASES, close_ticked=False, partial=None, end_phases=True,
                 omit=(), extra_close=0):
    lines = ["**Value claim:** you can close with a check waiting.", ""]
    if covers:
        lines.append(f"**Covers:** {covers}")
    if partial:
        lines.append(f"**Partially covers:** {partial}")
    if observations:
        lines += ["", "## Criterion observations", ""] + [f"- **{o.split(' — ', 1)[0]}** — {o.split(' — ', 1)[1]}"
                                                           for o in observations]
    if attestations:
        lines += ["", "## Operator attestations", ""] + [f"- **{a}** — the operator confirmed" for a in attestations]
    if end_phases:
        lines += ["", "## End Phases", ""]
        lines += [f"- [{'x' if n in ticked else ' '}] Phase {n} — step" for n in range(1, 6) if n not in omit]
        lines.append(f"- [{'x' if close_ticked else ' '}] Phase 6 — close")
        lines += ["- [ ] Phase 6 — close again"] * extra_close
    return "\n".join(lines) + "\n"


def run_stuck(fake, capsys, *, spec=SPEC, parent=PARENT, vault=VAULT):
    argv = ["stuck", f"--spec={spec}", f"--parent={parent}"]
    if vault is not None:
        argv.append(f"--vault={vault}")
    rc = pc.main(argv, runner=fake.runner)
    out = capsys.readouterr()
    return rc, out.out, out.err


def stuck_lore(closing, parent_body, *, spec_body=STUCK_SPEC, children=(("child-one", "done"),), **kwargs):
    return closing(spec_body=spec_body, parent_body=parent_body, children=children, **kwargs)


def test_stuck_an_in_progress_parent_finished_but_for_a_manual_check_is_certified(closing, capsys):
    fake = stuck_lore(closing, stuck_parent())
    rc, out, err = run_stuck(fake, capsys)
    assert (rc, out) == (0, "stuck: AC2\n"), err
    assert fake.update_ids() == ["--help"]


def test_stuck_a_blocked_parent_is_never_certified(closing, capsys):
    rc, out, err = run_stuck(stuck_lore(closing, stuck_parent(), parent_status="blocked"), capsys)
    assert (rc, out, code_of(err)) == (1, "", "parent-blocked")


@pytest.mark.parametrize("status", ["open", "ready", "done", "dropped"])
def test_stuck_a_parent_in_any_other_status_is_not_a_slice_parent(status, closing, capsys):
    rc, out, err = run_stuck(stuck_lore(closing, stuck_parent(), parent_status=status), capsys)
    assert (rc, out, code_of(err)) == (1, "", "not-a-slice-parent")


def test_stuck_a_parent_without_the_slice_label_is_not_a_slice_parent(closing, capsys):
    rc, out, err = run_stuck(stuck_lore(closing, stuck_parent(), parent_labels={}), capsys)
    assert (rc, out, code_of(err)) == (1, "", "not-a-slice-parent")


def test_stuck_a_parent_related_to_another_spec_is_not_a_slice_parent(closing, capsys):
    rc, _, err = run_stuck(stuck_lore(closing, stuck_parent(), parent_related={"spec": ["other"]}), capsys)
    assert (rc, code_of(err)) == (1, "not-a-slice-parent")


def test_stuck_a_child_that_is_not_done_means_the_build_is_unfinished(closing, capsys):
    fake = stuck_lore(closing, stuck_parent(), children=(("one", "done"), ("two", "ready")))
    rc, out, err = run_stuck(fake, capsys)
    assert (rc, out, code_of(err)) == (1, "", "build-unfinished")


@pytest.mark.parametrize("status", ["done", "dropped", "superseded"])
def test_stuck_a_terminal_child_counts_as_finished(status, closing, capsys):
    fake = stuck_lore(closing, stuck_parent(), children=(("one", "done"), ("two", status)))
    rc, out, err = run_stuck(fake, capsys)
    assert (rc, out) == (0, "stuck: AC2\n"), err


@pytest.mark.parametrize("status", ["open", "ready", "in-progress", "blocked"])
def test_stuck_a_child_in_a_live_status_means_the_build_is_unfinished(status, closing, capsys):
    fake = stuck_lore(closing, stuck_parent(), children=(("one", "done"), ("two", status)))
    rc, _, err = run_stuck(fake, capsys)
    assert (rc, code_of(err)) == (1, "build-unfinished")


@pytest.mark.parametrize("omit", [(1,), (3,), (1, 2, 3, 4)])
def test_stuck_an_end_phase_line_missing_before_the_close_means_the_build_is_unfinished(omit, closing, capsys):
    rc, _, err = run_stuck(stuck_lore(closing, stuck_parent(omit=omit)), capsys)
    assert (rc, code_of(err)) == (1, "build-unfinished")


def test_stuck_a_second_unticked_close_line_means_the_build_is_unfinished(closing, capsys):
    rc, _, err = run_stuck(stuck_lore(closing, stuck_parent(extra_close=1)), capsys)
    assert (rc, code_of(err)) == (1, "build-unfinished")


def test_stuck_a_spec_found_in_no_vault_is_a_read_failure(closing, capsys):
    fake = stuck_lore(closing, stuck_parent(), with_spec=False)
    rc, out, err = run_stuck(fake, capsys, vault=None)
    assert (rc, out, code_of(err)) == (2, "", "spec-not-found")


def test_stuck_a_spec_found_in_two_vaults_is_a_read_failure(closing, capsys):
    fake = stuck_lore(closing, stuck_parent(), layer="shared")
    fake._create_in("home", "spec", "The spec", "ready", STUCK_SPEC)
    rc, out, err = run_stuck(fake, capsys, vault=None)
    assert (rc, out, code_of(err)) == (2, "", "spec-ambiguous")


def test_stuck_a_spec_name_that_is_not_a_spec_is_a_read_failure(closing, capsys):
    fake = stuck_lore(closing, stuck_parent())
    real = fake.show(f"spec/{SPEC}")
    # hostile input: lore names a spec's kind from its id, so it never answers `spec/<name>` with another kind
    fake.runner.inject(
        lambda argv, _stdin: argv[:2] == ["record", "show"] and argv[-1] == f"spec/{SPEC}",
        returncode=0, stdout=json.dumps({**real, "sidecar": {**real["sidecar"], "kind": "task"}}),
    )
    rc, out, err = run_stuck(fake, capsys, vault=None)
    assert (rc, out, code_of(err)) == (2, "", "not-a-spec")


def test_stuck_a_parent_with_no_children_is_judged_on_its_phases_alone(closing, capsys):
    rc, out, _ = run_stuck(stuck_lore(closing, stuck_parent(), children=()), capsys)
    assert (rc, out) == (0, "stuck: AC2\n")


@pytest.mark.parametrize("ticked", [(1, 2, 3, 4), (1, 2, 3, 5), ()])
def test_stuck_an_unticked_phase_before_the_close_means_the_build_is_unfinished(ticked, closing, capsys):
    rc, _, err = run_stuck(stuck_lore(closing, stuck_parent(ticked=ticked)), capsys)
    assert (rc, code_of(err)) == (1, "build-unfinished")


def test_stuck_a_ticked_close_means_the_build_is_unfinished(closing, capsys):
    rc, _, err = run_stuck(stuck_lore(closing, stuck_parent(close_ticked=True)), capsys)
    assert (rc, code_of(err)) == (1, "build-unfinished")


def test_stuck_a_parent_with_no_end_phases_checklist_is_unfinished(closing, capsys):
    rc, _, err = run_stuck(stuck_lore(closing, stuck_parent(end_phases=False)), capsys)
    assert (rc, code_of(err)) == (1, "build-unfinished")


def test_stuck_an_uncertain_end_phases_heading_is_a_read_failure_not_a_guess(closing, capsys):
    body = stuck_parent() + "\n## End Phases\n\n- [x] Phase 6 — close\n"
    rc, out, _ = run_stuck(stuck_lore(closing, body), capsys)
    assert (rc, out) == (2, "")


def test_stuck_an_unobserved_criterion_whose_trailer_is_manual_check_is_stuck(closing, capsys):
    fake = stuck_lore(closing, stuck_parent(observations=()))
    rc, out, err = run_stuck(fake, capsys)
    assert (rc, out) == (0, "stuck: AC2\n"), err


def test_stuck_an_unobserved_criterion_whose_trailer_is_automated_is_a_close_gap(closing, capsys):
    fake = stuck_lore(closing, stuck_parent(covers="AC3", observations=()))
    rc, out, err = run_stuck(fake, capsys)
    assert (rc, out, code_of(err)) == (1, "", "close-gap-not-a-manual-check")


def test_stuck_a_covered_criterion_the_spec_does_not_declare_is_a_close_gap(closing, capsys):
    fake = stuck_lore(closing, stuck_parent(covers="AC9", observations=()))
    rc, _, err = run_stuck(fake, capsys)
    assert (rc, code_of(err)) == (1, "close-gap-not-a-manual-check")


def test_stuck_a_close_gap_beats_a_stuck_manual_check_on_the_same_parent(closing, capsys):
    body = stuck_parent(covers="AC2, AC3", observations=("AC2 — manual-check — looked",))
    rc, _, err = run_stuck(stuck_lore(closing, body), capsys)
    assert (rc, code_of(err)) == (1, "close-gap-not-a-manual-check")


def test_stuck_an_attested_manual_check_leaves_nothing_to_move(closing, capsys):
    body = stuck_parent(covers="AC1, AC2",
                        observations=("AC1 — automated-assertion — t.py::t", "AC2 — manual-check — looked"),
                        attestations=("AC2",))
    rc, out, err = run_stuck(stuck_lore(closing, body), capsys)
    assert (rc, out, code_of(err)) == (1, "", "no-unconfirmed-manual-check")


def test_stuck_only_the_unconfirmed_manual_check_is_named_among_observed_ones(closing, capsys):
    body = stuck_parent(covers="AC1, AC2",
                        observations=("AC1 — automated-assertion — t.py::t", "AC2 — manual-check — looked"))
    rc, out, _ = run_stuck(stuck_lore(closing, body), capsys)
    assert (rc, out) == (0, "stuck: AC2\n")


def test_stuck_identifiers_are_listed_in_numeric_order(closing, capsys):
    spec = STUCK_SPEC.replace("- **AC3.** The export is correct. *Verified by: automated assertion.*",
                              "- **AC3.** The export is correct. *Verified by: manual check.*")
    body = stuck_parent(covers="AC3, AC2", observations=("AC3 — manual-check — x", "AC2 — manual-check — y"))
    rc, out, _ = run_stuck(stuck_lore(closing, body, spec_body=spec), capsys)
    assert (rc, out) == (0, "stuck: AC2, AC3\n")


def test_stuck_a_half_closed_parent_is_still_stuck_and_resumable(closing, capsys):
    body = stuck_parent(covers=None, partial="AC2",
                        observations=("AC2 — pending-check — waiting entry on spec/the-spec; looked",))
    rc, out, err = run_stuck(stuck_lore(closing, body), capsys)
    assert (rc, out) == (0, "stuck: AC2\n"), err


def test_stuck_a_half_closed_parent_beside_a_still_covered_manual_check_names_both(closing, capsys):
    body = stuck_parent(covers="AC3", partial="AC2", observations=(
        "AC3 — manual-check — x", "AC2 — pending-check — waiting entry on spec/the-spec; looked"))
    rc, out, _ = run_stuck(stuck_lore(closing, body), capsys)
    assert (rc, out) == (0, "stuck: AC2, AC3\n")


def test_stuck_a_parent_whose_push_failed_is_never_certified(closing, capsys):
    labels = {"craft/slice-parent": "", "craft/push": "failed"}
    rc, out, err = run_stuck(stuck_lore(closing, stuck_parent(), parent_labels=labels), capsys)
    assert (rc, out, code_of(err)) == (1, "", "push-failed")


def test_stuck_a_push_label_with_another_value_does_not_refuse(closing, capsys):
    labels = {"craft/slice-parent": "", "craft/push": "ok"}
    rc, out, _ = run_stuck(stuck_lore(closing, stuck_parent(), parent_labels=labels), capsys)
    assert (rc, out) == (0, "stuck: AC2\n")


def test_stuck_a_lore_without_base_on_record_update_is_too_old_and_nothing_else_is_called(closing, capsys):
    fake = stuck_lore(closing, stuck_parent())
    # hostile input: this lore's `record update` has --base; an older one lists other options
    fake.runner.inject(["record", "update", "--help"], returncode=0, stdout="usage: lore record update\n  --diff\n")
    rc, out, err = run_stuck(fake, capsys)
    assert (rc, out, code_of(err)) == (1, "", "lore-too-old")
    assert fake.calls == [(["record", "update", "--help"], None)]


@pytest.mark.parametrize("kwargs", [{"spec": "bad name"}, {"parent": "../x"}, {"vault": "-v"}])
def test_stuck_a_name_of_unsafe_shape_is_refused_before_any_lore_call(kwargs, closing, capsys):
    fake = stuck_lore(closing, stuck_parent())
    rc, _, err = run_stuck(fake, capsys, **kwargs)
    assert (rc, code_of(err)) == (2, "invalid-name")
    assert fake.calls == []


def test_stuck_a_lore_failure_resolving_the_vault_is_a_read_failure(closing, capsys):
    fake = stuck_lore(closing, stuck_parent())
    fake.runner.inject(["vault", "ls"], returncode=1, stderr="boom")
    rc, out, err = run_stuck(fake, capsys, vault=None)
    assert (rc, out, code_of(err)) == (2, "", "lore-failed")


def test_stuck_a_failing_children_list_is_a_read_failure(closing, capsys):
    fake = stuck_lore(closing, stuck_parent())
    fake.runner.inject(["task", "list"], returncode=1, stderr="boom")
    rc, out, err = run_stuck(fake, capsys)
    assert (rc, out, code_of(err)) == (2, "", "lore-failed")


@pytest.mark.parametrize("stdout", ["not json", "{}", "[]", '[{"name": "x"}]'])
def test_stuck_a_children_list_that_does_not_hold_the_parent_is_a_read_failure(stdout, closing, capsys):
    fake = stuck_lore(closing, stuck_parent())
    # hostile input: real lore lists the parent among its tasks as JSON
    fake.runner.inject(["task", "list"], returncode=0, stdout=stdout)
    rc, out, _ = run_stuck(fake, capsys)
    assert (rc, out) == (2, "")


def test_stuck_a_child_missing_from_the_list_is_a_read_failure(closing, capsys):
    fake = stuck_lore(closing, stuck_parent())
    rows = [{"name": PARENT, "status": "in-progress", "parent": None, "children": ["ghost"]}]
    # hostile input: real lore lists every child a parent names
    fake.runner.inject(["task", "list"], returncode=0, stdout=json.dumps(rows))
    rc, out, _ = run_stuck(fake, capsys)
    assert (rc, out) == (2, "")


@pytest.mark.parametrize("missing", ["parent", "spec"])
def test_stuck_a_record_that_cannot_be_read_is_a_read_failure(missing, closing, capsys):
    fake = stuck_lore(closing, stuck_parent(), with_spec=missing != "spec")
    rc, out, _ = run_stuck(fake, capsys, parent="ghost" if missing == "parent" else PARENT)
    assert (rc, out) == (2, "")


def test_stuck_a_reason_line_never_carries_criterion_or_record_text(closing, capsys):
    spec = STUCK_SPEC.replace("The export is correct.", "Zebra marmalade protocol.")
    rc, _, err = run_stuck(stuck_lore(closing, stuck_parent(covers="AC3", observations=()), spec_body=spec), capsys)
    assert rc == 1
    assert "Zebra" not in err and "marmalade" not in err

