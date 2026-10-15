"""followup_gather.py entry half: spec resolution across configured vaults and
the verdict on whether the spec's slice loop has ended.

Failure modes drive the script's functions with an injected `lore` runner; the
seam tests run the script as a subprocess, exactly as a skill invokes it,
against the real `lore` CLI reading a throwaway vault. The child's environment
is built from scratch (never inherited) so it cannot reach the live install.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

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
    make_vault,
)

_script_spec = importlib.util.spec_from_file_location("followup_gather", _SCRIPT)
gather = importlib.util.module_from_spec(_script_spec)
sys.modules["followup_gather"] = gather
_script_spec.loader.exec_module(gather)


class FakeLore:
    """A runner that answers `vault ls` and `record show --json --vault V spec/N`."""

    def __init__(self, vaults, records=None, vault_ls_rc=0):
        self.vaults = vaults
        self.records = records or {}
        self.vault_ls_rc = vault_ls_rc
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:2] == ["vault", "ls"]:
            out = "".join(f"{v}\tteam\t/x/{v}\t[(all kinds)]\n" for v in self.vaults)
            return subprocess.CompletedProcess(argv, self.vault_ls_rc, out, "")
        if argv[:3] == ["record", "show", "--json"]:
            vault = argv[argv.index("--vault") + 1]
            rid = argv[-1]
            doc = self.records.get((vault, rid))
            if doc is None:
                return subprocess.CompletedProcess(argv, 1, "", f"lore: record not found: {rid}\n")
            body = doc if isinstance(doc, str) else json.dumps(doc)
            return subprocess.CompletedProcess(argv, 0, body, "")
        if argv[:2] == ["task", "list"]:
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        if argv[0] == "search":
            empty = {"hits": [], "truncated": False}
            return subprocess.CompletedProcess(argv, 0, json.dumps(empty), "")
        raise AssertionError(f"unexpected lore call {argv}")


def spec_doc(status="ready", labels=None, kind="spec", related=None):
    sidecar = {"kind": kind, "status": status}
    if labels is not None:
        sidecar["labels"] = labels
    if related is not None:
        sidecar["related"] = related
    return {"record_id": "spec/s", "kind": kind, "sidecar": sidecar, "body": "# s"}


def run_main(runner, name="s", capsys=None):
    code = gather.main([name], runner=runner)
    out = capsys.readouterr()
    return code, out.out, out.err


def reason_of(err):
    lines = [ln for ln in err.splitlines() if ln.startswith("reason-code: ")]
    assert len(lines) == 1, err
    return lines[0].removeprefix("reason-code: ")


@pytest.mark.parametrize("name", ["has space", "a$(touch x)", "semi;colon", "", "a\nb"])
def test_a_malformed_spec_name_is_refused_before_any_lore_call(name, capsys):
    runner = FakeLore(["v"])
    code, out, err = run_main(runner, name, capsys)
    assert code == 2
    assert reason_of(err) == "invalid-spec-name"
    assert runner.calls == []


@pytest.mark.parametrize("name", ["-x", "--flag", "-", "--", "-rf"])
def test_a_spec_name_starting_with_a_dash_is_refused_before_any_lore_call(name, capsys):
    runner = FakeLore(["v"])
    code = gather.main(["--", name], runner=runner)
    err = capsys.readouterr().err
    assert code == 2
    assert reason_of(err) == "invalid-spec-name"
    assert runner.calls == []


@pytest.mark.parametrize("name", ["-x", "--flag"])
def test_a_dash_name_given_without_a_separator_is_refused_with_a_reason_code(name, capsys):
    runner = FakeLore(["v"])
    code = gather.main([name], runner=runner)
    err = capsys.readouterr().err
    assert code == 2
    assert reason_of(err) in {"invalid-spec-name", "invalid-arguments"}
    assert runner.calls == []


@pytest.mark.parametrize("name", ["..", "../x", "a/../b", "a/..", "../../etc"])
def test_a_spec_name_with_a_dotdot_segment_is_refused_before_any_lore_call(name, capsys):
    runner = FakeLore(["v"])
    code, out, err = run_main(runner, name, capsys)
    assert code == 2
    assert reason_of(err) == "invalid-spec-name"
    assert runner.calls == []


@pytest.mark.parametrize("name", ["a..b", "..a", "a/.b", "v1.2.3"])
def test_a_dotted_name_with_no_dotdot_segment_still_reaches_lore(name, capsys):
    runner = FakeLore(["v"])
    code, out, err = run_main(runner, name, capsys)
    assert reason_of(err) == "spec-not-found"
    assert runner.calls != []


@pytest.mark.parametrize(
    "argv",
    [[], ["--bogus", "s"], ["s", "extra"], ["s", "--bogus"], ["a", "b", "c"]],
)
def test_bad_arguments_exit_two_with_invalid_arguments_and_never_call_lore(argv, capsys):
    runner = FakeLore(["v"])
    code = gather.main(argv, runner=runner)
    err = capsys.readouterr().err
    assert code == 2
    assert reason_of(err) == "invalid-arguments"
    assert runner.calls == []


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_still_exits_zero_with_usage_and_never_calls_lore(flag, capsys):
    runner = FakeLore(["v"])
    with pytest.raises(SystemExit) as exc:
        gather.main([flag], runner=runner)
    out = capsys.readouterr()
    assert exc.value.code == 0
    assert "usage:" in out.out
    assert "spec_name" in out.out
    assert "reason-code" not in out.err
    assert runner.calls == []


@pytest.mark.parametrize(
    "status,labels,entry",
    [
        ("ready", {"craft/slice-loop": "complete"}, "complete-marker"),
        ("ready", {"craft/slice-loop": "stopped"}, "stopped-marker"),
        ("complete", {}, "complete-status"),
        ("complete", {"craft/slice-loop": "other"}, "complete-status"),
    ],
)
def test_an_ended_slice_loop_exits_zero_naming_how_it_ended(status, labels, entry, capsys):
    runner = FakeLore(["v"], {("v", "spec/s"): spec_doc(status, labels)})
    code, out, err = run_main(runner, "s", capsys)
    assert code == 0, err
    result = json.loads(out)
    assert result["spec"] == "s"
    assert result["vault"] == "v"
    assert result["entry"] == entry


@pytest.mark.parametrize(
    "labels",
    [{}, {"craft/slice-loop": "in-progress"}, {"craft/slice-loop": ""}, {"unrelated": "complete"}],
)
def test_a_ready_spec_without_an_ended_marker_is_refused(labels, capsys):
    runner = FakeLore(["v"], {("v", "spec/s"): spec_doc("ready", labels)})
    code, out, err = run_main(runner, "s", capsys)
    assert code == 1
    assert out == ""
    assert reason_of(err) == "slice-loop-not-ended"


def test_a_spec_in_no_vault_is_not_found(capsys):
    runner = FakeLore(["a", "b"])
    code, _, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == "spec-not-found"


def test_a_spec_in_two_vaults_is_ambiguous(capsys):
    doc = spec_doc("complete")
    runner = FakeLore(["a", "b"], {("a", "spec/s"): doc, ("b", "spec/s"): doc})
    code, _, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == "spec-ambiguous"


def test_a_record_that_is_not_a_spec_is_refused(capsys):
    runner = FakeLore(["v"], {("v", "spec/s"): spec_doc("complete", kind="task")})
    code, _, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == "not-a-spec"


def test_resolve_spec_returns_the_vault_and_sidecar():
    doc = spec_doc("ready", {"craft/slice-loop": "stopped"})
    runner = FakeLore(["a", "b"], {("b", "spec/s"): doc})
    vault, sidecar = gather.resolve_spec(runner, "s")
    assert vault == "b"
    assert sidecar == doc["sidecar"]


def test_every_lore_call_is_an_argv_list_of_strings():
    runner = FakeLore(["a", "b"], {("b", "spec/s"): spec_doc("complete")})
    gather.main(["s"], runner=runner)
    assert runner.calls
    for argv in runner.calls:
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)


VAULT_LS = "v\tteam\t/x/v\t[(all kinds)]\n"


def _failing(exc=None, rc=None, stdout="", stderr="", on="record"):
    def runner(argv):
        if argv[:2] == ["vault", "ls"] and on == "record":
            return subprocess.CompletedProcess(argv, 0, VAULT_LS, "")
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(argv, rc, stdout, stderr)

    return runner


@pytest.mark.parametrize(
    "runner,reason",
    [
        (_failing(exc=FileNotFoundError("lore")), "lore-missing"),
        (_failing(exc=subprocess.TimeoutExpired(["lore"], 1)), "lore-timeout"),
        (_failing(rc=3, stderr="SECRET-vault-text"), "lore-failed"),
        (_failing(rc=0, stdout="not json SECRET-vault-text"), "lore-output-not-json"),
        (_failing(rc=0, stdout="{}"), "empty-sidecar"),
        (_failing(rc=0, stdout=json.dumps({"kind": "spec", "sidecar": {}})), "empty-sidecar"),
        (_failing(rc=0, stdout="[1, 2]"), "lore-output-not-json"),
        (_failing(exc=FileNotFoundError("lore"), on="ls"), "lore-missing"),
        (_failing(exc=subprocess.TimeoutExpired(["lore"], 1), on="ls"), "lore-timeout"),
        (_failing(rc=3, stderr="SECRET-vault-text", on="ls"), "lore-failed"),
    ],
)
def test_each_runner_failure_has_its_own_reason_code_and_echoes_nothing_from_lore(
    runner, reason, capsys
):
    code, out, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == reason
    assert "SECRET" not in out + err
    assert "Traceback" not in err


def test_a_vault_listing_with_no_vaults_cannot_certify(capsys):
    code, _, err = run_main(FakeLore([]), "s", capsys)
    assert code == 2
    assert reason_of(err) == "no-vaults"


@pytest.mark.parametrize("sidecar", [{"kind": "spec"}, {"status": "ready"}])
def test_a_sidecar_missing_kind_or_status_is_a_failed_lookup(sidecar, capsys):
    doc = {"record_id": "spec/s", "kind": "spec", "sidecar": sidecar}
    runner = FakeLore(["v"], {("v", "spec/s"): doc})
    code, _, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == "malformed-sidecar"


def test_an_unexpected_exception_exits_two_not_the_refusal_code(capsys):
    def runner(argv):
        raise RuntimeError("boom SECRET-vault-text")

    code, out, err = run_main(runner, "s", capsys)
    assert code == 2
    assert reason_of(err) == "internal-error"
    assert "SECRET" not in out + err
    assert "Traceback" not in err


def test_the_default_runner_reports_a_missing_binary_as_lore_missing(monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")
    code = gather.main(["s"])
    err = capsys.readouterr().err
    assert code == 2
    assert reason_of(err) == "lore-missing"


# --- seam: the script as a subprocess against the real lore CLI ---------------


def _build_env(tmp_path, vaults):
    """A from-scratch child environment whose `lore` sees only `vaults`
    (a list of (name, scope, path)); nothing is inherited from os.environ
    except PATH, behind a shim that runs this checkout's lore."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    cfg = tmp_path / "xdg_config"
    _lore_conftest.write_vault_config(cfg, vaults)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / "lore"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_lore_conftest.CLI_PATH}" "$@"\n')
    shim.chmod(0o755)
    return {
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "XDG_STATE_HOME": str(state),
        "XDG_CONFIG_HOME": str(cfg),
        "LORE_GROUPS_DIR": str(tmp_path / "groups"),
        "LORE_PUBLISH_DISABLE": "1",
        "LORE_EMAIL": "tester@example.com",
    }


