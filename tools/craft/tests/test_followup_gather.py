"""followup_gather.py: spec resolution across configured vaults, the verdict on
whether the spec's slice loop has ended, and the gathering of its follow-ups.

Every test drives the script's functions through the in-process `LoreRunner`
(real lore over a throwaway vault), injecting a failure or a reply real lore would
not give where the case needs one; the seam tests run the script as a subprocess,
exactly as a skill invokes it, against the real `lore` CLI reading a throwaway
vault. The child's environment is built from scratch (never inherited) so it
cannot reach the live install.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _lore_runner import LoreRunner

_LORE_TESTS = Path(__file__).resolve().parents[2] / "lore" / "tests"
_SCRIPT = (
    Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts" / "followup_gather.py"
)

_conftest_spec = importlib.util.spec_from_file_location(
    "lore_tests_conftest_for_followup_gather", _LORE_TESTS / "conftest.py"
)
_lore_conftest = importlib.util.module_from_spec(_conftest_spec)
sys.modules["lore_tests_conftest_for_followup_gather"] = _lore_conftest
_conftest_spec.loader.exec_module(_lore_conftest)

from lore_tests_conftest_for_followup_gather import (  # noqa: E402,F401
    _allow_real_publish_spawn,
    _isolate_ambient_env,
    _publish_spawn_guard,
)

_script_spec = importlib.util.spec_from_file_location("followup_gather", _SCRIPT)
gather = importlib.util.module_from_spec(_script_spec)
sys.modules["followup_gather"] = gather
_script_spec.loader.exec_module(gather)


def _lore(runner, *args, stdin=None):
    r = runner(list(args), stdin)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _create(env, vault, kind, title, status, related=(), labels=(), parent=None):
    args = ["record", "create", "--kind", kind, "--title", title, "--status", status,
            "--vault", vault]
    for edge in related:
        args += ["--related", edge]
    for label in labels:
        args += ["--label", label]
    if parent:
        args += ["--parent", parent]
    return _lore(env, *args, stdin="# body\n").split("/", 1)[1]


def make_spec(env, vault, title, status="ready", labels=(), related=()):
    return _create(env, vault, "spec", title, status, related, labels)


@pytest.fixture
def lore1(tmp_path):
    """A `LoreRunner` over one empty vault named `default`."""
    return LoreRunner(tmp_path)


def _is_show(record_id, vault=None):
    def match(argv, _stdin):
        return (
            argv[:3] == ["record", "show", "--json"]
            and argv[-1] == record_id
            and (vault is None or argv[argv.index("--vault") + 1] == vault)
        )

    return match


def show_reply(runner, record_id, doc, vault=None):
    """Make `record show --json` of `record_id` answer `doc` (a dict, or raw text)."""
    body = doc if isinstance(doc, str) else json.dumps(doc)
    runner.inject(_is_show(record_id, vault), returncode=0, stdout=body)


def search_reply(runner, kind, doc, returncode=0, stderr=""):
    """Make the `related-<kind>` searches answer `doc` (a dict, or raw text)."""
    body = doc if isinstance(doc, str) else json.dumps(doc)
    runner.inject(
        lambda argv, _stdin: argv[0] == "search" and argv[1].startswith(f"related-{kind}"),
        returncode=returncode, stdout=body, stderr=stderr,
    )


def run_main(runner, name="s", capsys=None):
    code = gather.main([name], runner=runner)
    out = capsys.readouterr()
    return code, out.out, out.err


def reason_of(err):
    lines = [ln for ln in err.splitlines() if ln.startswith("reason-code: ")]
    assert len(lines) == 1, err
    return lines[0].removeprefix("reason-code: ")


@pytest.mark.parametrize("name", ["has space", "a$(touch x)", "semi;colon", "", "a\nb"])
def test_a_malformed_spec_name_is_refused_before_any_lore_call(name, capsys, lore1):
    code, out, err = run_main(lore1, name, capsys)
    assert code == 2
    assert reason_of(err) == "invalid-spec-name"
    assert lore1.calls == []


@pytest.mark.parametrize("name", ["-x", "--flag", "-", "--", "-rf"])
def test_a_spec_name_starting_with_a_dash_is_refused_before_any_lore_call(name, capsys, lore1):
    code = gather.main(["--", name], runner=lore1)
    err = capsys.readouterr().err
    assert code == 2
    assert reason_of(err) == "invalid-spec-name"
    assert lore1.calls == []


@pytest.mark.parametrize("name", ["-x", "--flag"])
def test_a_dash_name_given_without_a_separator_is_refused_with_a_reason_code(name, capsys, lore1):
    code = gather.main([name], runner=lore1)
    err = capsys.readouterr().err
    assert code == 2
    assert reason_of(err) in {"invalid-spec-name", "invalid-arguments"}
    assert lore1.calls == []


@pytest.mark.parametrize("name", ["..", "../x", "a/../b", "a/..", "../../etc"])
def test_a_spec_name_with_a_dotdot_segment_is_refused_before_any_lore_call(name, capsys, lore1):
    code, out, err = run_main(lore1, name, capsys)
    assert code == 2
    assert reason_of(err) == "invalid-spec-name"
    assert lore1.calls == []


@pytest.mark.parametrize("name", ["a..b", "..a", "a/.b", "v1.2.3"])
def test_a_dotted_name_with_no_dotdot_segment_still_reaches_lore(name, capsys, lore1):
    code, out, err = run_main(lore1, name, capsys)
    assert reason_of(err) == "spec-not-found"
    assert lore1.calls != []


@pytest.mark.parametrize(
    "argv",
    [[], ["--bogus", "s"], ["s", "extra"], ["s", "--bogus"], ["a", "b", "c"]],
)
def test_bad_arguments_exit_two_with_invalid_arguments_and_never_call_lore(argv, capsys, lore1):
    code = gather.main(argv, runner=lore1)
    err = capsys.readouterr().err
    assert code == 2
    assert reason_of(err) == "invalid-arguments"
    assert lore1.calls == []


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_still_exits_zero_with_usage_and_never_calls_lore(flag, capsys, lore1):
    with pytest.raises(SystemExit) as exc:
        gather.main([flag], runner=lore1)
    out = capsys.readouterr()
    assert exc.value.code == 0
    assert "usage:" in out.out
    assert "spec_name" in out.out
    assert "reason-code" not in out.err
    assert lore1.calls == []


@pytest.mark.parametrize(
    "status,labels,entry",
    [
        ("ready", ["craft/slice-loop=complete"], "complete-marker"),
        ("ready", ["craft/slice-loop=stopped"], "stopped-marker"),
        ("complete", [], "complete-status"),
        ("complete", ["craft/slice-loop=other"], "complete-status"),
    ],
)
def test_an_ended_slice_loop_exits_zero_naming_how_it_ended(status, labels, entry, capsys, lore1):
    name = make_spec(lore1, "default", "s", status, labels)
    code, out, err = run_main(lore1, name, capsys)
    assert code == 0, err
    result = json.loads(out)
    assert result["spec"] == name
    assert result["vault"] == "default"
    assert result["entry"] == entry


@pytest.mark.parametrize(
    "labels",
    [[], ["craft/slice-loop=in-progress"], ["craft/slice-loop="], ["unrelated=complete"]],
)
def test_a_ready_spec_without_an_ended_marker_is_refused(labels, capsys, lore1):
    name = make_spec(lore1, "default", "s", "ready", labels)
    code, out, err = run_main(lore1, name, capsys)
    assert code == 1
    assert out == ""
    assert reason_of(err) == "slice-loop-not-ended"


def test_a_spec_in_no_vault_is_not_found(capsys, two_vault_lore_env):
    runner, _ = two_vault_lore_env
    code, _, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == "spec-not-found"


def test_a_spec_in_two_vaults_is_ambiguous(capsys, two_vault_lore_env):
    runner, (first, second) = two_vault_lore_env
    make_spec(runner, first, "s", "complete")
    make_spec(runner, second, "s", "complete")
    code, _, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == "spec-ambiguous"


def test_a_record_that_is_not_a_spec_is_refused(capsys, lore1):
    make_spec(lore1, "default", "s", "complete")
    show_reply(lore1, "spec/s", {"record_id": "spec/s", "kind": "task",
                                 "sidecar": {"kind": "task", "status": "complete"}, "body": "# s"})
    code, _, err = run_main(lore1, "s", capsys)
    assert code == 2
    assert reason_of(err) == "not-a-spec"


def test_resolve_spec_returns_the_vault_and_sidecar(two_vault_lore_env):
    runner, (_, second) = two_vault_lore_env
    name = make_spec(runner, second, "s", "ready", ["craft/slice-loop=stopped"])
    shown = json.loads(_lore(runner, "record", "show", "--json", "--vault", second, f"spec/{name}"))
    vault, sidecar = gather.resolve_spec(runner, name)
    assert vault == second
    assert sidecar == shown["sidecar"]


def test_every_lore_call_is_an_argv_list_of_strings(two_vault_lore_env):
    runner, (_, second) = two_vault_lore_env
    make_spec(runner, second, "s", "complete")
    runner.calls.clear()
    gather.main(["s"], runner=runner)
    assert runner.calls
    for argv, _stdin in runner.calls:
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)


def _vault_ls(argv, _stdin):
    return argv[:2] == ["vault", "ls"]


def _anything_but_vault_ls(argv, _stdin):
    return argv[:2] != ["vault", "ls"]


@pytest.mark.parametrize(
    "inject_kwargs,reason,on_listing",
    [
        ({"raises": FileNotFoundError("lore")}, "lore-missing", False),
        ({"raises": subprocess.TimeoutExpired(["lore"], 1)}, "lore-timeout", False),
        ({"returncode": 3, "stderr": "SECRET-vault-text"}, "lore-failed", False),
        ({"returncode": 0, "stdout": "not json SECRET-vault-text"}, "lore-output-not-json", False),
        ({"returncode": 0, "stdout": "{}"}, "empty-sidecar", False),
        ({"returncode": 0, "stdout": json.dumps({"kind": "spec", "sidecar": {}})}, "empty-sidecar", False),
        ({"returncode": 0, "stdout": "[1, 2]"}, "lore-output-not-json", False),
        ({"raises": FileNotFoundError("lore")}, "lore-missing", True),
        ({"raises": subprocess.TimeoutExpired(["lore"], 1)}, "lore-timeout", True),
        ({"returncode": 3, "stderr": "SECRET-vault-text"}, "lore-failed", True),
    ],
)
def test_each_runner_failure_has_its_own_reason_code_and_echoes_nothing_from_lore(
    inject_kwargs, reason, on_listing, capsys, lore1
):
    lore1.inject(_vault_ls if on_listing else _anything_but_vault_ls, **inject_kwargs)
    code, out, err = run_main(lore1, "s", capsys)
    assert code == 2
    assert reason_of(err) == reason
    assert "SECRET" not in out + err
    assert "Traceback" not in err


def test_a_vault_listing_with_no_vaults_cannot_certify(capsys, lore1):
    lore1.inject(["vault", "ls"], returncode=0, stdout="")
    code, _, err = run_main(lore1, "s", capsys)
    assert code == 2
    assert reason_of(err) == "no-vaults"


def test_a_config_declaring_no_vault_fails_the_listing(capsys, tmp_path):
    runner = LoreRunner(tmp_path, vaults=())
    code, out, err = run_main(runner, "s", capsys)
    assert (code, reason_of(err), out) == (2, "lore-failed", "")


def test_a_vault_listing_that_prints_only_a_message_has_no_vaults(capsys, lore1):
    (lore1.config_home / "lore" / "config.json").unlink()
    code, out, err = run_main(lore1, "s", capsys)
    assert (code, reason_of(err), out) == (2, "no-vaults", "")
    assert all(argv[:2] == ["vault", "ls"] for argv, _stdin in lore1.calls)


@pytest.mark.parametrize("bad", ["has space", "a$(touch x)", "-x"])
def test_a_vault_with_a_hostile_name_is_never_queried_or_emitted(bad, capsys, lore1):
    elsewhere = lore1.root / "vaults" / "bad"
    elsewhere.mkdir()
    lore1.write_config([("v", "default", lore1.vault_path("default")), (bad, "team", elsewhere)])
    make_spec(lore1, "v", "s", "complete")
    lore1.calls.clear()
    code, out, err = run_main(lore1, "s", capsys)
    assert code == 0, err
    assert json.loads(out)["vault"] == "v"
    assert bad not in out
    for argv, _stdin in lore1.calls:
        assert not any(bad in part for part in argv), argv


def test_a_vault_listing_naming_a_path_traversal_vault_never_queries_it(capsys, lore1):
    """Real lore refuses a config holding such a name, so the listing is injected."""
    make_spec(lore1, "default", "s", "complete")
    listing = (f"a/../b\tteam\t{lore1.root}/bad\t[(all kinds)]\n"
               f"default\tdefault\t{lore1.vault_path('default')}\t[(all kinds)]\n")
    lore1.inject(["vault", "ls"], returncode=0, stdout=listing)
    lore1.calls.clear()
    code, out, err = run_main(lore1, "s", capsys)
    assert code == 0, err
    assert "a/../b" not in out
    for argv, _stdin in lore1.calls:
        assert not any("a/../b" in part for part in argv), argv


@pytest.mark.parametrize("sidecar", [{"kind": "spec"}, {"status": "ready"}])
def test_a_sidecar_missing_kind_or_status_is_a_failed_lookup(sidecar, capsys, lore1):
    make_spec(lore1, "default", "s", "complete")
    show_reply(lore1, "spec/s", {"record_id": "spec/s", "kind": "spec", "sidecar": sidecar})
    code, _, err = run_main(lore1, "s", capsys)
    assert code == 2
    assert reason_of(err) == "malformed-sidecar"


def test_an_unexpected_exception_exits_two_not_the_refusal_code(capsys, lore1):
    lore1.inject([], raises=RuntimeError("boom SECRET-vault-text"))
    code, out, err = run_main(lore1, "s", capsys)
    assert code == 2
    assert reason_of(err) == "internal-error"
    assert "SECRET" not in out + err
    assert "Traceback" not in err


# --- the real lore CLI, in-process ------------------------------------------------
# `lore_env` is a `LoreRunner` (tools/craft/tests/_lore_runner.py): real lore run
# inside this interpreter against a throwaway vault. Tests pass it as the scripts'
# `runner=`. The seam tests (named `..._seam`) run the script as a real subprocess
# through `seam_env` instead.


@pytest.fixture
def lore_env(tmp_path):
    """(runner, vault_name): in-process lore seeing exactly one throwaway vault."""
    return LoreRunner(tmp_path), "default"


@pytest.fixture
def two_vault_lore_env(tmp_path):
    return LoreRunner(tmp_path, vaults=("default", "other")), ("default", "other")


def seam_env(runner, tmp_path):
    """The runner's fenced environment, with a `lore` shim running this checkout's
    lore first on PATH: what a real subprocess (the script, or `lore`) is given."""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "lore"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_lore_conftest.CLI_PATH}" "$@"\n')
    shim.chmod(0o755)
    env = runner.env()
    env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
    return env


def run_in_process(main, argv, runner, **kwargs):
    """Call a script's `main(argv, runner=runner)` and return what a subprocess would."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv], runner=runner, **kwargs)
    return subprocess.CompletedProcess(list(argv), code, out.getvalue(), err.getvalue())


