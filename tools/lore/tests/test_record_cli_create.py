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
  - unknown subcommand → non-zero with a "did you mean" hint (AC-DISP1).

Slice 1 (dedicated-field-flags plan) replaced the generic ``--set``/``--unset``
patch idiom with dedicated per-field flags. This file now exercises:
  - ``--status`` (scalar) sets the field; off-vocab status → non-zero, vocab named.
  - list flags ``--keyword`` / ``--related-file`` / ``--related-url`` /
    ``--related-phase`` append; ``--unset-<field> VALUE`` removes one item.
  - ``--related <kind>=<name>`` appends to that kind's list; empty kind/name and a
    bad kind are rejected.
  - ``keywords`` is optional: create with no ``--keyword`` succeeds.
  - ``--set``/``--unset`` are gone (argparse-unrecognized).
  - provenance fields remain unwritable (no flag exists for them).

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern).  Never
writes to the real $LORE_VAULT; always injects LORE_VAULT + XDG_STATE_HOME.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from conftest import make_vault as _make_vault, run_cli as _run

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    "--keyword", "foo",
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
        ["record", "create", "--title", "Test", "--keyword", "foo"],
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
# Slice 1: --status (scalar) sets the field; off-vocab → non-zero, vocab named
# ---------------------------------------------------------------------------

def test_status_flag_sets_field(tmp_path):
    """--status sets the sidecar status to an in-vocab value."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--status", "ready"],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["status"] == "ready"


def test_status_off_vocab_nonzero_names_vocab(tmp_path):
    """An off-vocab --status → non-zero; stderr names the permitted vocab (A3)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--kind", "decision", "--title", "T",
         "--keyword", "foo", "--status", "nonsense"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    err = r.stderr.lower()
    # Validation still fires and names the allowed values for the kind.
    assert "active" in err and "superseded" in err
    assert list(vault.glob("**/*.md")) == []


# ---------------------------------------------------------------------------
# Slice 1: repeatable list flags append; --unset-<field> VALUE removes one
# ---------------------------------------------------------------------------

def test_keyword_flag_appends(tmp_path):
    """--keyword a --keyword b → keywords == ['a', 'b'] (append order preserved)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--kind", "spec", "--title", "My Record",
         "--keyword", "a", "--keyword", "b"],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["keywords"] == ["a", "b"]


def test_unset_keyword_removes_single_item(tmp_path):
    """--unset-keyword a removes only 'a' from the keywords list."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--kind", "spec", "--title", "My Record",
         "--keyword", "a", "--keyword", "b", "--unset-keyword", "a"],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["keywords"] == ["b"]


def test_related_url_flag_appends_and_unsets(tmp_path):
    """--related-url appends to related-urls; --unset-related-url removes one."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + [
            "--related-url", "https://a.example",
            "--related-url", "https://b.example",
            "--unset-related-url", "https://a.example",
        ],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["related-urls"] == ["https://b.example"]


def test_related_file_flag_maps_to_related_files_or_folders(tmp_path):
    """--related-file appends to the related-files-or-folders sidecar key."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--related-file", "src/foo.py", "--related-file", "src/bar.py"],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["related-files-or-folders"] == ["src/foo.py", "src/bar.py"]


def test_related_phase_flag_appends(tmp_path):
    """--related-phase appends to related-phases (a valid phase passes validation)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--related-phase", "frame", "--related-phase", "build"],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["related-phases"] == ["frame", "build"]


# ---------------------------------------------------------------------------
# Slice 1: --related <kind>=<name> map flag
# ---------------------------------------------------------------------------

def test_related_map_flag_appends_under_kind(tmp_path):
    """--related plan=foo --related plan=bar → related == {'plan': ['foo','bar']}."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--related", "plan=foo", "--related", "plan=bar"],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["related"] == {"plan": ["foo", "bar"]}


def test_related_map_invalid_kind_nonzero_names_kind(tmp_path):
    """--related bogus=x (invalid kind) → non-zero; stderr names the bad kind."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--related", "bogus=x"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    assert "bogus" in r.stderr
    assert list(vault.glob("**/*.md")) == []


def test_related_map_empty_name_rejected_by_guard(tmp_path):
    """--related plan= (empty name) → non-zero from the applier guard (before validate)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--related", "plan="],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    assert list(vault.glob("**/*.md")) == []


def test_related_map_empty_kind_rejected_by_guard(tmp_path):
    """--related =foo (empty kind) → non-zero from the applier guard (before validate)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--related", "=foo"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    assert list(vault.glob("**/*.md")) == []