def _lore(env, *args, stdin=None):
    r = subprocess.run(["lore", *args], capture_output=True, text=True, env=env, input=stdin)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def lore_env(tmp_path):
    """(env, vault_name): a child environment seeing exactly one throwaway vault."""
    vault, _ = make_vault(tmp_path)
    return _build_env(tmp_path, [("default", "default", vault)]), "default"


@pytest.fixture
def two_vault_lore_env(tmp_path):
    first, _ = make_vault(tmp_path)
    second = tmp_path / "second"
    second.mkdir()
    env = _build_env(tmp_path, [("default", "default", first), ("other", "team", second)])
    return env, ("default", "other")


def make_spec(env, vault, title, status="ready", labels=(), related=()):
    args = ["record", "create", "--kind", "spec", "--title", title, "--status", status,
            "--vault", vault]
    for label in labels:
        args += ["--label", label]
    for edge in related:
        args += ["--related", edge]
    return _lore(env, *args, stdin="# body\n").split("/", 1)[1]


def run_script(env, *args):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args], capture_output=True, text=True, env=env
    )


def test_the_script_run_as_a_subprocess_certifies_a_stopped_spec(  # inert-gate: allow seam smoke
    lore_env,
):
    env, vault = lore_env
    name = make_spec(env, vault, "Seam stopped", labels=["craft/slice-loop=stopped"])
    r = run_script(env, name)
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["spec"] == name
    assert result["vault"] == vault
    assert result["entry"] == "stopped-marker"


