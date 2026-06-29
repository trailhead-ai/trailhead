"""Tests for the ``lore record create`` CLI — a thin shell over the record store.

Covers the test contract:

  - create with piped body → RECORD_ID on stdout; body/sidecar/index all
    present and consistent.
  - create with no stdin → empty body, sidecar has only auto-set/required
    metadata.
  - missing ``--kind`` → non-zero, stderr names the requirement, nothing
    created.
  - body whose first line is ``---`` is stored verbatim (sidecar unaffected)
    (a leading ``---`` block is NOT parsed as frontmatter).
  - unknown subcommand → non-zero with a "did you mean" hint.

Dedicated per-field flags replaced the generic ``--set``/``--unset``
patch idiom. This file now exercises:
  - ``--status`` (scalar) sets the field; off-vocab status → non-zero, vocab named.
  - list flags ``--keyword`` / ``--related-file`` / ``--related-url`` /
    ``--related-phase`` append; ``--unset-<field> VALUE`` removes one item.
  - ``--related <kind>=<name>`` appends to that kind's list; empty kind/name and a
    bad kind are rejected.
  - ``keywords`` is optional: create with no ``--keyword`` succeeds.
  - ``--set``/``--unset`` are gone (argparse-unrecognized).
  - provenance fields remain unwritable (no flag exists for them).

Scope flags on ``create`` are unified: the
routing flags ``--team``/``--suite``/``--product``/``--repo`` additionally write
their raw value into the namesake sidecar field, from the same loop that builds
the routing scope.  One input, both effects — field value and routing value
always agree.  This file exercises:
  - ``--team alpha`` with a config routing ``team:alpha`` → its vault: sidecar
    has ``team: alpha`` AND the record lands in that vault.
  - ``--team alpha`` with no config: sidecar has ``team: alpha``, record in the
    active vault.
  - Multiple scope flags (``--team alpha --repo r1``) write both fields.
  - Cannot decouple: no flag sets the ``team`` sidecar field to a value other
    than the routed scope (``--set team=…`` is rejected; no other ``team``-field
    flag exists).

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern).  Never
writes to the real vault: the CLI resolves the test vault from a seeded
config.json (isolated XDG_CONFIG_HOME) and XDG_STATE_HOME is fenced too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from conftest import make_vault as _make_vault, run_cli as _run, write_default_config  # noqa: F401

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(config_home: Path, vaults: list[dict]) -> Path:
    """Write a config.json under config_home/lore/ and return its path."""
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    cfg_path = lore_cfg / "config.json"
    cfg_path.write_text(json.dumps({"vaults": vaults}, indent=2), encoding="utf-8")
    return cfg_path


def _run_with_config(args, *, vault, state, config_home, stdin_text=None):
    return _run(
        args,
        vault=vault,
        state_dir=state,
        stdin_text=stdin_text,
        env_extra={"XDG_CONFIG_HOME": str(config_home)},
    )


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
    "record",
    "create",
    "--kind",
    "spec",
    "--title",
    "My Record",
    "--keyword",
    "foo",
]


# ---------------------------------------------------------------------------
# body from stdin → stored verbatim; sidecar + index consistent
# ---------------------------------------------------------------------------


def test_create_with_piped_body_returns_id_on_stdout(tmp_path):
    """create with piped body → RECORD_ID printed on stdout."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS,
        vault=vault,
        state_dir=state,
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
        vault=vault,
        state_dir=state,
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
        vault=vault,
        state_dir=state,
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
# no stdin → empty body, sidecar has only auto-set / required metadata
# ---------------------------------------------------------------------------


def test_create_no_stdin_empty_body(tmp_path):
    """With no stdin the stored body is empty."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS,
        vault=vault,
        state_dir=state,
        # no stdin_text → subprocess stdin is None (closed)
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    assert _find_body(vault, record_id) == ""


def test_create_no_stdin_sidecar_has_auto_fields_only(tmp_path):
    """With no stdin the sidecar carries auto-set + operator-required fields."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS,
        vault=vault,
        state_dir=state,
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
# missing --kind → non-zero, nothing created
# ---------------------------------------------------------------------------


