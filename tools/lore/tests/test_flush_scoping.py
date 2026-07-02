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
import sys
from pathlib import Path

from conftest import load_script, make_vault as _make_vault, run_cli as _run

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
            json.dumps(side_a, sort_keys=True, separators=(",", ":"))
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
    """A batch flush pushes ONCE for the whole batch, not once per session.

    Each per-session commit is still its own atomicity unit; only the network
    push is hoisted out of the loop (`_flush_commit(push=False)` + a single
    trailing `_flush_push`). Drives the CLI in-process so `_git` can be patched
    to (a) report a fake origin remote — the test vault has none — so the push
    path is actually reached, and (b) count pushes while letting add/diff/commit
    run for real, proving N commits but exactly one push.
    """

    def _run_counting_pushes(self, state):
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
            args = dispatch.build_parser().parse_args(["flush", "all"])
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                code = args.func(args)
        finally:
            flush._git = real_git
            os.environ.clear()
            os.environ.update(old_environ)
        return code, pushes["n"], buf_out.getvalue(), buf_err.getvalue()

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
