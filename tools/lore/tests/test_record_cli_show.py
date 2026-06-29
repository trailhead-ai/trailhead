"""Tests for the ``lore record show`` CLI — the canonical record reader.

``record show`` is the CLI-only way to read any record's body and sidecar, so
agents and skills never poke at vault files directly. It reads a record by its
``<kind>/<name>`` id only — reading THIS worktree's live session record is the
separate ``lore session show`` subcommand (see test_session_cli.py), so the
``<kind>/<name>`` grammar here has no special-cased exceptions. Test contract:

  plain:
    - ``record show <kind>/<name>`` prints the body to stdout.
  --json:
    - ``record show <kind>/<name> --json`` emits {record_id, kind, name,
      sidecar (dict), body (str)} — the sidecar is how callers read the
      un-indexed annotations (e.g. flush's ``flushed-at`` watermark).
  errors:
    - a malformed RECORD_ID (no '/') → non-zero + stderr.
    - a bare ``session`` (no '/') is NOT special-cased — same malformed error.
    - a nonexistent record → non-zero + stderr.

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern). Never writes
to the real vault: the CLI resolves the test vault from a seeded config.json
(isolated XDG_CONFIG_HOME) and XDG_STATE_HOME is fenced too.
"""
from __future__ import annotations

import json

from conftest import make_vault as _make_vault, run_cli as _run, write_default_config  # noqa: F401


def _create_record(vault, state, *, kind="spec", title="Test Record",
                   body="body text\n") -> str:
    r = _run(
        ["record", "create", "--kind", kind, "--title", title, "--keyword", "test"],
        vault=vault, state_dir=state, stdin_text=body,
    )
    assert r.returncode == 0, f"create failed: {r.stderr}"
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# plain: prints the body
# ---------------------------------------------------------------------------

def test_show_prints_body(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state, body="the body content\n")
    r = _run(["record", "show", rid], vault=vault, state_dir=state)
    assert r.returncode == 0, f"show failed: {r.stderr}"
    assert "the body content" in r.stdout


# ---------------------------------------------------------------------------
# --json: emits sidecar + body
# ---------------------------------------------------------------------------

def test_show_json_emits_sidecar_and_body(tmp_path):
    vault, state = _make_vault(tmp_path)
    rid = _create_record(vault, state, kind="decision", body="decided\n")
    r = _run(["record", "show", rid, "--json"], vault=vault, state_dir=state)
    assert r.returncode == 0, f"show --json failed: {r.stderr}"
    payload = json.loads(r.stdout)
    assert payload["record_id"] == rid
    assert payload["kind"] == "decision"
    assert payload["name"] == rid.split("/", 1)[1]
    assert "decided" in payload["body"]
    # The sidecar is a dict carrying at least the provenance the writer stamps.
    assert isinstance(payload["sidecar"], dict)
    assert payload["sidecar"], "sidecar should not be empty"


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

def test_show_invalid_record_id(tmp_path):
    vault, state = _make_vault(tmp_path)
    r = _run(["record", "show", "notaslug"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert r.stderr.strip()


def test_show_bare_session_is_not_special_cased(tmp_path):
    # Reading the live session is `lore session show`, NOT a `record show`
    # special case — a bare `session` has no '/', so it fails the <kind>/<name>
    # grammar like any other malformed id (guards against re-introducing a
    # bare-kind exception in `record show`).
    vault, state = _make_vault(tmp_path)
    r = _run(["record", "show", "session"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert "<kind>/<name>" in r.stderr


def test_show_nonexistent_record(tmp_path):
    vault, state = _make_vault(tmp_path)
    r = _run(["record", "show", "spec/does-not-exist"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert r.stderr.strip()
