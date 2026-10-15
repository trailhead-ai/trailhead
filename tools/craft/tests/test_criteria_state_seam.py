"""Seam: a spec amended through the real `lore record amend` to remove a
criterion its ledger already covers, read back through the real
`lore record criteria` / `lore record show`, passes `ledger_gate.py` and
`candidate_set.py --criteria-state`.

Lore's CLI runs in-process against a temp vault under the isolation fixtures
of lore's own test conftest, loaded by file path under a unique module name
(a bare `import conftest` would collide with craft's). The importing module
gets the autouse fixtures that fence HOME, XDG dirs and the publish worker.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_LORE_TESTS = Path(__file__).resolve().parents[2] / "lore" / "tests"
_SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "craft" / "scripts"

_conftest_spec = importlib.util.spec_from_file_location(
    "lore_tests_conftest_for_craft_seam", _LORE_TESTS / "conftest.py"
)
_lore_conftest = importlib.util.module_from_spec(_conftest_spec)
sys.modules["lore_tests_conftest_for_craft_seam"] = _lore_conftest
_conftest_spec.loader.exec_module(_lore_conftest)

from lore_tests_conftest_for_craft_seam import (  # noqa: E402,F401
    _allow_real_publish_spawn,
    _isolate_ambient_env,
    _publish_spawn_guard,
    make_vault,
    run_cli,
)

BODY_BEFORE = """# Seam spec

## Acceptance Criteria

- **AC1.** One.
- **AC2.** Two.
- **AC3.** Three.

## Slices

- **First** — a value claim. (`task/first`, closed 2026-01-01, covers AC1, AC2)
"""

BODY_AFTER = BODY_BEFORE.replace("- **AC2.** Two.\n", "")


def _gate(script: str, body: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / script), *args],
        input=body,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def lore(monkeypatch, tmp_path):
    """Run the real lore CLI in-process against an isolated temp vault; return stdout."""
    monkeypatch.delenv("LORE_VAULT_GUARD_ROOT", raising=False)
    monkeypatch.delenv("LORE_VAULT_GUARD_EXEMPT", raising=False)
    monkeypatch.setenv("LORE_GROUPS_DIR", str(tmp_path / "groups"))
    vault, state = make_vault(tmp_path)

    def run(*args, stdin_text=None):
        r = run_cli(list(args), vault=vault, state_dir=state, stdin_text=stdin_text)
        assert r.returncode == 0, r.stderr
        return r.stdout

    return run


def test_amended_away_criterion_is_void_through_the_real_lore_cli(  # inert-gate: allow seam smoke
    lore, tmp_path
):
    record_id = lore(
        "record", "create", "--kind", "spec", "--title", "Seam", "--keyword", "seam",
        "--status", "ready", stdin_text=BODY_BEFORE,
    ).strip()
    revision = json.loads(lore("record", "show", record_id, "--json"))["revision"]
    lore(
        "record", "amend", record_id, "--reason", "drop AC2", "--initiated-by", "operator",
        "--base", revision, stdin_text=BODY_AFTER,
    )

    state_file = tmp_path / "criteria.json"
    state_file.write_text(lore("record", "criteria", record_id), encoding="utf-8")
    shown = lore("record", "show", record_id)

    ledger = _gate("ledger_gate.py", shown)
    assert ledger.returncode == 0, ledger.stderr

    without_flag = _gate("candidate_set.py", shown)
    assert without_flag.returncode == 1
    assert "undeclared-covered-identifier" in without_flag.stderr

    gate = _gate("candidate_set.py", shown, "--criteria-state", str(state_file))
    assert gate.returncode == 0, gate.stderr
    lines = gate.stdout.splitlines()
    assert "covered: AC1" in lines
    assert "candidates: AC3" in lines
    assert lines[5] == "void: AC2 (removed by amend)"


BODY_DUPLICATED = """# Seam spec

## Acceptance Criteria

- **AC1.** One.
- **AC2.** Two.
- **AC2.** Two again.
- **AC007.** Seven.
- **AC8.** Eight.

## Slices

