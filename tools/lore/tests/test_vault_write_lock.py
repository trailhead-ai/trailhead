"""Behavioral proof of the vault-scoped write lock (``lore.locking``).

Covers the four races the lock closes plus the lock's own contract:

  1. **Same-stem double-create** — N concurrent ``record create`` calls with an
     identical title against ONE vault. Unguarded, ``_unique_stem``
     (``record/store.py``) is a check-then-act: two writers both see ``foo``
     free and both write ``foo.md``, so one record is lost.
  2. **Concurrent in-place updates** — distinct records in one vault; every body
     and index row must land and no ``database is locked`` may reach a caller.
  3. **Cross-vault contention on the ONE global SQLite index** — the per-vault
     flock does NOT serialize writers in *different* vaults, so this is the
     ``open_index`` hardening (explicit ``timeout=`` + ``BEGIN IMMEDIATE``
     provisioning retry), not the flock.
  4. **Opposed cross-vault moves** — ``move_record`` takes BOTH vaults' locks in
     sorted-path order; unsorted acquisition deadlocks two opposed movers.

Plus the helper's own contract: a held lock DELAYS a second writer rather than
failing it, and a wait past the notice threshold is reported on stderr so a
blocked writer is distinguishable from a stuck one.

Convention (Axiom 6): subprocess workers + a file-marker barrier, with
``XDG_STATE_HOME``/``XDG_CONFIG_HOME`` fenced into ``tmp_path`` — these tests
never touch the live install.
"""
from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from conftest import CLI_PATH, load_script, run_cli, write_default_config

REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# subprocess-worker harness
# ---------------------------------------------------------------------------

_WORKER = r"""
import os, sys, time, subprocess
from pathlib import Path

vault, state, config_home, barrier, n, tag = sys.argv[1:7]
argv_rest = sys.argv[7:]
n = int(n)

os.environ["XDG_STATE_HOME"] = state
os.environ["XDG_CONFIG_HOME"] = config_home
os.environ["LORE_EMAIL"] = "tester@example.com"

barrier_file = Path(barrier)
(barrier_file.parent / (barrier_file.name + ".ready." + tag)).write_text("1")
deadline = time.time() + 15
while time.time() < deadline:
    if len(list(barrier_file.parent.glob(barrier_file.name + ".ready.*"))) >= n:
        break
    time.sleep(0.0005)

args = [a.replace("{{TAG}}", tag) for a in argv_rest]
stdin_text = os.environ.get("WORKER_STDIN", "").replace("{{TAG}}", tag)
r = subprocess.run(
    [sys.executable, {cli!r}, *args],
    input=stdin_text, text=True, capture_output=True, env=dict(os.environ),
)
if r.returncode != 0:
    sys.stderr.write(f"[{{tag}}] rc={{r.returncode}} out={{r.stdout}} err={{r.stderr}}")
    sys.exit(r.returncode)
"""


def _spawn(*, vault, state, config_home, barrier, n, tag, args, stdin_text=""):
    """Spawn one barrier-synchronized worker running ``lore <args>``."""
    import os

    env = dict(os.environ)
    env["WORKER_STDIN"] = stdin_text
    return subprocess.Popen(
        [
            sys.executable, "-c", _WORKER.format(cli=str(CLI_PATH)),
            str(vault), str(state), str(config_home), str(barrier), str(n), tag,
            *args,
        ],
        stderr=subprocess.PIPE, text=True, env=env,
    )


def _collect(procs, timeout=120):
    errs = []
    for i, p in enumerate(procs):
        _, err = p.communicate(timeout=timeout)
        if p.returncode != 0:
            errs.append(f"worker {i} exited {p.returncode}:\n{err}")
    return errs


def _index_conn(state: Path) -> sqlite3.Connection:
    path = state / "lore" / "index.sqlite"
    assert path.exists(), "shared index.sqlite was never created"
    return sqlite3.connect(str(path))


