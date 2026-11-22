"""`portage eval-gate verdict` and `splice` — case verdicts into a push decision and a PR section.

Every test builds a real state directory (``plan.json``, ``results/<case>.json``) and drives
``dispatch.main(["eval-gate", "verdict"|"splice", ...])``, asserting on exit code, stdout and
the files written.
"""

from __future__ import annotations

import json
import os
import re

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.cli import dispatch
from portage.eval_gate import verdict as verdict_mod

START = "<!-- portage:eval-gate:start -->"
END = "<!-- portage:eval-gate:end -->"
SECRET = "sk-ant-api03-LEAKED0123456789abcdef"


def _entry(case, root="plugins/demo", grants=False, model="sonnet", push_model=None, merge_model=None):
    return {"plugin_root": root, "case": case, "runs": 3, "pass_at": 2,
            "runner": {"model": model, "allow_tools": ["Read"], "max_cost_usd": 1.0},
            "push_model": push_model, "merge_model": merge_model, "new_runner_grants": grants}


def _result(case, verdict="pass", passed=3, failed=0, errored=0, reason=None, runs=None):
    return {"case": case, "verdict": verdict, "passed": passed, "failed": failed,
            "errored": errored, "reason": reason, "runs": runs or []}


class State:
    def __init__(self, tmp_path):
        self.dir = tmp_path / "state"
        self.dir.mkdir(mode=0o700)
        os.chmod(self.dir, 0o700)

    def plan(self, cases=(), status="cases-selected", mapping_invalid=None, grants=False,
             needs_merge_model=None, mark_changes=None, tier=None):
        doc = {"schema_version": 1, "status": status, "repo": "/r", "base": "main",
               "merge_base": "abc", "changed_files": [], "mapping_invalid": mapping_invalid,
               "new_runner_grants": grants, "total_max_cost_usd": 1.0, "cases": list(cases)}
        if needs_merge_model is not None:
            doc["needs_merge_model"] = needs_merge_model
        if mark_changes is not None:
            doc["mark_changes"] = mark_changes
        if tier is not None:
            doc["tier"] = tier
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


def test_runs_carrying_the_timing_fields_read_the_same_as_runs_without_them(st, capsys):
    st.plan([_entry("bad")])
    plain = [{"passed": False, "errored": False, "graders": [{"name": "cites-y", "passed": False}]},
             {"passed": True, "errored": False, "graders": [{"name": "cites-y", "passed": True}]}]
    timed = [{**r, "duration_seconds": 12.5, "cost_usd": 0.3, "judge_cost_usd": None} for r in plain]
    st.result("bad", verdict="fail", passed=1, failed=1, runs=plain)
    baseline = verdict(capsys, st)
    st.result("bad", verdict="fail", passed=1, failed=1, runs=timed)
    assert baseline[0] == 1
    assert verdict(capsys, st) == baseline


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
    assert len([ln for ln in sec.splitlines() if ln.strip() and ln not in (START, END, "## Eval gate")
                and not ln.startswith("needs-merge-model")]) == 1


def _invalid_plan(st, revision):
    st.plan(status="mapping-invalid",
            mapping_invalid={"file": "plugins/demo/evals/eval-gate.json", "fault": "runs must be 1..5",
                             "revision": revision})


def test_a_base_revision_mapping_fault_says_the_fix_must_land_on_the_base(st, capsys):
    _invalid_plan(st, "base")
    code, out, _ = verdict(capsys, st)
    assert code == 1
    assert (
        "The base revision's mapping is invalid; the fix must land on the base "
        "(or be pushed directly), not on the branch"
    ) in out.split("<!--")[0]
    assert ("The eval-case mapping `plugins/demo/evals/eval-gate.json` is invalid at the base revision, "
            "so no case was selected. Fixing it on the branch will not clear this: the fix must land "
            "on the base (or be pushed directly).") in section_of(out)
    assert "fixed on the branch" not in out


