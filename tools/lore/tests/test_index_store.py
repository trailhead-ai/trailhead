"""Slice 1 (S2) tests: minimal derived index store + ``lore reindex``.

Covers every bullet in the Slice 1 test contract:
  - ``open_index`` creates the DB at the resolved state path (honoring a tmp
    ``$XDG_STATE_HOME`` override) and reports ``journal_mode == "wal"``.
  - ``upsert_row`` is idempotent: upserting the same key twice leaves one row;
    changing a scalar updates in place.
  - ``delete_row`` removes exactly the keyed row; deleting a missing row is a
    no-op (no raise).
  - ``rebuild`` over N sidecar+body pairs produces exactly N rows keyed
    correctly; re-running ``rebuild`` is idempotent; a since-deleted record
    row is gone after rebuild (drop + repopulate proves the recovery path).
  - ``rebuild`` spanning two vault roots keys rows by their respective vault.
  - ``lore reindex`` exits 0 and prints a row count.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Conftest helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"
CLI_PATH = REPO_ROOT / "plugins" / "lore" / "cli" / "lore"


def load_index_store(xdg_state_home: Path | None = None):
    """Load index_store freshly, optionally injecting XDG_STATE_HOME."""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    name = "index_store"
    if name in sys.modules:
        del sys.modules[name]
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    if xdg_state_home is not None:
        # Inject env override before exec so the module resolves path at import.
        # index_store uses a module-level lazy resolver; pass env via monkeypatch
        # is caller responsibility — we just pass xdg_state_home for open_index.
        pass
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def state_dir(tmp_path):
    """Return a tmp XDG_STATE_HOME base and the expected lore state path."""
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    return fake_state


@pytest.fixture()
def env_with_state(state_dir):
    """A dict-copy of os.environ with XDG_STATE_HOME pointing at tmp."""
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state_dir)
    return env


def _make_sidecar(kind="spec", name="my-spec", title="My Spec", status="draft"):
    return {
        "version": "v1",
        "kind": kind,
        "title": title,
        "keywords": ["foo"],
        "status": status,
        "team": "trailhead",
        "suite": None,
        "product": None,
        "repo": None,
        "created-at": "2026-06-17T10:00:00Z",
        "created-by": "tom@example.com",
        "updated-at": "2026-06-17T10:00:00Z",
        "updated-by": "tom@example.com",
        "last-referenced-at": None,
        "scope": None,
    }


# ---------------------------------------------------------------------------
# open_index: creates DB at the resolved state path + WAL mode
# ---------------------------------------------------------------------------

def test_open_index_creates_db_at_xdg_state_path(tmp_path):
    """open_index(env=...) creates the file at state_dir("lore")/index.sqlite."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    conn = mod.open_index(env=env)
    conn.close()

    expected_path = fake_state / "lore" / "index.sqlite"
    assert expected_path.exists(), f"Expected DB at {expected_path}"


def test_open_index_reports_wal_journal_mode(tmp_path):
    """open_index sets WAL mode; journal_mode query returns 'wal'."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    conn = mod.open_index(env=env)
    try:
        result = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()

    assert result[0] == "wal", f"Expected 'wal', got {result[0]!r}"


def test_open_index_creates_records_table(tmp_path):
    """open_index provisions the records table on first use."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    conn = mod.open_index(env=env)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='records'"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1, "Expected 'records' table to exist"


def test_open_index_wal_persists_across_reopen(tmp_path):
    """WAL mode persists after close + reopen."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    conn = mod.open_index(env=env)
    conn.close()

    conn2 = mod.open_index(env=env)
    try:
        result = conn2.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn2.close()

    assert result[0] == "wal"


# ---------------------------------------------------------------------------
# upsert_row: idempotent keyed upsert
# ---------------------------------------------------------------------------

def test_upsert_row_inserts_new_row(tmp_path):
    """upsert_row inserts a row keyed by (vault, kind, name)."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    conn = mod.open_index(env=env)
    try:
        sidecar = _make_sidecar()
        mod.upsert_row(conn, "/vault", "spec", "my-spec", sidecar, "body text")
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    finally:
        conn.close()

    assert count == 1


