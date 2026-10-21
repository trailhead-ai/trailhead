"""`portage eval-gate verdict` and `splice` — case verdicts into a push decision and a PR section.

Every test builds a real state directory (``plan.json``, ``results/<case>.json``) and drives
``dispatch.main(["eval-gate", "verdict"|"splice", ...])``, asserting on exit code, stdout and
the files written.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)

from portage.cli import dispatch
from portage.eval_gate import verdict as verdict_mod

START = "<!-- portage:eval-gate:start -->"
END = "<!-- portage:eval-gate:end -->"
SECRET = "sk-ant-api03-LEAKED0123456789abcdef"


def _entry(case, root="plugins/demo", grants=False):
    return {"plugin_root": root, "case": case, "runs": 3, "pass_at": 2,
            "runner": {"model": "sonnet", "allow_tools": ["Read"], "max_cost_usd": 1.0},
            "new_runner_grants": grants}


def _result(case, verdict="pass", passed=3, failed=0, errored=0, reason=None, runs=None):
    return {"case": case, "verdict": verdict, "passed": passed, "failed": failed,
            "errored": errored, "reason": reason, "runs": runs or []}


class State:
    def __init__(self, tmp_path):
        self.dir = tmp_path / "state"
        self.dir.mkdir(mode=0o700)
        os.chmod(self.dir, 0o700)

    def plan(self, cases=(), status="cases-selected", mapping_invalid=None, grants=False):
        doc = {"schema_version": 1, "status": status, "repo": "/r", "base": "main",
               "merge_base": "abc", "changed_files": [], "mapping_invalid": mapping_invalid,
               "new_runner_grants": grants, "total_max_cost_usd": 1.0, "cases": list(cases)}
        (self.dir / "plan.json").write_text(json.dumps(doc))

    def result(self, case, doc=None, **kw):
        (self.dir / "results").mkdir(exist_ok=True, mode=0o700)
        (self.dir / "results" / f"{case}.json").write_text(json.dumps(doc or _result(case, **kw)))


@pytest.fixture
def st(tmp_path):
    return State(tmp_path)


def verdict(capsys, st, *extra):
    code = dispatch.main(["eval-gate", "verdict", "--state", str(st.dir), *extra])
    out = capsys.readouterr()
    return code, out.out, out.err


def section_of(out):
    m = re.search(re.escape(START) + r".*?" + re.escape(END), out, re.S)
    assert m, out
    return m.group(0)


def test_all_planned_cases_pass_exits_0_and_lists_them(st, capsys):
    st.plan([_entry("a-case"), _entry("b-case")])
    st.result("a-case")
    st.result("b-case")
    code, out, _ = verdict(capsys, st)
    assert code == 0
    assert out.splitlines()[0] == "eval-gate: passed"
    sec = section_of(out)
    assert "## Eval gate" in sec and "`a-case`" in sec and "`b-case`" in sec


def test_one_fail_among_passes_exits_1_with_case_and_counts(st, capsys):
    st.plan([_entry("good"), _entry("bad")])
    st.result("good")
    st.result("bad", verdict="fail", passed=1, failed=2, errored=0)
    code, out, _ = verdict(capsys, st)
    assert code == 1
    assert out.splitlines()[0] == "eval-gate: failed"
    assert "bad: passed 1, failed 2, errored 0" in out
    assert "fix the prose or the case" in out and "re-run" in out


def test_exit_1_output_carries_state_path_and_per_run_grader_verdicts(st, capsys):
    st.plan([_entry("bad")])
    runs = [{"passed": False, "errored": False,
             "graders": [{"name": "mentions-x", "passed": True}, {"name": "cites-y", "passed": False}]},
            {"passed": True, "errored": False, "graders": [{"name": "mentions-x", "passed": True}]}]
    st.result("bad", verdict="fail", passed=1, failed=1, runs=runs)
    code, out, _ = verdict(capsys, st)
    assert code == 1
    assert str(st.dir) in out
    assert "run 1: mentions-x=pass cites-y=fail" in out
    assert "run 2: mentions-x=pass" in out


def test_fail_plus_not_run_exits_1(st, capsys):
    st.plan([_entry("bad"), _entry("skipped")])
    st.result("bad", verdict="fail", passed=0, failed=3)
    st.result("skipped", verdict="not-run", passed=0, failed=0, errored=3, reason="timeout")
    code, out, _ = verdict(capsys, st)
    assert code == 1
    assert "`skipped`" in section_of(out) and "timeout" in section_of(out)


def test_not_run_without_fail_exits_0_naming_case_and_reason(st, capsys):
    st.plan([_entry("slow")])
    st.result("slow", verdict="not-run", passed=0, failed=0, errored=3, reason="cost-cap")
    code, out, _ = verdict(capsys, st)
    assert code == 0
    sec = section_of(out)
    assert "`slow`" in sec and "`cost-cap`" in sec


def test_planned_case_without_result_is_not_run_never_pass(st, capsys):
    st.plan([_entry("silent"), _entry("done")])
    st.result("done")
    code, out, _ = verdict(capsys, st)
    assert code == 0
    sec = section_of(out)
    assert "`silent`" in sec and "`no-result-recorded`" in sec
    assert out.splitlines()[0] == "eval-gate: not-run"


def test_malformed_result_is_not_run_not_pass(st, capsys):
    st.plan([_entry("odd")])
    st.result("odd", doc={"case": "odd", "verdict": "pass-ish"})
    code, out, _ = verdict(capsys, st)
    assert code == 0
    assert "`no-result-recorded`" in section_of(out)


def test_result_whose_case_field_disagrees_with_its_file_is_not_pass(st, capsys):
    st.plan([_entry("odd")])
    st.result("odd", doc=_result("other"))
    _, out, _ = verdict(capsys, st)
    assert "`no-result-recorded`" in section_of(out)


def test_no_cases_mapped_exits_0_with_single_line_section(st, capsys):
    st.plan(status="no-cases-mapped")
    code, out, _ = verdict(capsys, st)
    assert code == 0
    sec = section_of(out)
    assert "No eval cases are mapped to this change." in sec
    assert len([l for l in sec.splitlines() if l.strip() and l not in (START, END, "## Eval gate")]) == 1


def test_base_unresolved_exits_0_with_could_not_determine(st, capsys):
    st.plan(status="base-unresolved")
    code, out, _ = verdict(capsys, st)
    assert code == 0
    assert "could not determine the changed files" in section_of(out)


def test_mapping_invalid_exits_1_naming_file_and_fault(st, capsys):
    st.plan(status="mapping-invalid",
            mapping_invalid={"file": "plugins/demo/evals/eval-gate.json", "fault": "runs must be 1..5"})
    code, out, _ = verdict(capsys, st)
    assert code == 1
    assert out.splitlines()[0] == "eval-gate: mapping-invalid"
    assert "plugins/demo/evals/eval-gate.json" in out and "runs must be 1..5" in out
    assert "fixed on the branch" in out


def test_gate_error_renders_without_reading_state(capsys):
    code = dispatch.main(["eval-gate", "verdict", "--gate-error", "plan"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines()[0] == "eval-gate: gate-error"
    sec = section_of(out)
    assert "errored" in sec and "`plan`" in sec


def test_gate_error_step_must_be_shaped(capsys):
    code = dispatch.main(["eval-gate", "verdict", "--gate-error", "plan; rm -rf /"])
    assert code == 2


def test_verdict_requires_state_or_gate_error(capsys):
    assert dispatch.main(["eval-gate", "verdict"]) == 2


def test_new_runner_grants_corpus_is_named(st, capsys):
    st.plan([_entry("fresh", root="plugins/newcorpus", grants=True), _entry("old")], grants=True)
    st.result("fresh")
    st.result("old")
    code, out, _ = verdict(capsys, st)
    assert code == 0
    sec = section_of(out)
    assert "new-runner-grants" in sec and "`plugins/newcorpus`" in sec
    assert "plugins/demo" not in sec


def test_result_for_unplanned_case_is_ignored_and_reported(st, capsys):
    st.plan([_entry("planned")])
    st.result("planned", verdict="fail", passed=0, failed=3)
    st.result("stray")
    code, out, _ = verdict(capsys, st)
    assert code == 1
    assert "ignored: stray" in out
    assert "`stray`" in section_of(out)


def test_unplanned_pass_does_not_stand_in_for_a_missing_planned_result(st, capsys):
    st.plan([_entry("planned")])
    st.result("stray")
    _, out, _ = verdict(capsys, st)
    assert "`no-result-recorded`" in section_of(out)


def test_canary_json_at_state_root_is_never_a_case_result(st, capsys):
    st.plan([_entry("a-case")])
    st.result("a-case")
    (st.dir / "canary.json").write_text(json.dumps(_result("sandbox-write")))
    code, out, _ = verdict(capsys, st)
    assert code == 0
    assert "ignored" not in out and "sandbox-write" not in out


def test_result_with_unsafe_name_is_not_echoed(st, capsys):
    st.plan([_entry("a-case")])
    st.result("a-case")
    (st.dir / "results" / "Bad Name; $(x).json").write_text(json.dumps(_result("x")))
    _, out, _ = verdict(capsys, st)
    assert "Bad Name" not in out and "$(x)" not in out
    assert "ignored: 1 result with an invalid name" in out


def test_sandbox_unwritable_names_case_reason_and_override(st, capsys):
    st.plan([_entry("blocked")])
    st.result("blocked", verdict="not-run", passed=0, failed=0, errored=3,
              reason="runner-sandbox-unwritable")
    code, out, _ = verdict(capsys, st)
    assert code == 0
    sec = section_of(out)
    assert "`blocked`" in sec and "`runner-sandbox-unwritable`" in sec
    assert "PORTAGE_EVAL_RUNNER" in sec
    assert "cannot write inside its sandbox" in sec


@pytest.mark.parametrize("reason,persistent", [
    ("runner-missing", True), ("harness-has-no-eval-runner", True),
    ("runner-sandbox-unwritable", True), ("timeout", False), ("cost-cap", False),
    ("runner-error", False), ("runs-errored", False), ("no-result-recorded", False)])
def test_persistent_line_classifies_each_reason(st, capsys, reason, persistent):
    st.plan([_entry("c")])
    st.result("c", verdict="not-run", passed=0, failed=0, errored=3, reason=reason)
    _, out, _ = verdict(capsys, st)
    line = out.splitlines()[1]
    assert line == (f"persistent: {reason}" if persistent else "persistent: none")


def test_persistent_line_lists_distinct_codes_sorted(st, capsys):
    st.plan([_entry("a"), _entry("b"), _entry("c")])
    st.result("a", verdict="not-run", reason="runner-missing")
    st.result("b", verdict="not-run", reason="runner-missing")
    st.result("c", verdict="not-run", reason="harness-has-no-eval-runner")
    _, out, _ = verdict(capsys, st)
    assert out.splitlines()[1] == "persistent: harness-has-no-eval-runner,runner-missing"


def test_persistent_classification_is_a_function():
    assert verdict_mod.persistent_reasons(["timeout", "runner-missing", "runner-missing"]) == ["runner-missing"]
    assert verdict_mod.persistent_reasons(["timeout"]) == []


def test_section_file_equals_printed_section(st, capsys):
    st.plan([_entry("a-case")])
    st.result("a-case")
    _, out, _ = verdict(capsys, st)
    assert (st.dir / "eval-gate-section.md").read_text() == section_of(out) + "\n"


def test_section_file_written_on_exit_1_too(st, capsys):
    st.plan([_entry("bad")])
    st.result("bad", verdict="fail", passed=0, failed=3)
    code, out, _ = verdict(capsys, st)
    assert code == 1
    assert (st.dir / "eval-gate-section.md").read_text() == section_of(out) + "\n"


def test_gate_error_with_state_writes_section_file(st, capsys):
    code, out, _ = verdict(capsys, st, "--gate-error", "run")
    assert code == 0
    assert (st.dir / "eval-gate-section.md").read_text() == section_of(out) + "\n"


def test_section_never_carries_runner_output(st, capsys):
    st.plan([_entry("a-case")])
    st.result("a-case", verdict="not-run", reason="runner-error", passed=0, errored=3)
    (st.dir / "runs").mkdir()
    (st.dir / "runs" / "a-case.json").write_text(json.dumps(
        {"error": SECRET, "cases": [{"prompt": SECRET}]}))
    (st.dir / "runs" / "a-case").mkdir()
    (st.dir / "runs" / "a-case" / "stderr.txt").write_text(SECRET)
    code, out, err = verdict(capsys, st)
    assert code == 0
    assert SECRET not in out + err
    assert SECRET not in (st.dir / "eval-gate-section.md").read_text()


def test_result_fields_outside_the_closed_vocabulary_do_not_reach_the_section(st, capsys):
    st.plan([_entry("a-case")])
    st.result("a-case", doc=_result("a-case", verdict="not-run", reason=SECRET, passed=0, errored=3))
    code, out, _ = verdict(capsys, st)
    assert code == 0
    assert SECRET not in out
    assert "`no-result-recorded`" in section_of(out)


def test_hostile_grader_name_is_not_echoed(st, capsys):
    st.plan([_entry("bad")])
    st.result("bad", verdict="fail", passed=0, failed=1,
              runs=[{"passed": False, "errored": False, "graders": [{"name": SECRET + "\x1b[2J", "passed": False}]}])
    _, out, _ = verdict(capsys, st)
    assert SECRET not in out and "\x1b" not in out


def test_state_dir_that_is_a_symlink_is_refused(st, tmp_path, capsys):
    st.plan([_entry("a-case")])
    link = tmp_path / "link"
    link.symlink_to(st.dir)
    code = dispatch.main(["eval-gate", "verdict", "--state", str(link)])
    assert code == 2
    assert "symlink" in capsys.readouterr().err


def test_state_dir_with_loose_mode_is_refused(st, capsys):
    st.plan([_entry("a-case")])
    os.chmod(st.dir, 0o755)
    assert dispatch.main(["eval-gate", "verdict", "--state", str(st.dir)]) == 2


def test_planned_case_name_failing_shape_is_refused(st, capsys):
    st.plan([_entry("Bad Name")])
    assert dispatch.main(["eval-gate", "verdict", "--state", str(st.dir)]) == 2


def test_symlinked_result_file_is_not_followed(st, tmp_path, capsys):
    st.plan([_entry("a-case")])
    (st.dir / "results").mkdir()
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(_result("a-case")))
    (st.dir / "results" / "a-case.json").symlink_to(target)
    _, out, _ = verdict(capsys, st)
    assert "`no-result-recorded`" in section_of(out)


# --- splice ---------------------------------------------------------------

def make_section(tmp_path, text="x"):
    sec = f"{START}\n## Eval gate\n\n{text}\n{END}\n"
    p = tmp_path / "section.md"
    p.write_text(sec)
    return p, sec


def splice(capsys, body, tmp_path, *extra, section=None):
    bf = tmp_path / "body.md"
    bf.write_text(body)
    sec_path = section or make_section(tmp_path)[0]
    code = dispatch.main(["eval-gate", "splice", "--body-file", str(bf),
                          "--section-file", str(sec_path), *extra])
    o = capsys.readouterr()
    return code, o.out, o.err, bf


def test_splice_appends_once_when_no_section(tmp_path, capsys):
    _, sec = make_section(tmp_path)
    code, out, _, _ = splice(capsys, "# Title\n\nbody text\n", tmp_path)
    assert code == 0
    assert out == "# Title\n\nbody text\n\n" + sec
    assert out.count(START) == 1


def test_splice_replaces_an_earlier_section_and_changes_nothing_else(tmp_path, capsys):
    old = f"{START}\n## Eval gate\n\nOLD\n{END}"
    body = f"intro\n\n{old}\n\ntrailing  text\n\n\n"
    code, out, _, _ = splice(capsys, body, tmp_path)
    assert code == 0
    new = make_section(tmp_path)[1].rstrip("\n")
    assert out == f"intro\n\n{new}\n\ntrailing  text\n\n\n"


def test_splice_state_form_reads_the_section_file_verdict_wrote(st, tmp_path, capsys):
    st.plan([_entry("a-case")])
    st.result("a-case")
    _, vout, _ = verdict(capsys, st)
    bf = tmp_path / "body.md"
    bf.write_text("hello\n")
    code = dispatch.main(["eval-gate", "splice", "--body-file", str(bf), "--state", str(st.dir)])
    out = capsys.readouterr().out
    assert code == 0
    assert section_of(out) == section_of(vout)
    assert out.startswith("hello\n")


def test_splice_start_without_end_exits_2_and_leaves_out_unwritten(tmp_path, capsys):
    outp = tmp_path / "out.md"
    code, out, err, bf = splice(capsys, f"a\n{START}\nhalf\n", tmp_path, "--out", str(outp))
    assert code == 2
    assert out == "" and not outp.exists()
    assert bf.read_text() == f"a\n{START}\nhalf\n"


def test_splice_end_without_start_exits_2(tmp_path, capsys):
    code, *_ = splice(capsys, f"a\n{END}\n", tmp_path)
    assert code == 2


def test_splice_two_sections_exit_2_and_leave_out_unwritten(tmp_path, capsys):
    sec = f"{START}\nx\n{END}"
    outp = tmp_path / "out.md"
    code, out, _, bf = splice(capsys, f"{sec}\n\n{sec}\n", tmp_path, "--out", str(outp))
    assert code == 2
    assert not outp.exists()
    assert bf.read_text() == f"{sec}\n\n{sec}\n"


def test_splice_end_before_start_exits_2(tmp_path, capsys):
    code, *_ = splice(capsys, f"{END}\nx\n{START}\n", tmp_path)
    assert code == 2


def test_splice_out_writes_exactly_the_spliced_body(tmp_path, capsys):
    outp = tmp_path / "out.md"
    code, out, _, _ = splice(capsys, "body\n", tmp_path, "--out", str(outp))
    assert code == 0
    assert outp.read_text() == "body\n\n" + make_section(tmp_path)[1]
    assert out == ""


def test_splice_rejects_a_section_file_without_exactly_one_marker_pair(tmp_path, capsys):
    bad = tmp_path / "bad.md"
    bad.write_text("no markers\n")
    code, *_ = splice(capsys, "body\n", tmp_path, section=bad)
    assert code == 2


def test_splice_missing_body_file_exits_2(tmp_path, capsys):
    sec = make_section(tmp_path)[0]
    assert dispatch.main(["eval-gate", "splice", "--body-file", str(tmp_path / "nope"),
                          "--section-file", str(sec)]) == 2


# --- OS faults exit 2, never a traceback or the stop code ------------------

def _leftovers(d):
    return [p.name for p in d.iterdir() if p.name.endswith(".tmp")]


def test_verdict_section_path_being_a_directory_exits_2_without_traceback(st, capsys):
    st.plan([_entry("a-case")])
    st.result("a-case")
    (st.dir / "eval-gate-section.md").mkdir()
    code = dispatch.main(["eval-gate", "verdict", "--state", str(st.dir)])
    err = capsys.readouterr().err
    assert code == 2
    assert err.startswith("eval-gate verdict:") and "Traceback" not in err and len(err.splitlines()) == 1
    assert _leftovers(st.dir) == []


def test_a_decided_fail_survives_a_section_write_fault(st, capsys):
    st.plan([_entry("bad")])
    st.result("bad", verdict="fail", passed=0, failed=3)
    (st.dir / "eval-gate-section.md").mkdir()
    code = dispatch.main(["eval-gate", "verdict", "--state", str(st.dir)])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err.startswith("eval-gate verdict:") and "Traceback" not in captured.err
    assert captured.out.splitlines()[0] == "eval-gate: failed"
    assert "`bad`" in section_of(captured.out)


def test_base_unresolved_is_a_persistent_reason(st, capsys):
    st.plan(status="base-unresolved")
    code, out, _ = verdict(capsys, st)
    assert code == 0
    assert out.splitlines()[:2] == ["eval-gate: base-unresolved", "persistent: base-unresolved"]


def test_case_removed_on_branch_is_listed_with_its_gloss_and_is_not_persistent(st, capsys):
    st.plan([_entry("gone")])
    st.result("gone", verdict="not-run", passed=0, failed=0, errored=3, reason="case-removed-on-branch")
    code, out, _ = verdict(capsys, st)
    assert code == 0
    assert out.splitlines()[:2] == ["eval-gate: not-run", "persistent: none"]
    assert ("`gone` — `case-removed-on-branch`: the branch removes this case; it was not run "
            "— review the removal") in section_of(out)


def test_verdict_unreadable_plan_exits_2(st, capsys):
    (st.dir / "plan.json").mkdir()
    assert dispatch.main(["eval-gate", "verdict", "--state", str(st.dir)]) == 2


def test_splice_unwritable_out_exits_2_without_traceback(tmp_path, capsys):
    code, out, err, _ = splice(capsys, "body\n", tmp_path, "--out", str(tmp_path / "no" / "such" / "out.md"))
    assert code == 2
    assert err.startswith("eval-gate splice:") and "Traceback" not in err
    assert out == ""


def test_splice_out_failure_leaves_no_temp_file(tmp_path, capsys):
    target = tmp_path / "isdir"
    target.mkdir()
    code, *_ = splice(capsys, "body\n", tmp_path, "--out", str(target))
    assert code == 2
    assert _leftovers(tmp_path) == []


# --- a trailing newline does not pass shape validation ---------------------

def test_result_file_with_trailing_newline_in_name_is_not_echoed(st, capsys):
    st.plan([_entry("a-case")])
    st.result("a-case")
    (st.dir / "results" / "zz\n.json").write_text(json.dumps(_result("zz\n")))
    _, out, _ = verdict(capsys, st)
    assert "zz" not in out
    assert "ignored: 1 result with an invalid name" in out


def test_planned_case_name_with_trailing_newline_is_refused(st, capsys):
    st.plan([_entry("a-case\n")])
    assert dispatch.main(["eval-gate", "verdict", "--state", str(st.dir)]) == 2


def test_grader_name_with_trailing_newline_is_not_echoed(st, capsys):
    st.plan([_entry("bad")])
    st.result("bad", verdict="fail", passed=0, failed=1,
              runs=[{"passed": False, "errored": False, "graders": [{"name": "grader\n", "passed": False}]}])
    _, out, _ = verdict(capsys, st)
    assert "grader\n=" not in out and "<invalid-grader-name>" in out


def test_corpus_path_with_trailing_newline_is_not_echoed(st, capsys):
    st.plan([_entry("a-case", root="plugins/x\n", grants=True)])
    st.result("a-case")
    _, out, _ = verdict(capsys, st)
    assert "plugins/x" not in out


def test_gate_error_step_with_trailing_newline_is_refused():
    assert dispatch.main(["eval-gate", "verdict", "--gate-error", "plan\n"]) == 2