def test_a_head_mapping_fault_keeps_the_fix_it_on_the_branch_wording(st, capsys):
    _invalid_plan(st, "head")
    code, out, _ = verdict(capsys, st)
    assert code == 1
    assert "The mapping must be fixed on the branch; there is no override." in out.split("<!--")[0]
    assert "The mapping must be fixed on the branch." in section_of(out)
    assert "base" not in section_of(out).replace("eval-gate.json", "")


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


# --- corpus-new-on-branch and tree-dirty -----------------------------------

def test_a_not_run_for_a_new_corpus_says_the_grants_are_unreviewed_and_still_pushes(st, capsys):
    st.plan([_entry("fresh", root="plugins/newcorpus", grants=True)], grants=True)
    st.result("fresh", verdict="not-run", reason="corpus-new-on-branch", passed=0, errored=3)
    code, out, _ = verdict(capsys, st)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "eval-gate: not-run"
    assert lines[1] == "persistent: none"
    sec = section_of(out)
    assert ("`fresh` — `corpus-new-on-branch`: this corpus is new on the branch, so its runner "
            "grants are unreviewed and its cases were not run — review the grants") in sec
    assert "new-runner-grants" in sec


def test_tree_dirty_stops_the_push_with_the_commit_or_stash_message(st, capsys):
    st.plan([], status="tree-dirty")
    code, out, _ = verdict(capsys, st)
    assert code == 1
    assert out.splitlines()[0] == "eval-gate: tree-dirty"
    msg = ("the working tree has uncommitted changes to tracked files; the gate grades committed "
           "prose — commit or stash, then push again")
    assert msg in out
    assert msg in section_of(out)
    assert (st.dir / "eval-gate-section.md").read_text().startswith(START)


# --- splice --out is written atomically and never through a symlink -------

def test_splice_out_refuses_an_existing_symlink_and_leaves_its_target_alone(tmp_path, capsys):
    target = tmp_path / "victim.txt"
    target.write_text("precious\n")
    link = tmp_path / "out.md"
    link.symlink_to(target)
    code, out, err, _ = splice(capsys, "body\n", tmp_path, "--out", str(link))
    assert code == 2
    assert err.startswith("eval-gate splice:")
    assert target.read_text() == "precious\n"
    assert link.is_symlink()


def test_splice_out_replaces_the_file_instead_of_writing_through_its_inode(tmp_path, capsys):
    outp = tmp_path / "out.md"
    outp.write_text("old\n")
    peer = tmp_path / "peer.md"
    os.link(outp, peer)
    code, *_ = splice(capsys, "body\n", tmp_path, "--out", str(outp))
    assert code == 0
    assert outp.read_text().startswith("body\n")
    assert peer.read_text() == "old\n"


class TestCachedPass:
    def test_a_cached_pass_reads_pass_cached_in_the_section_and_exits_0(self, st, capsys):
        st.plan([_entry("reused"), _entry("fresh")])
        st.result("reused", doc={**_result("reused"), "cached": True})
        st.result("fresh", doc={**_result("fresh"), "cached": False})
        code, out, _ = verdict(capsys, st)
        assert code == 0
        assert out.splitlines()[0] == "eval-gate: passed"
        lines = section_of(out).splitlines()
        assert [ln for ln in lines if "`reused`" in ln] == ["- `reused` — pass (cached) (model: sonnet)"]
        assert [ln for ln in lines if "`fresh`" in ln] == ["- `fresh` (model: sonnet)"]

    def test_a_pass_bullet_names_the_model_its_case_ran_on(self, st, capsys):
        st.plan([_entry("on-opus", model="opus"), _entry("on-sonnet")])
        st.result("on-opus")
        st.result("on-sonnet")
        _, out, _ = verdict(capsys, st)
        lines = section_of(out).splitlines()
        assert "- `on-opus` (model: opus)" in lines
        assert "- `on-sonnet` (model: sonnet)" in lines

    def test_a_pass_bullet_with_an_unusable_model_reads_unknown_model(self, st, capsys):
        st.plan([_entry("odd", model="has `tick`")])
        st.result("odd")
        _, out, _ = verdict(capsys, st)
        assert "- `odd` (model: <unknown-model>)" in section_of(out).splitlines()

    def test_a_result_without_cached_is_a_plain_pass(self, st, capsys):
        st.plan([_entry("old")])
        st.result("old")
        code, out, _ = verdict(capsys, st)
        assert code == 0
        assert "(cached)" not in out

    @pytest.mark.parametrize("bad", ["true", 1, 0, None, [], {"x": 1}])
    def test_a_non_boolean_cached_is_no_result_recorded(self, st, capsys, bad):
        st.plan([_entry("odd")])
        st.result("odd", doc={**_result("odd"), "cached": bad})
        code, out, _ = verdict(capsys, st)
        assert code == 0
        assert "`odd` — `no-result-recorded`" in section_of(out)
        assert "(cached)" not in out