def test_create_missing_kind_exits_nonzero(tmp_path):
    """Missing --kind → non-zero exit; nothing written."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--title", "Test", "--keyword", "foo"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    # Error message must name the missing requirement.
    assert "kind" in r.stderr.lower() or "kind" in r.stdout.lower()
    # Nothing created.
    assert list(vault.glob("**/*.md")) == []


# ---------------------------------------------------------------------------
# leading ``---`` in body is preserved verbatim, NOT parsed
# ---------------------------------------------------------------------------


def test_create_leading_triple_dash_body_stored_verbatim(tmp_path):
    """A body starting with '---' is stored as-is; sidecar is NOT affected."""
    vault, state = _make_vault(tmp_path)
    body_text = "---\nsome: yaml-like-content\n---\n\nActual body here.\n"
    r = _run(
        _BASE_ARGS,
        vault=vault,
        state_dir=state,
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
# --status (scalar) sets the field; off-vocab → non-zero, vocab named
# ---------------------------------------------------------------------------


def test_status_flag_sets_field(tmp_path):
    """--status sets the sidecar status to an in-vocab value."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--status", "ready"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["status"] == "ready"


def test_status_off_vocab_nonzero_names_vocab(tmp_path):
    """An off-vocab --status → non-zero; stderr names the permitted vocab."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        [
            "record",
            "create",
            "--kind",
            "decision",
            "--title",
            "T",
            "--keyword",
            "foo",
            "--status",
            "nonsense",
        ],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    err = r.stderr.lower()
    # Validation still fires and names the allowed values for the kind.
    assert "active" in err and "superseded" in err
    assert list(vault.glob("**/*.md")) == []


# ---------------------------------------------------------------------------
# repeatable list flags append; --unset-<field> VALUE removes one
# ---------------------------------------------------------------------------


def test_keyword_flag_appends(tmp_path):
    """--keyword a --keyword b → keywords == ['a', 'b'] (append order preserved)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        [
            "record",
            "create",
            "--kind",
            "spec",
            "--title",
            "My Record",
            "--keyword",
            "a",
            "--keyword",
            "b",
        ],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["keywords"] == ["a", "b"]


def test_unset_keyword_removes_single_item(tmp_path):
    """--unset-keyword a removes only 'a' from the keywords list."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        [
            "record",
            "create",
            "--kind",
            "spec",
            "--title",
            "My Record",
            "--keyword",
            "a",
            "--keyword",
            "b",
            "--unset-keyword",
            "a",
        ],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["keywords"] == ["b"]


def test_related_url_flag_appends_and_unsets(tmp_path):
    """--related-url appends to related-urls; --unset-related-url removes one."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS
        + [
            "--related-url",
            "https://a.example",
            "--related-url",
            "https://b.example",
            "--unset-related-url",
            "https://a.example",
        ],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["related-urls"] == ["https://b.example"]


def test_related_file_flag_maps_to_related_files_or_folders(tmp_path):
    """--related-file appends to the related-files-or-folders sidecar key."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--related-file", "src/foo.py", "--related-file", "src/bar.py"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["related-files-or-folders"] == ["src/foo.py", "src/bar.py"]


def test_related_phase_flag_appends(tmp_path):
    """--related-phase appends to related-phases (a valid phase passes validation)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--related-phase", "frame", "--related-phase", "build"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["related-phases"] == ["frame", "build"]


# ---------------------------------------------------------------------------
# --related <kind>=<name> map flag
# ---------------------------------------------------------------------------


def test_related_map_flag_appends_under_kind(tmp_path):
    """--related plan=foo --related plan=bar → related == {'plan': ['foo','bar']}."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--related", "plan=foo", "--related", "plan=bar"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar["related"] == {"plan": ["foo", "bar"]}


def test_related_map_invalid_kind_nonzero_names_kind(tmp_path):
    """--related bogus=x (invalid kind) → non-zero; stderr names the bad kind."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--related", "bogus=x"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    assert "bogus" in r.stderr
    assert list(vault.glob("**/*.md")) == []