def test_upsert_row_idempotent_same_key_leaves_one_row(tmp_path):
    """Upserting the same (vault, kind, name) twice leaves exactly one row."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    conn = mod.open_index(env=env)
    try:
        sidecar = _make_sidecar()
        mod.upsert_row(conn, "/vault", "spec", "my-spec", sidecar, "first")
        mod.upsert_row(conn, "/vault", "spec", "my-spec", sidecar, "second")
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        # S3 moved body text into the populated record_fts table (no records.body
        # column); read it back via the rowid alias join.
        body = conn.execute(
            "SELECT record_fts.body FROM records "
            "JOIN record_fts ON record_fts.rowid = records.rowid "
            "WHERE records.vault=? AND records.kind=? AND records.name=?",
            ("/vault", "spec", "my-spec")
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 1
    assert body == "second"


def test_upsert_row_updates_scalar_in_place(tmp_path):
    """Changing a scalar in the sidecar updates the row in place."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    conn = mod.open_index(env=env)
    try:
        sidecar = _make_sidecar(status="draft")
        mod.upsert_row(conn, "/vault", "spec", "my-spec", sidecar, "body")
        sidecar2 = _make_sidecar(status="ready")
        mod.upsert_row(conn, "/vault", "spec", "my-spec", sidecar2, "body")
        conn.commit()
        status = conn.execute(
            "SELECT status FROM records WHERE vault=? AND kind=? AND name=?",
            ("/vault", "spec", "my-spec")
        ).fetchone()[0]
    finally:
        conn.close()

    assert status == "ready"


# ---------------------------------------------------------------------------
# delete_row: keyed delete + no-op on missing
# ---------------------------------------------------------------------------

def test_delete_row_removes_keyed_row(tmp_path):
    """delete_row removes the row for (vault, kind, name)."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    conn = mod.open_index(env=env)
    try:
        sidecar = _make_sidecar()
        mod.upsert_row(conn, "/vault", "spec", "my-spec", sidecar, "body")
        conn.commit()
        mod.delete_row(conn, "/vault", "spec", "my-spec")
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    finally:
        conn.close()

    assert count == 0


def test_delete_row_does_not_remove_different_key(tmp_path):
    """delete_row only removes the exact (vault, kind, name) triplet."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    conn = mod.open_index(env=env)
    try:
        sidecar = _make_sidecar()
        mod.upsert_row(conn, "/vault", "spec", "my-spec", sidecar, "body")
        mod.upsert_row(conn, "/vault", "spec", "other-spec", sidecar, "body")
        conn.commit()
        mod.delete_row(conn, "/vault", "spec", "my-spec")
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    finally:
        conn.close()

    assert count == 1


def test_delete_row_missing_key_is_noop_no_raise(tmp_path):
    """delete_row on a missing (vault, kind, name) does not raise."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    conn = mod.open_index(env=env)
    try:
        # No row inserted — delete should be a silent no-op.
        mod.delete_row(conn, "/vault", "spec", "nonexistent")
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    finally:
        conn.close()

    assert count == 0


# ---------------------------------------------------------------------------
# rebuild: fixture-vault scan + idempotency + drop-deleted rows
# ---------------------------------------------------------------------------

def _write_fixture_vault(vault_root: Path, records: list[tuple[str, str, dict, str]]):
    """Write (kind, name, sidecar, body) pairs into a vault fixture.

    Creates <vault>/<kind>/<name>.json and <vault>/<kind>/<name>.md.
    """
    for kind, name, sidecar, body in records:
        kind_dir = vault_root / kind
        kind_dir.mkdir(parents=True, exist_ok=True)
        (kind_dir / f"{name}.json").write_text(json.dumps(sidecar))
        (kind_dir / f"{name}.md").write_text(body)


def test_rebuild_produces_exactly_n_rows(tmp_path):
    """rebuild over N sidecar+body pairs produces exactly N rows."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    vault_root = tmp_path / "vault"
    records = [
        ("spec", "spec-a", _make_sidecar(kind="spec", name="spec-a", title="Spec A"), "body a"),
        ("spec", "spec-b", _make_sidecar(kind="spec", name="spec-b", title="Spec B"), "body b"),
        ("plan", "plan-x", _make_sidecar(kind="plan", name="plan-x", title="Plan X"), "body x"),
    ]
    _write_fixture_vault(vault_root, records)

    conn = mod.open_index(env=env)
    try:
        count = mod.rebuild([str(vault_root)], conn)
        conn.commit()
        row_count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    finally:
        conn.close()

    assert count == 3
    assert row_count == 3


