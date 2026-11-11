"""set_aside.py: setting a candidate criterion aside onto the spec's `## Waiting` section.

The writer is driven through `main` with the in-process lore runner over a real spec, so
revisions, `--base` and the criteria state are real lore's, and the candidate test is the
candidate set's own derivation run in-process.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

CRAFT = Path(__file__).resolve().parents[1] / "plugins" / "craft"
_SCRIPTS = CRAFT / "scripts"
sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location("set_aside", _SCRIPTS / "set_aside.py")
sa = importlib.util.module_from_spec(_spec)
sys.modules["set_aside"] = sa
_spec.loader.exec_module(sa)

from _lore_runner import LoreRunner  # noqa: E402
import waiting  # noqa: E402

TODAY = "2026-11-08"
SPEC = "the-spec"
VAULT = "v"
SPEC_ID = f"spec/{SPEC}"
WORDS = "set aside by a slice pass until the outside thing it waits on is done"

SPEC_BODY = """# The spec

Prose.

## Acceptance Criteria

- **AC1.** The first thing works.
- **AC2.** The banner shows the count.
- **AC3.** The export is signed.
- **AC4.** The report opens in the app.
- **AC5.** The archive is compressed.

## Slices

- **First** — a claim. (`task/first`, closed 2026-01-01, covers AC1, partially covers AC3)
"""


def waiting_text(criterion, status="waiting", waits_on="the vendor ships the API", *extra, words=None):
    lines = [
        f"- **{criterion}.** set aside",
        f"  - waits on: {waits_on}",
        f"  - 2026-11-01 waiting: {words or WORDS}",
    ]
    if status != "waiting":
        lines.append(f"  - 2026-11-02 {status}: the operator's words")
    lines += extra
    return "\n".join(lines) + "\n"


def spec_with(*entries):
    return SPEC_BODY.rstrip("\n") + "\n\n## Waiting\n\n" + "\n".join(entries)


def item(criterion="AC2", waits_on="the vendor ships the API; unblocks when it is released"):
    return {"criterion": criterion, "waits_on": waits_on}


class World:
    """A real spec created through the in-process lore runner."""

    def __init__(self, root, spec_body=SPEC_BODY, *, close=(), vaults=(VAULT,)):
        self.runner = LoreRunner(root, vaults=vaults)
        self._quiet = False
        r = self.runner(["record", "create", "--kind", "spec", "--title", "The spec", "--status", "ready",
                         "--vault", VAULT], spec_body)
        assert r.returncode == 0, r.stderr
        for criterion in close:
            r = self.runner(["record", "amend", SPEC_ID, "--vault", VAULT, "--close", criterion,
                             "--reason", "closed by hand", "--initiated-by", "operator"])
            assert r.returncode == 0, r.stderr
        self.runner.calls.clear()

    @property
    def calls(self):
        return self.runner.calls

    def updates(self):
        return [(a, s) for a, s in self.calls if a[:2] == ["record", "update"]]

    def show(self):
        r = self.runner(["record", "show", "--json", "--vault", VAULT, SPEC_ID])
        self.runner.calls.pop()
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def body(self):
        return self.show()["body"]

    def revision(self):
        return self.show()["revision"]

    def fail_update(self):
        self.runner.inject(
            lambda argv, _s: argv[:3] == ["record", "update", SPEC_ID] and not self._quiet,
            returncode=1, stderr="boom", times=1,
        )

    def edited_by_another_writer_before_update(self):
        fired = []

        def watch(argv, _stdin):
            if argv[:3] == ["record", "update", SPEC_ID] and not fired:
                fired.append(True)
                self._quiet = True
                r = self.runner(["record", "update", SPEC_ID, "--vault", VAULT],
                                self.body() + "\nA note from another writer.\n")
                self._quiet = False
                assert r.returncode == 0, r.stderr
                self.runner.calls.pop()
            return False

        self.runner.inject(watch)
        self.fail_update()

    def update_then_time_out(self):
        busy = []

        def land(argv, stdin):
            if busy or argv[:3] != ["record", "update", SPEC_ID]:
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
def world(tmp_path):
    made = []

    def make(*args, **kwargs):
        made.append(None)
        return World(tmp_path / f"lore{len(made)}", *args, **kwargs)

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


def run(fake, entries_path, capsys, *, spec=SPEC, vault=VAULT):
    argv = [f"--spec={spec}", "--entries", str(entries_path)]
    if vault is not None:
        argv.append(f"--vault={vault}")
    rc = sa.main(argv, runner=fake.runner)
    out = capsys.readouterr()
    return rc, out.out, out.err


def code_of(err):
    lines = [ln for ln in err.splitlines() if "reason-code:" in ln]
    assert lines, err
    return lines[-1].split("reason-code:")[1].strip()


def test_a_candidate_is_set_aside_with_one_waiting_line_dated_today_and_the_read_revision(world, entries_file, capsys):
    fake = world()
    revision = fake.revision()

    rc, out, err = run(fake, entries_file([item("AC2")]), capsys)

    assert rc == 0, err
    assert out.splitlines() == ["set aside: AC2", "spec: written"]
    [(argv, body)] = fake.updates()
    assert argv[argv.index("--base") + 1] == revision
    [entry] = waiting.parse_waiting(body)
    assert (entry.criterion_id, entry.kind, entry.building_slice, entry.status) == ("AC2", "set aside", None, "waiting")
    assert entry.waits_on == item()["waits_on"]
    assert entry.history == ((TODAY, "waiting", WORDS),)
    assert fake.body() == body


def test_the_dated_line_never_carries_the_callers_text(world, entries_file, capsys):
    fake = world()
    run(fake, entries_file([item("AC2", "ZZ-MARKER outside thing")]), capsys)
    [entry] = waiting.parse_waiting(fake.updates()[0][1])
    assert "ZZ-MARKER" not in entry.history[0][2]


def test_two_entries_in_one_call_land_in_one_write(world, entries_file, capsys):
    fake = world()
    rc, out, err = run(fake, entries_file([item("AC4"), item("AC2", "a second thing")]), capsys)
    assert rc == 0, err
    assert out.splitlines() == ["set aside: AC4", "set aside: AC2", "spec: written"]
    assert len(fake.updates()) == 1
    assert {e.criterion_id for e in waiting.parse_waiting(fake.updates()[0][1])} == {"AC2", "AC4"}


def test_a_spec_with_no_waiting_section_gets_one_at_the_end_and_nothing_else_changes(world, entries_file, capsys):
    fake = world()
    rc, _, err = run(fake, entries_file([item("AC2")]), capsys)
    assert rc == 0, err
    after = fake.body()
    assert after.startswith(SPEC_BODY.rstrip("\n") + "\n\n## Waiting\n\n- **AC2.** set aside\n")
    assert waiting.excise_section(after).rstrip("\n") == SPEC_BODY.rstrip("\n")


def test_a_new_entry_joins_an_existing_waiting_section_after_a_blank_line(world, entries_file, capsys):
    fake = world(spec_with(waiting_text("AC2")))
    rc, _, err = run(fake, entries_file([item("AC4")]), capsys)
    assert rc == 0, err
    assert [e.criterion_id for e in waiting.parse_waiting(fake.body())] == ["AC2", "AC4"]
    assert f"{WORDS}\n\n- **AC4.** set aside" in fake.body()


@pytest.mark.parametrize(
    "criterion,close",
    [("AC1", ()), ("AC9", ()), ("AC3", ("AC3",))],
    ids=["fully-covered", "undeclared", "closed-by-the-operator"],
)
def test_a_criterion_that_is_not_a_candidate_is_refused_and_nothing_is_written(
    world, entries_file, capsys, criterion, close
):
    fake = world(close=close)
    rc, out, err = run(fake, entries_file([item("AC2"), item(criterion)]), capsys)
    assert (rc, out, code_of(err)) == (1, "", "not-a-candidate")
    assert fake.updates() == []
    assert criterion in err


def test_a_partially_covered_unclosed_criterion_is_still_a_candidate(world, entries_file, capsys):
    fake = world()
    rc, _, err = run(fake, entries_file([item("AC3")]), capsys)
    assert rc == 0, err


def test_a_released_entry_in_a_mixed_call_refuses_the_whole_call_and_writes_nothing(world, entries_file, capsys):
    fake = world(spec_with(waiting_text("AC2", "released"), waiting_text("AC4")))
    rc, out, err = run(fake, entries_file([item("AC5"), item("AC4"), item("AC2")]), capsys)
    assert (rc, out, code_of(err)) == (1, "", "entry-exists")
    assert fake.updates() == []


def test_a_waiting_entry_alone_is_a_rerun_that_writes_nothing(world, entries_file, capsys):
    fake = world(spec_with(waiting_text("AC2")))
    rc, out, err = run(fake, entries_file([item("AC2", "different words")]), capsys)
    assert rc == 0, err
    assert out.splitlines() == ["set aside: AC2", "spec: already-present"]
    assert fake.updates() == []


def test_a_released_entry_alone_is_refused_entry_exists(world, entries_file, capsys):
    fake = world(spec_with(waiting_text("AC2", "released")))
    rc, out, err = run(fake, entries_file([item("AC2")]), capsys)
    assert (rc, out, code_of(err)) == (1, "", "entry-exists")
    assert fake.updates() == []


def test_a_partial_rerun_appends_only_the_missing_entries_in_one_write_and_reports_each(world, entries_file, capsys):
    fake = world(spec_with(waiting_text("AC2")))
    rc, out, err = run(fake, entries_file([item("AC2"), item("AC4", "the vendor again")]), capsys)
    assert rc == 0, err
    assert out.splitlines() == ["set aside: AC2", "set aside: AC4", "spec: written"]
    assert len(fake.updates()) == 1
    entries = waiting.parse_waiting(fake.body())
    assert [(e.criterion_id, e.waits_on, len(e.history)) for e in entries] == [
        ("AC2", "the vendor ships the API", 1), ("AC4", "the vendor again", 1)]


@pytest.mark.parametrize(
    "waits_on",
    ["two\nlines", "carriage\rreturn", "# a heading", "- a bullet", "  # indented heading"],
)
def test_waits_on_that_is_not_one_plain_line_is_refused_before_any_lore_call(world, entries_file, capsys, waits_on):
    fake = world()
    rc, out, err = run(fake, entries_file([item("AC2", waits_on)]), capsys)
    assert (rc, out, code_of(err)) == (1, "", "multiline-entry-text")
    assert fake.calls == []


def test_waits_on_with_credential_shaped_text_is_refused_by_the_waiting_gate_and_nothing_is_written(
    world, entries_file, capsys
):
    fake = world()
    rc, out, err = run(fake, entries_file([item("AC2", "the vendor sk_live_" + "a1b2c3d4e5f6" + " key")]), capsys)
    assert (rc, out, code_of(err)) == (1, "", "credential-shaped-text")
    assert "a1b2c3d4e5f6" not in err
    assert fake.updates() == []


def test_waits_on_over_the_cap_is_refused_and_at_the_cap_is_written(world, entries_file, capsys):
    cap = sa.MAX_WAITS_ON_CHARS
    words = ("abc " * cap)[: cap - 1] + "z"
    assert len(words) == cap
    fake = world()
    rc, out, err = run(fake, entries_file([item("AC2", words + "z")]), capsys)
    assert (rc, out, code_of(err)) == (1, "", "waits-on-too-long")
    assert fake.calls == []
    rc, _, err = run(fake, entries_file([item("AC2", words)]), capsys)
    assert rc == 0, err
    assert waiting.parse_waiting(fake.body())[0].waits_on == words


def test_a_write_refused_because_the_spec_changed_after_the_read_reports_stale_spec(world, entries_file, capsys):
    fake = world()
    fake.edited_by_another_writer_before_update()
    rc, out, err = run(fake, entries_file([item("AC2")]), capsys)
    assert (rc, out, code_of(err)) == (2, "", "stale-spec")


def test_a_failed_write_that_in_fact_landed_is_treated_as_written(world, entries_file, capsys):
    fake = world()
    fake.update_then_time_out()
    rc, out, err = run(fake, entries_file([item("AC2")]), capsys)
    assert rc == 0, err
    assert out.splitlines() == ["set aside: AC2", "spec: written"]
    assert [e.criterion_id for e in waiting.parse_waiting(fake.body())] == ["AC2"]


def test_a_failed_write_with_an_unchanged_revision_reports_spec_write_failed(world, entries_file, capsys):
    fake = world()
    before = fake.revision()
    fake.fail_update()
    rc, out, err = run(fake, entries_file([item("AC2")]), capsys)
    assert (rc, out, code_of(err)) == (2, "", "spec-write-failed")
    assert fake.revision() == before


@pytest.mark.parametrize(
    "kwargs",
    [{"spec": "bad name"}, {"spec": "../x"}, {"vault": "-v"}, {"vault": "a b"}],
    ids=["spec-space", "spec-dotdot", "vault-dash", "vault-space"],
)
def test_a_spec_or_vault_failing_the_shape_check_is_refused_with_no_lore_call(world, entries_file, capsys, kwargs):
    fake = world()
    rc, out, err = run(fake, entries_file([item("AC2")]), capsys, **kwargs)
    assert (rc, out, code_of(err)) == (2, "", "invalid-name")
    assert fake.calls == []


@pytest.mark.parametrize(
    "entries",
    [
        [],
        {"criterion": "AC2", "waits_on": "x"},
        [{"criterion": "AC2"}],
        [{"criterion": "AC2", "waits_on": "x", "extra": 1}],
        [{"criterion": "ac2", "waits_on": "x"}],
        [{"criterion": "AC2", "waits_on": 5}],
        [{"criterion": "AC2", "waits_on": "  "}],
        [item("AC2"), item("AC2")],
    ],
    ids=["empty", "not-a-list", "no-waits-on", "extra-key", "bad-id", "non-string", "blank", "duplicate"],
)
def test_malformed_entries_are_refused_before_any_lore_call(world, entries_file, capsys, entries):
    fake = world()
    rc, out, err = run(fake, entries_file(entries), capsys)
    assert (rc, out, code_of(err)) == (2, "", "invalid-entries")
    assert fake.calls == []


def test_a_spec_that_is_not_in_the_vault_is_refused_spec_not_found(world, entries_file, capsys):
    fake = world()
    rc, out, err = run(fake, entries_file([item("AC2")]), capsys, spec="no-such-spec")
    assert (rc, out, code_of(err)) == (1, "", "spec-not-found")


# ---------------------------------------------------------------------------
# Reason-codes: the codes a document names for this writer are ones the module declares.
# ---------------------------------------------------------------------------


PARENT = "the-parent"
PARENT_ID = f"task/{PARENT}"
BUILD_WORDS = "set aside by a build session until the outside thing it waits on is done"

PARENT_PLAIN = """**Value claim:** the slice ships.

