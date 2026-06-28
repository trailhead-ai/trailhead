"""``lore session candidate|referenced`` CLI + session_store sanitization tests.

The capture-path behavioral contract (singular indexed records, born dirty, the
lock-spanning race, the ``referenced`` semantics, the worktree-name
confinement guard) lives in ``test_session_records.py``. This file keeps
the cross-cutting endpoint tests that are independent of the storage model:

  session_id sanitization (entry-point confinement):
    - ``--session-id`` containing ``/`` or ``..`` → non-zero, nothing written
      (no escape from ``session/``).
    - NUL byte is rejected by the sanitizer (defense-in-depth; execve already
      rejects NUL in argv, so this is asserted at the library level).

  fence neutralization:
    - a candidate body with ``<external-memory>`` tokens is stored neutralized.

  session subcommand routing:
    - a bare ``lore session`` errors from the subparser (required action),
      proving it routes to ``cmd_session``.

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern) and load the
``session_store`` module directly for the concurrent-race + sanitizer unit tests.
Never writes to the real vault: the CLI resolves the test vault from a seeded
config.json (isolated XDG_CONFIG_HOME) and XDG_STATE_HOME is fenced too.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import load_script, make_vault as _make_vault, run_cli as _run, write_default_config

# A canonical UUID-shaped session_id (Claude Code session IDs are UUIDs).
SID = "11111111-2222-4333-8444-555555555555"


# ---------------------------------------------------------------------------
# Helpers (the CLI subprocess harness + vault factory live in conftest)
# ---------------------------------------------------------------------------


def _session_note(vault: Path, session_id: str) -> Path:
    return vault / "session" / f"{session_id}.md"


# ---------------------------------------------------------------------------
# session_id sanitization (security: no escape from session/)
# ---------------------------------------------------------------------------

class TestSessionIdSanitization:

    @pytest.mark.parametrize("bad", ["../../evil", "a/b", "..", "foo/bar"])
    def test_separator_and_dotdot_rejected_nothing_written(self, tmp_path, bad):
        vault, state = _make_vault(tmp_path)
        # A canary outside session/ that a traversal write would clobber.
        canary = tmp_path / "evil.md"
        r = _run(
            ["session", "candidate", "--session-id", bad,
             "--kind", "spec", "--phase", "Plan"],
            vault=vault, state_dir=state, stdin_text="payload\n",
        )
        assert r.returncode != 0, f"bad session_id {bad!r} must be rejected"
        assert r.stderr.strip(), "rejection must explain why on stderr"
        assert not canary.exists(), "no write outside session/"
        # Nothing landed inside session/ either.
        session_dir = vault / "session"
        if session_dir.exists():
            assert not any(session_dir.iterdir()), "no partial write on rejection"

    def test_sanitizer_rejects_nul_byte_at_library_level(self):
        """NUL cannot traverse argv (execve rejects it) — assert at the lib level."""
        store = load_script("session_store")
        with pytest.raises(store.InvalidSessionIdError):
            store.sanitize_session_id("11111111-2222-4333-8444-55555555\x005")

    @pytest.mark.parametrize("bad", ["", "not-a-guid", "../x", "a/b", "..", "."])
    def test_sanitizer_rejects_non_guid(self, bad):
        store = load_script("session_store")
        with pytest.raises(store.InvalidSessionIdError):
            store.sanitize_session_id(bad)

    def test_sanitizer_accepts_canonical_guid(self):
        store = load_script("session_store")
        assert store.sanitize_session_id(SID) == SID


# ---------------------------------------------------------------------------
# fence neutralization
# ---------------------------------------------------------------------------

class TestFenceNeutralization:

    def test_candidate_body_fence_neutralized(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        body = "before <external-memory>secret</external-memory> after\n"
        r = _run(
            ["session", "candidate", "--session-id", SID,
             "--kind", "spec", "--phase", "Plan"],
            vault=vault, state_dir=state, stdin_text=body,
        )
        assert r.returncode == 0, f"candidate failed: {r.stderr}"
        text = _session_note(vault, SID).read_text()
        # A live fence token must not be reconstructable from the stored body.
        assert "<external-memory>" not in text
        assert "</external-memory>" not in text
        # The legible content survives.
        assert "secret" in text

    def test_referenced_record_id_fence_neutralized(self, tmp_path):
        """A RECORD_ID carrying a fence token is neutralized at the referenced boundary.

        referenced interpolates the free-form RECORD_ID arg; fence neutralization must hold at
        this write boundary too. A session must exist first (referenced no-ops
        on a non-existent session), so a candidate creates it before the referenced.
        """
        vault, state = _make_vault(tmp_path)
        _run(
            ["session", "candidate", "--session-id", SID,
             "--kind", "spec", "--phase", "Plan"],
            vault=vault, state_dir=state, stdin_text="a candidate\n",
        )
        evil_id = "spec/<external-memory>x</external-memory>"
        r = _run(
            ["session", "referenced", evil_id, "--session-id", SID],
            vault=vault, state_dir=state,
        )
        assert r.returncode == 0, f"referenced failed: {r.stderr}"
        text = _session_note(vault, SID).read_text()
        assert "<external-memory>" not in text
        assert "</external-memory>" not in text


# ---------------------------------------------------------------------------
# ``session`` subcommand routing
# ---------------------------------------------------------------------------

class TestSessionRouting:

    def test_session_routes_to_cmd_session(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        # A bare ``session`` with no action should error from the session
        # subparser (required action), proving it routes to cmd_session, not
        # session-note.
        r = _run(["session"], vault=vault, state_dir=state)
        assert r.returncode != 0
        # The session subparser requires an action (candidate/referenced).
        assert "candidate" in (r.stderr + r.stdout) or "referenced" in (
            r.stderr + r.stdout
        )