def test_rebuild_keys_rows_correctly(tmp_path):
    """rebuild rows are keyed by (vault_path, kind, name)."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    vault_root = tmp_path / "vault"
    _write_fixture_vault(vault_root, [
        ("spec", "my-spec", _make_sidecar(kind="spec", title="My Spec"), "body"),
    ])

    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault_root)], conn)
        conn.commit()
        row = conn.execute(
            "SELECT vault, kind, name FROM records"
        ).fetchone()
    finally:
        conn.close()

    assert row[0] == str(vault_root)
    assert row[1] == "spec"
    assert row[2] == "my-spec"


def test_rebuild_idempotent(tmp_path):
    """Running rebuild twice leaves exactly the same number of rows."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    vault_root = tmp_path / "vault"
    records = [
        ("spec", "spec-a", _make_sidecar(kind="spec", title="Spec A"), "body a"),
        ("spec", "spec-b", _make_sidecar(kind="spec", title="Spec B"), "body b"),
    ]
    _write_fixture_vault(vault_root, records)

    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault_root)], conn)
        conn.commit()
        mod.rebuild([str(vault_root)], conn)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    finally:
        conn.close()

    assert count == 2


def test_rebuild_drops_since_deleted_rows(tmp_path):
    """rebuild removes rows for records that no longer exist on disk."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    vault_root = tmp_path / "vault"
    spec_dir = vault_root / "spec"
    spec_dir.mkdir(parents=True)

    sidecar = _make_sidecar(kind="spec", name="spec-a", title="Spec A")
    (spec_dir / "spec-a.json").write_text(json.dumps(sidecar))
    (spec_dir / "spec-a.md").write_text("body a")
    sidecar_b = _make_sidecar(kind="spec", name="spec-b", title="Spec B")
    (spec_dir / "spec-b.json").write_text(json.dumps(sidecar_b))
    (spec_dir / "spec-b.md").write_text("body b")

    conn = mod.open_index(env=env)
    try:
        mod.rebuild([str(vault_root)], conn)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2

        # Delete spec-b from disk.
        (spec_dir / "spec-b.json").unlink()
        (spec_dir / "spec-b.md").unlink()

        # Rebuild again — spec-b row must be gone.
        mod.rebuild([str(vault_root)], conn)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        names = [r[0] for r in conn.execute("SELECT name FROM records").fetchall()]
    finally:
        conn.close()

    assert count == 1
    assert "spec-a" in names
    assert "spec-b" not in names


def test_rebuild_spans_two_vaults_keys_by_vault(tmp_path):
    """rebuild spanning two vault roots keys each row by its respective vault."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"

    _write_fixture_vault(vault_a, [
        ("spec", "spec-in-a", _make_sidecar(kind="spec", title="Spec In A"), "body"),
    ])
    _write_fixture_vault(vault_b, [
        ("spec", "spec-in-b", _make_sidecar(kind="spec", title="Spec In B"), "body"),
    ])

    conn = mod.open_index(env=env)
    try:
        count = mod.rebuild([str(vault_a), str(vault_b)], conn)
        conn.commit()
        rows = conn.execute(
            "SELECT vault, name FROM records ORDER BY name"
        ).fetchall()
    finally:
        conn.close()

    assert count == 2
    vaults = {r[0] for r in rows}
    names = {r[1] for r in rows}
    assert str(vault_a) in vaults
    assert str(vault_b) in vaults
    assert "spec-in-a" in names
    assert "spec-in-b" in names


# ---------------------------------------------------------------------------
# rebuild: only pairs (both .json + .md) are indexed
# ---------------------------------------------------------------------------

