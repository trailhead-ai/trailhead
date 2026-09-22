"""`lore flush` scoping — `all` + `<search>`.

  two positional scopes on ``lore flush`` (the no-arg form flushes the current
  session):
    - ``lore flush all`` (the literal reserved token ``all``) → discover every
      ``dirty`` session via the search facade (``kind:session status:dirty``) and
      flush each; ``clean`` sessions are left untouched.
    - ``lore flush <search>`` → treat any OTHER positional as a KQL query, run it
      via the existing facade, INTERSECT with ``dirty`` (only dirty matches are
      flushed); an empty/non-matching set is a clean no-op.
    - a bare ``all`` routes to the all-scope, NOT a KQL parse (real KQL queries are
      field-qualified, e.g. ``status:dirty`` — a bare ``all`` is never a query).

  per-session atomicity:
    - a named fault-injection mechanism (monkeypatch the per-session commit to
      raise AFTER the first session) proves earlier sessions stay ``clean``, the
      failed session is NAMED with retry-safe guidance, and the command exits
      non-zero.

  injection safety:
    - a ``<search>`` KQL string containing ``'; DROP TABLE …`` produces no SQL
      error / injection — the negative test names the existing ``kql_compile``
      guard (values are BIND params, never string-interpolated).

Tests run the CLI as a subprocess via the conftest harness (LORE_VAULT +
XDG_STATE_HOME injected) so the real vault/index are never touched. ALL fixtures
SYNTHETIC — zero private tokens (public repo).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import NamedTuple

from conftest import (
    load_script,
    make_vault as _make_vault,
    run_cli as _run,
    write_vault_config,
)

# Distinct synthetic session GUIDs.
SID_A = "aaaaaaaa-1111-4111-8111-111111111111"
SID_B = "bbbbbbbb-2222-4222-8222-222222222222"
SID_C = "cccccccc-3333-4333-8333-333333333333"

_NO_AMBIENT_SID = {"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_init(vault: Path) -> None:
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


def _sidecar(vault: Path, key: str) -> dict:
    return json.loads((vault / "session" / f"{key}.json").read_text())


def _candidate(vault, state, sid, body="a candidate\n"):
    """Materialize a dirty session record via the real capture path."""
    return _run(
        ["session", "candidate", "--session-id", sid, "--kind", "spec", "--phase", "Plan"],
        vault=vault, state_dir=state, stdin_text=body,
        env_extra=_NO_AMBIENT_SID,
    )


def _flush(vault, state, *scope, env_extra=None):
    extra = dict(_NO_AMBIENT_SID)
    if env_extra:
        extra.update(env_extra)
    return _run(["flush", *scope], vault=vault, state_dir=state, env_extra=extra)


def _flush_current(vault, state, sid):
    return _run(["flush", "--session-id", sid], vault=vault, state_dir=state,
                env_extra=_NO_AMBIENT_SID)


# ---------------------------------------------------------------------------
# `all` — flush every dirty session, leave clean untouched
# ---------------------------------------------------------------------------

class TestFlushAll:

    def test_all_flushes_every_dirty_session(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state, SID_A).returncode == 0
        assert _candidate(vault, state, SID_B).returncode == 0
        _commit_baseline(vault)

        r = _flush(vault, state, "all")
        assert r.returncode == 0, r.stderr

        assert _sidecar(vault, SID_A)["status"] == "clean"
        assert _sidecar(vault, SID_B)["status"] == "clean"

    def test_all_leaves_already_clean_untouched(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state, SID_A).returncode == 0
        assert _candidate(vault, state, SID_B).returncode == 0
        _commit_baseline(vault)
        # Flush A on its own first → A is now clean.
        assert _flush_current(vault, state, SID_A).returncode == 0
        clean_a = _sidecar(vault, SID_A)
        flushed_at_a = clean_a["annotations"]["flushed-at"]

        r = _flush(vault, state, "all")
        assert r.returncode == 0, r.stderr

        # B got flushed; A's flushed-at watermark is unchanged (no re-flip).
        assert _sidecar(vault, SID_B)["status"] == "clean"
        assert _sidecar(vault, SID_A)["annotations"]["flushed-at"] == flushed_at_a

    def test_all_routes_to_all_scope_not_kql_parse(self, tmp_path):
        """A bare ``all`` is the reserved scope token, never a KQL query.

        ``all`` is not field-qualified, so as a KQL string it would parse as a
        bare full-text term and match NOTHING (no session has the body word
        'all'); routing it through the all-scope flushes the dirty session.
        """
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state, SID_A).returncode == 0
        _commit_baseline(vault)

        r = _flush(vault, state, "all")
        assert r.returncode == 0, r.stderr
        # If `all` were treated as a KQL term it would match nothing and leave the
        # session dirty; the all-scope flips it clean.
        assert _sidecar(vault, SID_A)["status"] == "clean"

    def test_all_empty_vault_is_clean_noop(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        # Build an empty index (reindex with no records).
        assert _run(["reindex"], vault=vault, state_dir=state).returncode == 0

        r = _flush(vault, state, "all")
        assert r.returncode == 0, r.stderr
        assert _commit_count(vault) == 0, "no dirty sessions → no commit"

    def test_all_flushes_more_than_the_search_default_page(self, tmp_path):
        """`all` must flush EVERY dirty session, not the facade's default page (20).

        Discovery passes a high limit + rejects a truncated result, so a vault with
        more dirty sessions than the default `run_search` limit is flushed in full
        rather than silently leaving the overflow dirty (discovery-limit
        correctness — a real vault can hold dozens of session records).
        """
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        n = 25  # > the 20-row default page
        sids = [f"{i:08d}-4444-4444-8444-444444444444" for i in range(n)]
        for sid in sids:
            assert _candidate(vault, state, sid).returncode == 0
        _commit_baseline(vault)

        r = _flush(vault, state, "all")
        assert r.returncode == 0, r.stderr
        for sid in sids:
            assert _sidecar(vault, sid)["status"] == "clean", (
                f"session {sid} was left dirty — discovery capped at the default page"
            )


# ---------------------------------------------------------------------------
# `<search>` — KQL filter intersected with dirty
# ---------------------------------------------------------------------------

class TestFlushSearch:

    def test_search_filters_by_kql_and_intersects_dirty(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state, SID_A).returncode == 0
        assert _candidate(vault, state, SID_B).returncode == 0
        _commit_baseline(vault)
        # Flush B so it is clean; the KQL query matches all sessions but the
        # dirty-intersection must leave B (clean) alone and flush only A.
        assert _flush_current(vault, state, SID_B).returncode == 0

        r = _flush(vault, state, "kind:session")
        assert r.returncode == 0, r.stderr

        assert _sidecar(vault, SID_A)["status"] == "clean"  # was dirty, now flushed
        # B was already clean before the query — untouched (no re-flip).
        assert _sidecar(vault, SID_B)["status"] == "clean"

    def test_search_date_window_this_week_replacement(self, tmp_path):
        """A date-window query (the 'this week' replacement — no dedicated form).

        Two dirty sessions with different ``updated-at``; a
        ``updated-at:>=<cutoff>`` window selects only the recent one. Only the
        in-window dirty session is flushed.
        """
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state, SID_A).returncode == 0
        assert _candidate(vault, state, SID_B).returncode == 0
        _commit_baseline(vault)

        # Backdate A's sidecar to last month and reindex so the window excludes it.
        side_a = _sidecar(vault, SID_A)
        side_a["updated-at"] = "2000-01-01T00:00:00Z"
        (vault / "session" / f"{SID_A}.json").write_text(
            load_script("lore.record.sidecar").dumps(side_a)
        )
        assert _run(["reindex"], vault=vault, state_dir=state).returncode == 0

        # The comparison form is `field >= value` (space-separated, no `:`); a
        # date-only cutoff compares lexicographically against the stored ISO string.
        # This IS the "this week" replacement — a date-window query, no dedicated form.
        r = _flush(vault, state, "kind:session and updated-at >= 2020-01-01")
        assert r.returncode == 0, r.stderr

        # B is recent (in window) → flushed; A is backdated (out of window) → still dirty.
        assert _sidecar(vault, SID_B)["status"] == "clean"
        assert _sidecar(vault, SID_A)["status"] == "dirty"

    def test_search_empty_match_is_noop(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state, SID_A).returncode == 0
        _commit_baseline(vault)
        before = _commit_count(vault)

        r = _flush(vault, state, "kind:decision")  # matches no session
        assert r.returncode == 0, r.stderr
        assert _commit_count(vault) == before, "non-matching query → no commit"
        assert _sidecar(vault, SID_A)["status"] == "dirty", "non-match must not flush"

    def test_search_matching_only_clean_is_noop(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state, SID_A).returncode == 0
        _commit_baseline(vault)
        assert _flush_current(vault, state, SID_A).returncode == 0  # now clean
        before = _commit_count(vault)

        r = _flush(vault, state, "kind:session")  # matches A but A is clean
        assert r.returncode == 0, r.stderr
        assert _commit_count(vault) == before, "only-clean match → no new commit"


# ---------------------------------------------------------------------------
# per-session atomicity — fault injection
# ---------------------------------------------------------------------------

class TestMidBatchFaultInjection:
    """Named fault-injection: monkeypatch the per-session commit to raise AFTER
    the first session, proving earlier sessions stay clean + retry-safe guidance."""

    def _run_with_fault(self, state):
        """Run `lore flush all` with `_flush_commit` patched to raise on the 2nd call.

        Drives the CLI in-process (not subprocess) so the monkeypatch reaches the
        real per-session commit seam. Loads the CLI module by path, isolates the
        index/vault via the same env the conftest harness uses, and patches
        ``cli._flush_commit`` — the per-session commit — to raise after one success.
        """
        import os

        # ``_flush_commit`` (the per-session commit seam) lives in the flush
        # command module; ``build_parser`` is the dispatcher. Patch the seam
        # where it is defined and looked up (``flush._flush_commit``).
        from lore.cli import dispatch, flush

        env = {
            "XDG_STATE_HOME": str(state),
            "XDG_CONFIG_HOME": str(Path(state) / "_xdg_config"),
            "LORE_EMAIL": "tester@example.com",
            **_NO_AMBIENT_SID,
        }

        calls = {"n": 0}
        real_commit = flush._flush_commit

        def flaky_commit(vault_path, key, *, push=True):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("injected mid-batch commit failure")
            return real_commit(vault_path, key, push=push)

        old_environ = dict(os.environ)
        os.environ.update(env)
        flush._flush_commit = flaky_commit
        import io
        import contextlib
        buf_out, buf_err = io.StringIO(), io.StringIO()
        code = None
        try:
            args = dispatch.build_parser().parse_args(["flush", "all"])
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                code = args.func(args)
        finally:
            flush._flush_commit = real_commit
            os.environ.clear()
            os.environ.update(old_environ)
        return code, buf_out.getvalue(), buf_err.getvalue()

    def test_mid_batch_failure_keeps_earlier_clean_names_failed_retry_safe(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state, SID_A).returncode == 0
        assert _candidate(vault, state, SID_B).returncode == 0
        assert _candidate(vault, state, SID_C).returncode == 0
        _commit_baseline(vault)

        code, out, err = self._run_with_fault(state)
        combined = (out + err)

        # Non-zero exit on a mid-batch failure.
        assert code != 0, "a mid-batch failure must exit non-zero"

        # Exactly one session got flushed clean before the fault; the rest are
        # still dirty (atomic per-session — no partial flip).
        statuses = {
            sid: _sidecar(vault, sid)["status"] for sid in (SID_A, SID_B, SID_C)
        }
        cleaned = [sid for sid, st in statuses.items() if st == "clean"]
        assert len(cleaned) == 1, f"exactly one session flushed before fault: {statuses}"

        # The failed session is NAMED.
        failed = [sid for sid, st in statuses.items() if st == "dirty"]
        named = any(sid in combined for sid in failed)
        assert named, f"the failed session must be named in output: {combined!r}"

        # Retry-safe guidance: already-flushed are clean + re-run safely retries.
        low = combined.lower()
        assert "clean" in low, "must state already-flushed sessions are clean"
        assert "re-run" in low or "rerun" in low or "retr" in low, (
            f"must state a re-run safely retries: {combined!r}"
        )


# ---------------------------------------------------------------------------
# batch efficiency — one push per batch, not one per session
# ---------------------------------------------------------------------------

class TestBatchPushesOnce:
    """A `--no-sync` batch flush pushes ONCE for the whole batch, not per session.

    Each per-session commit is still its own atomicity unit; only the network
    push is hoisted out of the loop (`_flush_commit(push=False)` + a single
    trailing `_flush_push`). Drives the CLI in-process so `_git` can be patched
    to (a) report a fake origin remote — the test vault has none — so the push
    path is actually reached, and (b) count pushes while letting add/diff/commit
    run for real, proving N commits but exactly one push.

    `--no-sync` is the form under test because it is the only one that pushes
    here at all: a default flush ends with the sync tail, whose own `lore sync`
    pushes every writable vault, and `_flush_push` stands down for it (pinned by
    :meth:`test_the_sync_tail_owns_the_push_when_it_runs`).
    """

    def _run_counting_pushes(self, state, *, extra_args=("--no-sync",)):
        import contextlib
        import io
        import os

        # The push/remote git calls happen inside ``_flush_push`` /
        # ``_flush_commit`` in the flush command module, which look up ``_git`` as
        # their own module global — so patch ``flush._git`` (not ``common._git``)
        # to intercept them. The vault-is-git-toplevel check runs through the real
        # ``common._git`` and still sees the real repo.
        from lore.cli import dispatch, flush

        env = {
            "XDG_STATE_HOME": str(state),
            "XDG_CONFIG_HOME": str(Path(state) / "_xdg_config"),
            "LORE_EMAIL": "tester@example.com",
            **_NO_AMBIENT_SID,
        }

        real_git = flush._git
        pushes = {"n": 0}

        def counting_git(vault_path, *args):
            # Pretend an origin exists so `_flush_push` reaches the push step…
            if args[:2] == ("remote", "get-url"):
                return (0, "git@example.invalid:fake/vault.git", "")
            # …and count (without actually contacting a network) every push.
            if args[:1] == ("push",):
                pushes["n"] += 1
                return (0, "", "")
            return real_git(vault_path, *args)

        old_environ = dict(os.environ)
        os.environ.update(env)
        flush._git = counting_git
        buf_out, buf_err = io.StringIO(), io.StringIO()
        code = None
        try:
            args = dispatch.build_parser().parse_args(["flush", "all", *extra_args])
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                code = args.func(args)
        finally:
            flush._git = real_git
            os.environ.clear()
            os.environ.update(old_environ)
        return code, pushes["n"], buf_out.getvalue(), buf_err.getvalue()

    def test_the_sync_tail_owns_the_push_when_it_runs(self, tmp_path):
        """A batch whose tail actually SYNCS (`--wait`) must not ALSO push via
        `_flush_push`.

        Two pushes for one set of commits is the parallel-tail shape this slice
        exists to remove: the tail's `lore sync` is the push. The bare-default
        tail (`_flush_request_tail`) never runs an in-process sync at all, so
        exercising this property requires the one tail that does —
        `--wait` (`_flush_sync_tail`).
        """
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        for sid in (SID_A, SID_B):
            assert _candidate(vault, state, sid).returncode == 0
        _commit_baseline(vault)

        code, pushes, out, err = self._run_counting_pushes(state, extra_args=("--wait",))
        assert code == 0, err
        for sid in (SID_A, SID_B):
            assert _sidecar(vault, sid)["status"] == "clean"
        assert pushes == 0, (
            f"the sync tail pushes; `_flush_push` must stand down, got {pushes}"
        )

    def test_batch_of_n_sessions_pushes_exactly_once(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        sids = [SID_A, SID_B, SID_C]
        for sid in sids:
            assert _candidate(vault, state, sid).returncode == 0
        _commit_baseline(vault)
        before = _commit_count(vault)

        code, pushes, out, err = self._run_counting_pushes(state)
        assert code == 0, err

        # Every session was flushed + committed as its own unit …
        for sid in sids:
            assert _sidecar(vault, sid)["status"] == "clean"
        assert _commit_count(vault) == before + len(sids), (
            "each session must still be its own commit (per-session atomicity)"
        )
        # … but the whole batch pushed exactly once, not once per session.
        assert pushes == 1, f"a batch of {len(sids)} must push once, got {pushes}"

    def test_all_raced_clean_batch_does_not_push(self, tmp_path):
        """If discovery's matches all raced to clean before flush, push nothing.

        Flushing the dirty sessions first leaves them clean; a second `flush all`
        re-discovers nothing dirty (empty keys) → no commit, no push.
        """
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state, SID_A).returncode == 0
        _commit_baseline(vault)
        assert _flush_current(vault, state, SID_A).returncode == 0

        code, pushes, out, err = self._run_counting_pushes(state)
        assert code == 0, err
        assert pushes == 0, f"nothing dirty to flush → no push, got {pushes}"


# ---------------------------------------------------------------------------
# injection safety — names the kql_compile guard
# ---------------------------------------------------------------------------

class TestKqlInjectionSafety:

    def test_drop_table_query_does_not_inject(self, tmp_path):
        """A `<search>` with `'; DROP TABLE …` must not error or inject.

        The facade runs the query through ``kql_compile`` which BINDS every value
        as a ``?`` param (never string-interpolated) — so SQL injection is
        structurally impossible. The query simply matches nothing and the flush is
        a clean no-op; the ``records`` table survives.
        """
        vault, state = _make_vault(tmp_path)
        _git_init(vault)
        assert _candidate(vault, state, SID_A).returncode == 0
        _commit_baseline(vault)

        # A QUOTED field value carries the dangerous string ALL THE WAY through
        # compile + execution as a bound `?` param (not a parse error short-circuit),
        # so this genuinely exercises the SQL execution path with hostile input.
        evil = 'status:"x; DROP TABLE records; --"'
        r = _flush(vault, state, evil)
        # There must be NO SQL/sqlite error and NO injection — the dirty session
        # stays intact and the index table is unharmed.
        assert "sqlite3" not in (r.stdout + r.stderr).lower()
        assert "no such table" not in (r.stdout + r.stderr).lower()
        assert _sidecar(vault, SID_A)["status"] == "dirty", "no injection side effect"

        # The records table still exists + still holds the session row (not dropped).
        index_store = load_script("lore.search.index")
        conn = index_store.open_index(env={"XDG_STATE_HOME": str(state)})
        try:
            row = conn.execute(
                "SELECT status FROM records WHERE kind='session' AND name=?", (SID_A,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None and row[0] == "dirty", (
            "records table must survive — proves kql_compile parameterized the value"
        )

    def test_kql_compile_binds_values_not_interpolated(self):
        """White-box: kql_compile compiles a malicious value to a BIND param.

        Directly exercises the named guard — ``kql_compile.compile`` — proving the
        dangerous string lands in ``params`` (bound), never in the SQL text.
        """
        kql = load_script("lore.search.kql")
        kql_compile = load_script("lore.search.kql_compile")
        evil = "x; DROP TABLE records; --"
        ast = kql.parse(f'status:"{evil}"')
        cq = kql_compile.compile(ast)
        assert evil in cq.params, "the value must be a bound param"
        assert "DROP TABLE" not in cq.full_query(), (
            "the dangerous value must NOT appear in the compiled SQL text"
        )


class _Install(NamedTuple):
    """A provisioned two-vault install: the paths these tests thread.

    Carried as ONE value rather than passed as separate ``config_home`` /
    ``state`` / ``default_vault`` arguments, so a test reads as what it exercises
    instead of as parameter plumbing.
    """
    config_home: Path
    state: Path
    default_vault: Path
    other: Path


def _two_vault_install(tmp_path) -> _Install:
    """Provision a two-vault install.

    A ``default``-scope vault plus a ``product``-scope ``trailhead`` vault — both
    git toplevels so the flush commit path runs for real. The session vault is
    ``default``; the second is the sibling a session record must never be read
    from, flushed in, or written to.

    """
    config_home = tmp_path / "config"
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    default_vault = tmp_path / "v-default"
    other_vault = tmp_path / "v-trailhead"
    for v in (default_vault, other_vault):
        v.mkdir(parents=True, exist_ok=True)
        _git_init(v)
    write_vault_config(
        config_home,
        [
            ("default", "default", default_vault),
            ("trailhead", "product", other_vault),
        ],
    )
    return _Install(config_home, state, default_vault, other_vault)


def _config_path(config_home: Path) -> Path:
    return config_home / "lore" / "config.json"


def _drop_vault_from_config(config_home: Path, name: str) -> None:
    """Remove the named vault from the config, leaving its index rows behind.

    Reproduces a STALE index row: the record was captured while the vault was
    configured, so the global index still carries the row and its ``vault``
    column, but the vault is no longer part of this install.
    """
    path = _config_path(config_home)
    cfg = json.loads(path.read_text())
    cfg["vaults"] = [e for e in cfg["vaults"] if e["name"] != name]
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _run_cfg(args, inst: _Install, *, stdin_text=None, env_extra=None):
    """Run the CLI against a multi-vault install.

    ``run_cli`` seeds its own single-vault config and applies ``env_extra``
    last, so overriding ``XDG_CONFIG_HOME`` here points the CLI at the
    multi-vault ``config.json`` instead. A caller's ``env_extra`` overlays on
    top (e.g. ``CLAUDE_PROJECT_DIR`` to pin the worktree-fallback key).
    """
    extra = dict(_NO_AMBIENT_SID)
    extra["XDG_CONFIG_HOME"] = str(inst.config_home)
    if env_extra:
        extra.update(env_extra)
    return _run(args, vault=inst.default_vault, state_dir=inst.state,
                stdin_text=stdin_text, env_extra=extra)


def _plant_session(vault: Path, key: str, *, body="a candidate\n", status="dirty") -> None:
    """Write a session record straight onto disk in *vault*.

    The only way a record reaches a non-default vault now: a teammate captured it
    and it arrived through that vault's shared remote. Capture itself refuses any
    vault but the default one, so there is no CLI path that produces this.
    """
    session_dir = vault / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / f"{key}.json").write_text(
        json.dumps({
            "version": "v1", "kind": "session", "title": key, "status": status,
            "created-at": "2026-08-10T00:00:00Z", "created-by": "tester@example.com",
            "updated-at": "2026-08-10T00:00:00Z", "updated-by": "tester@example.com",
            "annotations": {},
        }, indent=2),
        encoding="utf-8",
    )
    (session_dir / f"{key}.md").write_text(
        f"# session: {key}\n- candidate 2026-08-10T00:00:00Z kind=spec phase=Plan\n  {body}",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Session resolution is the default vault, and only the default vault
# ---------------------------------------------------------------------------
#
# Session records live only in the default vault. A `session/` record in a
# product/team vault is a TEAMMATE's, arriving through that vault's shared remote
# — never this operator's to render, flush, or append a reference to. These pin
# that the session surfaces answer from the default vault regardless of what a
# sibling vault holds under the same key.


class TestSessionResolutionIsDefaultVaultOnly:

    def test_show_renders_the_default_vault_record_and_misses_a_sibling_one(
        self, tmp_path
    ):
        """Varying input: which vault holds the key."""
        inst = _two_vault_install(tmp_path)
        _plant_session(inst.default_vault, SID_A, body="BODY-IN-DEFAULT\n")
        _plant_session(inst.other, SID_B, body="BODY-IN-TRAILHEAD\n")
        assert _run_cfg(["reindex"], inst).returncode == 0

        mine = _run_cfg(["session", "show", "--session-id", SID_A], inst)
        assert mine.returncode == 0, mine.stderr
        assert "BODY-IN-DEFAULT" in mine.stdout

        theirs = _run_cfg(["session", "show", "--session-id", SID_B], inst)
        assert theirs.returncode != 0
        assert "BODY-IN-TRAILHEAD" not in theirs.stdout

    def test_show_on_a_split_key_renders_default_without_a_notice(self, tmp_path):
        """Both vaults hold the key: default's body is rendered, alone.

        Varying input: the body each vault holds under the one key. No
        multiple-vaults notice is emitted, because the sibling record is not part
        of this operator's session at all.
        """
        inst = _two_vault_install(tmp_path)
        _plant_session(inst.default_vault, SID_A, body="BODY-IN-DEFAULT\n")
        _plant_session(inst.other, SID_A, body="BODY-IN-TRAILHEAD\n")
        assert _run_cfg(["reindex"], inst).returncode == 0

        r = _run_cfg(["session", "show", "--session-id", SID_A], inst)
        assert r.returncode == 0, r.stderr
        assert "BODY-IN-DEFAULT" in r.stdout
        assert "BODY-IN-TRAILHEAD" not in r.stdout
        assert "multiple vaults" not in r.stderr

    def test_flush_by_id_flushes_default_and_leaves_a_sibling_record_dirty(
        self, tmp_path
    ):
        """Varying input: which vault holds the dirty record."""
        inst = _two_vault_install(tmp_path)
        _plant_session(inst.default_vault, SID_A)
        _plant_session(inst.other, SID_B)
        _commit_baseline(inst.default_vault)
        _commit_baseline(inst.other)
        assert _run_cfg(["reindex"], inst).returncode == 0

        assert _run_cfg(["flush", "--session-id", SID_A], inst).returncode == 0
        assert _sidecar(inst.default_vault, SID_A)["status"] == "clean"

        before = _commit_count(inst.other)
        r = _run_cfg(["flush", "--session-id", SID_B], inst)
        assert r.returncode == 0, r.stderr
        assert _sidecar(inst.other, SID_B)["status"] == "dirty", (
            "a session record in a sibling vault is not this operator's to flush"
        )
        assert _commit_count(inst.other) == before, "and nothing may be committed there"

    def test_flush_all_leaves_a_sibling_vaults_dirty_session_alone(self, tmp_path):
        """`flush all` discovers through the index, which spans every vault.

        Varying input: which vault holds each dirty record. The default vault's is
        flushed; a teammate's in the product vault is left exactly as it was —
        flushing it would commit and push under this operator's identity.
        """
        inst = _two_vault_install(tmp_path)
        _plant_session(inst.default_vault, SID_A)
        _plant_session(inst.other, SID_B)
        _commit_baseline(inst.default_vault)
        _commit_baseline(inst.other)
        assert _run_cfg(["reindex"], inst).returncode == 0
        before = _commit_count(inst.other)

        r = _run_cfg(["flush", "all"], inst)
        assert r.returncode == 0, r.stderr
        assert _sidecar(inst.default_vault, SID_A)["status"] == "clean"
        assert _sidecar(inst.other, SID_B)["status"] == "dirty"
        assert _commit_count(inst.other) == before

    def test_referenced_appends_to_default_only(self, tmp_path):
        """Varying input: which vault holds the record under the key."""
        inst = _two_vault_install(tmp_path)
        _plant_session(inst.default_vault, SID_A)
        _plant_session(inst.other, SID_A)
        assert _run_cfg(["reindex"], inst).returncode == 0

        r = _run_cfg(
            ["session", "referenced", "decision/some-thing", "--session-id", SID_A],
            inst,
        )
        assert r.returncode == 0, r.stderr
        assert "decision/some-thing" in (
            inst.default_vault / "session" / f"{SID_A}.md"
        ).read_text()
        assert "decision/some-thing" not in (
            inst.other / "session" / f"{SID_A}.md"
        ).read_text()


# ---------------------------------------------------------------------------
# `session referenced` — the inert no-op when the session vault lacks the key
# ---------------------------------------------------------------------------

class TestReferencedOnAMissingSession:

    def test_referenced_on_a_session_no_vault_holds_creates_nothing(self, tmp_path):
        """The no-op contract survives the cross-vault resolution."""
        inst = _two_vault_install(tmp_path)

        r = _run_cfg(["session", "referenced", "task/some-task",
                      "--session-id", SID_A], inst)
        assert r.returncode == 0, r.stderr
        for vault in (inst.default_vault, inst.other):
            assert not (vault / "session" / f"{SID_A}.md").exists()
            assert not (vault / "session" / f"{SID_A}.json").exists()


# ---------------------------------------------------------------------------
# unreadable config — fail closed, never a false "nothing to flush"
# ---------------------------------------------------------------------------

class TestUnreadableConfigFailsClosed:
    """A config that will not load must abort, not degrade to the default vault.

    `_resolve_all_vaults` returns a floor list of `default` PLUS an error. Treating
    that as a searchable vault set let `lore flush` print "no session exists —
    nothing to flush" and exit 0 for a session sitting dirty in a vault the broken
    config never named — a false success. Same refusal posture as `lore sync`.
    """

    def _broken(self, tmp_path) -> _Install:
        """A two-vault install with a session in the FLOOR vault, then a broken config.

        The session lives in ``default`` on purpose: degrading to the floor list
        would find it and succeed, so each test fails for the right reason —
        refusal — rather than for the incidental "no session anywhere".
        """
        inst = _two_vault_install(tmp_path)
        _plant_session(inst.default_vault, SID_A)
        _commit_baseline(inst.default_vault)
        (inst.config_home / "lore" / "config.json").write_text("{ not json at all")
        return inst

    def test_flush_aborts_on_an_unreadable_config(self, tmp_path):
        inst = self._broken(tmp_path)
        r = _run_cfg(["flush", "--session-id", SID_A], inst)
        assert r.returncode != 0, r.stdout
        assert "config" in r.stderr.lower(), r.stderr
        assert "nothing to flush" not in r.stdout
        assert _sidecar(inst.default_vault, SID_A)["status"] == "dirty", (
            "a refused flush must not have flipped anything"
        )

    def test_session_show_aborts_on_an_unreadable_config(self, tmp_path):
        inst = self._broken(tmp_path)
        r = _run_cfg(["session", "show", "--session-id", SID_A], inst)
        assert r.returncode != 0, r.stdout
        assert "config" in r.stderr.lower(), r.stderr
        assert "no session record resolved" not in r.stderr, (
            "the refusal must name the config, not report a missing session"
        )

    def test_session_referenced_aborts_on_an_unreadable_config(self, tmp_path):
        inst = self._broken(tmp_path)
        r = _run_cfg(["session", "referenced", "task/some-task",
                      "--session-id", SID_A], inst)
        assert r.returncode != 0, r.stdout
        assert "config" in r.stderr.lower(), r.stderr


# ---------------------------------------------------------------------------
# stale index rows — the `vault` column is cross-checked against live config
# ---------------------------------------------------------------------------

class TestStaleIndexRowIsSkipped:
    """Batch discovery reads the index's `vault` column; the index outlives config.

    A row indexed under a vault since removed from `config.json` names a path this
    install no longer governs. Acting on it verbatim would let a stale (or
    planted) row steer a flip + commit at an arbitrary path — so a hit is flushed
    only when its vault IS the session vault.
    """

    def test_flush_all_skips_a_hit_whose_vault_left_the_config(self, tmp_path):
        """Varying input: whether the hit's vault is the session vault.

        The default vault's dirty session is flushed in the same run that leaves
        the dropped vault's record untouched, so this cannot pass by flushing
        nothing at all.
        """
        inst = _two_vault_install(tmp_path)
        _plant_session(inst.other, SID_A)
        _plant_session(inst.default_vault, SID_B)
        _commit_baseline(inst.other)
        _commit_baseline(inst.default_vault)
        assert _run_cfg(["reindex"], inst).returncode == 0
        before = _commit_count(inst.other)
        _drop_vault_from_config(inst.config_home, "trailhead")

        r = _run_cfg(["flush", "all"], inst)
        assert r.returncode == 0, r.stderr
        assert _sidecar(inst.default_vault, SID_B)["status"] == "clean"
        assert _sidecar(inst.other, SID_A)["status"] == "dirty", (
            "an unconfigured vault must not be written to"
        )
        assert _commit_count(inst.other) == before
