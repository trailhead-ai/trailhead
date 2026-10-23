"""followup_parent.py `check` (sorts refined follow-ups into eligible and left out),
`assemble` (nests the confirmed ones under one follow-up parent and rolls forward after the
create) and `resume` (finishes an open parent).

End-to-end cases run the script as a subprocess against the real `lore` CLI reading
a throwaway vault (the pattern of test_followup_gather.py); failure cases drive the
module's functions with an injected `lore` runner.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from test_followup_gather import (  # noqa: F401
    _allow_real_publish_spawn,
    _build_env,
    _isolate_ambient_env,
    _lore,
    _publish_spawn_guard,
    make_spec,
    make_vault,
)

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts" / "followup_parent.py"
)
_spec = importlib.util.spec_from_file_location("followup_parent", _SCRIPT)
parent = importlib.util.module_from_spec(_spec)
sys.modules["followup_parent"] = parent
_spec.loader.exec_module(parent)



@pytest.fixture
def lore_env(tmp_path):
    """(env, vault_name): a child environment seeing exactly one throwaway vault."""
    vault, _ = make_vault(tmp_path)
    return _build_env(tmp_path, [("default", "default", vault)]), "default"


CLEAN = "**Delivers:** A new widget that frobs.\n\n**Files:** `src/a.py`, `src/b.py`\n"


def make_followup(
    env, vault, title, status="ready", body=CLEAN, labels=(), parent=None, depends=(), spec=None,
    task_edge=None,
):
    args = ["record", "create", "--kind", "task", "--title", title, "--status", status,
            "--vault", vault]
    for label in labels:
        args += ["--label", label]
    if parent:
        args += ["--parent", parent]
    for dep in depends:
        args += ["--depends-on", dep]
    if spec:
        args += ["--related", f"spec={spec}"]
    if task_edge:
        args += ["--related", f"task={task_edge}"]
    return _lore(env, *args, stdin=body).split("/", 1)[1]


def sentence(length):
    """Plain prose of exactly `length` characters, ending in a letter."""
    words = "the quick brown fox jumps over a lazy dog "
    return (words * (length // len(words) + 1))[:length].rstrip() + "z" * (
        length - len((words * (length // len(words) + 1))[:length].rstrip())
    )


def run_check(env, spec, *names):
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "check", spec, *names],
        capture_output=True, text=True, env=env,
    )
    return r


def check_ok(env, spec, *names):
    r = run_check(env, spec, *names)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def left_out(result):
    return {item["name"]: item for item in result["left-out"]}


def eligible(result):
    return {item["name"]: item for item in result["eligible"]}


@pytest.fixture
def spec(lore_env):
    env, vault = lore_env
    return make_spec(env, vault, "Parent spec", status="complete")


# --- one follow-up per verdict ---------------------------------------------------


def test_a_ready_follow_up_with_a_clean_body_is_eligible(lore_env, spec):
    env, vault = lore_env
    name = make_followup(env, vault, "Clean one", spec=spec)
    result = check_ok(env, spec, name)
    assert list(eligible(result)) == [name]
    assert result["left-out"] == []
    assert result["overlaps"] == []


UNRESOLVED_BODY = CLEAN + "\n## Refine — unresolved\n\n**Question:** which way?\n"


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"status": "open", "body": UNRESOLVED_BODY}, "unresolved-question"),
        ({"status": "open", "labels": ["route=plan"]}, "routed-to-plan"),
        ({"status": "open", "labels": ["route=brainstorm"]}, "routed-to-brainstorm"),
        ({"status": "blocked"}, "not-ready"),
        ({"status": "open"}, "not-ready"),
        ({"labels": ["craft/followup-parent"]}, "is-a-parent"),
        ({"labels": ["craft/slice-parent"]}, "is-a-parent"),
    ],
)
def test_each_unrefined_or_unfit_follow_up_is_left_out_with_its_reason(
    lore_env, spec, kwargs, reason
):
    env, vault = lore_env
    name = make_followup(env, vault, "Subject", **kwargs, spec=spec)
    result = check_ok(env, spec, name)
    assert result["eligible"] == []
    assert left_out(result)[name] == {"name": name, "reason": reason}


def test_a_follow_up_with_a_parent_is_already_nested(lore_env, spec):
    env, vault = lore_env
    holder = make_followup(env, vault, "Holder", status="open", spec=spec)
    name = make_followup(env, vault, "Nested", parent=holder, spec=spec)
    result = check_ok(env, spec, name)
    assert left_out(result)[name] == {"name": name, "reason": "already-nested"}


def test_a_ready_follow_up_carrying_a_route_label_is_routed_not_eligible(lore_env, spec):
    env, vault = lore_env
    name = make_followup(env, vault, "Routed ready", labels=["route=plan"], spec=spec)
    result = check_ok(env, spec, name)
    assert left_out(result)[name]["reason"] == "routed-to-plan"


def test_the_verdicts_are_decided_per_follow_up_within_one_call(lore_env, spec):
    env, vault = lore_env
    good = make_followup(env, vault, "Good", spec=spec)
    bad = make_followup(env, vault, "Bad", status="blocked", spec=spec)
    result = check_ok(env, spec, bad, good)
    assert list(eligible(result)) == [good]
    assert list(left_out(result)) == [bad]


# --- dependencies ------------------------------------------------------------------


def test_a_dependency_on_a_done_task_outside_the_set_keeps_it_eligible(lore_env, spec):
    env, vault = lore_env
    dep = make_followup(env, vault, "Finished", status="done", spec=spec)
    name = make_followup(env, vault, "Needs finished", depends=[dep], spec=spec)
    assert list(eligible(check_ok(env, spec, name))) == [name]


def test_a_dependency_on_an_open_task_outside_the_set_is_depends_outside(lore_env, spec):
    env, vault = lore_env
    dep = make_followup(env, vault, "Unfinished", status="open", spec=spec)
    name = make_followup(env, vault, "Needs unfinished", depends=[dep], spec=spec)
    result = check_ok(env, spec, name)
    assert left_out(result)[name] == {
        "name": name, "reason": "depends-outside", "dependency": dep,
    }


def test_a_dependency_on_a_dropped_task_is_depends_outside(lore_env, spec):
    env, vault = lore_env
    dep = make_followup(env, vault, "Dropped dep", status="dropped", spec=spec)
    name = make_followup(env, vault, "Needs dropped", depends=[dep], spec=spec)
    assert left_out(check_ok(env, spec, name))[name]["dependency"] == dep


def test_a_dependency_on_a_superseded_task_is_depends_outside(lore_env, spec):
    env, vault = lore_env
    dep = make_followup(env, vault, "Superseded dep", status="open", spec=spec)
    successor = make_followup(env, vault, "Successor", status="open", spec=spec)
    _lore(env, "record", "supersede", f"task/{dep}", "--by", f"task/{successor}")
    name = make_followup(env, vault, "Needs superseded", depends=[dep], spec=spec)
    result = check_ok(env, spec, name)
    assert left_out(result)[name] == {
        "name": name, "reason": "depends-outside", "dependency": dep,
    }


def test_a_dependency_on_an_eligible_sibling_keeps_it_eligible(lore_env, spec):
    env, vault = lore_env
    first = make_followup(env, vault, "First", spec=spec)
    second = make_followup(env, vault, "Second", depends=[first], spec=spec)
    assert set(eligible(check_ok(env, spec, second, first))) == {first, second}


def test_a_dependent_sorting_before_its_dependency_chain_is_still_eligible(lore_env, spec):
    env, vault = lore_env
    base = make_followup(env, vault, "Zulu base", spec=spec)
    middle = make_followup(env, vault, "Yankee middle", depends=[base], spec=spec)
    top = make_followup(env, vault, "Alpha top", depends=[middle], spec=spec)
    assert set(eligible(check_ok(env, spec, top, middle, base))) == {top, middle, base}


def test_a_dependency_on_a_left_out_sibling_is_left_out_naming_it(lore_env, spec):
    env, vault = lore_env
    first = make_followup(env, vault, "First", status="blocked", spec=spec)
    second = make_followup(env, vault, "Second", depends=[first], spec=spec)
    result = check_ok(env, spec, first, second)
    assert result["eligible"] == []
    assert left_out(result)[first]["reason"] == "not-ready"
    assert left_out(result)[second] == {
        "name": second, "reason": "depends-outside", "dependency": first,
    }


def test_a_two_step_chain_through_a_left_out_sibling_is_left_out_throughout(lore_env, spec):
    env, vault = lore_env
    root = make_followup(env, vault, "Root", status="blocked", spec=spec)
    middle = make_followup(env, vault, "Middle", depends=[root], spec=spec)
    leaf = make_followup(env, vault, "Leaf", depends=[middle], spec=spec)
    result = check_ok(env, spec, leaf, middle, root)
    assert result["eligible"] == []
    assert left_out(result)[middle]["dependency"] == root
    assert left_out(result)[leaf] == {
        "name": leaf, "reason": "depends-outside", "dependency": middle,
    }


def test_a_dependency_on_a_task_that_does_not_exist_is_depends_outside(lore_env, spec):
    env, vault = lore_env
    dep = make_followup(env, vault, "Soon gone", status="open", spec=spec)
    name = make_followup(env, vault, "Dangling", depends=[dep], spec=spec)
    _lore(env, "record", "delete", f"task/{dep}", "--vault", vault)
    assert left_out(check_ok(env, spec, name))[name]["dependency"] == dep


# --- revision, delivers, files --------------------------------------------------------


def shown_revision(env, vault, name):
    out = _lore(env, "record", "show", "--json", "--vault", vault, f"task/{name}")
    return json.loads(out)["revision"]


def test_the_emitted_revision_is_the_one_lore_reports_and_follows_updates(lore_env, spec):
    env, vault = lore_env
    name = make_followup(env, vault, "Revised", spec=spec)
    before = eligible(check_ok(env, spec, name))[name]["revision"]
    assert before == shown_revision(env, vault, name)
    _lore(env, "record", "update", f"task/{name}", stdin=CLEAN + "\nMore text.\n")
    after = eligible(check_ok(env, spec, name))[name]["revision"]
    assert after == shown_revision(env, vault, name)
    assert after != before


def test_delivers_and_files_carry_the_field_text(lore_env, spec):
    env, vault = lore_env
    body = (
        "Intro line.\n\n**Delivers:** First sentence. Second\nsentence over two lines.\n\n"
        "**Test contract:**\n- a test\n\n**Files:** `src/a.py`\n`src/b.py` (new)\n\n"
        "## Flow-out\n\n- [ ] done\n"
    )
    name = make_followup(env, vault, "Fields", body=body, spec=spec)
    item = eligible(check_ok(env, spec, name))[name]
    assert item["delivers"] == "First sentence. Second sentence over two lines."
    assert item["files"] == "`src/a.py` `src/b.py` (new)"


def test_a_missing_field_is_emitted_empty(lore_env, spec):
    env, vault = lore_env
    name = make_followup(env, vault, "No fields", body="Just prose.\n", spec=spec)
    item = eligible(check_ok(env, spec, name))[name]
    assert item["delivers"] == "" and item["files"] == ""


def test_a_credential_in_a_field_is_redacted(lore_env, spec):
    env, vault = lore_env
    body = "**Delivers:** Rotate AKIAIOSFODNN7EXAMPLE now.\n\n**Files:** `k/AKIAIOSFODNN7EXAMPLE.py`\n"
    name = make_followup(env, vault, "Leaky", body=body, spec=spec)
    r = run_check(env, spec, name)
    assert r.returncode == 0, r.stderr
    assert "AKIAIOSFODNN7EXAMPLE" not in r.stdout
    item = eligible(json.loads(r.stdout))[name]
    assert "[REDACTED]" in item["delivers"] and "[REDACTED]" in item["files"]


def test_control_and_invisible_characters_in_a_field_are_stripped(lore_env, spec):
    env, vault = lore_env
    body = "**Delivers:** a\x1b[31mred\x07 and​zero‮width\n\n**Files:** `x.py`\n"
    name = make_followup(env, vault, "Hostile", body=body, spec=spec)
    item = eligible(check_ok(env, spec, name))[name]
    assert item["delivers"] == "a[31mred andzerowidth"


def test_an_over_long_field_is_truncated_with_the_marker(lore_env, spec):
    env, vault = lore_env
    limit = parent.MAX_FIELD_CHARS
    text = sentence(limit + 50)
    body = f"**Delivers:** {text}\n\n**Files:** `src/a.py` {sentence(limit)}\n"
    name = make_followup(env, vault, "Long", body=body, spec=spec)
    item = eligible(check_ok(env, spec, name))[name]
    assert item["delivers"] == text[:limit] + parent.gather.TRUNCATION_MARKER
    assert item["files"].endswith(parent.gather.TRUNCATION_MARKER)
    assert len(item["files"]) == limit + 1


def test_a_field_one_past_the_bound_is_truncated(lore_env, spec):
    env, vault = lore_env
    limit = parent.MAX_FIELD_CHARS
    text = sentence(limit + 1)
    name = make_followup(env, vault, "Past", body=f"**Delivers:** {text}\n", spec=spec)
    item = eligible(check_ok(env, spec, name))[name]
    assert item["delivers"] == text[:limit] + parent.gather.TRUNCATION_MARKER


def test_a_field_at_exactly_the_bound_is_not_truncated(lore_env, spec):
    env, vault = lore_env
    limit = parent.MAX_FIELD_CHARS
    text = sentence(limit)
    name = make_followup(env, vault, "Edge", body=f"**Delivers:** {text}\n", spec=spec)
    item = eligible(check_ok(env, spec, name))[name]
    assert item["delivers"] == text


# --- overlaps ----------------------------------------------------------------------------


def test_two_eligible_follow_ups_naming_one_file_overlap(lore_env, spec):
    env, vault = lore_env
    one = make_followup(
        env, vault, "One", spec=spec,
        body="**Delivers:** a\n\n**Files:** `src/shared.py`, `src/one.py`\n",
    )
    two = make_followup(
        env, vault, "Two", spec=spec,
        body="**Delivers:** b\n\n**Files:** `src/shared.py`, `src/two.py`\n",
    )
    result = check_ok(env, spec, two, one)
    assert result["overlaps"] == [{"file": "src/shared.py", "names": sorted([one, two])}]


def test_follow_ups_with_disjoint_files_have_no_overlaps(lore_env, spec):
    env, vault = lore_env
    one = make_followup(env, vault, "One", body="**Delivers:** a\n\n**Files:** `src/one.py`\n", spec=spec)
    two = make_followup(env, vault, "Two", body="**Delivers:** b\n\n**Files:** `src/two.py`\n", spec=spec)
    assert check_ok(env, spec, one, two)["overlaps"] == []


def test_a_credential_shaped_path_in_an_overlap_is_redacted(lore_env, spec):
    env, vault = lore_env
    body = "**Delivers:** a\n\n**Files:** `k/AKIAIOSFODNN7EXAMPLE.py`\n"
    one = make_followup(env, vault, "One", body=body, spec=spec)
    two = make_followup(env, vault, "Two", body=body, spec=spec)
    r = run_check(env, spec, one, two)
    assert r.returncode == 0, r.stderr
    assert "AKIAIOSFODNN7EXAMPLE" not in r.stdout
    assert json.loads(r.stdout)["overlaps"] == [
        {"file": "k/[REDACTED].py", "names": sorted([one, two])}
    ]


def test_a_left_out_follow_up_does_not_create_an_overlap(lore_env, spec):
    env, vault = lore_env
    one = make_followup(env, vault, "One", body="**Delivers:** a\n\n**Files:** `src/s.py`\n", spec=spec)
    two = make_followup(
        env, vault, "Two", status="blocked", body="**Delivers:** b\n\n**Files:** `src/s.py`\n"
    )
    assert check_ok(env, spec, one, two)["overlaps"] == []


def test_a_path_in_prose_is_not_an_overlap(lore_env, spec):
    env, vault = lore_env
    one = make_followup(env, vault, "One", body="**Delivers:** a\n\n**Files:** src/s.py\n", spec=spec)
    two = make_followup(env, vault, "Two", body="**Delivers:** b\n\n**Files:** src/s.py\n", spec=spec)
    assert check_ok(env, spec, one, two)["overlaps"] == []


# --- failures: injected lore ----------------------------------------------------------------


class Lore:
    """A runner over canned records: `vault ls`, `record show --json`."""

    def __init__(self, tasks=None, show_rc=0, show_stdout=None, show_stderr="", raises=None):
        self.tasks = tasks or {}
        self.show_rc, self.show_stdout, self.show_stderr = show_rc, show_stdout, show_stderr
        self.raises = raises
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:2] == ["vault", "ls"]:
            return subprocess.CompletedProcess(argv, 0, "v\tteam\t/x/v\t[(all kinds)]\n", "")
        if argv[:3] == ["record", "show", "--json"]:
            rid = argv[-1]
            if rid.startswith("spec/"):
                doc = {"revision": "r", "sidecar": {"kind": "spec", "status": "complete"}}
                return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")
            if self.raises is not None:
                raise self.raises
            if self.show_stdout is not None or self.show_rc:
                return subprocess.CompletedProcess(
                    argv, self.show_rc, self.show_stdout or "", self.show_stderr
                )
            doc = self.tasks.get(rid)
            if doc is None:
                return subprocess.CompletedProcess(argv, 1, "", f"lore: record not found: {rid}\n")
            return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")
        if argv[:2] == ["task", "list"]:
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        if argv[0] == "search":
            return subprocess.CompletedProcess(argv, 0, json.dumps({"hits": [], "truncated": False}), "")
        raise AssertionError(f"unexpected lore call {argv}")


def task_doc(status="ready", body=CLEAN, revision="rev1", **sidecar):
    return {
        "revision": revision, "body": body,
        "sidecar": {"kind": "task", "status": status, "title": "t", "related": {"spec": ["s"]}, **sidecar},
    }


def run_main(runner, *argv, capsys):
    code = parent.main(list(argv), runner=runner)
    out = capsys.readouterr()
    return code, out.out, out.err


def reason_of(err):
    lines = [ln for ln in err.splitlines() if ln.startswith("reason-code: ")]
    assert len(lines) == 1, err
    return lines[0].removeprefix("reason-code: ")


def _show_doc(revision="r", sidecar=None):
    doc = {"body": "b", "sidecar": sidecar or {"kind": "task", "status": "ready"}}
    if revision is not None:
        doc["revision"] = revision
    return json.dumps(doc)


@pytest.mark.parametrize(
    "runner,reason",
    [
        (Lore(show_rc=3, show_stderr="SECRET-vault-text"), "lore-failed"),
        (Lore(show_stdout="not json SECRET-vault-text"), "lore-output-not-json"),
        (Lore(show_stdout="{}"), "empty-sidecar"),
        (Lore(show_stdout=_show_doc(sidecar={"kind": "task"})), "malformed-sidecar"),
        (Lore(show_stdout=_show_doc(revision=None)), "malformed-sidecar"),
        (Lore(show_stdout=_show_doc(sidecar={"kind": "adr", "status": "ready"})), "malformed-sidecar"),
        (Lore(raises=FileNotFoundError("lore")), "lore-missing"),
        (Lore(raises=subprocess.TimeoutExpired(["lore"], 1)), "lore-timeout"),
        (Lore(tasks={}), "followup-not-found"),
    ],
)
def test_a_failed_follow_up_read_exits_two_with_a_reason_and_nothing_on_stdout(
    runner, reason, capsys
):
    code, out, err = run_main(runner, "check", "s", "f", capsys=capsys)
    assert code == 2
    assert out == ""
    assert reason_of(err) == reason
    assert reason in parent.REASON_CODES
    assert "SECRET" not in err
    assert "Traceback" not in err


def test_one_failed_read_among_good_ones_leaves_stdout_empty(capsys):
    runner = Lore(tasks={"task/good": task_doc()})
    code, out, err = run_main(runner, "check", "s", "good", "missing", capsys=capsys)
    assert code == 2 and out == ""
    assert reason_of(err) == "followup-not-found"


@pytest.mark.parametrize(
    "argv",
    [
        ["check", "s", "has space"],
        ["check", "s", "a$(touch x)"],
        ["check", "s", "../x"],
        ["check", "s", "ok", "a;b"],
        ["check", "s", "a\nb"],
    ],
)
def test_an_invalid_follow_up_name_is_refused_before_any_lore_call(argv, capsys):
    runner = Lore()
    code, out, err = run_main(runner, *argv, capsys=capsys)
    assert code == 2 and out == ""
    assert reason_of(err) == "invalid-followup-name"
    assert runner.calls == []


def test_an_invalid_spec_name_is_refused_before_any_lore_call(capsys):
    runner = Lore()
    code, out, err = run_main(runner, "check", "bad name", "f", capsys=capsys)
    assert code == 2
    assert reason_of(err) == "invalid-spec-name"
    assert runner.calls == []


@pytest.mark.parametrize("argv", [[], ["check"], ["check", "s"], ["nope", "s", "f"]])
def test_missing_or_unknown_arguments_are_invalid_arguments(argv, capsys):
    runner = Lore()
    code, out, err = run_main(runner, *argv, capsys=capsys)
    assert code == 2 and out == ""
    assert reason_of(err) == "invalid-arguments"
    assert runner.calls == []


def test_a_dash_name_after_the_separator_is_refused_as_invalid(capsys):
    runner = Lore()
    code, _, err = run_main(runner, "check", "s", "--", "-rf", capsys=capsys)
    assert code == 2
    assert reason_of(err) == "invalid-followup-name"
    assert runner.calls == []


def test_an_unexpected_exception_exits_two_internal_error(capsys):
    code, out, err = run_main(Lore(raises=RuntimeError("SECRET boom")), "check", "s", "f", capsys=capsys)
    assert code == 2 and out == ""
    assert reason_of(err) == "internal-error"
    assert "SECRET" not in err


def test_a_depends_on_entry_of_an_unsafe_shape_leaves_that_follow_up_out_without_a_dependency_key():
    runner = Lore(tasks={
        "task/f": task_doc(**{"depends-on": ["-flag"]}),
        "task/free": task_doc(),
    })
    result = parent.check_followups(runner, "s", ["f", "free"])
    assert [i["name"] for i in result["eligible"]] == ["free"]
    assert result["left-out"] == [{"name": "f", "reason": "depends-outside"}]


@pytest.mark.parametrize("entries", ["task-x", ["ok", 3], {"a": "b"}])
def test_a_depends_on_that_is_not_a_list_of_strings_is_a_malformed_record(entries, capsys):
    runner = Lore(tasks={"task/f": task_doc(**{"depends-on": entries})})
    code, out, err = run_main(runner, "check", "s", "f", capsys=capsys)
    assert code == 2 and out == ""
    assert reason_of(err) == "malformed-sidecar"


def test_a_dependency_cycle_inside_the_set_is_left_out(capsys):
    runner = Lore(tasks={
        "task/one": task_doc(**{"depends-on": ["two"]}),
        "task/two": task_doc(**{"depends-on": ["one"]}),
        "task/free": task_doc(),
    })
    result = parent.check_followups(runner, "s", ["one", "two", "free"])
    assert [i["name"] for i in result["eligible"]] == ["free"]
    assert result["left-out"] == [
        {"name": "one", "reason": "depends-outside", "dependency": "two"},
        {"name": "two", "reason": "depends-outside", "dependency": "one"},
    ]


def test_a_qualified_dependency_is_unfinished_so_the_follow_up_is_left_out_with_the_entry_as_written():
    runner = Lore(tasks={"task/f": task_doc(**{"depends-on": ["task/some-open-thing"]})})
    result = parent.check_followups(runner, "s", ["f"])
    assert result["eligible"] == []
    assert result["left-out"] == [
        {"name": "f", "reason": "depends-outside", "dependency": "task/some-open-thing"}
    ]


def test_a_qualified_dependency_is_never_looked_up_as_a_task():
    runner = Lore(tasks={
        "task/f": task_doc(**{"depends-on": ["task/other"]}),
        "task/task/other": task_doc(status="done"),
    })
    result = parent.check_followups(runner, "s", ["f"])
    assert result["eligible"] == []


@pytest.mark.parametrize("entry", ["spec/x@stage", "task/../x", "task/has space", "-task/x"])
def test_a_qualified_entry_failing_the_shape_check_leaves_the_follow_up_out_with_no_dependency_key(entry):
    runner = Lore(tasks={"task/f": task_doc(**{"depends-on": [entry]})})
    result = parent.check_followups(runner, "s", ["f"])
    assert result["eligible"] == []
    assert result["left-out"] == [{"name": "f", "reason": "depends-outside"}]


# --- the importable seam later subcommands rely on ----------------------------------------------


def test_check_followups_returns_the_structure_the_json_emits(capsys):
    runner = Lore(tasks={"task/good": task_doc(), "task/bad": task_doc(status="blocked")})
    result = parent.check_followups(runner, "s", ["good", "bad"])
    assert set(result) == {"eligible", "left-out", "overlaps"}
    assert [i["name"] for i in result["eligible"]] == ["good"]
    assert result["left-out"] == [{"name": "bad", "reason": "not-ready"}]
    code, out, _ = run_main(runner, "check", "s", "good", "bad", capsys=capsys)
    assert code == 0
    shown = json.loads(out)
    assert shown.pop("confirm") == parent.check_confirmation("s", result["eligible"], None, None)
    assert shown == result


def test_read_revision_returns_the_revision_lore_reports():
    runner = Lore(tasks={"task/f": task_doc(revision="abc123")})
    assert parent.read_revision(runner, "v", "f") == "abc123"


def test_read_revision_of_a_missing_record_is_followup_not_found():
    with pytest.raises(parent.ParentError) as err:
        parent.read_revision(Lore(), "v", "gone")
    assert err.value.reason == "followup-not-found"


def test_every_left_out_reason_the_script_emits_is_a_member_of_the_constant(lore_env, spec):
    env, vault = lore_env
    holder = make_followup(env, vault, "Holder", status="open", spec=spec)
    made = {
        "unresolved-question": make_followup(env, vault, "U", status="open", body=UNRESOLVED_BODY, spec=spec),
        "routed-to-plan": make_followup(env, vault, "P", status="open", labels=["route=plan"], spec=spec),
        "routed-to-brainstorm": make_followup(env, vault, "B", status="open", labels=["route=brainstorm"], spec=spec),
        "not-ready": make_followup(env, vault, "N", status="blocked", spec=spec),
        "already-nested": make_followup(env, vault, "A", parent=holder, spec=spec),
        "is-a-parent": make_followup(env, vault, "I", labels=["craft/followup-parent"], spec=spec),
        "depends-outside": make_followup(env, vault, "D", depends=[holder], spec=spec),
        "not-spec-linked": make_followup(env, vault, "L"),
    }
    result = check_ok(env, spec, *made.values())
    emitted = {i["name"]: i["reason"] for i in result["left-out"]}
    assert emitted == {name: reason for reason, name in made.items()}
    # `not-found` needs a --parent listing, pinned by its own check --parent tests.
    assert set(emitted.values()) == parent.LEFT_OUT_REASONS - {"not-found"}


def test_an_emitted_left_out_reason_outside_the_constant_is_refused():
    with pytest.raises(AssertionError):
        parent._left_out("x", "made-up-reason")


# === assemble ==========================================================================

_SCRIPTS = _SCRIPT.parent
GATHER = _SCRIPTS / "followup_gather.py"
GATE = _SCRIPTS / "observation_gate.py"
TEMPLATE = _SCRIPTS.parent / "templates" / "plan.md"
DAY = datetime.date(2026, 10, 16)
LABEL = "craft/followup-parent"


class EnvRunner:
    """The real `lore` CLI against a throwaway vault, with an optional fault hook.

    `hook(argv, stdin, runner)` runs before every call; a CompletedProcess it returns
    replaces the call, None lets the real `lore` run (the hook may do side effects)."""

    def __init__(self, env, hook=None):
        self.env, self.hook, self.calls = env, hook, []

    def __call__(self, argv, stdin=None):
        self.calls.append(list(argv))
        if self.hook is not None:
            early = self.hook(list(argv), stdin, self)
            if early is not None:
                return early
        return self.real(argv, stdin)

    def real(self, argv, stdin=None):
        kwargs = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
        return subprocess.run(
            ["lore", *argv], capture_output=True, text=True, env=self.env, **kwargs
        )

    def writes(self):
        return [a for a in self.calls if a[:2] in (["record", "create"], ["record", "update"])]


def refused(argv, rc=1):
    return subprocess.CompletedProcess(argv, rc, "", "lore: injected failure")


def is_nest(argv, name=None):
    return (
        argv[:2] == ["record", "update"]
        and "--parent" in argv
        and (name is None or argv[2] == f"task/{name}")
    )


def is_unnest(argv, name=None):
    return (
        argv[:2] == ["record", "update"]
        and "--unset-parent" in argv
        and (name is None or argv[2] == f"task/{name}")
    )


def is_flip(argv):
    return argv[:2] == ["record", "update"] and "--status" in argv and "ready" in argv


def is_drop(argv):
    return argv[:2] == ["record", "update"] and "--status" in argv and "dropped" in argv


def make_children(env, vault, spec, count=3, prefix="Child"):
    return [make_followup(env, vault, f"{prefix} {i}", spec=spec) for i in range(count)]


def confirm(env, vault, names):
    return [
        {"name": n, "reason": f"worth doing: {n}", "revision": shown_revision(env, vault, n)}
        for n in names
    ]


def nest_code(spec_name, children):
    """The code `check --reasons` prints for exactly these children."""
    reasons = {c["name"]: c["reason"] for c in children}
    return parent.check_confirmation(spec_name, children, None, reasons)


def run_assemble(tmp_path, runner, spec, children, capsys, today=DAY, typed="auto"):
    path = tmp_path / "children.json"
    path.write_text(json.dumps(children))
    typed = nest_code(spec, children) if typed == "auto" else typed
    argv = ["assemble", spec, "--children", str(path)] + (["--confirm", typed] if typed else [])
    code = parent.main(argv, runner=runner, today=today)
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def sidecar_of(env, vault, name):
    return json.loads(_lore(env, "record", "show", "--json", "--vault", vault, f"task/{name}"))["sidecar"]


def body_of(env, vault, name):
    return json.loads(_lore(env, "record", "show", "--json", "--vault", vault, f"task/{name}"))["body"]


def parents_of(env, vault, names):
    return [sidecar_of(env, vault, n).get("parent") for n in names]


def nest(env, vault, name, holder):
    _lore(env, "record", "update", f"task/{name}", "--vault", vault, "--parent", holder)


def reason_codes(err):
    return [ln for ln in err.splitlines() if ln.startswith("reason-code: ")]


def vault_snapshot(env, vault):
    listing = json.loads(_lore(env, "task", "list", "--vault", vault, "--json"))
    return {
        item["name"]: _lore(env, "record", "show", "--json", "--vault", vault, f"task/{item['name']}")
        for item in listing
    }


def gather_json(env, spec):
    r = subprocess.run([sys.executable, str(GATHER), spec], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def gathered_names(env, spec):
    return sorted(item["name"] for item in gather_json(env, spec)["followups"])


# --- happy path and its observable seams ------------------------------------------------------


def test_assembling_three_children_leaves_a_ready_labelled_parent_with_all_nested(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    code, out, err = run_assemble(tmp_path, EnvRunner(env), spec, confirm(env, vault, names), capsys)
    assert code == 0, err
    name = out["parent"]
    side = sidecar_of(env, vault, name)
    assert side["status"] == "ready"
    assert LABEL in side["labels"]
    assert spec in side["related"]["spec"]
    assert side["title"] == f"Build the follow-ups of {spec} chosen 2026-10-16"
    assert [sidecar_of(env, vault, n)["parent"] for n in names] == [name] * 3
    assert out["nested"] == names
    assert out["failed"] == [] and out["undone"] == [] and out["undo-failed"] == []
    assert out["buildable"] is True


def test_the_default_runner_assembles_through_the_cli_as_a_subprocess(  # inert-gate: allow seam smoke
    lore_env, spec, tmp_path
):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=2)
    children = confirm(env, vault, names)
    path = tmp_path / "children.json"
    path.write_text(json.dumps(children))
    reasons = tmp_path / "reasons.json"
    reasons.write_text(json.dumps({c["name"]: c["reason"] for c in children}))
    checked = subprocess.run(
        [sys.executable, str(_SCRIPT), "check", spec, *names, "--reasons", str(reasons)],
        capture_output=True, text=True, env=env,
    )
    assert checked.returncode == 0, checked.stderr
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "assemble", spec, "--children", str(path),
         "--confirm", json.loads(checked.stdout)["confirm"]],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["buildable"] is True
    assert sidecar_of(env, vault, out["parent"])["status"] == "ready"


def test_lore_task_graph_renders_the_parent_with_its_three_children_runnable(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    _, out, _ = run_assemble(tmp_path, EnvRunner(env), spec, confirm(env, vault, names), capsys)
    graph = _lore(env, "task", "graph", out["parent"], "--vault", vault).splitlines()
    assert graph[0] == f"{out['parent']} [ready]"
    assert sorted(line.strip() for line in graph[1:]) == sorted(f"{n} [ready] (runnable)" for n in names)


def test_the_stored_body_has_no_coverage_field_and_one_section_per_child_with_its_reason(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    children = confirm(env, vault, names)
    _, out, _ = run_assemble(tmp_path, EnvRunner(env), spec, children, capsys)
    body = body_of(env, vault, out["parent"])
    assert "**Covers:**" not in body and "**Partially covers:**" not in body
    for child in children:
        section = body.split(f"### task/{child['name']}\n", 1)[1].split("\n###", 1)[0]
        assert f"**Selection reason:** {child['reason']}" in section
    assert body.count("### task/") == 3


def template_flow_out(path=TEMPLATE):
    text = path.read_text()
    return text[text.index("## Flow-out") :]


def test_the_body_flow_out_section_is_the_templates_section():
    body = parent.render_body("s", [{"name": "a", "reason": "r"}])
    assert body[body.index("## Flow-out") :] == template_flow_out()


def test_the_flow_out_section_is_read_from_the_template_at_run_time(tmp_path, monkeypatch):
    other = tmp_path / "plan.md"
    other.write_text("# T\n\nintro\n\n## Flow-out\n\n- [ ] a different checklist item\n")
    monkeypatch.setattr(parent, "TEMPLATE_PATH", other)
    body = parent.render_body("s", [{"name": "a", "reason": "r"}])
    assert body.endswith("## Flow-out\n\n- [ ] a different checklist item\n")
    assert "Touched area/subsystem" not in body


def test_the_open_slice_query_misses_the_parent_and_gather_lists_it_without_its_children(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    slice_name = make_followup(env, vault, "A slice", status="in-progress", labels=["craft/slice-parent"], spec=spec)
    assert gathered_names(env, spec) == sorted(names)
    _, out, _ = run_assemble(tmp_path, EnvRunner(env), spec, confirm(env, vault, names), capsys)
    query = (
        f"kind:task related-spec:{spec} has:label.craft.slice-parent "
        "-status:done -status:dropped -status:superseded"
    )
    hits = json.loads(_lore(env, "search", query, "--json"))["hits"]
    ids = [h["id"] for h in hits]
    assert any(i.endswith(f"/task/{slice_name}") for i in ids)
    assert not any(i.endswith(f"/task/{out['parent']}") for i in ids)
    gathered = gather_json(env, spec)
    assert gathered["followup-parents"] == [out["parent"]]
    assert gathered["followups"] == []


# --- the write order --------------------------------------------------------------------------


def test_the_writes_run_in_the_fixed_order_with_the_requery_and_revision_reads_between(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    runner = EnvRunner(env)
    _, out, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    name = out["parent"]
    writes = runner.writes()
    create = writes[0]
    assert create[:2] == ["record", "create"]
    assert ["--status", "open"] == create[create.index("--status") : create.index("--status") + 2]
    assert ["--label", LABEL] == create[create.index("--label") : create.index("--label") + 2]
    assert ["--related", f"spec={spec}"] == create[create.index("--related") : create.index("--related") + 2]
    assert [w[2] for w in writes[1:4]] == [f"task/{n}" for n in names]
    assert all(is_nest(w) and w[w.index("--parent") + 1] == name for w in writes[1:4])
    assert is_flip(writes[4]) and writes[4][2] == f"task/{name}" and len(writes) == 5

    calls = runner.calls
    created_at = calls.index(create)
    nest_idx = [calls.index(w) for w in writes[1:4]]
    searches = [i for i, c in enumerate(calls) if c[0] == "search" and i > created_at]
    assert searches and searches[0] < nest_idx[0]
    for child, idx in zip(names, nest_idx):
        show = ["record", "show", "--json", "--vault", vault, f"task/{child}"]
        assert calls[idx - 1] == show
        assert calls[idx + 1] == show
    assert calls.index(writes[4]) > nest_idx[-1] + 1


def test_the_parent_stays_open_until_every_child_is_confirmed_nested(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    seen = []

    def hook(argv, stdin, runner):
        if is_flip(argv):
            seen.append(parents_of(env, vault, names))
            seen.append(sidecar_of(env, vault, argv[2].removeprefix("task/"))["status"])

    run_assemble(tmp_path, EnvRunner(env, hook), spec, confirm(env, vault, names), capsys)
    parent_name = sidecar_of(env, vault, names[0])["parent"]
    assert seen == [[parent_name] * 3, "open"]


# --- the reason text --------------------------------------------------------------------------


def body_for(reason):
    return parent.render_body("s", [{"name": "kid", "reason": reason}])


def reason_line(body):
    return next(ln for ln in body.splitlines() if ln.startswith("**Selection reason:** "))


def covers_gate(body):
    return subprocess.run(
        [sys.executable, str(GATE)], input=body, capture_output=True, text=True
    )


def test_a_credential_shaped_token_in_a_reason_is_redacted():
    body = body_for("rotate AKIAIOSFODNN7EXAMPLE first")
    assert "AKIAIOSFODNN7EXAMPLE" not in body
    assert reason_line(body) == "**Selection reason:** rotate [REDACTED] first"


def test_control_characters_in_a_reason_are_stripped():
    assert reason_line(body_for("bell\x07 esc\x1b[31m red\x00")) == "**Selection reason:** bell esc31m red"


def test_invisible_format_characters_in_a_reason_are_stripped():
    assert reason_line(body_for("zero​width ‮flip⁠")) == "**Selection reason:** zerowidth flip"


@pytest.mark.parametrize(
    "reason",
    ["first\n**Covers:** AC1\nsecond", "first\r\n**Partially covers:** AC2", "x\n## Flow-out\n- [ ] forged"],
)
def test_a_reason_with_newlines_cannot_start_a_line_of_its_own(reason):
    body = body_for(reason)
    lines = body.splitlines()
    assert [ln for ln in lines if ln.startswith(("**Covers:**", "**Partially covers:**"))] == []
    assert lines.count("## Flow-out") == 1
    assert not any(ln.startswith("- [ ] forged") for ln in lines)
    assert reason_line(body).startswith("**Selection reason:** ")


def test_newlines_in_a_reason_become_single_spaces_between_its_words():
    assert reason_line(body_for("first\n**Covers:** AC1\r\nsecond")) == (
        "**Selection reason:** first **Covers:** AC1 second"
    )


def test_a_reason_carrying_a_newline_and_a_covers_field_passes_the_real_covers_gate():
    gate = covers_gate(body_for("first\n**Covers:** AC1\nsecond"))
    assert gate.returncode == 0, gate.stderr


def test_the_covers_gate_does_see_a_covers_field_that_is_on_a_line_of_its_own():
    gate = covers_gate(body_for("first") + "\n**Covers:** AC1\n")
    assert gate.returncode == 1


def test_a_reason_over_the_bound_is_cut_with_a_marker_and_one_at_the_bound_is_kept():
    limit = parent.MAX_REASON_CHARS
    text = "lorem " * 200
    assert not text[:limit].endswith(" ") and len(text) > limit
    assert reason_line(body_for(text[:limit])) == "**Selection reason:** " + text[:limit]
    assert reason_line(body_for(text)) == "**Selection reason:** " + text[:limit] + "…"


# --- refusals before any write ----------------------------------------------------------------


def stderr_lines(err):
    return err.splitlines()


def assert_refused(code, out, err, runner, reason, subject, expected_code=1):
    assert code == expected_code and out is None
    assert [ln for ln in stderr_lines(err) if ln.startswith("reason-code: ")] == [f"reason-code: {reason}"]
    assert f"subject: {subject}" in stderr_lines(err)
    assert runner.writes() == []


def test_a_child_that_is_no_longer_ready_is_refused_and_the_vault_is_unchanged(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    children = confirm(env, vault, names)
    _lore(env, "record", "update", f"task/{names[1]}", "--vault", vault, "--status", "blocked")
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_assemble(tmp_path, runner, spec, children, capsys)
    assert_refused(code, out, err, runner, "child-not-eligible", names[1])
    assert vault_snapshot(env, vault) == before


def test_a_child_nested_elsewhere_since_the_check_is_not_eligible(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=2)
    children = confirm(env, vault, names)
    holder = make_followup(env, vault, "Holder", status="open")
    nest(env, vault, names[0], holder)
    runner = EnvRunner(env)
    code, out, err = run_assemble(tmp_path, runner, spec, children, capsys)
    assert_refused(code, out, err, runner, "child-not-eligible", names[0])


def test_a_child_edited_since_the_check_is_refused_and_the_vault_is_unchanged(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    children = confirm(env, vault, names)
    _lore(env, "record", "update", f"task/{names[2]}", "--vault", vault, stdin=CLEAN + "\nA later edit.\n")
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_assemble(tmp_path, runner, spec, children, capsys)
    assert_refused(code, out, err, runner, "child-changed", names[2])
    assert vault_snapshot(env, vault) == before


@pytest.mark.parametrize("live_status", ["open", "ready", "in-progress", "blocked"])
def test_a_live_follow_up_parent_linked_to_the_spec_is_refused_and_the_vault_is_unchanged(
    lore_env, spec, tmp_path, capsys, live_status
):
    env, vault = lore_env
    existing = make_followup(env, vault, "Existing parent", status=live_status, labels=[LABEL], spec=spec)
    names = make_children(env, vault, spec)
    children = confirm(env, vault, names)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_assemble(tmp_path, runner, spec, children, capsys)
    assert_refused(code, out, err, runner, "followup-parent-exists", existing)
    assert vault_snapshot(env, vault) == before


# --- failed nesting, failed flip: roll forward; concurrent parent: undo -----------------------


def assert_rolled_forward(out, runner):
    """The report of a roll-forward after a failure: nothing undone, no un-nest or
    drop write attempted, nothing buildable."""
    assert out["buildable"] is False
    assert out["undone"] == [] and out["undo-failed"] == []
    assert not any(is_unnest(call) or is_drop(call) for call in runner.calls)


def test_a_failed_second_nesting_leaves_the_parent_open_holding_the_first_child(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    children = confirm(env, vault, names)
    runner = EnvRunner(env, lambda a, s, r: refused(a) if is_nest(a, names[1]) else None)
    code, out, err = run_assemble(tmp_path, runner, spec, children, capsys)
    assert code == 2
    assert not any(is_flip(call) for call in runner.calls)
    assert reason_codes(err) == ["reason-code: nesting-incomplete"]
    assert_rolled_forward(out, runner)
    assert out["failed"] == [{"name": names[1], "write": "nest"}]
    assert out["nested"] == [names[0]]
    assert parents_of(env, vault, names) == [out["parent"], None, None]
    assert sidecar_of(env, vault, out["parent"])["status"] == "open"
    assert "Dropped" not in body_of(env, vault, out["parent"])
    _, state, _, _ = run_state(EnvRunner(env), spec, out["parent"], capsys)
    assert [(h["name"], h["status"]) for h in state["held"]] == [(names[0], "ready")]
    assert state["unheld"] == names[1:]
    assert gather_json(env, spec)["followup-parents"] == [out["parent"]]
    assert gathered_names(env, spec) == sorted(names[1:])


def test_a_failed_flip_leaves_the_parent_open_holding_all_three(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    runner = EnvRunner(env, lambda a, s, r: refused(a) if is_flip(a) else None)
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert "reason-code: nesting-incomplete" in err.splitlines()
    assert_rolled_forward(out, runner)
    assert out["failed"] == [{"name": out["parent"], "write": "ready"}]
    assert out["nested"] == names
    assert parents_of(env, vault, names) == [out["parent"]] * 3
    assert sidecar_of(env, vault, out["parent"])["status"] == "open"
    assert gather_json(env, spec)["followup-parents"] == [out["parent"]]


def test_a_nesting_that_reads_back_unnested_is_a_failed_nesting(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec)

    def hook(argv, stdin, runner):
        if is_nest(argv, names[1]):
            return subprocess.CompletedProcess(argv, 0, "", "")

    runner = EnvRunner(env, hook)
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert out["failed"] == [{"name": names[1], "write": "confirm"}]
    assert_rolled_forward(out, runner)
    assert out["nested"] == [names[0]]
    assert sidecar_of(env, vault, out["parent"])["status"] == "open"
    assert sidecar_of(env, vault, names[0])["parent"] == out["parent"]


def test_a_nesting_that_reads_back_under_a_different_parent_is_a_failed_nesting(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    holder = make_followup(env, vault, "Other holder", status="open")

    def hook(argv, stdin, runner):
        if is_nest(argv, names[1]):
            return runner.real(_swap_parent(argv, holder))

    runner = EnvRunner(env, hook)
    code, out, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert out["failed"] == [{"name": names[1], "write": "confirm"}]
    assert_rolled_forward(out, runner)
    assert sidecar_of(env, vault, out["parent"])["status"] == "open"
    assert sidecar_of(env, vault, names[1])["parent"] == holder
    assert sidecar_of(env, vault, names[0])["parent"] == out["parent"]
    assert sidecar_of(env, vault, names[2]).get("parent") is None


def _swap_parent(argv, holder):
    swapped = list(argv)
    swapped[swapped.index("--parent") + 1] = holder
    return swapped


def test_a_child_edited_between_the_pre_write_check_and_its_nesting_is_a_failed_nesting(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    state = {"created": False, "edited": False}

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "create"]:
            state["created"] = True
        if state["created"] and not state["edited"] and argv[:3] == ["record", "show", "--json"] \
                and argv[-1] == f"task/{names[1]}":
            state["edited"] = True
            _lore(env, "record", "update", f"task/{names[1]}", "--vault", vault, stdin=CLEAN + "\nRaced edit.\n")

    runner = EnvRunner(env, hook)
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert_rolled_forward(out, runner)
    assert out["failed"] == [{"name": names[1], "write": "nest"}]
    assert not any(is_nest(w, names[1]) for w in runner.calls)
    assert parents_of(env, vault, names) == [out["parent"], None, None]
    assert sidecar_of(env, vault, out["parent"])["status"] == "open"


def test_a_flip_that_reads_back_not_ready_is_a_failed_flip(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec)

    def hook(argv, stdin, runner):
        if is_flip(argv):
            return subprocess.CompletedProcess(argv, 0, "", "")

    runner = EnvRunner(env, hook)
    code, out, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert_rolled_forward(out, runner)
    assert out["failed"] == [{"name": out["parent"], "write": "ready"}]
    assert sidecar_of(env, vault, out["parent"])["status"] == "open"
    assert parents_of(env, vault, names) == [out["parent"]] * 3


def test_a_nesting_whose_lore_call_times_out_is_a_failed_nesting_and_rolls_forward(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)

    def hook(argv, stdin, runner):
        if is_nest(argv, names[1]):
            raise subprocess.TimeoutExpired(argv, 1)

    runner = EnvRunner(env, hook)
    code, out, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert_rolled_forward(out, runner)
    assert out["failed"] == [{"name": names[1], "write": "nest"}]
    assert parents_of(env, vault, names) == [out["parent"], None, None]
    assert sidecar_of(env, vault, out["parent"])["status"] == "open"


def test_an_unexpected_exception_after_the_create_leaves_the_parent_open_and_reports_it(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)

    def hook(argv, stdin, runner):
        if is_nest(argv, names[1]):
            raise RuntimeError("SECRET boom")

    runner = EnvRunner(env, hook)
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert reason_codes(err) == ["reason-code: internal-error"]
    assert "SECRET" not in err
    assert_rolled_forward(out, runner)
    assert out["nested"] == [names[0]]
    (made,) = created_parent(env, vault, spec)
    assert out["parent"] == made
    assert sidecar_of(env, vault, made)["status"] == "open"
    assert parents_of(env, vault, names) == [made, None, None]


def created_parent(env, vault, spec):
    query = f"kind:task related-spec:{spec} has:label.craft.followup-parent"
    hits = json.loads(_lore(env, "search", query, "--json"))["hits"]
    return sorted(h["id"].rsplit("/task/", 1)[1] for h in hits)


def test_a_failed_flip_attempts_no_un_nest_even_when_every_un_nest_would_fail(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    runner = EnvRunner(env, lambda a, s, r: refused(a) if is_unnest(a) or is_flip(a) else None)
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert_rolled_forward(out, runner)
    assert parents_of(env, vault, names) == [out["parent"]] * 3


def test_a_refused_nesting_attempts_no_un_nest_even_when_an_un_nest_would_read_back_nested(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)

    def hook(argv, stdin, runner):
        if is_unnest(argv, names[0]):
            return subprocess.CompletedProcess(argv, 0, "", "")
        if is_nest(argv, names[1]):
            return refused(argv)

    runner = EnvRunner(env, hook)
    code, out, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert_rolled_forward(out, runner)
    assert sidecar_of(env, vault, names[0])["parent"] == out["parent"]


@pytest.mark.parametrize("silent", [False, True], ids=["write-refused", "write-silently-ignored"])
def test_a_failed_drop_of_a_concurrent_parent_is_reported_with_the_command_that_completes_it(
    lore_env, spec, tmp_path, capsys, silent
):
    env, vault = lore_env
    names = make_children(env, vault, spec)

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "create"]:
            done = runner.real(argv, stdin)
            make_followup(env, vault, "Rival parent", status="open", labels=[LABEL], spec=spec)
            return done
        if is_drop(argv):
            return subprocess.CompletedProcess(argv, 0, "", "") if silent else refused(argv)

    code, out, _ = run_assemble(tmp_path, EnvRunner(env, hook), spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert [(f["name"], f["write"]) for f in out["undo-failed"]] == [(out["parent"], "drop")]
    assert {"name": out["parent"], "write": "drop"} not in out["undone"]
    assert sidecar_of(env, vault, out["parent"])["status"] == "open"
    run = subprocess.run(shlex.split(out["undo-failed"][0]["command"]), capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stderr
    assert sidecar_of(env, vault, out["parent"])["status"] == "dropped"


def test_a_concurrent_parent_appearing_after_the_create_undoes_this_runs_parent(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    rival = {}

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "create"]:
            done = runner.real(argv, stdin)
            rival["name"] = make_followup(env, vault, "Rival parent", status="open", labels=[LABEL], spec=spec)
            return done

    code, out, err = run_assemble(tmp_path, EnvRunner(env, hook), spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert reason_codes(err) == [
        "reason-code: concurrent-followup-parent"
    ]
    assert f"subject: {' '.join(sorted([out['parent'], rival['name']]))}" in err.splitlines()
    assert out["buildable"] is False and out["nested"] == []
    assert out["undone"] == [{"name": out["parent"], "write": "drop"}]
    assert sidecar_of(env, vault, out["parent"])["status"] == "dropped"
    assert sidecar_of(env, vault, rival["name"])["status"] == "open"
    assert parents_of(env, vault, names) == [None] * 3


# --- the title, collisions and a create that fails -------------------------------------------


def test_a_second_assembly_on_the_same_day_creates_a_new_parent_and_leaves_the_first_alone(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    first_set = make_children(env, vault, spec, prefix="First")
    _, first, _ = run_assemble(tmp_path, EnvRunner(env), spec, confirm(env, vault, first_set), capsys)
    _lore(env, "record", "update", f"task/{first['parent']}", "--vault", vault, "--status", "dropped")
    frozen = _lore(env, "record", "show", "--json", "--vault", vault, f"task/{first['parent']}")
    second_set = make_children(env, vault, spec, prefix="Second")
    code, second, err = run_assemble(tmp_path, EnvRunner(env), spec, confirm(env, vault, second_set), capsys)
    assert code == 0, err
    assert second["parent"] == f"{first['parent']}-2"
    assert [sidecar_of(env, vault, n)["parent"] for n in second_set] == [second["parent"]] * 3
    assert _lore(env, "record", "show", "--json", "--vault", vault, f"task/{first['parent']}") == frozen


def test_a_parent_name_too_long_for_the_filesystem_is_parent_create_failed_with_nothing_written(
    lore_env, tmp_path, capsys
):
    env, vault = lore_env
    long_spec = make_spec(env, vault, "s" * 205, status="complete")
    names = [make_followup(env, vault, "Only child", spec=long_spec)]
    children = confirm(env, vault, names)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_assemble(tmp_path, runner, long_spec, children, capsys)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: parent-create-failed"]
    assert subject_of(err) == [] and "parent-state: unknown" not in err.splitlines()
    assert vault_snapshot(env, vault) == before
    assert [w[:2] for w in runner.writes()] == [["record", "create"]]


def test_a_create_that_exits_nonzero_is_parent_create_failed_whatever_it_printed(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "create"]:
            return subprocess.CompletedProcess(argv, 1, "task/looks-fine\n", "")

    runner = EnvRunner(env, hook)
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2 and out is None
    assert "reason-code: parent-create-failed" in err.splitlines()
    assert [w[:2] for w in runner.writes()] == [["record", "create"]]


def test_a_create_that_prints_something_other_than_a_task_name_is_parent_create_failed(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "create"]:
            return subprocess.CompletedProcess(argv, 0, "task/../../etc\n", "")

    runner = EnvRunner(env, hook)
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2 and out is None
    assert "reason-code: parent-create-failed" in err.splitlines()
    assert [w[:2] for w in runner.writes()] == [["record", "create"]]


# --- a qualified depends-on entry -------------------------------------------------------------


def edited_depends(name, entries):
    """A hook that makes `task/<name>` read back with `entries` as its depends-on, as an
    edited or synced sidecar would (the CLI refuses to write a qualified entry)."""

    def hook(argv, stdin, runner):
        if argv[:3] == ["record", "show", "--json"] and argv[-1] == f"task/{name}":
            doc = json.loads(runner.real(argv).stdout)
            doc["sidecar"]["depends-on"] = entries
            return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")

    return hook


def test_assemble_refuses_a_follow_up_whose_sidecar_depends_on_a_qualified_entry(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    children = confirm(env, vault, names)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env, edited_depends(names[1], ["task/some-open-thing"]))
    code, out, err = run_assemble(tmp_path, runner, spec, children, capsys)
    assert_refused(code, out, err, runner, "child-not-eligible", names[1])
    assert vault_snapshot(env, vault) == before


def test_check_leaves_out_a_follow_up_whose_sidecar_depends_on_a_qualified_entry(lore_env, spec):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=2)
    runner = EnvRunner(env, edited_depends(names[0], ["task/some-open-thing"]))
    result = parent.check_followups(runner, spec, names)
    assert [i["name"] for i in result["eligible"]] == [names[1]]
    assert result["left-out"] == [
        {"name": names[0], "reason": "depends-outside", "dependency": "task/some-open-thing"}
    ]


# --- a create whose outcome is unclear ---------------------------------------------------------


def landed_then(outcome):
    """A create hook: the real create runs and lands, then `outcome(argv)` is what the
    caller sees."""

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "create"]:
            runner.real(argv, stdin)
            return outcome(argv)

    return hook


def _timeout(argv):
    raise subprocess.TimeoutExpired(argv, 1)


UNCLEAR_CREATES = [
    pytest.param(_timeout, id="timeout"),
    pytest.param(lambda argv: refused(argv), id="exit-nonzero"),
    pytest.param(lambda argv: subprocess.CompletedProcess(argv, 0, "task/../../etc\n", ""), id="garbled-stdout"),
]


def title_of(day=DAY, spec_name=None):
    return f"Build the follow-ups of {spec_name} chosen {day.isoformat()}"


def subject_of(err):
    lines = [ln for ln in err.splitlines() if ln.startswith("subject: ")]
    assert len(lines) <= 1, err
    return lines[0].removeprefix("subject: ").split() if lines else []


@pytest.mark.parametrize("outcome", UNCLEAR_CREATES)
def test_a_create_that_landed_but_looked_failed_is_left_live_and_named_with_nothing_dropped(
    lore_env, spec, tmp_path, capsys, outcome
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    runner = EnvRunner(env, landed_then(outcome))
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: parent-create-failed"]
    (made,) = created_parent(env, vault, spec)
    assert subject_of(err) == [made]
    assert sidecar_of(env, vault, made)["status"] == "open"
    assert parents_of(env, vault, names) == [None] * 3
    assert not any(is_drop(a) or is_nest(a) for a in runner.calls)


def test_a_create_that_timed_out_without_landing_is_parent_create_failed_with_nothing_written(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    before = vault_snapshot(env, vault)

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "create"]:
            _timeout(argv)

    code, out, err = run_assemble(tmp_path, EnvRunner(env, hook), spec, confirm(env, vault, names), capsys)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: parent-create-failed"]
    assert vault_snapshot(env, vault) == before


def test_a_failed_create_leaves_alone_a_follow_up_parent_another_run_made_meanwhile(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    rival = {}

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "create"]:
            rival["name"] = make_followup(env, vault, "Rival parent", status="open", labels=[LABEL], spec=spec)
            return refused(argv)

    code, out, err = run_assemble(tmp_path, EnvRunner(env, hook), spec, confirm(env, vault, names), capsys)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: parent-create-failed"]
    assert sidecar_of(env, vault, rival["name"])["status"] == "open"
    assert subject_of(err) == []


def test_a_rival_with_this_runs_title_appearing_during_a_failed_create_is_left_live_and_named(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    rival = {}

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "create"]:
            rival["name"] = make_followup(
                env, vault, title_of(spec_name=spec), status="open", labels=[LABEL], spec=spec
            )
            return _timeout(argv)

    runner = EnvRunner(env, hook)
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: parent-create-failed"]
    assert subject_of(err) == [rival["name"]]
    assert sidecar_of(env, vault, rival["name"])["status"] == "open"
    assert not any(is_drop(a) or is_nest(a) for a in runner.calls)


def test_a_failed_create_names_every_live_parent_carrying_this_runs_title_and_only_those(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    made = {}

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "create"]:
            same = title_of(spec_name=spec)
            made["same"] = [
                make_followup(env, vault, same, status=st, labels=[LABEL], spec=spec)
                for st in ("open", "ready")
            ]
            make_followup(env, vault, "Other title", status="open", labels=[LABEL], spec=spec)
            return refused(argv)

    code, out, err = run_assemble(tmp_path, EnvRunner(env, hook), spec, confirm(env, vault, names), capsys)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: parent-create-failed"]
    assert subject_of(err) == sorted(made["same"])


def test_a_failed_requery_after_an_unclear_create_exits_two_with_no_stdout_and_marks_the_state_unknown(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    state = {"armed": False}

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "create"]:
            runner.real(argv, stdin)
            state["armed"] = True
            return _timeout(argv)
        if state["armed"] and argv[0] == "search":
            doc = json.loads(runner.real(argv).stdout)
            doc["truncated"] = True
            return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")

    runner = EnvRunner(env, hook)
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: search-truncated"]
    assert "parent-state: unknown" in err.splitlines()
    assert not any(is_drop(a) or is_nest(a) for a in runner.calls)
    (made,) = created_parent(env, vault, spec)
    assert sidecar_of(env, vault, made)["status"] == "open"


def test_a_failure_before_the_create_does_not_mark_the_parent_state_unknown(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    runner = EnvRunner(env, truncated_searches(after_create=False))
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2 and out is None
    assert "parent-state: unknown" not in err.splitlines()


# --- a failure after the create never touches a child another run moved -------------------------


def test_a_flip_failure_leaves_a_child_another_run_moved_under_a_different_parent_alone(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    holder = make_followup(env, vault, "Other holder", status="open")

    def hook(argv, stdin, runner):
        if is_flip(argv):
            _lore(env, "record", "update", f"task/{names[0]}", "--vault", vault, "--unset-parent")
            nest(env, vault, names[0], holder)
            return refused(argv)

    runner = EnvRunner(env, hook)
    code, out, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert_rolled_forward(out, runner)
    assert sidecar_of(env, vault, names[0])["parent"] == holder
    assert parents_of(env, vault, names[1:]) == [out["parent"]] * 2
    assert sidecar_of(env, vault, out["parent"])["status"] == "open"


def test_a_failed_read_before_a_nesting_is_a_failed_nesting_that_writes_nothing_to_that_child(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    state = {"first_nested": False}

    def hook(argv, stdin, runner):
        if is_nest(argv, names[0]):
            state["first_nested"] = True
        if state["first_nested"] and argv[:3] == ["record", "show", "--json"] and argv[-1] == f"task/{names[1]}":
            return refused(argv)

    runner = EnvRunner(env, hook)
    code, out, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert_rolled_forward(out, runner)
    assert out["failed"] == [{"name": names[1], "write": "nest"}]
    assert out["nested"] == [names[0]]
    assert not any(is_nest(a, names[1]) for a in runner.calls)
    assert sidecar_of(env, vault, names[0])["parent"] == out["parent"]
    assert sidecar_of(env, vault, out["parent"])["status"] == "open"


# --- a gather failure the script maps -----------------------------------------------------------


def truncated_searches(after_create):
    """A hook that makes every `lore search` report a truncated result set, once the
    create has run when `after_create` is set."""
    state = {"armed": not after_create}

    def hook(argv, stdin, runner):
        if argv[:2] == ["record", "create"]:
            done = runner.real(argv, stdin)
            state["armed"] = True
            return done
        if state["armed"] and argv[0] == "search":
            doc = json.loads(runner.real(argv).stdout)
            doc["truncated"] = True
            return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")

    return hook


def test_a_truncated_search_before_the_create_is_search_truncated_with_nothing_written(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env, truncated_searches(after_create=False))
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: search-truncated"]
    assert vault_snapshot(env, vault) == before


def test_a_truncated_search_after_the_create_leaves_the_parent_open_and_reports_search_truncated(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    runner = EnvRunner(env, truncated_searches(after_create=True))
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert reason_codes(err) == ["reason-code: search-truncated"]
    assert_rolled_forward(out, runner)
    assert out["nested"] == []
    assert sidecar_of(env, vault, out["parent"])["status"] == "open"
    assert parents_of(env, vault, names) == [None] * 3


# --- the children file and the arguments ------------------------------------------------------


def assemble_raw(tmp_path, content, capsys, spec_name="s", argv=None):
    runner = Lore()
    path = tmp_path / "children.json"
    if content is not None:
        path.write_text(content)
    code = parent.main(argv or ["assemble", spec_name, "--children", str(path)], runner=runner, today=DAY)
    out = capsys.readouterr()
    return code, out.out, out.err, runner


GOOD = {"name": "a", "reason": "r", "revision": "rev"}


@pytest.mark.parametrize(
    "content",
    [
        None,
        "not json",
        "{}",
        "[]",
        json.dumps([{"name": "a", "reason": "r"}]),
        json.dumps([{"name": "a", "revision": "v"}]),
        json.dumps([{"reason": "r", "revision": "v"}]),
        json.dumps([{**GOOD, "reason": 3}]),
        json.dumps([{**GOOD, "revision": ""}]),
        json.dumps([GOOD, GOOD]),
        json.dumps(["a"]),
    ],
)
def test_a_malformed_children_file_is_refused_before_any_lore_call(tmp_path, capsys, content):
    code, out, err, runner = assemble_raw(tmp_path, content, capsys)
    assert code == 2 and out == ""
    assert reason_of(err) == "invalid-children-file"
    assert runner.calls == []


@pytest.mark.parametrize("name", ["has space", "a$(touch x)", "../x", "-rf", "a;b"])
def test_a_child_name_with_a_hostile_shape_is_refused_before_any_lore_call(tmp_path, capsys, name):
    code, out, err, runner = assemble_raw(tmp_path, json.dumps([{**GOOD, "name": name}]), capsys)
    assert code == 2 and out == ""
    assert reason_of(err) == "invalid-followup-name"
    assert runner.calls == []


def test_a_hostile_spec_name_is_refused_before_any_lore_call(tmp_path, capsys):
    code, out, err, runner = assemble_raw(tmp_path, json.dumps([GOOD]), capsys, spec_name="bad name")
    assert code == 2
    assert reason_of(err) == "invalid-spec-name"
    assert runner.calls == []


@pytest.mark.parametrize("argv", [["assemble", "s"], ["assemble", "--children", "f"], ["assemble"]])
def test_assemble_without_its_spec_or_children_file_is_invalid_arguments(tmp_path, capsys, argv):
    code, out, err, runner = assemble_raw(tmp_path, None, capsys, argv=argv)
    assert code == 2 and out == ""
    assert reason_of(err) == "invalid-arguments"
    assert runner.calls == []


def test_an_unexpected_exception_during_assemble_exits_two_internal_error(tmp_path, capsys):
    path = tmp_path / "children.json"
    path.write_text(json.dumps([GOOD]))
    code = parent.main(
        ["assemble", "s", "--children", str(path), "--confirm", nest_code("s", [GOOD])],
        runner=Lore(raises=RuntimeError("SECRET")),
    )
    out = capsys.readouterr()
    assert code == 2 and out.out == ""
    assert reason_of(out.err) == "internal-error"
    assert "SECRET" not in out.err


# --- the default runner -----------------------------------------------------------------------


def test_the_default_runner_closes_stdin_for_a_metadata_write_and_pipes_a_body_otherwise(monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(parent.subprocess, "run", fake_run)
    parent.lore_runner(["record", "update", "task/x", "--unset-parent"])
    parent.lore_runner(["record", "create", "--kind", "task"], stdin="# body\n")
    (quiet_cmd, quiet), (piped_cmd, piped) = seen
    assert quiet_cmd[0] == "lore" and piped_cmd[:2] == ["lore", "record"]
    assert quiet["stdin"] == subprocess.DEVNULL and "input" not in quiet
    assert piped["input"] == "# body\n" and "stdin" not in piped
    assert quiet["shell"] is False and quiet["timeout"] == piped["timeout"] > 0


# --- state: what a live parent lists and holds -----------------------------------------------


def run_state(runner, spec_name, parent_name, capsys):
    code = parent.main(["state", spec_name, parent_name], runner=runner)
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.out, out.err


def hand_parent(
    env, vault, spec_name, headings, status="open", labels=(LABEL,), related=None, reason="because"
):
    """A parent record written by hand, its body listing `headings` verbatim."""
    sections = "".join(f"### task/{h}\n\n**Selection reason:** {reason}\n\n" for h in headings)
    args = ["record", "create", "--kind", "task", "--title", "Hand parent", "--status", status,
            "--vault", vault, "--related", f"spec={related or spec_name}"]
    for label in labels:
        args += ["--label", label]
    return _lore(env, *args, stdin=f"**Delta design:**\n\n{sections}").split("/", 1)[1]


def unnest(env, vault, name):
    _lore(env, "record", "update", f"task/{name}", "--vault", vault, "--unset-parent")


def test_state_of_an_assembled_parent_with_the_second_child_un_nested(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    _, assembled, _ = run_assemble(tmp_path, EnvRunner(env), spec, confirm(env, vault, names), capsys)
    unnest(env, vault, names[1])
    code, out, _, err = run_state(EnvRunner(env), spec, assembled["parent"], capsys)
    assert code == 0, err
    assert out.pop("drop-confirm") == parent.drop_confirmation(
        spec, parent.parent_state(EnvRunner(env), spec, assembled["parent"]), None
    )
    assert out == {
        "parent": assembled["parent"],
        "status": "ready",
        "listed": names,
        "held": sorted(
            [
                {"name": names[0], "status": "ready", "title": "Child 0", "delivers": "A new widget that frobs."},
                {"name": names[2], "status": "ready", "title": "Child 2", "delivers": "A new widget that frobs."},
            ],
            key=lambda i: i["name"],
        ),
        "unheld": [names[1]],
        "unlisted": [],
        "skipped": 0,
    }


def test_a_task_nested_by_hand_but_not_listed_is_unlisted_and_not_unheld(lore_env, spec, capsys):
    env, vault = lore_env
    (listed,) = make_children(env, vault, spec, count=1)
    stray = make_followup(env, vault, "Stray", spec=spec)
    name = hand_parent(env, vault, spec, [listed])
    nest(env, vault, stray, name)
    code, out, _, err = run_state(EnvRunner(env), spec, name, capsys)
    assert code == 0, err
    assert out["listed"] == [listed]
    assert out["unlisted"] == [stray]
    assert out["unheld"] == [listed]
    assert [h["name"] for h in out["held"]] == [stray]


def test_a_heading_failing_the_shape_check_is_skipped_and_counted(lore_env, spec, capsys):
    env, vault = lore_env
    (good,) = make_children(env, vault, spec, count=1)
    name = hand_parent(env, vault, spec, [good, "bad name"])
    code, out, _, err = run_state(EnvRunner(env), spec, name, capsys)
    assert code == 0, err
    assert out["listed"] == [good]
    assert out["skipped"] == 1


def test_parse_listing_returns_names_in_body_order_and_the_skipped_count():
    body = parent.render_body("s", [
        {"name": "zeta", "reason": "r"}, {"name": "alpha", "reason": "r"},
    ]).replace("### task/alpha", "### task/alpha\n\n### task/bad name\n\n### task/-dash")
    assert parent.parse_listing(body) == (["zeta", "alpha"], 2)
    assert parent.parse_listing("no sections here\n") == ([], 0)


def test_state_refuses_a_record_without_the_marker(lore_env, spec, capsys):
    env, vault = lore_env
    plain = hand_parent(env, vault, spec, [], labels=())
    code, out, raw, err = run_state(EnvRunner(env), spec, plain, capsys)
    assert code == 1 and raw == ""
    assert reason_of(err) == "not-a-followup-parent"
    assert f"subject: {plain}" in err.splitlines()


def test_state_refuses_a_marked_parent_linked_to_a_different_spec(lore_env, spec, capsys):
    env, vault = lore_env
    other = make_spec(env, vault, "Other spec", status="complete")
    elsewhere = hand_parent(env, vault, other, [])
    code, out, raw, err = run_state(EnvRunner(env), spec, elsewhere, capsys)
    assert code == 1 and raw == ""
    assert reason_of(err) == "not-a-followup-parent"
    assert f"subject: {elsewhere}" in err.splitlines()


def test_state_refuses_a_spec_record_named_as_the_parent(lore_env, spec, capsys):
    env, vault = lore_env
    code, out, raw, err = run_state(EnvRunner(env), spec, spec, capsys)
    assert code == 1 and raw == ""
    assert reason_of(err) == "not-a-followup-parent"


def test_a_failed_task_listing_is_lore_failed_with_nothing_on_stdout(lore_env, spec, capsys):
    env, vault = lore_env
    name = hand_parent(env, vault, spec, [])
    runner = EnvRunner(env, lambda a, s, r: refused(a) if a[:2] == ["task", "list"] else None)
    code, out, raw, err = run_state(runner, spec, name, capsys)
    assert code == 2 and raw == ""
    assert reason_of(err) == "lore-failed"


def test_a_nonexistent_parent_is_followup_not_found(lore_env, spec, capsys):
    env, vault = lore_env
    code, out, raw, err = run_state(EnvRunner(env), spec, "no-such-parent", capsys)
    assert code == 2 and raw == ""
    assert reason_of(err) == "followup-not-found"


def test_a_child_whose_complete_read_shows_another_parent_is_not_held(lore_env, spec, capsys):
    env, vault = lore_env
    kept, moved = make_children(env, vault, spec, count=2)
    name = hand_parent(env, vault, spec, [kept, moved])
    for child in (kept, moved):
        nest(env, vault, child, name)

    def hook(argv, stdin, runner):
        if argv[:3] == ["record", "show", "--json"] and argv[-1] == f"task/{moved}":
            doc = json.loads(runner.real(argv).stdout)
            doc["sidecar"]["parent"] = "someone-else"
            return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")

    code, out, _, err = run_state(EnvRunner(env, hook), spec, name, capsys)
    assert code == 0, err
    assert [h["name"] for h in out["held"]] == [kept]
    assert out["unheld"] == [moved]
    assert out["unlisted"] == []


def test_held_children_returns_name_and_status_of_each_confirmed_child(lore_env, spec):
    env, vault = lore_env
    a, b = make_children(env, vault, spec, count=2)
    name = hand_parent(env, vault, spec, [a, b])
    _lore(env, "record", "update", f"task/{b}", "--vault", vault, "--parent", name, "--status", "blocked")
    assert parent.held_children(EnvRunner(env), vault, name) == [
        {"name": b, "status": "blocked", "title": "Child 1", "delivers": "A new widget that frobs."}
    ]


# --- drop: un-nest the unfinished children, then drop the parent ------------------------------


def typed_drop_code(runner, spec_name, parent_name, reason_path):
    """The code `state --reason-file` prints, read through a plain runner on the same vault."""
    env = getattr(runner, "env", None)
    if env is None:
        return None
    try:
        state = parent.parent_state(EnvRunner(env), spec_name, parent_name)
        return parent.drop_confirmation(spec_name, state, parent.load_reason(str(reason_path)))
    except (parent.ParentError, parent.gather.GatherError):
        return None


def run_drop(runner, spec_name, parent_name, tmp_path, capsys, reason="no longer wanted", raw=None, typed="auto"):
    path = tmp_path / "reason.json"
    path.write_text(json.dumps(reason) if raw is None else raw)
    argv = ["drop", spec_name, parent_name, "--reason-file", str(path)]
    typed = typed_drop_code(runner, spec_name, parent_name, path) if typed == "auto" else typed
    code = parent.main(argv + (["--confirm", typed] if typed not in (None, "auto") else []), runner=runner)
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def assembled_parent(env, vault, spec, tmp_path, capsys, count=2):
    names = make_children(env, vault, spec, count=count)
    _, out, _ = run_assemble(tmp_path, EnvRunner(env), spec, confirm(env, vault, names), capsys)
    return out["parent"], names


def dropped_section(body):
    head, _, tail = body.partition("\n## Dropped\n\n")
    assert head and tail, body
    return tail


def paste(env, command):
    return subprocess.run(
        shlex.split(command), capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL
    )


def test_dropping_a_ready_parent_frees_both_children_and_gathers_them_again(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    assert gathered_names(env, spec) == []
    runner = EnvRunner(env)
    code, out, err = run_drop(runner, spec, parent_name, tmp_path, capsys, reason="plans changed")
    assert code == 0, err
    assert out["parent"] == parent_name and out["dropped"] is True
    assert out["un-nested"] == names
    assert out["kept"] == [] and out["failed"] == [] and out["reset"] == []
    assert parents_of(env, vault, names) == [None, None]
    assert sidecar_of(env, vault, parent_name)["status"] == "dropped"
    assert dropped_section(body_of(env, vault, parent_name)).strip() == "Reason: plans changed"
    assert gathered_names(env, spec) == names
    assert [a[2] for a in runner.calls if is_unnest(a)] == [f"task/{n}" for n in names]


def test_a_done_child_stays_nested_and_an_in_progress_child_is_un_nested(
    lore_env, spec, capsys, tmp_path
):
    env, vault = lore_env
    done = make_followup(env, vault, "Finished child", status="done", spec=spec)
    busy = make_followup(env, vault, "Busy child", status="in-progress", spec=spec)
    holder = hand_parent(env, vault, spec, [done, busy], status="in-progress")
    for n in (done, busy):
        nest(env, vault, n, holder)
    code, out, err = run_drop(EnvRunner(env), spec, holder, tmp_path, capsys)
    assert code == 0, err
    assert out["kept"] == [{"name": done, "status": "done"}]
    assert out["un-nested"] == [busy]
    assert sidecar_of(env, vault, done)["parent"] == holder
    assert sidecar_of(env, vault, busy).get("parent") is None
    assert sidecar_of(env, vault, holder)["status"] == "dropped"


@pytest.mark.parametrize("finished", ["done", "dropped"])
def test_a_child_that_finished_after_the_held_read_is_kept_not_un_nested(
    lore_env, spec, tmp_path, capsys, finished
):
    env, vault = lore_env
    parent_name, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    seen = {"parent": 0, "done": False}

    def hook(argv, stdin, runner):
        if argv[:3] == ["record", "show", "--json"]:
            if argv[-1] == f"task/{parent_name}":
                seen["parent"] += 1
            elif argv[-1] == f"task/{names[0]}" and seen["parent"] >= 3 and not seen["done"]:
                seen["done"] = True
                _lore(env, "record", "update", f"task/{names[0]}", "--vault", vault, "--status", finished)

    runner = EnvRunner(env, hook)
    code, out, err = run_drop(runner, spec, parent_name, tmp_path, capsys)
    assert seen["done"], "the status change never fired"
    assert code == 0, err
    assert out["un-nested"] == [names[1]]
    assert out["kept"] == [{"name": names[0], "status": finished}]
    assert not any(is_unnest(a, names[0]) for a in runner.calls)
    assert parents_of(env, vault, names) == [parent_name, None]
    assert sidecar_of(env, vault, parent_name)["status"] == "dropped"


def test_a_failed_first_un_nest_leaves_the_parent_live_and_a_rerun_completes_the_drop(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    runner = EnvRunner(env, lambda a, s, r: refused(a) if is_unnest(a, names[0]) else None)
    code, out, err = run_drop(runner, spec, parent_name, tmp_path, capsys)
    assert code == 2
    assert reason_codes(err) == ["reason-code: drop-incomplete"]
    assert out["dropped"] is False
    assert sidecar_of(env, vault, parent_name)["status"] == "ready"
    assert [(f["name"], f["write"]) for f in out["failed"]] == [(names[0], "un-nest")]
    assert out["un-nested"] == [names[1]]
    assert not any(is_drop(a) for a in runner.calls)
    assert paste(env, out["failed"][0]["command"]).returncode == 0
    assert sidecar_of(env, vault, names[0]).get("parent") is None
    code, out, err = run_drop(EnvRunner(env), spec, parent_name, tmp_path, capsys)
    assert code == 0, err
    assert out["dropped"] is True and out["un-nested"] == []
    assert sidecar_of(env, vault, parent_name)["status"] == "dropped"


def test_a_failed_drop_write_after_every_un_nest_names_a_command_that_drops_the_parent(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    runner = EnvRunner(env, lambda a, s, r: refused(a) if is_drop(a) else None)
    code, out, err = run_drop(runner, spec, parent_name, tmp_path, capsys, reason="secret-free reason")
    assert code == 2
    assert reason_codes(err) == ["reason-code: drop-incomplete"]
    assert out["un-nested"] == names and out["dropped"] is False
    assert [(f["name"], f["write"]) for f in out["failed"]] == [(parent_name, "drop")]
    assert "secret-free" not in out["failed"][0]["command"]
    assert sidecar_of(env, vault, parent_name)["status"] == "ready"
    assert paste(env, out["failed"][0]["command"]).returncode == 0
    assert sidecar_of(env, vault, parent_name)["status"] == "dropped"


def dropped_text(lore_env, spec, tmp_path, capsys, reason):
    env, vault = lore_env
    parent_name, _ = assembled_parent(env, vault, spec, tmp_path, capsys, count=1)
    code, _, err = run_drop(EnvRunner(env), spec, parent_name, tmp_path, capsys, reason=reason)
    assert code == 0, err
    return body_of(env, vault, parent_name)


def test_a_credential_in_the_drop_reason_is_redacted(lore_env, spec, tmp_path, capsys):
    body = dropped_text(lore_env, spec, tmp_path, capsys, "rotate AKIAIOSFODNN7EXAMPLE first")
    assert "AKIAIOSFODNN7EXAMPLE" not in body
    assert dropped_section(body).strip() == "Reason: rotate [REDACTED] first"


def test_a_drop_reason_with_a_newline_and_a_heading_stays_on_one_line(lore_env, spec, tmp_path, capsys):
    body = dropped_text(lore_env, spec, tmp_path, capsys, "first\n## Forged\r\n**Covers:** AC1\nlast")
    assert dropped_section(body).strip() == "Reason: first ## Forged **Covers:** AC1 last"
    assert [ln for ln in body.splitlines() if ln.startswith("## ")].count("## Dropped") == 1
    assert "## Forged" not in [ln for ln in body.splitlines()]


def test_control_characters_in_a_drop_reason_are_stripped(lore_env, spec, tmp_path, capsys):
    body = dropped_text(lore_env, spec, tmp_path, capsys, "bell\x07 esc\x1b[31m red\x00")
    assert dropped_section(body).strip() == "Reason: bell esc[31m red"


def test_invisible_characters_in_a_drop_reason_are_stripped(lore_env, spec, tmp_path, capsys):
    body = dropped_text(lore_env, spec, tmp_path, capsys, "zero​width ‮flip⁠")
    assert dropped_section(body).strip() == "Reason: zerowidth flip"


def test_a_drop_reason_over_the_bound_is_cut_with_a_marker(lore_env, spec, tmp_path, capsys):
    limit = parent.MAX_REASON_CHARS
    text = "lorem " * 200
    assert not text[:limit].endswith(" ")
    body = dropped_text(lore_env, spec, tmp_path, capsys, text)
    assert dropped_section(body).strip() == "Reason: " + text[:limit] + "…"


def _after_state(spec_name, act):
    """A hook running `act` once, between `state` (which resolves the spec first) and
    drop's own re-resolution of it: after the held read, before the first write."""
    seen = {"n": 0}

    def hook(argv, stdin, runner):
        if argv[:3] == ["record", "show", "--json"] and argv[-1] == f"spec/{spec_name}":
            seen["n"] += 1
            if seen["n"] == 2:
                act()

    return hook


