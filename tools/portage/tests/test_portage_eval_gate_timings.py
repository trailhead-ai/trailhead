"""`portage eval-gate timings` — measured run times per case, read from the timing history.

Every test writes a throwaway repository (a plugin with an ``eval-gate.json`` and its cases) and a
history file under ``tmp_path`` (``PORTAGE_CACHE_DIR``), then drives the real
``dispatch.main(["eval-gate", "timings", ...])`` in process and reads stdout. History lines carry the
case's current pass-cache key, computed the way the gate computes it.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.cli import dispatch
from portage.eval_gate import canary, history
from portage.eval_gate import timings as gate_timings
from portage.eval_gate.run import MAX_TIMEOUT_SECONDS, _cache_key
from test_portage_eval_gate_run import PASS, RUNNER as GATE_RUNNER, Env, _result

LEFT = MAX_TIMEOUT_SECONDS - canary.TIMEOUT_SECONDS
RUNNER = {"model": "sonnet", "allow_tools": ["Read"], "max_cost_usd": 2.0}
STAMP = "2026-11-19T12:30:00Z"


class Repo:
    def __init__(self, tmp_path, monkeypatch):
        self.root = tmp_path / "repo"
        self.cache = tmp_path / "cache"
        monkeypatch.setenv("PORTAGE_CACHE_DIR", str(self.cache))
        monkeypatch.delenv("PORTAGE_EVAL_RUNNER", raising=False)
        self.cases: dict[str, dict] = {}

    def case(self, name, *, plugin="p", merge_only=False, runs=3, pass_at=2, prompt=None):
        base = self.root / "plugins" / plugin
        (base / "evals" / name).mkdir(parents=True, exist_ok=True)
        (base / "evals" / name / "case.yaml").write_text("name: x\n")
        if prompt is not None:
            (base / "evals" / name / "prompt.md").write_text(prompt)
        (base / "skills").mkdir(exist_ok=True)
        (base / "skills" / "s.md").write_text("prose\n")
        spec = {"measures": ["skills"], "runs": runs, "pass_at": pass_at}
        if merge_only:
            spec["merge_only"] = True
        self.cases.setdefault(plugin, {})[name] = spec
        (base / "evals" / "eval-gate.json").write_text(json.dumps(
            {"schema_version": 1, "runner": RUNNER, "cases": self.cases[plugin]}))

    def key(self, name, plugin="p"):
        spec = self.cases[plugin][name]
        entry = {"case": name, "runner": RUNNER, "runs": spec["runs"], "pass_at": spec["pass_at"],
                 "plugin_root": f"plugins/{plugin}", "measures": spec["measures"]}
        return _cache_key(self.root / "plugins" / plugin, entry)

    def line(self, name, *, plugin="p", elapsed=10, runs=None, verdict="pass", reason=None,
             fingerprint="current", pinned=False, version="2.1.328", at=STAMP, case=None):
        runs = [run(100), run(100)] if runs is None else runs
        return {"recorded_at": at, "plugin": plugin, "case": case or name,
                "fingerprint": self.key(name, plugin) if fingerprint == "current" else fingerprint,
                "runner_version": version, "pinned_runner": pinned, "tier": "merge",
                "verdict": verdict, "reason": reason, "budget_seconds": MAX_TIMEOUT_SECONDS,
                "elapsed_seconds": elapsed, "runs": runs}

    def write(self, *lines, name=history.HISTORY_NAME, raw=()):
        d = self.cache / "eval-gate"
        d.mkdir(parents=True, exist_ok=True)
        body = "".join(json.dumps(x) + "\n" for x in lines) + "".join(r + "\n" for r in raw)
        (d / name).write_text(body)

    def timings(self, capsys, *extra):
        rc = dispatch.main(["eval-gate", "timings", "--repo", str(self.root), *extra])
        out = capsys.readouterr()
        return rc, out.out, out.err

    def rows(self, capsys, *extra):
        rc, out, _ = self.timings(capsys, *extra)
        assert rc == 0
        return table(out)


def run(duration, *, errored=False, passed=True, cost=0.1, judge=0.0):
    return {"passed": passed, "errored": errored, "duration_seconds": duration,
            "cost_usd": cost, "judge_cost_usd": judge}


def table(out):
    lines = out.splitlines()
    head = next(i for i, x in enumerate(lines) if x.split()[:2] == ["plugin", "case"])
    cols = lines[head].split()
    return {r.split()[1]: dict(zip(cols, r.split())) for r in lines[head + 1:-1]}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    return Repo(tmp_path, monkeypatch)


def flags(row):
    return set(row["flags"].split(",")) - {"-"}


class TestCannotFinishASplit:
    @pytest.mark.parametrize("elapsed,flagged", [(LEFT / 2 - 1, False), (LEFT / 2 + 1, True)])
    def test_a_single_batch_decision_over_half_the_budget_left_cannot_finish_a_split(
        self, repo, capsys, elapsed, flagged
    ):
        repo.case("c1")
        repo.write(repo.line("c1", elapsed=elapsed))
        assert ("cannot-finish-a-split" in flags(repo.rows(capsys)["c1"])) is flagged

    def test_a_multi_batch_decision_is_not_a_single_batch_elapsed(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1", elapsed=LEFT - 1, runs=[run(100)] * 3))
        row = repo.rows(capsys)["c1"]
        assert row["batch-max"] == "-" and flags(row) == set()

    def test_the_single_batch_size_follows_the_mapped_runs_and_pass_at(self, repo, capsys):
        repo.case("c1", runs=5, pass_at=1)
        repo.write(repo.line("c1", elapsed=LEFT - 1, runs=[run(100)] * 2))
        assert repo.rows(capsys)["c1"]["batch-max"] == "-"
        repo.write(repo.line("c1", elapsed=LEFT - 1, runs=[run(100)]))
        assert repo.rows(capsys)["c1"]["batch-max"] == str(LEFT - 1)


class TestBatchMax:
    def test_a_decision_with_no_runs_is_not_taken_for_a_single_batch(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1", elapsed=10, runs=[]))
        assert repo.rows(capsys)["c1"]["batch-max"] == "-"

    def test_a_timed_out_case_counts_as_at_least_the_full_budget(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1", elapsed=5, runs=[run(None)]))
        assert repo.rows(capsys)["c1"]["batch-max"] == f">={MAX_TIMEOUT_SECONDS}"


class TestTimedOut:
    def test_a_timeout_decision_makes_the_case_take_all_three_flags(self, repo, capsys):
        repo.case("c1", merge_only=True)
        repo.write(repo.line("c1", elapsed=150, runs=[run(150), run(None)]),
                   repo.line("c1", elapsed=500, runs=[run(None), run(None)],
                             verdict="not-run", reason="timeout"))
        row = repo.rows(capsys)["c1"]
        assert flags(row) == {"timed-out", "cannot-finish-a-split", "over-merge-margin"}
        assert (row["max"], row["timeouts"], row["null-runs"]) == ("150", "1", "3")

    def test_a_scored_run_with_no_duration_alone_makes_the_case_timed_out(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1", runs=[run(100), run(None)]))
        row = repo.rows(capsys)["c1"]
        assert "timed-out" in flags(row) and row["null-runs"] == "1" and row["timeouts"] == "0"

    def test_an_errored_run_with_no_duration_is_not_a_null_duration(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1", runs=[run(100), run(None, errored=True, passed=False)]))
        row = repo.rows(capsys)["c1"]
        assert "timed-out" not in flags(row) and row["null-runs"] == "0"


class TestOverMergeMargin:
    def test_a_merge_only_case_over_the_budget_left_is_flagged(self, repo, capsys):
        repo.case("c1", merge_only=True)
        repo.write(repo.line("c1", elapsed=LEFT + 1))
        assert "over-merge-margin" in flags(repo.rows(capsys)["c1"])

    def test_the_same_numbers_on_a_core_case_are_not(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1", elapsed=LEFT + 1))
        row = repo.rows(capsys)["c1"]
        assert "over-merge-margin" not in flags(row) and row["tier"] == "core"

    def test_a_merge_only_case_at_exactly_the_budget_left_is_not(self, repo, capsys):
        repo.case("c1", merge_only=True)
        repo.write(repo.line("c1", elapsed=LEFT))
        row = repo.rows(capsys)["c1"]
        assert "over-merge-margin" not in flags(row) and row["tier"] == "merge-only"


class TestMedianAndMax:
    def test_only_scored_runs_with_a_duration_count(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1", runs=[run(100), run(300), run(200),
                                         run(999, errored=True, passed=False)]))
        row = repo.rows(capsys)["c1"]
        assert (row["median"], row["max"], row["runs"]) == ("200", "300", "4")

    def test_mean_cost_per_decision_sums_run_and_judge_cost(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1", runs=[run(1, cost=1.0, judge=0.5), run(1, cost=0.5)]),
                   repo.line("c1", runs=[run(1, cost=1.0)]))
        assert float(repo.rows(capsys)["c1"]["mean-cost"]) == pytest.approx(1.5)


class TestWhichLinesCount:
    def test_stale_and_pinned_lines_are_counted_and_not_used(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1", runs=[run(100), run(100)]),
                   repo.line("c1", fingerprint="0" * 64, runs=[run(900), run(900)]),
                   repo.line("c1", pinned=True, runs=[run(800), run(800)]))
        rc, out, _ = repo.timings(capsys)
        row = table(out)["c1"]
        assert (row["decisions"], row["max"]) == ("1", "100")
        assert "stale 1" in out.splitlines()[-1] and "pinned 1" in out.splitlines()[-1]

    def test_a_case_name_with_a_control_character_is_mis_shaped_and_never_printed(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1"), repo.line("c1", case="c1\x1b[31mred"))
        rc, out, err = repo.timings(capsys)
        assert rc == 0 and "\x1b" not in out + err and "red" not in out
        assert "mis-shaped 1" in out.splitlines()[-1]

    def test_a_runner_version_that_is_not_a_version_is_not_printed(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1"), repo.line("c1", version="evil\x1b[31m"))
        out = repo.timings(capsys)[1]
        assert "2.1.328" in out and "evil" not in out and "\x1b" not in out

    def test_two_corrupt_lines_are_counted_as_two(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1"), raw=["{nope", '"a string"'])
        assert "corrupt 2" in repo.timings(capsys)[1].splitlines()[-1]

    def test_the_rotated_generation_is_read_too(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1"), name=history.HISTORY_NAME)
        repo.write(repo.line("c1"), repo.line("c1"), name=history.ROTATED_NAME)
        assert repo.rows(capsys)["c1"]["decisions"] == "3"

    def test_the_provenance_line_names_the_path_the_dates_and_the_versions(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1", at="2026-11-01T00:00:00Z", version="2.1.300"),
                   repo.line("c1", at="2026-11-19T00:00:00Z", version="2.1.328"))
        first = repo.timings(capsys)[1].splitlines()[0]
        assert str(history.history_path({"PORTAGE_CACHE_DIR": str(repo.cache)})) in first
        assert "2026-11-01" in first and "2026-11-19" in first
        assert "2.1.300" in first and "2.1.328" in first


class TestClosingLine:
    def test_a_one_decision_case_is_named_as_under_two_and_an_unrun_mapped_case_as_unmeasured(
        self, repo, capsys
    ):
        repo.case("c1")
        repo.case("c2")
        repo.case("c3")
        repo.write(repo.line("c1"), repo.line("c2"), repo.line("c2"))
        closing = repo.timings(capsys)[1].splitlines()[-1]
        under, _, unmeasured = closing.partition("no counted lines")
        assert "p/c1" in under and "p/c2" not in under
        assert "p/c3" in unmeasured and "p/c1" not in unmeasured

    def test_an_empty_history_says_no_runs_are_recorded_yet(self, repo, capsys):
        repo.case("c1")
        repo.write()
        rc, out, _ = repo.timings(capsys)
        assert rc == 0 and "no runs recorded yet" in out

    def test_a_missing_history_says_no_runs_are_recorded_yet(self, repo, capsys):
        repo.case("c1")
        rc, out, _ = repo.timings(capsys)
        assert rc == 0 and "no runs recorded yet" in out


class TestListing:
    def test_flagged_cases_come_first_then_plugin_and_case(self, repo, capsys):
        for name in ("a", "b", "c"):
            repo.case(name)
        repo.case("z", plugin="a")
        repo.write(*(repo.line(n) for n in ("a", "b", "c")), repo.line("c", elapsed=LEFT),
                   repo.line("z", plugin="a"))
        _, out, _ = repo.timings(capsys)
        order = [x.split()[:2] for x in out.splitlines()[2:-1]]
        assert order == [["p", "c"], ["a", "z"], ["p", "a"], ["p", "b"]]

    def test_the_plugin_flag_limits_the_report_to_one_plugin(self, repo, capsys):
        repo.case("c1")
        repo.case("c2", plugin="other")
        repo.write(repo.line("c1"), repo.line("c2", plugin="other"))
        rc, out, _ = repo.timings(capsys, "--plugin", "other")
        assert set(table(out)) == {"c2"}
        assert "stale 0" in out.splitlines()[-1] and "p/c1" not in out

    def test_the_declared_timeout_comes_from_the_case_and_defaults_to_300(self, repo, capsys):
        repo.case("c1", prompt="---\ntimeout_seconds: 900\n---\nbody\n")
        repo.case("c2")
        repo.write(repo.line("c1"), repo.line("c2"))
        rows = repo.rows(capsys)
        assert (rows["c1"]["timeout"], rows["c2"]["timeout"]) == ("900", "300")

    def test_it_writes_nothing(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1"))
        before = sorted(p.relative_to(repo.cache.parent).as_posix() for p in repo.cache.parent.rglob("*"))
        sizes = [p.stat().st_mtime_ns for p in repo.cache.rglob("*") if p.is_file()]
        repo.timings(capsys)
        after = sorted(p.relative_to(repo.cache.parent).as_posix() for p in repo.cache.parent.rglob("*"))
        assert before == after
        assert sizes == [p.stat().st_mtime_ns for p in repo.cache.rglob("*") if p.is_file()]


class TestMalformedNumbers:
    @pytest.mark.parametrize("field,was", [("elapsed_seconds", '"elapsed_seconds": 10'),
                                           ("duration_seconds", '"duration_seconds": 10'),
                                           ("cost_usd", '"cost_usd": 0.1')])
    def test_a_400_digit_integer_in_a_numeric_field_makes_the_line_mis_shaped(
            self, repo, capsys, field, was):
        repo.case("c1")
        text = json.dumps(repo.line("c1", elapsed=10, runs=[run(10)]))
        assert was in text
        repo.write(raw=[text.replace(was, f'"{field}": ' + "9" * 400)])
        rc, out, err = repo.timings(capsys)
        assert rc == 0 and "Traceback" not in err
        assert "mis-shaped 1" in out


class TestGateToTimingsSeam:
    def test_a_case_the_gate_ran_is_counted_not_stale(self, tmp_path, monkeypatch, capsys):
        env = Env(tmp_path, monkeypatch)
        try:
            cache = tmp_path / "cache"
            monkeypatch.setenv("PORTAGE_CACHE_DIR", str(cache))
            monkeypatch.delenv("PORTAGE_EVAL_RUNNER", raising=False)
            plugin = env.repo / "plugins" / "p"
            (plugin / "evals" / "c1").mkdir(parents=True)
            (plugin / "evals" / "c1" / "case.yaml").write_text("name: x\n")
            (plugin / "skills").mkdir()
            (plugin / "skills" / "s.md").write_text("prose\n")
            (plugin / "evals" / "eval-gate.json").write_text(json.dumps(
                {"schema_version": 1, "runner": GATE_RUNNER,
                 "cases": {"c1": {"measures": ["skills"], "runs": 3, "pass_at": 2}}}))
            env.cases = [dict(env.cases[0], measures=["skills"])]
            env.plan(tier="merge")
            env.fake(pool=_result([PASS] * 12, costUsd=0.1), code=0)
            assert env.run() == 0
            rc = dispatch.main(["eval-gate", "timings", "--repo", str(env.repo)])
            out = capsys.readouterr().out
            assert rc == 0
            assert table(out)["c1"]["decisions"] == "1"
            assert "stale 0" in out
        finally:
            shutil.rmtree(env.root, ignore_errors=True)


class TestFailures:
    @pytest.mark.parametrize("name", [history.HISTORY_NAME, history.ROTATED_NAME])
    def test_a_history_generation_that_is_a_directory_exits_2_with_a_clean_message(
            self, repo, capsys, name):
        repo.case("c1")
        (repo.cache / "eval-gate" / name).mkdir(parents=True)
        rc, out, err = repo.timings(capsys)
        assert rc == 2 and err.startswith("portage: ") and "Traceback" not in err and out == ""

    def test_an_unreadable_mapping_exits_2_with_a_clean_message(self, repo, capsys):
        repo.case("c1")
        (repo.root / "plugins" / "p" / "evals" / "eval-gate.json").write_text("{nope")
        rc, out, err = repo.timings(capsys)
        assert rc == 2 and err.startswith("portage:") and "Traceback" not in err and out == ""

    def test_a_repo_that_is_not_a_directory_exits_2(self, repo, capsys):
        rc = dispatch.main(["eval-gate", "timings", "--repo", str(repo.root / "missing")])
        err = capsys.readouterr().err
        assert rc == 2 and err.startswith("portage:")

    def test_a_missing_repo_flag_is_a_usage_error(self, repo, capsys):
        with pytest.raises(SystemExit) as e:
            dispatch.main(["eval-gate", "timings"])
        assert e.value.code == 2


def _nesting_that_overflows_json() -> str:
    """JSON array nesting just past where this interpreter's parser raises RecursionError (the
    parser's own limit, not ``sys.getrecursionlimit()``, on 3.12+)."""
    depth = sys.getrecursionlimit()
    while depth < 2_000_000:
        text = "[" * depth + "]" * depth
        try:
            json.loads(text)
        except RecursionError:
            return text
        depth *= 2
    pytest.skip("json.loads does not overflow on this interpreter")