def test_related_map_empty_name_rejected_by_guard(tmp_path):
    """--related plan= (empty name) → non-zero from the applier guard (before validate)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--related", "plan="],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    assert list(vault.glob("**/*.md")) == []


def test_related_map_empty_kind_rejected_by_guard(tmp_path):
    """--related =foo (empty kind) → non-zero from the applier guard (before validate)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--related", "=foo"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    assert list(vault.glob("**/*.md")) == []


# ---------------------------------------------------------------------------
# keywords optional — create with no --keyword succeeds
# ---------------------------------------------------------------------------


def test_create_no_keyword_succeeds(tmp_path):
    """create with NO --keyword now validates and succeeds (keywords optional)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--kind", "spec", "--title", "My Record"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar.get("keywords") in (None, [])


# ---------------------------------------------------------------------------
# --set/--unset are gone; provenance remains unwritable
# ---------------------------------------------------------------------------


def test_set_flag_is_unrecognized(tmp_path):
    """--set is removed: argparse rejects it as an unrecognized argument."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--set", "title=x"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    assert list(vault.glob("**/*.md")) == []


def test_unset_flag_is_unrecognized(tmp_path):
    """--unset is removed: argparse rejects it as an unrecognized argument."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--unset", "keywords"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    assert list(vault.glob("**/*.md")) == []


def test_no_flag_for_provenance_fields(tmp_path):
    """No dedicated flag exists for provenance keys (created-at etc.)."""
    vault, state = _make_vault(tmp_path)
    for prov_flag in ("--created-at", "--created-by", "--updated-at", "--updated-by"):
        r = _run(
            _BASE_ARGS + [prov_flag, "x"],
            vault=vault,
            state_dir=state,
        )
        assert r.returncode != 0, f"{prov_flag} should be unrecognized"


# ---------------------------------------------------------------------------
# unknown subcommand → non-zero with "did you mean" hint
# ---------------------------------------------------------------------------


def test_unknown_subcommand_hints_did_you_mean(tmp_path):
    """An unrecognized command → non-zero + 'did you mean' hint."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["frob"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "did you mean" in out.lower() or "unknown" in out.lower()


def test_search_is_a_registered_command(tmp_path):
    """``search`` is a real command, not an unknown-command hint.

    The ``search`` subcommand is registered, so the dormant ``search→recall``
    dispatch-hint scaffold is gone. Typing ``lore search`` with no query is now a
    *valid command* failing on a missing positional arg (argparse usage error) —
    it must NOT be mislabelled as an unknown command pointing at ``recall``.
    """
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["search"],
        vault=vault,
        state_dir=state,
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

    Regression guard: 'record create' missing --kind is a legitimate
    argparse error under a *valid* top-level command; the unknown-command hint
    must not fire and claim 'unknown command record'.
    """
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--title", "No Kind Here"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    out = (r.stdout + r.stderr).lower()
    assert "unknown command" not in out


# ---------------------------------------------------------------------------
# --label / --annotation / --unset-label / --unset-annotation
# ---------------------------------------------------------------------------


def test_create_label_two_entries_both_stored(tmp_path):
    """create --label worktree=s5 --label claude-code/model=x → both entries in labels."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS
        + [
            "--label",
            "worktree=s5",
            "--label",
            "claude-code/model=x",
        ],
        vault=vault,
        state_dir=state,
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
        vault=vault,
        state_dir=state,
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
        vault=vault,
        state_dir=state,
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
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    assert "BadKey" in r.stderr
    assert list(vault.glob("**/*.md")) == []


def test_create_label_value_with_equals_splits_on_first(tmp_path):
    """--annotation note=a=b → value is 'a=b' (split on first '=' only)."""
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--annotation", "note=a=b"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar["annotations"]["note"] == "a=b"


# ---------------------------------------------------------------------------
# scope flags write the namesake sidecar
# field AND drive vault routing — one input, both effects.
# ---------------------------------------------------------------------------


