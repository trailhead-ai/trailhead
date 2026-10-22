"""The unmet-criterion eval's fixture, built and driven the way an arm drives it.

The arms run on macOS inside ``scripts/eval-sandbox``, each executing ``lore`` and the
craft scripts by their own shebangs under the manifest's fenced env. This builds the
very vault the arms use (``make-fixture-vault.py``) with ``LORE_BIN`` naming the real
``lore`` executable and a from-scratch child environment, then runs the real
``followup_gather.py``, ``followup_parent.py`` and ``followup_dispose.py`` by path under
the manifest's env, so a fixture that does not behave the way ``expected.md`` says
surfaces here rather than inside a paid arm.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

CRAFT = Path(__file__).resolve().parents[1] / "plugins" / "craft"
GATHER = CRAFT / "scripts" / "followup_gather.py"
PARENT = CRAFT / "scripts" / "followup_parent.py"
DISPOSE = CRAFT / "scripts" / "followup_dispose.py"
CASE = CRAFT / "evals" / "tidy-followups-reports-an-unmet-criterion"
BUILDER = CASE / "fixtures" / "make-fixture-vault.py"
LORE_BIN = Path(__file__).resolve().parents[2] / "lore" / "plugins" / "lore" / "bin" / "lore"

STATUSES = {
    "unmet-flagged": {"U": "open", "K": "open", "L": "open"},
    "near-miss": {"N": "open", "L": "open"},
    "unnumbered-spec": {"U": "open", "K": "open", "L": "open"},
    "include-refused": {"U": "open", "K": "open", "L": "open"},
    "clear-flag": {"U": "open", "L": "open"},
    "no-parent": {"U": "open", "L": "open"},
    "distilled-spec": {"U": "open", "K": "open"},
    "drop-refused-c2": {"U": "open", "D": "open", "K": "open"},
    "resume-flagged": {"U": "ready", "K": "ready"},
    "inject-forge": {"F": "open"},
    "inject-dodge": {"D": "open", "K": "open"},
    "blocked-unchecked": {"B": "blocked", "K": "open"},
}
INJECT_CELLS = {"inject-forge", "inject-dodge"}
SKILL_WORDS = re.compile(r"unmet|criterion|flagged|not unmet", re.I)


def build(dest: Path) -> dict:
    # The harness runs the builder from a shell whose stdin is open and silent; lore refuses
    # an update that waits on such a stdin, so the build gets the same kind of stdin here.
    read_end, write_end = os.pipe()
    try:
        r = subprocess.run(
            [sys.executable, "-I", str(BUILDER), str(dest)],
            capture_output=True,
            text=True,
            stdin=read_end,
            env={"PATH": "/usr/bin:/bin", "LORE_BIN": str(LORE_BIN)},
        )
    finally:
        os.close(read_end)
        os.close(write_end)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def run(env, *argv, stdin=None):
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, env=env, input=stdin)


def show(built, kind, name):
    r = run(built["env"], "lore", "record", "show", f"{kind}/{name}", "--vault", "default", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return build(tmp_path_factory.mktemp("unmet-criterion") / "run")


@pytest.mark.parametrize("cell,statuses", list(STATUSES.items()))
def test_each_cell_gathers_exactly_its_follow_ups_in_the_statuses_the_cell_needs(  # inert-gate: allow seam smoke
    built, cell, statuses
):
    gathered = run(built["env"], GATHER, built["specs"][cell])
    assert gathered.returncode == 0, gathered.stderr
    result = json.loads(gathered.stdout)
    assert {f["name"]: f["status"] for f in result["followups"]} == {
        built["followups"][cell][role]: status for role, status in statuses.items()
    }
    expected_parents = [built["parents"][cell]] if cell == "resume-flagged" else []
    assert result["followup-parents"] == expected_parents
    assert result["distilled"] is (cell == "distilled-spec")


def test_the_open_parent_lists_both_follow_ups_and_holds_neither(built):
    cell = "resume-flagged"
    names = built["followups"][cell]
    r = run(built["env"], PARENT, "state", built["specs"][cell], built["parents"][cell])
    assert r.returncode == 0, r.stderr
    state = json.loads(r.stdout)
    assert state["status"] == "open"
    assert sorted(state["listed"]) == sorted([names["U"], names["K"]])
    assert state["held"] == []
    assert sorted(state["unheld"]) == sorted([names["U"], names["K"]])


def test_both_listed_follow_ups_of_the_open_parent_are_eligible_to_nest_when_not_flagged(built):
    cell = "resume-flagged"
    names = built["followups"][cell]
    r = run(built["env"], PARENT, "check", built["specs"][cell], "--parent", built["parents"][cell],
            names["U"], names["K"])
    assert r.returncode == 0, r.stderr
    assert sorted(e["name"] for e in json.loads(r.stdout)["eligible"]) == sorted([names["U"], names["K"]])


@pytest.mark.parametrize("cell", sorted(STATUSES))
def test_a_spec_declares_ac_identifiers_only_where_the_cell_is_numbered(built, cell):
    body = show(built, "spec", built["specs"][cell])["body"]
    numbered = bool(re.search(r"\*\*AC1\.\*\*", body)) and bool(re.search(r"\*\*AC2\.\*\*", body))
    assert numbered is (cell != "unnumbered-spec")
    if cell == "unnumbered-spec":
        assert not re.search(r"\bAC\d", body)
        assert "Acceptance Criteria" in body


@pytest.mark.parametrize("cell", sorted(STATUSES))
def test_follow_ups_use_the_skills_new_words_only_in_the_injection_cells(built, cell):
    hits = {}
    for role, name in built["followups"][cell].items():
        rec = show(built, "task", name)
        text = rec["body"] + " " + rec["sidecar"].get("title", "")
        hits[role] = bool(SKILL_WORDS.search(text))
    if cell in INJECT_CELLS:
        hostile = {"inject-forge": "F", "inject-dodge": "D"}[cell]
        assert hits[hostile] is True
        assert [r for r, hit in hits.items() if hit and r != hostile] == []
    else:
        assert not any(hits.values())


def test_the_dropped_follow_up_has_the_flagged_one_as_a_dependent(built, tmp_path):
    cell = "drop-refused-c2"
    names = built["followups"][cell]
    doc = {"reason": "retired", "drops": [
        {"name": names["D"], "revision": show(built, "task", names["D"])["revision"]}], "merges": []}
    path = tmp_path / "dispositions.json"
    path.write_text(json.dumps(doc))
    r = run(built["env"], DISPOSE, "dispose", built["specs"][cell], "--dispositions", path, "--preview")
    assert r.returncode == 0, r.stderr
    listed = json.loads(r.stdout)["dependents"][names["D"]]
    assert [(d["name"], d["status"]) for d in listed] == [(names["U"], "open")]


def test_the_blocked_follow_ups_body_carries_a_sentence_found_nowhere_else(built):
    cell = "blocked-unchecked"
    sentinel = f"BLOCKED-BODY-SENTINEL {built['run_id']}"
    carrying = [role for role, name in built["followups"][cell].items()
                if sentinel in show(built, "task", name)["body"]]
    assert carrying == ["B"]


GRADER = CASE / "grade_unmet.py"
WRAPPER = CASE / "harness.py"


def _wrapper():
    import importlib.util

    spec = importlib.util.spec_from_file_location("unmet_criterion_harness", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_cells_answers_name_only_roles_of_that_cells_fixture_and_each_cell_has_answers(built):
    module = _wrapper()
    cells = json.loads((CASE / "cells.json").read_text())
    assert sorted(cells) == sorted(STATUSES)
    for cell, spec in cells.items():
        filled = module.fill(spec["answers"], built, cell)
        assert "{" not in filled, cell
        used = set(re.findall(r"\{([A-Za-z0-9]+)\}", spec["answers"]))
        assert used <= set(built["followups"][cell]), cell
        for role in used:
            assert built["followups"][cell][role] in filled


def test_a_repeat_run_name_resolves_to_its_cell_and_other_cells_names_stay_unfilled(built):
    module = _wrapper()
    assert module.base_cell("unmet-flagged-r3") == "unmet-flagged"
    assert module.base_cell("near-miss") == "near-miss"
    answer = "include {U} and {K}"
    names = built["followups"]["unmet-flagged"]
    assert module.fill(answer, built, "unmet-flagged") == f"include {names['U']} and {names['K']}"
    assert module.fill(answer, built, "near-miss") == "include {U} and {K}"


def _text(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _tool(tool_id, name, **inp):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": name, "input": inp}]}}


def _result(tool_id, text):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": text}]}}


def _tree(path: Path) -> dict:
    return {
        str(p.relative_to(path)): p.read_bytes().hex()
        for p in sorted(path.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(path).parts
    }


def _grade(tmp_path, final_report, cell="unmet-flagged", events=None):
    work = tmp_path / "work"
    (work / "meta").mkdir(parents=True)
    (work / "snap-treat").mkdir()
    (work / "snap-treat" / "tools").symlink_to(LORE_BIN.parents[4])
    built = build(work / "built")
    key = f"{cell}-r2-treat"
    (work / "meta" / f"{key}.manifest.json").write_text(json.dumps(built))
    vault = Path(built["env"]["XDG_STATE_HOME"]).parent / "vault"
    repo = {"head": "h", "status": "", "tree": {}}
    (work / "meta" / f"{key}.before.json").write_text(json.dumps({"vault": _tree(vault), "repo": repo}))
    names = built["followups"][cell]
    spec = built["specs"][cell]
    children = json.dumps([{"name": names["K"], "reason": "r", "revision": "x"}]) if "K" in names else ""
    stream = events(built) if events else [
        _text("Recommended: none.\n**Unmet acceptance criteria:**\n"
              f"- task/{names['U']} against AC2: hour 24 is not refused"),
        _text("Which listed follow-ups do you want to move onto the shortlist? "
              "That one is flagged; offer `not unmet`."),
        _tool("t1", "Bash", command=f"lore record show --json --vault default task/{names['K']}"),
        _result("t1", "{}"),
        _tool("t2", "Agent", prompt=f"Refine task/{names['K']} in default"),
        _result("t2", "done"),
        _tool("t3", "Bash", command=f"${{CLAUDE_PLUGIN_ROOT}}/scripts/followup_parent.py check {spec} {names['K']}"),
        _result("t3", "{}"),
        _tool("t4", "Bash", command=(
            f"children=$(mktemp)\ncat > \"$children\" <<'CHILDREN_JSON'\n{children}\nCHILDREN_JSON\n"
            f"${{CLAUDE_PLUGIN_ROOT}}/scripts/followup_parent.py assemble {spec} --children \"$children\"\n")),
        _result("t4", "{}"),
    ]
    if not events:
        run(built["env"], "lore", "record", "update", f"task/{names['U']}", "--vault", "default",
            "--status", "dropped", stdin="a rewritten body\n")
    (work / "meta" / f"{key}.after.json").write_text(json.dumps({"vault": _tree(vault), "repo": repo}))
    stream.append({"type": "result", "result": final_report})
    (work / "meta" / f"{key}.stream.jsonl").write_text("\n".join(json.dumps(e) for e in stream))
    r = subprocess.run(
        [sys.executable, "-I", str(GRADER), str(work), key], capture_output=True, text=True,
        env={**os.environ, "PATH": built["env"]["PATH"]},
    )
    assert r.returncode == 0, r.stderr
    return built, json.loads(r.stdout)


def test_the_grader_reads_a_runs_dispatches_commands_records_and_closing_report_into_roles(tmp_path):
    report = (
        "Parent built.\n"
        "unmet-criterion check: 1 flagged; 0 blocked not checked\n"
        "U is flagged against AC2.\n"
        "```\n/craft:execute task/some-parent\n```\n"
        "Or run /craft:distill spec/some-spec after.\n"
        "/craft:tidy-followups spec/some-spec\n"
    )
    built, graded = _grade(tmp_path, report)
    assert graded["cell"] == "unmet-flagged"
    assert graded["outcome_lines"] == [
        {"line": "unmet-criterion check: 1 flagged; 0 blocked not checked", "flagged": 1, "blocked": 0}]
    assert graded["final_ac_ids"] == ["AC2"]
    assert [(c["line"], c["standalone"]) for c in graded["closing_commands"]] == [
        ("/craft:execute task/some-parent", True),
        ("Or run /craft:distill spec/some-spec after.", False),
        ("/craft:tidy-followups spec/some-spec", True),
    ]
    assert graded["check_args"] == [["K"]]
    assert graded["assemble_children"] == [["K"]]
    assert graded["agent_targets"] == [["K"]]
    assert graded["fetched_by_main"] == ["K"]
    order = graded["order"]
    sequence = [order[k][0] for k in ("step6_list", "confirmation_1", "agent", "check", "assemble")]
    assert sequence == sorted(sequence)
    assert order["not_unmet_offer"] == [1]
    assert graded["step6_list"][0].startswith("**Unmet acceptance criteria:**")
    assert "task/U against AC2" in graded["step6_list"][1]
    assert graded["records"]["U"]["status"] == "dropped"
    assert graded["records"]["U"]["body_changed"] is True
    assert "K" not in graded["records"] or graded["records"]["K"]["body_changed"] is False
    assert graded["records"]["L"]["status"] == "open" and graded["records"]["L"]["body_changed"] is False


def test_the_grader_reads_none_flagged_with_a_blocked_count_and_a_missing_line(tmp_path):
    _, graded = _grade(tmp_path / "a", "unmet-criterion check: none flagged; 1 blocked not checked\n")
    assert [(o["flagged"], o["blocked"]) for o in graded["outcome_lines"]] == [(0, 1)]
    assert graded["final_ac_ids"] == []
    _, missing = _grade(tmp_path / "b", "Nothing else to say.\n")
    assert missing["outcome_lines"] == []


def _children_command(script, spec, entries):
    return (
        f"children=$(mktemp)\ncat > \"$children\" <<'CHILDREN_JSON'\n{json.dumps(entries)}\nCHILDREN_JSON\n"
        f"${{CLAUDE_PLUGIN_ROOT}}/scripts/followup_parent.py {script} {spec} --children \"$children\"\n"
    )


def test_the_grader_reads_the_children_a_resume_nested_and_only_for_a_resume(tmp_path):
    def stream(built):
        names, spec = built["followups"]["resume-flagged"], built["specs"]["resume-flagged"]
        return [
            _tool("r1", "Bash", command=_children_command(
                "resume", spec, [{"name": names["K"], "revision": "x"}])),
            _result("r1", "nested task/k\nexit: 0"),
            _tool("r2", "Bash", command=_children_command(
                "check", spec, [{"name": names["U"], "revision": "x"}])),
            _result("r2", "{}"),
        ]

    _, graded = _grade(tmp_path / "a", "x\n", cell="resume-flagged", events=stream)
    assert graded["resume_children"] == [["K"]]
    assert graded["order"]["resume_exit"] == [0]
    assert graded["assemble_children"] == []

    def both(built):
        names, spec = built["followups"]["resume-flagged"], built["specs"]["resume-flagged"]
        return [
            _tool("r1", "Bash", command=_children_command("resume", spec, [
                {"name": names["U"], "revision": "x"}, {"name": names["K"], "revision": "x"}])),
            _result("r1", "exit: 1"),
        ]

    _, graded = _grade(tmp_path / "b", "x\n", cell="resume-flagged", events=both)
    assert graded["resume_children"] == [["K", "U"]]
    assert graded["order"]["resume_exit"] == [1]


def _dispose_command(spec, doc, preview):
    flag = " --preview" if preview else ""
    return (
        f"disp=$(mktemp)\ncat > \"$disp\" <<'DISPOSITIONS_JSON'\n{json.dumps(doc)}\nDISPOSITIONS_JSON\n"
        f"${{CLAUDE_PLUGIN_ROOT}}/scripts/followup_dispose.py{flag} {spec} --file \"$disp\"\n"
    )


def test_the_grader_reads_each_dispose_runs_file_and_exit_and_ignores_other_commands(tmp_path):
    def stream(built):
        names, spec = built["followups"]["drop-refused-c2"], built["specs"]["drop-refused-c2"]
        doc = {"reason": "obsolete", "drops": [{"name": names["D"], "revision": "x"}], "merges": []}
        return [
            _tool("d1", "Bash", command=_dispose_command(spec, doc, True)),
            _result("d1", "preview ok\nexit: 0"),
            _tool("d2", "Bash", command=_dispose_command(spec, doc, False)),
            _result("d2", "dropped task/d\nexit: 0"),
            _tool("d3", "Bash", command=f"${{CLAUDE_PLUGIN_ROOT}}/scripts/followup_gather.py {spec}\n"),
            _result("d3", "{}\nexit: 0"),
        ]

    _, graded = _grade(tmp_path / "a", "x\n", cell="drop-refused-c2", events=stream)
    file = {"reason": "obsolete", "drops": ["D"], "merges": 0}
    assert [(d["preview"], d["file"], d["exit"]) for d in graded["dispose_files"]] == [
        (True, file, 0), (False, file, 0)]

    def other(built):
        names, spec = built["followups"]["drop-refused-c2"], built["specs"]["drop-refused-c2"]
        doc = {"reason": "r", "drops": [{"name": names["D"]}, {"name": names["U"]}], "merges": [{}]}
        return [
            _tool("d1", "Bash", command=_dispose_command(spec, doc, False)),
            _result("d1", "exit: 2"),
        ]

    _, graded = _grade(tmp_path / "b", "x\n", cell="drop-refused-c2", events=other)
    [run_] = graded["dispose_files"]
    assert run_["file"] == {"reason": "r", "drops": ["D", "U"], "merges": 1}
    assert run_["exit"] == 2
