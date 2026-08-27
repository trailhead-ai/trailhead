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

  ``lore session show`` (read THIS worktree's session record):
    - ``--json`` emits {record_id, kind, name, sidecar (dict), body} — the
      sidecar is how flush reads status / the ``flushed-at`` watermark.
    - plain prints the body; an unresolvable session → non-zero + a diagnostic.

Tests run the CLI as a subprocess via CLI_PATH (conftest pattern) and load the
``session_store`` module directly for the concurrent-race + sanitizer unit tests.
Never writes to the real vault: the CLI resolves the test vault from a seeded
config.json (isolated XDG_CONFIG_HOME) and XDG_STATE_HOME is fenced too.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import (  # noqa: F401
    load_script,
    make_vault as _make_vault,
    run_cli as _run,
    run_cli_with_silent_pipe as _run_silent_pipe,
    write_default_config,
)

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
        store = load_script("lore.session.store")
        with pytest.raises(store.InvalidSessionIdError):
            store.sanitize_session_id("11111111-2222-4333-8444-55555555\x005")

    @pytest.mark.parametrize("bad", ["", "not-a-guid", "../x", "a/b", "..", "."])
    def test_sanitizer_rejects_non_guid(self, bad):
        store = load_script("lore.session.store")
        with pytest.raises(store.InvalidSessionIdError):
            store.sanitize_session_id(bad)

    def test_sanitizer_accepts_canonical_guid(self):
        store = load_script("lore.session.store")
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

    def test_candidate_silent_open_stdin_refuses_instead_of_blocking(self, tmp_path):
        """A real, open, never-EOF'ing pipe on stdin must not hang ``session candidate``.

        Regression test for lesson/lore-record-update-blocks-forever-on-a-
        silent-open-stdin (the pattern generalizes to every
        ``_read_stdin_body`` caller, ``session candidate`` included).
        """
        vault, state = _make_vault(tmp_path)
        note_path = _session_note(vault, SID)
        assert not note_path.exists()

        r, elapsed = _run_silent_pipe(
            ["session", "candidate", "--session-id", SID,
             "--kind", "spec", "--phase", "Plan"],
            # Deliberately far longer than any realistic refusal check: proves
            # the command returns without waiting out the pipe, rather than
            # merely returning faster than a tight number.
            vault=vault, state_dir=state, timeout=60.0,
        )

        assert elapsed < 15.0, f"took {elapsed}s — looks like it blocked on stdin"
        assert r.returncode != 0
        assert "stdin" in r.stderr.lower()
        assert not note_path.exists()  # no write happened

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
# --vault: explicit destination-vault targeting for ``session candidate``
# ---------------------------------------------------------------------------


def _write_config(config_home: Path, vaults: list) -> Path:
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    cfg_path = lore_cfg / "config.json"
    cfg_path.write_text(json.dumps({"vaults": vaults}, indent=2), encoding="utf-8")
    return cfg_path


def _run_cfg(args, *, vault, state, config_home, stdin_text=None):
    return _run(
        args, vault=vault, state_dir=state, stdin_text=stdin_text,
        env_extra={"XDG_CONFIG_HOME": str(config_home)},
    )


class TestSessionCandidateVaultFlag:

    def test_vault_flag_materializes_in_named_vault_not_active(self, tmp_path):
        """``--vault beta`` dirties/materializes the session record under
        beta's ``session/`` dir, not the active-vault resolution's target."""
        active_vault, state = _make_vault(tmp_path)
        beta_vault = tmp_path / "vault_beta"
        beta_vault.mkdir(parents=True)
        config_home = tmp_path / "config"
        _write_config(
            config_home,
            [
                {"name": "default", "scope": "default", "path": str(active_vault)},
                {"name": "beta", "scope": "team", "path": str(beta_vault)},
            ],
        )

        r = _run_cfg(
            ["session", "candidate", "--session-id", SID, "--kind", "spec",
             "--phase", "Plan", "--vault", "beta"],
            vault=active_vault, state=state, config_home=config_home,
            stdin_text="a candidate finding\n",
        )
        assert r.returncode == 0, f"candidate failed: {r.stderr}"
        assert _session_note(beta_vault, SID).exists()
        assert not _session_note(active_vault, SID).exists()
        assert "a candidate finding" in _session_note(beta_vault, SID).read_text()

    def test_vault_flag_unknown_name_errors_before_any_write(self, tmp_path):
        """An unconfigured ``--vault`` name errors nonzero before any write --
        no session note materializes anywhere."""
        active_vault, state = _make_vault(tmp_path)
        config_home = tmp_path / "config"
        _write_config(
            config_home,
            [{"name": "default", "scope": "default", "path": str(active_vault)}],
        )

        r = _run_cfg(
            ["session", "candidate", "--session-id", SID, "--kind", "spec",
             "--phase", "Plan", "--vault", "nope"],
            vault=active_vault, state=state, config_home=config_home,
            stdin_text="a candidate finding\n",
        )
        assert r.returncode != 0
        assert r.stderr.startswith("lore: ")
        assert not _session_note(active_vault, SID).exists()

    def test_vault_flag_composes_with_session_id(self, tmp_path):
        """``--vault`` selects which vault; ``--session-id`` still selects
        which session key -- the two compose independently."""
        active_vault, state = _make_vault(tmp_path)
        beta_vault = tmp_path / "vault_beta"
        beta_vault.mkdir(parents=True)
        config_home = tmp_path / "config"
        _write_config(
            config_home,
            [
                {"name": "default", "scope": "default", "path": str(active_vault)},
                {"name": "beta", "scope": "team", "path": str(beta_vault)},
            ],
        )
        other_sid = "22222222-3333-4444-8555-666666666666"

        r = _run_cfg(
            ["session", "candidate", "--session-id", other_sid, "--kind", "spec",
             "--phase", "Plan", "--vault", "beta"],
            vault=active_vault, state=state, config_home=config_home,
            stdin_text="finding\n",
        )
        assert r.returncode == 0, r.stderr
        assert _session_note(beta_vault, other_sid).exists()
        assert not _session_note(beta_vault, SID).exists()

    def test_vault_flag_omitted_preserves_active_vault_resolution(self, tmp_path):
        """Omitting ``--vault`` preserves the existing active-vault-resolution
        behavior unchanged."""
        vault, state = _make_vault(tmp_path)
        r = _run(
            ["session", "candidate", "--session-id", SID, "--kind", "spec",
             "--phase", "Plan"],
            vault=vault, state_dir=state, stdin_text="a candidate finding\n",
        )
        assert r.returncode == 0, f"candidate failed: {r.stderr}"
        assert _session_note(vault, SID).exists()


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
        # The session subparser requires an action (candidate/referenced/show).
        assert "candidate" in (r.stderr + r.stdout) or "referenced" in (
            r.stderr + r.stdout
        )


# ---------------------------------------------------------------------------
# ``lore session show`` — read THIS worktree's session record
# ---------------------------------------------------------------------------

class TestSessionShow:

    def test_show_json_emits_sidecar_and_body(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        # Born-dirty session via a candidate, keyed by session-id.
        c = _run(
            ["session", "candidate", "--session-id", SID, "--kind", "spec",
             "--phase", "Plan"],
            vault=vault, state_dir=state, stdin_text="a candidate finding\n",
        )
        assert c.returncode == 0, f"candidate failed: {c.stderr}"

        r = _run(
            ["session", "show", "--session-id", SID, "--json"],
            vault=vault, state_dir=state,
        )
        assert r.returncode == 0, f"session show failed: {r.stderr}"
        payload = json.loads(r.stdout)
        assert payload["record_id"] == f"session/{SID}"
        assert payload["kind"] == "session"
        assert "a candidate finding" in payload["body"]
        # The sidecar is how flush reads status / the flushed-at watermark.
        assert isinstance(payload["sidecar"], dict)
        assert payload["sidecar"]

    def test_show_plain_prints_body(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _run(
            ["session", "candidate", "--session-id", SID, "--kind", "spec",
             "--phase", "Plan"],
            vault=vault, state_dir=state, stdin_text="candidate body here\n",
        )
        r = _run(
            ["session", "show", "--session-id", SID],
            vault=vault, state_dir=state,
        )
        assert r.returncode == 0, f"session show failed: {r.stderr}"
        assert "candidate body here" in r.stdout

    def test_show_no_session_is_diagnostic_miss(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        r = _run(
            ["session", "show", "--session-id", SID],
            vault=vault, state_dir=state,
        )
        assert r.returncode != 0
        # The diagnostic names what was tried so callers don't go spelunking.
        assert "no session record resolved" in r.stderr


# ---------------------------------------------------------------------------
# ``lore session show`` — the key is sanitized BEFORE any path is constructed
# ---------------------------------------------------------------------------

class TestSessionShowKeyConfinement:
    """`show` builds `session/<key>.{md,json}` from caller-supplied selectors.

    An ABSOLUTE `--session-id` resets a `pathlib` join, so `/etc/passwd` probed
    `/etc/passwd.md` outside the vault entirely; a `../` key walked out the same
    way. The read path therefore runs the selectors through the SAME sanitizers
    the write paths use — rejected non-zero with a plain `error:` line, never a
    traceback.
    """

    @pytest.mark.parametrize(
        "selector,value",
        [
            ("--session-id", "/etc/passwd"),
            ("--session-id", "../../etc/passwd"),
            ("--worktree", "../x"),
            ("--worktree", "/etc"),
        ],
    )
    def test_off_shape_key_is_rejected(self, tmp_path, selector, value):
        vault, state = _make_vault(tmp_path)
        r = _run(
            ["session", "show", selector, value], vault=vault, state_dir=state,
            # No ambient session id: the `--worktree` cases must exercise the
            # worktree key, not fall back to the harness's own session GUID.
            env_extra={"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""},
        )
        assert r.returncode != 0, r.stdout
        assert "Traceback" not in r.stderr, r.stderr
        assert r.stderr.strip().startswith("error:"), r.stderr
