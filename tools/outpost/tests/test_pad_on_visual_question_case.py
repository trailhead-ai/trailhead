"""Tests for the ``pad-on-visual-question`` eval case: its fixture builder and its arms.

The fixture smoke tests run ``make-fixture-env.sh`` by its real path, then the real
``scratch_pad.py`` (the copy the builder placed in the run directory, the one an arm
loads through ``--plugin-dir``) with the env the builder emitted, per variant. Every
env is built from scratch; nothing is inherited from the developer's HOME or XDG state.

The arms contract test pins that the treatment arm rebuilds from today's ``rules.md``. Neither half
asserts anything about the eval's measured result.
"""

from __future__ import annotations

import json
import os
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import arms_contract as ac

CASE_DIR = ac.EVALS_DIR / "pad-on-visual-question"
BUILDER = CASE_DIR / "fixtures" / "make-fixture-env.sh"
ARMS_DIR = CASE_DIR / "arms"

GRADER = CASE_DIR / "grade-run.py"

BASELINE = "baseline.md"
TREATMENT = "treatment.md"

INSIDE, OUTSIDE, REFUSED = "inside", "outside", "refused"
VARIANTS = {
    "visual-inside-workspace": INSIDE,
    "visual-outside-workspace": OUTSIDE,
    "not-visual-layout-vocabulary": OUTSIDE,
    "visual-pad-cannot-be-made": REFUSED,
    "typed-answer-after-pad": OUTSIDE,
    "words-only-request": OUTSIDE,
    "dispatched-agent-observed": OUTSIDE,
    "flow-outside-workspace": OUTSIDE,
    "not-visual-flow-vocabulary": OUTSIDE,
}

ROUND = {
    "title": "Probe",
    "questions": [
        {
            "prompt": "Where does the control go?",
            "options": [
                {"name": "Left rail", "cost": "Always visible.", "layout": {}, "pick": True},
                {"name": "Top bar", "cost": "Pushes content down.", "layout": {}},
            ],
        }
    ],
}


def _build(
    tmp_path: Path,
    variant: str,
    python3: str | None = sys.executable,
    path: str = "/usr/bin:/bin",
) -> tuple[subprocess.CompletedProcess, Path]:
    run_dir = tmp_path / "run"
    env = {"PATH": path, "HOME": str(tmp_path / "unused-home")}
    if python3 is not None:
        env["EVAL_PYTHON3"] = python3
    result = subprocess.run(
        ["bash", str(BUILDER), str(run_dir), variant],
        capture_output=True,
        text=True,
        env=env,
    )
    return result, run_dir


