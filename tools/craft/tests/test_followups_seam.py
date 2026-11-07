"""Seam: the write chain a `## Follow-ups` writer runs, driven through the real
`lore record` CLI against a temp vault and the real `followups_gate.py`:

    lore record show --json  ->  followups_gate.py --prior  ->  lore record update --base

Lore's CLI runs in-process under the isolation fixtures of lore's own test
conftest, loaded by file path under a unique module name (a bare
`import conftest` would collide with craft's).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_LORE_TESTS = Path(__file__).resolve().parents[2] / "lore" / "tests"
_SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts"

_conftest_spec = importlib.util.spec_from_file_location(
    "lore_tests_conftest_for_followups_seam", _LORE_TESTS / "conftest.py"
)
_lore_conftest = importlib.util.module_from_spec(_conftest_spec)
sys.modules["lore_tests_conftest_for_followups_seam"] = _lore_conftest
_conftest_spec.loader.exec_module(_lore_conftest)

from lore_tests_conftest_for_followups_seam import (  # noqa: E402,F401
    _allow_real_publish_spawn,
    _isolate_ambient_env,
    _publish_spawn_guard,
    make_vault,
    run_cli,
)

BODY = """# Seam spec

Authored prose.

## Slices

- **First** — a value claim. (`task/first`, closed 2026-01-01, covers AC1)
"""

ENTRY = """- **F1.** Seam entry
  - status: open
  - what: something is wrong
  - where: `a/b.py`
  - found: during review
"""

WITH_SECTION = BODY + "\n## Follow-ups\n\n" + ENTRY
SLICES_APPENDED = BODY + "- **Second** — more. (`task/second`, closed 2026-01-02, covers AC1)\n"


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
        return subprocess.run(
            [sys.executable, str(_SCRIPTS / "followups_gate.py"), "--prior", str(prior_path)],
            input=proposed,
            capture_output=True,
            text=True,
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


def test_certified_first_write_lands_through_lore_and_reads_back(lore):
    rid = lore.create_spec()
    prior = lore.show(rid)

    base = _printed_base(lore.certify(prior, WITH_SECTION))
    assert base == prior["revision"]
    lore.ok("record", "update", rid, "--base", base, stdin_text=WITH_SECTION)

    shown = lore.show(rid)
    assert "## Follow-ups" in shown["body"]
    assert "- **F1.** Seam entry" in shown["body"]
    assert shown["revision"] != prior["revision"]


def test_crlf_stored_body_certifies_and_writes_with_the_gates_base(lore):
    rid = lore.create_spec()
    kind, name = rid.split("/", 1)
    raw = BODY.replace("\n", "\r\n").encode("utf-8")
    (lore.vault / kind / f"{name}.md").write_bytes(raw)
    prior = lore.show(rid)
    assert "\r" not in prior["body"]
    assert prior["revision"] == hashlib.sha256(raw).hexdigest()

    base = _printed_base(lore.certify(prior, WITH_SECTION))
    assert base == hashlib.sha256(raw).hexdigest()
    lore.ok("record", "update", rid, "--base", base, stdin_text=WITH_SECTION)

    assert "- **F1.** Seam entry" in lore.show(rid)["body"]


def test_write_after_another_write_changed_the_spec_is_refused_and_leaves_the_body(lore):
    rid = lore.create_spec()
    prior = lore.show(rid)
    base = _printed_base(lore.certify(prior, WITH_SECTION))

    lore.ok("record", "update", rid, stdin_text=SLICES_APPENDED)
    after_other = lore.stored_bytes(rid)

    r = lore.run("record", "update", rid, "--base", base, stdin_text=WITH_SECTION)
    assert r.returncode != 0
    assert "changed after you read it" in r.stderr
    assert lore.stored_bytes(rid) == after_other


def test_follow_ups_write_without_base_is_refused_and_leaves_the_body(lore):
    rid = lore.create_spec()
    before = lore.stored_bytes(rid)

    r = lore.run("record", "update", rid, stdin_text=WITH_SECTION)
    assert r.returncode != 0
    assert "--base is required" in r.stderr
    assert lore.stored_bytes(rid) == before


ENTRY_2 = ENTRY.replace("F1", "F2").replace("Seam entry", "Second entry")
TRAILING_NOTES = "\n## Notes\n\nOriginal notes.\n"


def test_proposed_body_that_also_edits_prose_before_the_section_is_refused_by_gate_and_by_lore(lore):
    rid = lore.create_spec()
    prior = lore.show(rid)
    proposed = WITH_SECTION.replace("Authored prose.", "Rewritten prose.")

    gate = lore.certify(prior, proposed)
    assert gate.returncode == 1
    assert "reason-code: change-outside-followups" in gate.stderr

    before = lore.stored_bytes(rid)
    r = lore.run("record", "update", rid, "--base", prior["revision"], stdin_text=proposed)
    assert r.returncode != 0
    assert lore.stored_bytes(rid) == before


def test_proposed_body_that_also_edits_prose_after_the_section_is_refused_by_gate_and_by_lore(lore):
    existing = WITH_SECTION + TRAILING_NOTES
    rid = lore.create_spec(body=existing)
    prior = lore.show(rid)
    proposed = existing.replace("Original notes.", "Rewritten notes.").replace(
        ENTRY, ENTRY + ENTRY_2
    )

    gate = lore.certify(prior, proposed)
    assert gate.returncode == 1
    assert "reason-code: change-outside-followups" in gate.stderr

    before = lore.stored_bytes(rid)
    r = lore.run("record", "update", rid, "--base", prior["revision"], stdin_text=proposed)
    assert r.returncode != 0
    assert lore.stored_bytes(rid) == before

    appended_only = existing.replace(ENTRY, ENTRY + ENTRY_2)
    base = _printed_base(lore.certify(prior, appended_only))
    lore.ok("record", "update", rid, "--base", base, stdin_text=appended_only)
    assert "- **F2.** Second entry" in lore.show(rid)["body"]


def test_slices_append_composed_before_an_entry_landed_is_refused_and_keeps_the_entry(lore):
    rid = lore.create_spec()
    prior = lore.show(rid)
    stale_read = prior["body"]
    base = _printed_base(lore.certify(prior, WITH_SECTION))
    lore.ok("record", "update", rid, "--base", base, stdin_text=WITH_SECTION)
    with_entry = lore.stored_bytes(rid)

    composed_from_stale = stale_read + "- **Second** — more. (`task/second`, closed 2026-01-02, covers AC1)\n"
    r = lore.run("record", "update", rid, stdin_text=composed_from_stale)
    assert r.returncode != 0
    assert lore.stored_bytes(rid) == with_entry
    assert "- **F1.** Seam entry" in lore.show(rid)["body"]


def test_slices_only_change_with_the_section_untouched_needs_no_base(lore):
    rid = lore.create_spec(body=WITH_SECTION)

    lore.ok("record", "update", rid, stdin_text=WITH_SECTION.replace(
        "covers AC1)\n", "covers AC1)\n- **Second** — more. (`task/second`, closed 2026-01-02, covers AC1)\n", 1
    ))

    shown = lore.show(rid)["body"]
    assert "**Second**" in shown
    assert "- **F1.** Seam entry" in shown
