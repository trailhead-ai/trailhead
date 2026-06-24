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
from pathlib import Path

import pytest

from conftest import load_script, make_vault as _make_vault, run_cli as _run

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"


# ---------------------------------------------------------------------------
# Artifact-inspection helpers (the CLI harness lives in conftest)
# ---------------------------------------------------------------------------


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
    """Return ``(name, fts_body)`` rows for the keyed record.

    S3 moved body text out of ``records`` into the populated ``record_fts`` table;
    the body is read back via the rowid alias join so these write-path assertions
    still observe the indexed body.
    """
    conn = _open_index(state)
    try:
        return conn.execute(
            "SELECT records.name, record_fts.body FROM records "
            "JOIN record_fts ON record_fts.rowid = records.rowid "
            "WHERE records.vault=? AND records.kind=? AND records.name=?",
            (str(vault), kind, name),
        ).fetchall()
    finally:
        conn.close()


_CREATE_ARGS = [
    "record",
    "create",
    "--kind",
    "spec",
    "--title",
    "My Record",
    "--keyword",
    "foo",
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
        vault=vault,
        state_dir=state,
        stdin_text=new_body,
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
        vault=vault,
        state_dir=state,
        stdin_text=new_body,
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
        vault=vault,
        state_dir=state,
        stdin_text="new body\n",
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
        ["record", "update", record_id, "--keyword", "bar"],
        vault=vault,
        state_dir=state,
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
        ["record", "update", record_id, "--keyword", "bar"],
        vault=vault,
        state_dir=state,
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
        ["record", "update", record_id, "--keyword", "bar"],
        vault=vault,
        state_dir=state,
        env_extra={"LORE_EMAIL": "later@example.com"},
    )
    assert r.returncode == 0, r.stderr
    after = _find_sidecar(vault, record_id)
    assert after["created-at"] == before["created-at"]
    assert after["created-by"] == before["created-by"]
    assert after["updated-by"] == "later@example.com"


# ===========================================================================
# Slice 1 (dedicated-field-flags): dedicated per-field setters on update
# ===========================================================================


