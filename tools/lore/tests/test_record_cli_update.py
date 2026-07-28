"""Tests for the ``lore record update`` CLI + the unified-diff applier.

Covers the test contract:

  - full-body replace (piped stdin replaces the whole body).
  - metadata-only (no stdin) leaves the body byte-identical; ``updated-*``
    advances while ``created-*`` stays stable; prints the
    ``no stdin`` notice to stderr at exit 0.
  - ``--diff`` clean apply updates the body + index.
  - ``--diff`` stale hunk → non-zero, body byte-for-byte unchanged, no index
    update, parseable rejected-hunk line on stderr.
  - ``--diff`` hunk inserting ``<external-memory>`` → stored body has the fence
    neutralized (the diff path is not a neutralization bypass).
  - invalid RECORD_ID → non-zero.
  - vault-move via ``move_record`` (two injected vault roots): new ID returned,
    artifacts under the new vault, old copy gone, index re-keyed; a
    crash-simulated move + ``reindex`` leaves exactly the new copy.

Plus direct unit tests for ``record_store.apply_unified_diff`` over the three
adversarial cases (CRLF, trailing-newline, adjacent hunks) with byte-for-byte
``==`` assertions on every reject path.

CLI tests run the lore CLI as a subprocess via CLI_PATH (conftest pattern). Never
writes to the real vault: the CLI resolves the test vault from a seeded
config.json (isolated XDG_CONFIG_HOME) and XDG_STATE_HOME is fenced too.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

import pytest

from conftest import load_script, make_vault as _make_vault, run_cli as _run, write_default_config  # noqa: F401


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
    mod = load_script("lore.search.index")
    return mod.open_index(env={"XDG_STATE_HOME": str(state)})


def _index_rows(state: Path, vault: Path, kind: str, name: str) -> list:
    """Return ``(name, fts_body)`` rows for the keyed record.

    The body text lives outside ``records`` in the populated ``record_fts`` table;
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
# CLI: full-body replace
# ===========================================================================


def test_update_full_body_replaces_body(tmp_path):
    """Piped stdin replaces the full body by default."""
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
# CLI: metadata-only (no stdin) + stderr notice
# ===========================================================================


def test_update_metadata_only_leaves_body_byte_identical(tmp_path):
    """No stdin → body unchanged; only sidecar params applied."""
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
    """No stdin → the metadata-only notice goes to stderr; exit stays 0."""
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
# dedicated per-field setters on update
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
# CLI: --diff clean apply
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
# CLI: --diff stale hunk → atomic reject
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


def test_update_diff_bare_hunk_header_rejects_not_silent_noop(tmp_path):
    """A bare ``@@`` hunk header must fail loudly, never a silent no-op success."""
    vault, state = _make_vault(tmp_path)
    original = "line one\nline two\nline three\n"
    record_id = _create(vault, state, body=original)
    kind, name = record_id.split("/", 1)

    # No line ranges on the hunk header — previously parsed to zero hunks and
    # silently "succeeded" with the body untouched.
    bare_diff = "@@ @@\n-line two\n+line TWO\n"

    r = _run(
        ["record", "update", record_id, "--diff"],
        vault=vault,
        state_dir=state,
        stdin_text=bare_diff,
    )
    assert r.returncode != 0
    assert "unparseable diff" in r.stderr
    # Body byte-for-byte unchanged; no index churn — same invariant as a reject.
    assert _find_body(vault, record_id) == original
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
# CLI: --diff is not a fence-neutralization bypass
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
# CLI: invalid RECORD_ID
# ===========================================================================


def test_update_invalid_record_id_nonzero(tmp_path):
    """A nonexistent RECORD_ID → non-zero exit."""
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
# CLI: automatic relocation on a scope change
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

    # Structured stdout signal — no silent move.
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
    mismatch never triggers a spurious self-move.
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