def test_a_ready_spec_with_no_marker_is_refused_through_real_lore(lore_env):
    env, vault = lore_env
    name = make_spec(env, vault, "Seam ready")
    r = run_script(env, name)
    assert r.returncode == 1
    assert "reason-code: slice-loop-not-ended" in r.stderr


def test_a_completed_spec_is_certified_by_status_through_real_lore(lore_env):
    env, vault = lore_env
    name = make_spec(env, vault, "Seam complete", status="complete")
    r = run_script(env, name)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["entry"] == "complete-status"


def test_an_unknown_spec_is_not_found_through_real_lore(lore_env):
    env, _ = lore_env
    r = run_script(env, "no-such-spec")
    assert r.returncode == 2
    assert "reason-code: spec-not-found" in r.stderr


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
    args = ["record", "create", "--kind", "task", "--title", title, "--status", status,
            "--vault", vault]
    for edge in related:
        args += ["--related", edge]
    for label in labels:
        args += ["--label", label]
    if parent:
        args += ["--parent", parent]
    return _lore(env, *args, stdin="# body\n").split("/", 1)[1]


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
    assert b.names() == []


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


def test_hostile_edge_values_on_the_spec_are_skipped_and_counted_through_real_lore(board):
    good = board.task("Good", related=[f"task={board.slice}"])
    for value in ['x") OR kind:task ("', "-flag"]:
        relate(board.env, f"spec/{board.spec}", f"task={value}")
    result = board.gather()
    assert [f["name"] for f in result["followups"]] == [good]
    assert result["skipped-edges"] == 2