def _tiered_plan(st, tier, pending):
    st.plan([_entry("core")])
    doc = json.loads((st.dir / "plan.json").read_text())
    doc.update({"tier": tier, "head": "abc123", "merge_only_pending": pending})
    (st.dir / "plan.json").write_text(json.dumps(doc))
    st.result("core")


def test_push_tier_section_names_the_tier_and_counts_pending_merge_only_cases(st, capsys):
    _tiered_plan(st, "push", 1)
    _, out, _ = verdict(capsys, st)
    section = section_of(out)
    assert "Tier: push" in section
    assert "1 merge-only case(s) touched by this change will run before merge" in section


def test_push_tier_pending_line_carries_the_count(st, capsys):
    _tiered_plan(st, "push", 3)
    _, out, _ = verdict(capsys, st)
    assert "3 merge-only case(s) touched by this change will run before merge" in section_of(out)


def test_push_tier_with_no_pending_merge_only_cases_has_no_pending_line(st, capsys):
    _tiered_plan(st, "push", 0)
    _, out, _ = verdict(capsys, st)
    section = section_of(out)
    assert "Tier: push" in section
    assert "merge-only" not in section


def test_merge_tier_section_names_the_tier_and_has_no_pending_line(st, capsys):
    _tiered_plan(st, "merge", 1)
    _, out, _ = verdict(capsys, st)
    section = section_of(out)
    assert "Tier: merge" in section
    assert "merge-only" not in section


def test_push_that_touches_only_merge_only_cases_still_says_they_run_before_merge(st, capsys):
    st.plan([], status="no-cases-mapped")
    doc = json.loads((st.dir / "plan.json").read_text())
    doc.update({"tier": "push", "head": "abc123", "merge_only_pending": 2})
    (st.dir / "plan.json").write_text(json.dumps(doc))
    code, out, _ = verdict(capsys, st)
    assert code == 0
    assert "2 merge-only case(s) touched by this change will run before merge" in section_of(out)


def _merge_tier_plan(st, tier, *, slow_result, core_result="pass"):
    slow = {**_entry("slow"), "merge_only": True}
    core = {**_entry("core"), "merge_only": False}
    st.plan([core, slow])
    doc = json.loads((st.dir / "plan.json").read_text())
    doc.update({"tier": tier, "head": "a" * 40, "merge_only_pending": 0})
    (st.dir / "plan.json").write_text(json.dumps(doc))
    for case, res in (("core", core_result), ("slow", slow_result)):
        if res == "pass":
            st.result(case)
        elif res == "not-run":
            st.result(case, verdict="not-run", passed=0, errored=3, reason="timeout")