def test_update_scope_change_rejects_symlinked_dest_kind_dir(tmp_path):
    """A move destination whose ``kind`` dir is symlinked outside the dest vault
    is rejected — mirroring the confinement guard applied to ``--parent``/
    ``--depends-on`` edge values, now also applied to the move destination.
    """
    vault_a, vault_b, state, config_home = _two_team_config(tmp_path)
    rid = _create_routed(vault_a, state, config_home, scope_args=["--team", "alpha"])
    kind, name = rid.split("/", 1)
    body_before = (vault_a / kind / f"{name}.md").read_text()

    outside = tmp_path / "outside"
    outside.mkdir()
    (vault_b / kind).symlink_to(outside, target_is_directory=True)

    r = _run_cfg(
        ["record", "update", rid, "--team", "beta"],
        vault=vault_a,
        state=state,
        config_home=config_home,
        stdin_text="",
    )
    assert r.returncode != 0
    assert "error:" in r.stderr

    # Nothing written through the symlink escape; the record never moved.
    assert not (outside / f"{name}.md").exists()
    assert not (outside / f"{name}.json").exists()
    assert (vault_a / kind / f"{name}.md").read_text() == body_before


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

    move_record writes verbatim, so the auto-move path must stamp
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
# CLI: --vault explicit current-location targeting (U1 contingency)
# ===========================================================================
#
# ``--vault NAME`` resolves the record's CURRENT location in exactly the named
# configured vault (via ``vault_config.load_config``), instead of
# ``_find_current_record_location``'s config-order first-match scan. This is
# distinct from the destination re-routing flags (--repo/--product/--suite/
# --team), which keep their existing meaning and can be combined with --vault.


def _duplicate_named_task_two_vaults(tmp_path, title="Dup Task"):
    """Two team vaults (config order alpha, beta), each holding an
    independently-created task record of the same name — the collision case
    ``_find_current_record_location``'s scan cannot disambiguate.
    """
    default_vault, state = _make_vault(tmp_path)
    alpha_vault = tmp_path / "vault_alpha"
    beta_vault = tmp_path / "vault_beta"
    alpha_vault.mkdir(parents=True)
    beta_vault.mkdir(parents=True)
    config_home = tmp_path / "config"
    _write_config(
        config_home,
        [
            {"name": "default", "scope": "default", "path": str(default_vault)},
            {"name": "alpha", "scope": "team", "path": str(alpha_vault)},
            {"name": "beta", "scope": "team", "path": str(beta_vault)},
        ],
    )
    for team, vault in (("alpha", alpha_vault), ("beta", beta_vault)):
        r = _run_cfg(
            [
                "record", "create", "--kind", "task", "--title", title,
                "--team", team, "--status", "open",
            ],
            vault=default_vault, state=state, config_home=config_home, stdin_text="",
        )
        assert r.returncode == 0, r.stderr
    return default_vault, alpha_vault, beta_vault, state, config_home


