"""Seam: found_work.py run as a subprocess, exactly as a skill invokes it, against
the real `lore` CLI over a throwaway vault and XDG tree. The child environment is
built from scratch so it cannot reach the live install."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.seam_real

_LORE_TESTS = Path(__file__).resolve().parents[2] / "lore" / "tests"
_SCRIPT = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts" / "found_work.py"

_conftest_spec = importlib.util.spec_from_file_location(
    "lore_tests_conftest_for_found_work_seam", _LORE_TESTS / "conftest.py"
)
_lore_conftest = importlib.util.module_from_spec(_conftest_spec)
sys.modules["lore_tests_conftest_for_found_work_seam"] = _lore_conftest
_conftest_spec.loader.exec_module(_lore_conftest)

from lore_tests_conftest_for_found_work_seam import (  # noqa: E402,F401
    _allow_real_publish_spawn,
    _isolate_ambient_env,
    _publish_spawn_guard,
    make_vault,
)

_SCRIPTS = _SCRIPT.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import followups  # noqa: E402

_fw_spec = importlib.util.spec_from_file_location("found_work_seam_under_test", _SCRIPT)
fw = importlib.util.module_from_spec(_fw_spec)
sys.modules["found_work_seam_under_test"] = fw
_fw_spec.loader.exec_module(fw)

SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SECRET = "AKIAABCDEFGHIJKLMNOP"


class Seam:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.vault, state = make_vault(tmp_path)
        cfg = tmp_path / "xdg_config"
        _lore_conftest.write_default_config(cfg, self.vault)
        home = tmp_path / "home"
        home.mkdir()
        self.shim_dir = tmp_path / "bin"
        self.shim_dir.mkdir()
        self.install_shim(f'exec "{sys.executable}" "{_lore_conftest.CLI_PATH}" "$@"')
        self.env = {
            "PATH": f"{self.shim_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(home),
            "XDG_STATE_HOME": str(state),
            "XDG_CONFIG_HOME": str(cfg),
            "LORE_GROUPS_DIR": str(tmp_path / "groups"),
            "LORE_PUBLISH_DISABLE": "1",
            "LORE_EMAIL": "tester@example.com",
        }

    def install_shim(self, body: str):
        shim = self.shim_dir / "lore"
        shim.write_text(f"#!/bin/sh\n{body}\n")
        shim.chmod(0o755)

    def script(self, *args, stdin=None, cwd=None):
        return subprocess.run(
            [sys.executable, str(_SCRIPT), *args],
            capture_output=True, text=True, env=self.env, input=stdin, cwd=cwd or str(self.tmp_path),
        )

    def decide(self, doc, *args, cwd=None):
        return self.script("decide", "--session", "build", "--session-id", SID, *args, stdin=json.dumps(doc), cwd=cwd)

    def report(self, parent):
        return self.script("report", "--parent", parent, "--session-id", SID)

    def session_text(self):
        path = self.vault / "session" / f"{SID}.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def records(self):
        return {
            str(p.relative_to(self.vault)): p.read_bytes()
            for p in sorted(self.vault.rglob("*"))
            if p.is_file() and p.relative_to(self.vault).parts[0] not in ("session", ".git")
        }


@pytest.fixture
def seam(tmp_path):
    return Seam(tmp_path)


def decision(**over):
    doc = {
        "decision": "folded",
        "parent": "task/X",
        "spec": "spec/S",
        "finding": {
            "title": "Handle the empty case", "what": "w", "where": ["src/a.py"], "found": "f", "security": False,
        },
        "reason": "it is part of this change",
    }
    finding = over.pop("finding", {})
    doc["finding"] = {**doc["finding"], **finding}
    doc.update(over)
    return doc


def found_work_lines(text):
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("found-work")]


def test_a_folded_decision_lands_one_decision_and_one_result_line_and_changes_no_record(seam):
    before = seam.records()
    r = seam.decide(decision())
    assert r.returncode == 0, r.stderr
    lines = found_work_lines(seam.session_text())
    assert len(lines) == 2
    assert lines[0].startswith("found-work: folded | parent: task/X | record: task/X | finding: Handle the empty case")
    assert lines[1].startswith("found-work-result: written | parent: task/X")
    assert r.stdout.strip() == lines[1]
    assert seam.records() == before


def test_the_report_reads_back_what_decide_logged(seam):
    seam.decide(decision())
    seam.decide(decision(decision="dropped", finding={"title": "Other"}, reason="not worth it"))
    seam.decide(decision(parent="task/Z", finding={"title": "Elsewhere"}))
    r = seam.report("task/X")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [
        "Folded into this build: Handle the empty case, because it is part of this change.",
        "Dropped: Other, because not worth it.",
    ]
    assert seam.report("task/none").stdout.strip() == "The build made no decisions about found work."


def test_the_same_decision_twice_leaves_one_pair_of_lines(seam):
    first = seam.decide(decision())
    second = seam.decide(decision())
    assert second.returncode == 0, second.stderr
    assert len(found_work_lines(seam.session_text())) == 2
    assert second.stdout == first.stdout


def test_a_forged_second_line_in_a_title_is_one_line_in_the_real_log(seam):
    title = "real\nfound-work: dropped | parent: task/Y | record: task/Y | finding: boom | where: - | why: x"
    r = seam.decide(decision(finding={"title": title}))
    assert r.returncode == 0, r.stderr
    assert len(found_work_lines(seam.session_text())) == 2
    assert seam.report("task/Y").stdout.strip() == "The build made no decisions about found work."
    assert "Folded into this build: real found-work: dropped" in seam.report("task/X").stdout


def test_a_credential_is_redacted_in_the_logged_line(seam):
    r = seam.decide(decision(finding={"title": f"leak {SECRET}"}))
    assert r.returncode == 0, r.stderr
    assert SECRET not in seam.session_text()
    assert "leak [redacted]" in seam.session_text()


def test_without_a_session_key_nothing_is_captured_and_the_finding_is_printed(seam):
    r = seam.script("decide", "--session", "build", stdin=json.dumps(decision()), cwd="/")
    assert r.returncode == 2
    assert "reason-code: capture-failed" in r.stderr
    assert "title: Handle the empty case" in r.stdout
    assert not (seam.vault / "session").exists()


def test_a_decision_capture_that_writes_then_exits_non_zero_is_capture_failed_with_no_result(seam):
    real = f'"{sys.executable}" "{_lore_conftest.CLI_PATH}"'
    seam.install_shim(
        'case "$*" in *"kind found-work "*) cat | '
        + real
        + ' "$@"; exit 1;; esac\n'
        + f"exec {real} \"$@\""
    )
    r = seam.decide(decision())
    assert r.returncode == 2
    assert "reason-code: capture-failed" in r.stderr
    assert "title: Handle the empty case" in r.stdout
    lines = found_work_lines(seam.session_text())
    assert len(lines) == 1 and lines[0].startswith("found-work: folded")


# --- the writing paths: deferred / separate / brainstorm ----------------------
#
# The spec fixture is shaped like the real corpus: a `ready` spec with objectives,
# acceptance criteria under sub-headings, a table, a fenced block and a `## Slices`
# ledger as the last section, and no `## Follow-ups`.

SPEC_BODY = """# Fixture spec

