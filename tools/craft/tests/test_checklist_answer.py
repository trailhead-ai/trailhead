"""checklist_answer.py: saves one operator answer to one waiting pending check.

Every test drives `main` with an in-process real lore (`LoreRunner`) over a real spec holding
a ledger and a `## Waiting` section, and asserts on what the vault holds afterwards. The one
subprocess seam is `test_checklist_answer_seam.py`.
"""

from __future__ import annotations

import datetime
import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from _lore_runner import LoreRunner

_SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import checklist_answer as ca  # noqa: E402
import waiting  # noqa: E402
import waiting_gate  # noqa: E402

TODAY = "2026-11-16"
SECRET = "sk_live_" + "a1b2c3d4e5f6"
SLICE = "build-it"
DEFAULT_WORDS = "passed on the check list (no note)"
STALE_TEXT = "error: spec/x changed after you read it: --base is old but the stored body's revision is new."

BODY_HEAD = """# Answer spec

Prose.

## Acceptance Criteria

- **AC1.** The first thing works.
- **AC2.** The banner shows the count.
- **AC3.** The report opens in the app.
- **AC4.** The export is named by date.
- **AC5.** The invoice carries the tax number.

## Slices

- **Parent** — claim (`task/{slice}`, closed 2026-11-08, covers AC1, partially covers AC2)
"""


def pending(cid, check="the banner shows 3", how="open the app", history=(("2026-11-08", "waiting", "set by slice"),),
            slice_=SLICE):
    lines = [f"- **{cid}.** pending check — task/{slice_}", f"  - check: {check}", f"  - how: {how}",
             "  - changed behaviour: no"]
    lines += [f"  - {d} {s}: {w}" for d, s, w in history]
    return "\n".join(lines) + "\n"


def set_aside(cid="AC5"):
    return f"- **{cid}.** set aside\n  - waits on: legal sign-off\n  - 2026-11-03 waiting: parked\n"


def spec_body(*entries):
    head = BODY_HEAD.format(slice=SLICE)
    return head + "\n## Waiting\n\n" + "\n".join(entries)


DEFAULT_ENTRIES = (
    pending("AC2"),
    pending("AC3"),
    pending("AC4", history=(("2026-11-08", "waiting", "set"), ("2026-11-10", "failed", "it broke"))),
    set_aside("AC5"),
)


