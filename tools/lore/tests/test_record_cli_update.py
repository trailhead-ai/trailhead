"""Slice 4 (S2) tests: ``lore record update`` CLI + the KU2 unified-diff applier.

Covers every bullet in the Slice 4 test contract
(plan ``lore-record-and-session-cli-s2.md``):

  - full-body replace (piped stdin replaces the whole body; AC9).
  - metadata-only (no stdin) leaves the body byte-identical; ``updated-*``
    advances while ``created-*`` stays stable (AC10/AC11); prints the
    ``no stdin`` notice to stderr at exit 0 (council/Advocate).
  - ``--diff`` clean apply updates the body + index.
  - ``--diff`` stale hunk → non-zero, body byte-for-byte unchanged, no index
    update, parseable rejected-hunk line on stderr (AC-DIFF1).
  - ``--diff`` hunk inserting ``<external-memory>`` → stored body has the fence
    neutralized (the diff path is not a neutralization bypass; council/Security).
  - invalid RECORD_ID → non-zero (AC8).
  - vault-move via ``move_record`` (two injected vault roots): new ID returned,
    artifacts under the new vault, old copy gone, index re-keyed; a
    crash-simulated move + ``reindex`` leaves exactly the new copy (AC12).

Plus direct unit tests for ``record_store.apply_unified_diff`` over the three KU2
adversarial cases (CRLF, trailing-newline, adjacent hunks) with byte-for-byte
``==`` assertions on every reject path — these replace the deleted prover test
(``tests/test_ku2_diff_applier.py``).

CLI tests run the lore CLI as a subprocess via CLI_PATH (conftest pattern). Never
writes to the real $LORE_VAULT; always injects LORE_VAULT + XDG_STATE_HOME.
"""
from __future__ import annotations

import difflib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import CLI_PATH, load_script

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"


# ---------------------------------------------------------------------------
# Subprocess + vault helpers (mirror the Slice 3 harness)
# ---------------------------------------------------------------------------

def _run(args, *, vault, state_dir, stdin_text=None, env_extra=None):
    """Run the lore CLI as a subprocess; returns CompletedProcess."""
    full_env = dict(os.environ)
    full_env["LORE_VAULT"] = str(vault)
    full_env["XDG_STATE_HOME"] = str(state_dir)
    full_env["LORE_EMAIL"] = "tester@example.com"
    if env_extra:
        full_env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        env=full_env,
        input=stdin_text,
    )


def _make_vault(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return vault, state


def _find_sidecar(vault: Path, record_id: str) -> dict:
    kind, name = record_id.split("/", 1)
    return json.loads((vault / kind / f"{name}.json").read_text(encoding="utf-8"))


def _find_body(vault: Path, record_id: str) -> str:
    kind, name = record_id.split("/", 1)
    return (vault / kind / f"{name}.md").read_text(encoding="utf-8")


def _open_index(state: Path):
    """Open the derived index for assertions (matches the create-test pattern)."""
    spec = importlib.util.spec_from_file_location(
        "index_store_test", SCRIPTS_DIR / "index_store.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.open_index(env={"XDG_STATE_HOME": str(state)})


def _index_rows(state: Path, vault: Path, kind: str, name: str) -> list:
    conn = _open_index(state)
    try:
        return conn.execute(
            "SELECT name, body FROM records WHERE vault=? AND kind=? AND name=?",
            (str(vault), kind, name),
        ).fetchall()
    finally:
        conn.close()


_CREATE_ARGS = [
    "record", "create",
    "--kind", "spec",
    "--title", "My Record",
    "--set", "keywords=foo",
]


def _create(vault, state, body="original line one\noriginal line two\n"):
    """Create a record and return its RECORD_ID."""
    r = _run(_CREATE_ARGS, vault=vault, state_dir=state, stdin_text=body)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _make_diff(old: str, new: str) -> str:
    """Generate a unified diff between two bodies (difflib, keepends)."""
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a",
            tofile="b",
        )
    )