def _arm_env(run_dir: Path) -> dict[str, str]:
    lines = (run_dir / "run.env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if line)


def _run_pad(run_dir: Path, pad: str, round_: dict = ROUND) -> subprocess.CompletedProcess:
    round_file = run_dir.parent / "round.json"
    round_file.write_text(json.dumps(round_))
    cwd = Path((run_dir / "cwd").read_text().strip())
    script = run_dir / "plugin" / "skills" / "scratch-pad" / "scratch_pad.py"
    env = _arm_env(run_dir)
    # The skill's documented command is a bare `python3`, so run the one the arm's PATH finds.
    python3 = shutil.which("python3", path=env["PATH"])
    assert python3, env["PATH"]
    return subprocess.run(
        [python3, str(script), "--pad", pad, "--round", str(round_file)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _built(tmp_path: Path, variant: str) -> Path:
    result, run_dir = _build(tmp_path, variant)
    assert result.returncode == 0, result.stderr
    return run_dir


@pytest.mark.parametrize(
    "variant", [v for v, kind in VARIANTS.items() if kind == INSIDE]
)
def test_inside_workspace_variants_publish_a_site_under_the_fixture_workspace(tmp_path, variant):
    run_dir = _built(tmp_path, variant)
    pad = "probe-inside"
    result = _run_pad(run_dir, pad)
    assert result.returncode == 0, result.stderr
    sites = list((run_dir / "xdg-state" / "camp").glob(f"*/worktrees/*/sites/{pad}"))
    assert len(sites) == 1, sites
    assert "Where does the control go?" in (sites[0] / "round-1" / "round.js").read_text()
    link = result.stdout.splitlines()[0]
    assert link.startswith("http://127.0.0.1:") and link.endswith(f"/{pad}/")
    assert not (run_dir / "xdg-state" / "outpost" / "pads").exists()


@pytest.mark.parametrize(
    "variant", [v for v, kind in VARIANTS.items() if kind == OUTSIDE]
)
def test_outside_variants_build_the_pad_under_the_fixture_state_directory(tmp_path, variant):
    run_dir = _built(tmp_path, variant)
    pad = "probe-outside"
    result = _run_pad(run_dir, pad)
    assert result.returncode == 0, result.stderr
    index = run_dir / "xdg-state" / "outpost" / "pads" / pad / "index.html"
    assert "Where does the control go?" in (index.parent / "round-1" / "round.js").read_text()
    assert result.stdout.splitlines()[0] == index.resolve().as_uri()
    assert not list((run_dir / "xdg-state").glob(f"camp/**/{pad}"))


def test_refused_variant_exits_non_zero_with_reasons_and_leaves_no_pad(tmp_path):
    run_dir = _built(tmp_path, "visual-pad-cannot-be-made")
    pad = "probe-refused"
    result = _run_pad(run_dir, pad)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "the sites folder is a symlink" in result.stderr
    assert not list(tmp_path.rglob(pad))


def test_a_python3_that_cannot_run_the_publisher_is_refused_and_builds_nothing(tmp_path):
    stub = tmp_path / "old-python3"
    stub.write_text("#!/bin/sh\nexit 1\n")
    stub.chmod(0o755)
    result, run_dir = _build(tmp_path, "visual-inside-workspace", python3=str(stub))
    assert result.returncode == 2
    assert "tomllib" in result.stderr
    assert not (run_dir / "run.env").exists()


def _assert_arm_python3_imports_tomllib(run_dir: Path) -> None:
    link = run_dir / "bin" / "python3"
    assert link.resolve().is_absolute() and link.resolve().exists(), link
    arm_python3 = shutil.which("python3", path=_arm_env(run_dir)["PATH"])
    assert arm_python3 == str(link), arm_python3
    probe = subprocess.run([arm_python3, "-c", "import tomllib"], capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr


def test_a_bare_name_python3_is_linked_as_the_interpreter_it_resolves_to(tmp_path):
    bin_dir = tmp_path / "pybin"
    bin_dir.mkdir()
    (bin_dir / "mypython").symlink_to(sys.executable)
    result, run_dir = _build(
        tmp_path, "visual-inside-workspace", python3="mypython", path=f"{bin_dir}:/usr/bin:/bin"
    )
    assert result.returncode == 0, result.stderr
    _assert_arm_python3_imports_tomllib(run_dir)


def test_a_symlinked_interpreter_is_linked_by_its_real_path(tmp_path):
    venvish = tmp_path / "venvish" / "bin"
    venvish.mkdir(parents=True)
    given = venvish / "python"
    given.symlink_to(sys.executable)
    result, run_dir = _build(tmp_path, "visual-inside-workspace", python3=str(given))
    assert result.returncode == 0, result.stderr
    target = os.readlink(run_dir / "bin" / "python3")
    assert target == os.path.realpath(given)
    assert target != str(given)


def test_an_absolute_path_python3_is_linked_as_an_importing_interpreter(tmp_path):
    result, run_dir = _build(tmp_path, "visual-inside-workspace", python3=sys.executable)
    assert result.returncode == 0, result.stderr
    _assert_arm_python3_imports_tomllib(run_dir)


def test_an_unset_python3_defaults_to_the_one_on_the_builders_path(tmp_path):
    bin_dir = tmp_path / "pybin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    result, run_dir = _build(
        tmp_path, "visual-inside-workspace", python3=None, path=f"{bin_dir}:/usr/bin:/bin"
    )
    assert result.returncode == 0, result.stderr
    _assert_arm_python3_imports_tomllib(run_dir)


def _relative_files(root: Path, skip_pycache: bool = True) -> set[str]:
    return {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if (p.is_file() or p.is_symlink())
        and not (skip_pycache and "__pycache__" in p.relative_to(root).parts)
    }


def test_the_plugin_copy_is_the_source_tree_without_evals_and_rules(tmp_path):
    source = CASE_DIR.parent.parent
    py_compile.compile(
        str(source / "skills" / "scratch-pad" / "scratch_pad.py"), doraise=True
    )
    assert list(source.rglob("__pycache__"))
    run_dir = _built(tmp_path, "visual-inside-workspace")
    expected = {
        rel
        for rel in _relative_files(source)
        if rel != "rules.md" and not rel.startswith("evals/")
    }
    assert expected
    assert _relative_files(run_dir / "plugin", skip_pycache=False) == expected
    assert not list((run_dir / "plugin").rglob("__pycache__"))


def test_a_non_empty_run_directory_is_refused_and_left_untouched(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    victim = run_dir / "run.env"
    victim.write_text("keep me")
    result, _ = _build(tmp_path, "visual-inside-workspace")
    assert result.returncode == 2
    assert str(run_dir) in result.stderr
    assert victim.read_text() == "keep me"
    assert list(run_dir.iterdir()) == [victim]


def test_a_symlink_left_in_a_run_directory_is_not_written_through(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    (run_dir / "prompt.md").symlink_to(outside)
    result, _ = _build(tmp_path, "visual-inside-workspace")
    assert result.returncode == 2
    assert outside.read_text() == "keep me"


def test_an_existing_empty_run_directory_is_accepted(tmp_path):
    (tmp_path / "run").mkdir()
    result, run_dir = _build(tmp_path, "visual-inside-workspace")
    assert result.returncode == 0, result.stderr
    assert (run_dir / "run.env").is_file()


def test_a_run_directory_path_with_a_space_is_refused_and_builds_nothing(tmp_path):
    spaced = tmp_path / "run dir"
    result = subprocess.run(
        ["bash", str(BUILDER), str(spaced), "visual-inside-workspace"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "EVAL_PYTHON3": sys.executable},
    )
    assert result.returncode == 2
    assert "run dir" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_the_interpreter_probes_do_not_import_from_the_current_directory(tmp_path):
    marker = tmp_path / "marker"
    cwd = tmp_path / "planted"
    cwd.mkdir()
    (cwd / "tomllib.py").write_text(
        f"open({str(marker)!r}, 'w').write('ran')\n"
    )
    result = subprocess.run(
        ["bash", str(BUILDER), str(tmp_path / "run"), "visual-inside-workspace"],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={"PATH": "/usr/bin:/bin", "EVAL_PYTHON3": sys.executable},
    )
    assert not marker.exists(), result.stderr
    assert result.returncode == 0, result.stderr


def test_each_variant_leaves_the_brief_for_the_arm_to_pass_as_the_prompt(tmp_path):
    briefs = {}
    for variant in VARIANTS:
        run_dir = _built(tmp_path / variant, variant)
        briefs[variant] = (run_dir / "prompt.md").read_text()
        assert briefs[variant].strip(), variant
    assert len(set(briefs.values())) == len(VARIANTS)


def test_an_unknown_variant_is_refused_and_builds_nothing(tmp_path):
    result, run_dir = _build(tmp_path, "no-such-variant")
    assert result.returncode != 0
    assert "no-such-variant" in result.stderr
    assert not (run_dir / "run.env").exists()


def _brief() -> str:
    return ac.read_arm(ARMS_DIR, BASELINE).split(ac.SEPARATOR, 1)[0]


def test_every_arm_on_disk_has_a_manifest_entry():
    ac.assert_arms_accounted_for(ARMS_DIR, {BASELINE, TREATMENT})


def test_live_treatment_arm_rebuilds_from_todays_rules():
    expected = ac.build_arm(_brief(), ac.current_rules())
    assert ac.read_arm(ARMS_DIR, TREATMENT) == expected


def test_both_arms_carry_the_same_brief_and_differ_only_in_the_ruleset():
    baseline, treatment = (ac.read_arm(ARMS_DIR, n).split(ac.SEPARATOR, 1) for n in (BASELINE, TREATMENT))
    assert baseline[0] == treatment[0]
    assert baseline[1] != treatment[1]


FLOW_ROUND = {
    "title": "Probe",
    "questions": [
        {
            "prompt": "Which process?",
            "options": [
                {"name": "Synchronous", "cost": "Slow.", "pick": True, "flow": "flowchart LR\n  a[Ask] --> b[Answer]"},
                {"name": "Queued", "cost": "Late.", "flow": "flowchart LR\n  a[Ask] --> q[Queue] --> b[Answer]"},
            ],
        }
    ],
}


def _capture(tmp_path: Path, final: str = "Done.") -> Path:
    capture = tmp_path / "run.capture.jsonl"
    events = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {}}]}},
        {"type": "result", "result": final},
    ]
    capture.write_text("\n".join(json.dumps(e) for e in events))
    return capture


