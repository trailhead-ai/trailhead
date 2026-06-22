"""Slice 5 (S2) tests: ``lore record delete`` CLI.

Covers every bullet in the Slice 5 test contract
(plan ``lore-record-and-session-cli-s2.md``):

  delete:
    - delete removes all three artifacts (md + json + index row).
    - invalid / nonexistent RECORD_ID → non-zero, nothing side-effected.

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern).
Never writes to the real $LORE_VAULT; always injects LORE_VAULT + XDG_STATE_HOME.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import make_vault as _make_vault, run_cli as _run

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_record(vault, state, *, kind="spec", title="Test Record", body="body text\n") -> str:
    """Create a record via the CLI and return its RECORD_ID."""
    r = _run(
        ["record", "create", "--kind", kind, "--title", title,
         "--keyword", "test"],
        vault=vault, state_dir=state,
        stdin_text=body,
    )
    assert r.returncode == 0, f"create failed: {r.stderr}"
    return r.stdout.strip()


def _open_index(state_dir):
    """Load index_store and open the index for test assertions."""
    import importlib.util
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(
        "index_store_test", SCRIPTS_DIR / "index_store.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.open_index(env={"XDG_STATE_HOME": str(state_dir)})


# ---------------------------------------------------------------------------
# delete: removes all three artifacts
# ---------------------------------------------------------------------------

def test_delete_removes_body(tmp_path):
    """delete removes the .md body file (AC13)."""
    vault, state = _make_vault(tmp_path)
    record_id = _create_record(vault, state)
    kind, name = record_id.split("/", 1)
    body_path = vault / kind / f"{name}.md"
    assert body_path.exists()

    r = _run(["record", "delete", record_id], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert not body_path.exists()


def test_delete_removes_sidecar(tmp_path):
    """delete removes the .json sidecar file (AC13)."""
    vault, state = _make_vault(tmp_path)
    record_id = _create_record(vault, state)
    kind, name = record_id.split("/", 1)
    sidecar_path = vault / kind / f"{name}.json"
    assert sidecar_path.exists()

    r = _run(["record", "delete", record_id], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr
    assert not sidecar_path.exists()


def test_delete_removes_index_row(tmp_path):
    """delete removes the index row (AC13)."""
    vault, state = _make_vault(tmp_path)
    record_id = _create_record(vault, state)
    kind, name = record_id.split("/", 1)

    r = _run(["record", "delete", record_id], vault=vault, state_dir=state)
    assert r.returncode == 0, r.stderr

    conn = _open_index(state)
    try:
        rows = conn.execute(
            "SELECT name FROM records WHERE vault=? AND kind=? AND name=?",
            (str(vault), kind, name),
        ).fetchall()
    finally:
        conn.close()
    assert rows == []


def test_delete_nonexistent_id_exits_nonzero(tmp_path):
    """delete with a nonexistent RECORD_ID → non-zero (AC13)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "delete", "spec/does-not-exist"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    assert "not found" in r.stderr.lower() or "error" in r.stderr.lower()


def test_delete_invalid_id_format_exits_nonzero(tmp_path):
    """delete with an invalid RECORD_ID (no slash) → non-zero."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "delete", "no-slash-here"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0


# ---------------------------------------------------------------------------
# CRITICAL security regression (audit Finding 1/5): RECORD_ID confinement on
# delete + update. A crafted ID with '..' / absolute segments must NOT delete
# or overwrite .md/.json files outside the active vault.
# ---------------------------------------------------------------------------

def _make_outside_victim(tmp_path: Path) -> Path:
    """Create a sibling 'other-vault' with a victim spec record outside the vault."""
    other = tmp_path / "other-vault" / "spec"
    other.mkdir(parents=True, exist_ok=True)
    (other / "victim.md").write_text("important other-vault content\n", encoding="utf-8")
    (other / "victim.json").write_text('{"kind": "spec"}\n', encoding="utf-8")
    return other


@pytest.mark.parametrize(
    "evil_id",
    [
        "../other-vault/spec/victim",          # climb out of the vault root
        "spec/../../other-vault/spec/victim",  # climb out via the name half
        "/etc/passwd",                          # absolute (no slash-split escape)
        "spec/../../../etc/hosts",              # deep traversal
    ],
)
def test_delete_rejects_record_id_escaping_vault(tmp_path, evil_id):
    """A traversal RECORD_ID on delete → non-zero, victim files untouched (AC14a)."""
    vault, state = _make_vault(tmp_path)
    victim = _make_outside_victim(tmp_path)

    r = _run(["record", "delete", evil_id], vault=vault, state_dir=state)
    assert r.returncode != 0, f"escape ID {evil_id!r} was not rejected: {r.stdout!r}"
    # The outside victim must still be on disk — nothing deleted outside the vault.
    assert (victim / "victim.md").exists()
    assert (victim / "victim.json").exists()


@pytest.mark.parametrize(
    "evil_id",
    [
        "../other-vault/spec/victim",
        "spec/../../other-vault/spec/victim",
        "/etc/passwd",
    ],
)
def test_update_rejects_record_id_escaping_vault(tmp_path, evil_id):
    """A traversal RECORD_ID on update → non-zero, victim files unmodified (AC14a)."""
    vault, state = _make_vault(tmp_path)
    victim = _make_outside_victim(tmp_path)
    before = (victim / "victim.md").read_text(encoding="utf-8")

    r = _run(
        ["record", "update", evil_id],
        vault=vault, state_dir=state,
        stdin_text="HIJACKED BODY\n",
    )
    assert r.returncode != 0, f"escape ID {evil_id!r} was not rejected: {r.stdout!r}"
    # The outside victim body must be byte-for-byte unchanged.
    assert (victim / "victim.md").read_text(encoding="utf-8") == before