class TestHostileInputs:
    def test_a_line_nested_past_the_recursion_limit_is_corrupt(self, repo, capsys):
        repo.case("c1")
        repo.write(repo.line("c1"), raw=[_nesting_that_overflows_json()])
        rc, out, err = repo.timings(capsys)
        assert rc == 0 and "corrupt 1" in out and err == ""
        assert table(out)["c1"]["decisions"] == "1"

    @pytest.mark.parametrize("stamp,counted", [("2026-11-19T12:30:00Z", True),
                                               ("\u0662\u0660\u0662\u0666-11-19T12:30:00Z", False),
                                               ("\uff12\uff10\uff12\uff16-11-19T12:30:00Z", False)])
    def test_only_ascii_digit_stamps_count(self, repo, capsys, stamp, counted):
        repo.case("c1")
        repo.write(repo.line("c1", at=stamp))
        rc, out, _ = repo.timings(capsys)
        assert rc == 0
        assert (stamp in out) is counted
        assert ("mis-shaped 0" in out) is counted

    def test_a_deeply_nested_mapping_exits_2_with_a_clean_message(self, repo, capsys):
        repo.case("c1")
        (repo.root / "plugins" / "p" / "evals" / "eval-gate.json").write_text(
            _nesting_that_overflows_json())
        rc, out, err = repo.timings(capsys)
        assert rc == 2 and err.startswith("portage: ") and "Traceback" not in err and out == ""

    def test_an_oserror_computing_a_case_key_exits_2_with_a_clean_message(self, repo, capsys, monkeypatch):
        repo.case("c1")

        def boom(*a, **k):
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(gate_timings, "_cache_key", boom)
        rc, out, err = repo.timings(capsys)
        assert rc == 2 and err.startswith("portage: ") and "Traceback" not in err and out == ""

    def test_a_fifo_history_exits_2_without_blocking(self, repo, capsys):
        repo.case("c1")
        d = repo.cache / "eval-gate"
        d.mkdir(parents=True)
        os.mkfifo(d / history.HISTORY_NAME)
        rc, out, err = repo.timings(capsys)
        assert rc == 2 and err.startswith("portage: ") and "regular" in err and out == ""

    def test_a_history_over_the_size_cap_is_refused_and_one_at_the_cap_is_read(self, repo, capsys):
        repo.case("c1")
        d = repo.cache / "eval-gate"
        repo.write(repo.line("c1"))
        f = d / history.HISTORY_NAME
        with open(f, "r+b") as fh:
            fh.truncate(gate_timings.MAX_HISTORY_BYTES + 1)
        rc, out, err = repo.timings(capsys)
        assert rc == 2 and err.startswith("portage: ") and "too large" in err and out == ""
        with open(f, "r+b") as fh:
            fh.truncate(gate_timings.MAX_HISTORY_BYTES)
        rc, out, err = repo.timings(capsys)
        assert rc == 0