def test_follow_ups_past_the_single_query_ceiling_are_all_gathered(board):
    children = [board.child] + [
        board.task(f"Kid {i:02d}", status="done", parent=board.slice) for i in range(40)
    ]
    first = board.task("Linked to first", related=[f"task={children[0]}"])
    last = board.task("Linked to last", related=[f"task={children[-1]}"])
    result = board.gather()
    assert result["child-count"] == 41
    assert [f["name"] for f in result["followups"]] == sorted([first, last])


# --- gathering: injected lore ---------------------------------------------------


def task_doc(name, status="open", title=None, parent=None, labels=None, related=None, kind="task"):
    sidecar = {"kind": kind, "status": status, "title": title or name.title()}
    if parent is not None:
        sidecar["parent"] = parent
    if labels is not None:
        sidecar["labels"] = labels
    if related is not None:
        sidecar["related"] = related
    return {"record_id": f"task/{name}", "kind": kind, "sidecar": sidecar, "body": "b"}


def hit(name, vault="v", kind="task"):
    return {
        "id": f"/x/{vault}/{kind}/{name}", "title": name, "kind": kind,
        "status": "open", "shared": 0, "vault": f"/x/{vault}", "snippet": "",
    }


def search_body(*hits, truncated=False):
    return json.dumps({
        "hits": list(hits), "showing": len(hits), "offset": 0, "total": len(hits),
        "stale": False, "config_stale": False, "truncated": truncated,
        "reverse_edge_alias": True,
    })


class GatherLore(FakeLore):
    """FakeLore plus `task list` and `search`. `search` is a callable taking the
    query and returning (returncode, stdout)."""

    def __init__(self, vaults, records=None, listing=(), search=None):
        super().__init__(vaults, records)
        self.listing = list(listing)
        self.search = search or (lambda q: (0, search_body()))
        self.queries = []

    def __call__(self, argv):
        if argv[:2] == ["task", "list"]:
            self.calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.listing), "")
        if argv[0] == "search":
            self.calls.append(list(argv))
            self.queries.append(argv[1])
            rc, out = self.search(argv[1])
            return subprocess.CompletedProcess(argv, rc, out, "boom vault text" if rc else "")
        return super().__call__(argv)


