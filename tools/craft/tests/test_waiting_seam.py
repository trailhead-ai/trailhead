"""Seam: the write chain a `## Waiting` writer runs, driven through the real
`lore record` CLI against a temp vault and the real `waiting_gate.py`:

    lore record show --json  ->  waiting_gate.py --prior  ->  lore record update --base

Lore's CLI runs in-process under the isolation fixtures of lore's own test
conftest, loaded by file path under a unique module name (a bare
`import conftest` would collide with craft's).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_LORE_TESTS = Path(__file__).resolve().parents[2] / "lore" / "tests"
_SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts"

_conftest_spec = importlib.util.spec_from_file_location(
    "lore_tests_conftest_for_waiting_seam", _LORE_TESTS / "conftest.py"
)
_lore_conftest = importlib.util.module_from_spec(_conftest_spec)
sys.modules["lore_tests_conftest_for_waiting_seam"] = _lore_conftest
_conftest_spec.loader.exec_module(_lore_conftest)

from lore_tests_conftest_for_waiting_seam import (  # noqa: E402,F401
    _allow_real_publish_spawn,
    _isolate_ambient_env,
    _publish_spawn_guard,
    make_vault,
    run_cli,
)

sys.path.insert(0, str(_SCRIPTS))
import waiting  # noqa: E402

TODAY = "2026-11-05"

BODY = """# Seam spec

Authored prose.

## Problem

The original problem statement.

## Slices

- **First** — a value claim. (`task/first`, closed 2026-01-01, covers AC1)
"""

ENTRY_7 = """- **AC7.** pending check — task/first
  - check: open the page
  - how: click through
  - changed behaviour: yes
  - 2026-11-02 waiting: needs a human eye
"""

ENTRY_8 = """- **AC8.** pending check — task/first
  - check: read the report
  - how: open the dashboard
  - changed behaviour: no
  - 2026-11-03 waiting: needs a human eye