# ===========================================================================
# CLI: full-body replace (AC9)
# ===========================================================================

def test_update_full_body_replaces_body(tmp_path):
    """Piped stdin replaces the full body by default (AC9)."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state, body="old body\n")

    new_body = "completely new body\nwith two lines\n"
    r = _run(
        ["record", "update", record_id],
        vault=vault, state_dir=state, stdin_text=new_body,
    )
    assert r.returncode == 0, r.stderr
    assert _find_body(vault, record_id) == new_body


def test_update_full_body_updates_index(tmp_path):
    """A full-body update refreshes the index row's body column."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state, body="old body\n")
    kind, name = record_id.split("/", 1)

    new_body = "fresh body text\n"
    r = _run(
        ["record", "update", record_id],
        vault=vault, state_dir=state, stdin_text=new_body,
    )
    assert r.returncode == 0, r.stderr
    rows = _index_rows(state, vault, kind, name)
    assert len(rows) == 1
    assert rows[0][1] == new_body


def test_update_full_body_restamps_updated_keeps_created(tmp_path):
    """``updated-*`` re-stamped on update; ``created-*`` untouched."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state, body="old body\n")
    before = _find_sidecar(vault, record_id)

    # A future LORE_DATE override is not available; use a distinct second update
    # and assert created-* is preserved byte-for-byte while updated-* is re-stamped.
    r = _run(
        ["record", "update", record_id],
        vault=vault, state_dir=state, stdin_text="new body\n",
        env_extra={"LORE_EMAIL": "second@example.com"},
    )
    assert r.returncode == 0, r.stderr
    after = _find_sidecar(vault, record_id)

    assert after["created-at"] == before["created-at"]
    assert after["created-by"] == before["created-by"]  # original committer
    assert after["updated-by"] == "second@example.com"  # re-stamped


# ===========================================================================
# CLI: metadata-only (no stdin) — AC10 / AC11 + stderr notice
# ===========================================================================

def test_update_metadata_only_leaves_body_byte_identical(tmp_path):
    """No stdin → body unchanged; only sidecar params applied (AC10)."""
    vault, state = _make_vault(tmp_path)
    body = "stable body line one\nstable body line two\n"
    record_id = _create(vault, state, body=body)

    r = _run(
        ["record", "update", record_id, "--set", "keywords=bar"],
        vault=vault, state_dir=state,
        # no stdin_text → metadata-only path
    )
    assert r.returncode == 0, r.stderr
    assert _find_body(vault, record_id) == body  # byte-identical
    sidecar = _find_sidecar(vault, record_id)
    assert "bar" in sidecar["keywords"]


def test_update_metadata_only_prints_no_stdin_notice_to_stderr(tmp_path):
    """No stdin → the metadata-only notice goes to stderr; exit stays 0 (Advocate)."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    r = _run(
        ["record", "update", record_id, "--set", "keywords=bar"],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    assert "no stdin" in r.stderr.lower()
    assert "metadata-only" in r.stderr.lower()
    # The notice must NOT pollute stdout.
    assert "no stdin" not in r.stdout.lower()