class TestMergeTierVerdict:
    def test_a_not_run_merge_only_case_stops_the_change(self, st, capsys):
        _merge_tier_plan(st, "merge", slow_result="not-run")
        code, out, _ = verdict(capsys, st)
        assert code == 1
        assert "merge-only-not-run" in out.split("<!--", 1)[0]
        assert "merge-only-not-run" in section_of(out)
        assert "`slow`" in section_of(out)

    def test_a_merge_only_case_with_no_result_at_all_stops_the_change(self, st, capsys):
        _merge_tier_plan(st, "merge", slow_result="missing")
        code, out, _ = verdict(capsys, st)
        assert code == 1
        assert "merge-only-not-run" in section_of(out)

    def test_a_not_run_core_case_still_lets_the_change_through(self, st, capsys):
        _merge_tier_plan(st, "merge", slow_result="pass", core_result="not-run")
        code, out, _ = verdict(capsys, st)
        assert code == 0
        assert "merge-only-not-run" not in out

    def test_a_passing_merge_only_case_lets_the_change_through(self, st, capsys):
        _merge_tier_plan(st, "merge", slow_result="pass")
        code, _, _ = verdict(capsys, st)
        assert code == 0

    def test_the_same_not_run_case_does_not_stop_a_push_tier_state(self, st, capsys):
        _merge_tier_plan(st, "push", slow_result="not-run")
        code, out, _ = verdict(capsys, st)
        assert code == 0
        assert "merge-only-not-run" not in out


def _slow_not_run(st, reason, *, tier="merge"):
    _merge_tier_plan(st, tier, slow_result="pass")
    st.result("slow", verdict="not-run", passed=0, errored=0, reason=reason)


class TestMergeTierUnrunnableCases:
    @pytest.mark.parametrize("reason", ["case-removed-on-branch", "corpus-new-on-branch"])
    def test_a_merge_only_case_that_can_never_run_does_not_stop_the_change(self, st, capsys, reason):
        _slow_not_run(st, reason)
        code, out, _ = verdict(capsys, st)
        assert code == 0
        assert "merge-only-not-run" not in out
        assert "eval-gate: not-run" in out

    @pytest.mark.parametrize("reason", ["case-removed-on-branch", "corpus-new-on-branch"])
    def test_it_is_reported_as_on_push(self, st, capsys, reason):
        _slow_not_run(st, reason)
        _, out, _ = verdict(capsys, st)
        assert f"`slow` — `{reason}`" in section_of(out)

    @pytest.mark.parametrize("reason", ["timeout", "cost-cap", "runner-error", "runs-errored",
                                        "head-moved"])
    def test_a_merge_only_case_that_could_still_run_stops_the_change(self, st, capsys, reason):
        _slow_not_run(st, reason)
        code, out, _ = verdict(capsys, st)
        assert code == 1
        assert "merge-only-not-run: slow" in out

    def test_an_unrunnable_case_does_not_hide_a_runnable_one_beside_it(self, st, capsys):
        slow = {**_entry("slow"), "merge_only": True}
        gone = {**_entry("gone"), "merge_only": True}
        st.plan([slow, gone])
        doc = json.loads((st.dir / "plan.json").read_text())
        doc.update({"tier": "merge", "head": "a" * 40, "merge_only_pending": 0})
        (st.dir / "plan.json").write_text(json.dumps(doc))
        st.result("slow", verdict="not-run", passed=0, errored=0, reason="timeout")
        st.result("gone", verdict="not-run", passed=0, errored=0, reason="case-removed-on-branch")
        code, out, _ = verdict(capsys, st)
        assert code == 1
        assert "merge-only-not-run: slow" in out
        assert "merge-only-not-run: slow, gone" not in out


