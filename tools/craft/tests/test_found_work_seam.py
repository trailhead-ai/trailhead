"""Seam: found_work.py run as a subprocess, exactly as a skill invokes it, against
the real `lore` CLI over a throwaway vault and XDG tree. The child environment is
built from scratch so it cannot reach the live install."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
        return self.script("decide", "--session-id", SID, *args, stdin=json.dumps(doc), cwd=cwd)

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
    r = seam.script("decide", stdin=json.dumps(decision()), cwd="/")
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
