"""Seam: a slice closes while its manual check waits, through the real chain.

    observation_gate (stall) -> pending_check.py -> observation_gate (accepts)
    -> parent done -> open-slice guard query (lore search) -> ledger line
    -> ledger_gate --parent-coverage -> candidate_set

Every `lore` call is a subprocess of lore's real CLI pointed at a temp vault (lore's
conftest is loaded by path under a unique name); craft's scripts run as subprocesses.
The ledger line is built in the test from the done parent's fields, exactly as
`/craft:slice` step 4 prescribes: that reconcile exists only as skill prose.
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
    "lore_tests_conftest_for_pending_check_seam", _LORE_TESTS / "conftest.py"
)
_lore_conftest = importlib.util.module_from_spec(_conftest_spec)
sys.modules["lore_tests_conftest_for_pending_check_seam"] = _lore_conftest
_conftest_spec.loader.exec_module(_lore_conftest)

from lore_tests_conftest_for_pending_check_seam import (  # noqa: E402,F401
    _allow_real_publish_spawn,
    _isolate_ambient_env,
    _publish_spawn_guard,
    make_vault,
)

sys.path.insert(0, str(_SCRIPTS))
import waiting  # noqa: E402

TODAY = "2026-11-08"
SECRET = "sk_live_" + "a1b2c3d4e5f6"

SPEC_BODY = """# Seam spec

Prose.

## Acceptance Criteria

- **AC1.** The first thing works.
- **AC2.** The banner shows the count.
- **AC3.** The report opens in the app.

## Slices
"""

PARENT_BODY = """**Value claim:** you can close with a check waiting.

**Covers:** AC1, AC2

## Criterion observations

- **AC1** — automated-assertion — tests/test_first.py::test_first
- **AC2** — manual-check — looked at the banner in the app
"""

AUTOMATED_PARENT_BODY = """**Value claim:** the first thing works.

**Covers:** AC1

## Criterion observations