class TestMergeOnlyNotRunRecovery:
    @pytest.mark.parametrize("reason,recovery", [
        ("timeout", "the runs split and the budget ran out; re-run the merge-tier gate"),
        ("cost-cap", "the case hit its own cost ceiling; raise its `max_cost_usd` or merge by hand"),
        ("runner-error", "re-run after the usage limit resets, or merge by hand"),
        ("runs-errored", "re-run after the usage limit resets, or merge by hand"),
        ("head-moved", "re-plan at the current head"),
        ("runner-missing", "the eval runner binary was not found on this machine; merge by hand"),
    ])
    def test_each_not_run_case_names_its_reason_and_the_matching_recovery(
        self, st, capsys, reason, recovery
    ):
        _slow_not_run(st, reason)
        code, out, _ = verdict(capsys, st)
        assert code == 1
        stdout_part = out.split("<!--", 1)[0]
        for text in (stdout_part, section_of(out)):
            line = next(ln for ln in text.splitlines() if "slow" in ln and recovery in ln)
            assert reason in line

    def test_the_recoveries_differ_by_reason(self, st, capsys):
        _slow_not_run(st, "timeout")
        _, timeout_out, _ = verdict(capsys, st)
        _slow_not_run(st, "runner-error")
        _, other_out, _ = verdict(capsys, st)
        assert "re-run the merge-tier gate" in timeout_out
        assert "re-run the merge-tier gate" not in other_out
        assert "merge by hand" not in timeout_out
        assert "usage limit resets" not in timeout_out
        assert "usage limit resets" in other_out


def _mark(case, reason="Needs deep multi-file reasoning", root="tools/craft/plugins/craft"):
    return {"plugin_root": root, "case": case, "reason": reason}


def _change(case, change, reason="Flaky on the push model", root="tools/craft/plugins/craft"):
    return {"plugin_root": root, "case": case, "change": change,
            "reason": None if change == "removed" else reason}


def _head(out):
    return out.split("<!--", 1)[0].splitlines()