def test_scope_team_with_config_writes_field_and_routes(tmp_path):
    """--team alpha with a config routing team:alpha → its vault: sidecar has
    ``team: alpha`` and the record physically lands in the scoped vault.

    One input drives both the field write and vault selection; the field value
    and the routing value always agree.

    Note: the config vault's ``name`` must equal ``normalize_vault_name("alpha")``
    == "alpha" for resolution to elect the scoped vault.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"

    scoped_vault = tmp_path / "team_alpha_vault"
    scoped_vault.mkdir(parents=True)

    _write_config(
        config_home,
        [
            {"name": "default", "scope": "default", "path": str(vault)},
            {
                "name": "alpha",
                "scope": "team",
                "records": ["decision"],
                "path": str(scoped_vault),
            },
        ],
    )

    r = _run_with_config(
        ["record", "create", "--kind", "decision", "--title", "T", "--team", "alpha"],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="body\n",
    )
    assert r.returncode == 0, r.stderr

    record_id = r.stdout.strip()
    assert record_id.startswith("decision/")

    # Field written: sidecar carries team: alpha
    sidecar = _find_sidecar(scoped_vault, record_id)
    assert sidecar.get("team") == "alpha"

    # Routing: record physically in the scoped vault, not the active vault
    kind, name = record_id.split("/", 1)
    assert (scoped_vault / kind / f"{name}.md").exists()
    assert not (vault / kind / f"{name}.md").exists()


def test_scope_team_no_config_writes_field_in_active_vault(tmp_path):
    """--team alpha with no config: sidecar has ``team: alpha``, record in the
    active vault (vanilla routing, field write still fires).
    """
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--kind", "decision", "--title", "T", "--team", "alpha"],
        vault=vault,
        state_dir=state,
        stdin_text="body\n",
    )
    assert r.returncode == 0, r.stderr

    record_id = r.stdout.strip()
    sidecar = _find_sidecar(vault, record_id)
    assert sidecar.get("team") == "alpha"

    # Record lands in the active vault
    kind, name = record_id.split("/", 1)
    assert (vault / kind / f"{name}.md").exists()


def test_scope_multiple_flags_write_all_fields(tmp_path):
    """--team alpha --repo r1 writes both ``team: alpha`` and ``repo: r1`` into
    the sidecar; each flag contributes its own field independently.
    """
    vault, state = _make_vault(tmp_path)
    r = _run(
        [
            "record",
            "create",
            "--kind",
            "decision",
            "--title",
            "T",
            "--team",
            "alpha",
            "--repo",
            "r1",
        ],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr

    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar.get("team") == "alpha"
    assert sidecar.get("repo") == "r1"


def test_scope_field_raw_value_not_scope_string(tmp_path):
    """The sidecar value is the raw flag value (``'my-team'``), NOT the scope-string
    form (``'team:my-team'``) — guards against wrong value derivation.
    """
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--kind", "decision", "--title", "T", "--team", "my-team"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr

    sidecar = _find_sidecar(vault, r.stdout.strip())
    assert sidecar.get("team") == "my-team"
    assert sidecar.get("team") != "team:my-team"


def test_scope_cannot_decouple_set_team_rejected(tmp_path):
    """``--set team=…`` is gone: no decoupled setter can write ``team``
    to a value that differs from the routing scope.  Argparse rejects ``--set``.
    """
    vault, state = _make_vault(tmp_path)
    r = _run(
        _BASE_ARGS + ["--set", "team=other"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode != 0
    assert list(vault.glob("**/*.md")) == []


def test_scope_no_other_team_field_flag(tmp_path):
    """No other flag (besides ``--team``) can set the ``team`` sidecar field;
    the field value always matches the routing flag's value.

    Verified by asserting that a create WITHOUT ``--team`` produces no ``team``
    field in the sidecar — if a second write path existed it would set the field
    through some other means and appear here.
    """
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "create", "--kind", "decision", "--title", "T"],
        vault=vault,
        state_dir=state,
    )
    assert r.returncode == 0, r.stderr

    sidecar = _find_sidecar(vault, r.stdout.strip())
    # No --team supplied → no team field in sidecar (no other write path exists).
    assert sidecar.get("team") is None


# ---------------------------------------------------------------------------
# Group-default scope routing: a record created inside a camp workspace whose
# group declares a [[lore_scopes]] binding inherits that scope when no routing
# flag is supplied. Explicit flags always win (setdefault, never assignment);
# when the elected route came from the binding the confirmation line is
# annotated ``(via group default)`` so a typo'd binding is diagnosable.
# ---------------------------------------------------------------------------


def _write_routing_config(config_home, *, default_vault, vaults):
    """Write a config.json with a default-scope floor plus the given vaults.

    ``vaults`` is a list of ``(name, scope, path)`` tuples. None of the scoped
    vaults declare a ``records`` allowlist, so each is eligible for any kind.
    """
    entries = [{"name": "default", "scope": "default", "path": str(default_vault)}]
    for name, scope, path in vaults:
        entries.append({"name": name, "scope": scope, "path": str(path)})
    return _write_config(config_home, entries)


def _write_group_binding(groups_dir, *, member_repo, group_name="trailhead", lore_scopes=None):
    """Write a camp group TOML binding ``member_repo`` to a [[lore_scopes]] map.

    ``lore_scopes`` is a list of ``{"scope", "name"}`` dicts. ``member_repo`` is
    declared as the group's only member repo_root, so a subprocess run with
    ``cwd=member_repo`` resolves to this group via camp's canonical-member-repo
    walk-up (which needs no camp_state_dir).
    """
    groups_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f'[group]\nname = "{group_name}"\n',
        f'\n[[members]]\nname = "repo"\nrepo_root = "{member_repo}"\n',
    ]
    for ls in lore_scopes or []:
        lines.append(f'\n[[lore_scopes]]\nscope = "{ls["scope"]}"\nname = "{ls["name"]}"\n')
    (groups_dir / f"{group_name}.toml").write_text("".join(lines), encoding="utf-8")


def _routing_env(config_home, groups_dir):
    return {"XDG_CONFIG_HOME": str(config_home), "LORE_GROUPS_DIR": str(groups_dir)}


def test_group_default_routes_to_bound_vault_with_provenance(tmp_path):
    """No routing flag + a group binding product=trailhead → the record lands in
    the trailhead (product) vault and the confirmation line is annotated.

    Mutation guard: the destination is asserted against the trailhead vault root
    explicitly (not just the stderr string). With the group-default seeding
    removed, ``participating_scopes`` is empty, resolution falls to the default
    floor, and the body would land in the default vault — failing the
    ``trailhead_vault`` existence assertion. The test therefore fails if the
    seeding is deleted.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    groups_dir = tmp_path / "groups"
    member_repo = tmp_path / "repo"
    member_repo.mkdir()
    trailhead_vault = tmp_path / "trailhead_vault"
    trailhead_vault.mkdir()

    _write_routing_config(
        config_home,
        default_vault=vault,
        vaults=[("trailhead", "product", trailhead_vault)],
    )
    _write_group_binding(
        groups_dir,
        member_repo=member_repo,
        lore_scopes=[{"scope": "product", "name": "trailhead"}],
    )

    r = _run(
        ["record", "create", "--kind", "spec", "--title", "T", "--keyword", "foo"],
        vault=vault,
        state_dir=state,
        env_extra=_routing_env(config_home, groups_dir),
        cwd=member_repo,
    )
    assert r.returncode == 0, r.stderr

    record_id = r.stdout.strip()
    kind, name = record_id.split("/", 1)
    # Destination asserted against the trailhead vault root, NOT the active vault.
    assert (trailhead_vault / kind / f"{name}.md").exists()
    assert not (vault / kind / f"{name}.md").exists()
    # Provenance annotation present because the elected route came from the binding.
    assert "Routed to vault: trailhead (product) (via group default)" in r.stderr