**Covers:** AC2, AC4, AC5

**Partially covers:** AC3

## Notes

Prose.
"""

PARENT_OBSERVED = """**Value claim:** the slice ships.

**Covers:** AC2, AC4, AC5

**Partially covers:** AC3

## Criterion observations

- **AC2** — manual-check — looked at the banner
- **AC4** — manual-check — opened the report by hand
- **AC5** — manual-check — checked the archive size
- **AC3** — pending-check — waiting entry on spec/the-spec; no observation recorded before close

## Operator attestations

- **AC2** — the operator saw the banner
- **AC4** — the operator opened the report
- **AC5** — the operator checked the size
"""


class BuildWorld(World):
    """A spec and an `in-progress` slice parent, both real lore records."""

    def __init__(self, root, parent_body=PARENT_PLAIN, *, status="in-progress", labels=None, related=None,
                 spec_body=SPEC_BODY, close=()):
        super().__init__(root, spec_body, close=close)
        labels = {"craft/slice-parent": ""} if labels is None else labels
        related = {"spec": [SPEC]} if related is None else related
        extras = [f"--label={k}={v}" for k, v in labels.items()]
        extras += [f"--related={kind}={name}" for kind, names in related.items() for name in names]
        r = self.runner(["record", "create", "--kind", "task", "--title", "The parent", "--status", status,
                         "--vault", VAULT, *extras], parent_body)
        assert r.returncode == 0, r.stderr
        self.runner.calls.clear()

    def update_ids(self):
        return [a[2] for a, _ in self.updates()]

    def parent_show(self):
        r = self.runner(["record", "show", "--json", "--vault", VAULT, PARENT_ID])
        self.runner.calls.pop()
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def parent_body(self):
        return self.parent_show()["body"]

    def parent_updates(self):
        return [(a, s) for a, s in self.updates() if a[2] == PARENT_ID]

    def edit_parent(self, transform):
        body = self.parent_body()
        r = self.runner(["record", "update", PARENT_ID, "--vault", VAULT], transform(body))
        self.runner.calls.pop()
        assert r.returncode == 0, r.stderr

    def fail_parent_update(self, *, after_other_writer=None):
        busy = []

        def watch(argv, _stdin):
            if busy or argv[:3] != ["record", "update", PARENT_ID]:
                return False
            if after_other_writer is not None:
                busy.append(True)
                after_other_writer()
                busy.clear()
            return True

        self.runner.inject(watch, returncode=1, stderr="boom", times=1)


@pytest.fixture
def build(tmp_path):
    made = []

    def make(*args, **kwargs):
        made.append(None)
        return BuildWorld(tmp_path / f"build{len(made)}", *args, **kwargs)

    return make


def run_build(fake, entries_path, capsys, *, parent=PARENT, spec=SPEC, vault=VAULT):
    argv = [f"--spec={spec}", f"--parent={parent}", "--entries", str(entries_path), f"--vault={vault}"]
    rc = sa.main(argv, runner=fake.runner)
    out = capsys.readouterr()
    return rc, out.out, out.err


def cover_lines(body):
    return [ln for ln in body.splitlines() if ln.startswith(("**Covers:**", "**Partially covers:**"))]


def test_a_covered_criterion_is_set_aside_and_leaves_the_parents_other_covers_in_order(build, entries_file, capsys):
    fake = build()
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys)
    assert rc == 0, err
    assert out.splitlines() == ["set aside: AC4", "spec: written", "parent: updated"]
    assert fake.update_ids() == [SPEC_ID, PARENT_ID]
    assert [e.criterion_id for e in waiting.parse_waiting(fake.body())] == ["AC4"]
    assert fake.parent_body() == PARENT_PLAIN.replace("**Covers:** AC2, AC4, AC5", "**Covers:** AC2, AC5")


@pytest.mark.parametrize(
    "parent_body,expected",
    [
        (PARENT_PLAIN, PARENT_PLAIN.replace("**Partially covers:** AC3\n", "")),
        (
            PARENT_PLAIN.replace("**Partially covers:** AC3", "**Partially covers:** AC3, AC1"),
            PARENT_PLAIN.replace("**Partially covers:** AC3", "**Partially covers:** AC1"),
        ),
    ],
    ids=["emptied-line-is-removed", "remaining-identifier-stays"],
)
def test_a_partially_covered_criterion_is_set_aside_and_an_emptied_line_is_removed(
    build, entries_file, capsys, parent_body, expected
):
    fake = build(parent_body)
    rc, out, err = run_build(fake, entries_file([item("AC3")]), capsys)
    assert rc == 0, err
    assert out.splitlines() == ["set aside: AC3", "spec: written", "parent: updated"]
    assert fake.parent_body() == expected


@pytest.mark.parametrize(
    "parent_body,criteria",
    [
        (PARENT_PLAIN.replace("**Covers:** AC2, AC4, AC5", "**Covers:** AC2"), ["AC2", "AC3"]),
        (PARENT_PLAIN.replace("**Covers:** AC2, AC4, AC5", "**Covers:** AC2").replace(
            "\n**Partially covers:** AC3\n", ""), ["AC2"]),
        (PARENT_PLAIN.replace("**Partially covers:** AC3", "**Partially covers:** AC3, AC1"), ["AC2", "AC4", "AC5", "AC3", "AC1"]),
    ],
    ids=["covers-and-partial-both-emptied", "only-covered-criterion", "everything-at-once"],
)
def test_a_set_aside_that_would_leave_the_parent_covering_nothing_is_refused_and_nothing_is_written(
    build, entries_file, capsys, parent_body, criteria
):
    fake = build(parent_body)
    rc, out, err = run_build(fake, entries_file([item(c) for c in criteria]), capsys)
    assert (rc, out, code_of(err)) == (1, "", "would-empty-slice")
    assert fake.updates() == []
    assert fake.parent_body() == parent_body


def test_a_criterion_that_is_not_on_the_parent_is_refused_not_on_this_slice_and_nothing_is_written(
    build, entries_file, capsys
):
    fake = build()
    rc, out, err = run_build(fake, entries_file([item("AC4"), item("AC1")]), capsys)
    assert (rc, out, code_of(err)) == (1, "", "not-on-this-slice")
    assert "AC1" in err
    assert fake.updates() == []


@pytest.mark.parametrize(
    "kwargs",
    [{"status": "done"}, {"labels": {"x/y": ""}}, {"related": {"spec": ["another-spec"]}}],
    ids=["done", "unlabelled", "other-spec"],
)
def test_a_parent_that_is_not_an_in_progress_slice_parent_of_this_spec_is_refused_and_nothing_is_written(
    build, entries_file, capsys, kwargs
):
    fake = build(**kwargs)
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys)
    assert (rc, out, code_of(err)) == (1, "", "not-a-slice-parent")
    assert fake.updates() == []


def test_a_parent_whose_covers_cannot_be_read_is_refused_unreadable_parent_and_nothing_is_written(
    build, entries_file, capsys
):
    fake = build(PARENT_PLAIN + "\n**Covers:** AC2\n")
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys)
    assert (rc, out, code_of(err)) == (2, "", "unreadable-parent")
    assert fake.updates() == []


def test_a_parent_that_does_not_exist_is_refused_parent_not_found(build, entries_file, capsys):
    fake = build()
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys, parent="no-such-parent")
    assert (rc, out, code_of(err)) == (1, "", "parent-not-found")
    assert fake.updates() == []


def test_a_parent_write_refused_as_stale_leaves_the_spec_entry_and_a_rerun_finishes_the_parent(
    build, entries_file, capsys
):
    fake = build()
    fake.fail_parent_update(after_other_writer=lambda: fake.edit_parent(lambda b: b + "\nA note.\n"))
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys)
    assert (rc, out, code_of(err)) == (2, "", "stale-parent")
    assert "spec entry already landed" in err
    assert [e.criterion_id for e in waiting.parse_waiting(fake.body())] == ["AC4"]
    assert fake.parent_body() == PARENT_PLAIN + "\nA note.\n"

    fake.calls.clear()
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys)
    assert rc == 0, err
    assert out.splitlines() == ["set aside: AC4", "spec: already-present", "parent: updated"]
    assert fake.update_ids() == [PARENT_ID]
    assert fake.parent_body() == (PARENT_PLAIN + "\nA note.\n").replace("AC2, AC4, AC5", "AC2, AC5")


def test_a_parent_write_that_fails_with_an_unchanged_revision_is_parent_write_failed(build, entries_file, capsys):
    fake = build()
    fake.fail_parent_update()
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys)
    assert (rc, out, code_of(err)) == (2, "", "parent-write-failed")
    assert "spec entry already landed" in err
    assert fake.parent_body() == PARENT_PLAIN


def test_the_parent_is_written_with_the_revision_of_the_read_after_the_spec_write(build, entries_file, capsys):
    fake = build()
    first_revision = fake.parent_show()["revision"]
    fired = []

    # another writer edits the parent after the spec write lands, before the writer re-reads it
    real_runner = fake.runner

    class Wrapped:
        def __getattr__(self, name):
            return getattr(real_runner, name)

        def __call__(self, argv, stdin=None):
            result = real_runner(argv, stdin)
            if argv[:3] == ["record", "update", SPEC_ID] and result.returncode == 0 and not fired:
                fired.append(True)
                fake.edit_parent(lambda b: b + "\nEdited between the reads.\n")
            return result

    rc = sa.main([f"--spec={SPEC}", f"--parent={PARENT}", "--entries", str(entries_file([item("AC4")])),
                  f"--vault={VAULT}"], runner=Wrapped())
    assert rc == 0, capsys.readouterr().err
    [(argv, body)] = fake.parent_updates()
    second_revision_base = argv[argv.index("--base") + 1]
    assert second_revision_base != first_revision
    assert "Edited between the reads." in body
    assert "**Covers:** AC2, AC5" in body


def test_a_parent_narrowed_elsewhere_after_the_spec_write_is_already_updated_and_not_rewritten(
    build, entries_file, capsys
):
    fake = build()
    real_runner = fake.runner
    fired = []

    class Wrapped:
        def __getattr__(self, name):
            return getattr(real_runner, name)

        def __call__(self, argv, stdin=None):
            result = real_runner(argv, stdin)
            if argv[:3] == ["record", "update", SPEC_ID] and result.returncode == 0 and not fired:
                fired.append(True)
                fake.edit_parent(lambda b: b.replace("AC2, AC4, AC5", "AC2, AC5"))
            return result

    rc = sa.main([f"--spec={SPEC}", f"--parent={PARENT}", "--entries", str(entries_file([item("AC4")])),
                  f"--vault={VAULT}"], runner=Wrapped())
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert out.out.splitlines() == ["set aside: AC4", "spec: written", "parent: already-updated"]
    assert fake.parent_updates() == []


def certify_covers_in_process(spec_body, covers, partial, monkeypatch):
    import io
    import covers_gate

    argv = []
    if covers:
        argv += ["--covers", ", ".join(covers)]
    if partial:
        argv += ["--partial-covers", ", ".join(partial)]
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(spec_body.encode("utf-8")), encoding="utf-8"))
    return covers_gate.main(argv)


def test_a_narrowed_parent_passes_the_close_paths_covers_and_observation_gates(
    build, entries_file, capsys, monkeypatch
):
    import observation_gate

    fake = build(PARENT_OBSERVED)
    observation_gate.certify(PARENT_OBSERVED)
    rc, _, err = run_build(fake, entries_file([item("AC3")]), capsys)
    assert rc == 0, err
    narrowed = fake.parent_body()
    covers, observations, partial = observation_gate.certify(narrowed)
    assert (covers, partial) == (["AC2", "AC4", "AC5"], [])
    assert set(observations) == {"AC2", "AC4", "AC5"}
    assert certify_covers_in_process(fake.body(), covers, partial, monkeypatch) == 0


def test_a_criterion_carrying_an_observation_line_loses_that_line_and_the_observation_gate_certifies(
    build, entries_file, capsys
):
    import observation_gate

    fake = build(PARENT_OBSERVED)
    rc, _, err = run_build(fake, entries_file([item("AC4")]), capsys)
    assert rc == 0, err
    narrowed = fake.parent_body()
    assert "- **AC4** — manual-check" not in narrowed
    assert narrowed == (
        PARENT_OBSERVED.replace("AC2, AC4, AC5", "AC2, AC5").replace(
            "- **AC4** — manual-check — opened the report by hand\n", "")
    )
    covers, observations, partial = observation_gate.certify(narrowed)
    assert (covers, set(observations), partial) == (["AC2", "AC5"], {"AC2", "AC5", "AC3"}, ["AC3"])

    left_in = PARENT_OBSERVED.replace("AC2, AC4, AC5", "AC2, AC5")
    with pytest.raises(observation_gate.ObservationViolation, match="AC4"):
        observation_gate.certify(left_in)


def test_a_partially_covered_criterion_with_an_observation_line_loses_it_too(build, entries_file, capsys):
    import observation_gate

    fake = build(PARENT_OBSERVED)
    rc, _, err = run_build(fake, entries_file([item("AC3")]), capsys)
    assert rc == 0, err
    narrowed = fake.parent_body()
    assert "AC3" not in narrowed
    observation_gate.certify(narrowed)


def test_a_remaining_identifier_the_covers_gate_refuses_leaves_the_parent_unwritten(build, entries_file, capsys):
    fake = build(PARENT_PLAIN.replace("AC2, AC4, AC5", "AC2, AC9, AC5"))
    rc, out, err = run_build(fake, entries_file([item("AC2")]), capsys)
    assert rc in (1, 2) and out == ""
    assert code_of(err) not in sa.REASON_CODES
    assert fake.parent_updates() == []


def test_a_partial_build_rerun_with_the_entry_present_narrows_the_parent_and_writes_nothing_to_the_spec(
    build, entries_file, capsys
):
    fake = build(spec_body=spec_with(waiting_text("AC4", words=BUILD_WORDS)))
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys)
    assert rc == 0, err
    assert out.splitlines() == ["set aside: AC4", "spec: already-present", "parent: updated"]
    assert fake.update_ids() == [PARENT_ID]
    assert fake.parent_body() == PARENT_PLAIN.replace("AC2, AC4, AC5", "AC2, AC5")


def test_a_released_entry_alone_in_build_mode_is_entry_exists_and_nothing_is_written(build, entries_file, capsys):
    fake = build(spec_body=spec_with(waiting_text("AC4", "released")))
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys)
    assert (rc, out, code_of(err)) == (1, "", "entry-exists")
    assert fake.updates() == []


NARROWED = PARENT_PLAIN.replace("AC2, AC4, AC5", "AC2, AC5")


def test_a_rerun_after_a_complete_set_aside_writes_nothing_and_reports_both_records_already_done(
    build, entries_file, capsys
):
    fake = build(NARROWED, spec_body=spec_with(waiting_text("AC4", words=BUILD_WORDS)))
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys)
    assert rc == 0, err
    assert out.splitlines() == ["set aside: AC4", "spec: already-present", "parent: already-updated"]
    assert fake.updates() == []
    assert fake.parent_body() == NARROWED


def test_a_criterion_off_the_parent_with_no_set_aside_entry_is_still_refused_not_on_this_slice(
    build, entries_file, capsys
):
    fake = build(NARROWED)
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys)
    assert (rc, out, code_of(err)) == (1, "", "not-on-this-slice")
    assert fake.updates() == []


def test_a_criterion_off_the_parent_whose_entry_was_released_is_not_already_done_and_is_entry_exists(
    build, entries_file, capsys
):
    fake = build(NARROWED, spec_body=spec_with(waiting_text("AC4", "released")))
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys)
    assert (rc, out, code_of(err)) == (1, "", "entry-exists")
    assert fake.updates() == []


def test_each_mode_writes_its_own_history_wording_never_the_callers_text(build, world, entries_file, capsys):
    sliced = world()
    run(sliced, entries_file([item("AC4", "ZZ-MARKER")]), capsys)
    built = build()
    run_build(built, entries_file([item("AC4", "ZZ-MARKER")]), capsys)
    [slice_entry] = waiting.parse_waiting(sliced.body())
    [build_entry] = waiting.parse_waiting(built.body())
    slice_words, build_words = slice_entry.history[0][2], build_entry.history[0][2]
    assert slice_words == WORDS
    assert build_words == BUILD_WORDS
    assert "slice pass" not in build_words and "ZZ-MARKER" not in build_words


def test_an_unusable_parent_name_is_refused_before_any_lore_call(build, entries_file, capsys):
    fake = build()
    rc, out, err = run_build(fake, entries_file([item("AC4")]), capsys, parent="bad name")
    assert (rc, out, code_of(err)) == (2, "", "invalid-name")
    assert fake.calls == []


def test_the_build_mode_codes_are_declared(build, entries_file, capsys):
    emitted = set()
    for kwargs in ({"status": "done"}, {}):
        fake = build(**kwargs)
        for criteria in (["AC1"], ["AC2", "AC4", "AC5", "AC3"]):
            _, _, err = run_build(fake, entries_file([item(c) for c in criteria]), capsys)
            emitted.add(code_of(err))
    assert {"not-a-slice-parent", "not-on-this-slice", "would-empty-slice"} <= emitted <= sa.REASON_CODES


DOCUMENTS = (CRAFT / "skills" / "slice" / "SKILL.md", CRAFT / "skills" / "_shared" / "execute.md")
_TOKEN_RE = re.compile(r"`([a-z]+(?:-[a-z]+)+)`")


def declared_codes(module) -> set[str]:
    return set(module.REASON_CODES) | set(waiting.REASON_CODES)


def codes_named_for_writer(text: str) -> set[str]:
    """Backticked hyphenated tokens in every paragraph that names `set_aside.py`."""
    named: set[str] = set()
    for block in re.split(r"\n\s*\n", text):
        if "set_aside.py" in block:
            named |= set(_TOKEN_RE.findall(block))
    return named


def undeclared_in(text: str, module) -> list[str]:
    return sorted(codes_named_for_writer(text) - declared_codes(module))


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: p.name)
def test_every_reason_code_a_skill_names_for_the_writer_is_one_the_module_declares(path):  # inert-gate: allow code-vs-document consistency seam
    assert undeclared_in(path.read_text(encoding="utf-8"), sa) == []


def test_the_check_reports_a_code_the_module_does_not_declare(tmp_path):
    declared = sorted(sa.REASON_CODES)[0]
    doc = f"Run `set_aside.py --spec x`; it may refuse `{declared}` or `zz-made-up-code`.\n\nOther `unrelated-token`."
    assert undeclared_in(doc, sa) == ["zz-made-up-code"]
    assert undeclared_in(doc.replace("zz-made-up-code", "not-a-candidate"), sa) == []


def test_the_declared_codes_are_the_ones_the_writer_can_emit(world, entries_file, capsys):
    fake = world()
    emitted = set()
    for criterion in ("AC1", "AC9"):
        _, _, err = run(fake, entries_file([item(criterion)]), capsys)
        emitted.add(code_of(err))
    _, _, err = run(fake, entries_file([item("AC2", "ab " * sa.MAX_WAITS_ON_CHARS)]), capsys)
    emitted.add(code_of(err))
    assert emitted <= sa.REASON_CODES