def run_script(runner, *args):
    return run_in_process(gather.main, args, runner)


def run_script_subprocess(env, *args):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args], capture_output=True, text=True, env=env
    )


def test_the_script_run_as_a_subprocess_certifies_a_stopped_spec(  # inert-gate: allow seam smoke
    lore_env, tmp_path
):
    """Seam: the script's CLI as a subprocess against real lore, exit 0 and JSON on stdout."""
    env, vault = lore_env
    name = make_spec(env, vault, "Seam stopped", labels=["craft/slice-loop=stopped"])
    r = run_script_subprocess(seam_env(env, tmp_path), name)
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["spec"] == name
    assert result["vault"] == vault
    assert result["entry"] == "stopped-marker"


def test_a_ready_spec_with_no_marker_is_refused_through_real_lore(lore_env, tmp_path):
    """Seam: a refusal through the script's CLI as a subprocess exits 1 with a reason-code."""
    env, vault = lore_env
    name = make_spec(env, vault, "Seam ready")
    r = run_script_subprocess(seam_env(env, tmp_path), name)
    assert r.returncode == 1
    assert "reason-code: slice-loop-not-ended" in r.stderr


def test_an_unknown_spec_is_not_found_through_real_lore(lore_env, tmp_path):
    """Seam: an error through the script's CLI as a subprocess exits 2 with a reason-code."""
    env, _ = lore_env
    r = run_script_subprocess(seam_env(env, tmp_path), "no-such-spec")
    assert r.returncode == 2
    assert "reason-code: spec-not-found" in r.stderr