def _index_names(state: Path, vault_name: str, kind: str) -> set[str]:
    """Index row names for ``kind`` in the vault whose dir basename is *vault_name*.

    Matched by basename because ``records.vault`` holds the vault path as the CLI
    resolved it, and macOS ``tmp_path`` differs from its realpath (``/private/var``
    vs ``/var``).
    """
    conn = _index_conn(state)
    try:
        rows = conn.execute(
            "SELECT name FROM records WHERE vault LIKE ? AND kind = ?",
            (f"%/{vault_name}", kind),
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# 1 — same-stem double-create
# ---------------------------------------------------------------------------

class TestSameStemCreateRace:
    WORKERS = 4

    def test_identical_titles_yield_distinct_stems_and_index_rows(self, tmp_path):
        """N concurrent creates of the SAME title → N records, N index rows.

        Unguarded, ``place_record``'s collision check races and two writers land
        on the same stem, so a record (and its index row) is silently lost.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        state = tmp_path / "state"
        state.mkdir()
        config_home = state / "_xdg_config"
        write_default_config(config_home, vault)
        barrier = tmp_path / "_barrier"

        procs = [
            _spawn(
                vault=vault, state=state, config_home=config_home,
                barrier=barrier, n=self.WORKERS, tag=f"w{i}",
                args=["record", "create", "--kind", "decision",
                      "--title", "same stem race"],
                stdin_text="body {TAG}\n",
            )
            for i in range(self.WORKERS)
        ]
        assert not _collect(procs), "concurrent same-title creates failed"

        mds = sorted(p.stem for p in (vault / "decision").glob("*.md"))
        assert len(mds) == self.WORKERS, (
            f"expected {self.WORKERS} distinct stems, found {len(mds)}: {mds}"
        )
        assert _index_names(state, "vault", "decision") == set(mds)


# ---------------------------------------------------------------------------
# 2 — concurrent in-place updates
# ---------------------------------------------------------------------------

class TestConcurrentUpdates:
    WORKERS = 4

    def test_concurrent_updates_to_distinct_records_all_land(self, tmp_path):
        """Every body + index row lands; no ``database is locked`` reaches a caller."""
        vault = tmp_path / "vault"
        vault.mkdir()
        state = tmp_path / "state"
        state.mkdir()
        config_home = state / "_xdg_config"
        write_default_config(config_home, vault)
        barrier = tmp_path / "_barrier"

        ids = []
        for i in range(self.WORKERS):
            r = run_cli(
                ["record", "create", "--kind", "decision", "--title", f"target {i}"],
                vault=vault, state_dir=state, stdin_text="seed\n",
            )
            assert r.returncode == 0, r.stderr
            ids.append(r.stdout.strip())

        procs = [
            _spawn(
                vault=vault, state=state, config_home=config_home,
                barrier=barrier, n=self.WORKERS, tag=f"w{i}",
                args=["record", "update", ids[i]],
                stdin_text="updated by {TAG}\n",
            )
            for i in range(self.WORKERS)
        ]
        errs = _collect(procs)
        assert not errs, "concurrent updates failed"
        assert not any("database is locked" in e for e in errs)

        for i, rid in enumerate(ids):
            body = (vault / f"{rid}.md").read_text(encoding="utf-8")
            assert body.strip() == f"updated by w{i}", f"{rid}: {body!r}"

        conn = _index_conn(state)
        try:
            for i, rid in enumerate(ids):
                name = rid.split("/", 1)[1]
                row = conn.execute(
                    "SELECT body FROM record_fts WHERE rowid = "
                    "(SELECT rowid FROM records WHERE kind='decision' AND name=?)",
                    (name,),
                ).fetchone()
                assert row is not None, f"{rid}: no index row after update"
                assert f"updated by w{i}" in row[0], (
                    f"{rid}: index STALE — disk updated, FTS body {row[0]!r}"
                )
        finally:
            conn.close()


    def test_concurrent_updates_to_the_SAME_record_never_lose_a_mutation(self, tmp_path):
        """Two writers mutating one record's sidecar both survive.

        ``record update`` is a read-modify-write: it reads the sidecar, applies the
        field flags in memory, then writes. With only the write locked, both
        writers read the same pre-state and the second write silently drops the
        first one's field. The whole read-modify-write has to be one critical
        section, so the losers serialize behind the winner and re-read.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        state = tmp_path / "state"
        state.mkdir()
        config_home = state / "_xdg_config"
        write_default_config(config_home, vault)
        barrier = tmp_path / "_barrier_same"

        r = run_cli(
            ["record", "create", "--kind", "decision", "--title", "contended"],
            vault=vault, state_dir=state, stdin_text="seed\n",
        )
        assert r.returncode == 0, r.stderr
        rid = r.stdout.strip()

        tags = [f"w{i}" for i in range(self.WORKERS)]
        procs = [
            _spawn(
                vault=vault, state=state, config_home=config_home,
                barrier=barrier, n=self.WORKERS, tag=tag,
                # Metadata-only (no stdin): each worker appends its own keyword.
                args=["record", "update", rid, "--keyword", "{TAG}"],
            )
            for tag in tags
        ]
        errs = _collect(procs)
        assert not errs, f"concurrent same-record updates failed: {errs}"

        sidecar = json.loads((vault / f"{rid}.json").read_text(encoding="utf-8"))
        keywords = sidecar.get("keywords", [])
        missing = [t for t in tags if t not in keywords]
        assert not missing, (
            f"lost update: {missing} never landed — keywords={keywords!r}. The "
            "read-modify-write is not one critical section."
        )


# ---------------------------------------------------------------------------
# 3 — cross-vault contention on the single global index
# ---------------------------------------------------------------------------

class TestCrossVaultIndexContention:
    WORKERS = 4
    ITERS = 5

    def test_two_vault_concurrent_writes_surface_no_lock_errors(self, tmp_path):
        """Writers in DIFFERENT vaults share one index; none may see SQLITE_BUSY.

        The per-vault flock cannot help here — this is ``open_index``'s explicit
        timeout + ``BEGIN IMMEDIATE`` provisioning retry.
        """
        state = tmp_path / "state"
        state.mkdir()
        barrier = tmp_path / "_barrier"

        procs = []
        for i in range(self.WORKERS):
            tag = f"w{i}"
            vault = tmp_path / f"vault_{'a' if i % 2 == 0 else 'b'}"
            vault.mkdir(exist_ok=True)
            config_home = tmp_path / f"cfg_{tag}"
            write_default_config(config_home, vault)
            procs.append(_spawn(
                vault=vault, state=state, config_home=config_home,
                barrier=barrier, n=self.WORKERS, tag=tag,
                args=["record", "create", "--kind", "decision",
                      "--title", "cross vault {TAG}"],
                stdin_text="body {TAG}\n",
            ))
        errs = _collect(procs)
        assert not errs, "cross-vault concurrent writes surfaced errors"

        for suffix in ("a", "b"):
            expected = self.WORKERS // 2
            names = _index_names(state, f"vault_{suffix}", "decision")
            assert len(names) == expected, f"vault_{suffix} index rows: {names}"


# ---------------------------------------------------------------------------
# 4 — opposed cross-vault moves are deadlock-free
# ---------------------------------------------------------------------------

def _seed_record(store, vault: Path, kind: str, name: str, conn) -> None:
    (vault / kind).mkdir(parents=True, exist_ok=True)
    location = store.RecordLocation(
        vault_root=str(vault), kind=kind, name=name,
        record_id=f"{kind}/{name}",
        body_path=vault / kind / f"{name}.md",
        sidecar_path=vault / kind / f"{name}.json",
    )
    sidecar = {
        "kind": kind, "title": name, "status": "open",
        "created-at": "2026-01-01T00:00:00Z", "created-by": "tester@example.com",
        "updated-at": "2026-01-01T00:00:00Z", "updated-by": "tester@example.com",
    }
    location.body_path.write_text(f"body {name}\n", encoding="utf-8")
    location.sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    store.update_index(conn, location.record_id, sidecar, f"body {name}\n", str(vault))
    conn.commit()


class TestOpposedCrossVaultMoves:
    def test_two_opposed_moves_do_not_deadlock(self, tmp_path, monkeypatch):
        """A→B and B→A concurrently: sorted-order acquisition means no deadlock.

        Unsorted (source-then-dest) acquisition has each mover holding the lock
        the other needs, and both block forever.
        """
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("LORE_EMAIL", "tester@example.com")
        store = load_script("lore.record.store")
        index = load_script("lore.search.index")

        vault_a = tmp_path / "vault_a"
        vault_b = tmp_path / "vault_b"
        conn = index.open_index()
        try:
            _seed_record(store, vault_a, "decision", "from-a", conn)
            _seed_record(store, vault_b, "decision", "from-b", conn)
        finally:
            conn.close()

        start = threading.Barrier(2)
        errors: list[str] = []

        def mover(src: Path, dst: Path, name: str) -> None:
            (dst / "decision").mkdir(parents=True, exist_ok=True)
            dest = store.RecordLocation(
                vault_root=str(dst), kind="decision", name=name,
                record_id=f"decision/{name}",
                body_path=dst / "decision" / f"{name}.md",
                sidecar_path=dst / "decision" / f"{name}.json",
            )
            c = index.open_index()
            try:
                start.wait(timeout=15)
                store.move_record(
                    f"decision/{name}", dest, c, old_vault_root=str(src)
                )
                c.commit()
            except Exception as exc:  # noqa: BLE001 — reported, not swallowed
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
            finally:
                c.close()

        threads = [
            threading.Thread(target=mover, args=(vault_a, vault_b, "from-a")),
            threading.Thread(target=mover, args=(vault_b, vault_a, "from-b")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        alive = [t for t in threads if t.is_alive()]
        assert not alive, "opposed cross-vault moves DEADLOCKED"
        assert not errors, errors

        assert (vault_b / "decision" / "from-a.md").exists()
        assert (vault_a / "decision" / "from-b.md").exists()
        assert not (vault_a / "decision" / "from-a.md").exists()
        assert not (vault_b / "decision" / "from-b.md").exists()


# ---------------------------------------------------------------------------
# the helper's own contract
# ---------------------------------------------------------------------------

_HOLDER = r"""
import sys, time
sys.path.insert(0, {plugin_root!r})
from lore.locking import vault_write_lock
from pathlib import Path

vault = Path(sys.argv[1])
hold_for = float(sys.argv[2])
held_marker = vault / "_held"
with vault_write_lock(vault):
    held_marker.write_text("1")
    time.sleep(hold_for)
"""


def _spawn_holder(vault: Path, hold_for: float) -> subprocess.Popen:
    """Hold the vault write lock in a subprocess for *hold_for* seconds."""
    code = _HOLDER.format(plugin_root=str(REPO_ROOT / "plugins" / "lore"))
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(vault), str(hold_for)],
        stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (vault / "_held").exists():
            return proc
        time.sleep(0.005)
    proc.kill()
    raise AssertionError("holder subprocess never acquired the lock")


def _wait_for_session_lock(locking, vault: Path, buf: io.StringIO) -> None:
    """Acquire the session lock from a second thread, capturing the wait notice."""
    with redirect_stderr(buf):
        with locking.session_write_lock(vault, "sess-x", notice_after=0.1):
            pass


class TestLockContract:
    def test_lock_blocks_rather_than_failing(self, tmp_path):
        """A held lock DELAYS the second writer; it never errors."""
        locking = load_script("lore.locking")
        vault = tmp_path / "vault"
        vault.mkdir()

        holder = _spawn_holder(vault, 1.0)
        try:
            t0 = time.monotonic()
            with locking.vault_write_lock(vault):
                waited = time.monotonic() - t0
        finally:
            holder.wait(timeout=15)
        assert waited >= 0.4, f"second writer did not block (waited {waited:.3f}s)"

    def test_uncontended_acquisition_is_silent(self, tmp_path):
        """No notice when the lock is free."""
        locking = load_script("lore.locking")
        vault = tmp_path / "vault"
        vault.mkdir()

        buf = io.StringIO()
        with redirect_stderr(buf), locking.vault_write_lock(vault):
            pass
        assert buf.getvalue() == "", f"unexpected stderr: {buf.getvalue()!r}"

    def test_wait_past_threshold_emits_stderr_notice(self, tmp_path):
        """A wait past the notice threshold reports it, so a blocked writer
        reads as blocked rather than stuck."""
        locking = load_script("lore.locking")
        vault = tmp_path / "vault"
        vault.mkdir()

        holder = _spawn_holder(vault, 1.0)
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                with locking.vault_write_lock(vault, notice_after=0.1):
                    pass
        finally:
            holder.wait(timeout=15)
        notice = buf.getvalue()
        assert "waiting for the vault write lock" in notice, f"no notice: {notice!r}"
        assert str(vault) in notice

    def test_default_notice_threshold_is_two_seconds(self):
        locking = load_script("lore.locking")
        assert locking.LOCK_WAIT_NOTICE_SECONDS == pytest.approx(2.0)

    def test_reentrant_within_a_thread(self, tmp_path):
        """Nested acquisition of the same vault lock must not self-deadlock —
        the CLI create path holds the lock across ``validate_and_write``, which
        acquires it again."""
        locking = load_script("lore.locking")
        vault = tmp_path / "vault"
        vault.mkdir()
        with locking.vault_write_lock(vault):
            with locking.vault_write_lock(vault):
                pass
        # Still acquirable after the nested release (depth unwound correctly).
        with locking.vault_write_lock(vault):
            pass

    def test_reentrant_across_two_spellings_of_one_vault(self, tmp_path):
        """Two spellings of ONE vault path are one lock, not two.

        Reentrancy is keyed by lock path; keyed by the *unresolved* spelling, a
        nested acquisition written ``vault`` outside and ``vault/../vault`` inside
        misses the depth bump, opens a second fd, and self-deadlocks on the flock
        this thread already holds.
        """
        locking = load_script("lore.locking")
        vault = tmp_path / "vault"
        vault.mkdir()
        alias = Path(f"{vault}/../{vault.name}")

        done = threading.Event()

        def nested() -> None:
            with locking.vault_write_lock(vault):
                with locking.vault_write_lock(alias):
                    pass
            done.set()

        t = threading.Thread(target=nested, daemon=True)
        t.start()
        t.join(timeout=5)
        assert done.is_set(), "two spellings of one vault self-deadlocked"

    def test_session_wait_notice_names_the_session_scope(self, tmp_path, monkeypatch):
        """A session-key wait must NOT read as a vault-lock wait.

        Operators (and ranger's mass-timeout triage) key on the vault-lock
        wording to mean "the whole vault is contended"; a per-session-key wait,
        which blocks only that one session's writers, must say so instead.
        """
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        locking = load_script("lore.locking")
        vault = tmp_path / "vault"
        vault.mkdir()

        holder_release = threading.Event()
        holding = threading.Event()

        def holder() -> None:
            with locking.session_write_lock(vault, "sess-x"):
                holding.set()
                holder_release.wait(timeout=15)

        t = threading.Thread(target=holder, daemon=True)
        t.start()
        assert holding.wait(timeout=15)
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                # Same-thread reentrancy would skip the wait, so contend from a
                # second thread's perspective: the holder thread owns the depth
                # entry, this one does not.
                waiter = threading.Thread(
                    target=lambda: _wait_for_session_lock(locking, vault, buf),
                    daemon=True,
                )
                waiter.start()
                time.sleep(0.4)
                holder_release.set()
                waiter.join(timeout=15)
        finally:
            holder_release.set()
            t.join(timeout=15)

        notice = buf.getvalue()
        assert "waiting for the session write lock" in notice, f"no notice: {notice!r}"
        assert "session/sess-x" in notice, notice
        assert "vault write lock" not in notice, (
            f"a session-key wait reads as a vault-wide wait: {notice!r}"
        )

    def test_lock_file_lives_at_the_vault_root(self, tmp_path):
        locking = load_script("lore.locking")
        vault = tmp_path / "vault"
        vault.mkdir()
        with locking.vault_write_lock(vault):
            assert (vault / ".lore.lock").exists()
