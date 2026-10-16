"""followup_parent.py `check` (sorts refined follow-ups into eligible and left out) and
`assemble` (nests the confirmed ones under one follow-up parent, or undoes itself).

End-to-end cases run the script as a subprocess against the real `lore` CLI reading
a throwaway vault (the pattern of test_followup_gather.py); failure cases drive the
module's functions with an injected `lore` runner.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
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
    env, vault, title, status="ready", body=CLEAN, labels=(), parent=None, depends=(), spec=None
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
    name = make_followup(env, vault, "Clean one")
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
    name = make_followup(env, vault, "Subject", **kwargs)
    result = check_ok(env, spec, name)
    assert result["eligible"] == []
    assert left_out(result)[name] == {"name": name, "reason": reason}


def test_a_follow_up_with_a_parent_is_already_nested(lore_env, spec):
    env, vault = lore_env
    holder = make_followup(env, vault, "Holder", status="open")
    name = make_followup(env, vault, "Nested", parent=holder)
    result = check_ok(env, spec, name)
    assert left_out(result)[name] == {"name": name, "reason": "already-nested"}


def test_a_ready_follow_up_carrying_a_route_label_is_routed_not_eligible(lore_env, spec):
    env, vault = lore_env
    name = make_followup(env, vault, "Routed ready", labels=["route=plan"])
    result = check_ok(env, spec, name)
    assert left_out(result)[name]["reason"] == "routed-to-plan"


def test_the_verdicts_are_decided_per_follow_up_within_one_call(lore_env, spec):
    env, vault = lore_env
    good = make_followup(env, vault, "Good")
    bad = make_followup(env, vault, "Bad", status="blocked")
    result = check_ok(env, spec, bad, good)
    assert list(eligible(result)) == [good]
    assert list(left_out(result)) == [bad]


# --- dependencies ------------------------------------------------------------------


def test_a_dependency_on_a_done_task_outside_the_set_keeps_it_eligible(lore_env, spec):
    env, vault = lore_env
    dep = make_followup(env, vault, "Finished", status="done")
    name = make_followup(env, vault, "Needs finished", depends=[dep])
    assert list(eligible(check_ok(env, spec, name))) == [name]


def test_a_dependency_on_an_open_task_outside_the_set_is_depends_outside(lore_env, spec):
    env, vault = lore_env
    dep = make_followup(env, vault, "Unfinished", status="open")
    name = make_followup(env, vault, "Needs unfinished", depends=[dep])
    result = check_ok(env, spec, name)
    assert left_out(result)[name] == {
        "name": name, "reason": "depends-outside", "dependency": dep,
    }


def test_a_dependency_on_a_dropped_task_is_depends_outside(lore_env, spec):
    env, vault = lore_env
    dep = make_followup(env, vault, "Dropped dep", status="dropped")
    name = make_followup(env, vault, "Needs dropped", depends=[dep])
    assert left_out(check_ok(env, spec, name))[name]["dependency"] == dep


def test_a_dependency_on_a_superseded_task_is_depends_outside(lore_env, spec):
    env, vault = lore_env
    dep = make_followup(env, vault, "Superseded dep", status="open")
    successor = make_followup(env, vault, "Successor", status="open")
    _lore(env, "record", "supersede", f"task/{dep}", "--by", f"task/{successor}")
    name = make_followup(env, vault, "Needs superseded", depends=[dep])
    result = check_ok(env, spec, name)
    assert left_out(result)[name] == {
        "name": name, "reason": "depends-outside", "dependency": dep,
    }


def test_a_dependency_on_an_eligible_sibling_keeps_it_eligible(lore_env, spec):
    env, vault = lore_env
    first = make_followup(env, vault, "First")
    second = make_followup(env, vault, "Second", depends=[first])
    assert set(eligible(check_ok(env, spec, second, first))) == {first, second}


def test_a_dependent_sorting_before_its_dependency_chain_is_still_eligible(lore_env, spec):
    env, vault = lore_env
    base = make_followup(env, vault, "Zulu base")
    middle = make_followup(env, vault, "Yankee middle", depends=[base])
    top = make_followup(env, vault, "Alpha top", depends=[middle])
    assert set(eligible(check_ok(env, spec, top, middle, base))) == {top, middle, base}


def test_a_dependency_on_a_left_out_sibling_is_left_out_naming_it(lore_env, spec):
    env, vault = lore_env
    first = make_followup(env, vault, "First", status="blocked")
    second = make_followup(env, vault, "Second", depends=[first])
    result = check_ok(env, spec, first, second)
    assert result["eligible"] == []
    assert left_out(result)[first]["reason"] == "not-ready"
    assert left_out(result)[second] == {
        "name": second, "reason": "depends-outside", "dependency": first,
    }


def test_a_two_step_chain_through_a_left_out_sibling_is_left_out_throughout(lore_env, spec):
    env, vault = lore_env
    root = make_followup(env, vault, "Root", status="blocked")
    middle = make_followup(env, vault, "Middle", depends=[root])
    leaf = make_followup(env, vault, "Leaf", depends=[middle])
    result = check_ok(env, spec, leaf, middle, root)
    assert result["eligible"] == []
    assert left_out(result)[middle]["dependency"] == root
    assert left_out(result)[leaf] == {
        "name": leaf, "reason": "depends-outside", "dependency": middle,
    }


def test_a_dependency_on_a_task_that_does_not_exist_is_depends_outside(lore_env, spec):
    env, vault = lore_env
    dep = make_followup(env, vault, "Soon gone", status="open")
    name = make_followup(env, vault, "Dangling", depends=[dep])
    _lore(env, "record", "delete", f"task/{dep}", "--vault", vault)
    assert left_out(check_ok(env, spec, name))[name]["dependency"] == dep


# --- revision, delivers, files --------------------------------------------------------


def shown_revision(env, vault, name):
    out = _lore(env, "record", "show", "--json", "--vault", vault, f"task/{name}")
    return json.loads(out)["revision"]


def test_the_emitted_revision_is_the_one_lore_reports_and_follows_updates(lore_env, spec):
    env, vault = lore_env
    name = make_followup(env, vault, "Revised")
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
    name = make_followup(env, vault, "Fields", body=body)
    item = eligible(check_ok(env, spec, name))[name]
    assert item["delivers"] == "First sentence. Second sentence over two lines."
    assert item["files"] == "`src/a.py` `src/b.py` (new)"


def test_a_missing_field_is_emitted_empty(lore_env, spec):
    env, vault = lore_env
    name = make_followup(env, vault, "No fields", body="Just prose.\n")
    item = eligible(check_ok(env, spec, name))[name]
    assert item["delivers"] == "" and item["files"] == ""


def test_a_credential_in_a_field_is_redacted(lore_env, spec):
    env, vault = lore_env
    body = "**Delivers:** Rotate AKIAIOSFODNN7EXAMPLE now.\n\n**Files:** `k/AKIAIOSFODNN7EXAMPLE.py`\n"
    name = make_followup(env, vault, "Leaky", body=body)
    r = run_check(env, spec, name)
    assert r.returncode == 0, r.stderr
    assert "AKIAIOSFODNN7EXAMPLE" not in r.stdout
    item = eligible(json.loads(r.stdout))[name]
    assert "[REDACTED]" in item["delivers"] and "[REDACTED]" in item["files"]


def test_control_and_invisible_characters_in_a_field_are_stripped(lore_env, spec):
    env, vault = lore_env
    body = "**Delivers:** a\x1b[31mred\x07 and​zero‮width\n\n**Files:** `x.py`\n"
    name = make_followup(env, vault, "Hostile", body=body)
    item = eligible(check_ok(env, spec, name))[name]
    assert item["delivers"] == "a[31mred andzerowidth"


def test_an_over_long_field_is_truncated_with_the_marker(lore_env, spec):
    env, vault = lore_env
    limit = parent.MAX_FIELD_CHARS
    text = sentence(limit + 50)
    body = f"**Delivers:** {text}\n\n**Files:** `src/a.py` {sentence(limit)}\n"
    name = make_followup(env, vault, "Long", body=body)
    item = eligible(check_ok(env, spec, name))[name]
    assert item["delivers"] == text[:limit] + parent.gather.TRUNCATION_MARKER
    assert item["files"].endswith(parent.gather.TRUNCATION_MARKER)
    assert len(item["files"]) == limit + 1


def test_a_field_one_past_the_bound_is_truncated(lore_env, spec):
    env, vault = lore_env
    limit = parent.MAX_FIELD_CHARS
    text = sentence(limit + 1)
    name = make_followup(env, vault, "Past", body=f"**Delivers:** {text}\n")
    item = eligible(check_ok(env, spec, name))[name]
    assert item["delivers"] == text[:limit] + parent.gather.TRUNCATION_MARKER


def test_a_field_at_exactly_the_bound_is_not_truncated(lore_env, spec):
    env, vault = lore_env
    limit = parent.MAX_FIELD_CHARS
    text = sentence(limit)
    name = make_followup(env, vault, "Edge", body=f"**Delivers:** {text}\n")
    item = eligible(check_ok(env, spec, name))[name]
    assert item["delivers"] == text


# --- overlaps ----------------------------------------------------------------------------


def test_two_eligible_follow_ups_naming_one_file_overlap(lore_env, spec):
    env, vault = lore_env
    one = make_followup(env, vault, "One", body="**Delivers:** a\n\n**Files:** `src/shared.py`, `src/one.py`\n")
    two = make_followup(env, vault, "Two", body="**Delivers:** b\n\n**Files:** `src/shared.py`, `src/two.py`\n")
    result = check_ok(env, spec, two, one)
    assert result["overlaps"] == [{"file": "src/shared.py", "names": sorted([one, two])}]


def test_follow_ups_with_disjoint_files_have_no_overlaps(lore_env, spec):
    env, vault = lore_env
    one = make_followup(env, vault, "One", body="**Delivers:** a\n\n**Files:** `src/one.py`\n")
    two = make_followup(env, vault, "Two", body="**Delivers:** b\n\n**Files:** `src/two.py`\n")
    assert check_ok(env, spec, one, two)["overlaps"] == []


def test_a_credential_shaped_path_in_an_overlap_is_redacted(lore_env, spec):
    env, vault = lore_env
    body = "**Delivers:** a\n\n**Files:** `k/AKIAIOSFODNN7EXAMPLE.py`\n"
    one = make_followup(env, vault, "One", body=body)
    two = make_followup(env, vault, "Two", body=body)
    r = run_check(env, spec, one, two)
    assert r.returncode == 0, r.stderr
    assert "AKIAIOSFODNN7EXAMPLE" not in r.stdout
    assert json.loads(r.stdout)["overlaps"] == [
        {"file": "k/[REDACTED].py", "names": sorted([one, two])}
    ]


def test_a_left_out_follow_up_does_not_create_an_overlap(lore_env, spec):
    env, vault = lore_env
    one = make_followup(env, vault, "One", body="**Delivers:** a\n\n**Files:** `src/s.py`\n")
    two = make_followup(
        env, vault, "Two", status="blocked", body="**Delivers:** b\n\n**Files:** `src/s.py`\n"
    )
    assert check_ok(env, spec, one, two)["overlaps"] == []


def test_a_path_in_prose_is_not_an_overlap(lore_env, spec):
    env, vault = lore_env
    one = make_followup(env, vault, "One", body="**Delivers:** a\n\n**Files:** src/s.py\n")
    two = make_followup(env, vault, "Two", body="**Delivers:** b\n\n**Files:** src/s.py\n")
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
        raise AssertionError(f"unexpected lore call {argv}")


def task_doc(status="ready", body=CLEAN, revision="rev1", **sidecar):
    return {
        "revision": revision, "body": body,
        "sidecar": {"kind": "task", "status": status, "title": "t", **sidecar},
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


def test_a_depends_on_entry_that_is_not_a_task_name_is_a_malformed_record(capsys):
    runner = Lore(tasks={"task/f": task_doc(**{"depends-on": ["-flag"]})})
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


def test_a_qualified_spec_dependency_is_not_a_task_dependency(capsys):
    runner = Lore(tasks={"task/f": task_doc(**{"depends-on": ["spec/other@stage"]})})
    code, out, _ = run_main(runner, "check", "s", "f", capsys=capsys)
    assert code == 0
    assert [i["name"] for i in json.loads(out)["eligible"]] == ["f"]


# --- the importable seam later subcommands rely on ----------------------------------------------


def test_check_followups_returns_the_structure_the_json_emits(capsys):
    runner = Lore(tasks={"task/good": task_doc(), "task/bad": task_doc(status="blocked")})
    result = parent.check_followups(runner, "s", ["good", "bad"])
    assert set(result) == {"eligible", "left-out", "overlaps"}
    assert [i["name"] for i in result["eligible"]] == ["good"]
    assert result["left-out"] == [{"name": "bad", "reason": "not-ready"}]
    code, out, _ = run_main(runner, "check", "s", "good", "bad", capsys=capsys)
    assert code == 0
    assert json.loads(out) == result


def test_read_revision_returns_the_revision_lore_reports():
    runner = Lore(tasks={"task/f": task_doc(revision="abc123")})
    assert parent.read_revision(runner, "v", "f") == "abc123"


def test_read_revision_of_a_missing_record_is_followup_not_found():
    with pytest.raises(parent.ParentError) as err:
        parent.read_revision(Lore(), "v", "gone")
    assert err.value.reason == "followup-not-found"


def test_every_left_out_reason_the_script_emits_is_a_member_of_the_constant(lore_env, spec):
    env, vault = lore_env
    holder = make_followup(env, vault, "Holder", status="open")
    made = {
        "unresolved-question": make_followup(env, vault, "U", status="open", body=UNRESOLVED_BODY),
        "routed-to-plan": make_followup(env, vault, "P", status="open", labels=["route=plan"]),
        "routed-to-brainstorm": make_followup(env, vault, "B", status="open", labels=["route=brainstorm"]),
        "not-ready": make_followup(env, vault, "N", status="blocked"),
        "already-nested": make_followup(env, vault, "A", parent=holder),
        "is-a-parent": make_followup(env, vault, "I", labels=["craft/followup-parent"]),
        "depends-outside": make_followup(env, vault, "D", depends=[holder]),
    }
    result = check_ok(env, spec, *made.values())
    emitted = {i["name"]: i["reason"] for i in result["left-out"]}
    assert emitted == {name: reason for reason, name in made.items()}
    assert set(emitted.values()) == parent.LEFT_OUT_REASONS


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


def run_assemble(tmp_path, runner, spec, children, capsys, today=DAY):
    path = tmp_path / "children.json"
    path.write_text(json.dumps(children))
    code = parent.main(["assemble", spec, "--children", str(path)], runner=runner, today=today)
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def sidecar_of(env, vault, name):
    return json.loads(_lore(env, "record", "show", "--json", "--vault", vault, f"task/{name}"))["sidecar"]


def body_of(env, vault, name):
    return json.loads(_lore(env, "record", "show", "--json", "--vault", vault, f"task/{name}"))["body"]


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
    path = tmp_path / "children.json"
    path.write_text(json.dumps(confirm(env, vault, names)))
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), "assemble", spec, "--children", str(path)],
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
            seen.append([sidecar_of(env, vault, n).get("parent") for n in names])
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
    assert reason_line(body_for("bell\x07 esc\x1b[31m red\x00")) == "**Selection reason:** bell esc[31m red"


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
    _lore(env, "record", "update", f"task/{names[0]}", "--vault", vault, "--parent", holder)
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


# --- failed nesting, failed flip, concurrent parent: undo ------------------------------------


def test_a_failed_second_nesting_undoes_the_first_and_drops_the_parent(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    children = confirm(env, vault, names)
    runner = EnvRunner(env, lambda a, s, r: refused(a) if is_nest(a, names[1]) else None)
    code, out, err = run_assemble(tmp_path, runner, spec, children, capsys)
    assert code == 2
    assert not any(is_flip(call) for call in runner.calls)
    assert [ln for ln in err.splitlines() if ln.startswith("reason-code: ")] == ["reason-code: nesting-incomplete"]
    assert out["buildable"] is False
    assert out["failed"] == [{"name": names[1], "write": "nest"}]
    assert out["nested"] == []
    assert out["undone"] == [
        {"name": names[0], "write": "un-nest"},
        {"name": out["parent"], "write": "drop"},
    ]
    assert out["undo-failed"] == []
    assert [sidecar_of(env, vault, n).get("parent") for n in names] == [None] * 3
    assert sidecar_of(env, vault, out["parent"])["status"] == "dropped"
    assert "nesting-incomplete" in body_of(env, vault, out["parent"])
    assert gathered_names(env, spec) == sorted(names)


def test_a_failed_flip_undoes_all_three_and_drops_the_parent(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    runner = EnvRunner(env, lambda a, s, r: refused(a) if is_flip(a) else None)
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert "reason-code: nesting-incomplete" in err.splitlines()
    assert out["buildable"] is False
    assert out["failed"] == [{"name": out["parent"], "write": "ready"}]
    assert [u for u in out["undone"] if u["write"] == "un-nest"] == [
        {"name": n, "write": "un-nest"} for n in reversed(names)
    ]
    assert out["undone"][-1] == {"name": out["parent"], "write": "drop"}
    assert [sidecar_of(env, vault, n).get("parent") for n in names] == [None] * 3
    assert sidecar_of(env, vault, out["parent"])["status"] == "dropped"
    assert gathered_names(env, spec) == sorted(names)


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
    assert out["buildable"] is False
    assert sidecar_of(env, vault, out["parent"])["status"] == "dropped"
    assert {u["name"] for u in out["undone"]} == {names[0], names[1], out["parent"]}


def test_a_nesting_that_reads_back_under_a_different_parent_is_a_failed_nesting(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    holder = make_followup(env, vault, "Other holder", status="open")

    def hook(argv, stdin, runner):
        if is_nest(argv, names[1]):
            return runner.real(_swap_parent(argv, holder))

    code, out, _ = run_assemble(tmp_path, EnvRunner(env, hook), spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert out["failed"] == [{"name": names[1], "write": "confirm"}]
    assert sidecar_of(env, vault, out["parent"])["status"] == "dropped"


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
    assert code == 2 and out["buildable"] is False
    assert out["failed"] == [{"name": names[1], "write": "nest"}]
    assert not any(is_nest(w, names[1]) for w in runner.calls)
    assert [sidecar_of(env, vault, n).get("parent") for n in names] == [None] * 3
    assert sidecar_of(env, vault, out["parent"])["status"] == "dropped"


def test_a_flip_that_reads_back_not_ready_is_a_failed_flip(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec)

    def hook(argv, stdin, runner):
        if is_flip(argv):
            return subprocess.CompletedProcess(argv, 0, "", "")

    code, out, _ = run_assemble(tmp_path, EnvRunner(env, hook), spec, confirm(env, vault, names), capsys)
    assert code == 2 and out["buildable"] is False
    assert out["failed"] == [{"name": out["parent"], "write": "ready"}]
    assert sidecar_of(env, vault, out["parent"])["status"] == "dropped"


def test_a_nesting_whose_lore_call_times_out_is_a_failed_nesting_and_is_undone(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)

    def hook(argv, stdin, runner):
        if is_nest(argv, names[1]):
            raise subprocess.TimeoutExpired(argv, 1)

    code, out, _ = run_assemble(tmp_path, EnvRunner(env, hook), spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert out["failed"] == [{"name": names[1], "write": "nest"}]
    assert [sidecar_of(env, vault, n).get("parent") for n in names] == [None] * 3
    assert sidecar_of(env, vault, out["parent"])["status"] == "dropped"


def test_an_unexpected_exception_after_the_create_undoes_the_writes_before_exiting_two(
    lore_env, spec, tmp_path, capsys
):
    env, vault = lore_env
    names = make_children(env, vault, spec)

    def hook(argv, stdin, runner):
        if is_nest(argv, names[1]):
            raise RuntimeError("SECRET boom")

    runner = EnvRunner(env, hook)
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2 and out is None
    assert [ln for ln in err.splitlines() if ln.startswith("reason-code: ")] == ["reason-code: internal-error"]
    assert "SECRET" not in err
    assert [sidecar_of(env, vault, n).get("parent") for n in names] == [None] * 3
    (made,) = created_parent(env, vault, spec)
    assert sidecar_of(env, vault, made)["status"] == "dropped"


def created_parent(env, vault, spec):
    query = f"kind:task related-spec:{spec} has:label.craft.followup-parent"
    hits = json.loads(_lore(env, "search", query, "--json"))["hits"]
    return sorted(h["id"].rsplit("/task/", 1)[1] for h in hits)


def test_a_failed_un_nest_is_reported_with_a_command_that_completes_it(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env
    names = make_children(env, vault, spec)
    runner = EnvRunner(env, lambda a, s, r: refused(a) if is_unnest(a, names[0]) else (
        refused(a) if is_nest(a, names[1]) else None))
    code, out, err = run_assemble(tmp_path, runner, spec, confirm(env, vault, names), capsys)
    assert code == 2
    assert len(out["undo-failed"]) == 1
    failed = out["undo-failed"][0]
    assert (failed["name"], failed["write"]) == (names[0], "un-nest")
    assert sidecar_of(env, vault, names[0])["parent"] == out["parent"]
    assert {"name": out["parent"], "write": "drop"} in out["undone"]
    run = subprocess.run(shlex.split(failed["command"]), capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stderr
    assert sidecar_of(env, vault, names[0]).get("parent") is None


def test_an_un_nest_that_reads_back_still_nested_is_an_undo_failure(lore_env, spec, tmp_path, capsys):
    env, vault = lore_env

    def hook(argv, stdin, runner):
        if is_unnest(argv, names[0]):
            return subprocess.CompletedProcess(argv, 0, "", "")
        if is_nest(argv, names[1]):
            return refused(argv)

    names = make_children(env, vault, spec)
    code, out, _ = run_assemble(tmp_path, EnvRunner(env, hook), spec, confirm(env, vault, names), capsys)
    assert [(f["name"], f["write"]) for f in out["undo-failed"]] == [(names[0], "un-nest")]
    assert {"name": names[0], "write": "un-nest"} not in out["undone"]


@pytest.mark.parametrize("silent", [False, True], ids=["write-refused", "write-silently-ignored"])
def test_a_failed_drop_is_reported_with_the_command_that_completes_it(
    lore_env, spec, tmp_path, capsys, silent
):
    env, vault = lore_env
    names = make_children(env, vault, spec)

    def hook(argv, stdin, runner):
        if is_drop(argv):
            return subprocess.CompletedProcess(argv, 0, "", "") if silent else refused(argv)
        if is_flip(argv):
            return refused(argv)

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
    assert [ln for ln in err.splitlines() if ln.startswith("reason-code: ")] == [
        "reason-code: concurrent-followup-parent"
    ]
    assert f"subject: {' '.join(sorted([out['parent'], rival['name']]))}" in err.splitlines()
    assert out["buildable"] is False and out["nested"] == []
    assert out["undone"] == [{"name": out["parent"], "write": "drop"}]
    assert sidecar_of(env, vault, out["parent"])["status"] == "dropped"
    assert sidecar_of(env, vault, rival["name"])["status"] == "open"
    assert [sidecar_of(env, vault, n).get("parent") for n in names] == [None] * 3


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
    assert [ln for ln in err.splitlines() if ln.startswith("reason-code: ")] == ["reason-code: parent-create-failed"]
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
    code = parent.main(["assemble", "s", "--children", str(path)], runner=Lore(raises=RuntimeError("SECRET")))
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