- **AC1** — automated-assertion — tests/test_first.py::test_first
"""

ENTRY = {"criterion": "AC2", "check": "the banner shows 3", "how": "open the app", "changed_behaviour": True}


class World:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        vault, _ = make_vault(tmp_path)
        self.vault = vault
        cfg = tmp_path / "xdg_config"
        _lore_conftest.write_vault_config(cfg, [("default", "default", vault)])
        for sub in ("state", "home", "bin"):
            (tmp_path / sub).mkdir(exist_ok=True)
        shim = tmp_path / "bin" / "lore"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_lore_conftest.CLI_PATH}" "$@"\n')
        shim.chmod(0o755)
        self.env = {
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path / "home"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CONFIG_HOME": str(cfg),
            "LORE_GROUPS_DIR": str(tmp_path / "groups"),
            "LORE_PUBLISH_DISABLE": "1",
            "LORE_EMAIL": "tester@example.com",
            "CRAFT_WAITING_GATE_TODAY": TODAY,
        }

    def lore(self, *args, stdin=None, check=True):
        r = subprocess.run(["lore", *args], capture_output=True, text=True, env=self.env, input=stdin)
        if check:
            assert r.returncode == 0, r.stderr
        return r

    def create_spec(self, body=SPEC_BODY):
        out = self.lore("record", "create", "--kind", "spec", "--title", "Seam spec", "--status", "ready",
                        "--vault", "default", stdin=body).stdout
        return out.strip().split("/", 1)[1]

    def create_parent(self, spec, body=PARENT_BODY, title="Seam parent"):
        out = self.lore("record", "create", "--kind", "task", "--title", title, "--status", "in-progress",
                        "--label", "craft/slice-parent=", "--related", f"spec={spec}", "--vault", "default",
                        stdin=body).stdout
        return out.strip().split("/", 1)[1]

    def show(self, record_id) -> dict:
        return json.loads(self.lore("record", "show", record_id, "--json").stdout)

    def stored(self, record_id) -> bytes:
        kind, name = record_id.split("/", 1)
        return (self.vault / kind / f"{name}.md").read_bytes()

    def script(self, name, *args, stdin=None):
        return subprocess.run([sys.executable, str(_SCRIPTS / name), *args], input=stdin,
                              capture_output=True, text=True, env=self.env)

    def observation_gate(self, parent):
        return self.script("observation_gate.py", stdin=self.show(f"task/{parent}")["body"])

    def pending_check(self, spec, parent, entries):
        path = self.tmp_path / "entries.json"
        path.write_text(json.dumps(entries))
        return self.script("pending_check.py", "--spec", spec, "--parent", parent, "--entries", str(path),
                           "--vault", "default")

    def open_slices(self, spec) -> str:
        query = (f"kind:task related-spec:{spec} has:label.craft.slice-parent "
                 "-status:done -status:dropped -status:superseded")
        return self.lore("search", query).stdout

    def close(self, parent):
        self.lore("record", "update", f"task/{parent}", "--status", "done")

    def reconcile(self, spec, parents):
        """Append a ledger line for each done parent not yet on it, as `/craft:slice` step 4
        builds it, then run the ledger gate with a `--parent-coverage` map built from the same
        parent reads."""
        existing = self.show(f"spec/{spec}")["body"]
        coverage = {}
        lines = []
        for parent in parents:
            shown = self.show(f"task/{parent}")
            body = shown["body"]
            fields = {}
            for label in ("Covers", "Partially covers"):
                for line in body.splitlines():
                    if line.startswith(f"**{label}:**"):
                        fields[label.lower()] = line.split(":**", 1)[1].strip()
            claim = body.split("**Value claim:**", 1)[1].splitlines()[0].strip()
            tokens = "".join(f", {k} {v}" for k, v in fields.items())
            closed = shown["sidecar"]["updated-at"][:10]
            if f"`task/{parent}`" not in existing:
                lines.append(f"- **{shown['sidecar']['title']}** — {claim} (`task/{parent}`, closed {closed}{tokens})")
            coverage[parent] = fields
        current = self.show(f"spec/{spec}")
        head, sep, tail = current["body"].partition("\n## Waiting")
        proposed = head.rstrip("\n") + "\n" + "\n".join(lines) + "\n" + (("\n## Waiting" + tail) if sep else "")
        self.lore("record", "update", f"spec/{spec}", "--base", current["revision"], stdin=proposed)
        path = self.tmp_path / "parent-coverage.json"
        path.write_text(json.dumps(coverage))
        return lines, self.script("ledger_gate.py", "--parent-coverage", str(path),
                                  stdin=self.show(f"spec/{spec}")["body"])

    def candidates(self, spec):
        return self.script("candidate_set.py", stdin=self.show(f"spec/{spec}")["body"])


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.delenv("LORE_VAULT_GUARD_ROOT", raising=False)
    monkeypatch.delenv("LORE_VAULT_GUARD_EXEMPT", raising=False)
    monkeypatch.setenv("LORE_GROUPS_DIR", str(tmp_path / "groups"))
    return World(tmp_path)


def _tokens(out: str) -> dict[str, str]:
    return dict(line.split(": ", 1) for line in out.splitlines())


def test_a_manual_check_waiting_on_the_operator_closes_the_slice_and_reaches_the_ledger_and_next_pass(world):
    spec = world.create_spec()
    parent = world.create_parent(spec)

    stalled = world.observation_gate(parent)  # step 1
    assert stalled.returncode == 1
    assert "AC2" in stalled.stderr
    assert "task/" + parent in world.open_slices(spec)

    written = world.pending_check(spec, parent, [ENTRY])  # step 2
    assert written.returncode == 0, written.stderr
    assert written.stdout.splitlines() == ["pending: AC2", "spec: written", "parent: updated"]
    entries = waiting.parse_waiting(world.show(f"spec/{spec}")["body"])
    assert [(e.criterion_id, e.building_slice, e.status) for e in entries] == [("AC2", parent, "waiting")]
    parent_body = world.show(f"task/{parent}")["body"]
    assert "**Covers:** AC1\n" in parent_body
    assert "**Partially covers:** AC2\n" in parent_body
    assert "- **AC2** — pending-check — " in parent_body

    accepted = world.observation_gate(parent)  # step 3
    assert accepted.returncode == 0, accepted.stderr
    world.close(parent)
    assert world.show(f"task/{parent}")["sidecar"]["status"] == "done"

    assert "task/" + parent not in world.open_slices(spec)  # step 4

    lines, ledger = world.reconcile(spec, [parent])  # step 5
    assert ledger.returncode == 0, ledger.stderr
    assert lines[0].endswith("covers AC1, partially covers AC2)")

    derived = world.candidates(spec)  # step 6
    assert derived.returncode == 0, derived.stderr
    tokens = _tokens(derived.stdout)
    assert tokens["candidates"] == "AC3"
    assert tokens["waiting"] == "AC2"
    assert tokens["complete-eligible"] == "no"

    third = world.create_parent(spec, AUTOMATED_PARENT_BODY.replace("AC1", "AC3"), title="Third")  # step 7
    assert world.observation_gate(third).returncode == 0
    world.close(third)
    _, ledger = world.reconcile(spec, [parent, third])
    assert ledger.returncode == 0, ledger.stderr
    tokens = _tokens(world.candidates(spec).stdout)
    assert tokens["candidates"] == "none"
    assert tokens["complete-eligible"] == "no"


def test_a_credential_shaped_check_is_refused_leaving_both_records_and_the_gate_still_stalls(world):
    spec = world.create_spec()
    parent = world.create_parent(spec)
    spec_before, parent_before = world.stored(f"spec/{spec}"), world.stored(f"task/{parent}")

    refused = world.pending_check(spec, parent, [dict(ENTRY, check=f"token {SECRET}")])

    assert refused.returncode == 1
    assert SECRET not in refused.stdout + refused.stderr
    assert world.stored(f"spec/{spec}") == spec_before
    assert world.stored(f"task/{parent}") == parent_before
    assert world.observation_gate(parent).returncode == 1


def test_a_spec_with_no_waiting_section_and_an_all_automated_parent_closes_and_derives_as_before(world):
    spec = world.create_spec()
    parent = world.create_parent(spec, AUTOMATED_PARENT_BODY)

    assert world.observation_gate(parent).returncode == 0
    world.close(parent)
    assert "task/" + parent not in world.open_slices(spec)
    lines, ledger = world.reconcile(spec, [parent])
    assert ledger.returncode == 0, ledger.stderr
    assert lines[0].endswith("covers AC1)")

    tokens = _tokens(world.candidates(spec).stdout)
    assert tokens["candidates"] == "AC2, AC3"
    assert tokens["waiting"] == "none"
    assert tokens["complete-eligible"] == "yes"
    assert "## Waiting" not in world.show(f"spec/{spec}")["body"]
