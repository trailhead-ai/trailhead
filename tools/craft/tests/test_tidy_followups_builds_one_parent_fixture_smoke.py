"""The write-path eval's fixture, built and driven the way an arm drives it.

The arms run on macOS inside ``scripts/eval-sandbox``, each executing ``lore`` and the
craft scripts by their own shebangs under the manifest's fenced env. This builds the
very vault the arms use (``make-fixture-vault.py``) with ``LORE_BIN`` naming the real
``lore`` executable and a from-scratch child environment, then runs the real scripts
by path under the manifest's env, so a fixture that does not behave the way
``expected.md`` says surfaces here rather than inside a paid arm.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

CRAFT = Path(__file__).resolve().parents[1] / "plugins" / "craft"
GATHER = CRAFT / "scripts" / "followup_gather.py"
PARENT = CRAFT / "scripts" / "followup_parent.py"
BUILDER = (
    CRAFT / "evals" / "tidy-followups-builds-one-parent" / "fixtures" / "make-fixture-vault.py"
)
LORE_BIN = Path(__file__).resolve().parents[2] / "lore" / "plugins" / "lore" / "bin" / "lore"


def build(dest: Path) -> dict:
    r = subprocess.run(
        [sys.executable, "-I", str(BUILDER), str(dest)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LORE_BIN": str(LORE_BIN)},
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def run(env, *argv, stdin=None):
    return subprocess.run(
        [str(a) for a in argv], capture_output=True, text=True, env=env, input=stdin
    )


def gather(built, spec_key):
    r = run(built["env"], GATHER, built["specs"][spec_key])
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return build(tmp_path_factory.mktemp("builds-one-parent") / "run")


@pytest.mark.parametrize(
    "spec_key,distilled",
    [
        ("chosen", False),
        ("distilled", True),
        ("none-included", False),
        ("none-included-distilled", True),
        ("left-out", False),
        ("nest-fail", False),
        ("inject", False),
    ],
)
def test_each_spec_gathers_exactly_its_follow_ups_and_its_distilled_state(
    built, spec_key, distilled
):
    result = gather(built, spec_key)
    assert result["entry"] == "complete-status"
    assert [f["name"] for f in result["followups"]] == sorted(built["followups"][spec_key].values())
    assert result["distilled"] is distilled
    assert result["followup-parents"] == []


def test_the_left_out_fixture_reaches_the_depends_outside_reason(built):
    q1, q2 = built["followups"]["left-out"]["Q1"], built["followups"]["left-out"]["Q2"]
    r = run(built["env"], PARENT, "check", built["specs"]["left-out"], q1, q2)
    assert r.returncode == 0, r.stderr
    checked = json.loads(r.stdout)
    reasons = {x["name"]: x for x in checked["left-out"]}
    assert reasons[q1]["reason"] == "not-ready"
    assert reasons[q2]["reason"] == "depends-outside"
    assert reasons[q2]["dependency"] == built["outside"]
    assert checked["eligible"] == []


def test_every_code_claim_in_a_follow_up_body_points_at_a_file_in_the_repo(built):
    repo = Path(built["repo"])
    claimed = 0
    for roles in built["followups"].values():
        for name in roles.values():
            r = run(built["env"], "lore", "record", "show", f"task/{name}", "--vault", "default")
            assert r.returncode == 0, r.stderr
            paths = re.findall(r"`(src/[\w./]+\.py)`", r.stdout)
            assert paths, f"{name} names no file in the repo"
            for path in paths:
                assert (repo / path).is_file(), f"{name} names {path}, absent from the repo"
                claimed += 1
    assert claimed >= 12


def test_the_fixture_repo_is_a_clean_single_commit_tree(built):
    repo = Path(built["repo"])
    status = run({"PATH": "/usr/bin:/bin"}, "git", "-C", repo, "status", "--porcelain")
    count = run({"PATH": "/usr/bin:/bin"}, "git", "-C", repo, "rev-list", "--count", "HEAD")
    assert status.stdout == ""
    assert count.stdout.strip() == "1"


def make_ready_and_confirm(built, spec_key, roles):
    names = [built["followups"][spec_key][r] for r in roles]
    for name in names:
        r = run(
            built["env"], "lore", "record", "update", f"task/{name}", "--vault", "default",
            "--status", "ready",
        )
        assert r.returncode == 0, r.stderr
    r = run(built["env"], PARENT, "check", built["specs"][spec_key], *names)
    assert r.returncode == 0, r.stderr
    eligible = json.loads(r.stdout)["eligible"]
    assert [e["name"] for e in eligible] == sorted(names)
    return [
        {"name": e["name"], "reason": "waiting costs more", "revision": e["revision"]}
        for e in eligible
    ]


def assemble(built, env_key, spec_key, children, tmp_path):
    path = tmp_path / "children.json"
    path.write_text(json.dumps(children))
    reasons = tmp_path / "reasons.json"
    reasons.write_text(json.dumps({c["name"]: c["reason"] for c in children}))
    spec = built["specs"][spec_key]
    checked = run(built["env"], PARENT, "check", spec, *[c["name"] for c in children], "--reasons", reasons)
    assert checked.returncode == 0, checked.stderr
    code = json.loads(checked.stdout)["confirm"]
    return run(built[env_key], PARENT, "assemble", spec, "--children", path, "--confirm", code)


def test_assembly_under_the_plain_env_nests_every_child(tmp_path):
    built = build(tmp_path / "run")
    children = make_ready_and_confirm(built, "nest-fail", ["N1", "N2"])
    r = assemble(built, "env", "nest-fail", children, tmp_path)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert sorted(report["nested"]) == sorted(c["name"] for c in children)
    assert report["buildable"] is True


def test_assembly_under_the_nestfail_env_fails_the_second_nesting_and_leaves_the_parent_open(tmp_path):
    built = build(tmp_path / "run")
    children = make_ready_and_confirm(built, "nest-fail", ["N1", "N2"])
    r = assemble(built, "nestfail_env", "nest-fail", children, tmp_path)
    assert r.returncode == 2
    assert "reason-code: nesting-incomplete" in r.stderr
    report = json.loads(r.stdout)
    assert report["buildable"] is False
    assert [f["write"] for f in report["failed"]] == ["nest"]
    assert len(report["nested"]) == 1
    assert report["undone"] == [] and report["undo-failed"] == []
    gathered = run(built["env"], GATHER, built["specs"]["nest-fail"])
    result = json.loads(gathered.stdout)
    assert result["followup-parents"] == [report["parent"]]
    assert [f["name"] for f in result["followups"]] == [
        c["name"] for c in children if c["name"] not in report["nested"]
    ]


@pytest.mark.parametrize("flags", [[], ["-d"], ["-t", "refine.XXXXXX"]])
def test_mktemp_on_the_manifest_path_creates_inside_tmpdir(built, tmp_path, flags):
    env = {**built["env"], "TMPDIR": str(tmp_path)}
    r = run(env, "mktemp", *flags)
    assert r.returncode == 0, r.stderr
    made = Path(r.stdout.strip())
    assert made.parent == tmp_path
    assert made.is_dir() == ("-d" in flags)
    if "-t" in flags:
        assert made.name.startswith("refine.")


def test_mktemp_given_a_full_template_uses_that_template(built, tmp_path):
    env = {**built["env"], "TMPDIR": str(tmp_path / "unused")}
    r = run(env, "mktemp", str(tmp_path / "named.XXXXXX"))
    assert r.returncode == 0, r.stderr
    assert Path(r.stdout.strip()).parent == tmp_path
    assert Path(r.stdout.strip()).name.startswith("named.")
