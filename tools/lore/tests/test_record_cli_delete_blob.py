"""Slice 5 (S2) tests: ``lore record delete`` + ``lore record blob`` CLI.

Covers every bullet in the Slice 5 test contract
(plan ``lore-record-and-session-cli-s2.md``):

  delete:
    - delete removes all three artifacts (md + json + index row).
    - invalid / nonexistent RECORD_ID → non-zero, nothing side-effected.

  blob:
    - blob writes under ``blob/``, creating nested intermediate dirs.
    - content is stored (fence-neutralized for a body containing
      ``<external-memory>`` tokens).

  traversal matrix (AC14a — Critical surface):
    - ``../escape`` (relative traversal) → non-zero, nothing written outside
      the blob root.
    - absolute path ``/etc/x`` → non-zero, nothing written.
    - absolute path under tmp → non-zero, nothing written.
    - symlink whose realpath escapes the blob root → non-zero, nothing
      written at the symlink target.
    - benign nested path ``a/b/c.md`` → non-zero 0, file written under blob/.

Blob path-traversal confinement invariant (AC14a, S2):
  The blob write path MUST resolve to a descendant of ``realpath(blob_root)``
  after making the blob_root exist (so realpath resolves through real dirs).
  This check catches ``..`` segments, absolute paths, and symlink escapes.
  The descendant check is:
      ``rp == blob_root_real or rp.startswith(blob_root_real + os.sep)``
  which cannot be fooled by a sibling directory sharing a name prefix.

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
# blob: writes under blob/, creating nested dirs, fence-neutralized
# ---------------------------------------------------------------------------

def test_blob_writes_under_blob_dir(tmp_path):
    """blob writes the content under <vault>/blob/<path> (AC14)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "blob", "notes/readme.md"],
        vault=vault, state_dir=state,
        stdin_text="# Hello\nThis is a blob.\n",
    )
    assert r.returncode == 0, r.stderr
    target = vault / "blob" / "notes" / "readme.md"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "# Hello\nThis is a blob.\n"


def test_blob_creates_nested_intermediate_dirs(tmp_path):
    """blob creates all intermediate directories under blob/ (AC14)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "blob", "a/b/c/deep.md"],
        vault=vault, state_dir=state,
        stdin_text="deep content\n",
    )
    assert r.returncode == 0, r.stderr
    target = vault / "blob" / "a" / "b" / "c" / "deep.md"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "deep content\n"


def test_blob_fence_neutralizes_external_memory(tmp_path):
    """blob body containing <external-memory> is stored neutralized (AC-FENCE1)."""
    vault, state = _make_vault(tmp_path)
    body_with_fence = (
        "<external-memory>\nsome injected content\n</external-memory>\n"
    )
    r = _run(
        ["record", "blob", "injection-test.md"],
        vault=vault, state_dir=state,
        stdin_text=body_with_fence,
    )
    assert r.returncode == 0, r.stderr
    stored = (vault / "blob" / "injection-test.md").read_text(encoding="utf-8")
    # The stored content must NOT contain a live fence token.
    assert "<external-memory>" not in stored
    assert "</external-memory>" not in stored


# ---------------------------------------------------------------------------
# traversal matrix (AC14a — Critical)
# ---------------------------------------------------------------------------

def test_blob_rejects_dotdot_path(tmp_path):
    """blob rejects a path with '..' traversal segments → non-zero, nothing written (AC14a)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "blob", "../escape.md"],
        vault=vault, state_dir=state,
        stdin_text="escape attempt\n",
    )
    assert r.returncode != 0
    # The escape target must not exist.
    escape_target = vault.parent / "escape.md"
    assert not escape_target.exists()
    # stderr must name the problem clearly.
    assert r.stderr.strip() != ""


def test_blob_rejects_absolute_path_etc(tmp_path):
    """blob rejects an absolute path → non-zero, nothing written (AC14a)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "blob", "/etc/x"],
        vault=vault, state_dir=state,
        stdin_text="escape attempt\n",
    )
    assert r.returncode != 0
    assert not Path("/etc/x").exists()
    assert r.stderr.strip() != ""


def test_blob_rejects_absolute_path_under_tmp(tmp_path):
    """blob rejects an absolute path even under tmp → non-zero, nothing written (AC14a)."""
    vault, state = _make_vault(tmp_path)
    escape_target = tmp_path / "escaped.md"
    r = _run(
        ["record", "blob", str(escape_target)],
        vault=vault, state_dir=state,
        stdin_text="escape attempt\n",
    )
    assert r.returncode != 0
    assert not escape_target.exists()
    assert r.stderr.strip() != ""


def test_blob_rejects_symlink_escaping_blob_root(tmp_path):
    """blob rejects a path whose realpath leaves the blob root (AC14a).

    Specifically: we create a symlink inside blob/ that points outside the vault
    (to a tmp directory), then attempt to write through it.  The realpath check
    detects the escape and must reject with non-zero and no write at the
    symlink target.
    """
    vault, state = _make_vault(tmp_path)
    blob_root = vault / "blob"
    blob_root.mkdir(parents=True, exist_ok=True)

    # Create a directory outside the vault that the symlink will point to.
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)

    # Create a symlink inside blob/ pointing outside.
    evil_link = blob_root / "evil"
    evil_link.symlink_to(outside_dir)

    r = _run(
        ["record", "blob", "evil/x.md"],
        vault=vault, state_dir=state,
        stdin_text="symlink escape\n",
    )
    assert r.returncode != 0, (
        f"Expected non-zero for symlink escape, got 0. stderr={r.stderr!r}"
    )
    # Nothing must be written at the symlink target.
    assert not (outside_dir / "x.md").exists()
    assert r.stderr.strip() != ""


def test_blob_accepts_benign_nested_path(tmp_path):
    """A benign nested path a/b/c.md succeeds (AC14)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "blob", "a/b/c.md"],
        vault=vault, state_dir=state,
        stdin_text="benign content\n",
    )
    assert r.returncode == 0, r.stderr
    target = vault / "blob" / "a" / "b" / "c.md"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "benign content\n"


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


# ---------------------------------------------------------------------------
# blob degenerate-path guard (audit Finding 3): '.' must not slip a transient
# .tmp write to the vault level. (Finding 4 — NUL byte — cannot traverse argv:
# execve/subprocess reject an embedded NUL before the CLI runs, so the in-CLI
# NUL guard is defense-in-depth and not exercisable via the subprocess harness.)
# ---------------------------------------------------------------------------

def test_blob_rejects_dot_path(tmp_path):
    """blob path '.' → non-zero, no stray .tmp written at the vault level (Finding 3)."""
    vault, state = _make_vault(tmp_path)
    r = _run(["record", "blob", "."], vault=vault, state_dir=state, stdin_text="x\n")
    assert r.returncode != 0
    # No transient blob*.tmp leaked at the vault root level.
    assert list(vault.glob("blob*.tmp")) == []
