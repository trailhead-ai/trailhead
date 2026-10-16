"""followup_parent.py `check`: sorts refined follow-ups into eligible and left out.

End-to-end cases run the script as a subprocess against the real `lore` CLI reading
a throwaway vault (the pattern of test_followup_gather.py); failure cases drive the
module's functions with an injected `lore` runner.
"""

from __future__ import annotations

import importlib.util
import json
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