class TestNeedsMergeModelReport:
    def test_two_marked_cases_print_with_reasons_in_stdout_and_section(self, st, capsys):
        st.plan([_entry("a-case")], needs_merge_model=[_mark("alpha"), _mark("beta", "Slow; very slow")])
        st.result("a-case")
        code, out, _ = verdict(capsys, st)
        line_a = "needs-merge-model: `tools/craft/plugins/craft/alpha` \u2014 \"Needs deep multi-file reasoning\""
        line_b = "needs-merge-model: `tools/craft/plugins/craft/beta` \u2014 \"Slow; very slow\""
        assert code == 0
        assert line_a in _head(out) and line_b in _head(out)
        assert line_a in section_of(out).splitlines() and line_b in section_of(out).splitlines()
        assert "needs-merge-model: none" not in out

    def test_a_mark_target_that_looks_like_a_web_address_prints_inside_a_code_span(self, st, capsys):
        st.plan([_entry("a-case")],
                needs_merge_model=[_mark("www.example", root="")],
                mark_changes=[_change("www.example", "added", root="")])
        st.result("a-case")
        _, out, _ = verdict(capsys, st)
        marked = "needs-merge-model: `www.example` \u2014 \"Needs deep multi-file reasoning\""
        changed = ("needs-merge-model changed: `www.example` (added) \u2014 "
                   "\"Flaky on the push model\"; takes effect once merged")
        for text in (_head(out), section_of(out).splitlines()):
            assert marked in text and changed in text

    def test_no_marks_prints_none(self, st, capsys):
        st.plan([_entry("a-case")], needs_merge_model=[], mark_changes=[])
        st.result("a-case")
        _, out, _ = verdict(capsys, st)
        assert "needs-merge-model: none" in _head(out)
        assert "needs-merge-model: none" in section_of(out).splitlines()
        assert "needs-merge-model changed" not in out

    def test_an_added_and_a_removed_change_print_two_changed_lines_removal_without_reason(self, st, capsys):
        st.plan([_entry("a-case")], needs_merge_model=[],
                mark_changes=[_change("beta", "added"), _change("gamma", "removed")])
        st.result("a-case")
        _, out, _ = verdict(capsys, st)
        added = ("needs-merge-model changed: `tools/craft/plugins/craft/beta` (added) \u2014 "
                 "\"Flaky on the push model\"; takes effect once merged")
        removed = "needs-merge-model changed: `tools/craft/plugins/craft/gamma` (removed)"
        for text in (_head(out), section_of(out).splitlines()):
            assert added in text and removed in text

    def test_an_altered_change_says_it_takes_effect_once_merged(self, st, capsys):
        st.plan([_entry("a-case")], mark_changes=[_change("beta", "altered", "New reason")])
        st.result("a-case")
        _, out, _ = verdict(capsys, st)
        assert ("needs-merge-model changed: `tools/craft/plugins/craft/beta` (altered) \u2014 "
                "\"New reason\"; takes effect once merged") in _head(out)

    def test_a_marked_and_changed_plan_orders_lines_after_ignored_and_before_failure_detail(self, st, capsys):
        st.plan([_entry("bad")], needs_merge_model=[_mark("alpha")],
                mark_changes=[_change("beta", "added")])
        st.result("bad", verdict="fail", passed=0, failed=3)
        st.result("stray")
        _, out, _ = verdict(capsys, st)
        lines = _head(out)
        idx = {k: next(i for i, ln in enumerate(lines) if ln.startswith(k))
               for k in ("eval-gate:", "persistent:", "ignored:", "needs-merge-model:",
                         "needs-merge-model changed:", "bad:")}
        assert list(idx.values()) == sorted(idx.values())
        assert idx["eval-gate:"] == 0 and idx["persistent:"] == 1

    def test_no_cases_mapped_with_an_added_change_still_prints_it(self, st, capsys):
        st.plan(status="no-cases-mapped", needs_merge_model=[],
                mark_changes=[_change("beta", "added")])
        code, out, _ = verdict(capsys, st)
        assert code == 0
        head = _head(out)
        assert head[:2] == ["eval-gate: no-cases-mapped", "persistent: none"]
        assert "needs-merge-model: none" in head
        assert any(ln.startswith("needs-merge-model changed: `tools/craft/plugins/craft/beta` (added)")
                   for ln in head)
        assert any(ln.startswith("needs-merge-model changed:") for ln in section_of(out).splitlines())

    def test_a_plan_written_before_the_fields_reads_as_none_and_no_changes(self, st, capsys):
        st.plan([_entry("a-case")])
        st.result("a-case")
        code, out, _ = verdict(capsys, st)
        assert code == 0
        assert _head(out)[:3] == ["eval-gate: passed", "persistent: none", "needs-merge-model: none"]
        assert "needs-merge-model changed" not in out

    def test_an_empty_plugin_root_prints_the_bare_case(self, st, capsys):
        st.plan([_entry("a-case")], needs_merge_model=[_mark("alpha", root="")])
        st.result("a-case")
        _, out, _ = verdict(capsys, st)
        assert "needs-merge-model: `alpha` \u2014 \"Needs deep multi-file reasoning\"" in _head(out)

    def test_statuses_that_did_not_read_the_mappings_print_no_marks_lines(self, st, capsys):
        st.plan(status="tree-dirty")
        _, out, _ = verdict(capsys, st)
        assert "needs-merge-model" not in out


BAD_REASONS = ["has `tick`", "a <b> tag", "two\nlines", "", "x" * 201, " leading", "trail\n"]
BAD_ROOTS = ["a/../b", "x y", "bad\n", 5, None]
BAD_CASES = ["Bad Case", "x\n", "", 5, None]


def _refused(st, capsys, **plan_kw):
    st.plan([_entry("a-case")])
    doc = json.loads((st.dir / "plan.json").read_text())
    (st.dir / "plan.json").write_text(json.dumps({**doc, **plan_kw}))
    st.result("a-case")
    code, out, err = verdict(capsys, st)
    assert code == 2 and out == ""
    return err


