"""The shared in-process lore runner: contract against real lore, fences, failure injection."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

import _lore_runner
from _lore_runner import LoreRunner, LoreRunnerEscape, lore_conftest

lore_runner = _lore_runner.lore_runner

CLI = str(lore_conftest.CLI_PATH)

BODY = "# body\n"
DIFF = "--- a\n+++ b\n@@ -1 +1 @@\n-# new body\n+# diffed body\n"

# Each row is one lore call; later rows read what earlier rows wrote. The
# non-default vault `other` is what makes `--vault` routing observable.
TABLE = [
    ("create", ["record", "create", "--kind", "task", "--title", "Contract one",
                "--status", "open", "--vault", "other"], BODY),
    ("show-json", ["record", "show", "--json", "--vault", "other", "task/contract-one"], None),
    ("update-stdin", ["record", "update", "task/contract-one", "--vault", "other"], "# new body\n"),
    ("update-diff", ["record", "update", "task/contract-one", "--vault", "other", "--diff"], DIFF),
    ("show-after-diff", ["record", "show", "--json", "--vault", "other", "task/contract-one"], None),
    ("search", ["search", "--json", "kind:task"], None),
    ("task-graph", ["task", "graph", "contract-one", "--vault", "other"], None),
    ("vault-ls", ["vault", "ls"], None),
    ("show-not-found", ["record", "show", "--vault", "other", "task/nope"], None),
    ("bad-flag", ["record", "show", "--bogus"], None),
]

_STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
_REVISION = re.compile(r"\b[0-9a-f]{64}\b")


def _normalise(text, root):
    """Strip what legitimately differs between two worlds: the root path, the
    clock, and the content hashes derived from the clock."""
    for form in {str(root), str(root.resolve())}:
        text = text.replace(form, "<ROOT>")
    return _REVISION.sub("<REV>", _STAMP.sub("<TIME>", text))


def _real_lore(runner, argv, stdin):
    done = subprocess.run(
        [sys.executable, CLI, *argv], capture_output=True, text=True, env=runner.env(),
        input=stdin if stdin is not None else "", cwd=runner.cwd,
    )
    return done


@pytest.fixture(scope="module")
def transcripts(tmp_path_factory):
    """The table, run once through the in-process runner and once through a real
    `lore` process, each in its own identical world."""
    in_root = tmp_path_factory.mktemp("inproc")
    sub_root = tmp_path_factory.mktemp("subproc")
    in_runner = LoreRunner(in_root, vaults=("default", "other"))
    sub_runner = LoreRunner(sub_root, vaults=("default", "other"))
    out = {}
    for row_id, argv, stdin in TABLE:
        mine = in_runner(argv, stdin)
        real = _real_lore(sub_runner, argv, stdin)
        out[row_id] = (
            (mine.returncode, _normalise(mine.stdout, in_root), _normalise(mine.stderr, in_root)),
            (real.returncode, _normalise(real.stdout, sub_root), _normalise(real.stderr, sub_root)),
        )
    return out


@pytest.mark.parametrize("row_id", [row[0] for row in TABLE])
def test_the_runner_matches_a_real_lore_process(row_id, transcripts):
    mine, real = transcripts[row_id]
    assert mine == real


def test_the_table_exercises_success_and_both_error_shapes(transcripts):
    codes = {row_id: pair[1][0] for row_id, pair in transcripts.items()}
    assert codes["create"] == 0 and codes["update-diff"] == 0
    assert codes["show-not-found"] == 1
    assert codes["bad-flag"] == 2
    shown = json.loads(transcripts["show-after-diff"][1][1])
    assert shown["body"] == "# diffed body\n"


# --- fences -------------------------------------------------------------------


def test_a_publish_triggering_write_asks_for_a_spawn_that_the_runner_refuses(lore_runner):
    done = lore_runner(
        ["record", "create", "--kind", "task", "--title", "Publish me", "--status", "open"],
        "# b\n",
    )
    assert done.returncode == 0, done.stderr
    assert lore_runner.spawn_attempts
    assert all("publish" in argv for argv in lore_runner.spawn_attempts)


def test_the_call_environment_is_fenced_whatever_the_ambient_one_holds(
    lore_runner, monkeypatch, tmp_path
):
    seen = {}

    def probe(args):
        seen.update(os.environ)
        return 0

    from lore.cli import dispatch

    monkeypatch.setattr(dispatch, "main", probe)
    monkeypatch.setenv("LORE_VAULT_GUARD_ROOT", "/somewhere")
    monkeypatch.setenv("LORE_VAULT_GUARD_EXEMPT", "/elsewhere")
    monkeypatch.setenv("LORE_PUBLISH_DISABLE", "0")
    monkeypatch.setenv("LORE_GROUPS_DIR", "/real/groups")
    monkeypatch.setenv("HOME", "/real/home")
    lore_runner(["vault", "ls"])
    assert "LORE_VAULT_GUARD_ROOT" not in seen
    assert "LORE_VAULT_GUARD_EXEMPT" not in seen
    assert seen["LORE_PUBLISH_DISABLE"] == "1"
    for key in ("HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "LORE_GROUPS_DIR"):
        assert seen[key].startswith(str(tmp_path)), key


def test_a_config_pointing_outside_the_root_raises_before_lore_runs(
    tmp_path, monkeypatch
):
    runner = LoreRunner(tmp_path / "root")
    ran = []
    monkeypatch.setattr(
        lore_conftest, "_run_cli_in_process", lambda *a, **k: ran.append(a)
    )
    runner.write_config([("default", "default", tmp_path / "elsewhere")])
    with pytest.raises(LoreRunnerEscape, match="elsewhere"):
        runner(["vault", "ls"])
    assert ran == []


def test_a_vault_that_symlinks_out_of_the_root_raises(tmp_path):
    runner = LoreRunner(tmp_path / "root")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "root" / "vaults" / "sneaky"
    link.symlink_to(outside)
    runner.write_config([("default", "default", link)])
    with pytest.raises(LoreRunnerEscape):
        runner(["vault", "ls"])


def test_a_vault_inside_the_root_is_accepted(tmp_path):
    runner = LoreRunner(tmp_path / "root")
    assert runner(["vault", "ls"]).returncode == 0


def test_an_unreadable_config_raises_rather_than_being_trusted(tmp_path):
    runner = LoreRunner(tmp_path / "root")
    (runner.config_home / "lore" / "config.json").write_text("{not json")
    with pytest.raises(LoreRunnerEscape):
        runner(["vault", "ls"])


def test_two_vaults_back_to_back_in_one_process_do_not_see_each_other(tmp_path, monkeypatch):
    first = LoreRunner(tmp_path / "a", vaults=("alpha",))
    second = LoreRunner(tmp_path / "b", vaults=("beta",))
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    monkeypatch.setenv("HOME", "/sentinel-home")
    env_before = dict(os.environ)
    cwd_before = os.getcwd()

    assert first(["record", "create", "--kind", "task", "--title", "Alpha one",
                  "--status", "open"], "# a\n").returncode == 0
    listed = second(["vault", "ls"])
    assert listed.stdout.startswith("beta\t")
    assert "alpha" not in listed.stdout
    assert second(["record", "show", "task/alpha-one"]).returncode == 1
    assert second(["record", "create", "--kind", "task", "--title", "Beta one",
                   "--status", "open"], "# b\n").returncode == 0
    assert first(["record", "show", "task/beta-one"]).returncode == 1
    assert first(["record", "show", "task/alpha-one"]).returncode == 0

    assert dict(os.environ) == env_before
    assert os.getcwd() == cwd_before


# --- failure injection --------------------------------------------------------


def test_an_injected_failure_returns_the_configured_code_and_stderr(lore_runner):
    lore_runner.inject(["vault", "ls"], returncode=3, stderr="lore: boom\n")
    done = lore_runner(["vault", "ls"])
    assert (done.returncode, done.stdout, done.stderr) == (3, "", "lore: boom\n")
    assert "record not found" in lore_runner(["record", "show", "task/nope"]).stderr


def test_an_injection_can_match_on_a_predicate_and_limit_how_often_it_answers(lore_runner):
    lore_runner.inject(
        lambda argv, stdin: argv[:2] == ["record", "show"] and argv[-1] == "task/x",
        stderr="lore: record not found: task/x\n", times=1,
    )
    first = lore_runner(["record", "show", "task/x"])
    second = lore_runner(["record", "show", "task/x"])
    assert first.returncode == 1 and first.stderr == "lore: record not found: task/x\n"
    assert second.returncode == 1  # real lore: genuinely not found, not injected
    assert "record not found" in second.stderr
    lore_runner.inject(["vault", "ls"], returncode=7, times=1)
    assert lore_runner(["vault", "ls"]).returncode == 7
    assert lore_runner(["vault", "ls"]).returncode == 0


def test_an_injection_can_raise_what_a_missing_lore_would(lore_runner):
    lore_runner.inject(["vault", "ls"], raises=FileNotFoundError("lore"))
    with pytest.raises(FileNotFoundError):
        lore_runner(["vault", "ls"])


def test_a_write_then_fail_injection_leaves_the_write_in_place(lore_runner):
    lore_runner.inject(["record", "create"], stderr="lore: index failed\n", run_first=True)
    failed = lore_runner(
        ["record", "create", "--kind", "task", "--title", "Landed", "--status", "open"], "# b\n"
    )
    assert failed.returncode == 1 and failed.stderr == "lore: index failed\n"
    assert lore_runner(["record", "show", "task/landed"]).returncode == 0


def test_every_call_is_recorded_with_its_stdin(lore_runner):
    lore_runner(["vault", "ls"])
    lore_runner(["record", "show", "task/nope"], "ignored")
    assert lore_runner.calls == [(["vault", "ls"], None), (["record", "show", "task/nope"], "ignored")]
