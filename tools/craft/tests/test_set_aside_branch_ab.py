"""The set-aside branch-load A/B: the shared driver's trace readers, the arm-diff check, the
branch-read grader, the fixture scaffolds, and the per-arm plugin copy. Everything is run through the
experiments' own driver (`ab.py`) and the real
scaffold scripts."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CRAFT = REPO / "tools" / "craft" / "plugins" / "craft"
FOUND = REPO / "tools" / "craft" / "experiments" / "ab-found-work-branch-load"


EXP = REPO / "tools" / "craft" / "experiments" / "ab-set-aside-branch-load"

_spec = importlib.util.spec_from_file_location("ab_set_aside_under_test", EXP / "ab.py")
ab = importlib.util.module_from_spec(_spec)
sys.modules["ab_set_aside_under_test"] = ab
_spec.loader.exec_module(ab)
shared = ab.shared

LOAD = "read `../_shared/waiting-criterion.md` now and follow it in full."
CASE_A = "ab-set-aside-a-facts-in-the-report"
CASE_B = "ab-set-aside-b-facts-on-the-record-only"
CASE_C = "ab-set-aside-c-context-problem"
BRANCH_READ_GRADER = "branch-file-was-read.md"


# --- 6. a trace line whose `message` is a string, not an object -------------------------------


def _jsonl(*entries):
    return "".join(json.dumps(e) + "\n" for e in entries)


def _read(path):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": path}}]}}


STRING_MESSAGE = {"type": "system", "message": "retrying after a rate limit"}


@pytest.mark.parametrize("command, extra", [("count-reads", []), ("evidence", ["/work/plugin", "inline"])])
def test_the_trace_commands_survive_a_line_whose_message_is_a_string(command, extra, tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(_jsonl(
        STRING_MESSAGE,
        _read("/work/plugin/skills/_shared/found-work.md"),
        STRING_MESSAGE,
    ))
    done = subprocess.run(
        [sys.executable, str(FOUND / "ab.py"), command, str(trace), *extra],
        capture_output=True, text=True, timeout=60,
    )
    assert "AttributeError" not in done.stderr, done.stderr
    if command == "count-reads":
        assert (done.returncode, done.stdout.strip()) == (0, "1")
    else:
        assert "never read" in done.stdout


def test_a_string_message_line_does_not_hide_the_blocks_around_it():
    trace = [STRING_MESSAGE, _read("/work/plugin/skills/_shared/found-work.md")]
    assert shared.count_branch_reads(trace) == 1


def _arms(tmp_path):
    """A scratch copy of both arms the mutation tests may damage."""
    dest = tmp_path / "arms"
    shutil.copytree(EXP / "arms", dest)
    return dest / "inline", dest / "branch"


# --- 1. the arm-diff check -------------------------------------------------------------------


def test_arm_diff_passes_on_the_real_arms():
    assert shared.check_arms(EXP / "arms" / "inline", EXP / "arms" / "branch", ab.EXPERIMENT) == []


def test_the_driver_check_command_agrees_with_the_function():
    done = subprocess.run([sys.executable, str(EXP / "ab.py"), "check"], capture_output=True, text=True, timeout=60)
    assert (done.returncode, done.stdout.strip()) == (0, "arm diff ok")


def test_arm_diff_fails_when_one_moved_byte_is_changed(tmp_path):
    inline, branch = _arms(tmp_path)
    moved = branch / "waiting-criterion.md"
    data = bytearray(moved.read_bytes())
    heading = b"### A criterion waiting on something outside the work\n"
    i = data.index(heading) + len(heading) + 20
    data[i] = ord("X") if data[i] != ord("X") else ord("Y")
    moved.write_bytes(bytes(data))
    problems = shared.check_arms(inline, branch, ab.EXPERIMENT)
    assert any("moved" in p for p in problems), problems


def test_arm_diff_fails_when_one_load_line_is_removed(tmp_path):
    inline, branch = _arms(tmp_path)
    proc = branch / "execute.md"
    lines = proc.read_text().splitlines(keepends=True)
    at = next(i for i, ln in enumerate(lines) if LOAD in ln)
    del lines[at]
    proc.write_text("".join(lines))
    problems = shared.check_arms(inline, branch, ab.EXPERIMENT)
    assert problems, "a branch arm missing a load line passed the check"


def test_arm_diff_fails_on_a_change_that_is_not_a_load_line(tmp_path):
    inline, branch = _arms(tmp_path)
    proc = branch / "execute.md"
    text = proc.read_text()
    assert "## Model Selection" in text
    proc.write_text(text.replace("## Model Selection", "## Model Choice", 1))
    problems = shared.check_arms(inline, branch, ab.EXPERIMENT)
    assert any("outside a load line" in p for p in problems), problems


def test_arm_diff_fails_when_a_citation_survives(tmp_path):
    inline, branch = _arms(tmp_path)
    proc = branch / "execute.md"
    text = proc.read_text()
    at = text.index(LOAD)
    proc.write_text(text[:at] + "see [it](#a-criterion-waiting-on-something-outside-the-work) " + text[at:])
    problems = shared.check_arms(inline, branch, ab.EXPERIMENT)
    assert problems, problems


# --- 2. the branch-read grader ---------------------------------------------------------------


def _bash(command):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": command}}]}}


GRADER = EXP / CASE_A / "graders" / BRANCH_READ_GRADER
PLUGIN = "/work/plugin"


def test_branch_read_grader_is_red_with_no_read_of_the_branch_file():
    trace = [_read(f"{PLUGIN}/skills/_shared/execute.md"), _read(f"{PLUGIN}/skills/_shared/found-work.md")]
    assert shared.grade_tool_used(GRADER, trace) is False


def test_branch_read_grader_is_green_on_a_read_of_the_branch_file():
    trace = [_read(f"{PLUGIN}/skills/_shared/execute.md"), _read(f"{PLUGIN}/skills/_shared/waiting-criterion.md")]
    assert shared.grade_tool_used(GRADER, trace) is True


def test_branch_read_grader_is_not_fooled_by_a_non_read_mention():
    trace = [_bash("cat /work/plugin/skills/_shared/waiting-criterion.md")]
    assert shared.grade_tool_used(GRADER, trace) is False


def test_branch_read_grader_is_not_fooled_by_a_different_file():
    trace = [_read("/work/plugin/skills/_shared/not-waiting-criterion.md.orig")]
    assert shared.grade_tool_used(GRADER, trace) is False


# --- the per-arm plugin copy and the evidence a run read that arm's procedure -----------------


def test_each_arm_copy_carries_that_arms_procedure_and_leaves_the_live_tree_alone(tmp_path):
    live = (CRAFT / "skills" / "_shared" / "execute.md").read_bytes()
    inline = shared.build_arm_plugin("inline", CASE_A, tmp_path / "i", exp=ab.EXPERIMENT)
    branch = shared.build_arm_plugin("branch", CASE_A, tmp_path / "b", exp=ab.EXPERIMENT)
    assert (
        (inline / "skills" / "_shared" / "execute.md").read_bytes()
        == (EXP / "arms" / "inline" / "execute.md").read_bytes()
    )
    assert (
        (branch / "skills" / "_shared" / "execute.md").read_bytes()
        == (EXP / "arms" / "branch" / "execute.md").read_bytes()
    )
    assert (
        (branch / "skills" / "_shared" / "waiting-criterion.md").read_bytes()
        == (EXP / "arms" / "branch" / "waiting-criterion.md").read_bytes()
    )
    assert not (inline / "skills" / "_shared" / "waiting-criterion.md").exists()
    assert (CRAFT / "skills" / "_shared" / "execute.md").read_bytes() == live


def test_the_arm_copy_holds_only_the_requested_case_and_the_branch_read_grader_only_where_it_applies(tmp_path):
    def graders(arm, case):
        root = shared.build_arm_plugin(arm, case, tmp_path / f"{arm}-{case}", exp=ab.EXPERIMENT)
        cases = sorted(p.parent.name for p in (root / "evals").glob("**/case.yaml"))
        assert cases == [case]
        return sorted(p.name for p in (root / "evals").glob(f"**/{case}/graders/*.md"))

    assert BRANCH_READ_GRADER in graders("branch", CASE_A)
    assert BRANCH_READ_GRADER not in graders("inline", CASE_A)


def _skill_base_read(tool_id, path, body):
    return [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": path}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": body}]}},
    ]


def test_evidence_accepts_each_arm_only_for_its_own_procedure_text():
    root = "/tmp/copy/craft"
    path = f"{root}/skills/_shared/execute.md"
    inline_text = "1\tsee [x](#a-criterion-waiting-on-something-outside-the-work) says\n"
    branch_text = f"1\twhen blocked, {LOAD}\n"
    assert shared.procedure_evidence(_skill_base_read("a", path, inline_text), root, "inline", ab.EXPERIMENT) == []
    assert shared.procedure_evidence(_skill_base_read("a", path, branch_text), root, "branch", ab.EXPERIMENT) == []
    assert shared.procedure_evidence(_skill_base_read("a", path, branch_text), root, "inline", ab.EXPERIMENT)
    assert shared.procedure_evidence(_skill_base_read("a", path, inline_text), root, "branch", ab.EXPERIMENT)


def test_the_branch_file_reads_in_a_trace_are_counted_not_graded():
    trace = [_read(f"{PLUGIN}/skills/_shared/waiting-criterion.md"), _bash("cat waiting-criterion.md"),
             _read(f"{PLUGIN}/skills/_shared/execute.md"), _read(f"{PLUGIN}/skills/_shared/waiting-criterion.md"),
             _read(f"{PLUGIN}/skills/_shared/found-work.md")]
    assert shared.count_branch_reads(trace, ab.EXPERIMENT) == 2
    assert shared.count_branch_reads([], ab.EXPERIMENT) == 0


# --- 3. the fixtures' scaffold seeding, checked through the seeded tools -----------------------


def _scaffold(case, tmp_path):
    home = tmp_path / "home"
    cwd = home / "cwd"
    cwd.mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "LORE_EMAIL": "eval@example.invalid"}
    done = subprocess.run(
        ["bash", str(EXP / case / "fixtures" / "scaffold.sh")], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=180,
    )
    assert done.returncode == 0, done.stderr
    return cwd, env


def _show(cwd, env, record):
    shown = subprocess.run(
        [str(cwd / "lore"), "record", "show", record], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert shown.returncode == 0, shown.stderr
    return shown.stdout


@pytest.mark.parametrize("case", [CASE_A, CASE_B, CASE_C])
def test_every_fixture_seeds_the_spec_the_condition_is_tested_against(case, tmp_path):
    cwd, env = _scaffold(case, tmp_path)
    spec = _show(cwd, env, "spec/invoice-members")
    assert all(fact in spec for fact in ("has not applied", "registration", "several weeks", "no test version"))
    parent = _show(cwd, env, "task/members-can-download-invoices-with-sales-tax")
    assert re.search(r"\*\*Covers:\*\*[^\n]*AC1, AC2", parent)


def test_the_seeded_set_aside_writer_logs_its_call_and_really_writes_the_spec(tmp_path):
    cwd, env = _scaffold(CASE_A, tmp_path)
    entries = tmp_path / "entries.json"
    entries.write_text(json.dumps([{"criterion": "AC2", "waits_on": "the provider's tax registration"}]))
    done = subprocess.run(
        [str(cwd / "scripts" / "set_aside.py"), "--spec", "invoice-members", "--entries", str(entries),
         "--vault", "eval", "--parent", "members-can-download-invoices-with-sales-tax"],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert (cwd / "lore-calls.log").read_text().startswith("set_aside.py --spec invoice-members")
    assert re.search(r"## Waiting.*\*\*AC2\.\*\* set aside", _show(cwd, env, "spec/invoice-members"), re.S)


# --- the bar's fingerprint ------------------------------------------------


def test_the_recorded_fingerprints_match_each_experiments_bar():
    for exp in (ab.EXPERIMENT, shared.FOUND_WORK):
        text = (exp.dir / "expected.md").read_text()
        recorded = re.search(r"recorded before any cell: `([0-9a-f]{40})`", text).group(1)
        done = subprocess.run(
            ["git", "hash-object", "--stdin"], input=shared.bar_text(exp=exp), capture_output=True, text=True
        )
        assert done.stdout.strip() == recorded, exp.dir.name
