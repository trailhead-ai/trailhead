"""Vault write lock, part 2 — the ``sync`` / ``flush`` / ``reindex`` paths.

``lore.locking`` sits under the record and session *write* primitives. This file
pins the three command-level paths that mutate a vault outside those primitives,
and the one place lore serializes globally:

  1. **``lore sync``'s tree-mutating section** — ``git add -A`` → ``commit`` →
     ``rebase`` / ``reset --hard`` — runs under that vault's
     ``vault_write_lock``, so ``move_record``'s copy → repoint → delete can
     never be staged half-done. ``fetch`` and ``push`` run **outside** it: they
     never touch the working tree, and a no-timeout flock held across a hung
     remote would starve every local writer.
  2. **``lore flush``'s commit** runs under the **session-key** lock — the flip
     and the commit are one unit, so a concurrent ``session candidate`` cannot
     land a ``dirty`` sidecar inside the flush commit — and, nested inside it,
     under the **vault** lock, so a concurrent ``sync``'s ``git add -A`` cannot
     land between the flush's own ``add`` and its ``commit`` and be swept into
     it. The acquisition order is fixed session-key → vault; nothing in lore
     takes them the other way round.
  2b. **The record-delete index commit** lands inside the vault lock, like the
     create path's — a commit after the release would publish the row drop to a
     writer that already holds the lock.
  2c. **A relocating ``record update`` enters ``move_record`` holding nothing**,
     so the paired sorted-order acquisition inside it is still a total order —
     holding one of the pair across the call is what would let two opposed
     cross-vault moves deadlock.
  3. **``lore reindex``'s rebuild** takes EVERY configured vault's lock, in
     sorted-path order, before the truncate-and-rescan — closing the window
     where a concurrent write's row is dropped by the truncate and missed by the
     rescan. This is deliberately the ONLY all-vault acquisition in lore: a
     named global serialization point, bounded by local disk-scan time.
  4. **Reindex is the documented repair path** for a writer killed between the
     body write and the index upsert.

Lock-scope and lock-order claims are asserted via an **instrumented lock
helper** (recording acquisitions and lock depth), never via timing. Races are
asserted with the suite convention: subprocess workers + a file-marker barrier,
``XDG_STATE_HOME``/``XDG_CONFIG_HOME`` fenced into ``tmp_path`` — these tests
never touch the live install.
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from conftest import (
    PLUGIN_ROOT,
    load_script,
    run_cli,
    write_default_config,
)
from test_sync_multi_vault import (
    _git,
    _make_bare_remote,
    _make_vault as _init_git_vault,
    _wire_remote,
)
from test_vault_write_lock import _collect, _index_names, _spawn

SID = "11111111-2222-4333-8444-555555555555"


# ── harness ────────────────────────────────────────────────────────────────


def _git_vault(path: Path) -> Path:
    """A committed git vault, gitignoring ``*.lock`` as every real vault does.

    ``config.installer`` scaffolds that ignore into every vault, which is what
    keeps lore's write-lock sidecars — including the ``.lore.lock`` that ``sync``
    itself now creates by taking the lock before probing the tree — out of the
    commits ``sync`` makes.
    """
    _init_git_vault(path, commit=False, dirty=False)
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "init")
    return path


def _locking():
    return importlib.import_module("lore.locking")


def _git_identity(path: Path) -> None:
    """Give a git repo a committer identity and disable signing.

    Test repos are created bare of any user config, and an unsigned commit with a
    known author is the only thing these tests need from it.
    """
    for key, val in (
        ("user.email", "t@e.st"),
        ("user.name", "Test"),
        ("commit.gpgsign", "false"),
    ):
        _git(path, "config", key, val)


@contextmanager
def _lock_depth_probe(sync_module):
    """Yield the git-call ``events`` recorded while instrumenting *sync_module*.

    ``events`` collects ``(git_subcommand, lock_depth_at_call)`` for every git
    invocation the module makes, so "was the lock held for this operation?" is
    read off the recording rather than inferred from wall-clock timing.
    """
    locking = _locking()
    events: list[tuple[str, int]] = []
    depth = {"n": 0}

    real_lock = locking.vault_write_lock
    real_git = sync_module._git

    @contextmanager
    def spy_lock(root, **kwargs):
        with real_lock(root, **kwargs):
            depth["n"] += 1
            try:
                yield
            finally:
                depth["n"] -= 1

    def spy_git(vault, *args):
        events.append((args[0], depth["n"]))
        return real_git(vault, *args)

    locking.vault_write_lock = spy_lock
    sync_module._git = spy_git
    try:
        yield events
    finally:
        locking.vault_write_lock = real_lock
        sync_module._git = real_git


def _depths(events, op: str) -> list[int]:
    return [d for name, d in events if name == op]


class _Args:
    """A CLI-handler argument namespace whose unset flags read as ``None``.

    The handlers reach for their flags with ``getattr(args, flag, default)``, so
    an in-process call only has to name the flags the case under test sets.
    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):
        return None


