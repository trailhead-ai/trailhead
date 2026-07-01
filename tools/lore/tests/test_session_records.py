"""Sessions are singular indexed records, born dirty.

  capture → singular indexed record:
    - first ``candidate`` materializes ``session/<key>.{md,json}`` born ``dirty``,
      indexed and discoverable via ``lore search 'kind:session status:dirty'``.
    - two distinct ``--session-id``s in one worktree → two records.
    - a candidate on a ``clean`` session flips it back to ``dirty``.

  referenced:
    - ``referenced`` never dirties an existing session (status stays ``clean``)
      and bumps ``last-referenced-at``.
    - ``referenced`` on a non-existent session creates NOTHING (no body, no
      sidecar, no index row).

  flock critical section (the load-bearing race test):
    - concurrent candidates for ONE id over many iterations → exactly ONE record
      with ALL body entries present AND an index FTS body consistent with the
      final body (no stale snapshot, no torn sidecar, no lost row).

  confinement guard:
    - a worktree-name key containing ``..`` / a path separator / a NUL byte is
      rejected non-zero and writes nothing (no escape from ``session/``).

Tests run the CLI as a subprocess via the conftest harness (LORE_VAULT +
XDG_STATE_HOME injected) so the real vault/index are never touched.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import (
    load_script,
    make_vault as _make_vault,
    run_cli as _run,
    write_default_config,
)

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "lore" / "scripts"

SID = "11111111-2222-4333-8444-555555555555"
SID2 = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_md(vault: Path, key: str) -> Path:
    return vault / "session" / f"{key}.md"


def _record_json(vault: Path, key: str) -> Path:
    return vault / "session" / f"{key}.json"


def _sidecar(vault: Path, key: str) -> dict:
    return json.loads(_record_json(vault, key).read_text())


def _index_rows(state: Path, name: str | None = None):
    index_store = load_script("lore.search.index")
    conn = index_store.open_index(env={"XDG_STATE_HOME": str(state)})
    try:
        if name is None:
            return conn.execute(
                "SELECT vault, kind, name, status FROM records WHERE kind='session'"
            ).fetchall()
        return conn.execute(
            "SELECT vault, kind, name, status FROM records "
            "WHERE kind='session' AND name=?",
            (name,),
        ).fetchall()
    finally:
        conn.close()


def _fts_body(state: Path, name: str) -> str | None:
    index_store = load_script("lore.search.index")
    conn = index_store.open_index(env={"XDG_STATE_HOME": str(state)})
    try:
        row = conn.execute(
            "SELECT body FROM record_fts WHERE rowid = "
            "(SELECT rowid FROM records WHERE kind='session' AND name=?)",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def _candidate(vault, state, sid, body="a candidate\n", kind="spec", phase="Plan",
               extra=None):
    args = ["session", "candidate", "--session-id", sid, "--kind", kind,
            "--phase", phase]
    if extra:
        args += extra
    # Clear any ambient session-id env so an empty ``sid`` truly takes the
    # worktree-key path (the host CI runner may export CLAUDE_CODE_SESSION_ID).
    env_extra = {"CLAUDE_CODE_SESSION_ID": "", "CLAUDE_SESSION_ID": ""}
    return _run(args, vault=vault, state_dir=state, stdin_text=body,
                env_extra=env_extra)


# ---------------------------------------------------------------------------
# First candidate → indexed, dirty, singular record
# ---------------------------------------------------------------------------

class TestFirstCandidate:

    def test_materializes_singular_dirty_record(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        r = _candidate(vault, state, SID, body="proposed a spec\n")
        assert r.returncode == 0, f"candidate failed: {r.stderr}"

        md = _record_md(vault, SID)
        js = _record_json(vault, SID)
        assert md.exists(), "candidate must write the singular session/<key>.md"
        assert js.exists(), "candidate must write the singular session/<key>.json"
        # No plural sessions/ artifact.
        assert not (vault / "sessions").exists(), "no plural sessions/ dir"

        side = _sidecar(vault, SID)
        assert side["kind"] == "session"
        assert side["status"] == "dirty", "first candidate is born dirty"
        assert side["title"], "a synthetic title is set"

        text = md.read_text()
        assert "- candidate" in text and "kind=spec" in text and "phase=Plan" in text
        assert "proposed a spec" in text

    def test_indexed_and_found_by_search(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        assert _candidate(vault, state, SID).returncode == 0

        rows = _index_rows(state, SID)
        assert len(rows) == 1, f"exactly one indexed session row expected: {rows}"
        assert rows[0][3] == "dirty"

        # Discoverable by the search facade.
        r = _run(["search", "kind:session status:dirty"], vault=vault, state_dir=state)
        assert r.returncode == 0, f"search failed: {r.stderr}"
        assert SID in r.stdout, f"dirty session not found by search:\n{r.stdout}"


# ---------------------------------------------------------------------------
# Two distinct session-ids → two records
# ---------------------------------------------------------------------------

class TestTwoSessionsTwoRecords:

    def test_two_session_ids_one_worktree_two_records(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        assert _candidate(vault, state, SID).returncode == 0
        assert _candidate(vault, state, SID2).returncode == 0

        assert _record_md(vault, SID).exists()
        assert _record_md(vault, SID2).exists()
        rows = _index_rows(state)
        names = sorted(row[2] for row in rows)
        assert names == sorted([SID, SID2]), f"expected two records: {names}"


# ---------------------------------------------------------------------------
# Subsequent candidate on a clean session flips it dirty
# ---------------------------------------------------------------------------

class TestCleanToDirty:

    def test_candidate_on_clean_session_flips_dirty(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        assert _candidate(vault, state, SID).returncode == 0

        # Manually mark the record clean (simulates a post-flush state) and reindex
        # via a fresh candidate's effect. We flip the sidecar to clean on disk and
        # reindex through the CLI's own search-visible state by running another
        # candidate, which must restore dirty.
        js = _record_json(vault, SID)
        side = json.loads(js.read_text())
        side["status"] = "clean"
        # Re-run the index to reflect the clean state first (use a fresh candidate
        # would re-dirty; instead assert via the next candidate). Write the clean
        # sidecar atomically through the record store helper to mirror real writes.
        record_store = load_script("lore.record.store")
        record_store.write_temp_then_rename(
            js, json.dumps(side, sort_keys=True, separators=(",", ":"))
        )

        # Next candidate must flip status back to dirty (sidecar + index).
        r = _candidate(vault, state, SID, body="second candidate\n",
                       kind="decision", phase="Build")
        assert r.returncode == 0, f"second candidate failed: {r.stderr}"

        assert _sidecar(vault, SID)["status"] == "dirty"
        rows = _index_rows(state, SID)
        assert rows and rows[0][3] == "dirty", f"index must reflect dirty: {rows}"
        text = _record_md(vault, SID).read_text()
        assert "second candidate" in text and "a candidate" in text


# ---------------------------------------------------------------------------
# referenced — never dirties an existing session
# ---------------------------------------------------------------------------

class TestReferenced:

    def test_referenced_on_nonexistent_session_creates_nothing(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        r = _run(
            ["session", "referenced", "spec/some-record", "--session-id", SID],
            vault=vault, state_dir=state,
        )
        assert r.returncode == 0, f"referenced should no-op cleanly: {r.stderr}"
        assert not _record_md(vault, SID).exists(), "no body created"
        assert not _record_json(vault, SID).exists(), "no sidecar created"
        assert _index_rows(state) == [], "no index row created"

    def test_referenced_does_not_dirty_and_bumps_last_referenced(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        assert _candidate(vault, state, SID).returncode == 0

        # Flush-like: mark clean on disk + reindex through record_store, so we can
        # prove referenced does NOT flip it back to dirty.
        js = _record_json(vault, SID)
        side = json.loads(js.read_text())
        side["status"] = "clean"
        side.pop("last-referenced-at", None)
        record_store = load_script("lore.record.store")
        record_store.write_temp_then_rename(
            js, json.dumps(side, sort_keys=True, separators=(",", ":"))
        )

        r = _run(
            ["session", "referenced", "spec/lore-search", "--session-id", SID],
            vault=vault, state_dir=state,
        )
        assert r.returncode == 0, f"referenced failed: {r.stderr}"

        after = _sidecar(vault, SID)
        assert after["status"] == "clean", "referenced must never dirty a session"
        assert after.get("annotations", {}).get("last-referenced-at"), (
            "referenced must bump last-referenced-at in the annotations map"
        )
        text = _record_md(vault, SID).read_text()
        assert "- referenced" in text and "spec/lore-search" in text

    def test_referenced_on_body_only_legacy_does_not_corrupt_index(self, tmp_path):
        # A legacy/migrated session can exist as a body-only ``session/<key>.md`` with
        # NO ``.json`` sidecar. ``referenced`` must append the body line but must NOT
        # fabricate a ``{}`` sidecar nor project an off-vocab ``status:""`` row into the
        # index (it never materializes a record).
        vault, state = _make_vault(tmp_path)
        body_path = _record_md(vault, SID)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_text("# session: legacy\n\n- candidate ...\n")

        r = _run(
            ["session", "referenced", "spec/lore-search", "--session-id", SID],
            vault=vault, state_dir=state,
        )
        assert r.returncode == 0, f"referenced failed: {r.stderr}"

        assert not _record_json(vault, SID).exists(), (
            "referenced must not fabricate a sidecar for a body-only legacy record"
        )
        assert _index_rows(state, SID) == [], (
            "referenced must not project a malformed status:'' row into the index"
        )
        text = body_path.read_text()
        assert "- referenced" in text and "spec/lore-search" in text, (
            "the referenced line is still appended to the existing body"
        )


# ---------------------------------------------------------------------------
# Confinement guard — worktree-name key
# ---------------------------------------------------------------------------

class TestWorktreeConfinement:

    @pytest.mark.parametrize("bad", ["../../evil", "a/b", "..", "foo/bar", "."])
    def test_bad_worktree_key_rejected_nothing_written(self, tmp_path, bad):
        vault, state = _make_vault(tmp_path)
        canary = tmp_path / "evil.md"
        # ``sid=""`` so the worktree path is taken; the bad worktree must be rejected.
        r = _candidate(vault, state, sid="", body="payload\n",
                       extra=["--worktree", bad])
        assert r.returncode != 0, f"bad worktree {bad!r} must be rejected"
        assert r.stderr.strip(), "rejection must explain why on stderr"
        assert not canary.exists(), "no traversal write outside session/"
        sdir = vault / "session"
        if sdir.exists():
            assert not any(sdir.iterdir()), "no partial write on rejection"

    def test_sanitize_worktree_name_unit(self):
        store = load_script("lore.session.store")
        # Accepts a plain allowlist name.
        assert store.sanitize_worktree_name("lore-flush") == "lore-flush"
        assert store.sanitize_worktree_name("Feat_123") == "Feat_123"
        for bad in ["../../evil", "a/b", "..", ".", "a\\b", "", "x" * 300,
                    "evil\x00name", "has space", "dots.dots"]:
            with pytest.raises(store.InvalidSessionIdError):
                store.sanitize_worktree_name(bad)

    def test_valid_candidate_via_worktree_key(self, tmp_path):
        vault, state = _make_vault(tmp_path)
        r = _candidate(vault, state, sid="", body="wt candidate\n",
                       extra=["--worktree", "lore-flush"])
        assert r.returncode == 0, f"valid worktree candidate failed: {r.stderr}"
        assert _record_md(vault, "lore-flush").exists()
        assert _sidecar(vault, "lore-flush")["status"] == "dirty"


# ---------------------------------------------------------------------------
# flock critical section — concurrent candidates (the race guard)
# ---------------------------------------------------------------------------

_RACE_WORKER = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, {scripts!r})
import os
# Config-only resolution: LORE_VAULT is not read, so the active vault is
# resolved from the config.json seeded under XDG_CONFIG_HOME (arg 7).
os.environ["XDG_CONFIG_HOME"] = sys.argv[7]
os.environ["XDG_STATE_HOME"] = sys.argv[6]
os.environ["LORE_EMAIL"] = "tester@example.com"

session_id = sys.argv[1]
entry_tag = sys.argv[2]
barrier_file = Path(sys.argv[4])
n = int(sys.argv[5])

cli = {cli!r}
import subprocess

ready = barrier_file.parent / (barrier_file.name + ".ready." + entry_tag)
ready.write_text("1")
deadline = time.time() + 10
while time.time() < deadline:
    markers = list(barrier_file.parent.glob(barrier_file.name + ".ready.*"))
    if len(markers) >= n:
        break
    time.sleep(0.0005)

r = subprocess.run(
    [sys.executable, cli, "session", "candidate", "--session-id", session_id,
     "--kind", "decision", "--phase", "Build"],
    input="body-" + entry_tag + "\n", text=True, capture_output=True,
    env=dict(os.environ),
)
if r.returncode != 0:
    sys.stderr.write(r.stderr)
    sys.exit(r.returncode)
"""