def test_a_completed_spec_is_certified_by_status_through_real_lore(lore_env):
    env, vault = lore_env
    name = make_spec(env, vault, "Seam complete", status="complete")
    r = run_script(env, name)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["entry"] == "complete-status"


def test_a_spec_named_in_two_configured_vaults_is_ambiguous_through_real_lore(
    two_vault_lore_env,
):
    env, (first, second) = two_vault_lore_env
    name = make_spec(env, first, "Seam twin", status="complete")
    make_spec(env, second, "Seam twin", status="complete")
    r = run_script(env, name)
    assert r.returncode == 2
    assert "reason-code: spec-ambiguous" in r.stderr


# --- gathering: real lore CLI ---------------------------------------------------

SLICE_LABEL = "craft/slice-parent="
FOLLOWUP_PARENT_LABEL = "craft/followup-parent="


def make_task(env, vault, title, status="open", related=(), labels=(), parent=None):
    return _create(env, vault, "task", title, status, related, labels, parent)


def relate(env, record_id, edge):
    _lore(env, "record", "update", record_id, "--related", edge)


class Board:
    """A completed spec with one labelled slice that has one finished child."""

    def __init__(self, env, vault):
        self.env, self.vault = env, vault
        self.spec = make_spec(env, vault, "Board spec", status="complete")
        self.slice = make_task(
            env, vault, "Board slice", status="in-progress",
            related=[f"spec={self.spec}"], labels=[SLICE_LABEL],
        )
        self.child = make_task(env, vault, "Board child", status="done", parent=self.slice)

    def task(self, title, **kw):
        return make_task(self.env, self.vault, title, **kw)

    def gather(self):
        r = run_script(self.env, self.spec)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def names(self):
        return [f["name"] for f in self.gather()["followups"]]