# ── 1 — sync: tree mutation inside the lock, network outside ───────────────


class TestSyncLockScope:
    def test_tree_mutation_is_locked_and_network_is_not(self, tmp_path):
        """``add``/``commit``/``rebase`` hold the vault lock; ``fetch``/``push`` do not.

        Holding lore's blocking, no-timeout flock across a network round-trip
        lets one hung remote starve every local writer, so hold time must be
        bounded by local git work only.
        """
        sync = load_script("lore.cli.sync")
        vault = _git_vault(tmp_path / "vault")
        remote = _make_bare_remote(tmp_path / "remote.git")
        _wire_remote(vault, remote, track=True)

        # Another device lands a commit, so this sync must actually rebase.
        peer = tmp_path / "peer"
        subprocess.run(
            ["git", "clone", str(remote), str(peer)], check=True, capture_output=True
        )
        _git_identity(peer)
        (peer / "peer.md").write_text("peer\n", encoding="utf-8")
        _git(peer, "add", "-A")
        _git(peer, "commit", "-m", "peer commit")
        _git(peer, "push", "origin", "HEAD")

        # Local dirt, so add + commit run too.
        (vault / "record.md").write_text("# a record\n", encoding="utf-8")

        say, say_err = sync._make_emitters("default", 9)
        with _lock_depth_probe(sync) as events:
            rc, pulled = sync._sync_one(vault, "lore: test sync", say, say_err)

        assert rc == 0
        assert pulled == 1, "the peer commit was never pulled — rebase did not run"

        for op in ("add", "commit", "rebase"):
            seen = _depths(events, op)
            assert seen, f"git {op} never ran"
            assert all(d >= 1 for d in seen), f"git {op} ran OUTSIDE the vault lock: {seen}"

        for op in ("fetch", "push"):
            seen = _depths(events, op)
            assert seen, f"git {op} never ran"
            assert all(d == 0 for d in seen), (
                f"git {op} ran INSIDE the vault lock ({seen}) — a hung remote would "
                "starve every local writer"
            )

    def test_unborn_branch_reset_is_locked(self, tmp_path):
        """The unborn-branch ``reset --hard`` is a tree mutation too."""
        sync = load_script("lore.cli.sync")
        remote = _make_bare_remote(tmp_path / "remote.git")

        seed = _git_vault(tmp_path / "seed")
        _wire_remote(seed, remote, track=True)

        vault = tmp_path / "vault"
        vault.mkdir()
        subprocess.run(["git", "init", str(vault)], check=True, capture_output=True)
        _git_identity(vault)
        branch = _git(seed, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        _git(vault, "checkout", "-b", branch)
        _git(vault, "remote", "add", "origin", str(remote))

        say, say_err = sync._make_emitters("default", 9)
        with _lock_depth_probe(sync) as events:
            rc, pulled = sync._sync_one(vault, "lore: test sync", say, say_err)

        assert rc == 0
        assert pulled >= 1, "the unborn vault never adopted the remote branch"
        seen = _depths(events, "reset")
        assert seen, "reset --hard never ran"
        assert all(d >= 1 for d in seen), f"reset --hard ran OUTSIDE the vault lock: {seen}"

    def test_clean_vault_sync_stays_a_no_op_and_creates_no_lock_file(self, tmp_path):
        """No-regression: a clean vault commits nothing — and is not even locked.

        The lock file is a sidecar INSIDE the vault, so locking a clean vault
        would leave it permanently un-clean. The clean answer comes from the
        pre-lock probe, creating nothing.
        """
        sync = load_script("lore.cli.sync")
        vault = _git_vault(tmp_path / "vault")
        before = _git(vault, "rev-parse", "HEAD").stdout.strip()

        say, say_err = sync._make_emitters("default", 9)
        rc, pulled = sync._sync_one(vault, "lore: test sync", say, say_err)

        assert (rc, pulled) == (0, 0)
        assert _git(vault, "rev-parse", "HEAD").stdout.strip() == before
        assert not (vault / ".lore.lock").exists(), (
            "a clean vault was locked — its tree can never read clean again"
        )
        assert _git(vault, "status", "--porcelain").stdout.strip() == ""

    def test_a_dirty_vault_commits_its_records_but_not_the_lock_file(self, tmp_path):
        """Records are committed; the lock sidecar sync itself created is not."""
        sync = load_script("lore.cli.sync")
        vault = _git_vault(tmp_path / "vault")
        (vault / "record.md").write_text("# a record\n", encoding="utf-8")

        say, say_err = sync._make_emitters("default", 9)
        rc, _pulled = sync._sync_one(vault, "lore: test sync", say, say_err)

        assert rc == 0
        tracked = _git(vault, "ls-files").stdout.split()
        assert "record.md" in tracked
        assert ".lore.lock" not in tracked


# ── 2 — sync racing a cross-vault move ─────────────────────────────────────


def _write_routed_config(config_home: Path, vault_a: Path, vault_b: Path) -> None:
    """Config routing ``decision`` records to A for ``team:alpha``, B for ``team:beta``."""
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    (lore_cfg / "config.json").write_text(
        json.dumps(
            {
                "vaults": [
                    {"name": "default", "scope": "default", "path": str(vault_a)},
                    {
                        "name": "alpha", "scope": "team",
                        "records": ["decision"], "path": str(vault_a),
                    },
                    {
                        "name": "beta", "scope": "team",
                        "records": ["decision"], "path": str(vault_b),
                    },
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )


class TestSyncVsCrossVaultMove:
    ITERS = 3

    def test_sync_never_commits_a_half_moved_record(self, tmp_path):
        """``lore sync`` racing a scope-change auto-move never commits the record
        into BOTH vaults (the copy-new → repoint → delete window)."""
        state = tmp_path / "state"
        state.mkdir()
        config_home = tmp_path / "cfg"

        for it in range(self.ITERS):
            vault_a = _git_vault(tmp_path / f"a{it}")
            vault_b = _git_vault(tmp_path / f"b{it}")
            _write_routed_config(config_home, vault_a, vault_b)

            r = run_cli(
                ["record", "create", "--kind", "decision", "--title", f"mover {it}",
                 "--team", "alpha"],
                vault=vault_a, state_dir=state, stdin_text="orig body\n",
                env_extra={"XDG_CONFIG_HOME": str(config_home)},
            )
            assert r.returncode == 0, r.stderr
            rid = r.stdout.strip()
            kind, name = rid.split("/", 1)
            _git(vault_a, "add", "-A")
            _git(vault_a, "commit", "-m", "record")

            barrier = tmp_path / f"_barrier_move{it}"
            procs = [
                _spawn(
                    vault=vault_a, state=state, config_home=config_home,
                    barrier=barrier, n=2, tag="sync", args=["sync"],
                ),
                _spawn(
                    vault=vault_a, state=state, config_home=config_home,
                    barrier=barrier, n=2, tag="move",
                    args=["record", "update", rid, "--team", "beta"],
                    stdin_text="moved body\n",
                ),
            ]
            assert not _collect(procs), f"iteration {it}: worker failed"

            for suffix in (".md", ".json"):
                rel = f"{kind}/{name}{suffix}"
                in_a = rel in _git(vault_a, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
                in_b = rel in _git(vault_b, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
                assert not (in_a and in_b), (
                    f"iteration {it}: {rel} committed in BOTH vaults — sync staged a "
                    "half-finished move"
                )
                on_disk = [v.name for v in (vault_a, vault_b) if (v / rel).exists()]
                assert len(on_disk) == 1, f"iteration {it}: {rel} on disk in {on_disk}"


class TestUpdateLockOrder:
    def test_a_relocating_update_holds_no_lock_across_move_record(
        self, tmp_path, monkeypatch
    ):
        """`record update` locks its read-modify-write — but lets go to relocate.

        ``move_record`` acquires source + destination as a SORTED set, and that
        total order is the only thing keeping two opposed cross-vault moves (A→B
        and B→A) from deadlocking on a lock with no timeout. Holding one of the
        pair across the call breaks the order, so the relocation branch must enter
        ``move_record`` with nothing held.
        """
        record_cli = load_script("lore.cli.record")
        store = importlib.import_module("lore.record.store")
        locking = _locking()

        state = tmp_path / "state"
        state.mkdir()
        config_home = tmp_path / "cfg"
        vault_a = tmp_path / "a"
        vault_b = tmp_path / "b"
        vault_a.mkdir()
        vault_b.mkdir()
        _write_routed_config(config_home, vault_a, vault_b)

        r = run_cli(
            ["record", "create", "--kind", "decision", "--title", "mover",
             "--team", "alpha"],
            vault=vault_a, state_dir=state, stdin_text="orig\n",
            env_extra={"XDG_CONFIG_HOME": str(config_home)},
        )
        assert r.returncode == 0, r.stderr
        record_id = r.stdout.strip()

        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
        monkeypatch.setenv("LORE_EMAIL", "tester@example.com")
        # Metadata-only update: pytest's captured stdin is not readable.
        monkeypatch.setattr(record_cli, "_read_stdin_body", lambda: "")

        depth = {"n": 0}
        depth_at_move: list[int] = []
        real_lock = locking.vault_write_lock
        real_move = store.move_record

        @contextmanager
        def spy_lock(root, **kwargs):
            with real_lock(root, **kwargs):
                depth["n"] += 1
                try:
                    yield
                finally:
                    depth["n"] -= 1

        def spy_move(*args, **kwargs):
            depth_at_move.append(depth["n"])
            return real_move(*args, **kwargs)

        monkeypatch.setattr(locking, "vault_write_lock", spy_lock)
        monkeypatch.setattr(store, "move_record", spy_move)

        rc = record_cli._cmd_record_update(_Args(record_id=record_id, team="beta"))

        assert rc == 0
        assert depth_at_move == [0], (
            f"the source-vault lock was still held entering move_record "
            f"(depth {depth_at_move}) — opposed cross-vault moves can deadlock"
        )
        assert (vault_b / "decision" / f"{record_id.split('/', 1)[1]}.md").exists(), (
            "the relocation did not land in the destination vault"
        )


# ── 3 — reindex: all-vault lock, sorted order, and the write race ──────────


_REINDEX_ORDER_WORKER = r"""
import sys
sys.path.insert(0, {plugin_root!r})
from contextlib import contextmanager

import lore.locking as locking
from lore.cli import areas

log_path = sys.argv[1]
real = locking.vault_write_lock

@contextmanager
def spy(root, **kwargs):
    with open(log_path, "a") as fh:
        fh.write(str(root) + "\n")
    with real(root, **kwargs):
        yield

locking.vault_write_lock = spy

count, error = areas.run_reindex()
if error is not None:
    sys.stderr.write(str(error))
    sys.exit(1)
"""


class TestReindexAllVaultLock:
    def test_reindex_locks_every_vault_in_sorted_path_order(self, tmp_path):
        """The one global serialization point: every configured vault's lock,
        acquired in sorted-path order so no acquisition order can deadlock."""
        state = tmp_path / "state"
        state.mkdir()
        config_home = tmp_path / "cfg"
        # Declared c → a → b, so config order is NOT sorted order.
        names = ["c_vault", "a_vault", "b_vault"]
        roots = []
        for name in names:
            root = tmp_path / name
            root.mkdir()
            roots.append(root)
        lore_cfg = config_home / "lore"
        lore_cfg.mkdir(parents=True)
        (lore_cfg / "config.json").write_text(
            json.dumps(
                {
                    "vaults": [
                        {"name": "default", "scope": "default", "path": str(roots[0])},
                        {"name": "second", "scope": "team",
                         "records": ["decision"], "path": str(roots[1])},
                        {"name": "third", "scope": "team",
                         "records": ["decision"], "path": str(roots[2])},
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        log = tmp_path / "_lock_order.log"
        import os

        env = dict(os.environ)
        env["XDG_STATE_HOME"] = str(state)
        env["XDG_CONFIG_HOME"] = str(config_home)
        env["LORE_EMAIL"] = "tester@example.com"
        proc = subprocess.run(
            [
                sys.executable, "-c",
                _REINDEX_ORDER_WORKER.format(plugin_root=str(PLUGIN_ROOT)),
                str(log),
            ],
            capture_output=True, text=True, env=env,
        )
        assert proc.returncode == 0, proc.stderr

        acquired = log.read_text(encoding="utf-8").split()
        assert acquired == sorted(str(r) for r in roots), (
            f"reindex lock order was {acquired}"
        )


class TestReindexVsWriteRace:
    ITERS = 3
    WRITERS = 3
    #: Enough on-disk records that ``rebuild``'s rescan is not instantaneous —
    #: the truncate-then-rescan window has to be wide enough for a concurrent
    #: writer to fall into it, or the test cannot distinguish locked from lucky.
    SEED = 40

    def test_a_create_racing_reindex_is_never_missing_from_the_index(self, tmp_path):
        """Unguarded, ``rebuild``'s truncate-then-rescan can drop a row the
        concurrent create just wrote and then miss its body on the rescan."""
        vault = tmp_path / "vault"
        vault.mkdir()
        state = tmp_path / "state"
        state.mkdir()
        config_home = state / "_xdg_config"
        write_default_config(config_home, vault)

        for i in range(self.SEED):
            r = run_cli(
                ["record", "create", "--kind", "decision", "--title", f"seed {i}"],
                vault=vault, state_dir=state, stdin_text="seed body\n",
            )
            assert r.returncode == 0, r.stderr

        for it in range(self.ITERS):
            barrier = tmp_path / f"_barrier{it}"
            n = self.WRITERS + 1
            procs = [
                _spawn(
                    vault=vault, state=state, config_home=config_home,
                    barrier=barrier, n=n, tag=f"writer{w}",
                    args=["record", "create", "--kind", "decision",
                          "--title", f"racer {it} {w}"],
                    stdin_text="body\n",
                )
                for w in range(self.WRITERS)
            ]
            procs.append(_spawn(
                vault=vault, state=state, config_home=config_home,
                barrier=barrier, n=n, tag="reindex", args=["reindex"],
            ))
            assert not _collect(procs), f"iteration {it}: worker failed"

            on_disk = {p.stem for p in (vault / "decision").glob("*.md")}
            indexed = _index_names(state, "vault", "decision")
            assert indexed == on_disk, (
                f"iteration {it}: index/disk divergence — missing from index: "
                f"{sorted(on_disk - indexed)}, stale in index: {sorted(indexed - on_disk)}"
            )


# ── 4 — reindex as the documented repair path ──────────────────────────────


_KILLED_WRITER = r"""
import os, sys
sys.path.insert(0, {plugin_root!r})

import lore.record.store as store

def die(*args, **kwargs):
    # Killed INSIDE the vault write lock, between the body write and the index
    # upsert — the flock is released by the kernel, the divergence is not.
    os._exit(9)

store.update_index = die

from lore.cli import dispatch
args = dispatch.build_parser().parse_args(
    ["record", "create", "--kind", "decision", "--title", "orphaned record"]
)
sys.exit(args.func(args))
"""


class TestKillMidCriticalSectionRepair:
    def test_reindex_repairs_a_writer_killed_before_its_index_upsert(self, tmp_path):
        """A per-slot timeout can kill a writer mid-critical-section. The body is
        on disk with no index row; ``lore reindex`` is the repair path."""
        import os

        vault = tmp_path / "vault"
        vault.mkdir()
        state = tmp_path / "state"
        state.mkdir()
        config_home = state / "_xdg_config"
        write_default_config(config_home, vault)

        # Seed one healthy record so the index db exists and reindex has company.
        r = run_cli(
            ["record", "create", "--kind", "decision", "--title", "healthy"],
            vault=vault, state_dir=state, stdin_text="fine\n",
        )
        assert r.returncode == 0, r.stderr

        env = dict(os.environ)
        env["XDG_STATE_HOME"] = str(state)
        env["XDG_CONFIG_HOME"] = str(config_home)
        env["LORE_EMAIL"] = "tester@example.com"
        proc = subprocess.run(
            [sys.executable, "-c", _KILLED_WRITER.format(plugin_root=str(PLUGIN_ROOT))],
            capture_output=True, text=True, env=env,
        )
        assert proc.returncode == 9, f"writer was not killed mid-write: {proc.stderr}"

        on_disk = {p.stem for p in (vault / "decision").glob("*.md")}
        orphans = on_disk - _index_names(state, "vault", "decision")
        assert len(orphans) == 1, (
            f"expected exactly one disk/index divergence, got {orphans}"
        )

        r = run_cli(["reindex"], vault=vault, state_dir=state)
        assert r.returncode == 0, r.stderr
        assert _index_names(state, "vault", "decision") == on_disk, (
            "reindex did not repair the divergence it is documented to repair"
        )

        # And the lock the killed writer held is free — the next writer proceeds.
        r = run_cli(
            ["record", "create", "--kind", "decision", "--title", "after the kill"],
            vault=vault, state_dir=state, stdin_text="fine\n",
        )
        assert r.returncode == 0, r.stderr


# ── 4b — record delete: the index commit lands inside the lock ─────────────


class _ConnProbe:
    """A ``sqlite3.Connection`` proxy that records the lock depth at ``commit``.

    ``sqlite3.Connection`` rejects attribute assignment, so the recording wrapper
    has to be a proxy rather than a patched method.
    """

    def __init__(self, conn, record):
        self._conn = conn
        self._record = record

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def commit(self):
        self._record()
        return self._conn.commit()


class TestDeleteCommitsUnderTheLock:
    def test_the_index_commit_lands_inside_the_vault_lock(self, tmp_path, monkeypatch):
        """`record delete` commits the row drop before releasing the lock.

        Symmetric with the create path. A commit after the release publishes the
        drop to a writer that already holds the lock and has therefore already
        decided the record exists.
        """
        record_cli = load_script("lore.cli.record")
        store = importlib.import_module("lore.record.store")
        locking = _locking()

        vault = tmp_path / "vault"
        vault.mkdir()
        state = tmp_path / "state"
        state.mkdir()
        config_home = state / "_xdg_config"
        write_default_config(config_home, vault)

        r = run_cli(
            ["record", "create", "--kind", "decision", "--title", "doomed"],
            vault=vault, state_dir=state, stdin_text="body\n",
        )
        assert r.returncode == 0, r.stderr
        record_id = r.stdout.strip()

        monkeypatch.setenv("XDG_STATE_HOME", str(state))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
        monkeypatch.setenv("LORE_EMAIL", "tester@example.com")

        depth = {"n": 0}
        commit_depths: list[int] = []
        real_lock = locking.vault_write_lock
        real_txn = store.index_transaction

        @contextmanager
        def spy_lock(root, **kwargs):
            with real_lock(root, **kwargs):
                depth["n"] += 1
                try:
                    yield
                finally:
                    depth["n"] -= 1

        @contextmanager
        def spy_txn(*args, **kwargs):
            with real_txn(*args, **kwargs) as conn:
                yield _ConnProbe(conn, lambda: commit_depths.append(depth["n"]))

        monkeypatch.setattr(locking, "vault_write_lock", spy_lock)
        monkeypatch.setattr(store, "index_transaction", spy_txn)

        rc = record_cli._cmd_record_delete(_Args(record_id=record_id))

        assert rc == 0
        assert commit_depths, "the delete never committed"
        assert all(d > 0 for d in commit_depths), (
            f"the index commit ran with the vault lock released: {commit_depths}"
        )


# ── 5 — flush: session-key lock + the vault lock ───────────────────────────


class TestFlushCommitLockGranularity:
    def test_commit_takes_the_session_key_lock_then_the_vault_lock(
        self, tmp_path, monkeypatch
    ):
        """The commit takes BOTH locks, session-key first.

        The session-key lock is the flip-and-commit atomicity unit. The vault lock
        is what keeps a concurrent ``lore sync`` — which holds the vault lock
        across its ``git add -A`` → ``commit`` — from staging the whole dirty tree
        between this commit's ``add`` and its ``commit``, which would break the
        flush's explicit-paths-only contract. The order is fixed session-key →
        vault so the two can never be taken in opposition.
        """
        flush = load_script("lore.cli.flush")
        locking = _locking()
        vault = _git_vault(tmp_path / "vault")
        (vault / "session").mkdir()
        (vault / "session" / f"{SID}.json").write_text(
            json.dumps({"kind": "session", "title": "s", "status": "clean"}),
            encoding="utf-8",
        )
        (vault / "session" / f"{SID}.md").write_text("session body\n", encoding="utf-8")

        events: list[str] = []
        real_session = locking.session_write_lock
        real_vault = locking.vault_write_lock
        real_git = flush._git

        @contextmanager
        def spy_session(root, key, **kwargs):
            with real_session(root, key, **kwargs):
                events.append(f"session:{root}:{key}")
                try:
                    yield
                finally:
                    events.append("session-release")

        @contextmanager
        def spy_vault(root, **kwargs):
            with real_vault(root, **kwargs):
                events.append(f"vault:{root}")
                try:
                    yield
                finally:
                    events.append("vault-release")

        def spy_git(v, *args):
            events.append(f"git:{args[0]}")
            return real_git(v, *args)

        monkeypatch.setattr(locking, "session_write_lock", spy_session)
        monkeypatch.setattr(locking, "vault_write_lock", spy_vault)
        monkeypatch.setattr(flush, "_git", spy_git)
        rc = flush._flush_commit(vault, SID, push=False)

        assert rc == 0
        assert f"session:{vault}:{SID}" in events, (
            f"the flush commit did not take the session-key lock: {events}"
        )
        assert f"vault:{vault}" in events, (
            f"the flush commit did not take the vault lock — a concurrent `sync` "
            f"can stage the whole tree into it: {events}"
        )
        # Fixed order: session-key acquired first, vault nested inside it.
        assert events.index(f"session:{vault}:{SID}") < events.index(f"vault:{vault}"), (
            f"locks taken in the wrong order (must be session-key → vault): {events}"
        )
        # Both git mutations happen while BOTH locks are held.
        vault_span = (events.index(f"vault:{vault}"), events.index("vault-release"))
        for op in ("add", "commit"):
            assert vault_span[0] < events.index(f"git:{op}") < vault_span[1], (
                f"git {op} ran outside the vault lock: {events}"
            )


class TestFlushVsCaptureRace:
    ITERS = 3

    def test_a_capture_racing_flush_never_lands_a_dirty_sidecar_in_the_commit(
        self, tmp_path, monkeypatch
    ):
        """The flip and the commit are ONE unit under the session-key lock, so a
        candidate captured concurrently either precedes the flip or follows the
        commit — never lands ``dirty`` inside the flush commit."""
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "")
        monkeypatch.setenv("CLAUDE_SESSION_ID", "")

        vault = _git_vault(tmp_path / "vault")
        state = tmp_path / "state"
        state.mkdir()
        config_home = state / "_xdg_config"
        write_default_config(config_home, vault)

        for it in range(self.ITERS):
            sid = f"1111111{it}-2222-4333-8444-555555555555"
            r = run_cli(
                ["session", "candidate", "--session-id", sid,
                 "--kind", "spec", "--phase", "Plan"],
                vault=vault, state_dir=state, stdin_text="first candidate\n",
            )
            assert r.returncode == 0, r.stderr
            _git(vault, "add", "-A")
            _git(vault, "commit", "-m", f"baseline {it}")

            barrier = tmp_path / f"_barrier_flush{it}"
            procs = [
                _spawn(
                    vault=vault, state=state, config_home=config_home,
                    barrier=barrier, n=2, tag="capture",
                    args=["session", "candidate", "--session-id", sid,
                          "--kind", "spec", "--phase", "Build"],
                    stdin_text="racing candidate\n",
                ),
                _spawn(
                    vault=vault, state=state, config_home=config_home,
                    barrier=barrier, n=2, tag="flush",
                    args=["flush", "--session-id", sid],
                ),
            ]
            assert not _collect(procs), f"iteration {it}: worker failed"

            sha = _git(
                vault, "rev-list", "-1", "--grep", f"session: flush {sid}", "HEAD"
            ).stdout.strip()
            assert sha, f"iteration {it}: the flush never committed"
            blob = _git(vault, "show", f"{sha}:session/{sid}.json").stdout
            assert json.loads(blob)["status"] == "clean", (
                f"iteration {it}: the flush commit contains a DIRTY sidecar — a "
                "capture was staged mid-flip"
            )