def _run_race(vault, state, session_id, n_workers=3):
    barrier_file = vault / "_barrier"
    cli = REPO_ROOT / "plugins" / "lore" / "cli" / "lore"
    code = _RACE_WORKER.format(scripts=str(SCRIPTS_DIR), cli=str(cli))
    # Seed config.json ONCE (before spawning) so every worker resolves the same
    # test vault via config — no LORE_VAULT, no write race on the config file.
    config_home = state / "_xdg_config"
    write_default_config(config_home, vault)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code, session_id, f"w{i}", str(vault),
             str(barrier_file), str(n_workers), str(state), str(config_home)],
            stderr=subprocess.PIPE, text=True,
        )
        for i in range(n_workers)
    ]
    errs = []
    for i, p in enumerate(procs):
        _, err = p.communicate(timeout=60)
        if p.returncode != 0:
            errs.append(f"worker {i} exited {p.returncode}: {err}")
    return errs


class TestConcurrentCandidates:
    """Race proof: exactly one record, all entries present, index consistent."""

    ITERATIONS = 25
    WORKERS = 3

    def test_concurrent_candidates_consistent_record_and_index(self, tmp_path):
        failures = []
        for i in range(self.ITERATIONS):
            vault = tmp_path / f"v{i}"
            state = tmp_path / f"s{i}"
            vault.mkdir(parents=True, exist_ok=True)
            state.mkdir(parents=True, exist_ok=True)
            sid = f"00ce{i:04d}-2222-4333-8444-555555555555"

            errs = _run_race(vault, state, sid, n_workers=self.WORKERS)
            if errs:
                failures.append(f"iter {i}: {errs}")
                continue

            # Exactly one record pair.
            mds = list((vault / "session").glob(f"{sid}*.md"))
            if len(mds) != 1:
                failures.append(f"iter {i}: expected 1 md, found {len(mds)}")
                continue
            body = mds[0].read_text()
            # All N entries present, none lost (dominant unguarded failure mode).
            for w in range(self.WORKERS):
                if f"body-w{w}" not in body:
                    failures.append(f"iter {i}: lost entry body-w{w}")
            # Index consistent with the final body: the FTS body must contain every
            # entry (no stale snapshot from an interleaved reindex).
            fts = _fts_body(state, sid)
            if fts is None:
                failures.append(f"iter {i}: no index row after concurrent candidates")
                continue
            for w in range(self.WORKERS):
                if f"body-w{w}" not in fts:
                    failures.append(
                        f"iter {i}: index STALE — body-w{w} on disk but not in FTS"
                    )
            rows = _index_rows(state, sid)
            if not rows or rows[0][3] != "dirty":
                failures.append(f"iter {i}: index status not dirty: {rows}")

        assert not failures, (
            f"Concurrent candidates FAILED on {len(failures)}/{self.ITERATIONS} "
            f"iterations:\n" + "\n".join(failures[:15])
        )
