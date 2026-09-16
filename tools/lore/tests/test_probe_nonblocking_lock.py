"""EPHEMERAL assumption probe — task/a-contended-sync-ends-immediately-at-exit-zero.

Not part of the permanent suite. Delete this whole file after reading the
probe report; nothing here is meant to survive into the real implementation's
test suite (the real tests belong in test_vault_write_lock.py /
test_sync_multi_vault.py once the feature is built).

Question being probed: does adding a non-blocking (``flock(LOCK_EX|LOCK_NB)``)
acquisition mode compose with locking.py's per-thread reentrancy bookkeeping
and the sorted multi-vault ExitStack, and can BlockingIOError (raised on
contention) be told apart from a genuinely broken lock before either reaches
cli/sync.py's generic ``except OSError`` handler?

``_flock_nb`` below is a probe clone of ``locking._flock`` — same reentrancy
bookkeeping (same ``_local.depths`` thread-local, same ``_resolve_key``), with
the one change under test: no blocking fallback. It exists only to answer the
composition question; it is not a suggested implementation.
"""

from __future__ import annotations

import fcntl
import subprocess
import sys
import textwrap
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest

from lore import locking


@contextmanager
def _flock_nb(lock_path: Path):
    key = str(locking._resolve_key(lock_path))
    depths = locking._depths()
    if depths.get(key):
        depths[key] += 1
        try:
            yield
        finally:
            depths[key] -= 1
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_path, "a")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fd.close()
        raise
    depths[key] = 1
    try:
        yield
    finally:
        depths[key] -= 1
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        finally:
            lock_fd.close()


def _vault_write_lock_nb(vault_root):
    return _flock_nb(Path(vault_root) / locking.VAULT_LOCK_NAME)


def _hold_lock_in_subprocess(lock_path: Path, hold_seconds: float) -> subprocess.Popen:
    """A genuine second PROCESS holds a real flock on lock_path."""
    code = textwrap.dedent(
        f"""
        import fcntl, time
        fd = open({str(lock_path)!r}, "a")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        time.sleep({hold_seconds})
        """
    )
    return subprocess.Popen([sys.executable, "-c", code])


def _wait_until_contended(lock_path: Path, timeout: float = 3.0) -> None:
    """Poll with our own non-blocking flock until it reports contention."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        fd = open(lock_path, "a")
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except BlockingIOError:
            return
        finally:
            fd.close()
        time.sleep(0.02)
    pytest.fail("subprocess never actually took the lock")


def test_nonblocking_reentrant_same_thread_succeeds_and_balances(tmp_path):
    """Q1: a non-blocking acquisition of a lock THIS thread already holds must
    go through the existing reentrancy depth-bump path (never touch the OS
    flock a second time), and release bookkeeping must return to zero."""
    vault = tmp_path / "v"
    vault.mkdir()
    key = str(locking._resolve_key(vault / locking.VAULT_LOCK_NAME))

    with locking.vault_write_lock(vault):
        assert locking._depths().get(key) == 1
        with _vault_write_lock_nb(vault):
            assert locking._depths()[key] == 2
        assert locking._depths()[key] == 1
    assert locking._depths().get(key, 0) == 0


def test_nonblocking_raises_blockingioerror_on_real_contention(tmp_path):
    """Q_discriminate, half 1: a REAL second process holding the lock makes a
    non-blocking acquire raise BlockingIOError, not swallow or hang."""
    vault = tmp_path / "v"
    vault.mkdir()
    lock_path = vault / locking.VAULT_LOCK_NAME
    lock_path.touch()
    proc = _hold_lock_in_subprocess(lock_path, hold_seconds=3)
    try:
        _wait_until_contended(lock_path)
        with pytest.raises(BlockingIOError):
            with _vault_write_lock_nb(vault):
                pass
    finally:
        proc.wait(timeout=5)


def test_broken_lock_raises_oserror_that_is_not_blockingioerror(tmp_path):
    """Q_discriminate, half 2: a genuinely broken lock (path occupied by a
    directory, not a file) raises OSError but NOT BlockingIOError."""
    vault = tmp_path / "v"
    vault.mkdir()
    (vault / locking.VAULT_LOCK_NAME).mkdir()  # broken: a dir where a file goes
    with pytest.raises(OSError) as exc_info:
        with _vault_write_lock_nb(vault):
            pass
    assert not isinstance(exc_info.value, BlockingIOError)


def test_except_blockingioerror_before_oserror_discriminates_both_real_cases(tmp_path):
    """The actual discrimination cli/sync.py needs: catching BlockingIOError
    ahead of the generic ``except OSError`` produces a DIFFERENT classification
    for a contended lock (real second process) than for a broken one (real
    directory-in-place-of-file), with no mocking on either side."""
    contended_vault = tmp_path / "contended"
    contended_vault.mkdir()
    lock_path = contended_vault / locking.VAULT_LOCK_NAME
    lock_path.touch()
    proc = _hold_lock_in_subprocess(lock_path, hold_seconds=3)

    broken_vault = tmp_path / "broken"
    broken_vault.mkdir()
    (broken_vault / locking.VAULT_LOCK_NAME).mkdir()

    def classify(vault) -> str:
        try:
            with _vault_write_lock_nb(vault):
                return "acquired"
        except BlockingIOError:
            return "in-progress"
        except OSError:
            return "broken"

    try:
        _wait_until_contended(lock_path)
        assert classify(contended_vault) == "in-progress"
        assert classify(broken_vault) == "broken"
    finally:
        proc.wait(timeout=5)


def test_exitstack_releases_already_acquired_lock_on_partial_batch_failure(tmp_path):
    """Q2: sorted multi-vault ExitStack, one vault contended (real second
    process) partway through the batch. The already-acquired vault's lock
    must come back released when the ExitStack unwinds — contextlib only
    registers a cleanup for a context whose __enter__ already succeeded, so a
    failed entry is never pushed and needs no explicit release path of its
    own. Prove it end to end rather than trusting that mechanism."""
    vault_a = tmp_path / "a"
    vault_a.mkdir()
    vault_b = tmp_path / "b"
    vault_b.mkdir()
    lock_b = vault_b / locking.VAULT_LOCK_NAME
    lock_b.touch()
    proc = _hold_lock_in_subprocess(lock_b, hold_seconds=3)
    try:
        _wait_until_contended(lock_b)

        raised = False
        with ExitStack() as stack:
            stack.enter_context(_vault_write_lock_nb(vault_a))
            try:
                stack.enter_context(_vault_write_lock_nb(vault_b))
            except BlockingIOError:
                raised = True
        assert raised, "expected the batch's second acquisition to contend"

        # vault_a's lock must be released now that the ExitStack unwound —
        # a fresh non-blocking flock on it from THIS process must succeed.
        fd = open(vault_a / locking.VAULT_LOCK_NAME, "a")
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        finally:
            fd.close()

        # vault_b is still genuinely held by the other process — untouched.
        fd = open(lock_b, "a")
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            fd.close()
    finally:
        proc.wait(timeout=5)
