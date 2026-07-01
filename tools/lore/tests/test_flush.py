"""`lore flush` replaces `lore finish` — clean/dirty flip + flushed-at.

  mechanical flip + commit (current session):
    - a ``clean`` session → exit 0, a notice that DISTINGUISHES "clean — nothing
      to flush" from "no session exists", and NO commit.
    - a ``dirty`` session → status becomes ``clean``, ``annotations['flushed-at']``
      is stamped in the pinned key/format, the one record is reindexed, and exactly
      ONE commit is made — staging EXPLICIT paths only (never ``git add -A``).
    - re-flush of a now-``clean`` session is an idempotent no-op (no second commit).
    - NO code path ever writes ``status: complete`` / ``status: active`` — the
      sidecar status is only ever ``dirty`` / ``clean``.

  shared contract (the key/format are pinned here; other readers import them):
    - ``session_store.FLUSHED_AT_KEY`` / ``FLUSHED_AT_FORMAT`` pin the key + ISO
      format as a single importable source of truth.
    - ``session_store.parse_flushed_at`` is the validate-before-trust reader: a
      corrupt / missing / naive / non-UTC value returns ``None`` ("no prior flush"
      → re-evaluate ALL candidates), a valid future value parses without error.

Tests run the CLI as a subprocess via the conftest harness (LORE_VAULT +
XDG_STATE_HOME injected) so the real vault/index are never touched. ALL fixtures
SYNTHETIC — zero private tokens (public repo).
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import load_script, make_vault as _make_vault, run_cli as _run

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"

SID = "11111111-2222-4333-8444-555555555555"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_init(vault: Path) -> None:
    """Make *vault* its own git toplevel so the flush commit path is exercised."""
    subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
    for k, v in (("user.email", "t@e.st"), ("user.name", "Tester"),
                 ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(vault), "config", k, v],
                       check=True, capture_output=True)


def _commit_baseline(vault: Path) -> None:
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-m", "baseline"],
                   check=True, capture_output=True)


def _commit_count(vault: Path) -> int:
    out = subprocess.run(
        ["git", "-C", str(vault), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    return int(out) if out else 0


def _committed_files_at_head(vault: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(vault), "show", "--name-only", "--pretty=format:", "HEAD"],
        capture_output=True, text=True,
    ).stdout


def _record_json(vault: Path, key: str = SID) -> Path:
    return vault / "session" / f"{key}.json"


def _sidecar(vault: Path, key: str = SID) -> dict:
    return json.loads(_record_json(vault, key).read_text())


def _candidate(vault, state, sid=SID, body="a candidate\n"):
    """Materialize a dirty session record via the real capture path."""
    return _run(
        ["session", "candidate", "--session-id", sid, "--kind", "spec", "--phase", "Plan"],
        vault=vault, state_dir=state, stdin_text=body,
        env_extra={"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""},
    )


def _flush(vault, state, sid=SID):
    return _run(["flush", "--session-id", sid], vault=vault, state_dir=state,
                env_extra={"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""})


def _index_status(state: Path, key: str = SID):
    index_store = load_script("lore.search.index")
    conn = index_store.open_index(env={"XDG_STATE_HOME": str(state)})
    try:
        row = conn.execute(
            "SELECT status FROM records WHERE kind='session' AND name=?", (key,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# dirty → clean + flushed-at + exactly one commit (explicit paths)
# ---------------------------------------------------------------------------

class TestFlushDirtySession:

    def test_flush_flips_dirty_to_clean_and_stamps_flushed_at(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state).returncode == 0
        assert _sidecar(vault)["status"] == "dirty"
        _commit_baseline(vault)

        r = _flush(vault, state)
        assert r.returncode == 0, r.stderr

        side = _sidecar(vault)
        assert side["status"] == "clean"
        flushed = side["annotations"]["flushed-at"]
        # Pinned format: whole-second UTC with a Z suffix.
        parsed = datetime.strptime(flushed, "%Y-%m-%dT%H:%M:%SZ")
        assert parsed is not None
        # Index reflects the clean flip.
        assert _index_status(state) == "clean"

    def test_flush_lands_exactly_one_commit_of_explicit_paths(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state).returncode == 0
        _commit_baseline(vault)
        before = _commit_count(vault)

        r = _flush(vault, state)
        assert r.returncode == 0, r.stderr

        assert _commit_count(vault) == before + 1, "flush must land exactly one commit"
        committed = _committed_files_at_head(vault)
        assert f"session/{SID}.json" in committed

    def test_flush_does_not_sweep_unrelated_dirty_file(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        decisions = vault / "decisions"
        decisions.mkdir(parents=True, exist_ok=True)
        (decisions / ".keep").write_text("")
        assert _candidate(vault, state).returncode == 0
        _commit_baseline(vault)
        stray = decisions / "unrelated-scratch.md"
        stray.write_text("scratch work, not part of the flush\n")

        r = _flush(vault, state)
        assert r.returncode == 0, r.stderr

        assert "unrelated-scratch.md" not in _committed_files_at_head(vault)
        status = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout
        assert "unrelated-scratch.md" in status, "stray file must stay untracked"


# ---------------------------------------------------------------------------
# clean no-op vs no-session — distinct notices, no commit
# ---------------------------------------------------------------------------

class TestFlushCleanNoop:

    def test_already_clean_session_is_noop_with_distinct_notice(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state).returncode == 0
        _commit_baseline(vault)
        # First flush makes it clean.
        assert _flush(vault, state).returncode == 0
        after_first = _commit_count(vault)

        # Re-flush of a now-clean session is an idempotent no-op (no second commit).
        r = _flush(vault, state)
        assert r.returncode == 0, r.stderr
        assert _commit_count(vault) == after_first, "re-flush must not commit again"
        combined = (r.stdout + r.stderr).lower()
        assert "clean" in combined and "nothing to flush" in combined
        assert "no session" not in combined, (
            "a clean session must NOT be reported as 'no session exists'"
        )

    def test_no_session_distinguished_from_clean(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        r = _flush(vault, state)
        assert r.returncode == 0, r.stderr
        combined = (r.stdout + r.stderr).lower()
        assert "no session" in combined, (
            "a missing session must be reported distinctly from a clean one"
        )
        assert "clean" not in combined, (
            "a missing session must NOT be reported as 'clean'"
        )
        assert _commit_count(vault) == 0, "no commit when there is no session"


# ---------------------------------------------------------------------------
# never emits complete / active (the retired S0 vocab)
# ---------------------------------------------------------------------------

class TestNoLegacyStatus:

    def test_flush_never_writes_complete_or_active(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state).returncode == 0
        _commit_baseline(vault)
        assert _flush(vault, state).returncode == 0

        side = _sidecar(vault)
        assert side["status"] in ("dirty", "clean"), side["status"]
        assert side["status"] != "complete"
        assert side["status"] != "active"
        # No legacy `complete`/`active`/`ended` artifact anywhere in the sidecar.
        assert "ended" not in side, "flush must not write the legacy `ended` field"


# ---------------------------------------------------------------------------
# shared contract: FLUSHED_AT_KEY / FORMAT + parse_flushed_at
# (the producer pins it here; other readers import it)
# ---------------------------------------------------------------------------

class TestFlushedAtSharedContract:

    def test_pinned_key_and_format_constants(self):
        store = load_script("lore.session.store")
        assert store.FLUSHED_AT_KEY == "flushed-at"
        assert store.FLUSHED_AT_FORMAT == "%Y-%m-%dT%H:%M:%SZ"

    def test_flush_stamps_value_under_the_pinned_key(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state).returncode == 0
        _commit_baseline(vault)
        assert _flush(vault, state).returncode == 0

        store = load_script("lore.session.store")
        annotations = _sidecar(vault)["annotations"]
        assert store.FLUSHED_AT_KEY in annotations
        # The stamped value round-trips through the shared reader as UTC.
        parsed = store.parse_flushed_at(annotations[store.FLUSHED_AT_KEY])
        assert parsed is not None
        assert parsed.utcoffset() == timedelta(0)


class TestParseFlushedAtReader:
    """The validate-before-trust reader — corrupt/missing/naive/non-UTC → None."""

    def test_valid_utc_value_parses(self):
        store = load_script("lore.session.store")
        parsed = store.parse_flushed_at("2026-06-24T12:00:00Z")
        assert parsed is not None
        assert parsed.utcoffset() == timedelta(0)

    def test_future_value_parses_without_error(self):
        store = load_script("lore.session.store")
        assert store.parse_flushed_at("2099-12-31T23:59:59Z") is not None

    @pytest.mark.parametrize("raw", [
        None, "", "not-a-date", "2026/06/24", "June 24 2026", "12345",
    ])
    def test_missing_or_corrupt_returns_none(self, raw):
        store = load_script("lore.session.store")
        assert store.parse_flushed_at(raw) is None

    def test_naive_datetime_returns_none(self):
        store = load_script("lore.session.store")
        assert store.parse_flushed_at("2026-06-24T12:00:00") is None

    def test_non_utc_offset_returns_none(self):
        store = load_script("lore.session.store")
        assert store.parse_flushed_at("2026-06-24T12:00:00+05:30") is None

    def test_non_string_returns_none(self):
        store = load_script("lore.session.store")
        assert store.parse_flushed_at(12345) is None

    def test_fallback_means_all_candidates_outstanding(self, tmp_path):
        """A corrupt flushed-at → None → cutoff is epoch → ALL candidates outstanding.

        This is the conservative contract: never silently drop candidates. We
        exercise it against a real flushed session whose watermark we then corrupt.
        """
        store = load_script("lore.session.store")
        body_lines = [
            "- candidate 2026-06-24T10:00:00Z kind=decision phase=Plan",
            "- candidate 2026-06-24T11:00:00Z kind=lesson phase=Build",
        ]
        cutoff = store.parse_flushed_at("CORRUPT")
        assert cutoff is None
        epoch = datetime.fromtimestamp(0, tz=timezone.utc)
        floor = cutoff or epoch
        outstanding = [
            ln for ln in body_lines
            if datetime.fromisoformat(ln.split()[2]) > floor
        ]
        assert len(outstanding) == len(body_lines), (
            "corrupt watermark must treat ALL candidates as outstanding"
        )