def entry(name, status="open", parent=None, children=()):
    return {"name": name, "status": status, "parent": parent, "children": list(children)}


def fake_board(follow_sidecar=None, spec_related=None, search=None, extra=None):
    """Spec s (complete) -> labelled slice p -> child k; one follow-up f found by search."""
    records = {
        ("v", "spec/s"): spec_doc(status="complete", related=spec_related),
        ("v", "task/p"): task_doc("p", "in-progress", labels={"craft/slice-parent": ""},
                                  related={"spec": ["s"]}),
        ("v", "task/k"): task_doc("k", "done", parent="p"),
        ("v", "task/f"): follow_sidecar or task_doc("f", "open", title="Follow"),
    }
    records.update(extra or {})
    listing = [entry("p", "in-progress", children=["k"]), entry("k", "done", parent="p"),
               entry("f", "open")]

    def default_search(q):
        return 0, search_body(hit("p")) if q.startswith("related-spec") else search_body(hit("f"))

    return GatherLore(["v"], records, listing, search or default_search)


def run_json(runner, capsys):
    code, out, err = run_main(runner, "s", capsys)
    return code, (json.loads(out) if out.strip() else None), out, err


def test_the_injected_board_gathers_its_follow_up(capsys):
    code, result, _, err = run_json(fake_board(), capsys)
    assert code == 0, err
    assert result["followups"] == [{"name": "f", "title": "Follow", "status": "open"}]


def test_a_truncated_search_exits_two_search_truncated_with_no_output(capsys):
    runner = fake_board(search=lambda q: (0, search_body(hit("p"), truncated=True)))
    code, _, out, err = run_json(runner, capsys)
    assert code == 2
    assert reason_of(err) == "search-truncated"
    assert out == ""


def test_a_failing_search_exits_two_lore_failed_with_no_output_or_vault_text(capsys):
    runner = fake_board(search=lambda q: (1, ""))
    code, _, out, err = run_json(runner, capsys)
    assert code == 2
    assert reason_of(err) == "lore-failed"
    assert out == ""
    assert "boom" not in err


def test_a_search_that_is_not_json_exits_two(capsys):
    runner = fake_board(search=lambda q: (0, "not json"))
    code, _, out, err = run_json(runner, capsys)
    assert (code, reason_of(err), out) == (2, "lore-output-not-json", "")


def test_a_candidate_whose_read_fails_exits_two_with_no_partial_set(capsys):
    runner = fake_board(extra={("v", "task/g"): task_doc("g", "open")})
    del runner.records[("v", "task/f")]
    code, _, out, err = run_json(runner, capsys)
    assert (code, reason_of(err), out) == (2, "lore-failed", "")


def test_a_candidate_with_an_empty_sidecar_exits_two_with_no_partial_set(capsys):
    runner = fake_board(follow_sidecar={"record_id": "task/f", "sidecar": {}, "body": ""})
    code, _, out, err = run_json(runner, capsys)
    assert (code, reason_of(err), out) == (2, "empty-sidecar", "")


def test_a_candidate_sidecar_missing_status_exits_two_with_no_partial_set(capsys):
    runner = fake_board(follow_sidecar={"sidecar": {"kind": "task", "title": "t"}})
    code, _, out, err = run_json(runner, capsys)
    assert (code, reason_of(err), out) == (2, "malformed-sidecar", "")


def test_a_failing_task_listing_exits_two(capsys):
    runner = fake_board()
    original = runner.__call__

    class Broken(GatherLore):
        def __call__(self, argv):
            if argv[:2] == ["task", "list"]:
                return subprocess.CompletedProcess(argv, 1, "", "x")
            return original(argv)

    broken = Broken(["v"], runner.records, runner.listing, runner.search)
    code, _, out, err = run_json(broken, capsys)
    assert (code, reason_of(err), out) == (2, "lore-failed", "")