@pytest.fixture
def board(lore_env):
    return Board(*lore_env)


def _linked_by_bare_slice(b):
    return b.task("F bare slice", related=[f"task={b.slice}"])


def _linked_by_qualified_slice(b):
    return b.task("F qualified slice", related=[f"task=task/{b.slice}"])


def _linked_by_spec(b):
    return b.task("F spec", related=[f"spec={b.spec}"])


def _linked_by_child(b):
    return b.task("F child", related=[f"task={b.child}"])


def _linked_by_qualified_spec(b):
    return b.task("F qualified spec", related=[f"spec=spec/{b.spec}"])


def _linked_by_qualified_child(b):
    return b.task("F qualified child", related=[f"task=task/{b.child}"])


def _linked_by_spec_outbound(b):
    name = b.task("F spec outbound")
    relate(b.env, f"spec/{b.spec}", f"task={name}")
    return name


def _linked_by_slice_outbound(b):
    name = b.task("F slice outbound")
    relate(b.env, f"task/{b.slice}", f"task={name}")
    return name


def _linked_by_child_outbound(b):
    name = b.task("F child outbound")
    relate(b.env, f"task/{b.child}", f"task={name}")
    return name


@pytest.mark.parametrize(
    "link",
    [
        _linked_by_bare_slice,
        _linked_by_qualified_slice,
        _linked_by_spec,
        _linked_by_child,
        _linked_by_qualified_spec,
        _linked_by_qualified_child,
        _linked_by_spec_outbound,
        _linked_by_slice_outbound,
        _linked_by_child_outbound,
    ],
)
def test_a_task_linked_to_the_spec_a_slice_or_a_child_is_gathered(board, link):
    name = link(board)
    assert board.names() == [name]


def test_a_task_linked_to_nothing_in_the_spec_is_not_gathered(board):
    board.task("Stranger")
    assert board.names() == []


@pytest.mark.parametrize("status", ["open", "ready", "blocked"])
def test_open_ready_and_blocked_follow_ups_are_gathered_with_their_status(board, status):
    name = board.task("F status", status=status, related=[f"task={board.slice}"])
    assert board.gather()["followups"] == [
        {"name": name, "status": status, "title": "F status"}
    ]