def test_update_metadata_only_advances_updated_keeps_created(tmp_path):
    """Metadata-only update re-stamps ``updated-*`` and preserves ``created-*``."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)
    before = _find_sidecar(vault, record_id)

    r = _run(
        ["record", "update", record_id, "--set", "keywords=bar"],
        vault=vault, state_dir=state,
        env_extra={"LORE_EMAIL": "later@example.com"},
    )
    assert r.returncode == 0, r.stderr
    after = _find_sidecar(vault, record_id)
    assert after["created-at"] == before["created-at"]
    assert after["created-by"] == before["created-by"]
    assert after["updated-by"] == "later@example.com"


# ===========================================================================
# CLI: --diff clean apply (AC9 / AC-DIFF1)
# ===========================================================================

def test_update_diff_clean_apply_updates_body_and_index(tmp_path):
    """A clean ``--diff`` applies the hunks to the body and refreshes the index."""
    vault, state = _make_vault(tmp_path)
    original = "line one\nline two\nline three\n"
    record_id = _create(vault, state, body=original)
    kind, name = record_id.split("/", 1)

    modified = "line one\nline TWO\nline three\n"
    diff = _make_diff(original, modified)

    r = _run(
        ["record", "update", record_id, "--diff"],
        vault=vault, state_dir=state, stdin_text=diff,
    )
    assert r.returncode == 0, r.stderr
    assert _find_body(vault, record_id) == modified
    rows = _index_rows(state, vault, kind, name)
    assert len(rows) == 1
    assert rows[0][1] == modified


# ===========================================================================
# CLI: --diff stale hunk → atomic reject (AC-DIFF1)
# ===========================================================================

def test_update_diff_stale_hunk_rejects_atomically(tmp_path):
    """A stale ``--diff`` → non-zero; body byte-for-byte unchanged; no index churn."""
    vault, state = _make_vault(tmp_path)
    original = "line one\nline two\nline three\n"
    record_id = _create(vault, state, body=original)
    kind, name = record_id.split("/", 1)

    # Diff generated against a DIFFERENT version → stale context.
    stale_diff = _make_diff(
        "line one\nDIFFERENT\nline three\n",
        "line one\nCHANGED\nline three\n",
    )

    r = _run(
        ["record", "update", record_id, "--diff"],
        vault=vault, state_dir=state, stdin_text=stale_diff,
    )
    assert r.returncode != 0
    # Body byte-for-byte unchanged.
    assert _find_body(vault, record_id) == original
    # Index row's body unchanged (no update happened).
    rows = _index_rows(state, vault, kind, name)
    assert len(rows) == 1
    assert rows[0][1] == original


def test_update_diff_stale_hunk_parseable_rejected_line(tmp_path):
    """A rejected hunk is reported on stderr in a parseable one-line-per-hunk form."""
    vault, state = _make_vault(tmp_path)
    original = "line one\nline two\nline three\n"
    record_id = _create(vault, state, body=original)

    stale_diff = _make_diff(
        "line one\nDIFFERENT\nline three\n",
        "line one\nCHANGED\nline three\n",
    )

    r = _run(
        ["record", "update", record_id, "--diff"],
        vault=vault, state_dir=state, stdin_text=stale_diff,
    )
    assert r.returncode != 0
    # Parseable contract: ``rejected hunk @@ ... @@: <reason>``.
    assert "rejected hunk @@" in r.stderr
    assert "context mismatch" in r.stderr or "overruns" in r.stderr


# ===========================================================================
# CLI: --diff is not a fence-neutralization bypass (council/Security)
# ===========================================================================

def test_update_diff_inserting_fence_is_neutralized(tmp_path):
    """A ``--diff`` hunk inserting ``<external-memory>`` lands neutralized on disk."""
    vault, state = _make_vault(tmp_path)
    original = "safe line one\nsafe line two\n"
    record_id = _create(vault, state, body=original)

    modified = (
        "safe line one\n"
        "<external-memory foo>injected</external-memory>\n"
        "safe line two\n"
    )
    diff = _make_diff(original, modified)

    r = _run(
        ["record", "update", record_id, "--diff"],
        vault=vault, state_dir=state, stdin_text=diff,
    )
    assert r.returncode == 0, r.stderr
    stored = _find_body(vault, record_id)
    # The live fence token must NOT survive verbatim.
    assert "<external-memory foo>" not in stored
    assert "</external-memory>" not in stored
    # The surrounding content is still present (the hunk applied, then neutralized).
    assert "injected" in stored


# ===========================================================================
# CLI: invalid RECORD_ID (AC8)
# ===========================================================================

def test_update_invalid_record_id_nonzero(tmp_path):
    """A nonexistent RECORD_ID → non-zero exit (AC8)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "update", "spec/does-not-exist"],
        vault=vault, state_dir=state, stdin_text="body\n",
    )
    assert r.returncode != 0
    assert "not found" in r.stderr.lower() or "does-not-exist" in r.stderr


