"""found_work.py: the decision pipeline and the report, driven with an injected
`lore` runner (a fake session log). The seam file drives the real lore CLI."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location("found_work_under_test", _SCRIPTS / "found_work.py")
fw = importlib.util.module_from_spec(_spec)
sys.modules["found_work_under_test"] = fw
_spec.loader.exec_module(fw)

import criterion_gate  # noqa: E402

SECRET = "AKIAABCDEFGHIJKLMNOP"
SID = "11111111-2222-3333-4444-555555555555"


class FakeLore:
    """Answers `lore session candidate` / `lore session show` over an in-memory log
    shaped like lore's session record. `events` is shared with write callbacks so
    tests can assert order."""

    def __init__(self, events=None, fail_kinds=(), write_then_fail_kinds=(), show_rc=0, show_err=""):
        self.events = events if events is not None else []
        self.calls = []
        self.entries = []  # (kind, body)
        self.fail_kinds = set(fail_kinds)
        self.write_then_fail_kinds = set(write_then_fail_kinds)
        self.show_rc = show_rc
        self.show_err = show_err

    def log(self):
        out = "# session: x\n\n"
        for kind, body in self.entries:
            out += f"- candidate 2026-11-07T00:00:00Z kind={kind} phase=Build\n"
            out += "".join(f"  {line}\n" for line in body.splitlines())
        return out

    def bodies(self, kind):
        return [b for k, b in self.entries if k == kind]

    def __call__(self, argv, stdin=None):
        self.calls.append((list(argv), stdin))
        if argv[:2] == ["session", "candidate"]:
            kind = argv[argv.index("--kind") + 1]
            assert argv[argv.index("--phase") + 1] == "Build"
            self.events.append(f"capture:{kind}")
            if kind in self.fail_kinds:
                return subprocess.CompletedProcess(argv, 1, "", "error: no session key")
            self.entries.append((kind, stdin))
            if kind in self.write_then_fail_kinds:
                return subprocess.CompletedProcess(argv, 1, "", "error: index failed")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["session", "show"]:
            if self.show_rc:
                return subprocess.CompletedProcess(argv, self.show_rc, "", self.show_err)
            return subprocess.CompletedProcess(argv, 0, self.log(), "")
        raise AssertionError(f"unexpected lore call {argv}")


def decision(**over):
    doc = {
        "decision": "folded",
        "parent": "task/X",
        "spec": "spec/S",
        "finding": {
            "title": "Handle the empty case",
            "what": "it crashes on empty input",
            "where": ["src/a.py"],
            "found": "during review",
            "security": False,
        },
        "reason": "it is part of this change",
    }
    finding_over = over.pop("finding", {})
    doc["finding"] = {**doc["finding"], **finding_over}
    doc.update(over)
    return doc


def run_decide(doc, lore, capsys, *extra, handlers=None):
    stdin = doc if isinstance(doc, str) else json.dumps(doc)
    code = fw.main(["decide", *extra], stdin=stdin, runner=lore, handlers=handlers)
    out = capsys.readouterr()
    return code, out.out, out.err


def run_report(parent, lore, capsys, *extra):
    code = fw.main(["report", "--parent", parent, *extra], runner=lore)
    out = capsys.readouterr()
    return code, out.out, out.err


def reason_of(err):
    lines = [ln for ln in err.splitlines() if ln.startswith("reason-code: ")]
    assert len(lines) == 1, err
    return lines[0][len("reason-code: "):]


DECISION_LINE = (
    "found-work: folded | parent: task/X | record: task/X | "
    "finding: Handle the empty case | where: src/a.py | why: it is part of this change"
)
RESULT_LINE = (
    "found-work-result: written | parent: task/X | finding: Handle the empty case | "
    "record: task/X | reason-code: none"
)


# --- the shared pipeline: folded / dropped end to end -------------------------


def test_folded_captures_one_decision_line_and_one_written_result_line(capsys):
    lore = FakeLore()
    code, out, err = run_decide(decision(), lore, capsys)
    assert code == 0, err
    assert lore.bodies("found-work") == [DECISION_LINE]
    assert lore.bodies("found-work-result") == [RESULT_LINE]
    assert out.strip() == RESULT_LINE
    assert all(argv[0] == "session" for argv, _ in lore.calls)


def test_session_id_is_passed_to_every_capture_and_to_the_read(capsys):
    lore = FakeLore()
    code, _, err = run_decide(decision(), lore, capsys, "--session-id", SID)
    assert code == 0, err
    for argv, _ in lore.calls:
        assert argv[argv.index("--session-id") + 1] == SID
    assert {a[1] for a, _ in lore.calls} == {"show", "candidate"}


def test_without_a_session_id_no_flag_is_passed(capsys):
    lore = FakeLore()
    run_decide(decision(), lore, capsys)
    assert all("--session-id" not in argv for argv, _ in lore.calls)


def test_dropped_with_a_reason_is_captured(capsys):
    lore = FakeLore()
    code, _, err = run_decide(decision(decision="dropped", reason="not worth it"), lore, capsys)
    assert code == 0, err
    assert lore.bodies("found-work")[0].startswith("found-work: dropped | parent: task/X")
    assert lore.bodies("found-work")[0].endswith("why: not worth it")


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_dropped_without_a_reason_is_refused_and_nothing_is_captured(capsys, reason):
    lore = FakeLore()
    doc = decision(decision="dropped")
    if reason is None:
        del doc["reason"]
    else:
        doc["reason"] = reason
    code, _, err = run_decide(doc, lore, capsys)
    assert code == 1
    assert reason_of(err) == "drop-without-reason"
    assert lore.entries == []


@pytest.mark.parametrize("spec", [None, "", "  "])
def test_a_decision_with_no_spec_is_refused_and_nothing_is_captured(capsys, spec):
    lore = FakeLore()
    doc = decision()
    if spec is None:
        del doc["spec"]
    else:
        doc["spec"] = spec
    code, _, err = run_decide(doc, lore, capsys)
    assert code == 1
    assert reason_of(err) == "no-spec"
    assert lore.entries == []


@pytest.mark.parametrize("bad", ["not json", "[]", '{"decision": "folded"}', json.dumps(decision(decision="nope"))])
def test_malformed_input_is_refused(capsys, bad):
    lore = FakeLore()
    code, _, err = run_decide(bad, lore, capsys)
    assert code == 1
    assert reason_of(err) == "malformed-input"
    assert lore.entries == []


# --- capture failure ----------------------------------------------------------


@pytest.mark.parametrize("mode", ["fails", "writes-then-fails", "raises"])
def test_a_failed_decision_capture_exits_2_writes_nothing_and_prints_the_scrubbed_finding(capsys, mode):
    events = []
    if mode == "fails":
        lore = FakeLore(events, fail_kinds={"found-work"})
    elif mode == "writes-then-fails":
        lore = FakeLore(events, write_then_fail_kinds={"found-work"})
    else:

        def lore(argv, stdin=None):
            if argv[:2] == ["session", "show"]:
                return subprocess.CompletedProcess(argv, 1, "", "no session record resolved")
            raise FileNotFoundError("lore")

    def write(d, r, rec):
        events.append("write")
        return fw.WriteResult(rec)

    handlers = {"folded": fw.Handler(prepare=lambda d, r: d.parent, write=write)}
    doc = decision(
        finding={"title": f"T {SECRET}", "what": f"W {SECRET}", "found": f"F {SECRET}", "where": ["src/a.py"]},
        reason=f"R {SECRET}",
    )
    code, out, err = run_decide(doc, lore, capsys, handlers=handlers)
    assert code == 2
    assert reason_of(err) == "capture-failed"
    assert "write" not in events
    assert "capture:found-work-result" not in events
    assert "T [redacted]" in out and "W [redacted]" in out and "F [redacted]" in out
    assert "R [redacted]" in out and "src/a.py" in out
    assert SECRET not in out + err


# --- order --------------------------------------------------------------------


def test_the_decision_is_captured_before_the_write_and_the_result_after_it(capsys):
    events = []
    lore = FakeLore(events)

    def prepare(d, r):
        events.append("prepare")
        return d.parent

    def write(d, r, rec):
        events.append("write")
        return fw.WriteResult("task/new")

    handlers = {"folded": fw.Handler(prepare=prepare, write=write)}
    code, out, err = run_decide(decision(), lore, capsys, handlers=handlers)
    assert code == 0, err
    assert events == ["prepare", "capture:found-work", "write", "capture:found-work-result"]
    assert "record: task/new" in lore.bodies("found-work-result")[0]


def test_a_failed_write_is_logged_as_a_failed_result_and_exits_3(capsys):
    lore = FakeLore()
    handlers = {
        "folded": fw.Handler(
            prepare=lambda d, r: d.parent,
            write=lambda d, r, rec: fw.WriteResult(rec, reason_code="shared-layer-prior"),
        )
    }
    code, out, _ = run_decide(decision(), lore, capsys, handlers=handlers)
    assert code == 3
    assert lore.bodies("found-work-result")[0].startswith("found-work-result: failed | parent: task/X")
    assert lore.bodies("found-work-result")[0].endswith("reason-code: shared-layer-prior")
    assert out.strip() == lore.bodies("found-work-result")[0]


def test_a_failed_result_capture_exits_2_and_prints_the_result_line(capsys):
    lore = FakeLore(fail_kinds={"found-work-result"})
    code, out, err = run_decide(decision(), lore, capsys)
    assert code == 2
    assert reason_of(err) == "capture-failed"
    assert out.strip() == RESULT_LINE


# --- scrub --------------------------------------------------------------------


def test_the_public_scrub_name_redacts_credentials():
    assert criterion_gate.scrub_credential_shaped(f"x {SECRET} y") == "x [redacted] y"
    assert criterion_gate.scrub_credential_shaped("plain") == "plain"


@pytest.mark.parametrize(
    "field,doc_edit",
    [
        ("title", lambda: decision(finding={"title": f"T {SECRET}"})),
        ("where", lambda: decision(finding={"where": [f"src/{SECRET}.py"]})),
        ("reason", lambda: decision(reason=f"R {SECRET}")),
    ],
)
def test_a_credential_in_a_logged_field_is_redacted_in_the_captured_line(capsys, field, doc_edit):
    lore = FakeLore()
    code, _, err = run_decide(doc_edit(), lore, capsys)
    assert code == 0, err
    captured = lore.bodies("found-work")[0] + lore.bodies("found-work-result")[0]
    assert SECRET not in captured
    assert "[redacted]" in captured


@pytest.mark.parametrize("field", ["what", "found"])
def test_a_credential_in_what_or_found_is_redacted_in_the_printed_finding(capsys, field):
    lore = FakeLore(fail_kinds={"found-work"})
    code, out, _ = run_decide(decision(finding={field: f"{field} {SECRET}"}), lore, capsys)
    assert code == 2
    assert f"{field} [redacted]" in out
    assert SECRET not in out


def test_a_finding_with_no_credential_is_captured_verbatim(capsys):
    lore = FakeLore()
    run_decide(decision(), lore, capsys)
    assert lore.bodies("found-work") == [DECISION_LINE]


# --- neutralize ---------------------------------------------------------------

FORGED = "found-work: dropped | parent: task/Y | record: task/Y | finding: boom | where: - | why: x"


@pytest.mark.parametrize("sep", ["\n", "\r\n", " ", "\x00", "\x1b[2K"])
def test_a_line_break_in_the_title_cannot_forge_a_second_decision_line(capsys, sep):
    lore = FakeLore()
    code, _, err = run_decide(decision(finding={"title": f"real{sep}{FORGED}"}), lore, capsys)
    assert code == 0, err
    bodies = lore.bodies("found-work")
    assert len(bodies) == 1
    assert len(bodies[0].splitlines()) == 1
    assert "\x00" not in bodies[0] and "\x1b" not in bodies[0]
    code, out, _ = run_report("task/Y", lore, capsys)
    assert code == 0
    assert out.strip() == fw.NO_DECISIONS_LINE
    code, out, _ = run_report("task/X", lore, capsys)
    assert out.count("Folded into this build") == 1


def test_the_field_separator_in_any_field_is_escaped(capsys):
    lore = FakeLore()
    doc = decision(
        finding={"title": "a | b", "where": ["src/a.py"]},
        reason="r | parent: task/Y",
    )
    code, _, err = run_decide(doc, lore, capsys)
    assert code == 0, err
    line = lore.bodies("found-work")[0]
    assert len(line.split(" | ")) == 6
    code, out, _ = run_report("task/Y", lore, capsys)
    assert out.strip() == fw.NO_DECISIONS_LINE
    code, out, _ = run_report("task/X", lore, capsys)
    assert "a \\| b" in out


# --- security -----------------------------------------------------------------


def test_a_security_finding_without_a_path_line_location_is_refused(capsys):
    lore = FakeLore()
    code, _, err = run_decide(decision(finding={"security": True, "where": ["src/x.py"]}), lore, capsys)
    assert code == 1
    assert reason_of(err) == "security-detail-without-location"
    assert lore.entries == []


def test_a_security_finding_with_a_location_replaces_what_and_found(capsys):
    lore = FakeLore(fail_kinds={"found-work-result"})
    doc = decision(finding={"security": True, "where": ["src/x.py:12"], "what": "SQLI-DETAIL", "found": "EXPLOIT-PATH"})
    code, out, err = run_decide(doc, lore, capsys)
    assert code == 2
    assert "src/x.py:12" in lore.bodies("found-work")[0]
    assert "SQLI-DETAIL" not in lore.log() + out + err
    assert "EXPLOIT-PATH" not in lore.log() + out + err


def test_a_security_finding_replaced_text_reaches_the_failure_print(capsys):
    lore = FakeLore(fail_kinds={"found-work"})
    doc = decision(finding={"security": True, "where": ["src/x.py:12"], "what": "SQLI-DETAIL", "found": "EXPLOIT-PATH"})
    code, out, _ = run_decide(doc, lore, capsys)
    assert code == 2
    assert "SQLI-DETAIL" not in out and "EXPLOIT-PATH" not in out
    assert "security finding — see location" in out


# --- paths and ids ------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd", "../x.py", "a/../b.py", "-rf", "a\x00b.py", "a\\b.py",
        " /etc/passwd", "\t-rf", "\u200b../secret", "\u200b/etc/passwd", "src/a.py ", "a|b.py",
    ],
    ids=[
        "absolute", "dotdot-leading", "dotdot-inner", "leading-dash", "nul", "backslash",
        "padded-absolute", "tab-dash", "zero-width-dotdot", "zero-width-absolute", "trailing-space", "pipe",
    ],
)
def test_a_where_path_outside_the_repository_is_refused(capsys, path):
    lore = FakeLore()
    code, _, err = run_decide(decision(finding={"where": ["src/ok.py", path]}), lore, capsys)
    assert code == 1
    assert reason_of(err) == "path-outside-repository"
    assert lore.entries == []


def test_a_plain_relative_path_is_accepted(capsys):
    lore = FakeLore()
    code, _, err = run_decide(decision(finding={"where": ["src/a.py", "docs/b.md:3"]}), lore, capsys)
    assert code == 0, err
    assert "where: src/a.py, docs/b.md:3" in lore.bodies("found-work")[0]


@pytest.mark.parametrize("field", ["parent", "spec"])
@pytest.mark.parametrize("bad", ["task/a b", 'task/a"b', "task/a'b", "task/a;b"])
def test_an_id_with_a_space_or_quote_is_refused(capsys, field, bad):
    lore = FakeLore()
    code, _, err = run_decide(decision(**{field: bad}), lore, capsys)
    assert code == 1
    assert reason_of(err) == "unsafe-id"
    assert lore.entries == []


def test_every_emitted_reason_code_is_in_the_exported_collection(capsys):
    seen = set()
    for doc in (
        decision(spec=""),
        decision(parent="a b"),
        decision(finding={"where": ["/x"]}),
        decision(decision="dropped", reason=""),
        decision(finding={"security": True}),
        "garbage",
    ):
        _, _, err = run_decide(doc, FakeLore(), capsys)
        seen.add(reason_of(err))
    _, _, err = run_decide(decision(), FakeLore(fail_kinds={"found-work"}), capsys)
    seen.add(reason_of(err))
    assert len(seen) == 7
    assert seen <= set(fw.REASON_CODES)


# --- idempotency --------------------------------------------------------------


def test_the_same_decision_twice_captures_once_and_prints_the_first_result(capsys):
    lore = FakeLore()
    run_decide(decision(), lore, capsys)
    code, out, err = run_decide(decision(), lore, capsys)
    assert code == 0, err
    assert len(lore.bodies("found-work")) == 1
    assert len(lore.bodies("found-work-result")) == 1
    assert out.strip() == RESULT_LINE


@pytest.mark.parametrize(
    "other",
    [
        decision(finding={"title": "A different finding"}),
        decision(finding={"where": ["src/other.py"]}),
        decision(parent="task/Z"),
    ],
    ids=["title", "where", "parent"],
)
def test_a_decision_differing_in_title_where_or_parent_is_not_a_retry(capsys, other):
    lore = FakeLore()
    run_decide(decision(), lore, capsys)
    code, _, err = run_decide(other, lore, capsys)
    assert code == 0, err
    assert len(lore.bodies("found-work")) == 2
    assert len(lore.bodies("found-work-result")) == 2


def test_a_missing_session_record_reads_as_an_empty_log(capsys):
    lore = FakeLore(show_rc=1, show_err="lore session show: no session record resolved.")
    code, _, err = run_decide(decision(), lore, capsys)
    assert code == 0, err


# --- report -------------------------------------------------------------------


def seed(
    lore, decision_name, parent, title, record,
    why="because", where="src/a.py", result=None, result_record=None, code="none",
):
    lore.entries.append(
        (
            "found-work",
            f"found-work: {decision_name} | parent: {parent} | record: {record}"
            f" | finding: {title} | where: {where} | why: {why}",
        )
    )
    if result:
        lore.entries.append(
            (
                "found-work-result",
                f"found-work-result: {result} | parent: {parent} | finding: {title}"
                f" | record: {result_record or record} | reason-code: {code}",
            )
        )


def test_report_prints_only_the_parents_decisions_one_sentence_each(capsys):
    lore = FakeLore()
    seed(lore, "folded", "task/A", "Fold me", "task/A", why="it is in scope", result="written")
    seed(lore, "dropped", "task/A", "Drop me", "task/A", why="not worth it", result="written")
    seed(lore, "deferred", "task/A", "Defer me", "spec/S#F1", why="too big", result="written")
    seed(lore, "separate", "task/A", "Split me", "task/A", why="elsewhere", result="written", result_record="task/new")
    seed(lore, "folded", "task/B", "Not mine", "task/B", result="written")
    code, out, _ = run_report("task/A", lore, capsys)
    assert code == 0
    assert out.splitlines() == [
        "Folded into this build: Fold me, because it is in scope.",
        "Dropped: Drop me, because not worth it.",
        "Deferred to spec/S#F1: Defer me, because too big.",
        "Made a separate task task/new: Split me, because elsewhere.",
    ]


def test_report_with_no_decisions_for_the_parent_prints_the_single_line(capsys):
    lore = FakeLore()
    seed(lore, "folded", "task/B", "Not mine", "task/B", result="written")
    code, out, _ = run_report("task/A", lore, capsys)
    assert code == 0
    assert out.splitlines() == [fw.NO_DECISIONS_LINE]


def test_report_matches_the_parent_exactly(capsys):
    lore = FakeLore()
    seed(lore, "folded", "task/AB", "Prefix sibling", "task/AB", result="written")
    _, out, _ = run_report("task/A", lore, capsys)
    assert out.splitlines() == [fw.NO_DECISIONS_LINE]


def test_report_lists_failed_results_first_marked_not_written_with_what_to_do(capsys):
    lore = FakeLore()
    seed(lore, "folded", "task/A", "Fold me", "task/A", result="written")
    seed(lore, "deferred", "task/A", "Defer me", "spec/S#F1", why="too big", result="failed", code="shared-layer-prior")
    code, out, _ = run_report("task/A", lore, capsys)
    lines = out.splitlines()
    assert code == 0
    assert lines[0] == fw.NEEDS_OPERATOR_HEADING
    assert lines[1].startswith("Not written: Defer me")
    assert "shared vault" in lines[1]
    assert lines[2] == ""
    assert lines[3] == "Folded into this build: Fold me, because because."
    assert lines[4:] == ["Deferred to spec/S#F1: Defer me, because too big."] or lines[4:] == []


def test_report_lists_a_brainstorm_task_with_its_command_under_the_heading(capsys):
    lore = FakeLore()
    seed(
        lore, "brainstorm", "task/A", "Open question", "task/A",
        why="too open", result="written", result_record="task/new-one",
    )
    _, out, _ = run_report("task/A", lore, capsys)
    lines = out.splitlines()
    assert lines[0] == fw.NEEDS_OPERATOR_HEADING
    assert lines[1] == "Needs a brainstorm: Open question. Run `/craft:brainstorm task/new-one`."
    assert "Needs a brainstorm: Open question, because too open." in lines[3:]


def test_report_never_prints_a_command_for_an_unsafe_record_id(capsys):
    lore = FakeLore()
    seed(lore, "brainstorm", "task/A", "Open question", "task/A", result="written", result_record="task/x; rm -rf /")
    _, out, _ = run_report("task/A", lore, capsys)
    assert "rm -rf" not in out


def test_report_ignores_lines_that_are_not_well_formed_or_not_candidates_of_that_kind(capsys):
    lore = FakeLore()
    lore.entries.append(("found-work", "found-work: folded | parent: task/A | finding: missing fields"))
    lore.entries.append(
        ("task", "found-work: dropped | parent: task/A | record: task/A | finding: sneaky | where: - | why: x")
    )
    _, out, _ = run_report("task/A", lore, capsys)
    assert out.splitlines() == [fw.NO_DECISIONS_LINE]


def test_report_reads_the_session_the_caller_names(capsys):
    lore = FakeLore()
    run_report("task/A", lore, capsys, "--session-id", SID)
    assert lore.calls[0][0] == ["session", "show", "--session-id", SID]


def test_report_refuses_an_unsafe_parent(capsys):
    code, _, err = run_report("task/a b", FakeLore(), capsys)
    assert code == 1
    assert reason_of(err) == "unsafe-id"


def test_report_with_an_unreadable_session_does_not_claim_there_were_no_decisions(capsys):
    lore = FakeLore(show_rc=1, show_err="error: vault config unreadable")
    code, out, err = run_report("task/A", lore, capsys)
    assert code == 2
    assert reason_of(err) == "session-unreadable"
    assert out == ""


def test_report_over_a_session_that_does_not_exist_yet_says_no_decisions(capsys):
    lore = FakeLore(show_rc=1, show_err="lore session show: no session record resolved.\n")
    code, out, _ = run_report("task/A", lore, capsys)
    assert code == 0
    assert out.splitlines() == [fw.NO_DECISIONS_LINE]


def test_a_logged_decision_with_no_result_is_not_a_retry_and_gets_a_fresh_result(capsys):
    lore = FakeLore()
    lore.entries.append(("found-work", DECISION_LINE))
    code, out, err = run_decide(decision(), lore, capsys)
    assert code == 0, err
    assert out.strip() == RESULT_LINE
    assert len(lore.bodies("found-work-result")) == 1


def test_report_gives_the_failed_write_what_to_do_not_only_what_happened(capsys):
    lore = FakeLore()
    seed(lore, "deferred", "task/A", "Defer me", "spec/S#F1", result="failed", code="shared-layer-prior")
    seed(lore, "deferred", "task/A", "Other", "spec/S#F2", result="failed", code="some-unlisted-code")
    _, out, _ = run_report("task/A", lore, capsys)
    lines = out.splitlines()
    assert "Flush will file it as a task" in lines[1]
    assert "Flush will file it as a task" in lines[2]


def test_report_gives_no_brainstorm_command_for_a_result_that_is_not_a_task(capsys):
    lore = FakeLore()
    seed(lore, "brainstorm", "task/A", "Open question", "task/A", result="written", result_record="spec/S#F1")
    _, out, _ = run_report("task/A", lore, capsys)
    assert "/craft:brainstorm" not in out


def test_report_does_not_trust_a_result_line_whose_record_is_malformed(capsys):
    lore = FakeLore()
    seed(lore, "separate", "task/A", "Split me", "task/A", result="written", result_record="task/x; rm -rf /")
    _, out, _ = run_report("task/A", lore, capsys)
    assert "rm -rf" not in out
    assert out.splitlines()[0] == fw.NEEDS_OPERATOR_HEADING
    assert out.splitlines()[1].startswith("Result not logged: Split me")


def test_report_ignores_decision_and_result_lines_with_extra_fields(capsys):
    lore = FakeLore()
    lore.entries.append(
        (
            "found-work",
            "found-work: folded | parent: task/A | record: task/A | finding: T | where: - | why: x | extra: y",
        )
    )
    lore.entries.append(
        ("found-work", "found-work: folded | parent: task/A | record: task/A | finding: T2 | where: - | why: x")
    )
    lore.entries.append(
        (
            "found-work-result",
            "found-work-result: failed | parent: task/A | finding: T2 | record: task/A"
            " | reason-code: a-b | extra: z",
        )
    )
    _, out, _ = run_report("task/A", lore, capsys)
    assert out.splitlines() == ["Folded into this build: T2, because x."]


# --- retry after a failed write -------------------------------------------------


def flaky_handlers(outcomes):
    """A `deferred` handler whose write returns the next outcome each call."""
    calls = []

    def write(d, r, rec):
        calls.append(d.title)
        return outcomes.pop(0)

    return {"deferred": fw.Handler(prepare=lambda d, r: "spec/S#F1", write=write)}, calls


def test_a_retry_after_a_failed_write_writes_again_and_exits_0(capsys):
    lore = FakeLore()
    handlers, calls = flaky_handlers([fw.WriteResult("spec/S#F1", "stale-base"), fw.WriteResult("spec/S#F1")])
    doc = decision(decision="deferred")
    code, _, _ = run_decide(doc, lore, capsys, handlers=handlers)
    assert code == 3
    code, out, err = run_decide(doc, lore, capsys, handlers=handlers)
    assert code == 0, err
    assert len(calls) == 2
    assert out.startswith("found-work-result: written | parent: task/X")
    assert len(lore.bodies("found-work-result")) == 2


def test_a_written_result_is_replayed_only_for_the_same_decision_kind(capsys):
    lore = FakeLore()
    handlers, calls = flaky_handlers([fw.WriteResult("spec/S#F1")])
    handlers["dropped"] = fw.HANDLERS["dropped"]
    run_decide(decision(decision="deferred"), lore, capsys, handlers=handlers)
    code, out, err = run_decide(decision(decision="dropped"), lore, capsys, handlers=handlers)
    assert code == 0, err
    assert [b.split(" | ")[0] for b in lore.bodies("found-work")] == ["found-work: deferred", "found-work: dropped"]
    assert len(calls) == 1


def test_the_report_after_a_recovered_retry_shows_the_finding_once_and_not_under_the_operator(capsys):
    lore = FakeLore()
    handlers, _ = flaky_handlers([fw.WriteResult("spec/S#F1", "stale-base"), fw.WriteResult("spec/S#F1")])
    doc = decision(decision="deferred", finding={"title": "Defer me"})
    run_decide(doc, lore, capsys, handlers=handlers)
    run_decide(doc, lore, capsys, handlers=handlers)
    _, out, _ = run_report("task/X", lore, capsys)
    assert fw.NEEDS_OPERATOR_HEADING not in out
    assert out.count("Defer me") == 1
    assert out.startswith("Deferred to spec/S#F1: Defer me")
    assert "discard it at flush" in out


def test_a_retried_folded_decision_is_reported_once(capsys):
    lore = FakeLore()
    seed(lore, "folded", "task/X", "Fold me", "task/X", result=None)
    seed(lore, "folded", "task/X", "Fold me", "task/X", result="written")
    _, out, _ = run_report("task/X", lore, capsys)
    assert out.count("Folded into this build: Fold me") == 1
    assert fw.NEEDS_OPERATOR_HEADING not in out


def test_a_decision_whose_result_was_lost_and_then_retried_leaves_no_stale_item(capsys):
    lore = FakeLore()
    seed(lore, "deferred", "task/X", "Defer me", "spec/S#F1", result=None)
    seed(lore, "deferred", "task/X", "Defer me", "spec/S#F1", result="written")
    _, out, _ = run_report("task/X", lore, capsys)
    assert "Result not logged" not in out and fw.NEEDS_OPERATOR_HEADING not in out


def test_a_failed_write_that_was_never_retried_stays_under_the_operator_and_says_flush_files_it(capsys):
    lore = FakeLore()
    seed(lore, "deferred", "task/X", "Defer me", "spec/S#F1", result="failed", code="stale-base")
    _, out, _ = run_report("task/X", lore, capsys)
    assert out.startswith(fw.NEEDS_OPERATOR_HEADING)
    assert "Flush will file it as a task" in out


# --- security findings carry a location only ------------------------------------

SEC_TITLE = "SQL injection via ' OR 1=1 -- in name param"
SEC_REASON = "attacker sends ' OR 1=1 -- to the name param"


def security_doc(kind="deferred"):
    return decision(
        decision=kind,
        reason=SEC_REASON,
        finding={
            "title": SEC_TITLE, "security": True, "where": ["export/csv.py:10"],
            "what": "SQLI-DETAIL", "found": "EXPLOIT-PATH",
        },
    )


def test_a_security_finding_logs_neither_its_title_nor_its_reason(capsys):
    lore = FakeLore()
    handlers, _ = flaky_handlers([fw.WriteResult("spec/S#F1")])
    code, out, err = run_decide(security_doc(), lore, capsys, handlers=handlers)
    assert code == 0, err
    _, rep, _ = run_report("task/X", lore, capsys)
    everything = lore.log() + out + err + rep
    for leaked in ("injection", "OR 1=1", "attacker", "name param"):
        assert leaked not in everything
    assert "Security finding at export/csv.py:10" in lore.bodies("found-work")[0]


def test_a_security_finding_keeps_the_task_body_free_of_its_title_and_reason(capsys):
    lore = FakeLore()
    seen = {}

    def write(d, r, rec):
        seen["body"] = fw._task_body(d, fw._found_work_key(d), False)
        seen["title"] = d.title
        return fw.WriteResult("task/new")

    handlers = {"separate": fw.Handler(prepare=lambda d, r: d.parent, write=write)}
    code, _, err = run_decide(security_doc("separate"), lore, capsys, handlers=handlers)
    assert code == 0, err
    assert "injection" not in seen["body"] + seen["title"] and "attacker" not in seen["body"]


def test_a_security_finding_retry_is_still_idempotent(capsys):
    lore = FakeLore()
    run_decide(security_doc("folded"), lore, capsys)
    code, _, err = run_decide(security_doc("folded"), lore, capsys)
    assert code == 0, err
    assert len(lore.bodies("found-work")) == 1


def test_a_dropped_security_finding_still_satisfies_drop_without_reason(capsys):
    lore = FakeLore()
    code, out, err = run_decide(security_doc("dropped"), lore, capsys)
    assert code == 0, err
    assert "attacker" not in lore.log()
    assert not lore.bodies("found-work")[0].endswith("why: -")