# ---------------------------------------------------------------------------
# Slice 1: keywords optional — create with no --keyword succeeds
# ---------------------------------------------------------------------------

def test_create_no_keyword_succeeds(tmp_path):
    """create with NO --keyword now validates and succeeds (keywords optional)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--kind", "spec", "--title", "My Record"],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar.get("keywords") in (None, [])


# ---------------------------------------------------------------------------
# Slice 1: --set/--unset are gone; provenance remains unwritable
# ---------------------------------------------------------------------------

def test_set_flag_is_unrecognized(tmp_path):
    """--set is removed: argparse rejects it as an unrecognized argument."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--set", "title=x"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    assert list(vault.glob("**/*.md")) == []


def test_unset_flag_is_unrecognized(tmp_path):
    """--unset is removed: argparse rejects it as an unrecognized argument."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--unset", "keywords"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    assert list(vault.glob("**/*.md")) == []


def test_no_flag_for_provenance_fields(tmp_path):
    """No dedicated flag exists for provenance keys (created-at etc.)."""
    vault, state = _make_vault(tmp_path)
    for prov_flag in ("--created-at", "--created-by", "--updated-at", "--updated-by"):
        r = _run(
            _BASE_ARGS + [prov_flag, "x"],
            vault=vault, state_dir=state,
        )
        assert r.returncode != 0, f"{prov_flag} should be unrecognized"


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


def test_search_is_a_registered_command(tmp_path):
    """``search`` is a real command in Slice 4 (S3), not an unknown-command hint.

    Slice 4 registers the ``search`` subcommand, so the dormant S2 ``search→recall``
    dispatch-hint scaffold is gone. Typing ``lore search`` with no query is now a
    *valid command* failing on a missing positional arg (argparse usage error) —
    it must NOT be mislabelled as an unknown command pointing at ``recall``. The
    ``recall→search`` cutover hint is owned by Slice 5.
    """
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["search"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    out = (r.stdout + r.stderr).lower()
    # A valid command's argparse error — not the unknown-command hint.
    assert "unknown command" not in out
    assert "did you mean 'lore recall'" not in out
    # argparse names the missing positional and the search usage.
    assert "search" in out


def test_valid_command_bad_arg_does_not_emit_unknown_command_hint(tmp_path):
    """A valid command failing on a sub-argument must NOT be mislabelled.

    Regression guard (AC-DISP1): 'record create' missing --kind is a legitimate
    argparse error under a *valid* top-level command; the unknown-command hint
    must not fire and claim 'unknown command record'.
    """
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--title", "No Kind Here"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    out = (r.stdout + r.stderr).lower()
    assert "unknown command" not in out


# ---------------------------------------------------------------------------
# Slice 2: --label / --annotation / --unset-label / --unset-annotation
# ---------------------------------------------------------------------------

def test_create_label_two_entries_both_stored(tmp_path):
    """create --label worktree=s5 --label claude-code/model=x → both entries in labels."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + [
            "--label", "worktree=s5",
            "--label", "claude-code/model=x",
        ],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar.get("labels") == {"worktree": "s5", "claude-code/model": "x"}


def test_create_label_validates_compact_serialization(tmp_path):
    """sidecar with labels is compact (single-line JSON, sorted keys)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--label", "worktree=s5"],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    kind, name = record_id.split("/", 1)
    raw = (vault / kind / f"{name}.json").read_text(encoding="utf-8")
    # Compact: no newlines inside the JSON, and round-trips cleanly.
    import json as _json
    assert "\n" not in raw
    parsed = _json.loads(raw)
    assert parsed["labels"] == {"worktree": "s5"}


def test_create_annotation_stored(tmp_path):
    """create --annotation note=hello → annotations field in sidecar."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--annotation", "note=hello"],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar.get("annotations") == {"note": "hello"}


def test_create_label_bad_key_nonzero_names_key(tmp_path):
    """--label BadKey=x → non-zero exit, stderr names the bad key."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--label", "BadKey=x"],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    assert "BadKey" in r.stderr
    assert list(vault.glob("**/*.md")) == []


def test_create_label_value_with_equals_splits_on_first(tmp_path):
    """--annotation note=a=b → value is 'a=b' (split on first '=' only)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--annotation", "note=a=b"],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["annotations"]["note"] == "a=b"