class TestNeedsMergeModelValidation:
    @pytest.mark.parametrize("field", ["needs_merge_model", "mark_changes"])
    @pytest.mark.parametrize("bad", [{}, "x", None, 3])
    def test_a_field_that_is_not_a_list_is_refused(self, st, capsys, field, bad):
        _refused(st, capsys, **{field: bad})

    @pytest.mark.parametrize("field", ["needs_merge_model", "mark_changes"])
    @pytest.mark.parametrize("bad", ["x", 3, None, ["x"]])
    def test_an_item_that_is_not_an_object_is_refused(self, st, capsys, field, bad):
        _refused(st, capsys, **{field: [bad]})

    @pytest.mark.parametrize("drop", ["plugin_root", "case", "reason"])
    def test_a_mark_missing_a_key_is_refused(self, st, capsys, drop):
        m = _mark("alpha")
        del m[drop]
        _refused(st, capsys, needs_merge_model=[m])

    def test_a_mark_with_an_extra_key_is_refused(self, st, capsys):
        _refused(st, capsys, needs_merge_model=[{**_mark("alpha"), "extra": "x"}])

    @pytest.mark.parametrize("reason", BAD_REASONS)
    def test_a_mark_reason_failing_its_loader_shape_is_refused_and_not_echoed(self, st, capsys, reason):
        err = _refused(st, capsys, needs_merge_model=[_mark("alpha", reason)])
        assert "tick" not in err and "<b>" not in err

    def test_a_mark_reason_that_is_not_a_string_is_refused(self, st, capsys):
        _refused(st, capsys, needs_merge_model=[_mark("alpha", None)])

    @pytest.mark.parametrize("case", BAD_CASES)
    def test_a_mark_case_failing_its_loader_shape_is_refused(self, st, capsys, case):
        _refused(st, capsys, needs_merge_model=[_mark(case)])

    @pytest.mark.parametrize("root", BAD_ROOTS)
    def test_a_mark_plugin_root_failing_its_loader_shape_is_refused(self, st, capsys, root):
        _refused(st, capsys, needs_merge_model=[_mark("alpha", root=root)])

    @pytest.mark.parametrize("drop", ["plugin_root", "case", "change", "reason"])
    def test_a_change_missing_a_key_is_refused(self, st, capsys, drop):
        c = _change("beta", "added")
        del c[drop]
        _refused(st, capsys, mark_changes=[c])

    def test_a_change_with_an_extra_key_is_refused(self, st, capsys):
        _refused(st, capsys, mark_changes=[{**_change("beta", "added"), "extra": "x"}])

    @pytest.mark.parametrize("change", ["renamed", "ADDED", "", None, 3])
    def test_a_change_outside_the_three_values_is_refused(self, st, capsys, change):
        _refused(st, capsys, mark_changes=[{**_change("beta", "added"), "change": change}])

    @pytest.mark.parametrize("reason", BAD_REASONS)
    @pytest.mark.parametrize("change", ["added", "altered"])
    def test_a_change_reason_failing_its_loader_shape_is_refused_and_not_echoed(self, st, capsys, change, reason):
        err = _refused(st, capsys, mark_changes=[_change("beta", change, reason)])
        assert "tick" not in err and "<b>" not in err

    @pytest.mark.parametrize("change", ["added", "altered"])
    def test_a_change_that_takes_effect_with_a_null_reason_is_refused(self, st, capsys, change):
        _refused(st, capsys, mark_changes=[{**_change("beta", change), "reason": None}])

    def test_a_removal_that_carries_a_reason_is_refused(self, st, capsys):
        _refused(st, capsys, mark_changes=[{**_change("beta", "removed"), "reason": "still here"}])

    @pytest.mark.parametrize("case", BAD_CASES)
    def test_a_change_case_failing_its_loader_shape_is_refused(self, st, capsys, case):
        _refused(st, capsys, mark_changes=[_change(case, "added")])

    @pytest.mark.parametrize("root", BAD_ROOTS)
    def test_a_change_plugin_root_failing_its_loader_shape_is_refused(self, st, capsys, root):
        _refused(st, capsys, mark_changes=[_change("beta", "added", root=root)])

    def test_a_malformed_field_is_refused_on_a_status_that_prints_no_marks_too(self, st, capsys):
        st.plan(status="no-cases-mapped")
        doc = json.loads((st.dir / "plan.json").read_text())
        (st.dir / "plan.json").write_text(json.dumps({**doc, "needs_merge_model": "oops"}))
        code, out, _ = verdict(capsys, st)
        assert code == 2 and out == ""

    def test_a_refused_plan_writes_no_section_file(self, st, capsys):
        _refused(st, capsys, needs_merge_model=[_mark("alpha", "has `tick`")])
        assert not (st.dir / "eval-gate-section.md").exists()


