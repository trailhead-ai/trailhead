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


def waiting_text(criterion, status="waiting", waits_on="the vendor ships the API", *extra):
    lines = [
        f"- **{criterion}.** set aside",
        f"  - waits on: {waits_on}",
        f"  - 2026-11-01 waiting: {WORDS}",
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