"""

WITH_ONE = BODY + "\n## Waiting\n\n" + ENTRY_7
WITH_TWO = BODY + "\n## Waiting\n\n" + ENTRY_7 + "\n" + ENTRY_8
SLICE_ROW = "- **Second** — more. (`task/second`, closed 2026-01-02, covers AC1)\n"


class Lore:
    def __init__(self, vault, state, tmp_path):
        self.vault = vault
        self.state = state
        self.tmp_path = tmp_path

    def run(self, *args, stdin_text=None):
        return run_cli(list(args), vault=self.vault, state_dir=self.state, stdin_text=stdin_text)

    def ok(self, *args, stdin_text=None):
        r = self.run(*args, stdin_text=stdin_text)
        assert r.returncode == 0, r.stderr
        return r.stdout

    def create_spec(self, body=BODY, status="ready"):
        return self.ok(
            "record", "create", "--kind", "spec", "--title", "Seam", "--keyword", "seam",
            "--status", status, stdin_text=body,
        ).strip()

    def show(self, record_id) -> dict:
        return json.loads(self.ok("record", "show", record_id, "--json"))

    def stored_bytes(self, record_id) -> bytes:
        kind, name = record_id.split("/", 1)
        return (self.vault / kind / f"{name}.md").read_bytes()

    def certify(self, prior: dict, proposed: str, name="prior.json") -> subprocess.CompletedProcess:
        prior_path = self.tmp_path / name
        prior_path.write_text(json.dumps(prior), encoding="utf-8")
        env = dict(os.environ, CRAFT_WAITING_GATE_TODAY=TODAY)
        return subprocess.run(
            [sys.executable, str(_SCRIPTS / "waiting_gate.py"), "--prior", str(prior_path)],
            input=proposed,
            capture_output=True,
            text=True,
            env=env,
        )


@pytest.fixture
def lore(monkeypatch, tmp_path):
    monkeypatch.delenv("LORE_VAULT_GUARD_ROOT", raising=False)
    monkeypatch.delenv("LORE_VAULT_GUARD_EXEMPT", raising=False)
    monkeypatch.setenv("LORE_GROUPS_DIR", str(tmp_path / "groups"))
    vault, state = make_vault(tmp_path)
    return Lore(vault, state, tmp_path)


def _printed_base(gate: subprocess.CompletedProcess) -> str:
    assert gate.returncode == 0, gate.stderr
    lines = gate.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("base: ")
    return lines[0][len("base: "):]


def _keys(body: str) -> list[tuple[str, str | None]]:
    return [e.key for e in waiting.parse_waiting(body)]


def test_certified_first_write_lands_through_lore_and_parse_waiting_reads_it_back(lore):
    rid = lore.create_spec()
    prior = lore.show(rid)

    base = _printed_base(lore.certify(prior, WITH_TWO))
    assert base == prior["revision"]
    lore.ok("record", "update", rid, "--base", base, stdin_text=WITH_TWO)

    shown = lore.show(rid)
    assert shown["revision"] != prior["revision"]
    entries = waiting.parse_waiting(shown["body"])
    assert [(e.criterion_id, e.building_slice) for e in entries] == [("AC7", "first"), ("AC8", "first")]
    assert entries[0].check == "open the page"
    assert entries[0].changed_behaviour is True
    assert entries[1].check == "read the report"
    assert entries[1].changed_behaviour is False


def test_write_certified_against_a_read_another_write_changed_is_refused_and_leaves_the_body(lore):
    rid = lore.create_spec()
    prior = lore.show(rid)
    base = _printed_base(lore.certify(prior, WITH_ONE))

    lore.ok("record", "update", rid, stdin_text=BODY + SLICE_ROW)
    after_other = lore.stored_bytes(rid)

    r = lore.run("record", "update", rid, "--base", base, stdin_text=WITH_ONE)
    assert r.returncode != 0
    assert "changed after you read it" in r.stderr
    assert lore.stored_bytes(rid) == after_other


def _land_one(lore):
    rid = lore.create_spec()
    prior = lore.show(rid)
    base = _printed_base(lore.certify(prior, WITH_ONE))
    lore.ok("record", "update", rid, "--base", base, stdin_text=WITH_ONE)
    return rid


def test_body_that_drops_an_entry_is_refused_by_the_gate_and_the_spec_is_unchanged(lore):
    rid = lore.create_spec()
    p1 = lore.show(rid)
    lore.ok("record", "update", rid, "--base", _printed_base(lore.certify(p1, WITH_TWO)),
            stdin_text=WITH_TWO)
    before = lore.stored_bytes(rid)

    gate = lore.certify(lore.show(rid), WITH_ONE)
    assert gate.returncode == 1
    assert "reason-code: removed-entry" in gate.stderr
    assert gate.stdout == ""
    assert lore.stored_bytes(rid) == before
    assert _keys(lore.show(rid)["body"]) == [("AC7", "first"), ("AC8", "first")]


def test_body_that_appends_passed_to_an_entry_is_refused_by_the_gate_and_the_spec_is_unchanged(lore):
    rid = _land_one(lore)
    before = lore.stored_bytes(rid)
    proposed = WITH_ONE.rstrip("\n") + "\n  - 2026-11-04 passed: looked fine\n"

    gate = lore.certify(lore.show(rid), proposed)
    assert gate.returncode == 1
    assert "reason-code: status-transition" in gate.stderr
    assert lore.stored_bytes(rid) == before
    assert [h[1] for h in waiting.parse_waiting(lore.show(rid)["body"])[0].history] == ["waiting"]


def test_body_that_changes_waiting_and_edits_problem_is_refused_by_gate_and_by_lore(lore):
    rid = lore.create_spec()
    prior = lore.show(rid)
    proposed = WITH_ONE.replace("The original problem statement.", "A rewritten problem.")

    gate = lore.certify(prior, proposed)
    assert gate.returncode == 1
    assert "reason-code: change-outside-waiting" in gate.stderr

    before = lore.stored_bytes(rid)
    r = lore.run("record", "update", rid, "--base", prior["revision"], stdin_text=proposed)
    assert r.returncode != 0
    assert lore.stored_bytes(rid) == before


def test_body_edited_after_certification_is_accepted_by_lore_because_base_certifies_the_prior(lore):
    rid = lore.create_spec()
    prior = lore.show(rid)
    base = _printed_base(lore.certify(prior, WITH_ONE))
    edited = WITH_ONE.replace("needs a human eye", "edited after the gate certified it")

    lore.ok("record", "update", rid, "--base", base, stdin_text=edited)

    entries = waiting.parse_waiting(lore.show(rid)["body"])
    assert entries[0].history == (("2026-11-02", "waiting", "edited after the gate certified it"),)


def test_slices_append_composed_before_an_entry_landed_is_refused_and_keeps_the_entry(lore):
    rid = lore.create_spec()
    prior = lore.show(rid)
    stale_read = prior["body"]
    base = _printed_base(lore.certify(prior, WITH_ONE))
    lore.ok("record", "update", rid, "--base", base, stdin_text=WITH_ONE)
    with_entry = lore.stored_bytes(rid)

    r = lore.run("record", "update", rid, stdin_text=stale_read.rstrip("\n") + "\n" + SLICE_ROW)
    assert r.returncode != 0
    assert lore.stored_bytes(rid) == with_entry
    assert _keys(lore.show(rid)["body"]) == [("AC7", "first")]


def test_waiting_write_without_base_is_refused_naming_the_section_and_leaves_the_body(lore):
    rid = lore.create_spec()
    before = lore.stored_bytes(rid)

    r = lore.run("record", "update", rid, stdin_text=WITH_ONE)
    assert r.returncode != 0
    assert "## Waiting" in r.stderr
    assert lore.stored_bytes(rid) == before
