"""The drop-and-merge eval's fixture, built and driven the way an arm drives it.

The arms run on macOS inside ``scripts/eval-sandbox``, each executing ``lore`` and the
craft scripts by their own shebangs under the manifest's fenced env. This builds the
very vault the arms use (``make-fixture-vault.py``) with ``LORE_BIN`` naming the real
``lore`` executable and a from-scratch child environment, then runs the real
``followup_gather.py`` and ``followup_dispose.py`` by path under the manifest's env, so a
fixture that does not behave the way ``expected.md`` says surfaces here rather than
inside a paid arm.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CRAFT = Path(__file__).resolve().parents[1] / "plugins" / "craft"
GATHER = CRAFT / "scripts" / "followup_gather.py"
DISPOSE = CRAFT / "scripts" / "followup_dispose.py"
BUILDER = CRAFT / "evals" / "tidy-followups-drops-and-merges" / "fixtures" / "make-fixture-vault.py"
LORE_BIN = Path(__file__).resolve().parents[2] / "lore" / "plugins" / "lore" / "bin" / "lore"


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
    return subprocess.run(
        [str(a) for a in argv], capture_output=True, text=True, env=env, input=stdin
    )


def show(built, name):
    r = run(built["env"], "lore", "record", "show", f"task/{name}", "--vault", "default", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def status_of(built, name):
    return show(built, name)["sidecar"]["status"]


def dispose(built, cell, tmp_path, drops=(), merges=(), reason="", preview=False, env=None):
    """Run `dispose` for `cell`. `drops` are roles; `merges` are (survivor, [absorbed]) roles."""
    names = built["followups"][cell]

    def member(role):
        if status_of(built, names[role]) == "blocked":
            return {"name": names[role]}
        return {"name": names[role], "revision": show(built, names[role])["revision"]}

    doc = {
        "reason": reason,
        "drops": [member(r) for r in drops],
        "merges": [
            {
                "survivor": member(survivor),
                "absorbed": [dict(member(r), intent=f"adds what {r} covers") for r in absorbed],
            }
            for survivor, absorbed in merges
        ],
    }
    path = tmp_path / "dispositions.json"
    path.write_text(json.dumps(doc))
    argv = [DISPOSE, "dispose", built["specs"][cell], "--dispositions", path]
    return run(env or built["env"], *argv, *(["--preview"] if preview else []))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return build(tmp_path_factory.mktemp("drops-and-merges") / "run")


@pytest.mark.parametrize(
    "cell,statuses",
    [
        ("drop-several", {"X1": "open", "X2": "ready", "X3": "blocked", "K": "open"}),
        ("merge-included", {"S": "open", "M": "open", "L": "open"}),
        ("merge-left-ready", {"A": "ready", "B": "open", "C": "open"}),
        ("merge-refused-dependent", {"P": "open", "Q": "open"}),
        ("edit-intent", {"E1": "open", "E2": "open"}),
        ("full-set", {"I": "open", "D": "open", "S": "open", "M": "open", "L1": "open", "L2": "open"}),
        ("drop-fails", {"F1": "open", "F2": "open"}),
        ("supersede-fails", {"G1": "open", "G2": "open", "G3": "open"}),
        ("drop-dependents", {"H1": "open", "T": "open"}),
    ],
)
def test_each_cell_gathers_exactly_its_follow_ups_in_the_statuses_the_cell_needs(  # inert-gate: allow seam smoke
    built, cell, statuses
):
    gathered = run(built["env"], GATHER, built["specs"][cell])
    assert gathered.returncode == 0, gathered.stderr
    result = json.loads(gathered.stdout)
    assert {f["name"]: f["status"] for f in result["followups"]} == {
        built["followups"][cell][role]: status for role, status in statuses.items()
    }
    assert result["followup-parents"] == []


def test_the_blocked_follow_up_can_be_dropped_without_a_revision_and_the_open_ones_with_theirs(
    built, tmp_path
):
    r = dispose(built, "drop-several", tmp_path, drops=["X1", "X2", "X3"], reason="retired line", preview=True)
    assert r.returncode == 0, r.stderr
    names = built["followups"]["drop-several"]
    assert {d["name"]: d["status"] for d in json.loads(r.stdout)["drops"]} == {
        names["X1"]: "open", names["X2"]: "ready", names["X3"]: "blocked",
    }


def test_a_ready_survivor_is_reset_to_open_by_the_preview_and_an_open_one_is_not(built, tmp_path):
    ready = dispose(built, "merge-left-ready", tmp_path, merges=[("A", ["B"])], preview=True)
    assert ready.returncode == 0, ready.stderr
    assert [m["reset-to-open"] for m in json.loads(ready.stdout)["merges"]] == [True]
    opened = dispose(built, "merge-included", tmp_path, merges=[("S", ["M"])], preview=True)
    assert opened.returncode == 0, opened.stderr
    assert [m["reset-to-open"] for m in json.loads(opened.stdout)["merges"]] == [False]


def test_folding_away_the_follow_up_another_task_depends_on_is_refused_and_the_other_way_round_is_not(
    built, tmp_path
):
    cell = "merge-refused-dependent"
    names, others = built["followups"][cell], built["others"][cell]
    refused = dispose(built, cell, tmp_path, merges=[("P", ["Q"])], preview=True)
    assert refused.returncode == 1
    assert "reason-code: not-mergeable" in refused.stderr
    assert f"dependents {names['Q']}: default/{others['R']}" in refused.stderr
    allowed = dispose(built, cell, tmp_path, merges=[("Q", ["P"])], preview=True)
    assert allowed.returncode == 0, allowed.stderr


def test_a_dropped_follow_up_lists_its_gathered_and_its_ungathered_dependents(built, tmp_path):
    cell = "drop-dependents"
    names, others = built["followups"][cell], built["others"][cell]
    r = dispose(built, cell, tmp_path, drops=["H1"], reason="retired table", preview=True)
    assert r.returncode == 0, r.stderr
    listed = json.loads(r.stdout)["dependents"][names["H1"]]
    assert {(d["name"], d["vault"], d["status"]) for d in listed} == {
        (names["T"], "default", "open"), (others["U"], "default", "open"),
    }


def test_the_full_set_follow_up_that_depends_on_the_dropped_one_is_listed_as_its_dependent(built, tmp_path):
    cell = "full-set"
    names = built["followups"][cell]
    r = dispose(built, cell, tmp_path, drops=["D"], merges=[("S", ["M"])], reason="retired", preview=True)
    assert r.returncode == 0, r.stderr
    assert [d["name"] for d in json.loads(r.stdout)["dependents"][names["D"]]] == [names["L2"]]


def test_the_drop_fail_shim_fails_only_the_named_drop_and_the_plain_env_drops_both(tmp_path):
    built = build(tmp_path / "run")
    names = built["followups"]["drop-fails"]
    r = dispose(built, "drop-fails", tmp_path, drops=["F1", "F2"], reason="retired",
                env=built["dropfail_env"])
    assert r.returncode == 2
    report = json.loads(r.stdout)
    assert [(f["name"], f["write"]) for f in report["failed"]] == [(names["F2"], "drop")]
    assert [d["name"] for d in report["dropped"]] == [names["F1"]]
    assert status_of(built, names["F1"]) == "dropped"
    assert status_of(built, names["F2"]) == "open"
    again = dispose(built, "drop-fails", tmp_path, drops=["F2"], reason="retired")
    assert again.returncode == 0, again.stderr
    assert status_of(built, names["F2"]) == "dropped"


def test_the_supersede_fail_shim_fails_only_the_named_supersede_and_the_plain_env_merges_both(tmp_path):
    built = build(tmp_path / "run")
    names = built["followups"]["supersede-fails"]
    r = dispose(built, "supersede-fails", tmp_path, merges=[("G1", ["G2", "G3"])],
                env=built["supersedefail_env"])
    assert r.returncode == 2
    report = json.loads(r.stdout)
    assert [(f["name"], f["write"]) for f in report["failed"]] == [(names["G3"], "supersede")]
    assert report["merged"] == []
    assert report["incomplete-merges"] == [{"survivor": names["G1"], "absorbed": [names["G2"], names["G3"]]}]
    assert status_of(built, names["G2"]) == "superseded"
    assert status_of(built, names["G3"]) == "open"


def test_the_plain_env_completes_the_same_merge_group_the_failing_shim_leaves_incomplete(tmp_path):
    built = build(tmp_path / "run")
    names = built["followups"]["supersede-fails"]
    r = dispose(built, "supersede-fails", tmp_path, merges=[("G1", ["G2", "G3"])])
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["merged"] == [
        {"survivor": names["G1"], "absorbed": [names["G2"], names["G3"]], "survivor-prior-status": "open"}
    ]
    assert status_of(built, names["G3"]) == "superseded"


OLD_CASE = CRAFT / "evals" / "tidy-followups-builds-one-parent"
GRADER = CRAFT / "evals" / "tidy-followups-drops-and-merges" / "grade_dispose.py"


def _tree(path: Path) -> dict:
    return {
        str(p.relative_to(path)): p.read_bytes().hex()
        for p in sorted(path.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(path).parts
    }


def _command(built, cell, doc, preview=False):
    return (
        'dispositions=$(mktemp)\ncat > "$dispositions" <<\'DISPOSITIONS_JSON\'\n'
        f"{json.dumps(doc)}\nDISPOSITIONS_JSON\n"
        f"${{CLAUDE_PLUGIN_ROOT}}/scripts/followup_dispose.py dispose {built['specs'][cell]} "
        f'--dispositions "$dispositions"{" --preview" if preview else ""}\n'
        'rc=$?\nrm -f "$dispositions"\necho "exit: $rc"\n'
    )


def _grade(tmp_path, final_report, drive):
    """Build a fixture, let `drive(built, events)` run real dispose commands and record them as the
    stream events an arm would have produced, then run the grader over the result."""
    work = tmp_path / "work"
    (work / "meta").mkdir(parents=True)
    (work / "snap-treat").mkdir()
    (work / "snap-treat" / "tools").symlink_to(LORE_BIN.parents[4])
    built = build(work / "built")
    key = "drop-several-treat"
    built_for_grader = {**built, "env": built["env"]}
    (work / "meta" / f"{key}.manifest.json").write_text(json.dumps(built_for_grader))
    vault = Path(built["env"]["XDG_STATE_HOME"]).parent / "vault"
    before = {"vault": _tree(vault), "repo": {"head": "h", "status": "", "tree": {}}}
    (work / "meta" / f"{key}.before.json").write_text(json.dumps(before))
    events = []
    drive(built, events)
    after = {"vault": _tree(vault), "repo": {"head": "h", "status": "", "tree": {}}}
    (work / "meta" / f"{key}.after.json").write_text(json.dumps(after))
    stream = events + [{"type": "result", "result": final_report}]
    (work / "meta" / f"{key}.stream.jsonl").write_text("\n".join(json.dumps(e) for e in stream))
    r = subprocess.run(
        [sys.executable, "-I", str(GRADER), str(work), key], capture_output=True, text=True,
        env={**os.environ, "PATH": built["env"]["PATH"]},
    )
    assert r.returncode == 0, r.stderr
    return built, json.loads(r.stdout)


def _text(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _bash(tool_id, command):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}]}}


def _result(tool_id, text):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": text}]}}


def test_the_grader_reads_a_real_preview_and_write_into_roles_orders_and_standalone_commands(tmp_path):
    def drive(built, events):
        names = built["followups"]["drop-several"]
        spec = built["specs"]["drop-several"]
        doc = {
            "reason": "the oven line closed",
            "drops": [{"name": names["X1"], "revision": show(built, names["X1"])["revision"]},
                      {"name": names["X3"]}],
            "merges": [],
        }
        path = tmp_path / "d.json"
        path.write_text(json.dumps(doc))
        for i, preview in enumerate((True, False)):
            r = run(built["env"], DISPOSE, "dispose", spec, "--dispositions", path,
                    *(["--preview"] if preview else []))
            events.append(_text("Why drop this follow-up? Drop, merge, include and leave as shown?"))
            events.append(_bash(f"t{i}", _command(built, "drop-several", doc, preview)))
            events.append(_result(f"t{i}", r.stdout + f"\nexit: {r.returncode}"))
        events.append(_bash("t9", f"lore record show task/{names['X3']} --vault default"))
        events.append(_result("t9", "body"))
        events.append(_text(f"dropped task/{names['X1']}\ndropped task/{names['X3']}"))

    built, graded = _grade(
        tmp_path,
        "Dropped two.\n```\n/craft:distill spec/some-spec\n```\nOr run /craft:tidy-followups spec/some-spec now.\n",
        drive,
    )
    assert [(r["preview"], r["exit"]) for r in graded["dispose_runs"]] == [(True, 0), (False, 0)]
    write = graded["dispose_runs"][1]
    assert [d["role"] for d in write["file"]["drops"]] == ["X1", "X3"]
    assert [d["revision"] for d in write["file"]["drops"]] == [True, False]
    assert write["file"]["reason"] == "the oven line closed"
    assert [d["name"] for d in write["result"]["dropped"]] == ["X1", "X3"]
    assert graded["fetched_by_main"] == ["X3"]
    assert graded["order"]["reason_question"][0] < graded["order"]["write_dispose"][0]
    assert graded["order"]["write_dispose"] == [write["at"]]
    assert graded["progress"]["assistant_text"][0]["line"] == "dropped task/X1"
    assert {r: v["status"] for r, v in graded["records"].items() if r in ("X1", "X2", "X3")} == {
        "X1": "dropped", "X3": "dropped", "X2": "ready"}
    assert graded["records"]["X1"]["dropped_note"] == "Reason: the oven line closed"
    assert graded["records"]["X2"]["body_changed"] is False
    assert [(c["line"], c["standalone"]) for c in graded["closing_commands"]] == [
        ("/craft:distill spec/some-spec", True),
        ("Or run /craft:tidy-followups spec/some-spec now.", False),
    ]


def test_a_cells_answers_name_the_records_of_the_fixture_the_arm_runs_against(built):
    import importlib.util

    path = CRAFT / "evals" / "tidy-followups-drops-and-merges" / "harness.py"
    spec = importlib.util.spec_from_file_location("drops_and_merges_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cells = json.loads((path.parent / "cells.json").read_text())
    cell = "merge-refused-dependent"
    filled = module.fill(cells[cell]["answers"], built, cell)
    names = built["followups"][cell]
    assert f"merge {names['Q']} into {names['P']}" in filled
    assert f"merge {names['P']} into {names['Q']}" in filled
    assert "{" not in filled
    other = module.fill("drop {X1}", built, "drop-several")
    assert other == f"drop {built['followups']['drop-several']['X1']}"
    assert module.fill("drop {Zed}", built, "drop-several") == "drop {Zed}"