def test_vault_flag_targets_named_vault_ignoring_config_order(tmp_path):
    """``update --vault beta`` changes beta's record only; alpha (config-order-
    first) is left untouched — the exact collision the prover reproduced."""
    default_vault, alpha_vault, beta_vault, state, config_home = (
        _duplicate_named_task_two_vaults(tmp_path)
    )
    record_id = "task/dup-task"

    r = _run_cfg(
        ["record", "update", record_id, "--vault", "beta", "--status", "blocked"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode == 0, r.stderr

    assert _find_sidecar(beta_vault, record_id)["status"] == "blocked"
    assert _find_sidecar(alpha_vault, record_id)["status"] == "open"


def test_vault_flag_record_absent_in_named_vault_errors_without_scan_fallback(tmp_path):
    """``--vault`` naming a vault that lacks the record errors plainly — it never
    falls back to scanning the other configured vaults, and nothing is written."""
    default_vault, state = _make_vault(tmp_path)
    alpha_vault = tmp_path / "vault_alpha"
    beta_vault = tmp_path / "vault_beta"
    alpha_vault.mkdir(parents=True)
    beta_vault.mkdir(parents=True)
    config_home = tmp_path / "config"
    _write_config(
        config_home,
        [
            {"name": "default", "scope": "default", "path": str(default_vault)},
            {"name": "alpha", "scope": "team", "path": str(alpha_vault)},
            {"name": "beta", "scope": "team", "path": str(beta_vault)},
        ],
    )
    r = _run_cfg(
        [
            "record", "create", "--kind", "task", "--title", "Solo",
            "--team", "alpha", "--status", "open",
        ],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode == 0, r.stderr
    record_id = "task/solo"

    r = _run_cfg(
        ["record", "update", record_id, "--vault", "beta", "--status", "blocked"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode != 0
    assert r.stderr.startswith("lore: ")

    # Untouched -- no fallback write to alpha (where the record actually lives).
    assert _find_sidecar(alpha_vault, record_id)["status"] == "open"
    # Nothing written into beta either.
    assert not (beta_vault / "task" / "solo.json").exists()
    assert not (beta_vault / "task" / "solo.md").exists()


def test_vault_flag_unknown_name_errors(tmp_path):
    """An unconfigured ``--vault`` name errors with ``lore: <msg>`` — nonzero."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state, body="body\n")

    r = _run(
        ["record", "update", record_id, "--vault", "nope", "--status", "blocked"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    assert r.stderr.startswith("lore: ")
    assert "nope" in r.stderr


def test_vault_flag_composes_with_diff_and_labels(tmp_path):
    """``--vault`` combined with ``--diff`` (body) and ``--label`` (map field)
    both apply against the named vault's record."""
    default_vault, alpha_vault, beta_vault, state, config_home = (
        _duplicate_named_task_two_vaults(tmp_path)
    )
    record_id = "task/dup-task"

    diff = _make_diff("", "beta body\n")
    r = _run_cfg(
        [
            "record", "update", record_id, "--vault", "beta", "--diff",
            "--label", "priority=high",
        ],
        vault=default_vault, state=state, config_home=config_home, stdin_text=diff,
    )
    assert r.returncode == 0, r.stderr

    assert _find_body(beta_vault, record_id) == "beta body\n"
    assert _find_sidecar(beta_vault, record_id)["labels"]["priority"] == "high"
    # alpha's copy is untouched by either the body or the label change.
    assert _find_body(alpha_vault, record_id) == ""
    assert "labels" not in _find_sidecar(alpha_vault, record_id)


def test_vault_flag_composes_with_team_destination_rerouting(tmp_path):
    """``--vault`` (current-location) composes with ``--team`` (destination
    re-routing): the record located via --vault in alpha auto-moves to the
    vault --team elects, exactly as an update without --vault would."""
    default_vault, alpha_vault, beta_vault, state, config_home = (
        _duplicate_named_task_two_vaults(tmp_path)
    )
    gamma_vault = tmp_path / "vault_gamma"
    gamma_vault.mkdir(parents=True)
    config_path = config_home / "lore" / "config.json"
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    cfg["vaults"].append({"name": "gamma", "scope": "team", "path": str(gamma_vault)})
    config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    record_id = "task/dup-task"

    r = _run_cfg(
        ["record", "update", record_id, "--vault", "alpha", "--team", "gamma"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode == 0, r.stderr
    assert "moved:" in r.stdout

    # alpha's ORIGINAL record is gone (moved away) to gamma; beta's separate
    # duplicate is untouched throughout.
    assert not (alpha_vault / "task" / "dup-task.json").exists()
    assert _find_sidecar(gamma_vault, record_id)["team"] == "gamma"
    assert _find_sidecar(beta_vault, record_id)["team"] == "beta"


def test_vault_flag_omitted_preserves_scan_behavior(tmp_path):
    """Omitting ``--vault`` still scans config order and updates the first
    match — unchanged from pre-existing behavior."""
    default_vault, alpha_vault, beta_vault, state, config_home = (
        _duplicate_named_task_two_vaults(tmp_path)
    )
    record_id = "task/dup-task"

    r = _run_cfg(
        ["record", "update", record_id, "--status", "blocked"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode == 0, r.stderr

    # Config order is default, alpha, beta -- alpha is the first vault (after
    # the default floor) holding a "dup-task" record, so the scan hits it.
    assert _find_sidecar(alpha_vault, record_id)["status"] == "blocked"
    assert _find_sidecar(beta_vault, record_id)["status"] == "open"


# ---------------------------------------------------------------------------
# --vault + scope-less sidecar: destination is the named vault, no move
# ---------------------------------------------------------------------------
#
# A record whose sidecar carries no repo/product/suite/team field (e.g. a
# legacy or externally-authored record) re-resolves its destination from an
# EMPTY merged scope, which always lands on the default vault (see
# vault/resolve.py's totality floor). Without a fix, ``--vault <elected>
# --status ready`` on such a record would silently MOVE it out of the elected
# vault into the default vault, even though no destination scope flag was
# given. These tests pin: when --vault is passed and no explicit destination
# scope flag (--repo/--product/--suite/--team) accompanies it, the
# destination is the named vault itself -- no re-resolution, no move.


def _elected_vault_config(tmp_path):
    """A default vault and a separately-rooted ``elected`` team vault."""
    default_vault, state = _make_vault(tmp_path)
    elected_vault = tmp_path / "vault_elected"
    elected_vault.mkdir(parents=True)
    config_home = tmp_path / "config"
    _write_config(
        config_home,
        [
            {"name": "default", "scope": "default", "path": str(default_vault)},
            {"name": "elected", "scope": "team", "path": str(elected_vault)},
        ],
    )
    return default_vault, elected_vault, state, config_home


def _create_scopeless_in_elected(default_vault, elected_vault, state, config_home, *, body="orig body\n"):
    """Create a task physically in ``elected_vault``, then strip its scope field.

    Routes the create with ``--team elected`` (the only way to place it there),
    then rewrites the on-disk sidecar to drop the ``team`` key -- simulating a
    record whose sidecar was never scope-stamped (the exact shape the finding
    describes), without which the CLI's own create routing would never produce
    a scope-less record outside the default vault.
    """
    r = _run_cfg(
        ["record", "create", "--kind", "task", "--title", "Elected Task",
         "--team", "elected", "--status", "open"],
        vault=default_vault, state=state, config_home=config_home, stdin_text=body,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    kind, name = record_id.split("/", 1)
    sidecar_path = elected_vault / kind / f"{name}.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    del sidecar["team"]
    sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return record_id


def test_vault_flag_scopeless_sidecar_status_update_stays_no_move(tmp_path):
    """``--vault elected --status ready`` on a scope-less record stays in elected."""
    default_vault, elected_vault, state, config_home = _elected_vault_config(tmp_path)
    record_id = _create_scopeless_in_elected(default_vault, elected_vault, state, config_home)
    kind, name = record_id.split("/", 1)
    assert "team" not in _find_sidecar(elected_vault, record_id)

    r = _run_cfg(
        ["record", "update", record_id, "--vault", "elected", "--status", "ready"],
        vault=default_vault, state=state, config_home=config_home, stdin_text="",
    )
    assert r.returncode == 0, r.stderr

    assert _find_sidecar(elected_vault, record_id)["status"] == "ready"
    assert not (default_vault / kind / f"{name}.json").exists()
    assert "moved:" not in r.stdout


def test_vault_flag_scopeless_sidecar_diff_body_stays_no_move(tmp_path):
    """Same, with a ``--diff`` body update instead of a metadata-only one."""
    default_vault, elected_vault, state, config_home = _elected_vault_config(tmp_path)
    record_id = _create_scopeless_in_elected(
        default_vault, elected_vault, state, config_home, body="orig body\n"
    )
    kind, name = record_id.split("/", 1)

    diff = _make_diff("orig body\n", "elected body\n")
    r = _run_cfg(
        ["record", "update", record_id, "--vault", "elected", "--diff"],
        vault=default_vault, state=state, config_home=config_home, stdin_text=diff,
    )
    assert r.returncode == 0, r.stderr

    assert _find_body(elected_vault, record_id) == "elected body\n"
    assert not (default_vault / kind / f"{name}.json").exists()
    assert "moved:" not in r.stdout


# ===========================================================================
# Unit tests for apply_unified_diff — adversarial cases
# ===========================================================================


@pytest.fixture
def rs():
    return load_script("lore.record.store")


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
    """CRLF body vs LF diff context — verbatim compare must reject."""

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
    """Trailing-newline mismatch — reject, body unchanged."""

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


class TestApplierBareHunkHeader:
    """A hunk header lacking line ranges must raise, never silently no-op."""

    def test_bare_at_at_header_is_format_error(self, rs):
        body = "line one\nline two\nline three\n"
        # No "-start,count +start,count" — the exact shape reported in the bug.
        bare_diff = "@@ @@\n-line two\n+line TWO\n"
        with pytest.raises(rs.DiffFormatError):
            rs.apply_unified_diff(body, bare_diff)

    def test_bare_header_leaves_body_unchanged_when_caught(self, rs):
        body = "line one\nline two\nline three\n"
        bare_diff = "@@ @@\n-line two\n+line TWO\n"
        try:
            rs.apply_unified_diff(body, bare_diff)
        except rs.DiffFormatError:
            pass
        # The applier never had a chance to mutate the caller's body — it isn't
        # mutated in place — but assert byte-for-byte identity for good measure.
        assert body == "line one\nline two\nline three\n"

    def test_valid_hunk_after_bare_hunk_still_raises(self, rs):
        """A well-formed hunk later in the diff must not mask the earlier bare one."""
        body = "line one\nline two\nline three\n"
        mixed_diff = "@@ @@\n-line one\n+line ONE\n@@ -3,1 +3,1 @@\n-line three\n+line THREE\n"
        with pytest.raises(rs.DiffFormatError):
            rs.apply_unified_diff(body, mixed_diff)


class TestApplierAdjacentHunks:
    """Adjacent hunks — offset tracking + atomic reject."""

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
# --label / --annotation / --unset-label / --unset-annotation
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


def test_update_label_reserved_key_nonzero(tmp_path):
    """update --label kind=x → non-zero; a label may not shadow a record kind."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    r = _run(
        ["record", "update", record_id, "--label", "kind=x"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    assert "kind" in r.stderr


def test_update_label_namespaced_reserved_name_succeeds(tmp_path):
    """update --label craft/subsystems=x → accepted; namespacing is the escape route."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)

    r = _run(
        ["record", "update", record_id, "--label", "craft/subsystems=pr-dashboard"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["labels"]["craft/subsystems"] == "pr-dashboard"


def _inject_label(vault: Path, record_id: str, key: str, value: str) -> None:
    """Write a labels key straight into the sidecar, bypassing the CLI.

    The validator refuses to produce this state through ``record create``, so a
    record predating a name's reservation has to be manufactured on disk. Only
    ever the ``make_vault`` test vault — never a live one.
    """
    kind, name = record_id.split("/", 1)
    path = vault / kind / f"{name}.json"
    sidecar = json.loads(path.read_text(encoding="utf-8"))
    sidecar.setdefault("labels", {})[key] = value
    path.write_text(json.dumps(sidecar), encoding="utf-8")


def test_update_unset_label_clears_a_reserved_key_the_record_already_carries(tmp_path):
    """The escape the refusal names: a record already holding a reserved key can
    drop it in one metadata-only update, leaving the body byte-identical."""
    vault, state = _make_vault(tmp_path)
    body = "original line one\noriginal line two\n"
    record_id = _create(vault, state, body=body)
    _inject_label(vault, record_id, "area", "home-manager")

    r = _run(
        ["record", "update", record_id, "--unset-label", "area"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    assert "area" not in _find_sidecar(vault, record_id).get("labels", {})
    assert _find_body(vault, record_id) == body


def test_update_label_refusal_stderr_names_the_unset_escape(tmp_path):
    """End to end: the refusal an agent reads carries the command that clears it,
    and the refused write leaves the stored label untouched."""
    vault, state = _make_vault(tmp_path)
    record_id = _create(vault, state)
    _inject_label(vault, record_id, "area", "home-manager")

    r = _run(
        ["record", "update", record_id, "--label", "area=elsewhere"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0, r.stdout
    assert "--unset-label area" in r.stderr
    assert _find_sidecar(vault, record_id)["labels"] == {"area": "home-manager"}


# ===========================================================================
# Group-default scope routing is create-only: update must NOT seed scopes from
# a camp group, so an unscoped update inside a bound workspace never relocates a
# record to the group's vault.
# ===========================================================================


def _write_routing_config(config_home, *, default_vault, trailhead_vault):
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    (lore_cfg / "config.json").write_text(
        json.dumps(
            {
                "vaults": [
                    {"name": "default", "scope": "default", "path": str(default_vault)},
                    {"name": "trailhead", "scope": "product", "path": str(trailhead_vault)},
                ]
            }
        ),
        encoding="utf-8",
    )


def _write_group_binding(groups_dir, *, member_repo):
    groups_dir.mkdir(parents=True, exist_ok=True)
    (groups_dir / "trailhead.toml").write_text(
        '[group]\nname = "trailhead"\n\n'
        f'[[members]]\nname = "repo"\nrepo_root = "{member_repo}"\n\n'
        '[[lore_scopes]]\nscope = "product"\nname = "trailhead"\n',
        encoding="utf-8",
    )


def test_update_inside_group_does_not_relocate_record(tmp_path):
    """An unscoped update run from inside a bound workspace leaves a default-vault
    record in the default vault — group-default seeding is create-only, so update
    never re-routes the record to the group's vault.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    groups_dir = tmp_path / "groups"
    member_repo = tmp_path / "repo"
    member_repo.mkdir()
    trailhead_vault = tmp_path / "trailhead_vault"
    trailhead_vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    _write_routing_config(config_home, default_vault=vault, trailhead_vault=trailhead_vault)
    _write_group_binding(groups_dir, member_repo=member_repo)
    env = {"XDG_CONFIG_HOME": str(config_home), "LORE_GROUPS_DIR": str(groups_dir)}

    # Create from outside any group → record lands in the default vault.
    c = _run(
        ["record", "create", "--kind", "spec", "--title", "T", "--keyword", "foo"],
        vault=vault,
        state_dir=state,
        env_extra=env,
        cwd=outside,
        stdin_text="original\n",
    )
    assert c.returncode == 0, c.stderr
    record_id = c.stdout.strip()
    kind, name = record_id.split("/", 1)
    assert (vault / kind / f"{name}.md").exists()

    # Update with NO scope flags, from inside the bound workspace.
    u = _run(
        ["record", "update", record_id],
        vault=vault,
        state_dir=state,
        env_extra=env,
        cwd=member_repo,
        stdin_text="updated body\n",
    )
    assert u.returncode == 0, u.stderr
    # The record stays in the default vault — it was NOT relocated to trailhead.
    assert (vault / kind / f"{name}.md").exists()
    assert not (trailhead_vault / kind / f"{name}.md").exists()
    assert _find_body(vault, record_id) == "updated body\n"


# ===========================================================================
# Task graph guards on update / delete
# ===========================================================================


def _mk_task(vault, state, title, *, extra=None, body="body\n"):
    """Create a ``task`` record and return its RECORD_ID."""
    args = ["record", "create", "--kind", "task", "--title", title]
    if extra:
        args += extra
    r = _run(args, vault=vault, state_dir=state, stdin_text=body)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_unset_depends_on_and_parent_round_trip(tmp_path):
    """--unset-depends-on removes one dep; --unset-parent clears the parent scalar."""
    vault, state = _make_vault(tmp_path)
    rid = _mk_task(vault, state, "a", extra=["--depends-on", "x", "--depends-on", "y", "--parent", "p"])
    before = _find_sidecar(vault, rid)
    assert before["depends-on"] == ["x", "y"]
    assert before["parent"] == "p"

    u = _run(
        ["record", "update", rid, "--unset-depends-on", "x", "--unset-parent"],
        vault=vault,
        state_dir=state,
    )
    assert u.returncode == 0, u.stderr
    after = _find_sidecar(vault, rid)
    assert after["depends-on"] == ["y"]
    assert "parent" not in after


def test_depends_on_cycle_rejected_on_update(tmp_path):
    """update introducing A→B→A is rejected and the record is left unchanged."""
    vault, state = _make_vault(tmp_path)
    a = _mk_task(vault, state, "a")
    _mk_task(vault, state, "b", extra=["--depends-on", "a"])
    # a depends-on b would close the cycle a→b→a.
    u = _run(["record", "update", a, "--depends-on", "b"], vault=vault, state_dir=state)
    assert u.returncode != 0
    assert "graph-guard [depends-on-cycle]" in u.stderr
    # a's sidecar unchanged — no depends-on written.
    assert "depends-on" not in _find_sidecar(vault, a)


def test_deep_ancestor_loop_rejected_on_update(tmp_path):
    """A three-level parent loop (c→a→b→c) is rejected."""
    vault, state = _make_vault(tmp_path)
    c = _mk_task(vault, state, "c")
    _mk_task(vault, state, "b", extra=["--parent", "c"])
    _mk_task(vault, state, "a", extra=["--parent", "b"])
    # Point c's parent at a → closes c→a→b→c.
    u = _run(["record", "update", c, "--parent", "a"], vault=vault, state_dir=state)
    assert u.returncode != 0
    assert "graph-guard [parent-loop]" in u.stderr
    assert "parent" not in _find_sidecar(vault, c)


def test_status_done_with_open_children_rejected_naming_them(tmp_path):
    """--status done on a parent with a non-terminal child is rejected, naming the child."""
    vault, state = _make_vault(tmp_path)
    parent = _mk_task(vault, state, "parent")
    _mk_task(vault, state, "kid", extra=["--parent", "parent"])  # status defaults to open
    u = _run(["record", "update", parent, "--status", "done"], vault=vault, state_dir=state)
    assert u.returncode != 0
    assert "graph-guard [parent-completion]" in u.stderr
    assert "kid" in u.stderr
    # Parent was not completed.
    assert _find_sidecar(vault, parent)["status"] != "done"


def test_terminal_children_satisfy_completion_guard(tmp_path):
    """Children in done/dropped/superseded do not block the parent completing."""
    vault, state = _make_vault(tmp_path)
    parent = _mk_task(vault, state, "parent")
    _mk_task(vault, state, "kid-done", extra=["--parent", "parent", "--status", "done"])
    _mk_task(vault, state, "kid-dropped", extra=["--parent", "parent", "--status", "dropped"])
    _mk_task(vault, state, "kid-sup", extra=["--parent", "parent", "--status", "superseded"])
    u = _run(["record", "update", parent, "--status", "done"], vault=vault, state_dir=state)
    assert u.returncode == 0, u.stderr
    assert _find_sidecar(vault, parent)["status"] == "done"


def test_dependent_warning_on_drop_does_not_block(tmp_path):
    """Dropping a depended-on task warns listing dependents but succeeds (exit 0)."""
    vault, state = _make_vault(tmp_path)
    a = _mk_task(vault, state, "a")
    _mk_task(vault, state, "b", extra=["--depends-on", "a"])
    u = _run(["record", "update", a, "--status", "dropped"], vault=vault, state_dir=state)
    assert u.returncode == 0, u.stderr
    assert "graph-guard [dependents]" in u.stderr
    assert "b" in u.stderr
    # The drop still happened — it is a warning, not a block.
    assert _find_sidecar(vault, a)["status"] == "dropped"


def test_dependent_warning_on_delete_does_not_block(tmp_path):
    """Deleting a depended-on task warns listing dependents but succeeds."""
    vault, state = _make_vault(tmp_path)
    a = _mk_task(vault, state, "a")
    _mk_task(vault, state, "b", extra=["--depends-on", "a"])
    d = _run(["record", "delete", a], vault=vault, state_dir=state)
    assert d.returncode == 0, d.stderr
    assert "graph-guard [dependents]" in d.stderr
    assert "b" in d.stderr
    assert not (vault / "task" / "a.md").exists()


def test_flow_out_reminder_iff_parent_body_lacks_section(tmp_path):
    """Completing a parent prints the flow-out reminder only when body lacks the section."""
    vault, state = _make_vault(tmp_path)
    # No section → reminder (only fires for a task with children).
    plain = _mk_task(vault, state, "plain", body="prose only\n")
    _mk_task(vault, state, "plain-kid", extra=["--parent", "plain", "--status", "done"])
    u1 = _run(["record", "update", plain, "--status", "done"], vault=vault, state_dir=state)
    assert u1.returncode == 0, u1.stderr
    assert "graph-guard [flow-out]" in u1.stderr

    # Has section → no reminder (metadata-only update keeps the body).
    ritual = _mk_task(vault, state, "ritual", body="intro\n\n## Flow-out\n- [ ] x\n")
    _mk_task(vault, state, "ritual-kid", extra=["--parent", "ritual", "--status", "done"])
    u2 = _run(["record", "update", ritual, "--status", "done"], vault=vault, state_dir=state)
    assert u2.returncode == 0, u2.stderr
    assert "graph-guard [flow-out]" not in u2.stderr


def test_no_flow_out_reminder_for_childless_task_on_update(tmp_path):
    """A childless leaf task set to done via update never gets the flow-out reminder."""
    vault, state = _make_vault(tmp_path)
    leaf = _mk_task(vault, state, "leaf", body="prose only\n")
    u = _run(["record", "update", leaf, "--status", "done"], vault=vault, state_dir=state)
    assert u.returncode == 0, u.stderr
    assert "graph-guard [flow-out]" not in u.stderr


def test_non_task_kind_unaffected_by_dependent_guard(tmp_path):
    """A non-task record sharing a name with a task is not swept into the task graph.

    A task ``bar`` depends-on task ``foo``. Dropping the *task* ``foo`` warns;
    dropping a *decision* that happens to be named ``foo`` does not — the guards
    are task-gated and read only the task/ subtree.
    """
    vault, state = _make_vault(tmp_path)
    _mk_task(vault, state, "foo")
    _mk_task(vault, state, "bar", extra=["--depends-on", "foo"])
    # A decision named foo (decision vocab includes 'dropped').
    dc = _run(
        ["record", "create", "--kind", "decision", "--title", "foo"],
        vault=vault, state_dir=state, stdin_text="body\n",
    )
    assert dc.returncode == 0, dc.stderr
    decision_id = dc.stdout.strip()
    assert decision_id.startswith("decision/")

    # Dropping the decision must NOT warn about the task's dependents.
    ud = _run(["record", "update", decision_id, "--status", "dropped"], vault=vault, state_dir=state)
    assert ud.returncode == 0, ud.stderr
    assert "graph-guard [dependents]" not in ud.stderr

    # Dropping the task DOES warn (proves the guard is live, just task-scoped).
    ut = _run(["record", "update", "task/foo", "--status", "dropped"], vault=vault, state_dir=state)
    assert ut.returncode == 0, ut.stderr
    assert "graph-guard [dependents]" in ut.stderr
    assert "bar" in ut.stderr


def test_guard_error_messages_share_one_format(tmp_path):
    """Every graph-guard line (errors + warning) matches the one shared shape."""
    import re

    vault, state = _make_vault(tmp_path)
    shape = re.compile(r"^graph-guard \[[a-z-]+\]: ", re.MULTILINE)

    lines: list[str] = []

    # depends-on cycle.
    a = _mk_task(vault, state, "a")
    _mk_task(vault, state, "b", extra=["--depends-on", "a"])
    r_cycle = _run(["record", "update", a, "--depends-on", "b"], vault=vault, state_dir=state)
    lines += [ln for ln in r_cycle.stderr.splitlines() if ln.startswith("graph-guard")]

    # parent loop.
    r_loop = _run(["record", "update", a, "--parent", "a"], vault=vault, state_dir=state)
    lines += [ln for ln in r_loop.stderr.splitlines() if ln.startswith("graph-guard")]

    # parent completion.
    parent = _mk_task(vault, state, "parent")
    _mk_task(vault, state, "kid", extra=["--parent", "parent"])
    r_done = _run(["record", "update", parent, "--status", "done"], vault=vault, state_dir=state)
    lines += [ln for ln in r_done.stderr.splitlines() if ln.startswith("graph-guard")]

    # dependent warning.
    r_warn = _run(["record", "update", a, "--status", "dropped"], vault=vault, state_dir=state)
    lines += [ln for ln in r_warn.stderr.splitlines() if ln.startswith("graph-guard")]

    assert lines, "expected at least one graph-guard line"
    for ln in lines:
        assert shape.match(ln), f"malformed guard line: {ln!r}"
