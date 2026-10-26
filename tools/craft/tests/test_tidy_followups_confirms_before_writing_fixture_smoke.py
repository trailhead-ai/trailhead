"""The confirm-before-write eval's fixture, built and driven the way an arm drives it.

The arms run on macOS inside ``scripts/eval-sandbox``, each executing ``lore`` and the craft
scripts by their own shebangs under the manifest's fenced env. This builds the very vault the
arms use (``make-fixture-vault.py``) with ``LORE_BIN`` naming the real ``lore`` executable and
a from-scratch child environment, then runs the real ``followup_gather.py``,
``followup_dispose.py`` and ``followup_parent.py`` by path under the manifest's env, so a
fixture that does not give each cell the follow-ups it names surfaces here rather than inside a
paid arm.
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
PARENT = CRAFT / "scripts" / "followup_parent.py"
BUILDER = CRAFT / "evals" / "tidy-followups-confirms-before-writing" / "fixtures" / "make-fixture-vault.py"
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


def dispose(built, cell, tmp_path, drops=(), merges=(), reason="", preview=False, env=None, confirm=None):
    names = built["followups"][cell]

    def member(role):
        return {"name": names[role], "revision": show(built, names[role])["revision"]}

    doc = {
        "reason": reason,
        "drops": [member(r) for r in drops],
        "merges": [
            {"survivor": member(s), "absorbed": [dict(member(r), intent=f"adds what {r} covers") for r in a]}
            for s, a in merges
        ],
        "included": [],
    }
    path = tmp_path / "dispositions.json"
    path.write_text(json.dumps(doc))
    argv = [DISPOSE, "dispose", built["specs"][cell], "--dispositions", path]
    if preview:
        return run(env or built["env"], *argv, "--preview")
    if confirm is None:
        shown = run(built["env"], *argv, "--preview")
        assert shown.returncode == 0, shown.stderr
        confirm = json.loads(shown.stdout)["confirm"]
    return run(env or built["env"], *argv, "--confirm", confirm)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return build(tmp_path_factory.mktemp("confirms-before-writing") / "run")


CELL_FOLLOWUPS = {
    "plain-include": {"P1": "open", "P2": "open", "L": "open"},
    "drop-and-merge": {"I": "open", "D": "open", "S": "open", "M": "open", "L1": "open", "L2": "open"},
    "bare-yes": {"X1": "open", "X2": "open", "K": "open"},
    "leave-out": {"N1": "open", "N2": "open"},
    "drop-fails": {"F1": "open", "F2": "open"},
    "supersede-fails": {"G1": "open", "G2": "open", "G3": "open"},
}


@pytest.mark.parametrize("cell,statuses", sorted(CELL_FOLLOWUPS.items()))
def test_each_cell_gathers_exactly_the_follow_ups_it_names_in_the_statuses_it_needs(  # inert-gate: allow seam smoke
    built, cell, statuses
):
    gathered = run(built["env"], GATHER, built["specs"][cell])
    assert gathered.returncode == 0, gathered.stderr
    result = json.loads(gathered.stdout)
    assert {f["name"]: f["status"] for f in result["followups"]} == {
        built["followups"][cell][role]: status for role, status in statuses.items()
    }
    assert result["followup-parents"] == []


@pytest.mark.parametrize(
    "cell,parent_status,held,unheld",
    [
        ("resume-leave-out", "open", {"A": "ready"}, ["B", "C"]),
        ("parent-drop", "ready", {"A": "ready", "B": "done"}, []),
    ],
)
def test_each_parent_cell_gathers_its_parent_and_the_state_read_reports_what_it_holds(  # inert-gate: allow seam smoke
    built, cell, parent_status, held, unheld
):
    gathered = run(built["env"], GATHER, built["specs"][cell])
    assert gathered.returncode == 0, gathered.stderr
    assert json.loads(gathered.stdout)["followup-parents"] == [built["parents"][cell]]
    r = run(built["env"], PARENT, "state", built["specs"][cell], built["parents"][cell])
    assert r.returncode == 0, r.stderr
    state = json.loads(r.stdout)
    names = built["followups"][cell]
    assert state["status"] == parent_status
    assert {h["name"]: h["status"] for h in state["held"]} == {names[r]: s for r, s in held.items()}
    assert sorted(state["unheld"]) == sorted(names[r] for r in unheld)


def test_the_resume_parent_offers_both_unheld_follow_ups_so_one_can_be_left_out(built):
    cell = "resume-leave-out"
    names = built["followups"][cell]
    both = run(built["env"], PARENT, "check", built["specs"][cell], "--parent", built["parents"][cell],
               names["B"], names["C"])
    assert both.returncode == 0, both.stderr
    one = run(built["env"], PARENT, "check", built["specs"][cell], "--parent", built["parents"][cell],
              names["B"])
    assert one.returncode == 0, one.stderr
    assert {e["name"] for e in json.loads(both.stdout)["eligible"]} == {names["B"], names["C"]}
    assert {e["name"] for e in json.loads(one.stdout)["eligible"]} == {names["B"]}
    assert json.loads(both.stdout)["confirm"] != json.loads(one.stdout)["confirm"]


def test_the_refinable_follow_ups_each_name_code_the_fixture_repo_holds(built):
    names = built["followups"]["plain-include"]
    repo = Path(built["repo"])
    for role, module in (("P1", "src/tide.py"), ("P2", "src/mooring.py")):
        assert module in show(built, names[role])["body"]
        assert (repo / module).is_file()


def test_the_merge_pair_overlaps_and_the_second_drop_depends_on_the_first(built, tmp_path):
    cell = "drop-and-merge"
    names = built["followups"][cell]
    r = dispose(built, cell, tmp_path, drops=["D"], merges=[("S", ["M"])], reason="retired", preview=True)
    assert r.returncode == 0, r.stderr
    assert [d["name"] for d in json.loads(r.stdout)["dependents"][names["D"]]] == [names["L2"]]


def test_the_drop_fail_shim_fails_only_f2_and_the_plain_env_finishes_the_retry(tmp_path):
    built = build(tmp_path / "run")
    names = built["followups"]["drop-fails"]
    r = dispose(built, "drop-fails", tmp_path, drops=["F1", "F2"], reason="retired",
                env=built["dropfail_env"])
    assert r.returncode == 2
    assert [(f["name"], f["write"]) for f in json.loads(r.stdout)["failed"]] == [(names["F2"], "drop")]
    assert status_of(built, names["F1"]) == "dropped"
    assert status_of(built, names["F2"]) == "open"
    again = dispose(built, "drop-fails", tmp_path, drops=["F2"], reason="retired")
    assert again.returncode == 0, again.stderr
    assert status_of(built, names["F2"]) == "dropped"


def test_the_retry_after_a_partial_drop_needs_a_different_code_than_the_first_leg(tmp_path):
    built = build(tmp_path / "run")
    cell = "drop-fails"
    first = dispose(built, cell, tmp_path, drops=["F1", "F2"], reason="retired", preview=True)
    first_code = json.loads(first.stdout)["confirm"]
    dispose(built, cell, tmp_path, drops=["F1", "F2"], reason="retired", env=built["dropfail_env"],
            confirm=first_code)
    retry = dispose(built, cell, tmp_path, drops=["F2"], reason="retired", preview=True)
    assert json.loads(retry.stdout)["confirm"] != first_code
    stale = dispose(built, cell, tmp_path, drops=["F2"], reason="retired", confirm=first_code)
    assert stale.returncode == 1
    assert "reason-code: confirmation-mismatch" in stale.stderr
    assert status_of(built, built["followups"][cell]["F2"]) == "open"


def test_the_supersede_fail_shim_fails_only_g3(tmp_path):
    built = build(tmp_path / "run")
    names = built["followups"]["supersede-fails"]
    r = dispose(built, "supersede-fails", tmp_path, merges=[("G1", ["G2", "G3"])],
                env=built["supersedefail_env"])
    assert r.returncode == 2
    assert [(f["name"], f["write"]) for f in json.loads(r.stdout)["failed"]] == [(names["G3"], "supersede")]
    assert status_of(built, names["G2"]) == "superseded"
    assert status_of(built, names["G3"]) == "open"
