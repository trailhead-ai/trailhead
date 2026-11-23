"""The Found-work branch-load A/B: the arm-diff check, the branch-read grader, the fixture
scaffolds, and the per-arm plugin copy. The arm
files, graders and scaffolds are run through the experiment's own driver (`ab.py`) and the real
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
EXP = REPO / "tools" / "craft" / "experiments" / "ab-found-work-branch-load"

_spec = importlib.util.spec_from_file_location("ab_under_test", EXP / "ab.py")
ab = importlib.util.module_from_spec(_spec)
sys.modules["ab_under_test"] = ab
_spec.loader.exec_module(ab)

LOAD = "read `../_shared/found-work.md` now and follow it in full."
BRANCH_READ_GRADER = "branch-file-was-read.md"


def _arms(tmp_path):
    """A scratch copy of both arms the mutation tests may damage."""
    dest = tmp_path / "arms"
    shutil.copytree(EXP / "arms", dest)
    return dest / "inline", dest / "branch"


# --- 1. the arm-diff check -------------------------------------------------------------------


def test_arm_diff_passes_on_the_real_arms():
    assert ab.check_arms(EXP / "arms" / "inline", EXP / "arms" / "branch") == []


def test_arm_diff_fails_when_one_moved_byte_is_changed(tmp_path):
    inline, branch = _arms(tmp_path)
    moved = branch / "found-work.md"
    data = bytearray(moved.read_bytes())
    i = data.index(b"## Found work\n") + len(b"## Found work\n") + 20
    data[i] = ord("X") if data[i] != ord("X") else ord("Y")
    moved.write_bytes(bytes(data))
    problems = ab.check_arms(inline, branch)
    assert any("moved" in p for p in problems), problems


def test_arm_diff_fails_when_one_load_line_is_removed(tmp_path):
    inline, branch = _arms(tmp_path)
    proc = branch / "execute.md"
    lines = proc.read_text().splitlines(keepends=True)
    at = next(i for i, ln in enumerate(lines) if LOAD in ln)
    del lines[at]
    proc.write_text("".join(lines))
    problems = ab.check_arms(inline, branch)
    assert problems, "a branch arm missing a load line passed the check"


def test_arm_diff_fails_on_a_change_that_is_not_a_load_line(tmp_path):
    inline, branch = _arms(tmp_path)
    proc = branch / "execute.md"
    text = proc.read_text()
    assert "## Model Selection" in text
    proc.write_text(text.replace("## Model Selection", "## Model Choice", 1))
    problems = ab.check_arms(inline, branch)
    assert any("outside a load line" in p for p in problems), problems


def test_arm_diff_fails_when_a_citation_survives(tmp_path):
    inline, branch = _arms(tmp_path)
    proc = branch / "execute.md"
    text = proc.read_text()
    at = text.index(LOAD)
    proc.write_text(text[:at] + "see [Found work](#found-work) " + text[at:])
    problems = ab.check_arms(inline, branch)
    assert problems, problems


# --- 2. the branch-read grader ---------------------------------------------------------------


def _read(path):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": path}}]}}


def _bash(command):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": command}}]}}


GRADER = EXP / "ab-found-work-a-spec-in-prompt" / "graders" / BRANCH_READ_GRADER
PLUGIN = "/work/plugin"


def test_branch_read_grader_is_red_with_no_read_of_the_branch_file():
    trace = [_read(f"{PLUGIN}/skills/_shared/execute.md"), _read(f"{PLUGIN}/skills/_shared/escalation.md")]
    assert ab.grade_tool_used(GRADER, trace) is False


def test_branch_read_grader_is_green_on_a_read_of_the_branch_file():
    trace = [_read(f"{PLUGIN}/skills/_shared/execute.md"), _read(f"{PLUGIN}/skills/_shared/found-work.md")]
    assert ab.grade_tool_used(GRADER, trace) is True


def test_branch_read_grader_is_not_fooled_by_a_non_read_mention():
    trace = [_bash("cat /work/plugin/skills/_shared/found-work.md")]
    assert ab.grade_tool_used(GRADER, trace) is False


def test_branch_read_grader_is_not_fooled_by_a_different_file():
    trace = [_read("/work/plugin/skills/_shared/not-found-work.md.orig")]
    assert ab.grade_tool_used(GRADER, trace) is False


# --- 4. the per-arm plugin copy and the evidence a run read that arm's procedure ---------------


@pytest.fixture
def fake_craft(tmp_path):
    root = tmp_path / "craft"
    (root / "skills" / "_shared").mkdir(parents=True)
    (root / "skills" / "_shared" / "execute.md").write_text("live procedure\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not for the copy\n")
    os.symlink(outside, root / "skills" / "escape")
    return root


def test_the_arm_copy_does_not_follow_symlinks(tmp_path, fake_craft):
    dest = tmp_path / "armcopy"
    ab.build_arm_plugin("inline", None, dest, craft_root=fake_craft)
    assert os.path.islink(dest / "skills" / "escape")
    assert os.readlink(dest / "skills" / "escape") == str(tmp_path / "outside")
    assert (dest / "skills" / "_shared" / "execute.md").read_bytes() == (EXP / "arms" / "inline" / "execute.md").read_bytes()


def test_each_arm_copy_carries_that_arms_procedure_and_leaves_the_live_tree_alone(tmp_path):
    live = (CRAFT / "skills" / "_shared" / "execute.md").read_bytes()
    inline = ab.build_arm_plugin("inline", "ab-found-work-a-spec-in-prompt", tmp_path / "i")
    branch = ab.build_arm_plugin("branch", "ab-found-work-a-spec-in-prompt", tmp_path / "b")
    assert (inline / "skills" / "_shared" / "execute.md").read_bytes() == (EXP / "arms" / "inline" / "execute.md").read_bytes()
    assert (branch / "skills" / "_shared" / "execute.md").read_bytes() == (EXP / "arms" / "branch" / "execute.md").read_bytes()
    assert (branch / "skills" / "_shared" / "found-work.md").read_bytes() == (EXP / "arms" / "branch" / "found-work.md").read_bytes()
    assert not (inline / "skills" / "_shared" / "found-work.md").exists()
    assert (CRAFT / "skills" / "_shared" / "execute.md").read_bytes() == live


def test_the_arm_copy_holds_only_the_requested_case_and_the_branch_read_grader_only_where_it_applies(tmp_path):
    def graders(arm, case):
        root = ab.build_arm_plugin(arm, case, tmp_path / f"{arm}-{case}")
        cases = sorted(p.parent.name for p in (root / "evals").glob("**/case.yaml"))
        assert cases == [case]
        return sorted(p.name for p in (root / "evals").glob(f"**/{case}/graders/*.md"))

    assert BRANCH_READ_GRADER in graders("branch", "ab-found-work-a-spec-in-prompt")
    assert BRANCH_READ_GRADER not in graders("inline", "ab-found-work-a-spec-in-prompt")
    assert BRANCH_READ_GRADER not in graders("branch", "ab-found-work-c-no-spec-link")


def test_the_scaffold_finds_the_repo_from_the_arm_copy(tmp_path):
    root = ab.build_arm_plugin("branch", "ab-found-work-a-spec-in-prompt", tmp_path / "b")
    marker = root / "evals" / "ab-found-work-branch-load" / "repo-root"
    assert Path(marker.read_text().strip()) == REPO


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
    inline_text = "1\tsee [Found work](#found-work) says\n"
    branch_text = f"1\twhen linked, {LOAD}\n"
    assert ab.procedure_evidence(_skill_base_read("a", path, inline_text), root, "inline") == []
    assert ab.procedure_evidence(_skill_base_read("a", path, branch_text), root, "branch") == []
    assert ab.procedure_evidence(_skill_base_read("a", path, branch_text), root, "inline")
    assert ab.procedure_evidence(_skill_base_read("a", path, inline_text), root, "branch")


def test_evidence_rejects_a_procedure_read_from_outside_the_arm_copy():
    live = "/repo/tools/craft/plugins/craft/skills/_shared/execute.md"
    trace = _skill_base_read("a", live, "1\tsee [Found work](#found-work) says\n")
    assert ab.procedure_evidence(trace, "/tmp/copy/craft", "inline")


def test_evidence_rejects_a_run_that_also_read_the_live_procedure():
    root = "/tmp/copy/craft"
    own = _skill_base_read("a", f"{root}/skills/_shared/execute.md", "1\tsee [Found work](#found-work) says\n")
    live = _skill_base_read("b", "/repo/tools/craft/plugins/craft/skills/_shared/execute.md", "1\tx\n")
    assert ab.procedure_evidence(own, root, "inline") == []
    assert ab.procedure_evidence(own + live, root, "inline")


def test_evidence_rejects_a_run_that_never_read_the_procedure():
    assert ab.procedure_evidence([], "/tmp/copy/craft", "inline")


# --- 3. fixture (b) seeds the spec link on the record, not in the prompt ----------------------


def _scaffold(case, tmp_path):
    home = tmp_path / "home"
    cwd = home / "cwd"
    cwd.mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "LORE_EMAIL": "eval@example.invalid"}
    done = subprocess.run(
        ["bash", str(EXP / case / "fixtures" / "scaffold.sh")], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return cwd, env


def _task_sidecar(cwd, env):
    shown = subprocess.run(
        [str(cwd / "lore"), "record", "show", "task/export-csv-build", "--json"],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=60,
    )
    assert shown.returncode == 0, shown.stderr
    return json.loads(shown.stdout)["sidecar"]


def _prompt(case):
    return (EXP / case / "prompt.md").read_text()


@pytest.mark.parametrize(
    "case, linked, in_prompt",
    [
        ("ab-found-work-a-spec-in-prompt", True, True),
        ("ab-found-work-b-spec-on-record-only", True, False),
        ("ab-found-work-c-no-spec-link", False, False),
    ],
)
def test_the_spec_link_is_on_the_record_per_fixture_and_in_the_prompt_only_for_a(case, linked, in_prompt, tmp_path):
    cwd, env = _scaffold(case, tmp_path)
    related = _task_sidecar(cwd, env).get("related", {})
    assert (related.get("spec") == ["export-invoices"]) is linked
    assert ("export-invoices" in _prompt(case)) is in_prompt


# --- 5. the runner grants and the bar ------------------------------------------------------------


def test_runner_grants_are_read_from_the_craft_mapping():
    mapping = json.loads((CRAFT / "evals" / "eval-gate.json").read_text())
    args = ab.runner_args()
    assert args[0] == "--allow-tools"
    assert args[1:] == mapping["runner"]["allow_tools"]


# --- the bar's fingerprint and the fixture (c) read count ---------------------------------------


def test_the_bar_text_is_only_what_lies_between_its_delimiters(tmp_path):
    doc = tmp_path / "expected.md"
    doc.write_text("intro\n\n<!-- bar-begin -->\nfloor 8/10\n<!-- bar-end -->\n\nverdict: pending\n")
    first = ab.bar_text(doc)
    assert first == "floor 8/10\n"
    doc.write_text("intro changed\n\n<!-- bar-begin -->\nfloor 8/10\n<!-- bar-end -->\n\nverdict: pass\n")
    assert ab.bar_text(doc) == first
    doc.write_text("intro\n\n<!-- bar-begin -->\nfloor 7/10\n<!-- bar-end -->\n")
    assert ab.bar_text(doc) != first


def test_the_real_expected_md_has_exactly_one_bar():
    assert ab.bar_text(EXP / "expected.md").strip()


def test_the_branch_file_reads_in_a_trace_are_counted_not_graded():
    trace = [_read(f"{PLUGIN}/skills/_shared/found-work.md"), _bash("cat found-work.md"),
             _read(f"{PLUGIN}/skills/_shared/execute.md"), _read(f"{PLUGIN}/skills/_shared/found-work.md")]
    assert ab.count_branch_reads(trace) == 2
    assert ab.count_branch_reads([]) == 0
