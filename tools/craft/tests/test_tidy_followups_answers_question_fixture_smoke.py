"""The answers-a-refinement-question eval's fixture, built and driven the way an arm drives it.

The arms run on macOS inside ``scripts/eval-sandbox``, each executing ``lore`` and the craft scripts
by their own shebangs under the manifest's fenced env. This builds the very vault the arms use
(``make-fixture-vault.py``) with ``LORE_BIN`` naming the real ``lore`` executable, then runs the real
``followup_parent.py`` by path under the manifest's env, so a fixture that does not put the section,
the answer or the shim where a cell needs it surfaces here and not inside a paid arm.
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
CASE = CRAFT / "evals" / "tidy-followups-answers-a-refinement-question"
PARENT = CRAFT / "scripts" / "followup_parent.py"
SKILL = CRAFT / "skills" / "tidy-followups" / "SKILL.md"
REPO = Path(__file__).resolve().parents[3]
CELLS = json.loads((CASE / "cells.json").read_text())

sys.path.insert(0, str(CRAFT / "evals" / "tidy-followups-builds-one-parent"))
sys.path.insert(0, str(CRAFT / "scripts"))
import driver  # noqa: E402

_spec = importlib.util.spec_from_file_location("answers_harness", CASE / "harness.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)
import capture_scan  # noqa: E402
import scrub as capture_scrub  # noqa: E402


def build(dest: Path) -> dict:
    """The vault an arm runs against, built by the harness's own build step with the repo's `lore`."""
    dest.mkdir(parents=True)
    return harness.old.build(dest.parent, dest, REPO)


def run(env, *argv, stdin=None):
    return subprocess.run(
        [str(a) for a in argv], capture_output=True, text=True, env=env,
        input=stdin if stdin is not None else "", timeout=120,
    )


def show(built, name):
    r = run(built["env"], "lore", "record", "show", f"task/{name}", "--vault", "default", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def check(built, cell, *roles, env=None):
    names = built["followups"][cell]
    r = run(env or built["env"], PARENT, "check", built["specs"][cell], *[names[x] for x in roles])
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def question(built, cell, role):
    names = built["followups"][cell]
    r = run(built["env"], PARENT, "question", built["specs"][cell], names[role])
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def answer(built, cell, role, text, env=None, preview=False, recommended=False, confirm=None, tmp=None):
    names = built["followups"][cell]
    argv = [PARENT, "answer", built["specs"][cell], names[role]]
    if recommended:
        argv += ["--recommended"]
    else:
        f = Path(tmp) / "answer.json"
        f.write_text(json.dumps(text))
        argv += ["--answer-file", f]
    if preview:
        return run(env or built["env"], *argv, "--preview")
    if confirm is None:
        confirm = question(built, cell, role)["questions"][0]["confirm"]
    return run(env or built["env"], *argv, "--confirm", confirm)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return build(tmp_path_factory.mktemp("answers-a-question") / "run")


SEEDED = ["answered", "dependent", "skip", "no-code", "upfront", "answer-write-fails", "refinement-fails",
          "hostile", "accept-recommended", "altered"]


@pytest.mark.parametrize("cell", SEEDED)
def test_a_seeded_cell_has_check_report_q1_as_an_unresolved_question_not_yet_answered(  # inert-gate: allow seam smoke
    built, cell
):
    left = {e["name"]: e for e in check(built, cell, "Q1")["left-out"]}
    entry = left[built["followups"][cell]["Q1"]]
    assert entry["reason"] == "unresolved-question"
    assert "answered" not in entry or entry["answered"] is False


def test_a_leftover_answer_makes_check_report_the_unresolved_question_as_answered(built):
    entry = check(built, "leftover-answer", "Q1")["left-out"][0]
    assert (entry["reason"], entry["answered"]) == ("unresolved-question", True)


def test_every_seeded_question_is_askable_with_its_own_code(built):
    codes = set()
    for cell in SEEDED:
        asked = question(built, cell, "Q1")
        assert asked["unaskable"] == []
        (q,) = asked["questions"]
        assert q["question"] and q["evidence"] and q["recommended"]
        codes.add(q["confirm"])
    assert len(codes) == len(SEEDED)


def test_the_hostile_cell_plants_its_marker_in_what_the_question_read_returns_and_no_other_cell_does(built):
    marker = CELLS["hostile"]["planted"]
    (q,) = question(built, "hostile", "Q1")["questions"]
    assert marker in q["evidence"] + q["recommended"]
    for cell in SEEDED:
        if cell != "hostile":
            (other,) = question(built, cell, "Q1")["questions"]
            assert marker not in json.dumps(other)


def update(built, name, *flags, stdin=None):
    r = run(built["env"], "lore", "record", "update", f"task/{name}", "--vault", "default", *flags, stdin=stdin)
    assert r.returncode == 0, r.stderr


def test_the_dependent_follow_up_waits_on_q1_until_q1_is_ready_and_then_comes_in_with_it(tmp_path):
    built = build(tmp_path / "run")
    names = built["followups"]["dependent"]
    update(built, names["D1"], "--status", "ready")
    waiting = {e["name"]: e for e in check(built, "dependent", "Q1", "D1")["left-out"]}
    assert waiting[names["D1"]] == {"name": names["D1"], "reason": "depends-outside", "dependency": names["Q1"]}
    body = show(built, names["Q1"])["body"].split("\n\n## Refine")[0]
    update(built, names["Q1"], "--status", "ready", stdin=body + "\n")
    both = check(built, "dependent", "Q1", "D1")
    assert sorted(e["name"] for e in both["eligible"]) == sorted([names["Q1"], names["D1"]])
    assert both["left-out"] == []


def test_the_answer_fail_shim_fails_only_the_write_carrying_the_answer_and_logs_the_hit(built, tmp_path):
    names = built["followups"]["answer-write-fails"]
    env = built["answerfail_env"]
    log = Path(env["XDG_STATE_HOME"]) / "shim-hits.log"
    other = run(env, "lore", "record", "update", f"task/{names['Q1']}", "--vault", "default", "--title",
                "Tide cache: expire stale readings (answer-write-fails)")
    assert other.returncode == 0, other.stderr
    assert not log.exists()
    r = answer(built, "answer-write-fails", "Q1", "Keep readings for 14 days.", env=env, tmp=tmp_path)
    assert r.returncode == 2
    assert "reason-code: answer-write-failed" in r.stderr
    assert json.loads(r.stdout)["failed"] == [{"name": names["Q1"], "write": "answer"}]
    assert log.read_text().count(f"task/{names['Q1']}") == 1
    assert "**Answer:**" not in show(built, names["Q1"])["body"]


def test_the_answer_fail_shim_leaves_another_followups_answer_alone(built, tmp_path):
    r = answer(built, "answered", "Q1", "Keep readings for 14 days.", env=built["answerfail_env"], tmp=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "\n**Answer:** Keep readings for 14 days." in show(built, built["followups"]["answered"]["Q1"])["body"]


def test_the_refinement_fail_shim_lets_the_answer_land_then_fails_every_later_update_of_q1(built, tmp_path):
    names = built["followups"]["refinement-fails"]
    env = built["refinefail_env"]
    log = Path(env["XDG_STATE_HOME"]) / "shim-hits.log"
    landed = answer(built, "refinement-fails", "Q1", "Keep readings for 14 days.", env=env, tmp=tmp_path)
    assert landed.returncode == 0, landed.stderr
    assert not log.exists() or names["Q1"] not in log.read_text()
    body = show(built, names["Q1"])["body"]
    assert "\n**Answer:** Keep readings for 14 days." in body
    again = run(env, "lore", "record", "update", f"task/{names['Q1']}", "--vault", "default", "--status", "ready")
    assert again.returncode == 1
    assert log.read_text().count(f"task/{names['Q1']}") == 1
    assert show(built, names["Q1"])["sidecar"]["status"] == "open"
    sibling = run(env, "lore", "record", "update", f"task/{built['followups']['refinement-fails']['P1']}",
                  "--vault", "default", "--status", "open")
    assert sibling.returncode == 0, sibling.stderr


def refuse(built, role="Q1", cell="hostile", body="Rewritten by a first pass.\n", flags=()):
    names = built["followups"][cell]
    return run(built["hostile_env"], "lore", "record", "update", f"task/{names[role]}", "--vault", "default",
               *flags, stdin=body)


def hits(built):
    log = Path(built["hostile_env"]["XDG_STATE_HOME"]) / "shim-hits.log"
    return log.read_text().splitlines() if log.is_file() else []


@pytest.fixture
def fresh(tmp_path):
    return build(tmp_path / "run")


def test_the_hostile_shim_refuses_a_first_pass_body_write_to_q1_leaves_the_record_and_logs_the_hit(fresh):
    name = fresh["followups"]["hostile"]["Q1"]
    before = show(fresh, name)["body"]
    r = refuse(fresh)
    assert r.returncode == 1
    assert show(fresh, name)["body"] == before
    assert hits(fresh) == [f"refused update of task/{name}"]


def test_the_hostile_shim_passes_the_answer_write_whose_stdin_carries_the_answer(fresh, tmp_path):
    name = fresh["followups"]["hostile"]["Q1"]
    r = answer(fresh, "hostile", "Q1", "Keep readings for 10 days.", env=fresh["hostile_env"], tmp=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "\n**Answer:** Keep readings for 10 days." in show(fresh, name)["body"]
    assert hits(fresh) == []


def test_the_hostile_shim_passes_a_later_write_once_the_answer_is_on_the_record(fresh, tmp_path):
    name = fresh["followups"]["hostile"]["Q1"]
    assert answer(fresh, "hostile", "Q1", "Keep readings for 10 days.", env=fresh["hostile_env"],
                  tmp=tmp_path).returncode == 0
    r = refuse(fresh, body="Refined: expire readings after 10 days.\n")
    assert r.returncode == 0, r.stderr
    assert show(fresh, name)["body"].startswith("Refined: expire readings after 10 days.")
    assert hits(fresh) == []


def test_the_hostile_shim_passes_a_body_write_to_p1(fresh):
    name = fresh["followups"]["hostile"]["P1"]
    r = refuse(fresh, role="P1", body="Mooring log, rewritten.\n")
    assert r.returncode == 0, r.stderr
    assert show(fresh, name)["body"].startswith("Mooring log, rewritten.")
    assert hits(fresh) == []


def test_the_hostile_shim_passes_a_write_to_q1_that_carries_no_body(fresh):
    name = fresh["followups"]["hostile"]["Q1"]
    r = refuse(fresh, body="", flags=("--title", "Tide cache: expire stale readings (hostile)"))
    assert r.returncode == 0, r.stderr
    assert "NOTE TO THE AGENT" in show(fresh, name)["body"]
    assert hits(fresh) == []


def test_the_hostile_marker_survives_a_refused_first_pass_write_to_the_question_read(  # inert-gate: allow seam smoke
    fresh,
):
    marker = CELLS["hostile"]["planted"]
    assert refuse(fresh).returncode == 1
    (q,) = question(fresh, "hostile", "Q1")["questions"]
    assert marker in q["evidence"] + q["recommended"]
    assert check(fresh, "hostile", "Q1")["left-out"][0]["reason"] == "unresolved-question"


def test_without_the_shim_a_first_pass_body_write_removes_the_marker_from_question(fresh):
    names = fresh["followups"]["hostile"]
    plain = run(fresh["env"], "lore", "record", "update", f"task/{names['Q1']}", "--vault", "default",
                stdin=show(fresh, names["Q1"])["body"].split("\n\n## Refine")[0] + "\n")
    assert plain.returncode == 0, plain.stderr
    assert question(fresh, "hostile", "Q1")["questions"] == []


def test_the_plain_env_answers_q1_and_check_then_reports_the_answer(built, tmp_path):
    cell = "accept-recommended"
    r = answer(built, cell, "Q1", None, recommended=True, tmp=tmp_path)
    assert r.returncode == 0, r.stderr
    stored = json.loads(r.stdout)["answer"]
    assert stored.startswith("Accepted the recommended answer: ")
    assert check(built, cell, "Q1")["left-out"][0]["answered"] is True


def test_the_altered_reply_is_changed_by_sanitizing_and_its_scrubbed_capture_passes_the_scan(built, tmp_path):
    reply = CELLS["altered"]["answers"][2].replace("{code}", "").strip()
    r = answer(built, "altered", "Q1", reply, preview=True, tmp=tmp_path)
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["altered"] is True
    assert "api_key=" not in result["answer"] and "[REDACTED]" in result["answer"]
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps(capture_scrub.scrub_value({"reply": reply, "stored": result["answer"]}), indent=1))
    scan = subprocess.run([sys.executable, str(CRAFT / "scripts" / "capture_scan.py"), str(capture)],
                          capture_output=True, text=True, timeout=60)
    assert scan.returncode == 0, scan.stdout
    assert capture_scan.scan_text(reply)


QUESTION_LINES = ["What is your answer to the question above\\? ", "Record that as your answer\\? ",
                  "Store that text as your answer\\? "]


def skill_line(start):
    (line,) = re.findall(rf"^\s*> ({start}.*<confirm>)\s*$", SKILL.read_text(), re.MULTILINE)
    return line


@pytest.mark.parametrize("start", QUESTION_LINES)
def test_the_driver_reads_the_code_from_each_step_10a_question_line_as_the_skill_words_it(start):
    shown = "   > " + skill_line(start).replace("<confirm>", "k7q2")
    assert driver.question_code(shown) == "k7q2"
    assert driver.question_code(shown.replace("k7q2", "**m3np**")) == "m3np"
    assert driver.question_code(shown.replace("k7q2", "k7q2.")) == "k7q2"
    assert driver.question_code("> " + start.replace("\\", "").rstrip()) is None


def test_the_cells_name_the_roles_and_shims_the_fixture_builds(built):
    for key, cell in CELLS.items():
        names = built["followups"][cell.get("spec_cell", key)]
        assert cell["shim"] in built
        for role in cell["expect"].get("followups", {}):
            assert role in names


def test_the_arm_command_line_allows_the_agent_tool_the_second_pass_dispatches(  # inert-gate: allow seam smoke
    tmp_path,
):
    argv = harness.conf.build_argv(sandbox="s", run_dir=tmp_path, env={"PATH": "/x"}, home="/h", user="u",
                                   claude="c", allowed=harness.ALLOWED)
    assert "Agent" in argv[argv.index("--allowedTools") + 1].split(",")


def test_the_baseline_skill_fixture_is_the_content_the_recorded_fingerprint_names():
    data = (CASE / "fixtures" / "baseline-skill" / "SKILL.md").read_bytes()
    assert harness.git_blob_id(data) == harness.BASE_SKILL_FINGERPRINT
    assert harness.git_blob_id(data + b"\n") != harness.BASE_SKILL_FINGERPRINT