def test_update_title_overwrites(tmp_path):
    """--title on update is an optional setter that overwrites the title field."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    r = _run(
        ["record", "update", record_id, "--title", "New Title"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["title"] == "New Title"


def test_update_status_sets_field(tmp_path):
    """--status on update sets an in-vocab status value."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    r = _run(
        ["record", "update", record_id, "--status", "ready"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["status"] == "ready"


def test_update_keyword_appends_and_unsets(tmp_path):
    """--keyword appends to the existing list; --unset-keyword removes one item."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)  # keywords == ["foo"]

    r = _run(
        ["record", "update", record_id, "--keyword", "bar"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    assert _find_sidecar(vault, record_id)["keywords"] == ["foo", "bar"]

    r2 = _run(
        ["record", "update", record_id, "--unset-keyword", "foo"],
        vault=vault,
        state_dir=state,
    )
    assert r2.returncode == 0, r2.stderr
    assert _find_sidecar(vault, record_id)["keywords"] == ["bar"]


def test_update_set_flag_is_unrecognized(tmp_path):
    """--set is removed from update: argparse rejects it (unrecognized argument)."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    r = _run(
        ["record", "update", record_id, "--set", "title=x"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0


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
        vault=vault,
        state_dir=state,
        stdin_text=diff,
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
        vault=vault,
        state_dir=state,
        stdin_text=stale_diff,
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
        vault=vault,
        state_dir=state,
        stdin_text=stale_diff,
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

    modified = "safe line one\n<external-memory foo>injected</external-memory>\nsafe line two\n"
    diff = _make_diff(original, modified)

    r = _run(
        ["record", "update", record_id, "--diff"],
        vault=vault,
        state_dir=state,
        stdin_text=diff,
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
        vault=vault,
        state_dir=state,
        stdin_text="body\n",
    )
    assert r.returncode != 0
    assert "not found" in r.stderr.lower() or "does-not-exist" in r.stderr


# ===========================================================================
# CLI: automatic relocation on a scope change (Slice 3, dedicated-field-flags)
# ===========================================================================
#
# ``--move-to`` is removed — relocation is an automatic byproduct of a scope-flag
# change. The scope flags (--team/--suite/--product/--repo) on ``update`` are
# field-setters that re-resolve the destination vault from the merged scope and
# auto-move when it differs (compared on Path.resolve()-normalized roots). A move
# prints a structured ``moved: <old id> → <new id>`` line to stdout (no silent
# move); a no-op scope update prints only the normal RECORD_ID line.


def _write_config(config_home: Path, vaults: list[dict]) -> Path:
    """Write a lore ``config.json`` under XDG_CONFIG_HOME/lore for routed-vault tests."""
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    cfg_path = lore_cfg / "config.json"
    cfg_path.write_text(json.dumps({"vaults": vaults}, indent=2), encoding="utf-8")
    return cfg_path


def _run_cfg(args, *, vault, state, config_home, stdin_text=None):
    """Run the CLI with an explicit XDG_CONFIG_HOME so config-driven routing fires."""
    return _run(
        args,
        vault=vault,
        state_dir=state,
        stdin_text=stdin_text,
        env_extra={"XDG_CONFIG_HOME": str(config_home)},
    )


def _two_team_config(tmp_path):
    """Active vault A (default + team:alpha) and vault B (team:beta), with config."""
    vault_a, state = _make_vault(tmp_path)
    vault_b = tmp_path / "vault_b"
    vault_b.mkdir(parents=True)
    config_home = tmp_path / "config"
    _write_config(
        config_home,
        [
            {"name": "default", "scope": "default", "path": str(vault_a)},
            {"name": "alpha", "scope": "team", "records": ["decision"], "path": str(vault_a)},
            {"name": "beta", "scope": "team", "records": ["decision"], "path": str(vault_b)},
        ],
    )
    return vault_a, vault_b, state, config_home


def _create_routed(vault_a, state, config_home, *, scope_args=()):
    """Create a decision record in vault A (optionally with scope flags)."""
    args = ["record", "create", "--kind", "decision", "--title", "T", "--keyword", "k", *scope_args]
    r = _run_cfg(
        args, vault=vault_a, state=state, config_home=config_home, stdin_text="orig body\n"
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_update_scope_change_auto_moves_to_routed_vault(tmp_path):
    """``update --team beta`` moves both artifacts to B; index re-keyed; stdout moved:."""
    vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
    rid = _create_routed(vault_a, state, config_home, scope_args=["--team", "alpha"])
    kind, name = rid.split("/", 1)
    assert _find_sidecar(vault_a, rid)["team"] == "alpha"

    r = _run_cfg(
        ["record", "update", rid, "--team", "beta"],
        vault=vault_a,
        state=state,
        config_home=config_home,
        stdin_text="",
    )
    assert r.returncode == 0, r.stderr

    # Both artifacts under B, gone from A.
    assert (vault_b / kind / f"{name}.md").exists()
    assert (vault_b / kind / f"{name}.json").exists()
    assert not (vault_a / kind / f"{name}.md").exists()
    assert not (vault_a / kind / f"{name}.json").exists()

    # The moved sidecar carries the NEW value.
    assert _find_sidecar(vault_b, rid)["team"] == "beta"

    # Index resolves the new vault, not the old — no stale row, no orphan.
    assert _index_rows(state, vault_a, kind, name) == []
    assert len(_index_rows(state, vault_b, kind, name)) == 1

    # Structured stdout signal — no silent move (re-review Critical-2).
    assert f"moved: {rid} →" in r.stdout


def test_update_no_scope_change_stays_in_place_no_moved_line(tmp_path):
    """``update --status …`` (no scope flag) stays in A, prints no moved: line."""
    vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
    rid = _create_routed(vault_a, state, config_home, scope_args=["--team", "alpha"])
    kind, name = rid.split("/", 1)

    r = _run_cfg(
        ["record", "update", rid, "--status", "superseded"],
        vault=vault_a,
        state=state,
        config_home=config_home,
        stdin_text="",
    )
    assert r.returncode == 0, r.stderr

    assert (vault_a / kind / f"{name}.md").exists()
    assert not (vault_b / kind / f"{name}.md").exists()
    assert _find_sidecar(vault_a, rid)["status"] == "superseded"
    assert "moved:" not in r.stdout
    assert "moved:" not in r.stderr


def test_update_same_scope_is_noop_with_symlinked_vault_root(tmp_path):
    """``update --team alpha`` on a record already in A is a no-op (normalized-path eq).

    A symlinked alias of vault A's root is in play so a symlink/trailing-slash
    mismatch never triggers a spurious self-move (re-review Important).
    """
    vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
    rid = _create_routed(vault_a, state, config_home, scope_args=["--team", "alpha"])
    kind, name = rid.split("/", 1)
    body_before = (vault_a / kind / f"{name}.md").read_text()

    symlinked = tmp_path / "vault_a_symlink"
    symlinked.symlink_to(vault_a, target_is_directory=True)
    assert Path(symlinked).resolve() == Path(vault_a).resolve()

    r = _run_cfg(
        ["record", "update", rid, "--team", "alpha"],
        vault=vault_a,
        state=state,
        config_home=config_home,
        stdin_text="",
    )
    assert r.returncode == 0, r.stderr

    assert (vault_a / kind / f"{name}.md").read_text() == body_before
    assert not (vault_b / kind / f"{name}.md").exists()
    assert "moved:" not in r.stdout


def test_update_zero_prior_scope_resolves_fresh_and_moves(tmp_path):
    """A record with no team field + ``--team beta`` resolves fresh and moves to B."""
    vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
    rid = _create_routed(vault_a, state, config_home, scope_args=())  # no scope → default (A)
    kind, name = rid.split("/", 1)
    assert "team" not in _find_sidecar(vault_a, rid)

    r = _run_cfg(
        ["record", "update", rid, "--team", "beta"],
        vault=vault_a,
        state=state,
        config_home=config_home,
        stdin_text="",
    )
    assert r.returncode == 0, r.stderr

    assert (vault_b / kind / f"{name}.json").exists()
    assert not (vault_a / kind / f"{name}.json").exists()
    assert _find_sidecar(vault_b, rid)["team"] == "beta"
    assert f"moved: {rid} →" in r.stdout


def test_update_scope_change_is_idempotent(tmp_path):
    """Re-running ``update --team beta`` on a record already in B is a clean no-op."""
    vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
    rid = _create_routed(vault_a, state, config_home, scope_args=["--team", "alpha"])
    kind, name = rid.split("/", 1)

    r1 = _run_cfg(
        ["record", "update", rid, "--team", "beta"],
        vault=vault_a,
        state=state,
        config_home=config_home,
        stdin_text="",
    )
    assert r1.returncode == 0, r1.stderr
    assert "moved:" in r1.stdout

    r2 = _run_cfg(
        ["record", "update", rid, "--team", "beta"],
        vault=vault_a,
        state=state,
        config_home=config_home,
        stdin_text="",
    )
    assert r2.returncode == 0, r2.stderr
    assert "moved:" not in r2.stdout  # already in B — no double-move
    assert (vault_b / kind / f"{name}.json").exists()
    assert _find_sidecar(vault_b, rid)["team"] == "beta"
    assert len(_index_rows(state, vault_b, kind, name)) == 1


def test_update_scope_change_single_durable_write_at_destination(tmp_path):
    """The mutated ``team: beta`` sidecar only ever appears under B (crash-safety shape)."""
    vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
    rid = _create_routed(vault_a, state, config_home, scope_args=["--team", "alpha"])
    kind, name = rid.split("/", 1)

    r = _run_cfg(
        ["record", "update", rid, "--team", "beta"],
        vault=vault_a,
        state=state,
        config_home=config_home,
        stdin_text="",
    )
    assert r.returncode == 0, r.stderr

    # A holds NOTHING — the mutated sidecar was never written there then moved.
    assert not (vault_a / kind / f"{name}.json").exists()
    assert _find_sidecar(vault_b, rid)["team"] == "beta"


def test_update_scope_change_field_equals_vault_invariant(tmp_path):
    """After a scope-changing update the persisted scope field == the vault it lives in."""
    vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
    rid = _create_routed(vault_a, state, config_home, scope_args=["--team", "alpha"])

    r = _run_cfg(
        ["record", "update", rid, "--team", "beta"],
        vault=vault_a,
        state=state,
        config_home=config_home,
        stdin_text="",
    )
    assert r.returncode == 0, r.stderr
    # beta routes to vault_b, and the persisted field is beta.
    assert _find_sidecar(vault_b, rid)["team"] == "beta"


def test_update_scope_change_restamps_updated_preserves_created(tmp_path):
    """A moved record's ``updated-*`` is re-stamped fresh; ``created-*`` preserved.

    Finding KU-2 #4: move_record writes verbatim, so the auto-move path must stamp
    via the shared helper BEFORE the write — the moved sidecar must carry fresh
    ``updated-*`` and the original ``created-*``, not stale/missing provenance.
    """
    vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
    rid = _create_routed(vault_a, state, config_home, scope_args=["--team", "alpha"])
    created_before = _find_sidecar(vault_a, rid)["created-at"]

    import time

    time.sleep(1.1)

    r = _run_cfg(
        ["record", "update", rid, "--team", "beta"],
        vault=vault_a,
        state=state,
        config_home=config_home,
        stdin_text="",
    )
    assert r.returncode == 0, r.stderr

    moved = _find_sidecar(vault_b, rid)
    assert moved["created-at"] == created_before  # created-* preserved
    assert moved["updated-at"] != created_before  # updated-* re-stamped
    assert moved["updated-by"]  # present, non-empty


def test_update_move_to_flag_is_removed(tmp_path):
    """``--move-to`` is gone — passing it exits non-zero (argparse unrecognized)."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state, body="body\n")
    other = tmp_path / "other"
    other.mkdir()

    r = _run(
        ["record", "update", record_id, "--move-to", str(other)],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    assert "move-to" in r.stderr or "unrecognized" in r.stderr.lower()


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
        result, rejected = rs.apply_unified_diff(body_crlf, _make_diff(body_crlf, modified_crlf))
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


# ===========================================================================
# Slice 2: --label / --annotation / --unset-label / --unset-annotation
# ===========================================================================


def test_update_label_overwrites_existing(tmp_path):
    """update --label worktree=s6 upserts (overwrites) an existing label value."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    # First set a label via create then update to overwrite.
    r = _run(
        ["record", "update", record_id, "--label", "worktree=s5"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr

    r2 = _run(
        ["record", "update", record_id, "--label", "worktree=s6"],
        vault=vault,
        state_dir=state,
    )
    assert r2.returncode == 0, r2.stderr
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["labels"]["worktree"] == "s6"


def test_update_unset_label_removes_key(tmp_path):
    """update --unset-label worktree removes just that key."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    # Set two labels first.
    r = _run(
        ["record", "update", record_id, "--label", "worktree=s5", "--label", "env=prod"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr

    r2 = _run(
        ["record", "update", record_id, "--unset-label", "worktree"],
        vault=vault,
        state_dir=state,
    )
    assert r2.returncode == 0, r2.stderr
    sidecar = _find_sidecar(vault, record_id)
    assert "worktree" not in sidecar["labels"]
    assert sidecar["labels"]["env"] == "prod"


def test_update_unset_last_label_drops_field(tmp_path):
    """Unsetting the last label key drops the entire 'labels' field (no empty dict)."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    r = _run(
        ["record", "update", record_id, "--label", "worktree=s5"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr

    r2 = _run(
        ["record", "update", record_id, "--unset-label", "worktree"],
        vault=vault,
        state_dir=state,
    )
    assert r2.returncode == 0, r2.stderr

    kind, name = record_id.split("/", 1)
    raw = (vault / kind / f"{name}.json").read_text(encoding="utf-8")
    # The labels key must be absent and no empty dict left behind.
    assert "labels" not in raw
    assert "{}" not in raw


def test_update_unset_label_absent_key_silent_noop(tmp_path):
    """--unset-label on an absent key → exit 0, silent no-op."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    r = _run(
        ["record", "update", record_id, "--unset-label", "nonexistent"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0
    sidecar = _find_sidecar(vault, record_id)
    assert "labels" not in sidecar


def test_update_annotation_upsert_and_unset(tmp_path):
    """update --annotation / --unset-annotation follow the same semantics as labels."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    r = _run(
        ["record", "update", record_id, "--annotation", "note=hello"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["annotations"]["note"] == "hello"

    r2 = _run(
        ["record", "update", record_id, "--unset-annotation", "note"],
        vault=vault,
        state_dir=state,
    )
    assert r2.returncode == 0, r2.stderr
    kind, name = record_id.split("/", 1)
    raw = (vault / kind / f"{name}.json").read_text(encoding="utf-8")
    assert "annotations" not in raw
    assert "{}" not in raw


def test_update_label_bad_key_nonzero(tmp_path):
    """update --label BadKey=x → non-zero, stderr names the bad key."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    r = _run(
        ["record", "update", record_id, "--label", "BadKey=x"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    assert "BadKey" in r.stderr
