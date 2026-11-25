"""The test-rules slimming A/B: the per-arm plugin copy and the case file it writes, the grants, the bar,
the fixtures' scaffold and `./run-tests` wrapper, and the graders that read the wrapper's log. Everything
runs through the experiment's own driver (`ab.py`) and its real scaffold scripts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CRAFT = REPO / "tools" / "craft" / "plugins" / "craft"
EXP = REPO / "tools" / "craft" / "experiments" / "ab-test-rules-slimming"

_spec = importlib.util.spec_from_file_location("ab_test_rules_under_test", EXP / "ab.py")
ab = importlib.util.module_from_spec(_spec)
sys.modules["ab_test_rules_under_test"] = ab
_spec.loader.exec_module(ab)

WRAPPER_GRANT = "Bash(./run-tests)"
RULES_TEXT = 'Rule one: "quote" # not a comment\n\n- second — naïve\n'
FROZEN_TEXT = "Frozen rules, a different text.\n"


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()
    }


def _files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() or p.is_symlink()}


def _mini(tmp_path, rules=RULES_TEXT, frozen=FROZEN_TEXT):
    """A small craft plugin and a small experiment folder, so the sources can be varied and compared."""
    craft = tmp_path / "src" / "craft"
    (craft / "skills").mkdir(parents=True)
    (craft / "evals" / "other-case").mkdir(parents=True)
    (craft / "skills" / "one.md").write_text("one\n")
    (craft / "rules.md").write_text(rules)
    (craft / "evals" / "eval-gate.json").write_text(json.dumps({"runner": {"allow_tools": ["Write", "Bash(./x *)"]}}))
    (craft / "evals" / "other-case" / "case.yaml").write_text("name: other\n")
    exp = tmp_path / "src" / "mini-exp"
    (exp / "c1" / "fixtures").mkdir(parents=True)
    (exp / "c1" / "case.yaml").write_text('schema_version: "1.1"\nname: c1\nruns: 3\n')
    (exp / "c1" / "prompt.md").write_text("do it\n")
    (exp / "c1" / "fixtures" / "scaffold.sh").write_text("#!/bin/sh\n")
    (exp / "scaffold").mkdir()
    (exp / "scaffold" / "scaffold.sh").write_text("#!/bin/sh\n")
    (exp / "arms" / "frozen").mkdir(parents=True)
    (exp / "arms" / "frozen" / "rules.md").write_text(frozen)
    (exp / "expected.md").write_text("the verdict is predicted here\n")
    (exp / "ab.py").write_text("# driver\n")
    return craft, exp


def _written_path(dest, exp, case="c1"):
    return Path(dest) / "evals" / exp.name / case / "case.yaml"


def _written(dest, exp, case="c1"):
    return _written_path(dest, exp, case).read_text()


# --- prepare: the ruleset each arm carries ---------------------------------------------------


@pytest.mark.parametrize("rules, frozen", [(RULES_TEXT, FROZEN_TEXT), ("Other live text.\n", "Other frozen text.\n")])
def test_prepare_sets_each_arms_ruleset_from_its_own_source(rules, frozen, tmp_path):
    craft, exp = _mini(tmp_path, rules, frozen)
    got = {}
    for arm in ("slimmed", "frozen", "none"):
        dest = tmp_path / f"out-{arm}" / "craft"
        ab.build_arm_plugin(arm, "c1", dest, craft_root=craft, exp_dir=exp)
        got[arm] = ab.get_append_system_prompt(_written_path(dest, exp))
    assert got == {"slimmed": rules, "frozen": frozen, "none": None}


def test_the_none_arm_drops_a_prompt_the_case_file_already_carried(tmp_path):
    craft, exp = _mini(tmp_path)
    (exp / "c1" / "case.yaml").write_text('name: c1\nexecution:\n  append_system_prompt: "stale"\nruns: 3\n')
    dest = tmp_path / "out" / "craft"
    ab.build_arm_plugin("none", "c1", dest, craft_root=craft, exp_dir=exp)
    text = _written(dest, exp)
    assert "append_system_prompt" not in text
    assert "name: c1" in text and "runs: 3" in text


@pytest.mark.parametrize("args", [("bogus", ab.CASES[0]), ("none", "no-such-case")])
def test_prepare_refuses_an_unknown_arm_or_case_and_writes_nothing(args, tmp_path):
    dest = tmp_path / "out" / "craft"
    done = subprocess.run(
        [sys.executable, str(EXP / "ab.py"), "prepare", *args, str(dest)],
        capture_output=True, text=True, timeout=60,
    )
    assert done.returncode != 0
    assert not (tmp_path / "out").exists()
    assert "arm" in done.stderr or "case" in done.stderr


def test_prepare_leaves_the_source_plugin_and_experiment_byte_identical(tmp_path):
    craft, exp = _mini(tmp_path)
    before = (_hashes(craft), _hashes(exp))
    for arm in ("slimmed", "frozen", "none"):
        ab.build_arm_plugin(arm, "c1", tmp_path / f"out-{arm}" / "craft", craft_root=craft, exp_dir=exp)
        assert (_hashes(craft), _hashes(exp)) == before, arm


def test_the_live_craft_tree_is_not_written_by_a_real_prepare(tmp_path):
    before = _hashes(CRAFT)
    ab.build_arm_plugin("slimmed", ab.CASES[0], tmp_path / "out" / "craft")
    assert _hashes(CRAFT) == before


def test_prepare_refuses_to_write_through_a_symlink_destination(tmp_path):
    craft, exp = _mini(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    dest = tmp_path / "link"
    dest.symlink_to(elsewhere)
    with pytest.raises(ValueError, match="symlink"):
        ab.build_arm_plugin("none", "c1", dest, craft_root=craft, exp_dir=exp)
    assert not elsewhere.exists()


def test_the_copy_holds_the_plugin_and_only_the_chosen_case_and_scaffold(tmp_path):
    craft, exp = _mini(tmp_path)
    dest = tmp_path / "out" / "craft"
    ab.build_arm_plugin("slimmed", "c1", dest, craft_root=craft, exp_dir=exp)
    plugin_files = {f for f in _files(craft) if not f.startswith("evals/") and f != "rules.md"}
    case_files = {f"evals/{exp.name}/c1/{f}" for f in _files(exp / "c1")}
    scaffold_files = {f"evals/{exp.name}/scaffold/{f}" for f in _files(exp / "scaffold")}
    assert _files(dest) == plugin_files | case_files | scaffold_files


# --- the case file's prompt, one function for every caller --------------------------------------


def test_set_append_system_prompt_adds_replaces_and_removes_the_prompt(tmp_path):
    case = tmp_path / "case.yaml"
    case.write_text('schema_version: "1.1"\nname: c\nruns: 3\n')
    ab.set_append_system_prompt(case, RULES_TEXT)
    assert ab.get_append_system_prompt(case) == RULES_TEXT
    ab.set_append_system_prompt(case, "second\n")
    assert ab.get_append_system_prompt(case) == "second\n"
    assert case.read_text().count("append_system_prompt") == 1
    ab.set_append_system_prompt(case, None)
    assert case.read_text() == 'schema_version: "1.1"\nname: c\nruns: 3\n'


def test_set_append_system_prompt_keeps_the_other_execution_keys(tmp_path):
    case = tmp_path / "case.yaml"
    case.write_text("name: c\nexecution:\n  model: m\n  append_system_prompt: \"old\"\n  max_turns: 9\nruns: 3\n")
    ab.set_append_system_prompt(case, "new\n")
    text = case.read_text()
    assert ab.get_append_system_prompt(case) == "new\n"
    assert "  model: m\n" in text and "  max_turns: 9\n" in text and text.endswith("runs: 3\n")
    ab.set_append_system_prompt(case, None)
    assert "  model: m\n" in case.read_text() and "append_system_prompt" not in case.read_text()


# --- grants and the bar ---------------------------------------------------------------------------


def test_runner_grants_are_the_gate_mappings_grants(tmp_path):
    craft, _ = _mini(tmp_path)
    assert ab.runner_grants(craft) == ["Write", "Bash(./x *)"]
    (craft / "evals" / "eval-gate.json").write_text(json.dumps({"runner": {"allow_tools": ["Read"]}}))
    assert ab.runner_grants(craft) == ["Read"]


def test_the_grants_command_prints_what_shlex_parses_back_to_the_live_grants():
    done = subprocess.run([sys.executable, str(EXP / "ab.py"), "grants"], capture_output=True, text=True, timeout=60)
    live = json.loads((CRAFT / "evals" / "eval-gate.json").read_text())["runner"]["allow_tools"]
    assert done.returncode == 0
    assert shlex.split(done.stdout) == live


# inert-gate: allow the push gate reads this block from main; the grant has no other consumer to run here
def test_the_gate_grants_run_tests_in_its_exact_form_and_no_wider():
    grants = ab.shared.runner_args(CRAFT)[1:]
    assert grants.count(WRAPPER_GRANT) == 1
    assert [g for g in grants if "run-tests" in g] == [WRAPPER_GRANT]


def test_bar_text_is_the_text_between_the_delimiters(tmp_path):
    md = tmp_path / "expected.md"
    md.write_text("intro\n<!-- bar-begin -->\nthe bar\nline two\n<!-- bar-end -->\nafter\n")
    assert ab.bar_text(md) == "the bar\nline two\n"
    md.write_text("no bar here\n")
    assert ab.bar_text(md) == ""


def test_the_bar_command_prints_the_committed_bar():
    done = subprocess.run([sys.executable, str(EXP / "ab.py"), "bar"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0
    assert done.stdout == ab.bar_text(EXP / "expected.md") != ""


# --- the fixtures, through the copy `prepare` writes ---------------------------------------------

AC61 = ("tests-a-registered-exporter-by-running-it", "tests-a-changed-default-through-what-it-changes")
AC62 = ("adds-a-cli-flag-test-first", "fixes-a-reported-bug-test-first")


def _env(home):
    return {"PATH": os.environ["PATH"], "HOME": str(home)}


def _scaffolded(case, tmp_path):
    """Run the prepared copy's own scaffold script in an empty directory, as the runner does."""
    plugin = ab.build_arm_plugin("none", case, tmp_path / "plugin" / "craft")
    cwd = tmp_path / "run"
    cwd.mkdir()
    done = subprocess.run(
        ["bash", str(plugin / "evals" / EXP.name / case / "fixtures" / "scaffold.sh")],
        cwd=cwd, env=_env(tmp_path), capture_output=True, text=True, timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return plugin, cwd


def _run_tests(cwd, *args):
    done = subprocess.run(
        ["./run-tests", *args], cwd=cwd, env=_env(cwd.parent), capture_output=True, text=True, timeout=120)
    return done.returncode, done.stdout


def _log(cwd):
    log = cwd / "run-tests.log"
    return log.read_text() if log.exists() else None


@pytest.mark.parametrize("case", ab.CASES)
def test_each_fixture_scaffolds_a_passing_suite_whose_first_run_is_logged_untouched(case, tmp_path):
    _, cwd = _scaffolded(case, tmp_path)
    assert _log(cwd) is None
    code, output = _run_tests(cwd)
    assert code == 0, output
    assert _log(cwd) == "exit=0 impl=untouched\n"
    assert not list(cwd.rglob("__pycache__"))


def test_run_tests_takes_no_arguments_into_account(tmp_path):
    _, cwd = _scaffolded(AC62[0], tmp_path)
    code, _ = _run_tests(cwd, "-k", "nothing_matches_this", "tests.test_cli")
    assert code == 0
    assert _log(cwd) == "exit=0 impl=untouched\n"


# --- the AC62 graders, applied to logs made by driving ./run-tests by hand ------------------------

SOLUTIONS = {
    "adds-a-cli-flag-test-first": {
        "test": ("tests/test_precision.py", '''import io
import unittest

from addup.cli import main


class PrecisionTest(unittest.TestCase):
    def test_precision_sets_the_decimals_of_the_total(self):
        out = io.StringIO()
        self.assertEqual(main(["--precision", "2", "1", "2.5"], out=out), 0)
        self.assertEqual(out.getvalue(), "3.50\\n")
'''),
        "impl": ("addup/cli.py", '''import argparse
import sys


def main(argv=None, out=None):
    parser = argparse.ArgumentParser(prog="addup", description="Add up numbers.")
    parser.add_argument("numbers", nargs="+", type=float)
    parser.add_argument("--precision", type=int)
    args = parser.parse_args(argv)
    out = out or sys.stdout
    total = sum(args.numbers)
    print(f"{total:g}" if args.precision is None else f"{total:.{args.precision}f}", file=out)
    return 0
'''),
    },
    "fixes-a-reported-bug-test-first": {
        "test": ("tests/test_equals_in_value.py", '''import unittest

from kvparse.parser import parse_pairs


class EqualsInValueTest(unittest.TestCase):
    def test_a_value_keeps_the_equals_signs_it_contains(self):
        self.assertEqual(parse_pairs("token=abc=def;mode=fast"), {"token": "abc=def", "mode": "fast"})
'''),
        "impl": ("kvparse/parser.py", '''def parse_pairs(text):
    """Turn 'a=1; b=2' into {'a': '1', 'b': '2'}."""
    result = {}
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        key, value = part.split("=", 1)
        result[key.strip()] = value.strip()
    return result
'''),
    },
}

GRADERS_62 = (
    "a-failing-run-was-logged-before-the-code-changed",
    "a-passing-run-was-logged-after-the-code-changed",
    "the-failing-run-came-before-the-passing-one",
)


def _grade(grader_path, cwd) -> bool:
    """Apply a `regex` grader file the way the runner does: look for the pattern in the file the grader
    names; `match: not_contains` inverts it; a file the run never made fails the grader."""
    g = ab.shared._front_matter(grader_path)
    assert g["type"] == "regex" and g["source"] == "file", g
    target = cwd / g["path"]
    if not target.exists():
        return False
    found = re.search(g["pattern"], target.read_text()) is not None
    return found if g.get("match", "contains") == "contains" else not found


def _drive(case, steps, cwd):
    """Do what an agent would, in order: t write the new test, i make the change, r run ./run-tests."""
    sol, codes = SOLUTIONS[case], []
    for step in steps:
        if step in "ti":
            rel, text = sol["test" if step == "t" else "impl"]
            (cwd / rel).write_text(text)
        else:
            codes.append(_run_tests(cwd)[0])
    return codes


@pytest.mark.parametrize("case", AC62)
@pytest.mark.parametrize("steps, verdicts, codes", [
    ("trir", (True, True, True), [1, 0]),
    ("itr", (False, True, False), [0]),
    ("ritr", (False, True, False), [0, 0]),
    ("tr", (True, False, False), [1]),
    ("ti", (False, False, False), []),
    ("r", (False, False, False), [0]),
])
def test_the_log_graders_pass_only_a_run_that_went_red_before_the_code_changed(case, steps, verdicts, codes, tmp_path):
    plugin, cwd = _scaffolded(case, tmp_path)
    got_codes = _drive(case, steps, cwd)
    assert [c == 0 for c in got_codes] == [c == 0 for c in codes]
    graders = plugin / "evals" / EXP.name / case / "graders"
    assert tuple(_grade(graders / f"{name}.md", cwd) for name in GRADERS_62) == verdicts


# --- the AC61 planted test files: what the `llm` grader is asked to tell apart -----------------

PLANTED = {
    "tests-a-registered-exporter-by-running-it": {
        "good": ("tests/test_tsv_export.py", '''import unittest

from reportbay.registry import export


class TsvExportTest(unittest.TestCase):
    def test_rows_become_tab_separated_lines_under_a_header(self):
        rows = [{"name": "a\\tb", "qty": 2}]
        self.assertEqual(export(rows, "tsv"), "name\\tqty\\na b\\t2\\n")
'''),
        "bad": ("tests/test_tsv_export.py", '''import unittest

from reportbay.registry import EXPORTERS


class TsvExportTest(unittest.TestCase):
    def test_tsv_is_registered(self):
        self.assertIn("tsv", EXPORTERS)
'''),
        "break": ("reportbay/exporters.py", '.replace("\\t", " ")', ""),
    },
    "tests-a-changed-default-through-what-it-changes": {
        "good": ("tests/test_retry_limit.py", '''import unittest

from courier.retry import deliver


class RetryLimitTest(unittest.TestCase):
    def test_a_send_that_keeps_failing_is_tried_five_times_then_raised(self):
        calls = []

        def send(message):
            calls.append(message)
            raise ConnectionError("down")

        with self.assertRaises(ConnectionError):
            deliver(send, "hello")
        self.assertEqual(len(calls), 5)
'''),
        "bad": ("tests/test_retry_limit.py", '''import unittest

from courier import retry


class RetryLimitTest(unittest.TestCase):
    def test_the_limit_is_five(self):
        self.assertEqual(retry.RETRY_LIMIT, 5)
'''),
        "break": ("courier/retry.py", "range(limit)", "range(1)"),
    },
}


@pytest.mark.parametrize("case", AC61)
def test_the_planted_files_both_run_green_but_only_the_good_one_fails_a_broken_subject(case, tmp_path):
    planted = PLANTED[case]
    results = {}
    for which in ("good", "bad"):
        (tmp_path / which).mkdir()
        _, cwd = _scaffolded(case, tmp_path / which)
        rel, text = planted[which]
        (cwd / rel).write_text(text)
        green = _run_tests(cwd)
        impl, old, new = planted["break"]
        source = (cwd / impl).read_text()
        assert old in source
        (cwd / impl).write_text(source.replace(old, new))
        results[which] = (green[0], _run_tests(cwd)[0])
    assert results["good"][0] == 0 and results["bad"][0] == 0
    assert results["good"][1] != 0 and results["bad"][1] == 0


def test_the_recorded_fingerprint_matches_the_bar():
    text = (EXP / "expected.md").read_text()
    recorded = re.search(r"recorded before any cell: `([0-9a-f]{40})`", text).group(1)
    done = subprocess.run(
        ["git", "hash-object", "--stdin"], input=ab.bar_text(), capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == recorded


# --- what the AC61 graders can see: the contents of the one file the prompt names ----------------


def _llm_sees(grader_path, cwd):
    """What an `llm` grader is shown: `focus: {source: file, path}` reads that file's contents; any other
    focus (such as `files`, which lists created paths without contents) shows no file contents."""
    g = ab.shared._front_matter(grader_path)
    assert g["type"] == "llm", g
    if g.get("focus") != "" or g.get("source") != "file":
        return None
    target = cwd / g["path"]
    return target.read_text() if target.exists() else None


@pytest.mark.parametrize("case", AC61)
@pytest.mark.parametrize("which", ["good", "bad"])
def test_the_llm_grader_is_shown_the_test_file_the_prompt_names(case, which, tmp_path):
    plugin, cwd = _scaffolded(case, tmp_path)
    copy = plugin / "evals" / EXP.name / case
    rel, text = PLANTED[case][which]
    judges = [p for p in (copy / "graders").glob("*.md") if ab.shared._front_matter(p)["type"] == "llm"]
    assert len(judges) == 1
    assert _llm_sees(judges[0], cwd) is None
    assert f"`{rel}`" in (copy / "prompt.md").read_text()
    (cwd / rel).write_text(text)
    assert _llm_sees(judges[0], cwd) == text


@pytest.mark.parametrize("case", AC61)
def test_the_file_exists_grader_checks_the_path_the_prompt_names(case, tmp_path):
    plugin, cwd = _scaffolded(case, tmp_path)
    graders = plugin / "evals" / EXP.name / case / "graders"
    exists = [ab.shared._front_matter(p) for p in graders.glob("*.md")]
    exists = [g for g in exists if g["type"] == "file_exists"]
    assert len(exists) == 1
    rel, text = PLANTED[case]["good"]
    assert not list(cwd.glob(exists[0]["path"]))
    (cwd / rel).write_text(text)
    assert [p.relative_to(cwd).as_posix() for p in cwd.glob(exists[0]["path"])] == [rel]


# --- sync and check: the committed push-gate cases carry the live rules ---------------------------


def _committed(tmp_path, cases=("c1", "c2")):
    """A mini craft plugin whose evals/ is to hold the committed cases, and a mini experiment with `cases`."""
    craft, exp = _mini(tmp_path)
    for name in cases:
        if name != "c1":
            shutil.copytree(exp / "c1", exp / name)
        (exp / name / "graders").mkdir(exist_ok=True)
        (exp / name / "graders" / "g.md").write_text(f"grader for {name}\n")
        (exp / name / "case.yaml").write_text(f'schema_version: "1.1"\nname: {name}\nruns: 3\n')
    return craft, exp, tuple(cases)


def _committed_case_yaml(craft, name):
    return craft / "evals" / name / "case.yaml"


def test_sync_writes_the_live_rules_text_into_each_committed_case(tmp_path):
    craft, exp, cases = _committed(tmp_path)
    ab.sync(craft, exp, cases)
    for name in cases:
        assert ab.get_append_system_prompt(_committed_case_yaml(craft, name)) == RULES_TEXT
        assert (craft / "evals" / name / "prompt.md").read_text() == "do it\n"
        assert (craft / "evals" / name / "graders" / "g.md").read_text() == f"grader for {name}\n"


def test_sync_carries_a_changed_rules_text_and_leaves_other_cases_alone(tmp_path):
    craft, exp, cases = _committed(tmp_path)
    ab.sync(craft, exp, cases)
    (craft / "rules.md").write_text("A different rule set.\n")
    ab.sync(craft, exp, cases)
    for name in cases:
        assert ab.get_append_system_prompt(_committed_case_yaml(craft, name)) == "A different rule set.\n"
    assert (craft / "evals" / "other-case" / "case.yaml").read_text() == "name: other\n"


def test_sync_replaces_a_grader_the_experiment_no_longer_has(tmp_path):
    craft, exp, cases = _committed(tmp_path)
    ab.sync(craft, exp, cases)
    (exp / "c1" / "graders" / "g.md").unlink()
    (exp / "c1" / "graders" / "h.md").write_text("new grader\n")
    ab.sync(craft, exp, cases)
    assert sorted(p.name for p in (craft / "evals" / "c1" / "graders").iterdir()) == ["h.md"]


def test_sync_keeps_the_committed_expected_md_and_scaffold_wrapper(tmp_path):
    craft, exp, cases = _committed(tmp_path)
    ab.sync(craft, exp, cases)
    (craft / "evals" / "c1" / "expected.md").write_text("kept\n")
    (craft / "evals" / "c1" / "fixtures").mkdir()
    (craft / "evals" / "c1" / "fixtures" / "scaffold.sh").write_text("wrapper\n")
    ab.sync(craft, exp, cases)
    assert (craft / "evals" / "c1" / "expected.md").read_text() == "kept\n"
    assert (craft / "evals" / "c1" / "fixtures" / "scaffold.sh").read_text() == "wrapper\n"


def test_check_passes_on_synced_cases(tmp_path):
    craft, exp, cases = _committed(tmp_path)
    ab.sync(craft, exp, cases)
    assert ab.check(craft, exp, cases) == []


def test_check_names_the_one_case_whose_text_was_edited_by_a_byte(tmp_path):
    craft, exp, cases = _committed(tmp_path)
    ab.sync(craft, exp, cases)
    path = _committed_case_yaml(craft, "c2")
    ab.set_append_system_prompt(path, RULES_TEXT + " ")
    problems = ab.check(craft, exp, cases)
    assert [p for p in problems if "c1" in p] == []
    assert len(problems) == 1 and "c2" in problems[0] and "rules.md" in problems[0]


def test_check_fails_for_every_case_after_rules_md_changes_without_a_sync(tmp_path):
    craft, exp, cases = _committed(tmp_path)
    ab.sync(craft, exp, cases)
    (craft / "rules.md").write_text(RULES_TEXT + "one more rule\n")
    problems = ab.check(craft, exp, cases)
    assert [c for c in cases if any(c in p for p in problems)] == list(cases)


@pytest.mark.parametrize("what", ["prompt", "grader-edited", "grader-missing", "grader-extra", "case-key", "no-case"])
def test_check_names_a_case_whose_prompt_graders_or_case_file_diverge_from_the_experiment(what, tmp_path):
    craft, exp, cases = _committed(tmp_path)
    ab.sync(craft, exp, cases)
    case = craft / "evals" / "c1"
    if what == "prompt":
        (case / "prompt.md").write_text("do it differently\n")
    elif what == "grader-edited":
        (case / "graders" / "g.md").write_text("weaker grader\n")
    elif what == "grader-missing":
        (case / "graders" / "g.md").unlink()
    elif what == "grader-extra":
        (case / "graders" / "extra.md").write_text("extra\n")
    elif what == "case-key":
        (case / "case.yaml").write_text((case / "case.yaml").read_text().replace("runs: 3", "runs: 1"))
    else:
        shutil.rmtree(case)
    problems = ab.check(craft, exp, cases)
    assert len(problems) == 1 and "c1" in problems[0]


def test_the_cli_syncs_then_checks_and_check_exits_nonzero_naming_a_drifted_case(tmp_path, monkeypatch, capsys):
    base = tmp_path / "tools" / "craft"
    craft, exp, cases = _committed(tmp_path)
    (base / "plugins").mkdir(parents=True)
    (base / "experiments").mkdir()
    shutil.copytree(craft, base / "plugins" / "craft")
    shutil.copytree(exp, base / "experiments" / exp.name)
    monkeypatch.setattr(ab, "EXP", base / "experiments" / exp.name)
    monkeypatch.setattr(ab, "CASES", cases)
    assert ab.main(["ab.py", "check"]) == 1
    assert "c1" in capsys.readouterr().err
    assert ab.main(["ab.py", "sync"]) == 0
    assert ab.main(["ab.py", "check"]) == 0
    (base / "plugins" / "craft" / "rules.md").write_text("edited\n")
    assert ab.main(["ab.py", "check"]) == 1
    err = capsys.readouterr().err
    assert "c1" in err and "c2" in err and "sync" in err


def test_the_real_committed_cases_carry_the_live_rules_text_and_the_experiments_files():
    assert ab.check() == []


@pytest.mark.parametrize("case", ab.CASES)
def test_each_committed_case_scaffolds_the_experiments_fixture(case, tmp_path):
    cwd = tmp_path / "run"
    cwd.mkdir()
    wrapper = CRAFT / "evals" / case / "fixtures" / "scaffold.sh"
    done = subprocess.run(["bash", str(wrapper)], cwd=cwd, env=_env(tmp_path), capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    code, output = _run_tests(cwd)
    assert code == 0, output
    assert _log(cwd) == "exit=0 impl=untouched\n"


# --- the case file stays scannable: a pointer in the text is followed by a real line break -------

POINTER_TEXT = "Rules.\n\ntrailhead core show adr/some-decision\n\n- next rule\n"


def test_the_block_written_for_a_known_text_has_the_two_indent_header_and_four_space_body(tmp_path):
    case = tmp_path / "case.yaml"
    case.write_text("name: c\n")
    ab.set_append_system_prompt(case, "a\n\n- b\n")
    assert case.read_bytes() == b"name: c\nexecution:\n  append_system_prompt: |2\n    a\n\n    - b\n"


def test_a_pointer_in_the_appended_text_is_not_glued_to_what_follows_it(tmp_path):
    case = tmp_path / "case.yaml"
    case.write_text("name: c\nruns: 3\n")
    ab.set_append_system_prompt(case, POINTER_TEXT)
    assert re.search(r"trailhead\s+core\s+show\s+(\S+)", case.read_text()).group(1) == "adr/some-decision"


@pytest.mark.parametrize("text", [
    POINTER_TEXT,
    "no trailing newline",
    "two trailing\n\n",
    "  leading spaces\n\n\nblank lines above\n",
    'quotes " and \' # not a comment: no | > & * !\n- dash\n',
    "tab\there\nnaïve — ünïcode\n",
    "crlf\r\nline\n",
    "trailing spaces   \nnext\n",
    "a\n   \nb\n",
    "a\n      \nb\n",
    "a\n   ",
    "line\u2028separator\n",
    "next\x85line\n",
    "a\rb\n",
    "\n",
    "",
])
def test_the_appended_text_reads_back_exactly_and_the_other_keys_survive(text, tmp_path):
    case = tmp_path / "case.yaml"
    case.write_text("name: c\nexecution:\n  model: m\n  max_turns: 9\nruns: 3\n")
    ab.set_append_system_prompt(case, text)
    assert ab.get_append_system_prompt(case) == text
    ab.set_append_system_prompt(case, "x\n")
    assert ab.get_append_system_prompt(case) == "x\n"
    ab.set_append_system_prompt(case, None)
    assert case.read_text() == "name: c\nexecution:\n  model: m\n  max_turns: 9\nruns: 3\n"
    assert ab.get_append_system_prompt(case) is None