@pytest.mark.parametrize("status", ["in-progress", "done", "dropped", "superseded"])
def test_settled_or_started_follow_ups_are_not_gathered(board, status):
    made = board.task(
        "F status", status="open" if status == "superseded" else status,
        related=[f"task={board.slice}"],
    )
    if status == "superseded":
        successor = board.task("Successor")
        _lore(board.env, "record", "supersede", f"task/{made}", "--by", f"task/{successor}")
    assert board.names() == []


def test_a_nested_task_is_not_a_follow_up(board):
    parent = board.task("Some parent")
    board.task("Nested", related=[f"task={board.slice}"], parent=parent)
    assert board.names() == []


def test_the_slice_parent_itself_is_not_a_follow_up(lore_env):
    env, vault = lore_env
    b = Board(env, vault)
    _lore(env, "record", "update", f"task/{b.slice}", "--status", "open")
    result = b.gather()
    assert result["followups"] == []
    assert result["excluded-by-label"] == {"count": 0, "names": []}
    assert result["candidates-read"] == 0


def test_a_task_labelled_as_a_follow_up_parent_is_excluded_and_counted(board):
    marked = board.task(
        "Marked", related=[f"task={board.slice}"], labels=[FOLLOWUP_PARENT_LABEL]
    )
    kept = board.task("Plain", related=[f"task={board.slice}"])
    result = board.gather()
    assert [f["name"] for f in result["followups"]] == [kept]
    assert result["excluded-by-label"] == {"count": 1, "names": [marked]}


def test_a_task_linked_only_to_another_specs_slice_is_not_gathered(board):
    other_spec = make_spec(board.env, board.vault, "Other spec", status="complete")
    other_slice = make_task(
        board.env, board.vault, "Other slice", status="in-progress",
        related=[f"spec={other_spec}"], labels=[SLICE_LABEL],
    )
    board.task("Other follow-up", related=[f"task={other_slice}"])
    assert board.names() == []


def test_a_spec_with_no_labelled_slice_uses_its_linked_parent_tasks(lore_env):
    env, vault = lore_env
    spec = make_spec(env, vault, "Fallback spec", status="complete")
    parent = make_task(env, vault, "Plain parent", status="in-progress", related=[f"spec={spec}"])
    child = make_task(env, vault, "Plain child", status="done", parent=parent)
    follow = make_task(env, vault, "Child follow-up", related=[f"task={child}"])
    r = run_script(env, spec)
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert [f["name"] for f in result["followups"]] == [follow]
    assert result["slice-basis"] == "linked-parents"
    assert result["slices"] == [parent]
    assert result["child-count"] == 1


def test_the_fallback_does_not_treat_a_follow_up_parent_as_a_slice(lore_env):
    env, vault = lore_env
    spec = make_spec(env, vault, "Fallback spec", status="complete")
    marked = make_task(
        env, vault, "Marked parent", status="open",
        related=[f"spec={spec}"], labels=[FOLLOWUP_PARENT_LABEL],
    )
    child = make_task(env, vault, "Marked child", status="done", parent=marked)
    make_task(env, vault, "Marked child follow-up", related=[f"task={child}"])
    r = run_script(env, spec)
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["slices"] == []
    assert result["followups"] == []


def test_the_labelled_slice_basis_is_reported_with_slices_and_counts(board):
    board.task("F one", related=[f"task={board.slice}"])
    board.task("F two", related=[f"spec={board.spec}"])
    result = board.gather()
    assert result["slice-basis"] == "labelled"
    assert result["slices"] == [board.slice]
    assert result["child-count"] == 1
    assert result["kept"] == 2
    assert result["candidates-read"] == 2
    assert result["excluded-by-label"] == {"count": 0, "names": []}
    assert result["skipped-edges"] == 0


def test_the_followups_are_sorted_by_name(board):
    for title in ["Zulu", "Alpha", "Mike"]:
        board.task(title, related=[f"task={board.slice}"])
    assert board.names() == ["alpha", "mike", "zulu"]


def test_a_spec_with_twenty_five_follow_ups_gathers_all_of_them(board):
    made = {board.task(f"Bulk {i:02d}", related=[f"task={board.slice}"]) for i in range(25)}
    assert set(board.names()) == made


def test_a_follow_up_created_just_before_the_run_is_gathered(board):
    first = board.task("First", related=[f"task={board.slice}"])
    assert board.names() == [first]
    second = board.task("Second", related=[f"task={board.slice}"])
    assert board.names() == sorted([first, second])


def test_a_spec_with_no_follow_ups_exits_zero_with_an_empty_list(board):
    result = board.gather()
    assert result["followups"] == []
    assert result["kept"] == 0


def test_hostile_edge_values_on_the_spec_are_skipped_counted_and_never_reach_lore(board):
    good = board.task("Good", related=[f"task={board.slice}"])
    hostile = ['x") OR kind:task ("', "-flag", "a/../b", "task/-x"]
    for value in hostile:
        relate(board.env, f"spec/{board.spec}", f"task={value}")
    board.env.calls.clear()
    result = board.gather()
    assert [f["name"] for f in result["followups"]] == [good]
    assert result["skipped-edges"] == len(hostile)
    for argv, _stdin in board.env.calls:
        assert not any(bad in part for part in argv for bad in hostile), argv


