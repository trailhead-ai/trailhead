"""`portage eval-gate cost` — what a one-file edit to each procedure file costs at the gate.

Every test builds a throwaway git repository (a plugin with skills, cases and an ``eval-gate.json``)
and a history directory under ``tmp_path``, writes runner-output files the way ``claude plugin eval``
leaves them, and drives the real ``cost.report`` (or the real ``dispatch.main`` for the seam and the
exit codes). Prices are chosen so a wrong rule gives a visibly different number.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.cli import dispatch
from portage.eval_gate import cost, history
from trailhead.tests._git_support import hermetic_git_env  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("hermetic_git_env")

MODEL = "claude-opus-5-5"
OTHER_MODEL = "claude-sonnet-4-5"
PLUGIN = "craft"
FIXTURES = Path(__file__).parent / "fixtures" / "eval_gate_cost"
SNAPSHOT = FIXTURES / "runner-output-craft.json"
SKILLS = f"plugins/{PLUGIN}/skills"
_STAMPS = itertools.count(1)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


class Fx:
    """A repository holding one plugin; ``commit`` writes its mapping and commits everything."""

    def __init__(self, tmp_path, monkeypatch):
        self.root = tmp_path / "repo"
        self.cache = tmp_path / "cache"
        self.runs = tmp_path / "runs"
        self.root.mkdir()
        self.runs.mkdir()
        monkeypatch.setenv("PORTAGE_CACHE_DIR", str(self.cache))
        _git(self.root, "init", "-b", "main")
        self.cases: dict[str, dict] = {}
        self.model = MODEL

    def write(self, rel: str, text: str = "prose\n") -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def skill(self, name: str) -> None:
        self.write(f"{SKILLS}/{name}")

    def case(self, name: str, *measures: str, merge_only: bool = False) -> None:
        spec: dict = {"measures": [f"skills/{m}" for m in measures]}
        if merge_only:
            spec["merge_only"] = True
        self.cases[name] = spec

    def commit(self) -> None:
        for name in self.cases:
            self.write(f"plugins/{PLUGIN}/evals/{name}/case.yaml", "name: x\n")
        runner = {"model": self.model, "allow_tools": ["Read"], "max_cost_usd": 2.0}
        self.write(f"plugins/{PLUGIN}/evals/eval-gate.json",
                   json.dumps({"schema_version": 1, "runner": runner, "cases": self.cases}))
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "--allow-empty", "-m", "c")

    @property
    def env(self) -> dict[str, str]:
        return {**os.environ, "PORTAGE_CACHE_DIR": str(self.cache)}

    def report(self, *dirs: Path, **kw) -> list[str]:
        return cost.report(self.root, PLUGIN, list(dirs), self.env, **kw)

    def history(self, *lines: dict) -> None:
        d = self.cache / "eval-gate"
        d.mkdir(parents=True, exist_ok=True)
        (d / history.HISTORY_NAME).write_text("".join(json.dumps(x) + "\n" for x in lines))


@pytest.fixture
def fx(tmp_path, monkeypatch):
    return Fx(tmp_path, monkeypatch)


def runner_file(directory: Path, name: str, *, case="alpha", costs=((1.0, 0.0),), model=MODEL,
                plugins=(PLUGIN,), ablation="none", starts="auto") -> Path:
    runs = []
    for i, (c, j) in enumerate(costs):
        run: dict = {"costUsd": c, "durationSeconds": 5}
        if j is not None:
            run["judgeCostUsd"] = j
        if starts == "auto":
            run["startedAt"] = f"2026-11-18T04:{next(_STAMPS) % 60:02d}:{i:02d}.000Z"
        elif starts is not None:
            run["startedAt"] = starts[i]
        runs.append(run)
    suite: dict = {"ablation": ablation, "plugins": list(plugins)}
    if model is not None:
        suite["modelOverride"] = model
    path = directory / name
    path.write_text(json.dumps({"cases": [{"arms": {"with": runs}, "name": case}], "suite": suite}))
    return path


def hline(case="alpha", cost_usd=1.0, judge=0.0, *, model="claude-opus-5-5", pinned=False,
          plugin=PLUGIN) -> dict:
    line = {"recorded_at": "2026-11-19T12:30:00Z", "plugin": plugin, "case": case, "fingerprint": None,
            "runner_version": "2.1.328", "pinned_runner": pinned, "tier": "merge", "verdict": "pass",
            "reason": None, "budget_seconds": 540, "elapsed_seconds": 10,
            "runs": [{"passed": True, "errored": False, "duration_seconds": 5,
                      "cost_usd": cost_usd, "judge_cost_usd": judge}]}
    if model != "absent":
        line["model"] = model
    return line


def last(lines: list[str]) -> str:
    return lines[-1]


def baseline(lines: list[str]) -> str:
    assert lines[-1].startswith("baseline: ")
    return lines[-1]


def section(lines: list[str], header: str) -> list[list[str]]:
    start = lines.index(header)
    rows = []
    for ln in lines[start + 1:]:
        if not ln.startswith("  "):
            break
        rows.append(ln.split())
    return [] if rows == [["none"]] else rows


def files(lines: list[str]) -> dict[str, dict[str, str]]:
    rows = section(lines, "procedure files:")
    head = rows[0] if rows else []
    return {r[0]: dict(zip(head, r)) for r in rows[1:]}


def counts(lines: list[str], prefix: str) -> dict[str, int]:
    line = next(x for x in lines if x.startswith(prefix))
    return {k: int(v) for k, v in re.findall(r"([a-z-]+) (\d+)", line)}


def priced(lines: list[str]) -> dict[str, list[str]]:
    return {r[0]: r for r in section(lines, "priced cases:")}


def two_runs(fx: Fx, case="alpha", a=1.0, b=3.0) -> None:
    runner_file(fx.runs, f"{case}-1.json", case=case, costs=((a, 0.0), (b, 0.0)))


def basic(fx: Fx) -> None:
    fx.skill("a.md")
    fx.case("alpha", "a.md")
    fx.commit()


class TestPopulation:
    def test_a_procedure_file_no_case_selects_is_listed_apart_and_leaves_the_median_alone(self, fx):
        for n in ("a.md", "b.md"):
            fx.skill(n)
        fx.case("alpha", "a.md")
        fx.case("beta", "b.md")
        fx.commit()
        two_runs(fx, "alpha", 1.0, 1.0)
        two_runs(fx, "beta", 3.0, 3.0)
        before = fx.report(fx.runs)
        assert baseline(before) == "baseline: 4.0000"
        fx.skill("c.md")
        fx.commit()
        after = fx.report(fx.runs)
        assert baseline(after) == "baseline: 4.0000"
        assert section(after, "files no case selects:") == [[f"{SKILLS}/c.md"]]
        assert f"{SKILLS}/c.md" not in files(after)
        fx.skill("d.md")
        fx.case("gamma", "d.md")
        fx.commit()
        two_runs(fx, "gamma", 10.0, 10.0)
        assert baseline(fx.report(fx.runs)) == "baseline: 6.0000"

    def test_only_regular_md_files_under_skills_are_procedure_files(self, fx):
        fx.skill("a.md")
        fx.skill("sub/deep.md")
        fx.skill("notes.txt")
        fx.write(f"plugins/{PLUGIN}/README.md")
        os.symlink("a.md", fx.root / SKILLS / "link.md")
        fx.case("alpha", "")
        fx.cases["alpha"]["measures"] = ["skills", "README.md"]
        fx.commit()
        lines = fx.report()
        listed = set(files(lines)) | {r[0] for r in section(lines, "files no case selects:")}
        assert listed == {f"{SKILLS}/a.md", f"{SKILLS}/sub/deep.md"}

    def test_a_case_of_a_corpus_nested_inside_skills_does_not_select_the_files_of_the_outer_plugin(self, fx):
        fx.skill("a.md")
        fx.case("alpha", "a.md")
        fx.write(f"{SKILLS}/inner/x.md")
        fx.write(f"{SKILLS}/inner/evals/inner-case/case.yaml", "name: x\n")
        fx.write(f"{SKILLS}/inner/evals/eval-gate.json", json.dumps(
            {"schema_version": 1, "runner": {"model": MODEL, "allow_tools": ["Read"], "max_cost_usd": 2.0},
             "cases": {"inner-case": {"measures": ["x.md"]}}}))
        fx.commit()
        lines = fx.report()
        assert f"{SKILLS}/inner/x.md" not in files(lines)
        assert f"{SKILLS}/inner/x.md" in {r[0] for r in section(lines, "files no case selects:")}
        assert section(lines, "unpriced cases:") == [["alpha", "push+merge"]]


    def test_no_file_selected_by_any_case_leaves_no_baseline(self, fx):
        fx.skill("a.md")
        fx.write(f"plugins/{PLUGIN}/README.md")
        fx.case("alpha", "")
        fx.cases["alpha"]["measures"] = ["README.md"]
        fx.commit()
        lines = fx.report()
        assert files(lines) == {}
        assert lines[-1] == "baseline: none (no procedure file is selected by a case)"


class TestTotals:
    @pytest.mark.parametrize("prices,expected", [((1.0, 2.0, 7.0), "baseline: 6.0000"),
                                                 ((1.0, 2.0, None), "baseline: 4.0000")])
    def test_files_selecting_different_case_sets_have_different_totals_and_the_baseline_is_their_median(
        self, fx, prices, expected
    ):
        for n in ("a.md", "b.md", "c.md"):
            fx.skill(n)
        fx.case("alpha", "a.md", "b.md")
        fx.case("beta", "b.md")
        fx.case("gamma", "c.md")
        fx.commit()
        for case, price in zip(("alpha", "beta", "gamma"), prices):
            if price is not None:
                two_runs(fx, case, price, price)
        lines = fx.report(fx.runs)
        if prices[2] is None:
            fx.skill("d.md")
            fx.cases.pop("gamma")
            fx.commit()
            lines = fx.report(fx.runs)
            totals = {k: v["total"] for k, v in files(lines).items()}
            assert totals == {f"{SKILLS}/a.md": "2.0000", f"{SKILLS}/b.md": "6.0000"}
        else:
            totals = {k: v["total"] for k, v in files(lines).items()}
            assert totals == {f"{SKILLS}/a.md": "2.0000", f"{SKILLS}/b.md": "6.0000",
                              f"{SKILLS}/c.md": "14.0000"}
        assert baseline(lines) == expected

    def test_a_merge_only_case_is_priced_at_merge_only_and_a_push_case_at_both_tiers(self, fx):
        fx.skill("a.md")
        fx.case("alpha", "a.md")
        fx.case("mo", "a.md", merge_only=True)
        fx.commit()
        two_runs(fx, "alpha", 1.0, 1.0)
        two_runs(fx, "mo", 4.0, 4.0)
        row = files(fx.report(fx.runs))[f"{SKILLS}/a.md"]
        assert (row["push-cases"], row["merge-cases"]) == ("1", "2")
        assert (row["push-cost"], row["merge-cost"], row["total"]) == ("1.0000", "5.0000", "6.0000")


class TestPricing:
    def test_runs_on_another_model_do_not_count_and_the_mapped_model_picks_the_runs(self, fx):
        basic(fx)
        runner_file(fx.runs, "opus.json", costs=((1.0, 0.0), (1.0, 0.0)), model=MODEL)
        runner_file(fx.runs, "other.json", costs=((9.0, 0.0), (9.0, 0.0)), model=OTHER_MODEL)
        assert priced(fx.report(fx.runs))["alpha"][2] == "1.0000"
        fx.model = OTHER_MODEL
        fx.commit()
        assert priced(fx.report(fx.runs))["alpha"][2] == "9.0000"

    def test_a_cases_price_is_the_median_of_its_runs_judge_cost_included(self, fx):
        basic(fx)
        runner_file(fx.runs, "a.json", costs=((0.4, 0.6), (0.9, 0.1), (2.0, 3.0)))
        row = priced(fx.report(fx.runs))["alpha"]
        assert (row[2], row[3]) == ("1.0000", "3")

    def test_a_case_priced_from_one_or_two_runs_is_flagged_and_three_is_not(self, fx):
        basic(fx)
        runner_file(fx.runs, "a.json", costs=((1.0, 0.0), (1.0, 0.0)))
        assert priced(fx.report(fx.runs))["alpha"][4] == "thin"
        runner_file(fx.runs, "b.json", costs=((1.0, 0.0),))
        assert priced(fx.report(fx.runs))["alpha"][4] == "-"

    def test_history_runs_price_a_case_on_the_model_the_line_recorded(self, fx):
        basic(fx)
        fx.history(hline(cost_usd=1.0, judge=0.5), hline(cost_usd=2.0, judge=1.0),
                   hline(cost_usd=40.0, model=OTHER_MODEL))
        row = priced(fx.report())["alpha"]
        assert (row[2], row[3]) == ("2.2500", "2")

    def test_a_selected_case_with_no_run_makes_the_baseline_incomplete_and_pricing_it_completes_it(self, fx):
        fx.skill("a.md")
        fx.case("alpha", "a.md")
        fx.case("mo", "a.md", merge_only=True)
        fx.case("beta", "a.md")
        fx.commit()
        two_runs(fx, "alpha", 1.0, 1.0)
        lines = fx.report(fx.runs)
        assert baseline(lines) == "baseline: incomplete (2 cases unpriced); lower bound 2.0000"
        assert section(lines, "unpriced cases:") == [["beta", "push+merge"], ["mo", "merge"]]
        assert files(lines)[f"{SKILLS}/a.md"]["unpriced"] == "2"
        two_runs(fx, "beta", 3.0, 3.0)
        two_runs(fx, "mo", 4.0, 4.0)
        assert baseline(fx.report(fx.runs)) == "baseline: 12.0000"

    def test_the_closing_figures_come_first_and_the_mean_sits_beside_the_median(self, fx):
        for n in ("a.md", "b.md", "c.md"):
            fx.skill(n)
        fx.case("alpha", "a.md")
        fx.case("beta", "b.md")
        fx.case("gamma", "c.md")
        fx.commit()
        for case, price in (("alpha", 1.0), ("beta", 2.0), ("gamma", 9.0)):
            two_runs(fx, case, price, price)
        lines = fx.report(fx.runs)
        assert lines[:3] == ["baseline: 4.0000", "mean: 8.0000 across 3 files", "unpriced: 0 cases"]
        assert lines[-1] == lines[0]

    def test_no_runs_from_and_an_empty_history_leave_every_case_unpriced_and_still_report(self, fx):
        basic(fx)
        lines = fx.report()
        assert baseline(lines) == "baseline: incomplete (1 cases unpriced); lower bound 0.0000"
        assert counts(lines, "runs:")["counted"] == 0
        assert section(lines, "priced cases:") == []


EXCLUDED = ("ablation", "multi-plugin", "other-plugin", "zero-cost", "pinned", "model-unknown", "duplicate")


class TestExclusions:
    """Good runs cost 1.0 and 3.0 (median 2.0); the excluded run would move the median."""

    def _good(self, fx, *extra_dirs):
        basic(fx)
        runner_file(fx.runs, "good.json", costs=((1.0, 0.0), (3.0, 0.0)))

    def _check(self, fx, reason, *dirs):
        lines = fx.report(fx.runs, *dirs)
        assert priced(lines)["alpha"][2] == "2.0000"
        assert counts(lines, "runs:")[reason] == 1
        assert sum(counts(lines, "runs:")[r] for r in EXCLUDED) == 1

    def test_ablation_runs_are_excluded(self, fx):
        self._good(fx)
        runner_file(fx.runs, "abl.json", costs=((50.0, 0.0),), ablation="skill")
        self._check(fx, "ablation")

    def test_multi_plugin_runs_are_excluded(self, fx):
        self._good(fx)
        runner_file(fx.runs, "multi.json", costs=((50.0, 0.0),), plugins=(PLUGIN, "other"))
        self._check(fx, "multi-plugin")

    def test_runs_of_another_plugin_are_not_counted_for_this_one(self, fx):
        self._good(fx)
        runner_file(fx.runs, "other.json", costs=((50.0, 0.0),), plugins=("other",))
        self._check(fx, "other-plugin")

    def test_zero_cost_runs_are_excluded(self, fx):
        self._good(fx)
        runner_file(fx.runs, "zero.json", costs=((0.0, 0.0),))
        self._check(fx, "zero-cost")

    def test_pinned_runner_history_lines_are_excluded(self, fx):
        self._good(fx)
        fx.history(hline(cost_usd=50.0, pinned=True))
        self._check(fx, "pinned")

    @pytest.mark.parametrize("model", [None, "absent", "../x y", 7])
    def test_history_lines_without_a_known_model_are_excluded(self, fx, model):
        self._good(fx)
        fx.history(hline(cost_usd=50.0, model=model))
        self._check(fx, "model-unknown")

    def test_history_runs_of_another_plugin_are_not_counted_for_this_one(self, fx):
        self._good(fx)
        fx.history(hline(cost_usd=50.0, plugin="other"))
        self._check(fx, "other-plugin")

    def test_a_run_seen_in_two_files_counts_once(self, fx):
        basic(fx)
        stamps = ["2026-11-18T04:01:00.000Z", "2026-11-18T04:02:00.000Z", "2026-11-18T04:03:00.000Z"]
        runner_file(fx.runs, "first.json", costs=((1.0, 0.0), (3.0, 0.0), (9.0, 0.0)), starts=stamps)
        copy = fx.runs.parent / "copy"
        copy.mkdir()
        runner_file(copy, "again.json", costs=((1.0, 0.0),), starts=stamps[:1])
        lines = fx.report(fx.runs, copy)
        assert priced(lines)["alpha"][2:4] == ["3.0000", "3"]
        assert counts(lines, "runs:")["duplicate"] == 1

    def test_the_same_stamp_with_a_different_cost_is_a_different_run(self, fx):
        basic(fx)
        stamp = ["2026-11-18T04:01:00.000Z"]
        runner_file(fx.runs, "a.json", costs=((1.0, 0.0),), starts=stamp)
        runner_file(fx.runs, "b.json", costs=((3.0, 0.0),), starts=stamp)
        lines = fx.report(fx.runs)
        assert priced(lines)["alpha"][2:4] == ["2.0000", "2"]
        assert counts(lines, "runs:")["duplicate"] == 0

    def test_a_start_time_of_the_wrong_shape_is_no_start_time(self, fx):
        basic(fx)
        for name, cost_usd in (("a.json", 1.0), ("b.json", 1.0), ("c.json", 3.0)):
            runner_file(fx.runs, name, costs=((cost_usd, 0.0),), starts=["EVIL stamp"])
        lines = fx.report(fx.runs)
        assert priced(lines)["alpha"][2:4] == ["1.0000", "3"]
        assert counts(lines, "runs:")["duplicate"] == 0

    def test_runs_with_no_start_time_are_never_deduplicated(self, fx):
        basic(fx)
        runner_file(fx.runs, "a.json", costs=((1.0, 0.0),), starts=None)
        runner_file(fx.runs, "b.json", costs=((1.0, 0.0),), starts=None)
        runner_file(fx.runs, "c.json", costs=((3.0, 0.0),), starts=None)
        lines = fx.report(fx.runs)
        assert priced(lines)["alpha"][2:4] == ["1.0000", "3"]
        assert counts(lines, "runs:")["duplicate"] == 0


class TestHostileRunnerOutputs:
    """Each hostile file carries cost 50 and a marker; the good file keeps the price at 2.0."""

    def _good(self, fx):
        basic(fx)
        runner_file(fx.runs, "good.json", costs=((1.0, 0.0), (3.0, 0.0)))

    def _check(self, fx, reason, marker="EVILMARKER", **kw):
        lines = fx.report(fx.runs, **kw)
        assert priced(lines)["alpha"][2:4] == ["2.0000", "2"]
        assert counts(lines, "runner outputs:")[reason] == 1
        assert counts(lines, "runner outputs:")["read"] == 1
        assert marker not in "\n".join(lines)

    def test_a_symlinked_file_is_skipped(self, fx):
        self._good(fx)
        elsewhere = fx.runs.parent / "elsewhere"
        elsewhere.mkdir()
        target = runner_file(elsewhere, "real.json", case="alpha-EVILMARKER", costs=((50.0, 0.0),))
        os.symlink(target, fx.runs / "link.json")
        self._check(fx, "not-regular")

    def test_an_oversized_file_is_skipped(self, fx):
        self._good(fx)
        big = runner_file(fx.runs, "big.json", costs=((50.0, 0.0),))
        big.write_text(big.read_text() + " " * (cost.MAX_FILE_BYTES + 1))
        self._check(fx, "too-large")

    def test_a_non_json_file_is_skipped(self, fx):
        self._good(fx)
        (fx.runs / "junk.json").write_text("EVILMARKER {not json")
        self._check(fx, "not-json")

    def test_a_case_name_outside_the_mapped_shape_is_skipped(self, fx):
        self._good(fx)
        runner_file(fx.runs, "evil.json", case="EVILMARKER\x1b[31m", costs=((50.0, 0.0),))
        self._check(fx, "bad-shape")

    @pytest.mark.parametrize("bad", ["-1.0", "NaN", "Infinity", "1000"])
    def test_a_negative_non_finite_or_over_bound_cost_skips_the_file(self, fx, bad):
        self._good(fx)
        runner_file(fx.runs, "bad.json", case="alpha", costs=((50.0, 0.0),)).write_text(
            '{"cases":[{"name":"alpha","arms":{"with":[{"costUsd":%s,"startedAt":"2026-11-18T04:00:00.000Z"}]}}],'
            '"suite":{"ablation":"none","modelOverride":"claude-opus-5-5","plugins":["craft"]}}' % bad)
        self._check(fx, "bad-number")

    def test_a_deeply_nested_file_is_skipped(self, fx):
        self._good(fx)
        nest = '{"EVILMARKER":' * 40 + "1" + "}" * 40
        (fx.runs / "deep.json").write_text(
            '{"cases":[{"name":"alpha","arms":{"with":[{"costUsd":50,"x":%s}]}}],'
            '"suite":{"ablation":"none","modelOverride":"claude-opus-5-5","plugins":["craft"]}}' % nest)
        self._check(fx, "too-deep")

    @pytest.mark.parametrize("depth", [200000])
    def test_a_file_nested_past_the_parsers_own_limit_is_skipped(self, fx, depth):
        self._good(fx)
        (fx.runs / "abyss.json").write_text("[" * depth + "]" * depth)
        self._check(fx, "too-deep")

    def test_files_past_the_directory_bound_are_skipped(self, fx):
        basic(fx)
        first = runner_file(fx.runs, "a-good.json", costs=((1.0, 0.0), (3.0, 0.0)))
        runner_file(fx.runs, "b-more.json", costs=((50.0, 0.0),))
        lines = fx.report(fx.runs, max_dir_bytes=first.stat().st_size + 1)
        assert priced(lines)["alpha"][2:4] == ["2.0000", "2"]
        assert counts(lines, "runner outputs:")["directory-bound"] == 1

    def test_malformed_files_are_charged_against_the_directory_bound(self, fx):
        basic(fx)
        pad = " " * 400
        for i in range(4):
            (fx.runs / f"m{i}.json").write_text("{not json" + pad)
        size = (fx.runs / "m0.json").stat().st_size
        lines = fx.report(fx.runs, max_dir_bytes=2 * size + 1)
        tally = counts(lines, "runner outputs:")
        assert (tally["not-json"], tally["directory-bound"]) == (2, 2)

    def test_a_listing_over_the_entry_cap_reports_the_truncation(self, fx):
        basic(fx)
        for i in range(5):
            runner_file(fx.runs, f"r{i}.json", costs=((1.0, 0.0),))
        lines = fx.report(fx.runs, max_entries=3)
        tally = counts(lines, "runner outputs:")
        assert (tally["read"], tally["listings-truncated"]) == (3, 1)

    @pytest.mark.parametrize("cap", [5, 6, 1000])
    def test_a_listing_within_the_entry_cap_reports_no_truncation(self, fx, cap):
        basic(fx)
        for i in range(5):
            runner_file(fx.runs, f"r{i}.json", costs=((1.0, 0.0),))
        tally = counts(fx.report(fx.runs, max_entries=cap), "runner outputs:")
        assert (tally["read"], tally["listings-truncated"]) == (5, 0)

    @pytest.mark.parametrize("model", [None, 7, "claude-opus-5-5\x1b[31m", ["claude-opus-5-5"]])
    def test_a_runner_output_without_a_model_id_is_model_unknown_and_unpriced(self, fx, model):
        basic(fx)
        runner_file(fx.runs, "m.json", costs=((50.0, 0.0),), model=model)
        lines = fx.report(fx.runs)
        assert "alpha" not in priced(lines)
        assert counts(lines, "runs:")["model-unknown"] == 1 and counts(lines, "runs:")["counted"] == 0

    @pytest.mark.parametrize("doc", ['[1, 2, 3]', '"EVILMARKER"', '{"cases": [5], "suite": {"plugins": ["craft"]}}',
                                     '{"cases": ["EVILMARKER"], "suite": {"plugins": ["craft"]}}'])
    def test_a_root_of_the_wrong_shape_is_skipped_as_bad_shape(self, fx, doc):
        self._good(fx)
        (fx.runs / "odd.json").write_text(doc)
        self._check(fx, "bad-shape")

    def test_only_json_files_directly_in_the_directory_are_read(self, fx):
        basic(fx)
        runner_file(fx.runs, "good.json", costs=((1.0, 0.0), (3.0, 0.0)))
        (fx.runs / "notes.txt").write_text("x")
        (fx.runs / "sub").mkdir()
        runner_file(fx.runs / "sub", "nested.json", costs=((50.0, 0.0),))
        lines = fx.report(fx.runs)
        assert priced(lines)["alpha"][2] == "2.0000"
        skipped = counts(lines, "runner outputs:")
        assert skipped["read"] == 1 and all(skipped[r] == 0 for r in cost.SKIP_REASONS)

    def test_a_directory_named_twice_is_read_once(self, fx):
        basic(fx)
        runner_file(fx.runs, "good.json", costs=((1.0, 0.0), (3.0, 0.0)))
        assert counts(fx.report(fx.runs, fx.runs), "runner outputs:")["read"] == 1


class TestSnapshotShape:
    def _case_name(self) -> str:
        return json.loads(SNAPSHOT.read_text())["cases"][0]["name"]

    def _repo(self, fx: Fx) -> str:
        name = self._case_name()
        fx.skill("a.md")
        fx.case(name, "a.md")
        fx.commit()
        (fx.runs / SNAPSHOT.name).write_text(SNAPSHOT.read_text())
        return name

    def test_a_runner_output_shaped_like_the_real_snapshot_prices_its_case_on_the_matching_model(self, fx):
        name = self._repo(fx)
        lines = fx.report(fx.runs)
        row = priced(lines)[name]
        assert (row[2], row[3]) == ("0.4428", "2")
        assert counts(lines, "runs:")["counted"] == 2 and counts(lines, "runs:")["matched"] == 2
        assert counts(lines, "runner outputs:")["read"] == 1

    def test_a_mapped_model_the_snapshot_does_not_name_matches_no_run(self, fx):
        name = self._repo(fx)
        fx.model = OTHER_MODEL
        fx.commit()
        lines = fx.report(fx.runs)
        assert counts(lines, "runs:")["counted"] == 2 and counts(lines, "runs:")["matched"] == 0
        assert section(lines, "unpriced cases:") == [[name, "push+merge"]]


class TestCommand:
    def test_the_command_parses_and_prints_the_table_and_the_closing_line(self, fx, capsys):
        basic(fx)
        second = fx.runs.parent / "more"
        second.mkdir()
        runner_file(fx.runs, "one.json", costs=((1.0, 0.0),))
        runner_file(second, "two.json", costs=((3.0, 0.0),))
        rc = dispatch.main(["eval-gate", "cost", "--repo", str(fx.root), "--plugin", PLUGIN,
                            "--runs-from", str(fx.runs), "--runs-from", str(second)])
        out = capsys.readouterr()
        lines = out.out.splitlines()
        assert rc == 0 and out.err == ""
        assert files(lines)[f"{SKILLS}/a.md"]["total"] == "4.0000"
        assert lines[-1] == "baseline: 4.0000"
        assert counts(lines, "runner outputs:")["read"] == 2

    @pytest.mark.parametrize("source", ["repo", "mapping", "runs-from", "history"])
    def test_an_unreadable_source_exits_two_naming_which_one_failed(self, fx, capsys, source):
        basic(fx)
        args = ["--repo", str(fx.root), "--plugin", PLUGIN]
        if source == "repo":
            args[1] = str(fx.root / "missing")
        elif source == "mapping":
            args[3] = "nosuchplugin"
        elif source == "runs-from":
            args += ["--runs-from", str(fx.runs / "missing")]
        else:
            (fx.cache / "eval-gate" / history.HISTORY_NAME).mkdir(parents=True)
        rc = dispatch.main(["eval-gate", "cost", *args])
        out = capsys.readouterr()
        assert rc == 2 and out.out == ""
        assert out.err.startswith(f"portage: eval-gate cost: {source} unreadable")
        assert len(out.err.splitlines()) == 1
        if source == "repo":
            assert "is not a directory" in out.err

    @pytest.mark.parametrize("kind", ["file", "symlink"])
    def test_a_runs_from_that_is_not_a_real_directory_is_refused(self, fx, capsys, kind):
        basic(fx)
        target = fx.runs.parent / "plain"
        if kind == "file":
            target.write_text("x")
        else:
            os.symlink(fx.runs, target)
        rc = dispatch.main(["eval-gate", "cost", "--repo", str(fx.root), "--plugin", PLUGIN,
                            "--runs-from", str(target)])
        err = capsys.readouterr().err
        assert rc == 2 and "runs-from unreadable" in err and "is not a directory" in err