ASK = "needs-merge-model mark"


class TestModelOnFailure:
    def test_a_push_tier_failure_on_the_push_model_names_the_model_and_asks_the_operator(self, st, capsys):
        st.plan([_entry("bad", model="sonnet", push_model="sonnet")], tier="push")
        st.result("bad", verdict="fail", passed=0, failed=3)
        code, out, _ = verdict(capsys, st)
        head = _head(out)
        assert code == 1
        assert "bad: passed 0, failed 3, errored 0 (model: sonnet)" in head
        ask = [ln for ln in head if ASK in ln]
        assert len(ask) == 1 and "bad" in ask[0] and "merge model" in ask[0]
        assert "once" in ask[0] and "main" in ask[0] and "do not add one yourself" in ask[0].lower()
        assert "`bad`: passed 0, failed 3, errored 0 (model: sonnet)" in section_of(out)

    def test_a_push_tier_failure_where_push_and_merge_models_match_has_no_ask(self, st, capsys):
        st.plan([_entry("bad", model="sonnet", push_model="sonnet", merge_model="sonnet")], tier="push")
        st.result("bad", verdict="fail", passed=0, failed=3)
        _, out, _ = verdict(capsys, st)
        assert "bad: passed 0, failed 3, errored 0 (model: sonnet)" in out and ASK not in out

    def test_a_push_tier_failure_on_a_cheaper_push_model_asks_when_merge_model_differs(self, st, capsys):
        st.plan([_entry("bad", model="sonnet", push_model="sonnet", merge_model="opus")], tier="push")
        st.result("bad", verdict="fail", passed=0, failed=3)
        _, out, _ = verdict(capsys, st)
        assert ASK in out

    def test_a_merge_tier_failure_names_the_model_without_the_ask(self, st, capsys):
        st.plan([_entry("bad", model="opus", push_model="sonnet"), _entry("same", model="sonnet", push_model="sonnet")],
                tier="merge")
        st.result("same", verdict="fail", passed=0, failed=3)
        st.result("bad", verdict="fail", passed=0, failed=3)
        _, out, _ = verdict(capsys, st)
        assert "bad: passed 0, failed 3, errored 0 (model: opus)" in _head(out)
        assert "same: passed 0, failed 3, errored 0 (model: sonnet)" in _head(out)
        assert ASK not in out

    def test_a_push_tier_failure_on_a_marked_case_running_the_merge_model_has_no_ask(self, st, capsys):
        st.plan([_entry("bad", model="opus", push_model="sonnet")], tier="push")
        st.result("bad", verdict="fail", passed=0, failed=3)
        _, out, _ = verdict(capsys, st)
        assert "(model: opus)" in out and ASK not in out

    def test_a_push_tier_failure_with_no_push_model_configured_has_no_ask(self, st, capsys):
        st.plan([_entry("bad", model="sonnet", push_model=None)], tier="push")
        st.result("bad", verdict="fail", passed=0, failed=3)
        _, out, _ = verdict(capsys, st)
        assert "(model: sonnet)" in out and ASK not in out