def test_group_default_flush_path_routes_to_bound_vault(tmp_path):
    """A session candidate captured to the default vault, then finalized via
    ``lore record create`` from within the bound member repo, routes to the
    group's vault — the flush/checkpoint capture lands in the bound vault.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    groups_dir = tmp_path / "groups"
    member_repo = tmp_path / "repo"
    member_repo.mkdir()
    trailhead_vault = tmp_path / "trailhead_vault"
    trailhead_vault.mkdir()

    _write_routing_config(
        config_home,
        default_vault=vault,
        vaults=[("trailhead", "product", trailhead_vault)],
    )
    _write_group_binding(
        groups_dir,
        member_repo=member_repo,
        lore_scopes=[{"scope": "product", "name": "trailhead"}],
    )

    env = _routing_env(config_home, groups_dir)

    # Capture a candidate from within the group workspace (writes to the default
    # vault — the candidate path is never routed).
    cand = _run(
        [
            "session", "candidate",
            "--session-id", "11111111-1111-1111-1111-111111111111",
            "--kind", "decision",
            "--phase", "Build",
        ],
        vault=vault,
        state_dir=state,
        env_extra=env,
        cwd=member_repo,
        stdin_text="a candidate proposal\n",
    )
    assert cand.returncode == 0, cand.stderr

    # Finalize the candidate into a durable record from within the member repo.
    r = _run(
        ["record", "create", "--kind", "decision", "--title", "Flushed", "--keyword", "foo"],
        vault=vault,
        state_dir=state,
        env_extra=env,
        cwd=member_repo,
        stdin_text="finalized body\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    kind, name = record_id.split("/", 1)
    assert (trailhead_vault / kind / f"{name}.md").exists()
    assert "Routed to vault: trailhead (product) (via group default)" in r.stderr


def test_explicit_product_flag_overrides_group_default(tmp_path):
    """``--product other`` beats the binding product=trailhead: the record lands
    in ``other``, the sidecar product field is ``other`` (the group default never
    overwrites an explicit flag's sidecar value), and no provenance is shown.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    groups_dir = tmp_path / "groups"
    member_repo = tmp_path / "repo"
    member_repo.mkdir()
    trailhead_vault = tmp_path / "trailhead_vault"
    trailhead_vault.mkdir()
    other_vault = tmp_path / "other_vault"
    other_vault.mkdir()

    _write_routing_config(
        config_home,
        default_vault=vault,
        vaults=[
            ("trailhead", "product", trailhead_vault),
            ("other", "product", other_vault),
        ],
    )
    _write_group_binding(
        groups_dir,
        member_repo=member_repo,
        lore_scopes=[{"scope": "product", "name": "trailhead"}],
    )

    r = _run(
        ["record", "create", "--kind", "spec", "--title", "T", "--keyword", "foo",
         "--product", "other"],
        vault=vault,
        state_dir=state,
        env_extra=_routing_env(config_home, groups_dir),
        cwd=member_repo,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    kind, name = record_id.split("/", 1)
    assert (other_vault / kind / f"{name}.md").exists()
    assert not (trailhead_vault / kind / f"{name}.md").exists()

    # Sidecar product field matches the elected vault — no group default leak.
    sidecar = _find_sidecar(other_vault, record_id)
    assert sidecar["product"] == "other"
    # No provenance suffix on an explicit-flag route.
    assert "(via group default)" not in r.stderr


def test_higher_precedence_repo_flag_overrides_group_default(tmp_path):
    """``--repo somerepo`` (repo > product) routes to the repo vault even though
    the binding seeds product=trailhead; the sidecar repo field matches the
    elected vault and no provenance is shown (the elected route is a typed flag).

    The seeded product field still lands in the sidecar: ``setdefault`` runs per
    scope independent of which scope wins routing, so the record records that it
    participates in the product scope even though the repo flag elects the repo
    vault. The explicit repo flag is never overwritten — that is the integrity
    the override guarantees, not suppression of the lower-precedence binding.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    groups_dir = tmp_path / "groups"
    member_repo = tmp_path / "repo"
    member_repo.mkdir()
    trailhead_vault = tmp_path / "trailhead_vault"
    trailhead_vault.mkdir()
    repo_vault = tmp_path / "repo_vault"
    repo_vault.mkdir()

    _write_routing_config(
        config_home,
        default_vault=vault,
        vaults=[
            ("trailhead", "product", trailhead_vault),
            ("somerepo", "repo", repo_vault),
        ],
    )
    _write_group_binding(
        groups_dir,
        member_repo=member_repo,
        lore_scopes=[{"scope": "product", "name": "trailhead"}],
    )

    r = _run(
        ["record", "create", "--kind", "spec", "--title", "T", "--keyword", "foo",
         "--repo", "somerepo"],
        vault=vault,
        state_dir=state,
        env_extra=_routing_env(config_home, groups_dir),
        cwd=member_repo,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    kind, name = record_id.split("/", 1)
    assert (repo_vault / kind / f"{name}.md").exists()
    assert not (trailhead_vault / kind / f"{name}.md").exists()

    sidecar = _find_sidecar(repo_vault, record_id)
    assert sidecar["repo"] == "somerepo"
    # The lower-precedence binding is still seeded (multi-scope participation);
    # only the explicitly-typed scope's value is protected from being overwritten.
    assert sidecar.get("product") == "trailhead"
    assert "(via group default)" not in r.stderr


def test_cwd_outside_any_group_routes_to_default(tmp_path):
    """cwd outside any configured group → vanilla routing to the default vault;
    the confirmation line carries no provenance suffix.
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

    _write_routing_config(
        config_home,
        default_vault=vault,
        vaults=[("trailhead", "product", trailhead_vault)],
    )
    _write_group_binding(
        groups_dir,
        member_repo=member_repo,
        lore_scopes=[{"scope": "product", "name": "trailhead"}],
    )

    r = _run(
        ["record", "create", "--kind", "spec", "--title", "T", "--keyword", "foo"],
        vault=vault,
        state_dir=state,
        env_extra=_routing_env(config_home, groups_dir),
        cwd=outside,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    kind, name = record_id.split("/", 1)
    assert (vault / kind / f"{name}.md").exists()
    assert not (trailhead_vault / kind / f"{name}.md").exists()
    assert "(via group default)" not in r.stderr


def test_typed_flag_route_never_shows_group_default_provenance(tmp_path):
    """A pure typed-flag route to trailhead (no binding involved) routes there but
    never shows ``(via group default)`` — the suffix is tied to seeding, not to
    the destination vault.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    groups_dir = tmp_path / "groups"
    trailhead_vault = tmp_path / "trailhead_vault"
    trailhead_vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    _write_routing_config(
        config_home,
        default_vault=vault,
        vaults=[("trailhead", "product", trailhead_vault)],
    )
    # A group exists but cwd is outside it, so no scope is ever seeded.
    member_repo = tmp_path / "repo"
    member_repo.mkdir()
    _write_group_binding(
        groups_dir,
        member_repo=member_repo,
        lore_scopes=[{"scope": "product", "name": "trailhead"}],
    )

    r = _run(
        ["record", "create", "--kind", "spec", "--title", "T", "--keyword", "foo",
         "--product", "trailhead"],
        vault=vault,
        state_dir=state,
        env_extra=_routing_env(config_home, groups_dir),
        cwd=outside,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    kind, name = record_id.split("/", 1)
    assert (trailhead_vault / kind / f"{name}.md").exists()
    assert "Routed to vault: trailhead (product)" in r.stderr
    assert "(via group default)" not in r.stderr


def test_malformed_group_config_degrades_to_default(tmp_path):
    """A malformed group binding (bad scope) degrades to default routing without
    crashing — the record lands in the default vault and create still succeeds.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    groups_dir = tmp_path / "groups"
    groups_dir.mkdir()
    member_repo = tmp_path / "repo"
    member_repo.mkdir()
    trailhead_vault = tmp_path / "trailhead_vault"
    trailhead_vault.mkdir()

    _write_routing_config(
        config_home,
        default_vault=vault,
        vaults=[("trailhead", "product", trailhead_vault)],
    )
    # ``scope = "bogus"`` is outside {repo, product, suite, team} → GroupConfigError.
    (groups_dir / "bad.toml").write_text(
        '[group]\nname = "trailhead"\n\n'
        f'[[members]]\nname = "repo"\nrepo_root = "{member_repo}"\n\n'
        '[[lore_scopes]]\nscope = "bogus"\nname = "trailhead"\n',
        encoding="utf-8",
    )

    r = _run(
        ["record", "create", "--kind", "spec", "--title", "T", "--keyword", "foo"],
        vault=vault,
        state_dir=state,
        env_extra=_routing_env(config_home, groups_dir),
        cwd=member_repo,
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    kind, name = record_id.split("/", 1)
    assert (vault / kind / f"{name}.md").exists()
    assert not (trailhead_vault / kind / f"{name}.md").exists()
    assert "(via group default)" not in r.stderr