def test_rebuild_skips_orphan_json_without_md(tmp_path):
    """rebuild ignores a .json with no matching .md — incomplete record."""
    mod = load_index_store()
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)

    vault_root = tmp_path / "vault"
    spec_dir = vault_root / "spec"
    spec_dir.mkdir(parents=True)
    sidecar = _make_sidecar(kind="spec", name="orphan")
    (spec_dir / "orphan.json").write_text(json.dumps(sidecar))
    # No orphan.md written.

    conn = mod.open_index(env=env)
    try:
        count = mod.rebuild([str(vault_root)], conn)
        conn.commit()
    finally:
        conn.close()

    assert count == 0


# ---------------------------------------------------------------------------
# lore reindex: CLI exits 0 and prints a row count
# ---------------------------------------------------------------------------

def _run_lore(args: list[str], env: dict) -> subprocess.CompletedProcess:
    """Run the lore CLI as a subprocess."""
    return subprocess.run(
        [sys.executable, str(CLI_PATH)] + args,
        capture_output=True,
        text=True,
        env=env,
    )


def test_lore_reindex_exits_0(tmp_path):
    """``lore reindex`` exits 0."""
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)
    env["LORE_VAULT"] = str(vault_root)

    result = _run_lore(["reindex"], env)
    assert result.returncode == 0, f"stderr: {result.stderr}"


def test_lore_reindex_prints_row_count_to_stdout(tmp_path):
    """``lore reindex`` prints a row count on stdout."""
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    vault_root = tmp_path / "vault"

    spec_dir = vault_root / "spec"
    spec_dir.mkdir(parents=True)
    sidecar = _make_sidecar(kind="spec", name="a-spec", title="A Spec")
    (spec_dir / "a-spec.json").write_text(json.dumps(sidecar))
    (spec_dir / "a-spec.md").write_text("spec body")

    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-config")
    env["LORE_VAULT"] = str(vault_root)

    result = _run_lore(["reindex"], env)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    stdout = result.stdout.strip()
    # stdout must contain the digit 1 (one record indexed)
    assert "1" in stdout, f"Expected count in stdout, got: {stdout!r}"


def test_lore_reindex_count_reflects_vault_records(tmp_path):
    """``lore reindex`` count matches the number of complete record pairs."""
    fake_state = tmp_path / "xdg-state"
    fake_state.mkdir()
    vault_root = tmp_path / "vault"

    for i in range(3):
        kind_dir = vault_root / "spec"
        kind_dir.mkdir(parents=True, exist_ok=True)
        sidecar = _make_sidecar(kind="spec", name=f"spec-{i}", title=f"Spec {i}")
        (kind_dir / f"spec-{i}.json").write_text(json.dumps(sidecar))
        (kind_dir / f"spec-{i}.md").write_text(f"body {i}")

    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(fake_state)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-config")
    env["LORE_VAULT"] = str(vault_root)

    result = _run_lore(["reindex"], env)
    assert result.returncode == 0
    assert "3" in result.stdout.strip()


# ---------------------------------------------------------------------------
# scan_vault / remove_vault — per-vault incremental helpers (Slice 4, S4)
# ---------------------------------------------------------------------------

def test_scan_vault_inserts_rows_for_existing_pairs(tmp_path):
    """scan_vault upserts one row per <kind>/<name>.json+.md pair; returns count."""
    mod = load_index_store()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
    (tmp_path / "xdg-state").mkdir()

    vault_root = tmp_path / "vault"
    _write_fixture_vault(vault_root, [
        ("spec", "spec-a", _make_sidecar(kind="spec", name="spec-a", title="Spec A"), "body a"),
        ("plan", "plan-x", _make_sidecar(kind="plan", name="plan-x", title="Plan X"), "body x"),
    ])

    conn = mod.open_index(env=env)
    try:
        count = mod.scan_vault(str(vault_root), conn, shared=0)
        conn.commit()
        rows = conn.execute(
            "SELECT vault, kind, name, shared FROM records ORDER BY name"
        ).fetchall()
    finally:
        conn.close()

    assert count == 2
    assert {(r[0], r[1], r[2]) for r in rows} == {
        (str(vault_root), "spec", "spec-a"),
        (str(vault_root), "plan", "plan-x"),
    }
    assert all(r[3] == 0 for r in rows)