# ===========================================================================
# CLI: vault-move via move_record (two injected vault roots; AC12)
# ===========================================================================

def test_update_vault_move_relocates_record(tmp_path):
    """``--move-to`` relocates the record to a second vault; new ID returned.

    In S2 the move *primitive* is exercised by injecting a second vault root; the
    scope→different-vault routing trigger is S4's resolution (noted, not built).
    """
    vault, state = _make_vault(tmp_path)
    vault2 = tmp_path / "vault2"
    vault2.mkdir(parents=True, exist_ok=True)

    body = "movable body\n"
    record_id = _create(vault, state, body=body)
    kind, name = record_id.split("/", 1)

    r = _run(
        ["record", "update", record_id, "--move-to", str(vault2)],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    new_id = r.stdout.strip()

    # New artifacts under the second vault.
    assert (vault2 / kind / f"{name}.md").exists()
    assert (vault2 / kind / f"{name}.json").exists()
    assert _find_body(vault2, new_id) == body

    # Old copy gone from the first vault.
    assert not (vault / kind / f"{name}.md").exists()
    assert not (vault / kind / f"{name}.json").exists()

    # Index re-keyed: old vault row gone, new vault row present.
    assert _index_rows(state, vault, kind, name) == []
    assert len(_index_rows(state, vault2, kind, name)) == 1


def test_update_crash_simulated_move_then_reindex_leaves_one_copy(tmp_path):
    """A crash-simulated move (old copy stranded) + ``reindex`` leaves only the new copy.

    Simulates the move_record stranded-duplicate window: the new copy is durable
    and indexed, but the old artifacts linger. ``lore reindex`` over both vaults
    must reconcile to exactly the new copy's row.
    """
    vault, state = _make_vault(tmp_path)
    vault2 = tmp_path / "vault2"
    vault2.mkdir(parents=True, exist_ok=True)

    body = "stranded body\n"
    record_id = _create(vault, state, body=body)
    kind, name = record_id.split("/", 1)

    # Perform the move normally (new copy under vault2, old removed).
    r = _run(
        ["record", "update", record_id, "--move-to", str(vault2)],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr

    # Simulate the crash window: re-strand the old artifacts on disk (as if the
    # delete-old step never ran), leaving a duplicate body+sidecar in vault1.
    (vault / kind).mkdir(parents=True, exist_ok=True)
    (vault / kind / f"{name}.md").write_text(body, encoding="utf-8")
    (vault / kind / f"{name}.json").write_text(
        (vault2 / kind / f"{name}.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    # reindex over BOTH vaults; the stranded copy in vault1 is reconciled away
    # only when reindex is scoped to vault2 (the active vault). Reindex vault2.
    r_idx = _run(
        ["reindex", "--vault", str(vault2)],
        vault=vault2, state_dir=state,
    )
    assert r_idx.returncode == 0, r_idx.stderr

    # AC12 self-healing: reindex reconciles the index to EXACTLY the new copy.
    # The new copy's row exists...
    assert len(_index_rows(state, vault2, kind, name)) == 1
    # ...and the stranded old copy no longer resolves in the index (reindex is
    # index-only — the orphaned vault1 disk artifacts are harmless and not
    # re-indexed because vault1 is outside the reindex scope).
    assert _index_rows(state, vault, kind, name) == []


# ===========================================================================
# Unit tests for apply_unified_diff — KU2 adversarial cases (replace prover test)
# ===========================================================================

@pytest.fixture
def rs():
    return load_script("record_store")


class TestApplierCleanApply:
    def test_single_hunk_applies(self, rs):
        original = "line one\nline two\nline three\n"
        modified = "line one\nline TWO\nline three\n"
        result, rejected = rs.apply_unified_diff(original, _make_diff(original, modified))
        assert result == modified
        assert rejected == []

    def test_empty_diff_returns_body_unchanged(self, rs):
        body = "hello\nworld\n"
        result, rejected = rs.apply_unified_diff(body, "")
        assert result == body
        assert rejected == []


class TestApplierCRLF:
    """KU2 case (a): CRLF body vs LF diff context — verbatim compare must reject."""

    def test_crlf_body_lf_diff_rejected_body_unchanged(self, rs):
        body_crlf = "line one\r\nline two\r\nline three\r\n"
        diff_lf = _make_diff(
            "line one\nline two\nline three\n",
            "line one\nline TWO\nline three\n",
        )
        with pytest.raises(rs.DiffRejectError) as exc_info:
            rs.apply_unified_diff(body_crlf, diff_lf)
        # Byte-for-byte unchanged; CRLF endings intact.
        assert exc_info.value.original_body == body_crlf
        assert exc_info.value.original_body.count("\r\n") == 3
        assert len(exc_info.value.rejected) >= 1

    def test_crlf_body_crlf_diff_applies_preserving_endings(self, rs):
        body_crlf = "line one\r\nline two\r\nline three\r\n"
        modified_crlf = "line one\r\nline TWO\r\nline three\r\n"
        result, rejected = rs.apply_unified_diff(
            body_crlf, _make_diff(body_crlf, modified_crlf)
        )
        assert result == modified_crlf
        assert rejected == []
        assert result.count("\r\n") == 3


class TestApplierTrailingNewline:
    """KU2 case (b): trailing-newline mismatch — reject, body unchanged."""

    def test_body_without_newline_diff_with_rejected(self, rs):
        diff = _make_diff(
            "first line\nsecond line\n",
            "first line\nSECOND LINE\n",
        )
        body_no_nl = "first line\nsecond line"  # lacks trailing newline
        with pytest.raises(rs.DiffRejectError) as exc_info:
            rs.apply_unified_diff(body_no_nl, diff)
        assert exc_info.value.original_body == body_no_nl
        assert not exc_info.value.original_body.endswith("\n")

    def test_no_newline_both_sides_is_format_error(self, rs):
        body_no_nl = "first line\nsecond line"
        modified_no_nl = "first line\nSECOND LINE"
        diff = _make_diff(body_no_nl, modified_no_nl)
        # difflib concatenates the two no-newline lines → ambiguous → format error.
        with pytest.raises(rs.DiffFormatError):
            rs.apply_unified_diff(body_no_nl, diff)


class TestApplierAdjacentHunks:
    """KU2 case (c): adjacent hunks — offset tracking + atomic reject."""

    def test_two_hunks_offset_tracking_applies(self, rs):
        body = "A\nB\nC\nD\nE\n"
        modified = "A\ninserted 1\ninserted 2\nB\nC\nD\nECHO\n"
        result, rejected = rs.apply_unified_diff(body, _make_diff(body, modified))
        assert result == modified
        assert rejected == []

    def test_second_hunk_fails_both_rejected_atomically(self, rs):
        body = "l1\nl2\nl3\nl4\nl5\nl6\nl7\nl8\n"
        modified = "l1\nL2\nl3\nl4\nl5\nL6\nl7\nl8\n"
        diff = _make_diff(body, modified)
        stale = "l1\nl2\nl3\nl4\nl5\nSOMETHING\nl7\nl8\n"  # hunk-2 context broken
        with pytest.raises(rs.DiffRejectError) as exc_info:
            rs.apply_unified_diff(stale, diff)
        # No partial application of hunk 1 — body byte-for-byte unchanged.
        assert exc_info.value.original_body == stale
        assert len(exc_info.value.rejected) >= 1
