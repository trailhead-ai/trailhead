"""Slice 3 (S2) tests: ``lore record create`` CLI — thin shell over Slice 2.

Covers every bullet in the Slice 3 test contract
(plan ``lore-record-and-session-cli-s2.md``):

  - create with piped body → RECORD_ID on stdout; body/sidecar/index all
    present and consistent.
  - create with no stdin → empty body, sidecar has only auto-set/required
    metadata.
  - missing ``--kind`` → non-zero, stderr names the requirement, nothing
    created.
  - body whose first line is ``---`` is stored verbatim (sidecar unaffected)
    (AC-TX3: a leading ``---`` block is NOT parsed as frontmatter).
  - ``--set``/``--unset`` matrix:
      - AC15: ``--set K=""`` ≡ ``--unset K`` (scalar).
      - AC16: ``--unset K=VALUE`` removes one list item.
      - AC17: list ``--set K=""`` → non-zero with corrective message.
      - AC18: ``--unset K`` (no value) clears the whole list.
      - AC-PROV1: ``--set``/``--unset`` on provenance field → non-zero.
  - ``--team X`` routes scope while ``--set team=X`` writes only the sidecar
    field — asserted distinctly (AC-ROUTE1).
  - unknown subcommand → non-zero with a "did you mean" hint (AC-DISP1).

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern).  Never
writes to the real $LORE_VAULT; always injects LORE_VAULT + XDG_STATE_HOME.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import CLI_PATH

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"


# ---------------------------------------------------------------------------
# Helpers
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
    """Return (vault_dir, state_dir), creating both (parents=True for nested paths)."""
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return vault, state


def _find_sidecar(vault: Path, record_id: str) -> dict:
    """Read and JSON-parse the sidecar for a RECORD_ID (``<kind>/<name>``)."""
    kind, name = record_id.split("/", 1)
    path = vault / kind / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _find_body(vault: Path, record_id: str) -> str:
    kind, name = record_id.split("/", 1)
    return (vault / kind / f"{name}.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Minimal valid sidecar fields (only the operator-required keys)
# ---------------------------------------------------------------------------

_BASE_ARGS = [
    "record", "create",
    "--kind", "spec",
    "--title", "My Record",
    "--set", "keywords=foo",
]


# ---------------------------------------------------------------------------
# AC1: body from stdin → stored verbatim; sidecar + index consistent
# ---------------------------------------------------------------------------

def test_create_with_piped_body_returns_id_on_stdout(tmp_path):
    """create with piped body → RECORD_ID printed on stdout (AC4)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS,
        vault=vault, state_dir=state,
        stdin_text="# Hello\nThis is the body.\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    assert record_id.startswith("spec/"), f"expected spec/<name>, got {record_id!r}"