@pytest.mark.parametrize("elsewhere", [True, False], ids=["re-parented", "already-free"])
def test_a_child_moved_off_the_parent_before_its_un_nest_is_kept_and_not_touched(
    lore_env, spec, tmp_path, capsys, elsewhere
):
    env, vault = lore_env
    parent_name, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    rival = hand_parent(env, vault, spec, [], status="open") if elsewhere else None
    flags = ["--parent", rival] if elsewhere else ["--unset-parent"]
    hook = _after_state(
        spec, lambda: _lore(env, "record", "update", f"task/{names[0]}", "--vault", vault, *flags)
    )
    runner = EnvRunner(env, hook)
    code, out, err = run_drop(runner, spec, parent_name, tmp_path, capsys)
    assert code == 0, err
    assert out["kept"] == [{"name": names[0], "status": "ready"}]
    assert out["un-nested"] == [names[1]]
    assert not any(is_unnest(a, names[0]) for a in runner.calls)
    assert sidecar_of(env, vault, names[0]).get("parent") == rival
    if elsewhere:
        assert sidecar_of(env, vault, rival)["status"] == "open"
    assert sidecar_of(env, vault, parent_name)["status"] == "dropped"


def test_a_parent_that_started_building_between_state_and_the_first_write_is_refused(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    hook = _after_state(
        spec,
        lambda: _lore(env, "record", "update", f"task/{parent_name}", "--vault", vault, "--status", "in-progress"),
    )
    runner = EnvRunner(env, hook)
    code, out, err = run_drop(runner, spec, parent_name, tmp_path, capsys)
    assert code == 1 and out is None
    assert reason_codes(err) == ["reason-code: parent-changed"]
    assert f"subject: {parent_name}" in err.splitlines()
    assert runner.writes() == []
    assert sidecar_of(env, vault, parent_name)["status"] == "in-progress"
    assert [sidecar_of(env, vault, n)["parent"] for n in names] == [parent_name] * 2


def test_an_un_nested_in_progress_child_gets_a_reset_command_that_makes_it_gatherable(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    busy = make_followup(env, vault, "Busy child", status="in-progress", spec=spec)
    holder = hand_parent(env, vault, spec, [busy], status="ready")
    nest(env, vault, busy, holder)
    code, out, err = run_drop(EnvRunner(env), spec, holder, tmp_path, capsys)
    assert code == 0, err
    assert [r["name"] for r in out["reset"]] == [busy]
    assert gathered_names(env, spec) == []
    assert paste(env, out["reset"][0]["command"]).returncode == 0
    assert gathered_names(env, spec) == [busy]


def test_a_ready_child_needs_no_reset(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, _ = assembled_parent(env, vault, spec, tmp_path, capsys, count=1)
    _, out, _ = run_drop(EnvRunner(env), spec, parent_name, tmp_path, capsys)
    assert out["reset"] == []


@pytest.mark.parametrize(
    "make",
    [
        lambda p: p.symlink_to(p.parent / "real.json"),
        lambda p: p.write_text(json.dumps("x" * 4097)),
        lambda p: p.write_text('"fine"' + " " * 5000),
        lambda p: p.write_text("123"),
        lambda p: p.write_text('["a"]'),
        lambda p: p.write_text("not json"),
        lambda p: p.write_text('""'),
        lambda p: p.write_text('"   "'),
        lambda p: p.write_text('"\\u200b\\u0007"'),
        lambda p: None,
    ],
    ids=[
        "symlink", "over-cap", "over-cap-padded", "number", "list", "not-json",
        "empty", "blank", "only-stripped", "missing",
    ],
)
def test_an_unusable_reason_file_is_refused_with_the_vault_unchanged(
    lore_env, spec, tmp_path, capsys, make
):
    env, vault = lore_env
    parent_name, _ = assembled_parent(env, vault, spec, tmp_path, capsys)
    before = vault_snapshot(env, vault)
    (tmp_path / "real.json").write_text(json.dumps("fine"))
    path = tmp_path / "reason-under-test.json"
    make(path)
    runner = EnvRunner(env)
    code = parent.main(["drop", spec, parent_name, "--reason-file", str(path)], runner=runner)
    out = capsys.readouterr()
    assert code == 2 and out.out == ""
    assert reason_codes(out.err) == [
        "reason-code: invalid-reason-file"
    ]
    assert runner.writes() == [] and vault_snapshot(env, vault) == before


def test_a_reason_file_at_exactly_the_cap_is_accepted(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, _ = assembled_parent(env, vault, spec, tmp_path, capsys, count=1)
    raw = json.dumps("x" * (4096 - 2))
    assert len(raw.encode()) == 4096
    code, _, err = run_drop(EnvRunner(env), spec, parent_name, tmp_path, capsys, raw=raw)
    assert code == 0, err


@pytest.mark.parametrize("status", ["dropped", "done"])
def test_a_parent_that_is_no_longer_live_is_refused_with_the_vault_unchanged(
    lore_env, spec, tmp_path, capsys, status
):
    env, vault = lore_env
    holder = hand_parent(env, vault, spec, [], status=status)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_drop(runner, spec, holder, tmp_path, capsys)
    assert code == 1 and out is None
    assert reason_codes(err) == ["reason-code: parent-not-live"]
    assert runner.writes() == [] and vault_snapshot(env, vault) == before


def test_an_unmarked_task_is_refused_as_not_a_followup_parent_with_the_vault_unchanged(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    plain = make_followup(env, vault, "Plain task", status="open", spec=spec)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_drop(runner, spec, plain, tmp_path, capsys)
    assert code == 1 and out is None
    assert reason_codes(err) == [
        "reason-code: not-a-followup-parent"
    ]
    assert runner.writes() == [] and vault_snapshot(env, vault) == before


# --- resume: finish an open parent --------------------------------------------------------------


def resume_code(runner, spec_name, parent_name, children):
    """The code `check --parent` prints for exactly these children, from the parent as it stands."""
    removed = []
    if isinstance(runner, EnvRunner):
        try:
            state = parent.parent_state(EnvRunner(runner.env), spec_name, parent_name)
        except parent.ParentError:
            state = None
        if state is not None:
            removed = parent.resume_removals(state, children)
    return parent.check_confirmation(spec_name, children, parent_name, None, removed)


def run_resume(runner, spec_name, parent_name, children, tmp_path, capsys, raw=None, typed="auto"):
    path = tmp_path / "resume-children.json"
    path.write_text(json.dumps(children) if raw is None else raw)
    if typed == "auto":
        typed = resume_code(runner, spec_name, parent_name, children) if raw is None else None
    argv = ["resume", spec_name, parent_name, "--children", str(path)] + (["--confirm", typed] if typed else [])
    code = parent.main(argv, runner=runner)
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def revisions(env, vault, names):
    return [{"name": n, "revision": shown_revision(env, vault, n)} for n in names]


def interrupted(env, vault, spec, tmp_path, capsys, count=3, fail_at=1):
    """(parent, names): an assembly whose nesting of names[fail_at] failed, so the
    parent is `open` holding the children before it."""
    names = make_children(env, vault, spec, count=count)
    runner = EnvRunner(env, lambda a, s, r: refused(a) if is_nest(a, names[fail_at]) else None)
    _, out, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    return out["parent"], names


def is_body_write(argv, name):
    return argv[:3] == ["record", "update", f"task/{name}"] and len(argv) == 5


def test_resume_nests_the_remaining_children_and_makes_the_parent_ready(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    code, out, err = run_resume(
        EnvRunner(env), spec, parent_name, revisions(env, vault, names[1:]), tmp_path, capsys
    )
    assert code == 0, err
    assert out == {"parent": parent_name, "removed": [], "nested": names[1:], "failed": [], "buildable": True}
    assert sidecar_of(env, vault, parent_name)["status"] == "ready"
    assert parents_of(env, vault, names) == [parent_name] * 3
    graph = _lore(env, "task", "graph", parent_name, "--vault", vault).splitlines()
    assert graph[0] == f"{parent_name} [ready]"
    assert sorted(line.strip() for line in graph[1:]) == sorted(f"{n} [ready] (runnable)" for n in names)


def test_resume_with_an_empty_file_flips_a_parent_that_holds_every_listed_child(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    runner = EnvRunner(env, lambda a, s, r: refused(a) if is_flip(a) else None)
    _, assembled, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert sidecar_of(env, vault, assembled["parent"])["status"] == "open"
    code, out, err = run_resume(EnvRunner(env), spec, assembled["parent"], [], tmp_path, capsys)
    assert code == 0, err
    assert out == {"parent": assembled["parent"], "removed": [], "nested": [], "failed": [], "buildable": True}
    assert sidecar_of(env, vault, assembled["parent"])["status"] == "ready"


def test_resume_without_the_third_child_removes_its_section_and_leaves_it_gatherable(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    code, out, err = run_resume(
        EnvRunner(env), spec, parent_name, revisions(env, vault, names[1:2]), tmp_path, capsys
    )
    assert code == 0, err
    assert out["removed"] == [names[2]] and out["nested"] == [names[1]]
    body = body_of(env, vault, parent_name)
    assert f"### task/{names[2]}" not in body and body.count("### task/") == 2
    assert parents_of(env, vault, names) == [parent_name, parent_name, None]
    assert gathered_names(env, spec) == [names[2]]
    assert sidecar_of(env, vault, parent_name)["status"] == "ready"
    graph = _lore(env, "task", "graph", parent_name, "--vault", vault).splitlines()
    assert sorted(line.strip() for line in graph[1:]) == sorted(f"{n} [ready] (runnable)" for n in names[:2])


# --- resume: refusals, each leaving the vault unchanged --------------------------------------


def test_resume_refuses_a_name_the_body_does_not_list_as_child_not_listed(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    outsider = make_followup(env, vault, "Outsider", spec=spec)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(
        runner, spec, parent_name, revisions(env, vault, [names[1], outsider]), tmp_path, capsys
    )
    assert_refused(code, out, err, runner, "child-not-listed", outsider)
    assert vault_snapshot(env, vault) == before


def test_resume_refuses_a_child_edited_since_the_confirmation_as_child_changed(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    children = revisions(env, vault, names[1:])
    _lore(env, "record", "update", f"task/{names[2]}", "--vault", vault, stdin=CLEAN + "\nA later edit.\n")
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, parent_name, children, tmp_path, capsys)
    assert_refused(code, out, err, runner, "child-changed", names[2])
    assert vault_snapshot(env, vault) == before


def test_resume_refuses_a_child_no_longer_ready_as_child_not_eligible(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    children = revisions(env, vault, names[1:])
    _lore(env, "record", "update", f"task/{names[1]}", "--vault", vault, "--status", "blocked")
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, parent_name, children, tmp_path, capsys)
    assert_refused(code, out, err, runner, "child-not-eligible", names[1])
    assert vault_snapshot(env, vault) == before


def test_resume_refuses_a_held_child_set_back_to_open_as_child_not_eligible(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    children = revisions(env, vault, names[1:])
    _lore(env, "record", "update", f"task/{names[0]}", "--vault", vault, "--status", "open")
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, parent_name, children, tmp_path, capsys)
    assert_refused(code, out, err, runner, "child-not-eligible", names[0])
    assert vault_snapshot(env, vault) == before


def dependent_pair(env, vault, spec, tmp_path, capsys):
    """(parent, a, b): `b` depends on `a`; the assembly nested `a` and failed on `b`."""
    a = make_followup(env, vault, "Base", spec=spec)
    b = make_followup(env, vault, "Needs base", spec=spec, depends=[a])
    runner = EnvRunner(env, lambda argv, s, r: refused(argv) if is_nest(argv, b) else None)
    _, out, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, [a, b]), capsys)
    assert out["nested"] == [a] and out["failed"] == [{"name": b, "write": "nest"}]
    return out["parent"], a, b


def test_resume_nests_a_child_whose_dependency_the_parent_already_holds(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, a, b = dependent_pair(env, vault, spec, tmp_path, capsys)
    code, out, err = run_resume(
        EnvRunner(env), spec, parent_name, revisions(env, vault, [b]), tmp_path, capsys
    )
    assert code == 0, err
    assert out["nested"] == [b] and out["buildable"] is True
    assert sidecar_of(env, vault, parent_name)["status"] == "ready"
    assert parents_of(env, vault, [a, b]) == [parent_name] * 2


def test_resume_nests_two_children_one_of_which_depends_on_the_other(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    a = make_followup(env, vault, "First", spec=spec)
    b = make_followup(env, vault, "Second", spec=spec)
    c = make_followup(env, vault, "Third", spec=spec, depends=[b])
    runner = EnvRunner(env, lambda argv, s, r: refused(argv) if is_nest(argv, b) else None)
    _, out, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, [a, b, c]), capsys)
    code, out2, err = run_resume(
        EnvRunner(env), spec, out["parent"], revisions(env, vault, [b, c]), tmp_path, capsys
    )
    assert code == 0, err
    assert out2["nested"] == [b, c] and out2["buildable"] is True
    assert parents_of(env, vault, [a, b, c]) == [out["parent"]] * 3


def test_resume_still_refuses_a_dependency_that_is_neither_held_nor_confirmed(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, a, b = dependent_pair(env, vault, spec, tmp_path, capsys)
    outside = make_followup(env, vault, "Unfinished outside", status="open")
    _lore(env, "record", "update", f"task/{b}", "--vault", vault, "--unset-depends-on", a,
          "--depends-on", outside)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, parent_name, revisions(env, vault, [b]), tmp_path, capsys)
    assert_refused(code, out, err, runner, "child-not-eligible", b)
    assert vault_snapshot(env, vault) == before


def run_check_json(runner, spec_name, *argv, capsys):
    code = parent.main(["check", spec_name, *argv], runner=runner)
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def test_check_with_the_parent_counts_a_dependency_the_parent_holds_as_inside(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, a, b = dependent_pair(env, vault, spec, tmp_path, capsys)
    code, out, err = run_check_json(EnvRunner(env), spec, b, "--parent", parent_name, capsys=capsys)
    assert code == 0, err
    assert [item["name"] for item in out["eligible"]] == [b] and out["left-out"] == []
    assert out["eligible"][0]["revision"] == shown_revision(env, vault, b)


def test_check_without_the_parent_still_counts_that_dependency_as_outside(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, a, b = dependent_pair(env, vault, spec, tmp_path, capsys)
    code, out, err = run_check_json(EnvRunner(env), spec, b, capsys=capsys)
    assert code == 0, err
    assert out["left-out"] == [{"name": b, "reason": "depends-outside", "dependency": a}]


def test_check_with_the_parent_counts_a_dependency_on_a_done_held_child_as_satisfied(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, a, b = dependent_pair(env, vault, spec, tmp_path, capsys)
    _lore(env, "record", "update", f"task/{a}", "--vault", vault, "--status", "done")
    code, plain, err = run_check_json(EnvRunner(env), spec, b, capsys=capsys)
    assert code == 0, err
    assert [item["name"] for item in plain["eligible"]] == [b]
    code, out, err = run_check_json(EnvRunner(env), spec, b, "--parent", parent_name, capsys=capsys)
    assert code == 0, err
    assert [item["name"] for item in out["eligible"]] == [b] and out["left-out"] == []


def test_check_with_the_parent_still_counts_a_dependency_on_a_blocked_held_child_as_unfinished(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, a, b = dependent_pair(env, vault, spec, tmp_path, capsys)
    _lore(env, "record", "update", f"task/{a}", "--vault", vault, "--status", "blocked")
    code, out, err = run_check_json(EnvRunner(env), spec, b, "--parent", parent_name, capsys=capsys)
    assert code == 0, err
    assert out["left-out"] == [{"name": b, "reason": "depends-outside", "dependency": a}]


def renamed_third_child(env, vault, spec, tmp_path, capsys):
    """(parent, names, old): an interrupted parent whose unheld third child was renamed, so
    the parent's body still lists `old`, the name it no longer has."""
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys, fail_at=2)
    old = names[2]
    _lore(env, "record", "rename", f"task/{old}", "--vault", vault, "--title", "Renamed third")
    return parent_name, names, old


def test_a_renamed_unheld_child_stays_listed_under_its_old_name_in_the_parent_body(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names, old = renamed_third_child(env, vault, spec, tmp_path, capsys)
    code, state, _, err = run_state(EnvRunner(env), spec, parent_name, capsys)
    assert code == 0, err
    assert state["unheld"] == [old]


def test_check_with_the_parent_reports_a_listed_unheld_name_that_no_longer_resolves_as_not_found(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names, old = renamed_third_child(env, vault, spec, tmp_path, capsys)
    code, out, err = run_check_json(EnvRunner(env), spec, old, "--parent", parent_name, capsys=capsys)
    assert code == 0, err
    assert out["eligible"] == [] and out["left-out"] == [{"name": old, "reason": "not-found"}]


def test_check_with_the_parent_still_refuses_a_missing_name_the_parent_does_not_list(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names, old = renamed_third_child(env, vault, spec, tmp_path, capsys)
    code, out, err = run_check_json(
        EnvRunner(env), spec, "never-existed", "--parent", parent_name, capsys=capsys
    )
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: followup-not-found"]


def test_check_without_the_parent_still_refuses_a_missing_name(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, names, old = renamed_third_child(env, vault, spec, tmp_path, capsys)
    code, out, err = run_check_json(EnvRunner(env), spec, old, capsys=capsys)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: followup-not-found"]


def test_a_renamed_child_goes_through_check_then_resume_to_a_ready_parent_without_the_stale_name(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names, old = renamed_third_child(env, vault, spec, tmp_path, capsys)
    code, checked, err = run_check_json(EnvRunner(env), spec, old, "--parent", parent_name, capsys=capsys)
    assert code == 0, err
    assert [item["name"] for item in checked["left-out"]] == [old]
    code, out, err = run_resume(EnvRunner(env), spec, parent_name, [], tmp_path, capsys)
    assert code == 0, err
    assert out["removed"] == [old] and out["buildable"] is True
    assert sidecar_of(env, vault, parent_name)["status"] == "ready"
    assert f"### task/{old}" not in body_of(env, vault, parent_name)


def test_check_with_a_parent_that_is_not_a_follow_up_parent_of_the_spec_is_refused(
    lore_env, spec, capsys
):
    env, vault = lore_env
    plain = make_followup(env, vault, "Plain", status="open", spec=spec)
    b = make_followup(env, vault, "Candidate", spec=spec)
    runner = EnvRunner(env)
    code, out, err = run_check_json(runner, spec, b, "--parent", plain, capsys=capsys)
    assert code == 1 and out is None
    assert reason_codes(err) == ["reason-code: not-a-followup-parent"]
    assert runner.writes() == []


def test_resume_refuses_a_held_child_the_parent_does_not_list_as_unlisted_child(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    stray = make_followup(env, vault, "Stray", spec=spec)
    nest(env, vault, stray, parent_name)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, parent_name, revisions(env, vault, names[1:]), tmp_path, capsys)
    assert_refused(code, out, err, runner, "unlisted-child", stray)
    assert vault_snapshot(env, vault) == before


def _make_held_child_route_labelled(env, vault, name):
    _lore(env, "record", "update", f"task/{name}", "--vault", vault, "--label", "route=plan")


def _make_held_child_unresolved(env, vault, name):
    body = body_of(env, vault, name)
    _lore(env, "record", "update", f"task/{name}", "--vault", vault,
          stdin=body + "\n## Refine — unresolved\n\nWhich way?\n")


def _make_held_child_depend_on_unfinished(env, vault, name):
    outside = make_followup(env, vault, "Unfinished outside", status="open")
    _lore(env, "record", "update", f"task/{name}", "--vault", vault, "--depends-on", outside)


@pytest.mark.parametrize(
    "degrade",
    [_make_held_child_route_labelled, _make_held_child_unresolved, _make_held_child_depend_on_unfinished],
    ids=["route-label", "unresolved-heading", "unfinished-dependency"],
)
def test_resume_refuses_a_listed_held_child_that_stopped_being_eligible(
    lore_env, spec, tmp_path, capsys, degrade
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    children = revisions(env, vault, names[1:])
    degrade(env, vault, names[0])
    assert sidecar_of(env, vault, names[0])["status"] == "ready"
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, parent_name, children, tmp_path, capsys)
    assert_refused(code, out, err, runner, "child-not-eligible", names[0])
    assert vault_snapshot(env, vault) == before


def test_resume_refuses_a_held_child_another_parent_took_after_the_state_read(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    holder = make_followup(env, vault, "Other holder", status="open")
    children = revisions(env, vault, names[1:])
    shows = []

    def hook(argv, stdin, runner):
        if argv[:3] == ["record", "show", "--json"] and argv[-1] == f"task/{names[0]}":
            shows.append(argv)
            if len(shows) == 3:
                nest(env, vault, names[0], holder)

    runner = EnvRunner(env, hook)
    code, out, err = run_resume(runner, spec, parent_name, children, tmp_path, capsys)
    assert len(shows) >= 3
    assert_refused(code, out, err, runner, "child-not-eligible", names[0])
    assert sidecar_of(env, vault, names[0])["parent"] == holder
    assert sidecar_of(env, vault, parent_name)["status"] == "open"


def test_resume_refuses_a_ready_parent_as_parent_not_resumable(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, parent_name, [], tmp_path, capsys)
    assert_refused(code, out, err, runner, "parent-not-resumable", parent_name)
    assert vault_snapshot(env, vault) == before


def test_a_second_resume_on_a_parent_the_first_made_ready_is_parent_not_resumable(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    children = revisions(env, vault, names[1:])
    code, _, err = run_resume(EnvRunner(env), spec, parent_name, children, tmp_path, capsys)
    assert code == 0, err
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, parent_name, children, tmp_path, capsys)
    assert_refused(code, out, err, runner, "parent-not-resumable", parent_name)
    assert vault_snapshot(env, vault) == before


def test_resume_refuses_while_another_live_parent_is_linked_to_the_spec(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    other = make_followup(env, vault, "Other parent", status="open", labels=[LABEL], spec=spec)
    children = revisions(env, vault, names[1:])
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, parent_name, children, tmp_path, capsys)
    assert_refused(code, out, err, runner, "followup-parent-exists", other)
    assert vault_snapshot(env, vault) == before


def test_resume_with_no_held_child_and_an_empty_file_is_nothing_to_build(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys, fail_at=0)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, parent_name, [], tmp_path, capsys)
    assert_refused(code, out, err, runner, "nothing-to-build", parent_name)
    assert vault_snapshot(env, vault) == before


def test_resume_refuses_a_task_that_is_not_a_follow_up_parent(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    plain = make_followup(env, vault, "Plain", status="open", spec=spec)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, plain, [], tmp_path, capsys)
    assert_refused(code, out, err, runner, "not-a-followup-parent", plain)
    assert vault_snapshot(env, vault) == before


@pytest.mark.parametrize(
    "raw",
    ["not json", "{}", '"x"', '[{"name": "a"}]', '[{"name": "a", "revision": ""}]', '[3]',
     '[{"name": "a", "revision": "r"}, {"name": "a", "revision": "r"}]'],
)
def test_a_malformed_resume_children_file_is_refused_before_any_lore_call(tmp_path, capsys, raw):
    runner = Lore()
    code, out, err = run_resume(runner, "s", "p", [], tmp_path, capsys, raw=raw)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: invalid-children-file"]
    assert runner.calls == []


def test_a_resume_child_name_of_a_hostile_shape_is_refused_before_any_lore_call(tmp_path, capsys):
    runner = Lore()
    code, out, err = run_resume(runner, "s", "p", [{"name": "a b", "revision": "r"}], tmp_path, capsys)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: invalid-followup-name"]
    assert runner.calls == []


def test_resume_without_its_children_file_is_invalid_arguments(capsys):
    runner = Lore()
    assert parent.main(["resume", "s", "p"], runner=runner) == 2
    assert reason_codes(capsys.readouterr().err) == ["reason-code: invalid-arguments"]
    assert runner.calls == []


# --- resume: failure semantics ------------------------------------------------------------------


def test_a_failed_nesting_during_resume_leaves_the_parent_open_and_a_second_resume_completes_it(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    runner = EnvRunner(env, lambda a, s, r: refused(a) if is_nest(a, names[2]) else None)
    code, out, err = run_resume(runner, spec, parent_name, revisions(env, vault, names[1:]), tmp_path, capsys)
    assert code == 2
    assert reason_codes(err) == ["reason-code: nesting-incomplete"]
    assert out == {
        "parent": parent_name, "removed": [], "nested": [names[1]],
        "failed": [{"name": names[2], "write": "nest"}], "buildable": False,
    }
    assert not any(is_flip(call) for call in runner.calls)
    assert sidecar_of(env, vault, parent_name)["status"] == "open"
    assert parents_of(env, vault, names) == [parent_name, parent_name, None]
    code, out, err = run_resume(
        EnvRunner(env), spec, parent_name, revisions(env, vault, names[2:]), tmp_path, capsys
    )
    assert code == 0, err
    assert out["nested"] == [names[2]] and out["buildable"] is True
    assert sidecar_of(env, vault, parent_name)["status"] == "ready"
    assert parents_of(env, vault, names) == [parent_name] * 3


@pytest.mark.parametrize("silent", [False, True], ids=["write-refused", "write-silently-ignored"])
def test_a_failed_body_rewrite_leaves_no_child_nested_and_the_body_unchanged(
    lore_env, spec, tmp_path, capsys, silent
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    before = body_of(env, vault, parent_name)

    def hook(argv, stdin, runner):
        if is_body_write(argv, parent_name):
            return subprocess.CompletedProcess(argv, 0, "", "") if silent else refused(argv)

    runner = EnvRunner(env, hook)
    code, out, err = run_resume(runner, spec, parent_name, revisions(env, vault, names[1:2]), tmp_path, capsys)
    assert code == 2
    assert reason_codes(err) == ["reason-code: nesting-incomplete"]
    assert out == {
        "parent": parent_name, "removed": [], "nested": [],
        "failed": [{"name": parent_name, "write": "list"}], "buildable": False,
    }
    assert not any(is_nest(call) or is_flip(call) for call in runner.calls)
    assert body_of(env, vault, parent_name) == before
    assert parents_of(env, vault, names) == [parent_name, None, None]
    assert sidecar_of(env, vault, parent_name)["status"] == "open"


def _with_extra_prose(env, vault, parent_name):
    body = body_of(env, vault, parent_name)
    edited = body.replace("**Delta design:**", "**Delta design:** EXTRA PROSE", 1)
    assert edited != body
    _lore(env, "record", "update", f"task/{parent_name}", "--vault", vault, stdin=edited)
    return body_of(env, vault, parent_name)


@pytest.mark.parametrize("silent", [False, True], ids=["write-refused", "write-silently-ignored"])
def test_a_refused_body_rewrite_with_nothing_removed_is_still_a_failed_list_when_the_prose_survives(
    lore_env, spec, tmp_path, capsys, silent
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    before = _with_extra_prose(env, vault, parent_name)

    def hook(argv, stdin, runner):
        if is_body_write(argv, parent_name):
            return subprocess.CompletedProcess(argv, 0, "", "") if silent else refused(argv)

    runner = EnvRunner(env, hook)
    code, out, err = run_resume(runner, spec, parent_name, revisions(env, vault, names[1:]), tmp_path, capsys)
    assert code == 2
    assert reason_codes(err) == ["reason-code: nesting-incomplete"]
    assert out == {
        "parent": parent_name, "removed": [], "nested": [],
        "failed": [{"name": parent_name, "write": "list"}], "buildable": False,
    }
    assert not any(is_nest(call) or is_flip(call) for call in runner.calls)
    assert body_of(env, vault, parent_name) == before
    assert parents_of(env, vault, names) == [parent_name, None, None]
    assert sidecar_of(env, vault, parent_name)["status"] == "open"


def test_a_failed_complete_read_before_the_flip_leaves_the_parent_open(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    state = {"nested": 0}

    def hook(argv, stdin, runner):
        if is_nest(argv, names[2]):
            state["nested"] += 1
        if state["nested"] and argv[:2] == ["task", "list"]:
            return refused(argv)

    runner = EnvRunner(env, hook)
    code, out, err = run_resume(runner, spec, parent_name, revisions(env, vault, names[1:]), tmp_path, capsys)
    assert code == 2
    assert reason_codes(err) == ["reason-code: nesting-incomplete"]
    assert out["failed"] == [{"name": parent_name, "write": "ready"}] and out["buildable"] is False
    assert not any(is_flip(call) for call in runner.calls)
    assert sidecar_of(env, vault, parent_name)["status"] == "open"


def test_an_unexpected_exception_during_resume_leaves_the_parent_open_and_reports_it(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)

    def hook(argv, stdin, runner):
        if is_nest(argv, names[2]):
            raise RuntimeError("SECRET boom")

    code, out, err = run_resume(
        EnvRunner(env, hook), spec, parent_name, revisions(env, vault, names[1:]), tmp_path, capsys
    )
    assert code == 2
    assert reason_codes(err) == ["reason-code: internal-error"]
    assert "SECRET" not in err
    assert out["parent"] == parent_name and out["nested"] == [names[1]] and out["buildable"] is False
    assert sidecar_of(env, vault, parent_name)["status"] == "open"


# --- resume: the body is re-rendered, never patched -------------------------------------------


def test_a_resumed_body_is_exactly_the_templates_shape_whatever_prose_an_editor_left(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    body = body_of(env, vault, parent_name)
    edited = body.replace(
        "**Delta design:**", "**Delta design:** INJECTED PROSE\n\nAn extra paragraph.\n\n## Forged heading", 1
    ).replace(
        f"**Selection reason:** worth doing: {names[1]}\n",
        f"**Selection reason:** worth doing: {names[1]}\n\nMore prose.\n",
        1,
    )
    assert edited != body
    _lore(env, "record", "update", f"task/{parent_name}", "--vault", vault, stdin=edited)
    code, _, err = run_resume(
        EnvRunner(env), spec, parent_name, revisions(env, vault, names[1:]), tmp_path, capsys
    )
    assert code == 0, err
    expected = parent.render_body(spec, [{"name": n, "reason": f"worth doing: {n}"} for n in names])
    assert body_of(env, vault, parent_name).rstrip("\n") == expected.rstrip("\n")


def test_a_listed_section_without_its_reason_gets_no_reason_from_the_next_section(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys, count=2)
    body = body_of(env, vault, parent_name)
    without = body.replace(f"**Selection reason:** worth doing: {names[0]}\n", "", 1)
    assert without != body
    _lore(env, "record", "update", f"task/{parent_name}", "--vault", vault, stdin=without)
    code, _, err = run_resume(
        EnvRunner(env), spec, parent_name, revisions(env, vault, names[1:]), tmp_path, capsys
    )
    assert code == 0, err
    reasons = [ln.rstrip() for ln in body_of(env, vault, parent_name).splitlines()
               if ln.startswith("**Selection reason:**")]
    assert reasons == ["**Selection reason:**", f"**Selection reason:** worth doing: {names[1]}"]


def resumed_reason(env, vault, spec, tmp_path, capsys, raw):
    """The stored `**Selection reason:**` line of names[0] after a resume of a parent
    whose body an editor changed to carry `raw` as that reason."""
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys, count=2)
    body = body_of(env, vault, parent_name)
    old = f"**Selection reason:** worth doing: {names[0]}"
    assert old in body
    _lore(env, "record", "update", f"task/{parent_name}", "--vault", vault,
          stdin=body.replace(old, f"**Selection reason:** {raw}", 1))
    code, _, err = run_resume(
        EnvRunner(env), spec, parent_name, revisions(env, vault, names[1:]), tmp_path, capsys
    )
    assert code == 0, err
    return body_of(env, vault, parent_name)


def test_a_credential_in_a_listed_reason_is_redacted_on_resume(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    body = resumed_reason(env, vault, spec, tmp_path, capsys, "rotate AKIAIOSFODNN7EXAMPLE first")
    assert "AKIAIOSFODNN7EXAMPLE" not in body
    assert reason_line(body) == "**Selection reason:** rotate [REDACTED] first"


def test_control_characters_in_a_listed_reason_are_stripped_on_resume(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    body = resumed_reason(env, vault, spec, tmp_path, capsys, "bell\x07 esc\x1b[31m red\x00")
    assert reason_line(body) == "**Selection reason:** bell esc31m red"


def test_invisible_characters_in_a_listed_reason_are_stripped_on_resume(lore_env, spec, tmp_path, capsys):
    body = resumed_reason(*lore_env, spec, tmp_path, capsys, "zero​width ‮flip⁠")
    assert reason_line(body) == "**Selection reason:** zerowidth flip"


def test_a_listed_reason_over_several_lines_is_folded_to_one_on_resume(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    body = resumed_reason(env, vault, spec, tmp_path, capsys, "first\n**Covers:** AC1\nsecond")
    assert reason_line(body) == "**Selection reason:** first **Covers:** AC1 second"
    assert covers_gate(body).returncode == 0


def test_a_listed_reason_over_the_bound_is_cut_with_a_marker_on_resume(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    limit = parent.MAX_REASON_CHARS
    text = "lorem " * 200
    assert not text[:limit].endswith(" ")
    body = resumed_reason(env, vault, spec, tmp_path, capsys, text)
    assert reason_line(body) == "**Selection reason:** " + text[:limit] + "…"


# --- the shared nesting guard -------------------------------------------------------------------


def _change_in_the_window(env, vault, name, change, holder):
    if change == "parent":
        nest(env, vault, name, holder)
    else:
        _lore(env, "record", "update", f"task/{name}", "--vault", vault, "--status", "blocked")


def _window_hook(env, vault, name, change, holder, armed_by):
    """Changes `name` once, at the first read of it after `armed_by(argv)` has been seen."""
    state = {"armed": False, "done": False}

    def hook(argv, stdin, runner):
        if armed_by(argv, stdin):
            state["armed"] = True
        if state["armed"] and not state["done"] and argv[:3] == ["record", "show", "--json"] \
                and argv[-1] == f"task/{name}":
            state["done"] = True
            _change_in_the_window(env, vault, name, change, holder)

    return hook


@pytest.mark.parametrize("change", ["parent", "status"])
def test_assemble_does_not_nest_a_child_another_parent_or_a_status_took_in_the_window(
    lore_env, spec, tmp_path, capsys, change
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    holder = make_followup(env, vault, "Other holder", status="open")
    hook = _window_hook(env, vault, names[1], change, holder, lambda a, s: a[:2] == ["record", "create"])
    runner = EnvRunner(env, hook)
    code, out, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert_rolled_forward(out, runner)
    assert out["failed"] == [{"name": names[1], "write": "nest"}]
    assert not any(is_nest(a, names[1]) for a in runner.calls)
    side = sidecar_of(env, vault, names[1])
    assert side.get("parent") == (holder if change == "parent" else None)
    assert side["status"] == ("ready" if change == "parent" else "blocked")
    assert sidecar_of(env, vault, holder).get("parent") is None
    assert sidecar_of(env, vault, names[0])["parent"] == out["parent"]


@pytest.mark.parametrize("change", ["parent", "status"])
def test_resume_does_not_nest_a_child_another_parent_or_a_status_took_in_the_window(
    lore_env, spec, tmp_path, capsys, change
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys)
    holder = make_followup(env, vault, "Other holder", status="open")
    children = revisions(env, vault, names[1:])
    hook = _window_hook(env, vault, names[1], change, holder, lambda a, s: is_body_write(a, parent_name))
    runner = EnvRunner(env, hook)
    code, out, err = run_resume(runner, spec, parent_name, children, tmp_path, capsys)
    assert code == 2
    assert reason_codes(err) == ["reason-code: nesting-incomplete"]
    assert out["failed"] == [{"name": names[1], "write": "nest"}]
    assert out["nested"] == [] and out["buildable"] is False
    assert not any(is_nest(a, names[1]) for a in runner.calls)
    side = sidecar_of(env, vault, names[1])
    assert side.get("parent") == (holder if change == "parent" else None)
    assert sidecar_of(env, vault, parent_name)["status"] == "open"


# --- the rollback path ----------------------------------------------------------------------


def test_a_dropped_parent_set_back_to_open_by_hand_is_made_ready_again_by_resume(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    code, _, err = run_drop(EnvRunner(env), spec, parent_name, tmp_path, capsys)
    assert code == 0, err
    assert parents_of(env, vault, names) == [None, None]
    _lore(env, "record", "update", f"task/{parent_name}", "--vault", vault, "--status", "open")
    code, out, err = run_resume(
        EnvRunner(env), spec, parent_name, revisions(env, vault, names), tmp_path, capsys
    )
    assert code == 0, err
    assert out["nested"] == names and out["buildable"] is True
    assert sidecar_of(env, vault, parent_name)["status"] == "ready"
    assert parents_of(env, vault, names) == [parent_name] * 2
    assert "## Dropped" not in body_of(env, vault, parent_name)


# --- hostile names reach no lore call -------------------------------------------------------------

HOSTILE_NAMES = ["a$(touch x)", "-rf", "../x", "has space"]


def _argv_for(command, spec_name, parent_name, tmp_path, file_text=None):
    if command == "state":
        return ["state", "--", spec_name, parent_name]
    path = tmp_path / f"{command}.json"
    path.write_text(file_text if file_text is not None else ('"why"' if command == "drop" else "[]"))
    flag = "--reason-file" if command == "drop" else "--children"
    return [command, flag, str(path), "--", spec_name, parent_name]


@pytest.mark.parametrize("command", ["state", "drop", "resume"])
@pytest.mark.parametrize("hostile", HOSTILE_NAMES)
@pytest.mark.parametrize("position", ["spec", "parent"])
def test_a_hostile_spec_or_parent_name_is_refused_before_any_lore_call(
    command, hostile, position, tmp_path, capsys
):
    runner = Lore()
    spec_name, parent_name = (hostile, "p") if position == "spec" else ("s", hostile)
    code, out, err = run_main(runner, *_argv_for(command, spec_name, parent_name, tmp_path), capsys=capsys)
    assert code == 2 and out == ""
    assert reason_of(err) == ("invalid-spec-name" if position == "spec" else "invalid-followup-name")
    assert runner.calls == []


@pytest.mark.parametrize("command", ["drop", "resume"])
@pytest.mark.parametrize("position", ["spec", "parent"])
def test_a_hostile_name_is_refused_before_the_reason_or_children_file_is_read(
    command, position, tmp_path, capsys
):
    runner = Lore()
    spec_name, parent_name = ("a b", "p") if position == "spec" else ("s", "a b")
    argv = _argv_for(command, spec_name, parent_name, tmp_path, file_text="not json")
    code, out, err = run_main(runner, *argv, capsys=capsys)
    assert code == 2 and out == ""
    assert reason_of(err) == ("invalid-spec-name" if position == "spec" else "invalid-followup-name")
    assert runner.calls == []


@pytest.mark.parametrize("hostile", HOSTILE_NAMES)
def test_check_refuses_a_hostile_parent_name_before_any_lore_call(hostile, capsys):
    runner = Lore()
    code, out, err = run_main(runner, "check", "s", f"--parent={hostile}", "f", capsys=capsys)
    assert code == 2 and out == ""
    assert reason_of(err) == "invalid-followup-name"
    assert runner.calls == []


# --- hardening: a forged marker, unrelated children, forged text, hostile files ---------------

SHAPE_CASES = ["unlisted", "slice-parent", "has-parent", "no-listing"]


def forged_parent(env, vault, spec, case):
    """A hand-labelled live `open` parent that is shaped like `case` rather than like
    the parent `assemble` writes; it holds its one listed child."""
    listed = make_followup(env, vault, "Listed child", spec=spec)
    labels = (LABEL, "craft/slice-parent") if case == "slice-parent" else (LABEL,)
    name = hand_parent(env, vault, spec, [] if case == "no-listing" else [listed], labels=labels)
    nest(env, vault, listed, name)
    stray = None
    if case == "unlisted":
        stray = make_followup(env, vault, "Stray", spec=spec)
        nest(env, vault, stray, name)
    if case == "has-parent":
        nest(env, vault, name, make_followup(env, vault, "Outer", status="open", spec=spec))
    return name, stray


def run_act(command, runner, spec, name, tmp_path, capsys):
    if command == "drop":
        return run_drop(runner, spec, name, tmp_path, capsys)
    return run_resume(runner, spec, name, [], tmp_path, capsys)


@pytest.mark.parametrize("command", ["drop", "resume"])
@pytest.mark.parametrize("case", SHAPE_CASES)
def test_a_parent_not_shaped_like_a_followup_parent_is_refused_with_the_vault_unchanged(
    lore_env, spec, tmp_path, capsys, command, case
):
    env, vault = lore_env
    name, stray = forged_parent(env, vault, spec, case)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_act(command, runner, spec, name, tmp_path, capsys)
    expected, subject = ("unlisted-child", stray) if case == "unlisted" else ("parent-not-followup-shaped", name)
    assert code == 1 and out is None
    assert reason_codes(err) == [f"reason-code: {expected}"]
    assert f"subject: {subject}" in err.splitlines()
    assert runner.writes() == [] and vault_snapshot(env, vault) == before


@pytest.mark.parametrize("command", ["drop", "resume"])
def test_a_genuinely_assembled_parent_is_not_refused_as_misshapen(
    lore_env, spec, tmp_path, capsys, command
):
    env, vault = lore_env
    name, _ = interrupted(env, vault, spec, tmp_path, capsys)
    code, out, err = run_act(command, EnvRunner(env), spec, name, tmp_path, capsys)
    assert code == 0, err


def unrelated_task(env, vault):
    return make_followup(env, vault, "Unrelated ready task")


def test_check_leaves_out_a_ready_task_that_is_not_linked_to_the_spec(lore_env, spec):
    env, vault = lore_env
    outsider = unrelated_task(env, vault)
    linked = make_followup(env, vault, "Linked task", spec=spec)
    result = check_ok(env, spec, outsider, linked)
    assert left_out(result) == {outsider: {"name": outsider, "reason": "not-spec-linked"}}
    assert list(eligible(result)) == [linked]


def test_a_task_the_spec_names_is_linked_though_it_does_not_name_the_spec(lore_env):
    env, vault = lore_env
    named = unrelated_task(env, vault)
    owner = make_spec(env, vault, "Names a task", status="complete", related=[f"task={named}"])
    assert list(eligible(check_ok(env, owner, named))) == [named]


@pytest.mark.parametrize("anchor", ["slice", "slice-child"])
def test_a_follow_up_linked_to_a_slice_of_the_spec_rather_than_the_spec_is_eligible_and_nests(
    lore_env, spec, tmp_path, capsys, anchor
):
    env, vault = lore_env
    slice_name = make_followup(env, vault, "A slice", status="done", labels=["craft/slice-parent"], spec=spec)
    child = make_followup(env, vault, "Finished child", status="done", parent=slice_name)
    follow_up = make_followup(env, vault, "Follow-up", task_edge=slice_name if anchor == "slice" else child)
    assert list(eligible(check_ok(env, spec, follow_up))) == [follow_up]
    code, out, err = run_assemble(tmp_path, EnvRunner(env), spec, confirm(env, vault, [follow_up]), capsys)
    assert code == 0, err
    assert out["nested"] == [follow_up]


def test_check_with_a_parent_also_leaves_out_a_task_not_linked_to_the_spec(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    outsider = unrelated_task(env, vault)
    name = hand_parent(env, vault, spec, [outsider])
    code, out, err = run_check_json(EnvRunner(env), spec, "--parent", name, outsider, capsys=capsys)
    assert code == 0, err
    assert out["eligible"] == []
    assert out["left-out"] == [{"name": outsider, "reason": "not-spec-linked"}]


def test_assemble_refuses_a_child_not_linked_to_the_spec_with_the_vault_unchanged(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    outsider = unrelated_task(env, vault)
    children = confirm(env, vault, [outsider])
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_assemble(tmp_path, runner, spec, children, capsys)
    assert_refused(code, out, err, runner, "child-not-eligible", outsider)
    assert vault_snapshot(env, vault) == before


def test_resume_refuses_a_listed_child_not_linked_to_the_spec_and_leaves_the_parent_open(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    outsider = unrelated_task(env, vault)
    name = hand_parent(env, vault, spec, [outsider])
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, name, revisions(env, vault, [outsider]), tmp_path, capsys)
    assert_refused(code, out, err, runner, "child-not-eligible", outsider)
    assert vault_snapshot(env, vault) == before
    assert sidecar_of(env, vault, name)["status"] == "open"


def test_resume_refuses_a_held_child_not_linked_to_the_spec(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    linked = make_followup(env, vault, "Linked child", spec=spec)
    outsider = unrelated_task(env, vault)
    name = hand_parent(env, vault, spec, [linked, outsider])
    nest(env, vault, outsider, name)
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(runner, spec, name, revisions(env, vault, [linked]), tmp_path, capsys)
    assert_refused(code, out, err, runner, "child-not-eligible", outsider)
    assert vault_snapshot(env, vault) == before


def test_state_emits_a_sanitized_title_and_delivers_for_each_held_child(lore_env, spec, capsys):
    env, vault = lore_env
    long_text = "lorem " * 500
    noisy = make_followup(
        env, vault, "Zero​width title", spec=spec,
        body=f"**Delivers:** first\nline\x07 {long_text}\n\n**Files:** `a.py`\n",
    )
    plain = make_followup(env, vault, "Plain title", spec=spec, body="**Delivers:** one thing.\n")
    name = hand_parent(env, vault, spec, [noisy, plain])
    nest(env, vault, noisy, name)
    nest(env, vault, plain, name)
    code, out, _, err = run_state(EnvRunner(env), spec, name, capsys)
    assert code == 0, err
    held = {item["name"]: item for item in out["held"]}
    expected = parent._one_line(f"first\nline\x07 {long_text}", parent.MAX_FIELD_CHARS)
    assert expected.endswith("…") and "\x07" not in expected
    assert held[noisy] == {"name": noisy, "status": "ready", "title": "Zerowidth title", "delivers": expected}
    assert held[plain] == {"name": plain, "status": "ready", "title": "Plain title", "delivers": "one thing."}


def test_a_drop_report_keeps_only_the_name_and_status_of_a_kept_child(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    done = make_followup(env, vault, "Finished child", status="done", spec=spec)
    name = hand_parent(env, vault, spec, [done], status="ready")
    nest(env, vault, done, name)
    code, out, err = run_drop(EnvRunner(env), spec, name, tmp_path, capsys)
    assert code == 0, err
    assert out["kept"] == [{"name": done, "status": "done"}]


FORGED_REASON = (
    "x [click](https://evil.example/?q=1) ![i](https://evil.example/p.png) <!-- hidden --> `tick`"
)
FORGED_REASON_CLEAN = "x click(https://evil.example/?q=1) i(https://evil.example/p.png) -- hidden -- tick"


@pytest.mark.parametrize("char", ["[", "]", "<", ">", "`", "!"])
def test_render_body_strips_each_markdown_active_character_from_a_reason(char):
    assert reason_line(body_for(f"a{char}b")) == "**Selection reason:** ab"


def test_assemble_carries_a_forged_reason_into_the_parent_body_without_markdown_syntax(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=1)
    children = [{**c, "reason": FORGED_REASON} for c in confirm(env, vault, names)]
    code, out, err = run_assemble(tmp_path, EnvRunner(env), spec, children, capsys)
    assert code == 0, err
    assert reason_line(body_of(env, vault, out["parent"])) == f"**Selection reason:** {FORGED_REASON_CLEAN}"


def test_resume_carries_a_forged_listed_reason_into_the_parent_body_without_markdown_syntax(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=1)
    name = hand_parent(env, vault, spec, names, reason=FORGED_REASON)
    code, out, err = run_resume(EnvRunner(env), spec, name, revisions(env, vault, names), tmp_path, capsys)
    assert code == 0, err
    assert reason_line(body_of(env, vault, name)) == f"**Selection reason:** {FORGED_REASON_CLEAN}"


def resume_with_injection(env, vault, spec, tmp_path, capsys, inject):
    """Resume a parent whose one listed child is unheld, running `inject(parent, child)`
    straight after the body rewrite lands."""
    (child,) = make_children(env, vault, spec, count=1)
    held = make_followup(env, vault, "Held child", spec=spec)
    name = hand_parent(env, vault, spec, [held, child])
    nest(env, vault, held, name)

    def hook(argv, stdin, runner):
        if is_body_write(argv, name):
            done = runner.real(argv, stdin)
            inject(name, held)
            return done

    runner = EnvRunner(env, hook)
    code, out, err = run_resume(runner, spec, name, revisions(env, vault, [child]), tmp_path, capsys)
    return name, runner, code, out, err


@pytest.mark.parametrize("change", ["stray-nested", "held-child-blocked"])
def test_resume_does_not_flip_when_the_held_set_changed_after_the_body_rewrite(
    lore_env, spec, tmp_path, capsys, change
):
    env, vault = lore_env
    stray = make_followup(env, vault, "Stray", spec=spec)

    def inject(name, held):
        if change == "stray-nested":
            nest(env, vault, stray, name)
        else:
            _lore(env, "record", "update", f"task/{held}", "--vault", vault, "--status", "blocked")

    name, runner, code, out, err = resume_with_injection(env, vault, spec, tmp_path, capsys, inject)
    assert code == 2
    assert reason_codes(err) == ["reason-code: nesting-incomplete"]
    assert out["buildable"] is False
    assert {"name": name, "write": "ready"} in out["failed"]
    assert not any(is_flip(a) for a in runner.calls)
    assert sidecar_of(env, vault, name)["status"] == "open"


def test_a_symlinked_children_file_is_refused_for_both_loaders(tmp_path, capsys):
    real = tmp_path / "real.json"
    real.write_text(json.dumps([GOOD]))
    link = tmp_path / "link.json"
    link.symlink_to(real)
    for argv in (["assemble", "s", "--children", str(link)], ["resume", "s", "p", "--children", str(link)]):
        code, out, err, _ = assemble_raw(tmp_path, None, capsys, argv=argv)
        assert code == 2 and reason_of(err) == "invalid-children-file"


def test_a_children_file_that_is_not_a_regular_file_is_refused_even_when_it_holds_valid_json(
    tmp_path, capsys
):
    fifo = tmp_path / "children.fifo"
    os.mkfifo(fifo)
    keep_open = os.open(fifo, os.O_RDWR)
    try:
        os.write(keep_open, json.dumps([GOOD]).encode())
        code, out, err, runner = assemble_raw(
            tmp_path, None, capsys, argv=["assemble", "s", "--children", str(fifo)]
        )
    finally:
        os.close(keep_open)
    assert code == 2 and out == "" and reason_of(err) == "invalid-children-file"
    assert runner.calls == []


def test_a_children_file_over_the_cap_is_refused_and_one_at_the_cap_is_read(tmp_path, capsys):
    cap = parent.MAX_CHILDREN_FILE_BYTES
    head = json.dumps([{**GOOD, "reason": ""}])
    exact = json.dumps([{**GOOD, "reason": "x" * (cap - len(head))}])
    assert len(exact.encode()) == cap
    code, out, err, runner = assemble_raw(tmp_path, exact + " ", capsys)
    assert code == 2 and reason_of(err) == "invalid-children-file" and runner.calls == []
    (tmp_path / "children.json").write_text(exact)
    assert parent.load_children(str(tmp_path / "children.json"))[0]["name"] == "a"


@pytest.fixture
def small_json_stack(monkeypatch):
    """`json.loads` overflowing its stack on deeply nested text, as it does on interpreters
    whose parser recursion limit is lower than the file-size cap allows depth for."""
    real = json.loads

    def loads(text, *args, **kwargs):
        if isinstance(text, str) and text.startswith("[[[["):
            raise RecursionError("maximum recursion depth exceeded")
        return real(text, *args, **kwargs)

    monkeypatch.setattr(parent.json, "loads", loads)


@pytest.mark.parametrize("command", ["assemble", "resume"])
def test_deeply_nested_json_in_a_children_file_is_invalid_children_file(
    tmp_path, capsys, command, small_json_stack
):
    argv = ["assemble", "s"] if command == "assemble" else ["resume", "s", "p"]
    path = tmp_path / "deep.json"
    path.write_text("[" * 20000 + "]" * 20000)
    code, out, err, runner = assemble_raw(tmp_path, None, capsys, argv=[*argv, "--children", str(path)])
    assert code == 2 and out == ""
    assert reason_of(err) == "invalid-children-file"
    assert runner.calls == []


def test_deeply_nested_json_in_a_reason_file_is_invalid_reason_file(
    lore_env, spec, tmp_path, capsys, small_json_stack
):
    env, vault = lore_env
    name, _ = assembled_parent(env, vault, spec, tmp_path, capsys)
    runner = EnvRunner(env)
    code, out, err = run_drop(runner, spec, name, tmp_path, capsys, raw="[" * 3000)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: invalid-reason-file"]
    assert runner.writes() == []


@pytest.mark.parametrize(
    "reason",
    ["## Forged heading", "# Forged heading", "- forged item", "**Covers:** AC1"],
)
def test_a_drop_reason_cannot_start_a_heading_or_list_under_the_dropped_section(
    lore_env, spec, tmp_path, capsys, reason
):
    body = dropped_text(lore_env, spec, tmp_path, capsys, reason)
    assert dropped_section(body).strip() == f"Reason: {reason}"
    assert [ln for ln in body.splitlines() if ln.startswith("#")].count("## Dropped") == 1
    assert not any(ln.startswith(("## Forged", "# Forged", "- forged")) for ln in body.splitlines())


# --- confirmation codes: proof the confirmation was shown before the write it gates -----------------


def cli(runner, *argv, capsys):
    code = parent.main(list(argv), runner=runner, today=DAY)
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def write_json(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(json.dumps(value))
    return str(path)


def printed_confirm(env, spec_name, names, tmp_path, capsys, parent_name=None, reasons=None):
    argv = ["check", spec_name, *names]
    if parent_name:
        argv += ["--parent", parent_name]
    if reasons is not None:
        argv += ["--reasons", write_json(tmp_path, "reasons.json", reasons)]
    code, out, err = cli(EnvRunner(env), *argv, capsys=capsys)
    assert code == 0, err
    return out["confirm"]


def reasons_for(names, text="worth doing"):
    return {n: f"{text}: {n}" for n in names}


def assemble_with(runner, spec_name, children, tmp_path, capsys, confirm_code):
    argv = ["assemble", spec_name, "--children", write_json(tmp_path, "children.json", children)]
    if confirm_code is not None:
        argv += ["--confirm", confirm_code]
    return cli(runner, *argv, capsys=capsys)


def test_check_prints_a_confirm_that_assemble_takes_to_create_and_nest(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=2)
    code = printed_confirm(env, spec, names, tmp_path, capsys, reasons=reasons_for(names))
    assert len(code) == 4
    status, out, err = assemble_with(
        EnvRunner(env), spec, confirm(env, vault, names), tmp_path, capsys, code
    )
    assert status == 0, err
    assert out["buildable"] is True and sorted(out["nested"]) == sorted(names)
    assert parents_of(env, vault, names) == [out["parent"]] * 2


def test_assemble_with_only_one_of_two_confirmed_children_is_a_mismatch_and_creates_nothing(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=2)
    code = printed_confirm(env, spec, names, tmp_path, capsys, reasons=reasons_for(names))
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    status, out, err = assemble_with(runner, spec, confirm(env, vault, names[:1]), tmp_path, capsys, code)
    assert status == 1 and out is None
    assert reason_codes(err) == ["reason-code: confirmation-mismatch"]
    assert runner.calls == [] and vault_snapshot(env, vault) == before


@pytest.mark.parametrize("typed", [None, "", "   "], ids=["absent", "empty", "blank"])
def test_assemble_without_a_code_is_confirmation_missing_and_makes_no_lore_call(
    lore_env, spec, tmp_path, capsys, typed
):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=2)
    runner = EnvRunner(env)
    status, out, err = assemble_with(runner, spec, confirm(env, vault, names), tmp_path, capsys, typed)
    assert status == 1 and out is None
    assert reason_codes(err) == ["reason-code: confirmation-missing"]
    assert runner.calls == []
    assert parents_of(env, vault, names) == [None] * 2


def test_assemble_takes_the_code_in_any_case_and_with_surrounding_space(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=1)
    code = printed_confirm(env, spec, names, tmp_path, capsys, reasons=reasons_for(names))
    status, _, err = assemble_with(
        EnvRunner(env), spec, confirm(env, vault, names), tmp_path, capsys, f"  {code.upper()} "
    )
    assert status == 0, err


def test_assemble_with_the_confirmed_code_but_one_changed_reason_is_a_mismatch_and_creates_nothing(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=2)
    code = printed_confirm(env, spec, names, tmp_path, capsys, reasons=reasons_for(names))
    children = confirm(env, vault, names)
    children[1]["reason"] = "a different reason than the one shown"
    runner = EnvRunner(env)
    status, out, err = assemble_with(runner, spec, children, tmp_path, capsys, code)
    assert status == 1 and out is None
    assert reason_codes(err) == ["reason-code: confirmation-mismatch"]
    assert runner.writes() == [] and parents_of(env, vault, names) == [None] * 2


def test_a_reason_that_differs_only_in_what_is_sanitized_away_keeps_the_code(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=1)
    shown = {names[0]: "fixes the widget"}
    code = printed_confirm(env, spec, names, tmp_path, capsys, reasons=shown)
    children = confirm(env, vault, names)
    children[0]["reason"] = "fixes\n  the `widget`\u200b"
    status, _, err = assemble_with(EnvRunner(env), spec, children, tmp_path, capsys, code)
    assert status == 0, err


def test_the_code_over_reasons_differs_from_the_code_without_them(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=1)
    with_reasons = printed_confirm(env, spec, names, tmp_path, capsys, reasons=reasons_for(names))
    without = printed_confirm(env, spec, names, tmp_path, capsys)
    assert with_reasons != without


def test_a_child_edited_after_the_check_changes_the_code_assemble_needs(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=1)
    reasons = reasons_for(names)
    before = printed_confirm(env, spec, names, tmp_path, capsys, reasons=reasons)
    _lore(env, "record", "update", f"task/{names[0]}", "--vault", vault, stdin=CLEAN + "\nMore detail.\n")
    after = printed_confirm(env, spec, names, tmp_path, capsys, reasons=reasons)
    assert before != after


def test_the_eligible_set_alone_changes_the_code(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=2)
    both = printed_confirm(env, spec, names, tmp_path, capsys)
    one = printed_confirm(env, spec, names[:1], tmp_path, capsys)
    assert both != one


def test_check_with_a_reasons_file_missing_an_eligible_name_is_invalid_reason_file(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=2)
    reasons = write_json(tmp_path, "reasons.json", reasons_for(names[:1]))
    code, out, err = cli(EnvRunner(env), "check", spec, *names, "--reasons", reasons, capsys=capsys)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: invalid-reason-file"]


def test_check_with_a_parent_and_reasons_is_invalid_arguments(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec, count=1)
    holder = hand_parent(env, vault, spec, names)
    reasons = write_json(tmp_path, "reasons.json", reasons_for(names))
    code, out, err = cli(
        EnvRunner(env), "check", spec, *names, "--parent", holder, "--reasons", reasons, capsys=capsys
    )
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: invalid-arguments"]


def resume_with(runner, spec_name, parent_name, children, tmp_path, capsys, confirm_code):
    argv = ["resume", spec_name, parent_name, "--children", write_json(tmp_path, "resume.json", children)]
    if confirm_code is not None:
        argv += ["--confirm", confirm_code]
    return cli(runner, *argv, capsys=capsys)


def test_resume_accepts_the_parent_check_code_and_refuses_the_plain_check_code(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    holder, names = interrupted(env, vault, spec, tmp_path, capsys)
    rest = names[1:]
    with_parent = printed_confirm(env, spec, rest, tmp_path, capsys, parent_name=holder)
    plain = printed_confirm(env, spec, rest, tmp_path, capsys)
    assert with_parent != plain
    refused_runner = EnvRunner(env)
    status, out, err = resume_with(refused_runner, spec, holder, revisions(env, vault, rest), tmp_path, capsys, plain)
    assert status == 1 and out is None
    assert reason_codes(err) == ["reason-code: confirmation-mismatch"]
    assert refused_runner.writes() == [] and parents_of(env, vault, names) == [holder, None, None]
    status, out, err = resume_with(
        EnvRunner(env), spec, holder, revisions(env, vault, rest), tmp_path, capsys, with_parent
    )
    assert status == 0, err
    assert out["buildable"] is True and parents_of(env, vault, names) == [holder] * 3


def test_the_parent_alone_changes_the_check_code_and_resume_binds_to_it(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    holder, names = interrupted(env, vault, spec, tmp_path, capsys)
    rest = names[1:]
    other = hand_parent(env, vault, spec, [], status="open")
    assert printed_confirm(env, spec, rest, tmp_path, capsys, parent_name=holder) != printed_confirm(
        env, spec, rest, tmp_path, capsys, parent_name=other
    )
    wrong = printed_confirm(env, spec, rest, tmp_path, capsys, parent_name=other)
    runner = EnvRunner(env)
    status, out, err = resume_with(runner, spec, holder, revisions(env, vault, rest), tmp_path, capsys, wrong)
    assert status == 1 and out is None
    assert reason_codes(err) == ["reason-code: confirmation-mismatch"]
    assert runner.writes() == []


def test_resume_binds_to_the_children_and_their_revisions(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    holder, names = interrupted(env, vault, spec, tmp_path, capsys)
    rest = names[1:]
    code = printed_confirm(env, spec, rest, tmp_path, capsys, parent_name=holder)
    runner = EnvRunner(env)
    status, out, err = resume_with(
        runner, spec, holder, revisions(env, vault, rest[:1]), tmp_path, capsys, code
    )
    assert status == 1 and out is None
    assert reason_codes(err) == ["reason-code: confirmation-mismatch"]
    assert runner.writes() == []
    stale = revisions(env, vault, rest)
    stale[0]["revision"] = "not-the-revision-shown"
    status, out, err = resume_with(runner, spec, holder, stale, tmp_path, capsys, code)
    assert status == 1 and reason_codes(err) == ["reason-code: confirmation-mismatch"]
    assert runner.writes() == []


def test_resume_without_a_code_is_confirmation_missing_and_makes_no_lore_call(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    holder, names = interrupted(env, vault, spec, tmp_path, capsys)
    runner = EnvRunner(env)
    status, out, err = resume_with(runner, spec, holder, revisions(env, vault, names[1:]), tmp_path, capsys, None)
    assert status == 1 and out is None
    assert reason_codes(err) == ["reason-code: confirmation-missing"]
    assert runner.calls == []


def state_json(env, spec_name, parent_name, capsys, reason_path=None):
    argv = ["state", spec_name, parent_name]
    if reason_path:
        argv += ["--reason-file", reason_path]
    code, out, err = cli(EnvRunner(env), *argv, capsys=capsys)
    assert code == 0, err
    return out


def drop_with(runner, spec_name, parent_name, reason_path, capsys, confirm_code):
    argv = ["drop", spec_name, parent_name, "--reason-file", reason_path]
    if confirm_code is not None:
        argv += ["--confirm", confirm_code]
    return cli(runner, *argv, capsys=capsys)


def test_state_prints_a_drop_confirm_that_drop_takes_to_un_nest_and_drop(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    holder, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    reason = write_json(tmp_path, "why.json", "plans changed")
    code = state_json(env, spec, holder, capsys, reason)["drop-confirm"]
    assert len(code) == 4
    status, out, err = drop_with(EnvRunner(env), spec, holder, reason, capsys, code)
    assert status == 0, err
    assert out["dropped"] is True and sorted(out["un-nested"]) == sorted(names)
    assert sidecar_of(env, vault, holder)["status"] == "dropped"


def test_drop_refuses_the_shown_code_after_a_held_child_changed_status_and_un_nests_nothing(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    holder, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    reason = write_json(tmp_path, "why.json", "plans changed")
    code = state_json(env, spec, holder, capsys, reason)["drop-confirm"]
    _lore(env, "record", "update", f"task/{names[0]}", "--vault", vault, "--status", "blocked")
    runner = EnvRunner(env)
    status, out, err = drop_with(runner, spec, holder, reason, capsys, code)
    assert status == 1 and out is None
    assert reason_codes(err) == ["reason-code: confirmation-mismatch"]
    assert runner.writes() == []
    assert parents_of(env, vault, names) == [holder] * 2
    assert sidecar_of(env, vault, holder)["status"] == "ready"


def test_a_held_child_status_alone_changes_the_drop_confirm(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    holder, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    reason = write_json(tmp_path, "why.json", "plans changed")
    before = state_json(env, spec, holder, capsys, reason)["drop-confirm"]
    _lore(env, "record", "update", f"task/{names[0]}", "--vault", vault, "--status", "blocked")
    assert state_json(env, spec, holder, capsys, reason)["drop-confirm"] != before


def test_a_held_child_alone_changes_the_drop_confirm(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    holder, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    reason = write_json(tmp_path, "why.json", "plans changed")
    before = state_json(env, spec, holder, capsys, reason)["drop-confirm"]
    unnest(env, vault, names[0])
    assert state_json(env, spec, holder, capsys, reason)["drop-confirm"] != before


def test_the_drop_reason_alone_changes_the_drop_confirm_and_drop_binds_to_it(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    holder, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    shown = write_json(tmp_path, "shown.json", "plans changed")
    other = write_json(tmp_path, "other.json", "a different reason")
    code = state_json(env, spec, holder, capsys, shown)["drop-confirm"]
    assert code != state_json(env, spec, holder, capsys, other)["drop-confirm"]
    assert code != state_json(env, spec, holder, capsys)["drop-confirm"]
    runner = EnvRunner(env)
    status, out, err = drop_with(runner, spec, holder, other, capsys, code)
    assert status == 1 and out is None
    assert reason_codes(err) == ["reason-code: confirmation-mismatch"]
    assert runner.writes() == []


def test_a_drop_reason_that_differs_only_in_what_is_sanitized_away_keeps_the_drop_confirm(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    holder, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    shown = write_json(tmp_path, "shown.json", "plans changed")
    typed = write_json(tmp_path, "typed.json", "plans\n   changed​")
    code = state_json(env, spec, holder, capsys, shown)["drop-confirm"]
    status, _, err = drop_with(EnvRunner(env), spec, holder, typed, capsys, code)
    assert status == 0, err


def test_drop_without_a_code_is_confirmation_missing_and_writes_nothing(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    holder, names = assembled_parent(env, vault, spec, tmp_path, capsys)
    reason = write_json(tmp_path, "why.json", "plans changed")
    runner = EnvRunner(env)
    status, out, err = drop_with(runner, spec, holder, reason, capsys, None)
    assert status == 1 and out is None
    assert reason_codes(err) == ["reason-code: confirmation-missing"]
    assert runner.writes() == []
    assert parents_of(env, vault, names) == [holder] * 2


def test_a_code_minted_for_one_write_does_not_satisfy_another(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    holder, names = interrupted(env, vault, spec, tmp_path, capsys)
    reason = write_json(tmp_path, "why.json", "plans changed")
    drop_code = state_json(env, spec, holder, capsys, reason)["drop-confirm"]
    runner = EnvRunner(env)
    status, _, err = resume_with(runner, spec, holder, [], tmp_path, capsys, drop_code)
    assert status == 1 and reason_codes(err) == ["reason-code: confirmation-mismatch"]
    assert runner.writes() == []


def test_state_with_an_unreadable_reason_file_is_invalid_reason_file(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    holder, _ = assembled_parent(env, vault, spec, tmp_path, capsys)
    code, out, err = cli(
        EnvRunner(env), "state", spec, holder, "--reason-file", str(tmp_path / "nope.json"), capsys=capsys
    )
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: invalid-reason-file"]


def test_check_with_the_parent_and_no_names_mints_a_code_that_resumes_a_parent_holding_every_listed_child(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    runner = EnvRunner(env, lambda a, s, r: refused(a) if is_flip(a) else None)
    _, assembled, _ = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    parent_name = assembled["parent"]
    assert sidecar_of(env, vault, parent_name)["status"] == "open"
    code, checked, err = run_check_json(EnvRunner(env), spec, "--parent", parent_name, capsys=capsys)
    assert code == 0, err
    assert checked["eligible"] == [] and checked["left-out"] == []
    code, out, err = run_resume(
        EnvRunner(env), spec, parent_name, [], tmp_path, capsys, typed=checked["confirm"]
    )
    assert code == 0, err
    assert out["buildable"] is True
    assert sidecar_of(env, vault, parent_name)["status"] == "ready"


def test_the_no_names_resume_code_is_specific_to_its_parent_and_to_the_children_it_covers(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys, count=3)
    _, empty, _ = run_check_json(EnvRunner(env), spec, "--parent", parent_name, capsys=capsys)
    _, one, _ = run_check_json(EnvRunner(env), spec, names[2], "--parent", parent_name, capsys=capsys)
    assert empty["confirm"] != parent.check_confirmation(spec, [], "another-parent", None)
    assert empty["confirm"] != one["confirm"]


def test_the_resume_code_differs_when_the_names_the_resume_removes_from_the_list_differ():
    children = [{"name": "kept", "revision": "r1"}]
    none = parent.check_confirmation("s", children, "p", None, removed=[])
    one = parent.check_confirmation("s", children, "p", None, removed=["gone-a"])
    two = parent.check_confirmation("s", children, "p", None, removed=["gone-a", "gone-b"])
    assert len({none, one, two}) == 3
    assert one == parent.check_confirmation("s", children, "p", None, removed=["gone-a"])


def test_resume_refuses_when_a_name_was_added_to_the_list_after_the_check(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    parent_name, names = interrupted(env, vault, spec, tmp_path, capsys, count=3)
    code, checked, err = run_check_json(EnvRunner(env), spec, names[1], "--parent", parent_name, capsys=capsys)
    assert code == 0, err
    late = make_followup(env, vault, "Added late", spec=spec)
    listed = [{"name": n, "reason": "worth doing"} for n in [*names, late]]
    _lore(env, "record", "update", f"task/{parent_name}", "--vault", vault, stdin=parent.render_body(spec, listed))
    before = vault_snapshot(env, vault)
    runner = EnvRunner(env)
    code, out, err = run_resume(
        runner, spec, parent_name, revisions(env, vault, [names[1]]), tmp_path, capsys, typed=checked["confirm"]
    )
    assert code == 1 and out is None
    assert reason_codes(err) == ["reason-code: confirmation-mismatch"]
    assert runner.writes() == [] and vault_snapshot(env, vault) == before


def test_check_without_the_parent_and_with_no_names_is_refused_as_invalid_arguments(lore_env, spec, capsys):
    env, vault = lore_env
    runner = EnvRunner(env)
    code, out, err = run_check_json(runner, spec, capsys=capsys)
    assert code == 2 and out is None
    assert reason_codes(err) == ["reason-code: invalid-arguments"]
    assert runner.writes() == []