def test_hostile_edge_values_never_reach_a_query_or_a_read(capsys):
    hostile = ['x") OR kind:task ("', "-flag", "a/../b", "task/-x"]
    runner = fake_board(spec_related={"task": [*hostile, "f"]})
    code, result, _, err = run_json(runner, capsys)
    assert code == 0, err
    assert result["skipped-edges"] == len(hostile)
    assert [f["name"] for f in result["followups"]] == ["f"]
    for argv in runner.calls:
        assert not any(bad in part for part in argv for bad in hostile), argv


def test_a_hit_with_a_hostile_record_name_is_skipped_and_counted(capsys):
    def search(q):
        return 0, search_body(hit("p") if q.startswith("related-spec") else hit("f"),
                              hit("a b"), hit("-x"))

    code, result, _, err = run_json(fake_board(search=search), capsys)
    assert code == 0, err
    assert result["skipped-edges"] == 2
    assert [f["name"] for f in result["followups"]] == ["f"]


def test_hits_from_another_vault_or_kind_are_ignored(capsys):
    def search(q):
        if q.startswith("related-spec"):
            return 0, search_body(hit("p"))
        return 0, search_body(hit("f"), hit("other", vault="elsewhere"), hit("s", kind="spec"))

    code, result, _, err = run_json(fake_board(search=search), capsys)
    assert code == 0, err
    assert [f["name"] for f in result["followups"]] == ["f"]
    assert result["candidates-read"] == 1


def test_a_listed_task_with_a_hostile_name_is_skipped_and_never_queried_or_read(capsys):
    runner = fake_board()
    runner.listing += [entry("bad name", parent="p"), entry("-x", parent="p")]
    code, result, _, err = run_json(runner, capsys)
    assert code == 0, err
    assert result["skipped-edges"] == 2
    assert result["child-count"] == 1
    for argv in runner.calls:
        assert not any(bad in part for part in argv for bad in ("bad name", "-x")), argv


def test_a_dangling_edge_is_skipped_and_counted(capsys):
    runner = fake_board(spec_related={"task": ["ghost", "f"]})
    code, result, _, err = run_json(runner, capsys)
    assert code == 0, err
    assert result["skipped-edges"] == 1
    assert [f["name"] for f in result["followups"]] == ["f"]


def test_every_query_carries_at_most_the_batch_size_in_values(capsys):
    kids = [f"kid{i:03d}" for i in range(100)]
    records = {("v", f"task/{k}"): task_doc(k, "done", parent="p") for k in kids}
    runner = fake_board(extra=records)
    runner.listing[0]["children"] = kids
    runner.listing += [entry(k, "done", parent="p") for k in kids]
    code, result, _, err = run_json(runner, capsys)
    assert code == 0, err
    task_queries = [q for q in runner.queries if q.startswith("related-task")]
    assert len(task_queries) > 1
    assert all(q.count('"') // 2 <= gather.QUERY_BATCH_SIZE for q in task_queries)
    seen = {v for q in task_queries for v in re.findall(r'"([^"]+)"', q)}
    assert {f"task/{k}" for k in kids} <= seen and set(kids) <= seen


def test_a_title_is_emitted_without_control_characters(capsys):
    runner = fake_board(follow_sidecar=task_doc("f", title="Red\x1b[31m alert\x07\x85 done\n"))
    _, result, _, _ = run_json(runner, capsys)
    assert result["followups"][0]["title"] == "Red[31m alert done"


def test_a_title_carrying_a_credential_is_emitted_redacted(capsys):
    runner = fake_board(follow_sidecar=task_doc("f", title="Rotate AKIAIOSFODNN7EXAMPLE now"))
    _, result, _, _ = run_json(runner, capsys)
    title = result["followups"][0]["title"]
    assert "AKIAIOSFODNN7EXAMPLE" not in title
    assert title.startswith("Rotate ") and title.endswith(" now")


def test_a_title_with_nothing_to_strip_is_emitted_unchanged(capsys):
    runner = fake_board(follow_sidecar=task_doc("f", title="Plain follow-up title"))
    _, result, _, _ = run_json(runner, capsys)
    assert result["followups"][0]["title"] == "Plain follow-up title"


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