- **First** — a value claim. (`task/first`, closed 2026-01-01, covers AC1)
"""


def test_a_body_with_a_repeated_and_a_non_canonical_id_passes_the_gate_with_lores_state(  # inert-gate: allow seam smoke
    lore, tmp_path
):
    record_id = lore(
        "record", "create", "--kind", "spec", "--title", "Seam dup", "--keyword", "seam",
        "--status", "ready", stdin_text=BODY_DUPLICATED,
    ).strip()
    printed = json.loads(lore("record", "criteria", record_id))
    assert printed["declared"] == ["AC1", "AC2", "AC2", "AC007", "AC8"]

    state_file = tmp_path / "criteria.json"
    state_file.write_text(json.dumps(printed), encoding="utf-8")
    shown = lore("record", "show", record_id)

    assert _gate("candidate_set.py", shown).returncode == 0
    gate = _gate("candidate_set.py", shown, "--criteria-state", str(state_file))
    assert gate.returncode == 0, gate.stderr


BODY_UNICODE_DIGIT = """# Seam spec

## Acceptance Criteria

- **AC1.** One.
- **AC٢.** Two.

## Slices

- **First** — a value claim. (`task/first`, closed 2026-01-01, covers AC1)
"""


def test_a_body_declaring_a_unicode_digit_id_passes_the_gate_with_lores_state(  # inert-gate: allow seam smoke
    lore, tmp_path
):
    record_id = lore(
        "record", "create", "--kind", "spec", "--title", "Seam unicode", "--keyword", "seam",
        "--status", "ready", stdin_text=BODY_UNICODE_DIGIT,
    ).strip()
    printed = json.loads(lore("record", "criteria", record_id))
    assert printed["declared"] == ["AC1", "AC٢"]

    state_file = tmp_path / "criteria.json"
    state_file.write_text(json.dumps(printed), encoding="utf-8")
    shown = lore("record", "show", record_id)

    gate = _gate("candidate_set.py", shown, "--criteria-state", str(state_file))
    assert gate.returncode == 0, gate.stderr


BODY_LEDGER = BODY_BEFORE.replace(
    "covers AC1, AC2)",
    "covers AC1, AC2)\n- **Second** — more. (`task/second`, closed 2026-01-02, covers AC2)",
)
BODY_NO_LEDGER = BODY_BEFORE.split("## Slices")[0].rstrip() + "\n"


_SNAPSHOT_OF_TWO_STDOUT = (
    "criteria: AC1, AC2, AC3\n"
    "covered: AC1\n"
    "candidates: AC2, AC3\n"
    "partial: none\n"
    "complete-eligible: yes\n"
    "void: AC2 (reopened by amend)\n"
    "closed: AC1\n"
    "closed-uncounted: none\n"
)


@pytest.mark.parametrize(
    ("body", "reopened", "closed", "expected_stdout"),
    [
        (BODY_LEDGER, {"AC2": ["first", "second"]}, ["AC1"], _SNAPSHOT_OF_TWO_STDOUT),
        (BODY_NO_LEDGER, {"AC2": []}, [], None),
    ],
    ids=["snapshot-of-two-tasks", "empty-snapshot"],
)
def test_lores_reopen_and_close_marks_are_accepted_by_the_gate(  # inert-gate: allow seam smoke
    lore, tmp_path, body, reopened, closed, expected_stdout
):
    record_id = lore(
        "record", "create", "--kind", "spec", "--title", "Seam marks", "--keyword", "seam",
        "--status", "ready", stdin_text=body,
    ).strip()
    lore(
        "record", "amend", record_id, "--reason", "reopen AC2",
        "--initiated-by", "operator", "--reopen", "AC2",
        *[a for c in closed for a in ("--close", c)], stdin_text="",
    )
    printed = json.loads(lore("record", "criteria", record_id))
    assert printed["reopened"] == reopened
    assert printed.get("closed", []) == closed

    marked_file = tmp_path / "marked.json"
    marked_file.write_text(json.dumps(printed), encoding="utf-8")
    plain_file = tmp_path / "plain.json"
    plain_file.write_text(
        json.dumps({k: v for k, v in printed.items() if k not in ("reopened", "closed")}),
        encoding="utf-8",
    )
    shown = lore("record", "show", record_id)

    marked = _gate("candidate_set.py", shown, "--criteria-state", str(marked_file))
    plain = _gate("candidate_set.py", shown, "--criteria-state", str(plain_file))
    assert marked.returncode == 0, marked.stderr
    if expected_stdout is None:
        assert marked.stdout == plain.stdout
    else:
        assert marked.stdout == expected_stdout


BODY_THREE_ENTRIES_BASE = """# Seam spec

