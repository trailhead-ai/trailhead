"""The eval gate's pass cache — a case's pass is reused while nothing it depends on changed.

Every test drives ``dispatch.main(["eval-gate", "run", ...])`` against the fake runner of the
run tests, on a real plugin tree under ``tmp_path``. The cache directory is pointed under
``tmp_path`` (``PORTAGE_CACHE_DIR``, the per-app override ``trailhead.paths`` honours) so no test
reaches the real ``~/.cache``.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

import _portage_cli  # noqa: F401  (prepends the plugin root onto sys.path)
from _portage_cli import contained_tempdir  # noqa: F401  (autouse fixture)

from portage.eval_gate import cache
from test_portage_eval_gate_run import ERR, FAIL, PASS, RUNNER, Env, _result


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    e.cache = tmp_path / "cache"
    monkeypatch.setenv("PORTAGE_CACHE_DIR", str(e.cache))
    plugin = e.repo / "plugins" / "p"
    files = {
        "evals/c1/case.yaml": "name: c1\n",
        "evals/c1/fixtures/seed.txt": "seed\n",
        "evals/c2/case.yaml": "name: c2\n",
        "evals/scaffold.sh": "echo scaffold\n",
        "evals/eval-gate.json": "{}\n",
        "evals/notes/readme.md": "not a scaffold\n",
        "skills/s/SKILL.md": "measured prose\n",
        "skills/other/OTHER.md": "unrelated prose\n",
    }
    for rel, text in files.items():
        (plugin / rel).parent.mkdir(parents=True, exist_ok=True)
        (plugin / rel).write_text(text)
    e.plugin = plugin
    e.cases[0]["measures"] = ["skills/s"]
    e.plan()
    e.fake(pool=_result([PASS] * 12, costUsd=0.1), code=0)
    yield e
    shutil.rmtree(e.root, ignore_errors=True)


def _cache_files(env) -> list[Path]:
    d = env.cache / "eval-gate"
    return sorted(d.glob("*.json")) if d.is_dir() else []


def _run_twice(env, between=None):
    assert env.run() == 0
    first = env.result()
    before = len(env.case_calls)
    if between:
        between()
        env.plan()
    assert env.run() == 0
    return first, env.result(), len(env.case_calls) - before


class TestReuse:
    def test_a_first_pass_runs_the_runner_and_stores_a_file(self, env):
        assert env.run() == 0
        assert len(env.case_calls) == 1
        r = env.result()
        assert (r["verdict"], r["cached"]) == ("pass", False)
        files = _cache_files(env)
        assert len(files) == 1
        stored = json.loads(files[0].read_text())
        assert stored["verdict"] == "pass" and stored["passed"] == 2

    def test_an_unchanged_second_run_calls_no_runner_and_no_canary_and_is_cached(self, env):
        assert env.run() == 0
        calls = len(env.calls)
        assert env.run() == 0
        assert len(env.calls) == calls
        r = env.result()
        assert (r["verdict"], r["cached"], r["reason"]) == ("pass", True, None)
        assert (r["passed"], r["failed"], r["errored"]) == (2, 0, 0)
        assert len(r["runs"]) == 2 and r["runs"][0]["graders"]

    def test_a_hit_needs_no_canary_state(self, env):
        assert env.run() == 0
        (env.state / "canary.json").unlink()
        calls = len(env.calls)
        assert env.run() == 0
        assert len(env.calls) == calls and not (env.state / "canary.json").exists()

    def test_a_plan_entry_without_measures_still_caches(self, env):
        del env.cases[0]["measures"]
        env.plan()
        first, second, runner_calls = _run_twice(env)
        assert (first["cached"], second["cached"], runner_calls) == (False, True, 0)

    def test_the_cache_lives_under_the_injected_xdg_cache_home(self, env):
        env.mp.delenv("PORTAGE_CACHE_DIR")
        xdg = env.root / "x"
        env.mp.setenv("XDG_CACHE_HOME", str(xdg))
        assert env.run() == 0
        assert len(list((xdg / "portage" / "eval-gate").glob("*.json"))) == 1


def _edit(path_of):
    def go(env):
        p = path_of(env)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(p.read_text() + "changed\n" if p.exists() else "new\n")
    return go


def _runner_model(env):
    env.cases[0]["runner"] = {**RUNNER, "model": "opus"}
    env.plan()


def _pass_at(env):
    env.cases[0]["pass_at"] = 3
    env.plan()


class TestMisses:
    @pytest.mark.parametrize("change", [
        pytest.param(_edit(lambda e: e.plugin / "skills/s/SKILL.md"), id="measured-prose-file"),
        pytest.param(_edit(lambda e: e.plugin / "evals/c1/case.yaml"), id="case-directory-file"),
        pytest.param(_edit(lambda e: e.plugin / "evals/c1/fixtures/seed.txt"), id="case-subdirectory-file"),
        pytest.param(_edit(lambda e: e.plugin / "evals/scaffold.sh"), id="corpus-scaffold"),
        pytest.param(_edit(lambda e: e.plugin / "evals/c1/added.md"), id="file-added-to-the-case"),
        pytest.param(_runner_model, id="runner-model"),
        pytest.param(_pass_at, id="pass-at"),
    ])
    def test_a_change_to_an_input_calls_the_runner_again(self, env, change):
        _, second, runner_calls = _run_twice(env, lambda: change(env))
        assert runner_calls >= 1
        assert second["cached"] is False

    @pytest.mark.parametrize("change", [
        pytest.param(_edit(lambda e: e.plugin / "skills/other/OTHER.md"), id="unrelated-plugin-file"),
        pytest.param(_edit(lambda e: e.plugin / "evals/c2/case.yaml"), id="another-cases-file"),
        pytest.param(_edit(lambda e: e.plugin / "evals/eval-gate.json"), id="the-mapping-file"),
        pytest.param(_edit(lambda e: e.plugin / "evals/notes/readme.md"), id="a-subdirectory-of-evals"),
    ])
    def test_a_change_outside_the_inputs_is_still_a_hit(self, env, change):
        _, second, runner_calls = _run_twice(env, lambda: change(env))
        assert (runner_calls, second["cached"]) == (0, True)

    def test_a_measured_directory_is_part_of_the_key(self, env):
        env.cases[0]["measures"] = ["skills"]
        env.plan()
        _, second, runner_calls = _run_twice(
            env, lambda: _edit(lambda e: e.plugin / "skills/other/OTHER.md")(env))
        assert (runner_calls >= 1, second["cached"]) == (True, False)


class TestOnlyPassesAreStored:
    @pytest.mark.parametrize("runs,verdict", [([FAIL] * 12, "fail"), ([ERR] * 12, "not-run")])
    def test_a_fail_or_not_run_writes_nothing_and_the_next_run_calls_the_runner(self, env, runs, verdict):
        env.fake(pool=_result(runs, costUsd=0.1), code=1)
        _, second, runner_calls = _run_twice(env)
        assert _cache_files(env) == []
        assert runner_calls >= 1
        assert (second["verdict"], second["cached"]) == (verdict, False)


class TestRefusedOrBrokenCache:
    def _prime(self, env):
        assert env.run() == 0
        assert len(_cache_files(env)) == 1

    def test_a_group_writable_cache_directory_is_a_miss(self, env):
        self._prime(env)
        os.chmod(env.cache / "eval-gate", 0o770)
        before = len(env.case_calls)
        assert env.run() == 0
        assert len(env.case_calls) == before + 1
        assert env.result()["cached"] is False

    def test_a_symlinked_cache_directory_is_a_miss(self, env):
        self._prime(env)
        real = env.cache / "real-eval-gate"
        (env.cache / "eval-gate").rename(real)
        (env.cache / "eval-gate").symlink_to(real)
        before = len(env.case_calls)
        assert env.run() == 0
        assert len(env.case_calls) == before + 1
        assert env.result()["cached"] is False

    def test_a_refused_cache_directory_is_never_written(self, env):
        (env.cache / "eval-gate").mkdir(parents=True, mode=0o770)
        os.chmod(env.cache / "eval-gate", 0o770)
        assert env.run() == 0
        assert env.result()["verdict"] == "pass"
        assert _cache_files(env) == []

    @pytest.mark.parametrize("text", ["{not json", "[]", '{"verdict": "fail"}',
                                      '{"verdict": "fail", "passed": 0, "failed": 3, "errored": 0, "runs": []}',
                                      '{"verdict": "pass", "passed": "two", "failed": 0, "errored": 0, "runs": []}',
                                      '{"verdict": "pass", "passed": 2, "failed": 0, "errored": 0, "runs": "x"}'])
    def test_a_corrupt_cache_file_is_a_miss(self, env, text):
        self._prime(env)
        _cache_files(env)[0].write_text(text)
        before = len(env.case_calls)
        assert env.run() == 0
        assert len(env.case_calls) == before + 1
        assert env.result()["cached"] is False

    def test_a_miss_after_a_corrupt_file_repairs_it(self, env):
        self._prime(env)
        _cache_files(env)[0].write_text("{not json")
        assert env.run() == 0
        before = len(env.calls)
        assert env.run() == 0
        assert len(env.calls) == before and env.result()["cached"] is True


class TestFingerprint:
    def _fp(self, env):
        return cache.fingerprint(env.plugin, env.cases[0])

    def test_the_same_tree_gives_the_same_key_and_an_edit_gives_another(self, env):
        a = self._fp(env)
        assert a == self._fp(env)
        (env.plugin / "skills/s/SKILL.md").write_text("different\n")
        assert self._fp(env) != a

    def test_a_symlink_in_the_case_directory_is_not_followed(self, env):
        outside = env.tmp / "outside.txt"
        outside.write_text("one\n")
        (env.plugin / "evals/c1/link").symlink_to(outside)
        a = self._fp(env)
        outside.write_text("two\n")
        assert self._fp(env) == a


class TestACachedPassIsNotReusedAcrossAMovedHead:
    def test_a_moved_head_is_refused_before_the_cache_is_read(self, env):
        assert env.run() == 0
        assert len(_cache_files(env)) == 1
        calls = len(env.calls)
        env.git("commit", "-q", "--allow-empty", "-m", "later")
        assert env.run() == 0
        r = env.result()
        assert (r["verdict"], r["reason"], r["cached"]) == ("not-run", "head-moved", False)
        assert len(env.calls) == calls