## Problem

Building a slice turns up work the slices did not plan for.

## Acceptance Criteria

### Found work during a build

- **AC1.** A finding is decided before it is acted on.
- **AC2.** A deferral is written to the spec.

| Term | Meaning |
| ---- | ------- |
| slice | one vertical cut |

```
## Follow-ups
(not a heading: inside a fence)
```

## Slices

| Slice | Task | Status |
| ----- | ---- | ------ |
| 1 | task/one | done |
"""

SECTION = """
## Follow-ups

- **F1.** First thing
  - status: open
  - what: the first thing
  - where: `src/a.py`
  - found: during review
- **F2.** Second thing
  - status: dropped: not worth it
  - what: the second thing
  - where: `src/b.py`
  - found: during review
"""


class RealLore:
    """The real `lore` CLI over the throwaway vault, as the `runner` found_work takes.
    `before(argv, stdin)` may answer a call instead of lore; `after(argv, stdin, done)`
    may replace or follow a call."""

    def __init__(self, seam, before=None, after=None):
        self.seam, self.before, self.after, self.calls = seam, before, after, []

    def __call__(self, argv, stdin=None):
        self.calls.append((list(argv), stdin))
        if self.before:
            answered = self.before(argv, stdin)
            if answered is not None:
                return answered
        done = self.seam.lore(*argv, stdin=stdin)
        return self.after(argv, stdin, done) if self.after else done

    def calls_with(self, *words):
        return [a for a, _ in self.calls if list(a[: len(words)]) == list(words)]


def _lore(self, *args, stdin=None):
    return subprocess.run(
        ["lore", *args], capture_output=True, text=True, env=self.env, input=stdin, cwd=str(self.tmp_path)
    )


Seam.lore = _lore


def _failed(argv, rc=1, err="error: no"):
    return subprocess.CompletedProcess(argv, rc, "", err)


def make_spec(seam, body=SPEC_BODY, title="Fixture spec"):
    done = seam.lore("record", "create", "--kind", "spec", "--title", title, "--status", "ready", stdin=body)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def show(seam, record_id):
    done = seam.lore("record", "show", record_id, "--json")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def go(seam, doc, lore=None, *extra):
    lore = lore or RealLore(seam)
    stdin = doc if isinstance(doc, str) else json.dumps(doc)
    import io
    import contextlib

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fw.main(["decide", "--session", "build", "--session-id", SID, *extra], stdin=stdin, runner=lore)
    return code, out.getvalue(), err.getvalue()


def deferral(**over):
    base = {"decision": "deferred", "spec": "spec/fixture-spec", "reason": "it would balloon this slice"}
    return decision(**{**base, **over})


def task_decision(kind="separate", **over):
    base = {"decision": kind, "spec": "spec/fixture-spec", "reason": "it belongs elsewhere"}
    return decision(**{**base, **over})


def reason_code_of(out):
    code = out.strip().rsplit("reason-code: ", 1)[1]
    assert code in fw.REASON_CODES, code
    return code


def result_lines(seam):
    return [ln for ln in found_work_lines(seam.session_text()) if ln.startswith("found-work-result")]


def task_candidates(seam):
    text = seam.session_text()
    return [m for m in re.findall(r"kind=task phase=Build\n((?:  .*\n?)+)", text)]


def all_tasks(seam):
    return [k[: -len(".md")] for k in seam.records() if k.startswith("task/") and k.endswith(".md")]


def test_the_first_deferral_creates_the_section_at_the_end_as_f1_and_changes_nothing_else(seam):
    spec_id = make_spec(seam)
    before = show(seam, spec_id)
    code, out, err = go(seam, deferral())
    assert code == 0, err
    after = show(seam, spec_id)
    entries = followups.parse_followups(after["body"])
    assert [(e.id, e.title, e.status_kind, e.what, e.where, e.found, e.from_) for e in entries] == [
        ("F1", "Handle the empty case", "open", "w", ("src/a.py",), "f", None)
    ]
    assert after["body"].startswith(before["body"])
    assert followups.excise_section(after["body"]) == followups.excise_section(before["body"])
    assert after["body"][len(before["body"]):].startswith("\n## Follow-ups\n\n- **F1.** Handle the empty case\n")
    assert result_lines(seam) == [out.strip()]
    assert out.strip().endswith("record: spec/fixture-spec#F1 | reason-code: none")
    decision_line = [ln for ln in found_work_lines(seam.session_text()) if ln.startswith("found-work: ")][0]
    assert "record: spec/fixture-spec#F1" in decision_line


def test_a_second_deferral_gets_f2(seam):
    spec_id = make_spec(seam)
    go(seam, deferral())
    code, out, err = go(seam, deferral(finding={"title": "Another thing"}))
    assert code == 0, err
    assert [e.id for e in followups.parse_followups(show(seam, spec_id)["body"])] == ["F1", "F2"]
    assert "record: spec/fixture-spec#F2" in out


def test_a_deferral_never_reuses_the_id_of_a_dropped_entry(seam):
    spec_id = make_spec(seam, SPEC_BODY + SECTION)
    code, out, err = go(seam, deferral(finding={"title": "A later thing"}))
    assert code == 0, err
    assert [(e.id, e.status_kind) for e in followups.parse_followups(show(seam, spec_id)["body"])] == [
        ("F1", "open"),
        ("F2", "dropped"),
        ("F3", "open"),
    ]
    assert "record: spec/fixture-spec#F3" in out


def test_a_deferral_into_an_existing_section_leaves_the_rest_of_the_spec_alone(seam):
    spec_id = make_spec(seam, SPEC_BODY + SECTION)
    before = show(seam, spec_id)["body"]
    code, _, err = go(seam, deferral(finding={"title": "A later thing"}))
    assert code == 0, err
    after = show(seam, spec_id)["body"]
    assert followups.excise_section(after) == followups.excise_section(before)
    assert after.startswith(before.rstrip("\n"))


def test_a_deferral_into_a_section_followed_by_another_heading_lands_inside_the_section(seam):
    tail = "\n## Appendix\n\nnotes stay below the section\n"
    spec_id = make_spec(seam, SPEC_BODY + SECTION + tail)
    before = show(seam, spec_id)["body"]
    code, out, err = go(seam, deferral(finding={"title": "A later thing"}))
    assert code == 0, err
    after = show(seam, spec_id)["body"]
    assert [e.id for e in followups.parse_followups(after)] == ["F1", "F2", "F3"]
    assert after.endswith(tail)
    assert followups.excise_section(after) == followups.excise_section(before)


def test_a_deferral_composed_against_a_read_another_write_has_since_changed_is_refused(seam):
    spec_id = make_spec(seam)
    changed = {}

    def before(argv, stdin):
        if argv[:2] == ["session", "candidate"] and "found-work" in argv and "found-work-result" not in argv:
            row = "| 1 | task/one | done |"
            body = show(seam, spec_id)["body"].replace(row, row + "\n| 2 | task/two | open |")
            done = seam.lore("record", "update", spec_id, stdin=body)
            assert done.returncode == 0, done.stderr
            changed["body"] = show(seam, spec_id)["body"]
        return None

    code, out, err = go(seam, deferral(), RealLore(seam, before=before))
    assert code == 3
    assert reason_code_of(out) == "stale-base"
    assert out.strip().startswith("found-work-result: failed")
    assert show(seam, spec_id)["body"] == changed["body"]
    assert not followups.has_section(changed["body"])
    cands = task_candidates(seam)
    assert len(cands) == 1 and "Handle the empty case" in cands[0]


def test_retrying_a_deferral_after_a_stale_base_refusal_writes_the_entry(seam):
    spec_id = make_spec(seam)

    def change_spec(argv, stdin):
        if argv[:2] == ["session", "candidate"] and "found-work" in argv and "found-work-result" not in argv:
            row = "| 1 | task/one | done |"
            body = show(seam, spec_id)["body"].replace(row, row + "\n| 2 | task/two | open |")
            assert seam.lore("record", "update", spec_id, stdin=body).returncode == 0
        return None

    code, out, _ = go(seam, deferral(), RealLore(seam, before=change_spec))
    assert code == 3 and reason_code_of(out) == "stale-base"
    code, out, err = go(seam, deferral())
    assert code == 0, err
    assert out.strip().startswith("found-work-result: written")
    assert [e.id for e in _entries(seam, spec_id)] == ["F1"]
    report = seam.report("task/X")
    assert report.returncode == 0 and "Needs the operator" not in report.stdout
    assert report.stdout.count("Handle the empty case") == 1


def test_a_deferral_into_a_spec_read_from_a_shared_layer_is_refused(seam):
    spec_id = make_spec(seam)
    before_body = show(seam, spec_id)["body"]

    def after(argv, stdin, done):
        if argv[:2] == ["record", "show"] and "--json" in argv:
            doc = json.loads(done.stdout)
            doc["layer"] = "shared"
            return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")
        return done

    lore = RealLore(seam, after=after)
    code, out, err = go(seam, deferral(), lore)
    assert code == 3
    assert reason_code_of(out) == "shared-layer-prior"
    assert show(seam, spec_id)["body"] == before_body
    assert lore.calls_with("record", "update") == []
    assert len(task_candidates(seam)) == 1


def test_a_write_that_lands_but_does_not_read_back_as_the_new_entry_fails(seam):
    spec_id = make_spec(seam)
    state = {"updated": False}

    def after(argv, stdin, done):
        if argv[:2] == ["record", "update"]:
            state["updated"] = True
        if argv[:2] == ["record", "show"] and state["updated"]:
            doc = json.loads(done.stdout)
            doc["body"] = SPEC_BODY
            return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")
        return done

    code, out, err = go(seam, deferral(), RealLore(seam, after=after))
    assert code == 3
    assert reason_code_of(out) == "readback-failed"
    assert followups.parse_followups(show(seam, spec_id)["body"])[0].id == "F1"
    assert len(task_candidates(seam)) == 1


def test_a_gate_refusal_is_relayed_by_its_reason_code_and_writes_nothing(seam):
    spec_id = make_spec(seam)
    before_body = show(seam, spec_id)["body"]
    lore = RealLore(seam)
    code, out, err = go(seam, deferral(finding={"where": []}), lore)
    assert code == 3
    assert reason_code_of(out) == "malformed-entry"
    assert show(seam, spec_id)["body"] == before_body
    assert lore.calls_with("record", "update") == []
    assert len(task_candidates(seam)) == 1


def test_an_unreadable_spec_fails_the_deferral_without_a_write(seam):
    lore = RealLore(seam)
    code, out, err = go(seam, deferral(), lore)
    assert code == 3
    assert reason_code_of(out) == "spec-unreadable"
    assert "record: spec/fixture-spec |" in out
    assert lore.calls_with("record", "update") == []
    assert len(task_candidates(seam)) == 1


def test_a_lore_update_failure_is_a_fixed_reason_code_never_lores_free_text(seam):
    make_spec(seam)

    def before(argv, stdin):
        if argv[:2] == ["record", "update"]:
            return _failed(argv, 1, "error: disk exploded, token AKIAABCDEFGHIJKLMNOP leaked")
        return None

    code, out, err = go(seam, deferral(), RealLore(seam, before=before))
    assert code == 3
    assert reason_code_of(out) == "write-failed"
    assert "exploded" not in seam.session_text() and SECRET not in seam.session_text()


def test_separate_creates_an_open_task_with_no_spec_relation_and_the_finding_where(seam):
    code, out, err = go(seam, task_decision(finding={"where": ["src/a.py", "src/b.py"]}))
    assert code == 0, err
    task_id = out.strip().split("record: ")[1].split(" |")[0]
    assert re.fullmatch(r"task/[a-z0-9-]+", task_id)
    doc = show(seam, task_id)
    assert doc["sidecar"]["status"] == "open"
    assert doc["sidecar"]["title"] == "Handle the empty case"
    assert "labels" not in doc["sidecar"] or "route" not in doc["sidecar"]["labels"]
    assert not any(k.startswith("related") for k in doc["sidecar"])
    assert "spec/fixture-spec" not in json.dumps(doc["sidecar"])
    assert "src/a.py" in doc["body"] and "src/b.py" in doc["body"]
    assert "what: w\n" in doc["body"] and "found: f\n" in doc["body"]
    assert "task/X" in doc["body"]
    assert "needs a brainstorm" not in doc["body"]


def test_brainstorm_creates_a_task_labelled_for_brainstorm_that_report_prints_the_command_for(seam):
    code, out, err = go(seam, task_decision("brainstorm"))
    assert code == 0, err
    task_id = out.strip().split("record: ")[1].split(" |")[0]
    doc = show(seam, task_id)
    assert doc["sidecar"]["labels"]["route"] == "brainstorm"
    assert doc["sidecar"]["status"] == "open"
    assert "needs a brainstorm before it can be built" in doc["body"]
    assert not any(k.startswith("related") for k in doc["sidecar"])
    r = seam.report("task/X")
    assert r.returncode == 0, r.stderr
    assert f"Run `/craft:brainstorm {task_id}`." in r.stdout


@pytest.mark.parametrize("field", ["title", "what", "found"])
def test_a_credential_in_any_field_is_redacted_in_the_written_entry(seam, field):
    spec_id = make_spec(seam)
    code, out, err = go(seam, deferral(finding={field: f"leak {SECRET}"}))
    assert code == 0, err
    assert SECRET not in show(seam, spec_id)["body"]
    assert SECRET not in seam.session_text()
    assert "leak [redacted]" in show(seam, spec_id)["body"]


@pytest.mark.parametrize("kind", ["separate", "brainstorm"])
@pytest.mark.parametrize("field", ["title", "what", "found"])
def test_a_credential_in_any_field_is_redacted_in_the_created_task(seam, kind, field):
    code, out, err = go(seam, task_decision(kind, finding={field: f"leak {SECRET}"}))
    assert code == 0, err
    task_id = out.strip().split("record: ")[1].split(" |")[0]
    assert SECRET not in json.dumps(show(seam, task_id))
    assert SECRET not in seam.session_text()
    assert SECRET not in "".join(seam.records().keys())


def test_a_credential_in_the_reason_is_redacted_in_the_created_task(seam):
    code, out, err = go(seam, task_decision(reason=f"because {SECRET}"))
    assert code == 0, err
    task_id = out.strip().split("record: ")[1].split(" |")[0]
    assert SECRET not in json.dumps(show(seam, task_id))
    assert "because [redacted]" in show(seam, task_id)["body"]


@pytest.mark.parametrize("field", ["title", "what", "found", "reason"])
def test_a_credential_in_any_field_is_redacted_in_the_fallback_candidate(seam, field):
    make_spec(seam)

    def before(argv, stdin):
        if argv[:2] == ["record", "update"]:
            return _failed(argv)
        return None

    doc = deferral(reason=f"because {SECRET}") if field == "reason" else deferral(finding={field: f"leak {SECRET}"})
    code, out, err = go(seam, doc, RealLore(seam, before=before))
    assert code == 3
    cands = task_candidates(seam)
    assert len(cands) == 1 and SECRET not in cands[0]
    assert SECRET not in seam.session_text()


@pytest.mark.parametrize("make_doc", [deferral, task_decision, lambda **kw: task_decision("brainstorm", **kw)])
def test_when_the_decision_capture_fails_nothing_is_written(seam, make_doc):
    spec_id = make_spec(seam)
    before_body = show(seam, spec_id)["body"]
    tasks_before = all_tasks(seam)

    def before(argv, stdin):
        if argv[:2] == ["session", "candidate"] and "found-work" in argv:
            return _failed(argv)
        return None

    lore = RealLore(seam, before=before)
    code, out, err = go(seam, make_doc(), lore)
    assert code == 2
    assert "reason-code: capture-failed" in err
    assert lore.calls_with("record", "update") == [] and lore.calls_with("record", "create") == []
    assert show(seam, spec_id)["body"] == before_body
    assert all_tasks(seam) == tasks_before


@pytest.mark.parametrize("make_doc", [deferral, task_decision, lambda **kw: task_decision("brainstorm", **kw)])
def test_a_failed_write_exits_3_with_a_failed_result_and_a_task_candidate(seam, make_doc):
    make_spec(seam)

    def before(argv, stdin):
        if argv[:2] in (["record", "update"], ["record", "create"]):
            return _failed(argv, 1, "error: free text with | pipes and\nnewlines")
        return None

    code, out, err = go(seam, make_doc(), RealLore(seam, before=before))
    assert code == 3
    assert out.strip().startswith("found-work-result: failed | parent: task/X")
    assert reason_code_of(out) == "write-failed"
    assert "free text" not in seam.session_text()
    cands = task_candidates(seam)
    assert len(cands) == 1 and "Handle the empty case" in cands[0] and "src/a.py" in cands[0]


def test_a_create_that_exits_non_zero_is_a_failed_write_whatever_it_printed(seam):
    def before(argv, stdin):
        if argv[:2] == ["record", "create"]:
            return subprocess.CompletedProcess(argv, 1, "task/looks-fine\n", "error: nope")
        return None

    code, out, err = go(seam, task_decision(), RealLore(seam, before=before))
    assert code == 3
    assert reason_code_of(out) == "write-failed"


def test_a_create_that_prints_two_ids_is_a_failed_write(seam):
    def before(argv, stdin):
        if argv[:2] == ["record", "create"]:
            return subprocess.CompletedProcess(argv, 0, "task/a\ntask/b\n", "")
        return None

    code, out, err = go(seam, task_decision(), RealLore(seam, before=before))
    assert code == 3
    assert reason_code_of(out) == "write-failed"


def test_a_task_lookup_failure_fails_the_write_instead_of_risking_a_second_task(seam):
    def before(argv, stdin):
        if argv[:1] == ["search"]:
            return _failed(argv)
        return None

    lore = RealLore(seam, before=before)
    code, out, err = go(seam, task_decision(), lore)
    assert code == 3
    assert reason_code_of(out) == "lookup-failed"
    assert lore.calls_with("record", "create") == []


def test_create_output_that_is_not_one_task_id_is_a_failed_write(seam):
    def after(argv, stdin, done):
        if argv[:2] == ["record", "create"]:
            return subprocess.CompletedProcess(argv, 0, "task/ok; rm -rf\n", "")
        return done

    code, out, err = go(seam, task_decision(), RealLore(seam, after=after))
    assert code == 3
    assert reason_code_of(out) == "write-failed"


def test_no_finding_text_reaches_a_command_line_but_the_neutralized_title_as_one_element(seam):
    make_spec(seam)
    finding = {"title": "Odd -title; $(x) `y`", "what": "WHATMARK", "found": "FOUNDMARK", "where": ["src/WHEREMARK.py"]}
    docs = (
        deferral(finding=finding),
        task_decision(finding=finding, parent="task/P2"),
        task_decision("brainstorm", finding=finding, parent="task/P3"),
    )
    for doc in docs:
        lore = RealLore(seam)
        code, out, err = go(seam, doc, lore)
        assert code == 0, err
        assert lore.calls_with("record", "update") or lore.calls_with("record", "create")
        for argv, _ in lore.calls:
            joined = " ".join(argv)
            assert not any(m in joined for m in ("WHATMARK", "FOUNDMARK", "WHEREMARK", "it would balloon"))
            if "Odd" in joined:
                assert argv[:2] == ["record", "create"]
                assert [a for a in argv if "Odd" in a] == ["--title=Odd -title; $(x) `y`"]


def test_retrying_a_deferral_after_a_written_result_adds_no_second_entry(seam):
    spec_id = make_spec(seam)
    first = go(seam, deferral())
    second = go(seam, deferral())
    assert second[0] == 0 and second[1] == first[1]
    assert [e.id for e in followups.parse_followups(show(seam, spec_id)["body"])] == ["F1"]
    assert len(found_work_lines(seam.session_text())) == 2


@pytest.mark.parametrize("kind", ["separate", "brainstorm"])
def test_retrying_a_task_decision_after_a_written_result_adds_no_second_task(seam, kind):
    first = go(seam, task_decision(kind))
    second = go(seam, task_decision(kind))
    assert second[0] == 0 and second[1] == first[1]
    assert len(all_tasks(seam)) == 1
    assert len(found_work_lines(seam.session_text())) == 2


def _lose_result_capture(argv, stdin):
    if argv[:2] == ["session", "candidate"] and "found-work-result" in argv:
        return _failed(argv)
    return None


def test_retrying_a_deferral_whose_result_capture_was_lost_returns_the_same_entry(seam):
    spec_id = make_spec(seam)
    code, out, _ = go(seam, deferral(), RealLore(seam, before=_lose_result_capture))
    assert code == 2 and "#F1" in out
    code, out, err = go(seam, deferral())
    assert code == 0, err
    assert out.strip().endswith("record: spec/fixture-spec#F1 | reason-code: none")
    assert [e.id for e in followups.parse_followups(show(seam, spec_id)["body"])] == ["F1"]


@pytest.mark.parametrize("kind", ["separate", "brainstorm"])
def test_retrying_a_task_decision_whose_result_capture_was_lost_returns_the_same_task(seam, kind):
    code, out, _ = go(seam, task_decision(kind), RealLore(seam, before=_lose_result_capture))
    assert code == 2
    first_id = out.strip().split("record: ")[1].split(" |")[0]
    code, out, err = go(seam, task_decision(kind))
    assert code == 0, err
    assert f"record: {first_id} |" in out
    assert all_tasks(seam) == [first_id]


@pytest.mark.parametrize(
    "other",
    [{"finding": {"title": "A different finding"}}, {"finding": {"where": ["src/other.py"]}}, {"parent": "task/Y"}],
    ids=["title", "where", "parent"],
)
def test_a_lost_result_retry_matches_parent_title_and_where_of_the_task(seam, other):
    go(seam, task_decision(), RealLore(seam, before=_lose_result_capture))
    code, out, err = go(seam, task_decision(**other))
    assert code == 0, err
    assert len(all_tasks(seam)) == 2


def test_a_task_that_only_mentions_the_key_in_prose_is_not_the_prior_task(seam):
    key = fw._found_work_key(
        fw.build_decision(json.dumps(task_decision()))
    )
    done = seam.lore(
        "record", "create", "--kind", "task", "--title=Unrelated", "--status", "open", stdin=f"we saw {key} once\n"
    )
    assert done.returncode == 0, done.stderr
    code, out, err = go(seam, task_decision())
    assert code == 0, err
    assert len(all_tasks(seam)) == 2
    assert "record: task/unrelated" not in out


def test_a_deferred_decision_needs_a_spec_record_id_and_captures_nothing(seam):
    lore = RealLore(seam)
    code, out, err = go(seam, deferral(spec="story/fixture"), lore)
    assert code == 1
    assert "reason-code: unsafe-id" in err
    assert lore.calls_with("session", "candidate") == []


@pytest.mark.parametrize(
    "other", [{"where": ["src/other.py"]}, {"title": "A different finding"}], ids=["where", "title"]
)
def test_a_deferral_retry_after_a_lost_result_matches_title_and_where(seam, other):
    spec_id = make_spec(seam)
    go(seam, deferral(), RealLore(seam, before=_lose_result_capture))
    code, out, err = go(seam, deferral(finding=other))
    assert code == 0, err
    assert [e.id for e in followups.parse_followups(show(seam, spec_id)["body"])] == ["F1", "F2"]


def test_a_dropped_entry_with_the_same_title_and_where_is_not_the_prior_deferral(seam):
    dropped = SECTION.replace("First thing", "Handle the empty case").replace("src/a.py", "src/a.py")
    dropped = dropped.replace("status: open", "status: dropped: not worth it", 1)
    spec_id = make_spec(seam, SPEC_BODY + dropped)
    code, out, err = go(seam, deferral())
    assert code == 0, err
    assert [(e.id, e.status_kind) for e in followups.parse_followups(show(seam, spec_id)["body"])] == [
        ("F1", "dropped"),
        ("F2", "dropped"),
        ("F3", "open"),
    ]


def _entries(seam, spec_id):
    return followups.parse_followups(show(seam, spec_id)["body"])


def _decision_lines(seam):
    return [ln for ln in found_work_lines(seam.session_text()) if ln.startswith("found-work: ")]


def test_a_deferral_with_path_line_writes_bare_paths_and_logs_the_line(seam):
    spec_id = make_spec(seam)
    code, out, err = go(seam, deferral(finding={"where": ["src/a.py:10", "src/b.py:3-7", "src/a.py:42"]}))
    assert code == 0, err
    assert [e.where for e in _entries(seam, spec_id)] == [("src/a.py", "src/b.py")]
    assert "where: src/a.py:10, src/b.py:3-7, src/a.py:42 |" in _decision_lines(seam)[0]


def test_a_security_deferral_names_path_line_and_carries_no_finding_text(seam):
    spec_id = make_spec(seam)
    doc = deferral(
        finding={
            "security": True,
            "where": ["export/csv.py:10", "export/io.py:7"],
            "what": "SQLI-DETAIL",
            "found": "EXPLOIT-PATH",
        }
    )
    code, out, err = go(seam, doc)
    assert code == 0, err
    (entry,) = _entries(seam, spec_id)
    assert entry.what == "security finding — see export/csv.py:10, export/io.py:7"
    assert entry.where == ("export/csv.py", "export/io.py")
    body = show(seam, spec_id)["body"] + seam.session_text()
    assert "SQLI-DETAIL" not in body and "EXPLOIT-PATH" not in body


@pytest.mark.parametrize("where", ["../x.py:3", "x.py:abc", "x.py:"], ids=["parent-dir", "non-numeric", "empty-line"])
def test_a_path_still_invalid_after_the_line_is_stripped_is_refused_before_any_capture(seam, where):
    spec_id = make_spec(seam)
    lore = RealLore(seam)
    code, out, err = go(seam, deferral(finding={"where": [where]}), lore)
    assert code == 1
    assert "reason-code: path-outside-repository" in err
    assert lore.calls_with("session", "candidate") == []
    assert _entries(seam, spec_id) == []


def test_retrying_a_path_line_deferral_adds_no_second_entry(seam):
    spec_id = make_spec(seam)
    doc = deferral(finding={"where": ["src/a.py:10"]})
    first = go(seam, doc)
    second = go(seam, doc)
    assert second[0] == 0 and second[1] == first[1]
    assert [e.id for e in _entries(seam, spec_id)] == ["F1"]


def test_retrying_a_path_line_deferral_after_a_lost_result_returns_the_same_entry(seam):
    spec_id = make_spec(seam)
    doc = deferral(finding={"where": ["src/a.py:10"]})
    code, out, _ = go(seam, doc, RealLore(seam, before=_lose_result_capture))
    assert code == 2 and "#F1" in out
    code, out, err = go(seam, doc)
    assert code == 0, err
    assert out.strip().endswith("record: spec/fixture-spec#F1 | reason-code: none")
    assert [e.id for e in _entries(seam, spec_id)] == ["F1"]


DEEP = "tools/craft/plugins/craft/scripts/followups.py"


def test_a_deep_path_reaches_the_decision_line_the_entry_and_the_task_intact(seam):
    spec_id = make_spec(seam)
    code, out, err = go(seam, deferral(finding={"where": [f"{DEEP}:10"]}))
    assert code == 0, err
    assert [e.where for e in _entries(seam, spec_id)] == [(DEEP,)]
    assert f"where: {DEEP}:10 |" in _decision_lines(seam)[0]
    code, out, err = go(seam, task_decision(finding={"where": [f"{DEEP}:10"]}))
    assert code == 0, err
    task_id = out.strip().split("record: ")[1].split(" |")[0]
    assert f"`{DEEP}:10`" in show(seam, task_id)["body"]


def test_two_security_findings_in_different_deep_files_make_two_tasks(seam):
    for path in (f"{DEEP}:10", "tools/craft/plugins/craft/scripts/found_work.py:20"):
        code, out, err = go(seam, task_decision(finding={"security": True, "where": [path]}))
        assert code == 0, err
    assert len(all_tasks(seam)) == 2


def test_two_security_findings_at_one_location_make_two_tasks_and_a_retry_makes_none(seam):
    def sec(title):
        return task_decision(finding={"security": True, "title": title, "where": ["export/csv.py:10"]})

    for title in ("first problem", "second problem", "first problem"):
        code, out, err = go(seam, sec(title))
        assert code == 0, err
    assert len(all_tasks(seam)) == 2
    assert "first problem" not in seam.session_text() + json.dumps(seam.records(), default=str)


def test_a_brainstorm_after_a_separate_decision_is_its_own_task(seam):
    code, first, err = go(seam, task_decision("separate"))
    assert code == 0, err
    code, second, err = go(seam, task_decision("brainstorm"))
    assert code == 0, err
    assert len(all_tasks(seam)) == 2
    assert first.split("record: ")[1].split(" |")[0] != second.split("record: ")[1].split(" |")[0]


def test_a_lost_result_retry_after_a_separate_decision_does_not_reuse_it_for_a_brainstorm(seam):
    go(seam, task_decision("separate"), RealLore(seam, before=_lose_result_capture))
    code, out, err = go(seam, task_decision("brainstorm"))
    assert code == 0, err
    assert len(all_tasks(seam)) == 2


# --- entry: fold then done through lore's --base check ---------------------------


def make_task(seam, title, body, status, parent=None):
    argv = ["record", "create", "--kind", "task", "--title", title, "--status", status]
    if parent:
        argv += ["--parent", parent.split("/", 1)[1]]
    done = seam.lore(*argv, stdin=body)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def transition_doc(kind, entries, parent, **over):
    return {"transition": kind, "parent": parent, "spec": "spec/fixture-spec", "entries": entries,
            "reason": "it touches the same code", **over}


def run_entry_cli(seam, doc, vault="default"):
    return seam.script("entry", "--session", "build", "--session-id", SID, "--vault", vault, stdin=json.dumps(doc))


def test_a_fold_then_a_done_land_on_a_ready_spec_through_lores_base_check_and_read_back(seam):
    spec_id = make_spec(seam, SPEC_BODY + SECTION.replace("status: dropped: not worth it", "status: open"))
    assert show(seam, spec_id)["sidecar"]["status"] == "ready"
    parent = make_task(seam, "Slice parent", "the slice\n", "in-progress")
    delivers = "# T\n\n**Delivers:** Resolves spec/fixture-spec#F1: First thing\n"
    make_task(seam, "Build the first thing", delivers, "done", parent)
    before = show(seam, spec_id)["body"]

    fold = run_entry_cli(seam, transition_doc("fold", ["F1", "F2"], parent))
    assert fold.returncode == 0, fold.stderr
    assert [ln.split(" | ")[0] for ln in fold.stdout.splitlines()] == ["found-work-result: written"] * 2
    folded = show(seam, spec_id)["body"]
    assert {e.id: (e.status_kind, e.status_detail) for e in followups.parse_followups(folded)} == {
        "F1": ("folded", parent), "F2": ("folded", parent)}
    assert followups.excise_section(folded) == followups.excise_section(before)

    done = run_entry_cli(seam, transition_doc("done", ["F1"], parent, reason=""))
    assert done.returncode == 0, done.stderr
    assert {e.id: (e.status_kind, e.status_detail) for e in followups.parse_followups(show(seam, spec_id)["body"])} == {
        "F1": ("done", parent), "F2": ("folded", parent)}

    unbuilt = run_entry_cli(seam, transition_doc("done", ["F2"], parent))
    assert unbuilt.returncode == 1
    assert "reason-code: entry-not-built" in unbuilt.stderr

    log = found_work_lines(seam.session_text())
    assert [ln.split(" | ")[0] for ln in log] == (
        ["found-work: entry-folded"] * 2 + ["found-work-result: written"] * 2
        + ["found-work: entry-done", "found-work-result: written"]
    )
    report = seam.report(parent)
    assert report.stdout.splitlines() == [
        "Folded spec/fixture-spec#F1 into this slice: First thing, because it touches the same code.",
        "Folded spec/fixture-spec#F2 into this slice: Second thing, because it touches the same code.",
        "Marked spec/fixture-spec#F1 done by this slice: First thing.",
    ]


# --- reopen-abandoned against the real lore ---------------------------------------


def _folded_section(*pairs):
    entries = "".join(
        f"- **F{n}.** Thing {n}\n  - status: folded into {task}\n  - what: w\n  - where: `src/a.py`\n  - found: f\n"
        for n, task in pairs
    )
    return "\n## Follow-ups\n\n" + entries


def test_reopen_abandoned_reopens_the_entry_of_a_dropped_slice_and_leaves_the_running_one(seam):
    dropped = make_task(seam, "Dropped slice", "the slice\n", "dropped")
    running = make_task(seam, "Running slice", "the slice\n", "in-progress")
    spec_id = make_spec(seam, SPEC_BODY + _folded_section((1, dropped), (2, running)))
    before = show(seam, spec_id)["body"]

    done = seam.script("reopen-abandoned", "--spec", spec_id, "--session", "build", "--session-id", SID, "--vault", "default")
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [
        f"found-work-result: written | parent: {dropped} | finding: Thing 1 | record: {spec_id}#F1"
        " | reason-code: none",
        f"reopened: {spec_id}#F1 ({dropped})",
    ]
    after = show(seam, spec_id)["body"]
    assert {e.id: (e.status_kind, e.status_detail) for e in followups.parse_followups(after)} == {
        "F1": ("open", None), "F2": ("folded", running)}
    assert followups.excise_section(after) == followups.excise_section(before)
    assert found_work_lines(seam.session_text())[0] == (
        f"found-work: entry-reopened | parent: {dropped} | record: {spec_id}#F1 | finding: Thing 1"
        f" | where: src/a.py | why: its slice {dropped} ended dropped without building it"
    )

    again = seam.script("reopen-abandoned", "--spec", spec_id, "--session", "build", "--session-id", SID, "--vault", "default")
    assert (again.returncode, again.stdout, again.stderr) == (0, "", "")


def test_reopen_abandoned_marks_the_entry_a_done_child_built_done_and_reopens_the_one_nothing_built(seam):
    parent = make_task(seam, "Finished slice", "the slice\n", "done")
    delivers = "# T\n\n**Delivers:** Resolves spec/fixture-spec#F1: Thing 1\n"
    make_task(seam, "Build thing one", delivers, "done", parent)
    spec_id = make_spec(seam, SPEC_BODY + _folded_section((1, parent), (2, parent)))

    done = seam.script("reopen-abandoned", "--spec", spec_id, "--session", "build", "--session-id", SID, "--vault", "default")
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-2:] == [
        f"marked done: {spec_id}#F1 ({parent})", f"reopened: {spec_id}#F2 ({parent})"]
    after = show(seam, spec_id)["body"]
    assert {e.id: (e.status_kind, e.status_detail) for e in followups.parse_followups(after)} == {
        "F1": ("done", parent), "F2": ("open", None)}
    assert [ln.split(" | ")[0] for ln in found_work_lines(seam.session_text())] == (
        ["found-work: entry-done", "found-work: entry-reopened"] + ["found-work-result: written"] * 2
    )


def test_reopen_abandoned_with_an_unknown_task_exits_2_lookup_failed(seam):
    spec_id = make_spec(seam, SPEC_BODY + _folded_section((1, "task/no-such-task")))
    done = seam.script("reopen-abandoned", "--spec", spec_id, "--session", "build", "--session-id", SID, "--vault", "default")
    assert done.returncode == 2
    assert "reason-code: lookup-failed" in done.stderr
    assert {e.id: e.status_kind for e in followups.parse_followups(show(seam, spec_id)["body"])} == {"F1": "folded"}


def _statuses(seam, spec_id):
    entries = followups.parse_followups(show(seam, spec_id)["body"])
    return {e.id: (e.status_kind, e.status_detail, e.found) for e in entries}


def test_a_drop_naming_a_real_task_then_a_restore_land_through_lores_base_check_and_the_gate(seam):
    spec_id = make_spec(seam, SPEC_BODY + SECTION)
    parent = make_task(seam, "Slice parent", "the slice\n", "in-progress")
    elsewhere = make_task(seam, "Work done elsewhere", "the work\n", "open")

    ghost = run_entry_cli(seam, transition_doc("drop", ["F1"], parent, reason="moved to task/no-such-task"))
    assert ghost.returncode == 1
    assert "reason-code: drop-target-missing" in ghost.stderr
    assert _statuses(seam, spec_id)["F1"][0] == "open"

    reason = f"moved to {elsewhere}"
    drop = run_entry_cli(seam, transition_doc("drop", ["F1"], parent, reason=reason))
    assert drop.returncode == 0, drop.stderr
    assert drop.stdout.splitlines() == [
        "found-work-result: written | parent: " + parent
        + " | finding: First thing | record: spec/fixture-spec#F1 | reason-code: none"
    ]
    assert _statuses(seam, spec_id)["F1"][:2] == ("dropped", reason)

    restore = run_entry_cli(seam, transition_doc("restore", ["F1"], parent, reason="needed after all"))
    assert restore.returncode == 0, restore.stderr
    after = _statuses(seam, spec_id)
    assert after["F1"][:2] == ("dropped", reason)
    assert after["F3"] == ("open", None, "restored from F1")

    assert seam.report(parent).stdout.splitlines() == [
        f"Dropped spec/fixture-spec#F1: First thing, because {reason}.",
        "Restored dropped work as spec/fixture-spec#F3: First thing, because needed after all.",
    ]
    empty = seam.script("report", "--parent", "task/nobody", "--session", "slice", "--session-id", SID)
    assert empty.stdout.strip() == "The slice made no decisions about found work."
