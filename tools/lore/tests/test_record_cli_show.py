"""Tests for the ``lore record show`` CLI — the canonical record reader.

``record show`` is the CLI-only way to read any record's body and sidecar, so
agents and skills never poke at vault files directly. Test contract:

  plain:
    - ``record show <kind>/<name>`` prints the body to stdout.
  --json:
    - ``record show <kind>/<name> --json`` emits {record_id, kind, name,
      sidecar (dict), body (str)} — the sidecar is how callers read the
      un-indexed annotations (e.g. flush's ``flushed-at`` watermark).
  current session:
    - ``record show session`` (kind only, no name) resolves THIS worktree's
      session record via the session resolver — the one useful behavior the
      retired ``session-note`` command used to carry, now folded into the
      canonical reader.
  errors:
    - a malformed RECORD_ID (no '/', not 'session') → non-zero + stderr.
    - a nonexistent record → non-zero + stderr.
    - 'session' with no resolvable session → non-zero + stderr.

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern). Never writes
to the real vault: the CLI resolves the test vault from a seeded config.json
(isolated XDG_CONFIG_HOME) and XDG_STATE_HOME is fenced too.
"""
from __future__ import annotations

import json
from pathlib import Path

from conftest import make_vault as _make_vault, run_cli as _run, write_default_config  # noqa: F401

# A canonical UUID-shaped session_id (Claude Code session IDs are UUIDs).
SID = "11111111-2222-4333-8444-555555555555"


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
# current session: `record show session`
# ---------------------------------------------------------------------------

def test_show_session_resolves_current(tmp_path):
    vault, state = _make_vault(tmp_path)
    # Born-dirty session via a candidate, keyed by session-id.
    c = _run(
        ["session", "candidate", "--session-id", SID, "--kind", "spec",
         "--phase", "Plan"],
        vault=vault, state_dir=state, stdin_text="a candidate finding\n",
    )
    assert c.returncode == 0, f"candidate failed: {c.stderr}"

    r = _run(
        ["record", "show", "session", "--session-id", SID, "--json"],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, f"show session failed: {r.stderr}"
    payload = json.loads(r.stdout)
    assert payload["record_id"] == f"session/{SID}"
    assert payload["kind"] == "session"
    assert "a candidate finding" in payload["body"]
    # The sidecar is how flush reads status / the flushed-at watermark.
    assert isinstance(payload["sidecar"], dict)
    assert payload["sidecar"]


def test_show_session_plain_prints_body(tmp_path):
    vault, state = _make_vault(tmp_path)
    _run(
        ["session", "candidate", "--session-id", SID, "--kind", "spec",
         "--phase", "Plan"],
        vault=vault, state_dir=state, stdin_text="candidate body here\n",
    )
    r = _run(
        ["record", "show", "session", "--session-id", SID],
        vault=vault, state_dir=state,
    )
    assert r.returncode == 0, f"show session failed: {r.stderr}"
    assert "candidate body here" in r.stdout


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

def test_show_invalid_record_id(tmp_path):
    vault, state = _make_vault(tmp_path)
    r = _run(["record", "show", "notaslug"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert r.stderr.strip()


def test_show_nonexistent_record(tmp_path):
    vault, state = _make_vault(tmp_path)
    r = _run(["record", "show", "spec/does-not-exist"], vault=vault, state_dir=state)
    assert r.returncode != 0
    assert r.stderr.strip()


def test_show_session_no_session(tmp_path):
    vault, state = _make_vault(tmp_path)
    r = _run(
        ["record", "show", "session", "--session-id", SID],
        vault=vault, state_dir=state,
    )
    assert r.returncode != 0
    assert r.stderr.strip()