def test_follow_ups_past_the_single_query_ceiling_are_all_gathered(board):
    children = [board.child] + [
        board.task(f"Kid {i:02d}", status="done", parent=board.slice) for i in range(40)
    ]
    first = board.task("Linked to first", related=[f"task={children[0]}"])
    last = board.task("Linked to last", related=[f"task={children[-1]}"])
    board.env.calls.clear()
    result = board.gather()
    assert result["child-count"] == 41
    assert [f["name"] for f in result["followups"]] == sorted([first, last])
    task_queries = [argv[1] for argv, _stdin in board.env.calls
                    if argv[0] == "search" and argv[1].startswith("related-task")]
    assert len(task_queries) > 1
    assert all(q.count('"') // 2 <= gather.QUERY_BATCH_SIZE for q in task_queries)
    seen = {v for q in task_queries for v in re.findall(r'"([^"]+)"', q)}
    assert {f"task/{k}" for k in children} <= seen and set(children) <= seen


# --- gathering: lore answers the real CLI would not give -------------------------


def hit(runner, name, kind="task", vault_dir=None):
    vault_dir = vault_dir or str(runner.vault_path("default"))
    return {
        "id": f"{vault_dir}/{kind}/{name}", "title": name, "kind": kind,
        "status": "open", "shared": 0, "vault": vault_dir, "snippet": "",
    }


def search_body(*hits, truncated=False):
    return json.dumps({
        "hits": list(hits), "showing": len(hits), "offset": 0, "total": len(hits),
        "stale": False, "config_stale": False, "truncated": truncated,
        "reverse_edge_alias": True,
    })


def entry(name, status="open", parent=None, children=()):
    return {"name": name, "status": status, "parent": parent, "children": list(children)}


def retitled(board, name, title):
    """Make `record show` of task `name` answer its real record with `title` in the sidecar."""
    shown = json.loads(_lore(board.env, "record", "show", "--json", "--vault", board.vault,
                             f"task/{name}"))
    shown["sidecar"]["title"] = title
    show_reply(board.env, f"task/{name}", shown)


def followup(board, title="Follow"):
    return board.task(title, related=[f"task={board.slice}"])


def run_board(board, capsys):
    code, out, err = run_main(board.env, board.spec, capsys)
    return code, (json.loads(out) if out.strip() else None), out, err


def test_a_search_hit_whose_own_edges_and_every_outbound_edge_omit_it_is_not_gathered(board):
    stray = board.task("Follow", related=["task=elsewhere"])
    search_reply(board.env, "task", search_body(hit(board.env, stray)))
    result = board.gather()
    assert result["followups"] == []
    assert (result["candidates-read"], result["kept"]) == (1, 0)


def test_a_truncated_search_exits_two_search_truncated_with_no_output(board, capsys):
    search_reply(board.env, "spec", search_body(hit(board.env, board.slice), truncated=True))
    code, _, out, err = run_board(board, capsys)
    assert code == 2
    assert reason_of(err) == "search-truncated"
    assert out == ""


def test_a_failing_search_exits_two_lore_failed_with_no_output_or_vault_text(board, capsys):
    search_reply(board.env, "spec", "", returncode=1, stderr="boom vault text")
    code, _, out, err = run_board(board, capsys)
    assert code == 2
    assert reason_of(err) == "lore-failed"
    assert out == ""
    assert "boom" not in err


def test_a_search_that_is_not_json_exits_two(board, capsys):
    search_reply(board.env, "spec", "not json")
    code, _, out, err = run_board(board, capsys)
    assert (code, reason_of(err), out) == (2, "lore-output-not-json", "")


def test_a_candidate_whose_read_fails_exits_two_with_no_partial_set(board, capsys):
    kept = followup(board, "Kept")
    search_reply(board.env, "task", search_body(hit(board.env, kept), hit(board.env, "vanished")))
    code, _, out, err = run_board(board, capsys)
    assert (code, reason_of(err), out) == (2, "lore-failed", "")


def test_a_candidate_with_an_empty_sidecar_exits_two_with_no_partial_set(board, capsys):
    name = followup(board)
    show_reply(board.env, f"task/{name}", {"record_id": f"task/{name}", "sidecar": {}, "body": ""})
    code, _, out, err = run_board(board, capsys)
    assert (code, reason_of(err), out) == (2, "empty-sidecar", "")


def test_a_candidate_sidecar_missing_status_exits_two_with_no_partial_set(board, capsys):
    name = followup(board)
    show_reply(board.env, f"task/{name}", {"sidecar": {"kind": "task", "title": "t"}})
    code, _, out, err = run_board(board, capsys)
    assert (code, reason_of(err), out) == (2, "malformed-sidecar", "")


def test_a_failing_task_listing_exits_two(board, capsys):
    board.env.inject(["task", "list"], returncode=1, stderr="x")
    code, _, out, err = run_board(board, capsys)
    assert (code, reason_of(err), out) == (2, "lore-failed", "")


def test_a_hit_with_a_hostile_record_name_is_skipped_and_counted(board):
    name = followup(board)
    search_reply(board.env, "task",
                 search_body(hit(board.env, name), hit(board.env, "a b"), hit(board.env, "-x")))
    result = board.gather()
    assert result["skipped-edges"] == 2
    assert [f["name"] for f in result["followups"]] == [name]


def test_hits_from_another_vault_or_kind_are_ignored(board):
    name = followup(board)
    search_reply(board.env, "task", search_body(
        hit(board.env, name),
        hit(board.env, "other", vault_dir="/x/elsewhere"),
        hit(board.env, board.spec, kind="spec"),
    ))
    result = board.gather()
    assert [f["name"] for f in result["followups"]] == [name]
    assert result["candidates-read"] == 1


def test_a_listed_task_with_a_hostile_name_is_skipped_and_never_queried_or_read(board):
    listing = json.loads(_lore(board.env, "task", "list", "--vault", board.vault, "--json"))
    listing += [entry("bad name", parent=board.slice), entry("-x", parent=board.slice)]
    board.env.inject(["task", "list"], returncode=0, stdout=json.dumps(listing))
    board.env.calls.clear()
    result = board.gather()
    assert result["skipped-edges"] == 2
    assert result["child-count"] == 1
    for argv, _stdin in board.env.calls:
        assert not any(bad in part for part in argv for bad in ("bad name", "-x")), argv


def test_a_dangling_edge_is_skipped_and_counted(board):
    name = board.task("Follow")
    relate(board.env, f"spec/{board.spec}", "task=ghost")
    relate(board.env, f"spec/{board.spec}", f"task={name}")
    result = board.gather()
    assert result["skipped-edges"] == 1
    assert [f["name"] for f in result["followups"]] == [name]


def test_a_title_is_emitted_without_control_characters(board):
    name = followup(board)
    retitled(board, name, "Red\x1b[31m alert\x07\x85 done\n")
    assert board.gather()["followups"][0]["title"] == "Red[31m alert done"


def test_a_title_carrying_a_credential_is_emitted_redacted(board):
    name = followup(board)
    retitled(board, name, "Rotate AKIAIOSFODNN7EXAMPLE now")
    title = board.gather()["followups"][0]["title"]
    assert "AKIAIOSFODNN7EXAMPLE" not in title
    assert title.startswith("Rotate ") and title.endswith(" now")


def test_a_title_with_nothing_to_strip_is_emitted_unchanged(board):
    followup(board, "Plain follow-up title")
    assert board.gather()["followups"][0]["title"] == "Plain follow-up title"


def test_a_hostile_oversized_title_is_bounded_and_sanitized_quickly():
    import time

    start = time.monotonic()
    out = gather.sanitize_title("token" * 40000)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"sanitize took {elapsed:.2f}s"
    assert len(out) <= gather.MAX_TITLE_CHARS + 1
    assert out.endswith("\u2026")


def test_a_title_within_the_bound_keeps_its_length_and_has_no_marker():
    title = ("ab " * gather.MAX_TITLE_CHARS)[: gather.MAX_TITLE_CHARS]
    assert gather.sanitize_title(title) == title


def test_a_title_one_past_the_bound_is_truncated_with_a_marker():
    title = ("ab " * gather.MAX_TITLE_CHARS)[: gather.MAX_TITLE_CHARS + 1]
    assert gather.sanitize_title(title) == title[: gather.MAX_TITLE_CHARS] + "\u2026"


@pytest.mark.parametrize("before_cut", [4, 8, 12, 16])
def test_a_credential_straddling_the_bound_leaves_no_part_of_itself_behind(before_cut):
    token = "AKIAIOSFODNN7EXAMPLE"
    lead = ("word " * gather.MAX_TITLE_CHARS)[: gather.MAX_TITLE_CHARS - before_cut - 1] + " "
    out = gather.sanitize_title(lead + token + " tail")
    assert token[:before_cut] not in out
    assert out.startswith(lead) and out.endswith("\u2026")


def _entropy_run(seed: int, n: int) -> str:
    import base64
    import random

    raw = base64.b64encode(random.Random(seed).randbytes(n)).decode()
    return raw.replace("/", "Q").replace("+", "Z").rstrip("=")[:n]


@pytest.mark.parametrize("lead_len", [480, 4570])
def test_earlier_redactions_never_pull_a_credential_fragment_from_past_the_bound(lead_len):
    token = _entropy_run(2, 60)
    out = gather.sanitize_text(_entropy_run(1, lead_len) + " " + token + " tail", 500)
    fragments = {token[i : i + 12] for i in range(len(token) - 11)}
    assert not any(f in out for f in fragments), out
    assert "tail" not in out
    assert out.startswith("[REDACTED]") and out.endswith("\u2026")


@pytest.mark.parametrize(
    "ch", ["\u200b", "\u202e", "\u2028", "\u2029", "\U000e0041", "\u200d", "\ufeff"]
)
def test_invisible_and_format_characters_are_stripped_from_a_title(ch):
    assert gather.sanitize_title(f"ab{ch}cd") == "abcd"


def test_a_credential_split_by_a_zero_width_character_is_redacted():
    token = "AKIA" + "\u200b" + "IOSFODNN7" + "EXAMPLE"
    out = gather.sanitize_title(f"Rotate {token} now")
    assert "IOSFODNN7EXAMPLE" not in out
    assert out.startswith("Rotate ") and out.endswith(" now")


def test_ordinary_non_ascii_letters_survive_a_title():
    title = "caf\u00e9 \u00fcber \u6f22\u5b57"
    assert gather.sanitize_title(title) == title


def test_a_title_scanner_that_cannot_be_imported_exits_two_internal_error(
    board, capsys, monkeypatch
):
    followup(board)
    monkeypatch.setitem(sys.modules, "capture_scan", None)
    code, _, out, err = run_board(board, capsys)
    assert (code, reason_of(err), out) == (2, "internal-error", "")


def test_every_reason_code_the_code_can_emit_is_in_the_collection():
    import ast

    tree = ast.parse(_SCRIPT.read_text())
    emitted = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in {"GatherError", "_refuse"}:
            emitted |= {a.value for a in node.args
                        if isinstance(a, ast.Constant) and isinstance(a.value, str)
                        and re.fullmatch(r"[a-z]+(-[a-z]+)*", a.value)}
    assert emitted == set(gather.REASON_CODES)


def test_an_unlisted_reason_code_cannot_be_raised():
    with pytest.raises(AssertionError):
        gather.GatherError("made-up-code")


# --- distilled flag and live follow-up parents ---------------------------------


def _annotate(env, record_id, value):
    _lore(env, "record", "update", record_id, "--annotation", f"distilled={value}", stdin="")


@pytest.mark.parametrize("value", ["adr", "zero-adr", "rejected"])
def test_a_spec_with_a_distilled_annotation_reports_distilled_true(board, value):
    _annotate(board.env, f"spec/{board.spec}", value)
    assert board.gather()["distilled"] is True


def test_a_spec_with_no_distilled_annotation_reports_distilled_false(board):
    assert board.gather()["distilled"] is False


@pytest.mark.parametrize("status", ["open", "ready", "in-progress", "blocked"])
def test_a_live_follow_up_parent_linked_to_the_spec_is_listed(board, status):
    parent = board.task(
        "Live parent", status=status,
        related=[f"spec={board.spec}"], labels=[FOLLOWUP_PARENT_LABEL],
    )
    assert board.gather()["followup-parents"] == [parent]


@pytest.mark.parametrize("status", ["done", "dropped", "superseded"])
def test_a_settled_follow_up_parent_is_not_listed(board, status):
    settled = board.task(
        "Settled parent", status="open" if status == "superseded" else status,
        related=[f"spec={board.spec}"], labels=[FOLLOWUP_PARENT_LABEL],
    )
    if status == "superseded":
        successor = board.task("Successor")
        _lore(board.env, "record", "supersede", f"task/{settled}", "--by", f"task/{successor}")
    assert board.gather()["followup-parents"] == []


def test_a_follow_up_parent_linked_to_a_different_spec_is_not_listed(board):
    other = make_spec(board.env, board.vault, "Other spec", status="complete")
    board.task("Other parent", related=[f"spec={other}"], labels=[FOLLOWUP_PARENT_LABEL])
    assert board.gather()["followup-parents"] == []


def test_a_follow_up_parent_named_only_by_a_spec_outbound_edge_is_listed(board):
    parent = board.task("Outbound parent", labels=[FOLLOWUP_PARENT_LABEL])
    relate(board.env, f"spec/{board.spec}", f"task={parent}")
    assert board.gather()["followup-parents"] == [parent]


def test_a_task_linked_to_the_spec_without_the_label_is_not_a_parent_but_is_gathered(board):
    plain = board.task("Plain", related=[f"spec={board.spec}"])
    result = board.gather()
    assert result["followup-parents"] == []
    assert [f["name"] for f in result["followups"]] == [plain]


def test_follow_up_parents_are_sorted_by_name(board):
    names = [
        board.task(t, related=[f"spec={board.spec}"], labels=[FOLLOWUP_PARENT_LABEL])
        for t in ("Zed parent", "Alpha parent", "Mid parent")
    ]
    assert board.gather()["followup-parents"] == sorted(names)


def test_a_live_follow_up_parent_and_its_children_are_still_not_gathered(board):
    parent = board.task(
        "Live parent", related=[f"spec={board.spec}"], labels=[FOLLOWUP_PARENT_LABEL]
    )
    board.task("Nested", status="open", related=[f"spec={board.spec}"], parent=parent)
    result = board.gather()
    assert result["followup-parents"] == [parent]
    assert result["followups"] == []


def test_a_failed_lookup_of_a_follow_up_parent_exits_two_with_no_stdout(board, capsys):
    search_reply(board.env, "spec", search_body(hit(board.env, board.slice), hit(board.env, "vanished-parent")))
    code, _, out, err = run_board(board, capsys)
    assert code == 2
    assert out == ""
    assert reason_of(err) == "lore-failed"


def test_sanitize_text_redacts_a_token_after_a_backslash_u_sequence():
    token = "A" * 28
    out = gather.sanitize_text("C:\\uAb12" + token + " tail", 500)
    assert token not in out and "[REDACTED]" in out


def test_sanitize_text_redacts_a_token_after_a_control_looking_escape():
    out = gather.sanitize_text("\\u0000/opt/homebrew/Cellar/python@3 tail", 500)
    assert "[REDACTED]" in out