def _grade(tmp_path: Path, run_dir: Path, variant: str = "flow-outside-workspace") -> dict:
    result = subprocess.run(
        [sys.executable, str(GRADER), str(run_dir), str(_capture(tmp_path)), variant],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _draw(run_dir: Path, pad: str, round_: dict) -> None:
    assert _run_pad(run_dir, pad, round_).returncode == 0


def test_the_grader_counts_flow_options_in_a_pad_a_run_left_under_the_outpost_state(tmp_path):
    run_dir = _built(tmp_path, "flow-outside-workspace")
    _draw(run_dir, "refund-approval", FLOW_ROUND)
    graded = _grade(tmp_path, run_dir)
    assert graded["pads"] == ["refund-approval"]
    assert graded["flow options"] == 2
    assert graded["final message"] == "Done."


def test_the_grader_counts_no_flow_options_when_the_pad_holds_only_layouts(tmp_path):
    run_dir = _built(tmp_path, "flow-outside-workspace")
    _draw(run_dir, "filters", ROUND)
    graded = _grade(tmp_path, run_dir)
    assert graded["pads"] == ["filters"]
    assert graded["flow options"] == 0


def test_the_grader_reports_no_pad_for_a_run_that_drew_nothing(tmp_path):
    run_dir = _built(tmp_path, "not-visual-flow-vocabulary")
    graded = _grade(tmp_path, run_dir, "not-visual-flow-vocabulary")
    assert graded["pads"] == []
    assert graded["flow options"] == 0


def _round_js(pad: Path) -> Path:
    return pad / "round-1" / "round.js"


def _only_pad(run_dir: Path) -> Path:
    return next(run_dir.glob("xdg-state/outpost/pads/*"))


@pytest.mark.parametrize("damage", ["", "\n", "window.PAD_ROUND = JSON.parse(not json);"])
def test_the_grader_records_a_malformed_or_empty_round_as_a_grading_failure_for_that_pad(tmp_path, damage):
    run_dir = _built(tmp_path, "flow-outside-workspace")
    _draw(run_dir, "refund-approval", FLOW_ROUND)
    _draw(run_dir, "filters", FLOW_ROUND)
    broken = run_dir / "xdg-state/outpost/pads/filters"
    _round_js(broken).write_text(damage)
    graded = _grade(tmp_path, run_dir)
    assert graded["pads"] == ["filters", "refund-approval"]
    assert graded["flow options"] == 2
    assert [f["pad"] for f in graded["grading failures"]] == ["filters"]
    assert graded["grading failures"][0]["reason"]


def test_the_grader_skips_a_round_js_that_is_a_symlink(tmp_path):
    run_dir = _built(tmp_path, "flow-outside-workspace")
    _draw(run_dir, "refund-approval", FLOW_ROUND)
    pad = _only_pad(run_dir)
    real = tmp_path / "elsewhere.js"
    real.write_text(_round_js(pad).read_text())
    _round_js(pad).unlink()
    _round_js(pad).symlink_to(real)
    graded = _grade(tmp_path, run_dir)
    assert graded["flow options"] == 0
    assert graded["grading failures"] == []


def test_a_clean_run_has_no_grading_failures(tmp_path):
    run_dir = _built(tmp_path, "flow-outside-workspace")
    _draw(run_dir, "refund-approval", FLOW_ROUND)
    assert _grade(tmp_path, run_dir)["grading failures"] == []