## Acceptance Criteria

- **AC1.** One.
- **AC2.** Two.
- **AC3.** Three.

## Slices

- **First** — a value claim. (`task/first`, closed 2026-01-01, covers AC1)
- **Second** — more. (`task/second`, closed 2026-01-02, partially covers AC2)
"""


def _ledger_gate_with_parents(body: str, tmp_path: Path, parents: dict) -> subprocess.CompletedProcess:
    parents_file = tmp_path / "parents.json"
    parents_file.write_text(json.dumps(parents), encoding="utf-8")
    return _gate("ledger_gate.py", body, "--parent-coverage", str(parents_file))


def test_reopen_then_recover_then_close_flows_through_the_real_cli_and_gates(  # inert-gate: allow seam smoke
    lore, tmp_path
):
    record_id = lore(
        "record", "create", "--kind", "spec", "--title", "Seam flow", "--keyword", "seam",
        "--status", "ready", stdin_text=BODY_THREE_ENTRIES_BASE,
    ).strip()
    lore(
        "record", "amend", record_id, "--reason", "reopen AC1",
        "--initiated-by", "operator", "--reopen", "AC1", stdin_text="",
    )

    third = "- **Third** — recovers AC1. (`task/third`, closed 2026-01-03, covers AC1)\n"
    lore("record", "update", record_id, stdin_text=BODY_THREE_ENTRIES_BASE + third)
    shown = lore("record", "show", record_id)
    assert "task/third" in shown

    parents = {
        "first": {"covers": "AC1"},
        "second": {"partially covers": "AC2"},
        "third": {"covers": "AC1"},
    }
    certified = _ledger_gate_with_parents(shown, tmp_path, parents)
    assert certified.returncode == 0, certified.stderr

    lore(
        "record", "amend", record_id, "--reason", "close AC2",
        "--initiated-by", "operator", "--close", "AC2", stdin_text="",
    )
    shown = lore("record", "show", record_id)
    state_file = tmp_path / "criteria.json"
    state_file.write_text(lore("record", "criteria", record_id), encoding="utf-8")

    gate = _gate("candidate_set.py", shown, "--criteria-state", str(state_file))
    assert gate.returncode == 0, gate.stderr
    lines = gate.stdout.splitlines()
    assert "covered: AC1, AC2" in lines
    assert "candidates: AC3" in lines
    assert "partial: none" in lines
    assert "void: AC1 (reopened by amend)" in lines
    assert "closed: AC2" in lines
    assert "closed-uncounted: none" in lines


def test_a_stale_snapshot_task_does_not_fail_the_gate_through_lores_state(  # inert-gate: allow seam smoke
    lore, tmp_path
):
    record_id = lore(
        "record", "create", "--kind", "spec", "--title", "Seam stale", "--keyword", "seam",
        "--status", "ready", stdin_text=BODY_LEDGER,
    ).strip()
    lore(
        "record", "amend", record_id, "--reason", "reopen AC2",
        "--initiated-by", "operator", "--reopen", "AC2", stdin_text="",
    )
    shown = lore("record", "show", record_id)
    without_second = "\n".join(
        ln for ln in shown.split("\n") if "task/second" not in ln
    )
    state_file = tmp_path / "criteria.json"
    state_file.write_text(lore("record", "criteria", record_id), encoding="utf-8")

    gate = _gate("candidate_set.py", without_second, "--criteria-state", str(state_file))
    assert gate.returncode == 0, gate.stderr
    assert "void: AC2 (reopened by amend)" in gate.stdout.splitlines()
