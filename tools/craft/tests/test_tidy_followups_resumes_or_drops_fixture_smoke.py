"""The resume-or-drop eval's fixture, built and driven the way an arm drives it.

The arms run on macOS inside ``scripts/eval-sandbox``, each executing ``lore`` and the
craft scripts by their own shebangs under the manifest's fenced env. This builds the
very vault the arms use (``make-fixture-vault.py``) with ``LORE_BIN`` naming the real
``lore`` executable and a from-scratch child environment, then runs the real scripts
by path under the manifest's env, so a fixture that does not behave the way
``expected.md`` says surfaces here rather than inside a paid arm.
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
PARENT = CRAFT / "scripts" / "followup_parent.py"
BUILDER = (
    CRAFT / "evals" / "tidy-followups-resumes-or-drops-a-parent" / "fixtures" / "make-fixture-vault.py"
)
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


def state(built, cell):
    r = run(built["env"], PARENT, "state", built["specs"][cell], built["parents"][cell])
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def status_of(built, name):
    r = run(built["env"], "lore", "record", "show", f"task/{name}", "--vault", "default", "--json")
    assert r.returncode == 0, r.stderr
    sidecar = json.loads(r.stdout)["sidecar"]
    return sidecar["status"], sidecar.get("parent")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return build(tmp_path_factory.mktemp("resumes-or-drops") / "run")


@pytest.mark.parametrize(
    "cell,status,held,unheld",
    [
        ("resume", "open", {"A": "ready"}, ["B"]),
        ("resume-left-out", "open", {"A": "ready"}, ["B"]),
        ("refuse-build", "ready", {"A": "ready", "B": "ready"}, []),
        ("refuse-drop", "in-progress", {"A": "done", "B": "ready"}, []),
    ],
)
def test_each_cell_gathers_its_one_parent_and_the_state_read_shows_what_it_lists_and_holds(
    built, cell, status, held, unheld
):
    gathered = run(built["env"], GATHER, built["specs"][cell])
    assert gathered.returncode == 0, gathered.stderr
    assert json.loads(gathered.stdout)["followup-parents"] == [built["parents"][cell]]
    result = state(built, cell)
    assert result["status"] == status
    assert {h["name"]: h["status"] for h in result["held"]} == {
        built["followups"][cell][role]: s for role, s in held.items()
    }
    assert result["unheld"] == [built["followups"][cell][role] for role in unheld]
    assert result["unlisted"] == []
    assert result["skipped"] == 0


def test_the_unheld_follow_up_of_each_open_parent_is_gathered_and_the_held_one_is_not(built):
    for cell in ("resume", "resume-left-out"):
        gathered = json.loads(run(built["env"], GATHER, built["specs"][cell]).stdout)
        assert [f["name"] for f in gathered["followups"]] == [built["followups"][cell]["B"]]


def test_the_resume_fixture_resumes_to_a_ready_parent_holding_both_follow_ups(tmp_path):
    built = build(tmp_path / "run")
    cell = "resume"
    spec, parent, b = built["specs"][cell], built["parents"][cell], built["followups"][cell]["B"]
    checked = run(built["env"], PARENT, "check", spec, b)
    assert checked.returncode == 0, checked.stderr
    eligible = json.loads(checked.stdout)["eligible"]
    assert [e["name"] for e in eligible] == [b]
    children = tmp_path / "children.json"
    children.write_text(json.dumps([{"name": b, "revision": eligible[0]["revision"]}]))
    shown = run(built["env"], PARENT, "check", spec, b, "--parent", parent)
    assert shown.returncode == 0, shown.stderr
    r = run(built["env"], PARENT, "resume", spec, parent, "--children", children,
            "--confirm", json.loads(shown.stdout)["confirm"])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["nested"] == [b] and report["buildable"] is True
    assert status_of(built, parent)[0] == "ready"
    assert status_of(built, b) == ("ready", parent)


def test_the_resume_left_out_fixture_reports_not_ready_and_resumes_without_it(tmp_path):
    built = build(tmp_path / "run")
    cell = "resume-left-out"
    spec, parent, b = built["specs"][cell], built["parents"][cell], built["followups"][cell]["B"]
    checked = json.loads(run(built["env"], PARENT, "check", spec, b).stdout)
    assert checked["eligible"] == []
    assert [(x["name"], x["reason"]) for x in checked["left-out"]] == [(b, "not-ready")]
    children = tmp_path / "children.json"
    children.write_text("[]")
    shown = run(built["env"], PARENT, "check", spec, b, "--parent", parent)
    assert shown.returncode == 0, shown.stderr
    r = run(built["env"], PARENT, "resume", spec, parent, "--children", children,
            "--confirm", json.loads(shown.stdout)["confirm"])
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["removed"] == [b]
    assert status_of(built, parent)[0] == "ready"
    assert status_of(built, b) == ("open", None)


def test_the_refuse_drop_fixture_drops_with_the_ready_child_un_nested_and_the_done_one_kept(tmp_path):
    built = build(tmp_path / "run")
    cell = "refuse-drop"
    spec, parent = built["specs"][cell], built["parents"][cell]
    done, ready = built["followups"][cell]["A"], built["followups"][cell]["B"]
    reason = tmp_path / "reason.json"
    reason.write_text(json.dumps("the survey tooling was retired"))
    shown = run(built["env"], PARENT, "state", spec, parent, "--reason-file", reason)
    assert shown.returncode == 0, shown.stderr
    r = run(built["env"], PARENT, "drop", spec, parent, "--reason-file", reason,
            "--confirm", json.loads(shown.stdout)["drop-confirm"])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["un-nested"] == [ready]
    assert report["kept"] == [{"name": done, "status": "done"}]
    assert status_of(built, parent)[0] == "dropped"
    assert status_of(built, ready) == ("ready", None)
    assert status_of(built, done) == ("done", parent)
    shown = run(built["env"], "lore", "record", "show", f"task/{parent}", "--vault", "default")
    assert "the survey tooling was retired" in shown.stdout

