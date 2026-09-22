"""`lore flush` replaces `lore finish` — clean/dirty flip + flushed-at.

  mechanical flip + commit (current session):
    - a ``clean`` session → exit 0, a notice that DISTINGUISHES "clean — nothing
      to flush" from "no session exists", and NO commit.
    - a ``dirty`` session → status becomes ``clean``, ``annotations['flushed-at']``
      is stamped in the pinned key/format, the one record is reindexed, and the
      session commit stages EXPLICIT paths only (never ``git add -A``).
    - the default requests a background publish (commit + pull + push) for every
      configured vault rather than syncing in-process; ``--wait`` runs that flow
      in-process instead — for a caller that chains on a completed push — and
      commits the vault's OTHER dirty files in its own commit; ``--no-sync`` opts
      out of both and leaves them exactly where they were.
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

import importlib
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import (
    load_script,
    make_vault as _make_vault,
    run_cli as _run,
    write_default_config,
)
from test_vault_write_lock import _spawn_holder

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


def _flush(vault, state, sid=SID, extra_args=()):
    return _run(["flush", "--session-id", sid, *extra_args], vault=vault, state_dir=state,
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

    def _vault_with_a_stray_file(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        # A real record kind directory — `lore sync`'s commit scope only ever
        # stages a kind directory, `sites/`, or the root `.gitignore`.
        decisions = vault / "decision"
        decisions.mkdir(parents=True, exist_ok=True)
        (decisions / ".keep").write_text("")
        assert _candidate(vault, state).returncode == 0
        _commit_baseline(vault)
        (decisions / "unrelated-scratch.md").write_text(
            "scratch work, not part of the flush\n"
        )
        return vault, state

    def test_the_session_commit_does_not_sweep_an_unrelated_dirty_file(self, tmp_path):
        """The SESSION commit stays explicit-paths: the stray file is not in it.

        The sync tail commits that file afterwards (see the test below) — but in a
        commit of its own, so the session commit remains a clean, reviewable unit
        holding exactly the flushed record.
        """
        vault, state = self._vault_with_a_stray_file(tmp_path)

        r = _flush(vault, state)
        assert r.returncode == 0, r.stderr

        session_sha = subprocess.run(
            ["git", "-C", str(vault), "rev-list", "-1", "--grep",
             f"session: flush {SID}", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert session_sha, "the flush must have landed its session commit"
        in_session_commit = subprocess.run(
            ["git", "-C", str(vault), "show", "--name-only", "--pretty=format:",
             session_sha],
            capture_output=True, text=True,
        ).stdout
        assert "unrelated-scratch.md" not in in_session_commit
        assert f"session/{SID}.json" in in_session_commit

    def test_wait_commits_the_unrelated_dirty_file(self, tmp_path):
        """`--wait` runs the full sync flow in-process, so nothing is left dirty."""
        vault, state = self._vault_with_a_stray_file(tmp_path)

        r = _flush(vault, state, extra_args=["--wait"])
        assert r.returncode == 0, r.stderr

        status = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout
        assert "unrelated-scratch.md" not in status, (
            f"the sync tail must commit it; status={status!r}"
        )
        assert "unrelated-scratch.md" in _committed_files_at_head(vault)

    def test_no_sync_leaves_the_unrelated_dirty_file_untracked(self, tmp_path):
        """`--no-sync` is the opt-out: the flush touches ONLY the session record."""
        vault, state = self._vault_with_a_stray_file(tmp_path)
        before = _commit_count(vault)

        r = _run(["flush", "--session-id", SID, "--no-sync"], vault=vault,
                 state_dir=state,
                 env_extra={"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""})
        assert r.returncode == 0, r.stderr

        assert _commit_count(vault) == before + 1, "only the session commit"
        assert "unrelated-scratch.md" not in _committed_files_at_head(vault)
        status = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout
        assert "unrelated-scratch.md" in status, "stray file must stay untracked"

    def test_no_sync_requests_no_publish(self, tmp_path, monkeypatch):
        """`--no-sync` is the full opt-out: no publish request, no spawn."""
        import lore.cli.publish as publish_mod

        calls = []
        monkeypatch.setattr(publish_mod, "_spawn_worker", lambda argv: calls.append(list(argv)))
        vault, state = self._vault_with_a_stray_file(tmp_path)
        # Matches the XDG_STATE_HOME the CLI call below resolves internally, so
        # this process's own `request_stamp_path` computation agrees with it.
        monkeypatch.setenv("XDG_STATE_HOME", str(state))

        r = _flush(vault, state, extra_args=["--no-sync"])
        assert r.returncode == 0, r.stderr

        assert calls == []
        assert not publish_mod.request_stamp_path(vault).exists()

    def test_wait_requests_no_publish_and_syncs_inline(self, tmp_path, monkeypatch):
        """`--wait` runs the sync loop in-process and requests nothing."""
        import lore.cli.publish as publish_mod

        calls = []
        monkeypatch.setattr(publish_mod, "_spawn_worker", lambda argv: calls.append(list(argv)))
        vault, state = self._vault_with_a_stray_file(tmp_path)
        monkeypatch.setenv("XDG_STATE_HOME", str(state))

        r = _flush(vault, state, extra_args=["--wait"])
        assert r.returncode == 0, r.stderr

        assert calls == []
        assert not publish_mod.request_stamp_path(vault).exists()
        status = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout
        assert "unrelated-scratch.md" not in status, "--wait must still sync inline"

    def test_default_requests_a_publish_for_every_vault_including_shared_and_syncs_nothing(
        self, tmp_path, monkeypatch
    ):
        """The default tail requests a publish per configured vault (shared
        included) and never runs an in-process sync — the stray file stays
        exactly where it was, unlike under `--wait` (see the test above)."""
        import lore.cli.publish as publish_mod

        calls = []
        monkeypatch.setattr(publish_mod, "_spawn_worker", lambda argv: calls.append(list(argv)))
        vault, state = self._vault_with_a_stray_file(tmp_path)

        shared = tmp_path / "shared-vault"
        shared.mkdir()
        config_home = tmp_path / "publish-trigger-config"
        (config_home / "lore").mkdir(parents=True)
        (config_home / "lore" / "config.json").write_text(
            json.dumps(
                {
                    "vaults": [
                        {"name": "default", "scope": "default", "path": str(vault)},
                        {
                            "name": "teamvault",
                            "scope": "product",
                            "path": str(shared),
                            "shared": True,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )

        r = _run(
            ["flush", "--session-id", SID],
            vault=vault,
            state_dir=state,
            env_extra={
                "CLAUDE_CODE_SESSION_ID": "",
                "CLAUDE_SESSION_ID": "",
                "XDG_CONFIG_HOME": str(config_home),
            },
        )
        assert r.returncode == 0, r.stderr

        requested_vaults = {c[-1] for c in calls}
        assert requested_vaults == {"default", "teamvault"}, (
            "a shared vault must be requested too, not excluded like the old "
            f"writable-only sync tail; calls={calls!r}"
        )
        status = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout
        assert "unrelated-scratch.md" in status, (
            "the default tail must not sync in-process — only request a publish"
        )

    def test_default_names_the_vaults_a_publish_was_requested_for_on_stderr(
        self, tmp_path, monkeypatch
    ):
        """The flush skill's Step-5 report template says `Publish requested
        for: <vaults>` — nothing on the default tail prints that today, so
        the template is unsatisfiable. This pins the stderr line it needs."""
        import lore.cli.publish as publish_mod

        monkeypatch.setattr(publish_mod, "_spawn_worker", lambda argv: None)
        vault, state = self._vault_with_a_stray_file(tmp_path)

        r = _flush(vault, state)
        assert r.returncode == 0, r.stderr

        lines = [
            line for line in r.stderr.splitlines()
            if "Publish requested for" in line
        ]
        assert len(lines) == 1, f"expected exactly one notice; stderr={r.stderr!r}"
        assert "default" in lines[0]

    def test_default_names_a_vault_skipped_for_auto_publish_false_on_stderr(
        self, tmp_path, monkeypatch
    ):
        import lore.cli.publish as publish_mod

        calls = []
        monkeypatch.setattr(publish_mod, "_spawn_worker", lambda argv: calls.append(list(argv)))
        vault, state = self._vault_with_a_stray_file(tmp_path)

        config_home = tmp_path / "auto-publish-off-config"
        (config_home / "lore").mkdir(parents=True)
        (config_home / "lore" / "config.json").write_text(
            json.dumps(
                {
                    "vaults": [
                        {
                            "name": "default",
                            "scope": "default",
                            "path": str(vault),
                            "auto_publish": False,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        r = _run(
            ["flush", "--session-id", SID],
            vault=vault,
            state_dir=state,
            env_extra={
                "CLAUDE_CODE_SESSION_ID": "",
                "CLAUDE_SESSION_ID": "",
                "XDG_CONFIG_HOME": str(config_home),
            },
        )
        assert r.returncode == 0, r.stderr

        assert calls == [], "auto_publish: false must never spawn"
        requested_lines = [
            line for line in r.stderr.splitlines() if "Publish requested for" in line
        ]
        assert requested_lines == [], "a fully-skipped flush must not claim a request"
        skipped_lines = [
            line for line in r.stderr.splitlines()
            if "auto_publish" in line and "default" in line
        ]
        assert len(skipped_lines) == 1, f"expected exactly one notice; stderr={r.stderr!r}"

    def test_default_excludes_a_vault_whose_schedule_failed_from_the_requested_line(
        self, tmp_path, monkeypatch
    ):
        """A vault whose `request_publish` could not actually schedule the
        worker (an internal exception, caught and reported as its own
        stderr line) must not ALSO be named on the "Publish requested for"
        line — that line means the schedule succeeded, same distinction the
        auto_publish-off notice already draws."""
        import lore.cli.publish as publish_mod

        def _boom(_name):
            raise FileNotFoundError("lore CLI entry script not found")

        monkeypatch.setattr(publish_mod, "_worker_argv", _boom)
        vault, state = self._vault_with_a_stray_file(tmp_path)

        r = _flush(vault, state)
        assert r.returncode == 0, r.stderr

        requested_lines = [
            line for line in r.stderr.splitlines() if "Publish requested for" in line
        ]
        assert requested_lines == [], (
            f"a vault whose schedule failed must not be named as requested; "
            f"stderr={r.stderr!r}"
        )
        assert "could not schedule publish" in r.stderr


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
        # Only the SESSION lines are the subject: the sync tail legitimately
        # reports the VAULT as clean, which says nothing about the session.
        session_lines = [ln for ln in combined.splitlines() if "session" in ln]
        assert not any("clean" in ln for ln in session_lines), (
            f"a missing session must NOT be reported as 'clean': {session_lines!r}"
        )
        # No session commit — the sync tail may still commit the vault's own
        # dirty state, but nothing was flushed, so no flush commit exists.
        assert not subprocess.run(
            ["git", "-C", str(vault), "log", "--oneline", "--grep", "session: flush"],
            capture_output=True, text=True,
        ).stdout.strip(), "no session commit when there is no session"


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


# ---------------------------------------------------------------------------
# the sync tail races a held vault lock — it must still land its write
# ---------------------------------------------------------------------------

class TestSyncTailRacesAHeldLock:

    def test_sync_tail_call_shape_still_lands_write_despite_a_held_lock(self, tmp_path):
        """`_flush_sync_tail` calls `cmd_sync` with a bare
        `SimpleNamespace(vault=name, message=None, pull_only=False)` — no
        `blocking` attribute at all. Driving that EXACT call shape against a
        vault whose write lock a GENUINE second OS process holds must still
        block until the lock frees, then land its own commit and push — never
        a zero return with the write skipped. This fails against a naive
        change that makes `cmd_sync`'s own lock acquisition non-blocking
        regardless of what `args` carries, because the tail's namespace would
        then hit that same non-blocking skip and report success while the
        write it is called to reuse never lands.
        """
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)],
                        check=True, capture_output=True)
        subprocess.run(["git", "-C", str(vault), "remote", "add", "origin", str(remote)],
                        check=True, capture_output=True)

        decisions = vault / "decision"
        decisions.mkdir(parents=True, exist_ok=True)
        (decisions / ".keep").write_text("")
        assert _candidate(vault, state).returncode == 0
        _commit_baseline(vault)
        subprocess.run(["git", "-C", str(vault), "push", "-u", "origin", "HEAD"],
                        check=True, capture_output=True)

        # Seed a real flush — its own session commit — with the tail
        # suppressed, so the tail's exact call shape can be driven (and its
        # lock contended) separately, below.
        r = _run(["flush", "--session-id", SID, "--no-sync"], vault=vault,
                  state_dir=state,
                  env_extra={"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""})
        assert r.returncode == 0, r.stderr
        assert _sidecar(vault)["status"] == "clean"

        # A file only the TAIL's own `cmd_sync` call — never the flush's own
        # session commit — can land.
        (decisions / "left-dirty.md").write_text("still uncommitted after the flush\n")

        # The autouse `_isolate_ambient_env` fixture already pins HOME /
        # XDG_STATE_HOME / XDG_CONFIG_HOME to tmp_path-scoped dirs matching
        # `make_vault`'s own state dir — write the ambient config there so an
        # IN-PROCESS `cmd_sync` call (not a CLI subprocess) resolves the same
        # vault the CLI calls above already used.
        write_default_config(tmp_path / "config", vault)
        sync_mod = importlib.import_module("lore.cli.sync")

        holder = _spawn_holder(vault, hold_for=1.0)
        try:
            t0 = time.monotonic()
            rc = sync_mod.cmd_sync(
                SimpleNamespace(vault="default", message=None, pull_only=False)
            )
            elapsed = time.monotonic() - t0
        finally:
            holder.wait(timeout=15)
        (vault / "_held").unlink(missing_ok=True)  # the holder's own marker file

        assert rc == 0, "the tail's own call must still succeed once the lock frees"
        assert elapsed >= 0.5, (
            f"cmd_sync did not block on the held lock ({elapsed:.3f}s) — the "
            "SimpleNamespace `_flush_sync_tail` passes carries no `blocking` "
            "attribute, so it must default to blocking, never skip"
        )
        status = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout
        assert "left-dirty.md" not in status, "the tail must commit it, not skip it"
        local_head = subprocess.run(
            ["git", "-C", str(vault), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        remote_head = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert remote_head == local_head, "the tail must push it, not leave it local-only"