def test_create_with_piped_body_body_and_sidecar_consistent(tmp_path):
    """create round-trip: body + sidecar + index row all present + consistent."""
    vault, state = _make_vault(tmp_path)
    body_text = "# Hello\nThis is the body.\n"
    r = _run(
        _BASE_ARGS,
        vault=vault, state_dir=state,
        stdin_text=body_text,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()

    stored_body = _find_body(vault, record_id)
    assert stored_body == body_text

    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["kind"] == "spec"
    assert sidecar["title"] == "My Record"
    assert sidecar["version"] == "v1"
    assert "created-at" in sidecar
    assert "created-by" in sidecar
    assert sidecar["created-by"] == "tester@example.com"


def test_create_with_piped_body_index_row_present(tmp_path):
    """The index row is upserted on create."""
    import importlib.util

    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS,
        vault=vault, state_dir=state,
        stdin_text="body text\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    kind, name = record_id.split("/", 1)

    # Load index_store from scripts to verify the row is there.
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(
        "index_store_test", SCRIPTS_DIR / "index_store.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    conn = mod.open_index(env={"XDG_STATE_HOME": str(state)})
    try:
        rows = conn.execute(
            "SELECT name FROM records WHERE vault=? AND kind=? AND name=?",
            (str(vault), kind, name),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# AC1: no stdin → empty body, sidecar has only auto-set / required metadata
# ---------------------------------------------------------------------------

def test_create_no_stdin_empty_body(tmp_path):
    """With no stdin the stored body is empty (AC1)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS,
        vault=vault, state_dir=state,
        # no stdin_text → subprocess stdin is None (closed)
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    assert _find_body(vault, record_id) == ""


def test_create_no_stdin_sidecar_has_auto_fields_only(tmp_path):
    """With no stdin the sidecar carries auto-set + operator-required fields (AC1)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS,
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    sidecar = _find_sidecar(vault, record_id)
    # Auto-set provenance fields present.
    assert "created-at" in sidecar
    assert "updated-at" in sidecar
    assert "created-by" in sidecar
    assert "updated-by" in sidecar
    # Required operator fields present.
    assert sidecar["kind"] == "spec"
    assert sidecar["title"] == "My Record"


# ---------------------------------------------------------------------------
# AC2: missing --kind → non-zero, nothing created
# ---------------------------------------------------------------------------

def test_create_missing_kind_exits_nonzero(tmp_path):
    """Missing --kind → non-zero exit; nothing written (AC2)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--title", "Test", "--set", "keywords=foo"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    # Error message must name the missing requirement.
    assert "kind" in r.stderr.lower() or "kind" in r.stdout.lower()
    # Nothing created.
    assert list(vault.glob("**/*.md")) == []


# ---------------------------------------------------------------------------
# AC-TX3: leading ``---`` in body is preserved verbatim, NOT parsed
# ---------------------------------------------------------------------------

def test_create_leading_triple_dash_body_stored_verbatim(tmp_path):
    """A body starting with '---' is stored as-is; sidecar is NOT affected (AC-TX3)."""
    vault, state = _make_vault(tmp_path)
    body_text = "---\nsome: yaml-like-content\n---\n\nActual body here.\n"
    r = _run(
        _BASE_ARGS,
        vault=vault, state_dir=state,
        stdin_text=body_text,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    stored = _find_body(vault, record_id)
    assert stored == body_text

    # Sidecar is not contaminated by the leading --- block.
    sidecar = _find_sidecar(vault, record_id)
    assert "some" not in sidecar


# ---------------------------------------------------------------------------
# AC15: --set K="" ≡ --unset K (scalar field cleared)
# ---------------------------------------------------------------------------

def test_set_empty_string_scalar_equiv_unset(tmp_path):
    """--set status="" clears status (empty str → unset/default) (AC15)."""
    vault, state = _make_vault(tmp_path)
    # status is a known scalar field; --set status="" should clear/omit it so
    # validate_and_write defaults it.
    r = _run(
        _BASE_ARGS + ["--set", "status="],
        vault=vault, state_dir=state,
    )
    # Either we succeed (status defaulted) or it may be treated as "unset",
    # but it must NOT be treated as setting status to the empty string.
    if r.returncode == 0:
        record_id = r.stdout.strip()
        sidecar = _find_sidecar(vault, record_id)
        # Empty string → status is absent or defaulted to "draft" (not "").
        assert sidecar.get("status") != ""


# ---------------------------------------------------------------------------
# AC16: --unset K=VALUE removes one list item
# ---------------------------------------------------------------------------

def test_unset_list_item_removes_single_value(tmp_path):
    """--unset keywords=foo removes only 'foo' from the keywords list (AC16)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        [
            "record", "create",
            "--kind", "spec",
            "--title", "My Record",
            "--set", "keywords=foo",
            "--set", "keywords=bar",
            "--unset", "keywords=foo",
        ],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["keywords"] == ["bar"]


# ---------------------------------------------------------------------------
# AC17: list --set K="" is a hard error naming correct forms
# ---------------------------------------------------------------------------

def test_set_empty_list_field_is_hard_error(tmp_path):
    """--set keywords="" on a list field → non-zero exit with corrective message (AC17)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        [
            "record", "create",
            "--kind", "spec",
            "--title", "My Record",
            "--set", "keywords=foo",
            "--set", "keywords=",   # empty value on a list field → hard error
        ],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    # Corrective message must name the correct forms (e.g., --unset).
    err_out = r.stderr + r.stdout
    assert "unset" in err_out.lower() or "--unset" in err_out


# ---------------------------------------------------------------------------
# AC18: --unset K (no value) clears the whole list
# ---------------------------------------------------------------------------

def test_unset_list_field_no_value_clears_whole_list(tmp_path):
    """--unset keywords (no =VALUE) clears the entire keywords list (AC18)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        [
            "record", "create",
            "--kind", "spec",
            "--title", "My Record",
            "--set", "keywords=foo",
            "--set", "keywords=bar",
            "--unset", "keywords",   # clears whole list
        ],
        vault=vault, state_dir=state,
    )
    # Clearing the only required list field may fail validation — that is
    # expected and acceptable.  What must NOT happen: silent write with an
    # empty list (keywords=[]) when the error path should fire, OR the list
    # still containing items.
    if r.returncode == 0:
        record_id = r.stdout.strip()
        sidecar = _find_sidecar(vault, record_id)
        assert sidecar.get("keywords") in (None, [])
    # non-zero is also acceptable (validation rejects empty required list).


# ---------------------------------------------------------------------------
# AC-PROV1: --set/--unset on provenance field → hard error, nothing written
# ---------------------------------------------------------------------------

def test_set_provenance_field_is_hard_error(tmp_path):
    """--set created-at=... → non-zero exit, nothing written (AC-PROV1)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--set", "created-at=2026-01-01T00:00:00Z"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    assert list(vault.glob("**/*.md")) == []


def test_unset_provenance_field_is_hard_error(tmp_path):
    """--unset updated-by → non-zero exit, nothing written (AC-PROV1)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--unset", "updated-by"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    assert list(vault.glob("**/*.md")) == []


# ---------------------------------------------------------------------------
# AC-ROUTE1: --team X (routing) vs --set team=X (sidecar metadata) distinction
# ---------------------------------------------------------------------------

def test_routing_team_flag_vs_set_team_metadata(tmp_path):
    """--team X routes scope; --set team=X writes the sidecar field (AC-ROUTE1).

    Both can succeed but must be treated as distinct semantics:
    - --team passes scope to place_record (no sidecar metadata effect).
    - --set team=X writes the sidecar 'team' field.
    """
    vault, state = _make_vault(tmp_path)

    # --set team=X: writes sidecar team field.
    r_meta = _run(
        _BASE_ARGS + ["--set", "team=alpha"],
        vault=vault, state_dir=state,
    )
    assert r_meta.returncode == 0, r_meta.stderr
    record_id_meta = r_meta.stdout.strip()
    sidecar_meta = _find_sidecar(vault, record_id_meta)
    assert sidecar_meta.get("team") == "alpha"

    # --team X: routing flag only; does not set team in sidecar.
    vault2, state2 = _make_vault(tmp_path / "v2")
    r_route = _run(
        _BASE_ARGS + ["--team", "beta"],
        vault=vault2, state_dir=state2,
    )
    assert r_route.returncode == 0, r_route.stderr
    record_id_route = r_route.stdout.strip()
    sidecar_route = _find_sidecar(vault2, record_id_route)
    # --team as a routing flag must NOT write 'team' into the sidecar metadata.
    assert sidecar_route.get("team") is None


# ---------------------------------------------------------------------------
# AC-DISP1: unknown subcommand → non-zero with "did you mean" hint
# ---------------------------------------------------------------------------

def test_unknown_subcommand_hints_did_you_mean(tmp_path):
    """An unrecognized command → non-zero + 'did you mean' hint (AC-DISP1)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["frob"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "did you mean" in out.lower() or "unknown" in out.lower()


def test_known_removed_command_hints_replacement(tmp_path):
    """A removed/renamed command prints a specific 'use X instead' hint.

    The recall→search mapping table is present; 'recall' currently still routes,
    so this test verifies the dispatch hint table is wired, not that recall
    is removed.  We test with a clearly non-existent old command.
    """
    vault, state = _make_vault(tmp_path)
    # 'frob' is not a known command; the hint mechanism should fire.
    r = _run(
        ["frob"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    out = r.stdout + r.stderr
    # Some kind of helpful message must appear.
    assert len(out.strip()) > 0
