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
def isolated_lore_env(monkeypatch, tmp_path):
    monkeypatch.delenv("LORE_VAULT_GUARD_ROOT", raising=False)
    monkeypatch.delenv("LORE_VAULT_GUARD_EXEMPT", raising=False)
    monkeypatch.setenv("LORE_GROUPS_DIR", str(tmp_path / "groups"))
    return make_vault(tmp_path)


def test_amended_away_criterion_is_void_through_the_real_lore_cli(  # inert-gate: allow seam smoke
    isolated_lore_env, tmp_path
):
    vault, state = isolated_lore_env

    def lore(*args, stdin_text=None):
        r = run_cli(list(args), vault=vault, state_dir=state, stdin_text=stdin_text)
        assert r.returncode == 0, r.stderr
        return r.stdout

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
    assert lines[-1] == "void: AC2 (removed by amend)"


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
    isolated_lore_env, tmp_path
):
    vault, state = isolated_lore_env

    def lore(*args, stdin_text=None):
        r = run_cli(list(args), vault=vault, state_dir=state, stdin_text=stdin_text)
        assert r.returncode == 0, r.stderr
        return r.stdout

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