class World:
    def __init__(self, tmp_path, entries=DEFAULT_ENTRIES, body=None, vault="default"):
        self.tmp_path = tmp_path
        self.vault = vault
        self.lore = LoreRunner(tmp_path / "lore", vaults=("default", vault) if vault != "default" else ("default",))
        r = self.lore(["record", "create", "--kind", "spec", "--title", "answer-spec", "--status", "ready",
                       "--vault", self.vault], body if body is not None else spec_body(*entries))
        assert r.returncode == 0, r.stderr
        self.spec = r.stdout.splitlines()[0]
        self.lore.calls.clear()
        self.words_path = tmp_path / "mktemp" / "tmp.words0001"

    def show(self):
        r = self.lore(["record", "show", "--json", "--vault", self.vault, self.spec])
        self.lore.calls.pop()
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    @property
    def revision(self):
        return self.show()["revision"]

    @property
    def body(self):
        return self.show()["body"]

    def edit(self, transform):
        """An out-of-band change to the spec body, as a second session would make."""
        shown = self.show()
        r = self.lore(["record", "update", self.spec, "--vault", self.vault, "--base", shown["revision"]],
                      transform(shown["body"]))
        self.lore.calls.pop()
        assert r.returncode == 0, r.stderr

    def criteria(self):
        r = self.lore(["record", "criteria", self.spec, "--vault", self.vault])
        self.lore.calls.pop()
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout), r.stdout

    def entry(self, cid, slice_=SLICE):
        return {e.key: e for e in waiting.parse_waiting(self.body)}[(cid, slice_)]

    def candidate_set(self):
        state = self.tmp_path / "criteria-state.json"
        state.write_text(self.criteria()[1], encoding="utf-8")
        done = subprocess.run([sys.executable, str(_SCRIPTS / "candidate_set.py"), "--criteria-state", str(state)],
                              input=self.body, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        return dict(line.split(": ", 1) for line in done.stdout.splitlines() if ": " in line)

    def writes(self):
        return [argv for argv, _ in self.lore.calls if argv[:2] in (["record", "update"], ["record", "amend"])
                and "--help" not in argv]

    def run(self, capsys, *, criterion="AC2", slice_=SLICE, answer="failed", words="the banner showed 2",
            base=None, vault=None, spec=None, runner=None):
        if words is not None:
            self.words_path.write_text(words, encoding="utf-8")
        argv = ["--vault", vault or self.vault, "--spec", spec or self.spec, "--criterion", criterion,
                "--slice", f"task/{slice_}", "--answer", answer, "--base", base or self.revision,
                "--words-file", str(self.words_path)]
        self.lore.calls.clear()
        code = ca.main(argv, runner or self.lore)
        cap = capsys.readouterr()
        if code != 0:
            assert cap.out == ""
            assert code_of(cap.err) in ca.REASON_CODES
        return Result(code, cap.out, cap.err)


class Result:
    def __init__(self, code, out, err):
        self.code, self.out, self.err = code, out, err

    @property
    def reason(self):
        return code_of(self.err)


def code_of(stderr):
    lines = [ln for ln in stderr.splitlines() if "reason-code:" in ln]
    return lines[-1].split("reason-code:", 1)[1].strip() if lines else None


@pytest.fixture(autouse=True)
def mktemp_dir(monkeypatch, tmp_path):
    """The directory `mktemp` would use in this test: `$TMPDIR`."""
    directory = tmp_path / "mktemp"
    directory.mkdir()
    monkeypatch.setenv("TMPDIR", str(directory))
    return directory


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv(waiting_gate.TODAY_ENV, TODAY)
    return World(tmp_path)


def inject_write(lore, head, **kwargs):
    """Fail the write `head` (`["record", "update"]` or `["record", "amend"]`), not its `--help` probe."""
    lore.inject(lambda argv, _s: argv[:2] == head and "--help" not in argv, **kwargs)


def make_shared(lore, vault="default"):
    config = lore.config_home / "lore" / "config.json"
    doc = json.loads(config.read_text(encoding="utf-8"))
    for entry_ in doc["vaults"]:
        entry_["shared"] = entry_["name"] == vault
    config.write_text(json.dumps(doc), encoding="utf-8")


# --- saving -------------------------------------------------------------------


def test_a_failed_answer_appends_the_dated_line_and_returns_the_criterion_to_the_candidates(world, capsys):
    before = world.candidate_set()
    assert "AC2" not in before["candidates"].split(", ")
    prior = world.body

    res = world.run(capsys, answer="failed", words="the banner showed 2, not 3")

    assert (res.code, res.out.splitlines()[0]) == (0, "saved: AC2 failed")
    assert world.body == prior.replace(
        "  - 2026-11-08 waiting: set by slice\n\n- **AC3.**",
        "  - 2026-11-08 waiting: set by slice\n  - 2026-11-16 failed: the banner showed 2, not 3\n\n- **AC3.**")
    entry = world.entry("AC2")
    assert (entry.status, entry.history[-1]) == ("failed", (TODAY, "failed", "the banner showed 2, not 3"))
    after = world.candidate_set()
    assert "AC2" in after["candidates"].split(", ")
    assert "AC2" in after["partial"].split(", ")
    assert "AC2" not in after["waiting"].split(", ")
    assert "closed" not in world.criteria()[0] or "AC2" not in world.criteria()[0]["closed"]


def test_a_failed_answer_is_written_with_update_and_never_amend(world, capsys):
    world.run(capsys, answer="failed")
    assert [argv[:2] for argv in world.writes()] == [["record", "update"]]


def test_a_passed_answer_appends_the_line_and_closes_the_criterion_in_one_write(world, capsys):
    res = world.run(capsys, answer="passed", words="saw 3 on the banner")

    assert (res.code, res.out.splitlines()[0]) == (0, "saved: AC2 passed")
    assert world.entry("AC2").history[-1] == (TODAY, "passed", "saw 3 on the banner")
    state, _ = world.criteria()
    assert state["closed"] == ["AC2"]
    tokens = world.candidate_set()
    assert "AC2" not in tokens["candidates"].split(", ")
    assert tokens["closed"] == "AC2"
    assert [argv[:2] for argv in world.writes()] == [["record", "amend"]]


def test_a_passed_answer_with_no_words_records_exactly_the_default_note(world, capsys):
    res = world.run(capsys, answer="passed", words=" \n\t ")
    assert res.code == 0, res.err
    assert world.entry("AC2").history[-1] == (TODAY, "passed", DEFAULT_WORDS)


def test_words_are_flattened_to_one_line_and_control_and_format_characters_removed(world, capsys):
    res = world.run(capsys, answer="failed", words="first line\n\nsecond\tline\x07 with​ a zero-width break  ")
    assert res.code == 0, res.err
    assert world.entry("AC2").history[-1][2] == "first line second line with a zero-width break"


def test_words_at_the_length_limit_are_saved_and_one_over_is_refused(world, capsys):
    limit = ca.MAX_WORDS_CHARS
    at_limit = ("abcd " * limit)[: limit - 1] + "z"
    refused = world.run(capsys, answer="failed", words=at_limit + "z")
    assert (refused.code, refused.reason) == (1, "words-too-long")
    assert world.writes() == []

    saved = world.run(capsys, answer="failed", words=at_limit)
    assert (saved.code, saved.out.splitlines()[0]) == (0, "saved: AC2 failed")
    assert world.entry("AC2").history[-1][2] == at_limit


# --- the base revision --------------------------------------------------------


def test_a_base_older_than_the_stored_revision_is_stale_and_nothing_is_written(world, capsys):
    old = world.revision
    world.edit(lambda b: b.rstrip("\n") + "\n\n" + pending("AC1", slice_="other"))
    current = world.revision

    res = world.run(capsys, base=old)

    assert (res.code, res.reason) == (1, "stale")
    assert world.writes() == []
    assert world.revision == current


def test_a_write_lore_refuses_as_stale_is_stale_when_the_spec_moved_after_the_read(world, capsys):
    shown = world.show()
    world.edit(lambda b: b.rstrip("\n") + "\n\n" + pending("AC1", slice_="other"))
    current = world.revision
    # the read the script makes still shows the revision the operator saw; the write then meets the newer one
    world.lore.inject(["record", "show"], returncode=0, stdout=json.dumps(shown), times=1)

    res = world.run(capsys, base=shown["revision"])

    assert (res.code, res.reason) == (1, "stale")
    assert len(world.writes()) == 1
    assert world.revision == current


def test_a_malformed_base_is_refused_before_lore_is_called(world, capsys):
    res = world.run(capsys, base="zzz")
    assert (res.code, res.reason) == (2, "unsafe-name")
    assert world.lore.calls == []


# --- refusals that leave the spec byte-identical ------------------------------


@pytest.mark.parametrize(
    "kwargs, code, exit_code",
    [
        (dict(criterion="AC9"), "entry-not-found", 1),
        (dict(slice_="another-slice"), "entry-not-found", 1),
        (dict(criterion="AC4"), "entry-not-waiting", 1),
        (dict(criterion="AC5"), "answer-not-pending-check", 1),
        (dict(answer="failed", words=""), "words-required", 1),
        (dict(answer="failed", words=" \n "), "words-required", 1),
        (dict(words=f"it printed {SECRET}"), "credential-shaped-words", 1),
        (dict(words="it printed sk_live_​a1b2c3d4e5f6"), "credential-shaped-words", 1),
        (dict(words="y" * 5000), "words-too-long", 1),
    ],
)
def test_each_refusal_has_its_own_code_and_leaves_the_spec_unchanged(world, capsys, kwargs, code, exit_code):
    before = world.revision

    res = world.run(capsys, base=before, **kwargs)

    assert (res.code, res.reason) == (exit_code, code)
    assert world.writes() == []
    assert world.revision == before
    assert SECRET not in res.out + res.err


def test_an_already_answered_entry_is_refused_as_not_waiting_before_its_words_are_judged(world, capsys):
    res = world.run(capsys, criterion="AC4", answer="failed", words="")
    assert (res.code, res.reason) == (1, "entry-not-waiting")


@pytest.mark.parametrize(
    "field, value",
    [("vault", "v;x"), ("spec", "spec/a b"), ("spec", "task/abc"), ("slice", "../x"),
     ("slice", "Upper"), ("criterion", "AC0"), ("criterion", "ac2"), ("criterion", "AC2 --close AC3"),
     ("base", "A" * 64), ("base", "a" * 63)],
)
def test_an_unsafe_value_is_refused_before_lore_is_called(world, capsys, field, value):
    values = {"vault": "default", "spec": world.spec, "slice": f"task/{SLICE}", "criterion": "AC2",
              "base": "a" * 64}
    values[field] = f"task/{value}" if field == "slice" else value
    world.words_path.write_text("words", encoding="utf-8")
    argv = ["--vault", values["vault"], "--spec", values["spec"], "--criterion", values["criterion"],
            "--slice", values["slice"], "--answer", "failed", "--base", values["base"],
            "--words-file", str(world.words_path)]

    code = ca.main(argv, world.lore)
    cap = capsys.readouterr()

    assert (code, code_of(cap.err)) == (2, "unsafe-name")
    assert world.lore.calls == []


def test_a_malformed_waiting_section_is_refused_unread(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(waiting_gate.TODAY_ENV, TODAY)
    broken = World(tmp_path, body=spec_body(pending("AC2")) + "\nloose prose in the section\n")
    before = broken.revision

    res = broken.run(capsys, base=before)

    assert (res.code, res.reason) == (2, "malformed-prior-section")
    assert broken.writes() == []


def test_a_line_the_gate_finds_too_long_is_refused_with_the_gates_own_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(waiting_gate.TODAY_ENV, TODAY)
    long_check = World(tmp_path, entries=(pending("AC2", check="c" * 1200),))
    before = long_check.revision

    res = long_check.run(capsys, base=before)

    assert (res.code, res.reason) == (1, "field-too-long")
    assert long_check.writes() == []
    assert long_check.revision == before


def test_a_malformed_clock_variable_cannot_certify(world, capsys, monkeypatch):
    monkeypatch.setenv(waiting_gate.TODAY_ENV, "not-a-date")
    before = world.revision
    res = world.run(capsys, base=before)
    assert (res.code, res.reason) == (2, "invalid-today")
    assert world.revision == before


def test_today_is_computed_once_so_a_midnight_crossing_cannot_split_the_line_from_the_gate(world, capsys,
                                                                                            monkeypatch):
    days = iter([datetime.date(2026, 11, 16), datetime.date(2026, 11, 17), datetime.date(2026, 11, 18)])
    monkeypatch.setattr(waiting_gate, "_today", lambda: next(days))

    res = world.run(capsys, answer="failed")

    assert (res.code, res.out.splitlines()[0]) == (0, "saved: AC2 failed")
    assert world.entry("AC2").history[-1][0] == "2026-11-16"


# --- the close ----------------------------------------------------------------


def test_a_passed_answer_on_a_criterion_lore_lists_as_closed_is_close_refused_and_leaves_the_spec(world, capsys):
    shown = world.show()
    closed = world.lore(["record", "amend", world.spec, "--vault", "default", "--close", "AC2", "--initiated-by",
                         "operator", "--reason", "closed by hand", "--base", shown["revision"]], shown["body"])
    assert closed.returncode == 0, closed.stderr
    before = world.revision

    res = world.run(capsys, answer="passed", base=before)

    assert (res.code, res.reason) == (1, "close-refused")
    assert world.revision == before


def test_a_passed_answer_on_a_criterion_no_slice_covered_is_close_refused(world, capsys):
    before = world.revision
    res = world.run(capsys, criterion="AC3", answer="passed", base=before)
    assert (res.code, res.reason) == (1, "close-refused")
    assert world.revision == before


def test_the_closed_criterion_is_the_matched_entrys_own_even_when_the_texts_name_another(tmp_path, monkeypatch,
                                                                                       capsys):
    monkeypatch.setenv(waiting_gate.TODAY_ENV, TODAY)
    named = World(tmp_path, entries=(pending("AC2", check="the banner (not AC3) shows 3"), pending("AC3")))

    res = named.run(capsys, answer="passed", words="AC3 also looks right; AC1 too")

    assert res.code == 0, res.err
    close_args = [a for argv in named.writes() for i, a in enumerate(argv) if argv[i - 1] == "--close"]
    assert close_args == ["AC2"]
    assert named.criteria()[0]["closed"] == ["AC2"]


def test_the_operators_words_never_reach_an_argv_and_the_reason_is_fixed_text(world, capsys):
    res = world.run(capsys, answer="passed", words="saw 3 on the banner")

    assert res.code == 0, res.err
    (argv,) = world.writes()
    assert argv[argv.index("--reason") + 1] == "check passed on the check list"
    assert argv[argv.index("--initiated-by") + 1] == "operator"
    assert not any("saw 3" in part for call, _ in world.lore.calls for part in call)
    amend_stdins = [stdin for call, stdin in world.lore.calls
                    if call[:2] == ["record", "amend"] and "--help" not in call]
    assert amend_stdins[0].count("saw 3") == 1


# --- lore support, layer and failures ----------------------------------------


@pytest.mark.parametrize(
    "help_argv, stdout, answer",
    [
        (["record", "amend", "--help"], "usage: lore record amend [--base REVISION]", "passed"),
        (["record", "amend", "--help"], "usage: lore record amend [--close ACn]", "passed"),
        (["record", "update", "--help"], "usage: lore record update [--title T]", "passed"),
        (["record", "amend", "--help"], "usage: lore record amend [--close ACn] [--base REVISION]", "failed"),
    ],
)
def test_a_lore_missing_a_flag_the_write_needs_is_too_old_before_any_read(world, capsys, help_argv, stdout, answer):
    if "--close" in stdout and "--base" in stdout:
        world.lore.inject(["record", "update", "--help"], returncode=0, stdout="usage: lore record update")
    world.lore.inject(help_argv, returncode=0, stdout=stdout)

    res = world.run(capsys, answer=answer)

    assert (res.code, res.reason) == (2, "lore-too-old")
    assert not any(argv[:3] == ["record", "show", "--json"] for argv, _ in world.lore.calls)
    assert world.writes() == []


def test_a_lore_that_cannot_describe_a_command_is_too_old(world, capsys):
    world.lore.inject(["record", "amend", "--help"], returncode=2,
                      stdout="usage: lore record amend [--close ACn] [--base REVISION]")
    res = world.run(capsys, answer="failed")
    assert (res.code, res.reason) == (2, "lore-too-old")


@pytest.mark.parametrize("failing", [["record", "update", "--help"], ["record", "show"]])
def test_a_lore_that_is_missing_or_hangs_leaves_the_spec_unreadable(world, capsys, failing):
    before = world.revision
    world.lore.inject(failing, raises=FileNotFoundError("lore"))
    res = world.run(capsys, base=before)
    assert (res.code, res.reason) == (2, "spec-unreadable")


def test_a_spec_that_is_not_there_is_unreadable(world, capsys):
    res = world.run(capsys, spec="spec/nope", base="a" * 64)
    assert (res.code, res.reason) == (2, "spec-unreadable")


def test_a_shared_layer_spec_is_refused_before_anything_is_composed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(waiting_gate.TODAY_ENV, TODAY)
    team = World(tmp_path, vault="team")
    before = team.revision
    make_shared(team.lore, "team")

    res = team.run(capsys, base=before)

    assert (res.code, res.reason) == (2, "shared-vault")
    assert team.writes() == []
    make_shared(team.lore, "none")
    assert team.revision == before


@pytest.mark.parametrize("answer, argv_head", [("failed", ["record", "update"]), ("passed", ["record", "amend"])])
def test_a_write_failure_is_write_failed_and_lores_text_appears_in_no_output(world, capsys, answer, argv_head):
    before = world.revision
    inject_write(world.lore, argv_head, returncode=1, stderr="LORE-INTERNAL-DETAIL /secret/path", stdout="LORE-STDOUT")

    res = world.run(capsys, answer=answer, base=before)

    assert (res.code, res.reason) == (2, "write-failed")
    assert "LORE-INTERNAL-DETAIL" not in res.out + res.err
    assert "LORE-STDOUT" not in res.out + res.err
    assert world.revision == before


def test_a_write_lore_refuses_with_an_already_closed_text_is_close_refused_only_when_the_spec_is_unchanged(world,
                                                                                                          capsys):
    inject_write(world.lore, ["record", "amend"], returncode=1, stderr="error: AC2 is already closed; nothing to do")
    res = world.run(capsys, answer="passed")
    assert (res.code, res.reason) == (1, "close-refused")


def test_a_refused_failed_write_is_never_close_refused_whatever_lore_says(world, capsys):
    inject_write(world.lore, ["record", "update"], returncode=1, stderr="error: AC2 is already closed; nothing to do")
    res = world.run(capsys, answer="failed")
    assert (res.code, res.reason) == (2, "write-failed")


# --- the words file -----------------------------------------------------------


@pytest.mark.parametrize(
    "scenario",
    ["saved", "credential", "write-failed", "unsafe-name", "stale", "lore-too-old", "entry-not-found"],
)
def test_the_words_file_is_removed_on_every_outcome(world, capsys, scenario):
    kwargs = {}
    if scenario == "credential":
        kwargs["words"] = f"it printed {SECRET}"
    if scenario == "write-failed":
        inject_write(world.lore, ["record", "update"], returncode=1)
    if scenario == "unsafe-name":
        kwargs["vault"] = "v;x"
    if scenario == "stale":
        kwargs["base"] = "0" * 64
    if scenario == "lore-too-old":
        world.lore.inject(["record", "amend", "--help"], returncode=0, stdout="usage: lore record amend")
    if scenario == "entry-not-found":
        kwargs["criterion"] = "AC9"
    base = kwargs.pop("base", None)

    res = world.run(capsys, base=base, **kwargs)

    assert (res.code == 0) == (scenario == "saved")
    assert not world.words_path.exists()


# --- two answers in turn ------------------------------------------------------


def revision_line(res):
    lines = res.out.splitlines()
    assert len(lines) == 2 and lines[1].startswith("revision: "), res.out
    return lines[1].removeprefix("revision: ")


def test_a_save_prints_the_specs_new_revision_on_a_second_line(world, capsys):
    before = world.revision

    res = world.run(capsys, answer="failed")

    assert res.code == 0, res.err
    assert revision_line(res) == world.revision
    assert revision_line(res) != before


def test_a_passed_save_prints_the_specs_new_revision_too(world, capsys):
    res = world.run(capsys, answer="passed", words="")

    assert res.code == 0, res.err
    assert revision_line(res) == world.revision


def test_a_save_whose_revision_re_read_fails_still_stands_with_no_revision_line(world, capsys):
    shows = []

    def runner(argv, stdin=None):
        if argv[:3] == ["record", "show", "--json"]:
            shows.append(argv)
            if len(shows) > 1:
                return subprocess.CompletedProcess(argv, 1, "", "lore went away")
        return world.lore(argv, stdin=stdin)

    res = world.run(capsys, runner=runner)

    assert (res.code, res.out) == (0, "saved: AC2 failed\n")
    assert world.entry("AC2").status == "failed"


def after_write(world, change):
    """A runner that applies `change` to the spec just after our write, before the script reads it again."""
    def runner(argv, stdin=None):
        done = world.lore(argv, stdin=stdin)
        if argv[:2] in (["record", "update"], ["record", "amend"]) and "--help" not in argv:
            world.edit(change)
        return done
    return runner


def test_a_save_another_writer_changed_before_the_re_read_prints_no_revision_line(world, capsys):
    res = world.run(capsys, runner=after_write(world, lambda b: b.rstrip("\n") + "\n\nAn unseen addition.\n"))

    assert (res.code, res.out) == (0, "saved: AC2 failed\n")


def test_a_save_that_gained_only_a_trailing_newline_still_prints_its_revision(world, capsys):
    res = world.run(capsys, runner=after_write(world, lambda b: b.rstrip("\n") + "\n\n"))

    assert res.code == 0, res.err
    assert revision_line(res) == world.revision


def test_a_write_lore_refused_because_another_writer_saved_an_unparsable_section_is_stale(world, capsys):
    def runner(argv, stdin=None):
        if argv[:2] in (["record", "update"], ["record", "amend"]) and "--help" not in argv:
            world.edit(lambda b: b + "\nloose prose in the section\n")
        return world.lore(argv, stdin=stdin)

    res = world.run(capsys, runner=runner)

    assert (res.code, res.reason) == (1, "stale")


def test_answering_two_entries_in_turn_with_the_returned_revisions_saves_both(world, capsys):
    first = world.run(capsys, criterion="AC2", answer="failed", words="no 3 on the banner")
    assert first.code == 0, first.err
    second = world.run(capsys, criterion="AC3", answer="failed", words="the report did not open",
                       base=revision_line(first))
    assert (second.code, second.out.splitlines()[0]) == (0, "saved: AC3 failed")
    assert world.entry("AC2").history[-1][1:] == ("failed", "no 3 on the banner")
    assert world.entry("AC3").history[-1][1:] == ("failed", "the report did not open")



# --- a write that landed ------------------------------------------------------


@pytest.mark.parametrize("answer, argv_head, words", [("failed", ["record", "update"], "it broke"),
                                                      ("passed", ["record", "amend"], "all good")])
def test_a_write_lore_committed_and_then_reported_failing_is_saved(world, capsys, answer, argv_head, words):
    inject_write(world.lore, argv_head, run_first=True, returncode=1, stderr="timed out after commit")

    res = world.run(capsys, answer=answer, words=words)

    assert (res.code, res.out.splitlines()[0]) == (0, f"saved: AC2 {answer}"), res.err
    assert revision_line(res) == world.revision
    assert world.entry("AC2").history[-1][1:] == (answer, words)


def test_a_landed_write_another_writer_changed_before_the_re_read_prints_no_revision_line(world, capsys):
    inject_write(world.lore, ["record", "update"], run_first=True, returncode=1, stderr="timed out after commit",
                 times=1)
    runner = after_write(world, lambda b: b.rstrip("\n") + "\n\n" + pending("AC1", slice_="other"))

    res = world.run(capsys, runner=runner, words="it broke")

    assert (res.code, res.out) == (0, "saved: AC2 failed\n"), res.err
    assert world.entry("AC2").history[-1][1:] == ("failed", "it broke")


def test_a_write_that_did_not_land_while_the_spec_moved_is_still_stale(world, capsys):
    shown = world.show()
    world.edit(lambda b: b.rstrip("\n") + "\n\n" + pending("AC1", slice_="other"))
    world.lore.inject(["record", "show"], returncode=0, stdout=json.dumps(shown), times=1)

    res = world.run(capsys, base=shown["revision"])

    assert (res.code, res.reason) == (1, "stale")


def test_a_second_answer_given_the_first_revision_is_stale(world, capsys):
    base = world.revision
    assert world.run(capsys, criterion="AC2", base=base).code == 0
    second = world.run(capsys, criterion="AC3", base=base, words="x")
    assert (second.code, second.reason) == (1, "stale")
    assert world.entry("AC3").status == "waiting"


# --- the words file is the one argparse read ----------------------------------


def run_argv(world, capsys, argv):
    world.lore.calls.clear()
    code = ca.main(argv, world.lore)
    cap = capsys.readouterr()
    return Result(code, cap.out, cap.err)


def answer_argv(world, *words_flags):
    return ["--vault", world.vault, "--spec", world.spec, "--criterion", "AC2", "--slice", f"task/{SLICE}",
            "--answer", "failed", "--base", world.revision, *words_flags]


def test_the_equals_form_of_the_words_flag_is_read_and_removed(world, capsys):
    world.words_path.write_text("banner showed 2", encoding="utf-8")

    res = run_argv(world, capsys, answer_argv(world, f"--words-file={world.words_path}"))

    assert (res.code, res.out.splitlines()[0]) == (0, "saved: AC2 failed")
    assert world.entry("AC2").history[-1][1:] == ("failed", "banner showed 2")
    assert not world.words_path.exists()


def test_a_repeated_words_flag_removes_the_file_that_was_read_and_only_that_one(world, capsys, tmp_path):
    first = tmp_path / "mktemp" / "tmp.first0001"
    first.write_text("from the first file", encoding="utf-8")
    world.words_path.write_text("from the last file", encoding="utf-8")

    res = run_argv(world, capsys, answer_argv(world, "--words-file", str(first), "--words-file",
                                              str(world.words_path)))

    assert res.code == 0, res.err
    assert world.entry("AC2").history[-1][1:] == ("failed", "from the last file")
    assert not world.words_path.exists()
    assert first.read_text(encoding="utf-8") == "from the first file"


@pytest.mark.parametrize("flag", ["--words", "--word"])
def test_an_abbreviated_words_flag_is_refused_as_invalid_arguments(world, capsys, flag):
    world.words_path.write_text("banner showed 2", encoding="utf-8")

    res = run_argv(world, capsys, answer_argv(world, flag, str(world.words_path)))

    assert (res.code, res.out, code_of(res.err)) == (2, "", "invalid-arguments")
    assert world.writes() == []


@pytest.mark.parametrize("form", ["split", "equals"])
def test_a_words_file_is_removed_when_the_arguments_do_not_parse(world, capsys, form):
    world.words_path.write_text("banner showed 2", encoding="utf-8")
    flag = ["--words-file", str(world.words_path)] if form == "split" else [f"--words-file={world.words_path}"]

    res = run_argv(world, capsys, ["--vault", world.vault, *flag])

    assert (res.code, code_of(res.err)) == (2, "invalid-arguments")
    assert not world.words_path.exists()


# --- a words file that cannot be removed ----------------------------------------


@pytest.fixture
def locked_words(world, tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root can remove a file from a read-only directory")
    locked = tmp_path / "locked"
    locked.mkdir()
    monkeypatch.setenv("TMPDIR", str(locked))
    world.words_path = locked / "tmp.words0001"
    yield world
    locked.chmod(0o700)


def lock(world):
    world.words_path.parent.chmod(0o500)


def test_a_save_stays_exit_0_when_the_words_file_cannot_be_removed(locked_words, capsys):
    world = locked_words
    world.words_path.write_text("banner showed 2", encoding="utf-8")
    lock(world)

    res = run_argv(world, capsys, answer_argv(world, "--words-file", str(world.words_path)))

    assert (res.code, res.out.splitlines()[0]) == (0, "saved: AC2 failed"), res.err
    assert world.entry("AC2").status == "failed"


def test_a_refusal_keeps_its_own_code_when_the_words_file_cannot_be_removed(locked_words, capsys):
    world = locked_words
    world.words_path.write_text("banner showed 2", encoding="utf-8")
    lock(world)
    argv = answer_argv(world, "--words-file", str(world.words_path))
    argv[argv.index("AC2")] = "AC9"

    res = run_argv(world, capsys, argv)

    assert (res.code, code_of(res.err)) == (1, "entry-not-found")


# --- the words file must be what mktemp made ----------------------------------


def refused_unread(world, capsys, path, argv=None):
    world.lore.calls.clear()
    res = run_argv(world, capsys, argv or answer_argv(world, "--words-file", str(path)))
    assert (res.code, res.out, code_of(res.err)) == (2, "", "invalid-arguments")
    assert world.writes() == []
    return res


def test_a_words_file_outside_the_mktemp_directory_is_refused_and_left_alone(world, capsys, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    outside = elsewhere / "tmp.outside01"
    outside.write_text("banner showed 2", encoding="utf-8")

    refused_unread(world, capsys, outside)

    assert outside.read_text(encoding="utf-8") == "banner showed 2"


def test_a_symlink_in_the_mktemp_directory_is_refused_and_neither_link_nor_target_is_removed(
    world, capsys, tmp_path, mktemp_dir
):
    target = tmp_path / "gitconfig"
    target.write_text("[user]\n", encoding="utf-8")
    link = mktemp_dir / "tmp.link0001"
    link.symlink_to(target)

    refused_unread(world, capsys, link)

    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "[user]\n"


@pytest.mark.parametrize("name", ["words.txt", "tmp.abc", "tmp.abc_def1", "xtmp.abcdef12", "tmp.abcdef12.bak"])
def test_a_words_file_not_named_the_way_mktemp_names_it_is_refused_and_left_alone(world, capsys, mktemp_dir, name):
    odd = mktemp_dir / name
    odd.write_text("banner showed 2", encoding="utf-8")

    refused_unread(world, capsys, odd)

    assert odd.read_text(encoding="utf-8") == "banner showed 2"


def test_a_words_file_named_like_mktemps_shortest_name_is_read(world, capsys, mktemp_dir):
    world.words_path = mktemp_dir / "tmp.Ab3Xy9"

    res = world.run(capsys)

    assert res.code == 0, res.err
    assert not world.words_path.exists()


def test_a_fifo_in_the_mktemp_directory_is_refused_without_being_opened(world, capsys, mktemp_dir):
    fifo = mktemp_dir / "tmp.fifo0001"
    os.mkfifo(fifo)

    def hung(*_):
        raise AssertionError("the script blocked opening the FIFO")

    signal.signal(signal.SIGALRM, hung)
    signal.alarm(10)
    try:
        refused_unread(world, capsys, fifo)
    finally:
        signal.alarm(0)
    assert fifo.exists()


def test_a_directory_named_like_a_words_file_is_refused_and_left_alone(world, capsys, mktemp_dir):
    directory = mktemp_dir / "tmp.dir00001"
    directory.mkdir()

    refused_unread(world, capsys, directory)

    assert directory.is_dir()


def test_a_words_file_owned_by_someone_else_is_refused_and_left_alone(world, capsys, monkeypatch):
    world.words_path.write_text("banner showed 2", encoding="utf-8")
    monkeypatch.setattr(ca.os, "geteuid", lambda: os.stat(world.words_path).st_uid + 1)

    refused_unread(world, capsys, world.words_path)

    assert world.words_path.read_text(encoding="utf-8") == "banner showed 2"


@pytest.mark.parametrize("unset", [True, False])
def test_an_unset_or_empty_tmpdir_means_slash_tmp(world, capsys, monkeypatch, unset):
    if unset:
        monkeypatch.delenv("TMPDIR")
    else:
        monkeypatch.setenv("TMPDIR", "")
    name = f"tmp.{uuid.uuid4().hex}"
    in_tmp = Path("/tmp") / name
    in_tmp.write_text("banner showed 2", encoding="utf-8")
    try:
        res = run_argv(world, capsys, answer_argv(world, "--words-file", str(in_tmp)))
        assert (res.code, res.out.splitlines()[0]) == (0, "saved: AC2 failed"), res.err
        assert not in_tmp.exists()
    finally:
        in_tmp.unlink(missing_ok=True)


def test_a_tmpdir_reached_through_a_symlink_is_compared_after_resolving_it(world, capsys, tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "alias").symlink_to(real)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "alias"))
    words = real / "tmp.alias001"
    words.write_text("banner showed 2", encoding="utf-8")

    res = run_argv(world, capsys, answer_argv(world, "--words-file", str(words)))

    assert res.code == 0, res.err
    assert not words.exists()


def test_an_unparseable_argv_naming_an_outside_path_removes_nothing(world, capsys, tmp_path):
    outside = tmp_path / "keep.txt"
    outside.write_text("precious", encoding="utf-8")

    res = run_argv(world, capsys, ["--vault", world.vault, "--words-file", str(outside)])

    assert (res.code, code_of(res.err)) == (2, "invalid-arguments")
    assert outside.read_text(encoding="utf-8") == "precious"


def test_a_garbage_base_with_an_outside_words_file_removes_nothing(world, capsys, tmp_path):
    outside = tmp_path / "keep.txt"
    outside.write_text("precious", encoding="utf-8")
    argv = answer_argv(world, "--words-file", str(outside))
    argv[argv.index("--base") + 1] = "0" * 64

    res = run_argv(world, capsys, argv)

    assert res.code != 0
    assert outside.read_text(encoding="utf-8") == "precious"