def test_scan_vault_honors_shared_flag(tmp_path):
    """scan_vault stamps the shared flag it is passed onto every row."""
    mod = load_index_store()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
    (tmp_path / "xdg-state").mkdir()

    vault_root = tmp_path / "vault"
    _write_fixture_vault(vault_root, [
        ("spec", "spec-a", _make_sidecar(kind="spec", name="spec-a", title="Spec A"), "body a"),
    ])

    conn = mod.open_index(env=env)
    try:
        mod.scan_vault(str(vault_root), conn, shared=1)
        conn.commit()
        shared = conn.execute("SELECT shared FROM records").fetchone()[0]
    finally:
        conn.close()

    assert shared == 1


def test_scan_vault_skips_malformed_sidecar(tmp_path):
    """A malformed sidecar skips exactly that record, never aborts the scan."""
    mod = load_index_store()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
    (tmp_path / "xdg-state").mkdir()

    vault_root = tmp_path / "vault"
    _write_fixture_vault(vault_root, [
        ("spec", "good", _make_sidecar(kind="spec", name="good", title="Good"), "ok"),
    ])
    bad_dir = vault_root / "spec"
    (bad_dir / "bad.json").write_text("{not valid json")
    (bad_dir / "bad.md").write_text("body")

    conn = mod.open_index(env=env)
    try:
        count = mod.scan_vault(str(vault_root), conn, shared=0)
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    finally:
        conn.close()

    assert count == 1
    assert total == 1


def test_scan_vault_missing_root_returns_zero(tmp_path):
    """scan_vault over a non-existent root is a no-op returning 0."""
    mod = load_index_store()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
    (tmp_path / "xdg-state").mkdir()

    conn = mod.open_index(env=env)
    try:
        count = mod.scan_vault(str(tmp_path / "nope"), conn, shared=0)
    finally:
        conn.close()

    assert count == 0


def test_remove_vault_removes_only_that_vaults_rows(tmp_path):
    """remove_vault deletes all rows for one root, leaves other vaults intact."""
    mod = load_index_store()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
    (tmp_path / "xdg-state").mkdir()

    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"
    _write_fixture_vault(vault_a, [
        ("spec", "a1", _make_sidecar(kind="spec", name="a1", title="A1"), "b"),
        ("plan", "a2", _make_sidecar(kind="plan", name="a2", title="A2"), "b"),
    ])
    _write_fixture_vault(vault_b, [
        ("spec", "b1", _make_sidecar(kind="spec", name="b1", title="B1"), "b"),
    ])

    conn = mod.open_index(env=env)
    try:
        mod.scan_vault(str(vault_a), conn, shared=0)
        mod.scan_vault(str(vault_b), conn, shared=1)
        conn.commit()
        removed = mod.remove_vault(str(vault_a), conn)
        conn.commit()
        remaining = conn.execute(
            "SELECT vault FROM records"
        ).fetchall()
    finally:
        conn.close()

    assert removed == 2
    assert all(r[0] == str(vault_b) for r in remaining)
    assert len(remaining) == 1


def test_remove_vault_cleans_fts_rows(tmp_path):
    """remove_vault must not orphan record_fts rows (FTS is not FK-cascaded)."""
    mod = load_index_store()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
    (tmp_path / "xdg-state").mkdir()

    vault_root = tmp_path / "vault"
    _write_fixture_vault(vault_root, [
        ("spec", "s1", _make_sidecar(kind="spec", name="s1", title="S1"), "body one"),
        ("spec", "s2", _make_sidecar(kind="spec", name="s2", title="S2"), "body two"),
    ])

    conn = mod.open_index(env=env)
    try:
        mod.scan_vault(str(vault_root), conn, shared=0)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM record_fts").fetchone()[0] == 2
        mod.remove_vault(str(vault_root), conn)
        conn.commit()
        fts_count = conn.execute("SELECT COUNT(*) FROM record_fts").fetchone()[0]
        rec_count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    finally:
        conn.close()

    assert fts_count == 0
    assert rec_count == 0


def test_remove_vault_missing_root_returns_zero(tmp_path):
    """remove_vault for a root with no rows returns 0 (silent)."""
    mod = load_index_store()
    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
    (tmp_path / "xdg-state").mkdir()

    conn = mod.open_index(env=env)
    try:
        removed = mod.remove_vault(str(tmp_path / "nope"), conn)
    finally:
        conn.close()

    assert removed == 0
