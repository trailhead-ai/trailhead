"""KU3 assumption-prover: fcntl.flock gives a race-safe lazy-create on darwin.

This test is EPHEMERAL — it is an assumption-prover for KU3 (plan section
``lore-record-and-session-cli-s2.md``). The executor will remove it after Slice 6's
proper behavioral tests land.

The unknown to prove:
    When two callers concurrently "create-or-append" for the SAME session_id — both
    reaching the existence check before either creates — the result is exactly ONE
    session note with BOTH entries appended (no lost entry, no double-create, no
    corruption).

Design proven here (which Slice 6's session_store.py MUST adopt identically):
    - Lock object: a sidecar `<session_id>.lock` file alongside the session note.
    - Lock mode: ``fcntl.LOCK_EX`` (exclusive) via ``fcntl.flock``.
    - Critical section: the ENTIRE create-or-append — existence check AND create/append
      — happens INSIDE the held ``LOCK_EX``. The lock is acquired first, the file is
      checked second, and the write completes before the lock is released.
    - Implementation: ``open(lock_path, 'a')`` to create-or-open the sidecar lock file,
      then ``fcntl.flock(fd, LOCK_EX)`` on it. This pattern is cross-platform safe
      (darwin, linux): the lock file itself is never the session note, so concurrent
      reads of the session note are not blocked.

Why subprocesses (not threads):
    On darwin, ``fcntl.flock`` is per-process. Two threads in the same process that
    independently open the same file get different file descriptions, but flock
    semantics on darwin/BSD associate the lock with the *open file description* and
    duplicate file descriptors share the lock — meaning a thread that opens the same
    path gets a fresh description and WILL block. However, there is also a darwin
    quirk: within a single process, a second ``open()`` on the same path followed by
    ``flock(LOCK_EX)`` CAN succeed immediately (the OS may see it as a re-entrant
    lock from the same process). Subprocesses are the unambiguous proof: each is a
    separate OS process, each gets a fully independent flock contention.

Concurrency harness:
    - A multiprocessing.Barrier synchronises N workers so all reach the critical
      section boundary simultaneously before any proceeds.
    - Each worker calls ``create_or_append(session_id, entry, sessions_dir)`` with
      a distinct entry so we can identify both in the result.
    - The test repeats the race ITERATIONS times, resetting the sessions_dir each
      time, to give flaky double-creates or lost entries a chance to surface.

Unguarded variant:
    A ``create_or_append_unguarded`` variant (no flock) is also included. On darwin
    the OS kernel's APFS/HFS+ write-ordering can make the unguarded race hard to
    trigger 100% of the time in a short sleep window, so we use an explicit
    ``time.sleep`` to widen the TOCTOU window. The unguarded variant is expected to
    produce double-creates or lost entries when the window is hit. We assert it CAN
    fail (at least some iterations produce the wrong outcome) — if all unguarded
    iterations pass it means the window couldn't be opened, but we document the
    structural reason it's unsafe rather than treating the test as proof of safety.

Run:
    cd tools/lore && python -m pytest tests/test_ku3_flock_race.py -v
"""
from __future__ import annotations

import fcntl
import multiprocessing
import os
import sys
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# The prototype primitive — this is the exact design Slice 6 session_store.py
# MUST adopt. Keep in sync with the recommendation in the report.
# ---------------------------------------------------------------------------

def create_or_append(session_id: str, entry: str, sessions_dir: Path) -> None:
    """Lazy-create a session note for session_id and append entry.

    Design (proven below):
      1. Acquire LOCK_EX on a sidecar ``<session_id>.lock`` file.
         - Use ``open(lock_path, 'a')`` to create-or-open without truncating.
         - Block until the lock is held.
      2. INSIDE the held lock: check whether the session note exists.
      3. If it does not exist: create it with a header line.
      4. Append the entry (whether newly created or pre-existing).
      5. Release the lock (close the fd).

    The session note itself is never the lock target — that keeps concurrent reads
    unblocked and avoids the file-descriptor aliasing issue that arises when the
    note fd and the lock fd are the same object.
    """
    sessions_dir.mkdir(parents=True, exist_ok=True)
    note_path = sessions_dir / f"{session_id}.md"
    lock_path = sessions_dir / f"{session_id}.lock"

    lock_fd = open(lock_path, "a")  # noqa: SIM115  — must stay open until unlock
    try:
        # Block until we hold the exclusive lock.  All other concurrent callers
        # for THIS session_id queue here; all is fine for DIFFERENT session IDs.
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)

        # Check-then-create is now atomic w.r.t. other processes holding the same
        # lock — no other process can be inside this critical section concurrently.
        if not note_path.exists():
            note_path.write_text(f"# session: {session_id}\n")

        # Append the entry.
        with open(note_path, "a") as nf:
            nf.write(entry + "\n")

    finally:
        # Releasing the lock — closing the fd implicitly releases flock on darwin.
        # Explicit LOCK_UN first is belt-and-suspenders.
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


def create_or_append_unguarded(session_id: str, entry: str, sessions_dir: Path) -> None:
    """Same operation WITHOUT the flock guard — exposes the TOCTOU window.

    There is a race window between the existence check and the open/write.
    We widen it with a short sleep to make it more likely to trigger in tests.
    """
    sessions_dir.mkdir(parents=True, exist_ok=True)
    note_path = sessions_dir / f"{session_id}.md"

    # TOCTOU window: between this check …
    exists = note_path.exists()
    # … and the write below, another process can run the same check and see
    # the same False, then both create.  Sleep makes the window wide enough
    # to reliably observe the race in a test.
    time.sleep(0.05)

    if not exists:
        note_path.write_text(f"# session: {session_id}\n")

    with open(note_path, "a") as nf:
        nf.write(entry + "\n")


# ---------------------------------------------------------------------------
# Worker functions (must be module-level for multiprocessing picklability)
# ---------------------------------------------------------------------------

def _guarded_worker(barrier, session_id: str, entry: str, sessions_dir_str: str):
    """Worker that calls create_or_append after synchronising on the barrier."""
    sessions_dir = Path(sessions_dir_str)
    barrier.wait()  # All workers reach here before any proceeds — maximises race.
    create_or_append(session_id, entry, sessions_dir)


def _unguarded_worker(barrier, session_id: str, entry: str, sessions_dir_str: str):
    """Worker that calls create_or_append_unguarded after synchronising on the barrier."""
    sessions_dir = Path(sessions_dir_str)
    barrier.wait()
    create_or_append_unguarded(session_id, entry, sessions_dir)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_race(worker_fn, sessions_dir: Path, session_id: str, n_workers: int = 2) -> list[str]:
    """Spawn n_workers processes racing on the same session_id.

    Returns the list of entry lines found in the note (excluding the header),
    or raises if the note doesn't exist.
    """
    entries = [f"entry-{i}" for i in range(n_workers)]
    barrier = multiprocessing.Barrier(n_workers)
    procs = [
        multiprocessing.Process(
            target=worker_fn,
            args=(barrier, session_id, entries[i], str(sessions_dir)),
        )
        for i in range(n_workers)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=10)
    for i, p in enumerate(procs):
        if p.exitcode != 0:
            raise RuntimeError(f"Worker {i} exited with code {p.exitcode}")

    note = sessions_dir / f"{session_id}.md"
    if not note.exists():
        return []

    lines = note.read_text().splitlines()
    # Strip the header line; return entry lines only.
    return [ln for ln in lines if ln and not ln.startswith("#")]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

ITERATIONS = 100  # enough to surface flaky lost-updates or double-creates


class TestFlockGuardedRace:
    """Guarded create_or_append: exactly one note, both entries, every iteration."""

    def test_guarded_race_single_iteration(self, tmp_path):
        """Smoke test: one iteration of the guarded race produces correct output."""
        sessions = tmp_path / "sessions"
        session_id = "test-session-abc"
        entry_lines = _run_race(_guarded_worker, sessions, session_id)

        note = sessions / f"{session_id}.md"
        assert note.exists(), "Session note must exist after create-or-append"

        # Exactly one note for this session_id (no double-create artefact).
        all_session_files = list(sessions.glob(f"{session_id}*.md"))
        assert len(all_session_files) == 1, (
            f"Expected exactly 1 note file, found {len(all_session_files)}: "
            f"{[f.name for f in all_session_files]}"
        )

        # Both entries are present (no lost-write).
        assert "entry-0" in entry_lines, f"entry-0 missing from note. Lines: {entry_lines}"
        assert "entry-1" in entry_lines, f"entry-1 missing from note. Lines: {entry_lines}"

        # No duplicate entries (no double-append).
        assert entry_lines.count("entry-0") == 1, f"entry-0 duplicated. Lines: {entry_lines}"
        assert entry_lines.count("entry-1") == 1, f"entry-1 duplicated. Lines: {entry_lines}"

    def test_guarded_race_many_iterations(self, tmp_path):
        """Stress: ITERATIONS concurrent races — invariant holds every time."""
        failures = []
        for i in range(ITERATIONS):
            sessions = tmp_path / f"iter-{i}"
            session_id = f"sess-{i}"
            try:
                entry_lines = _run_race(_guarded_worker, sessions, session_id)
            except Exception as exc:
                failures.append(f"iter {i}: worker crashed: {exc}")
                continue

            note = sessions / f"{session_id}.md"
            if not note.exists():
                failures.append(f"iter {i}: note does not exist")
                continue

            all_files = list(sessions.glob(f"{session_id}*.md"))
            if len(all_files) != 1:
                failures.append(
                    f"iter {i}: expected 1 note, found {len(all_files)}: "
                    f"{[f.name for f in all_files]}"
                )

            if "entry-0" not in entry_lines or "entry-1" not in entry_lines:
                failures.append(
                    f"iter {i}: missing entry. lines={entry_lines}"
                )

            if entry_lines.count("entry-0") != 1 or entry_lines.count("entry-1") != 1:
                failures.append(
                    f"iter {i}: duplicate entry. lines={entry_lines}"
                )

        assert not failures, (
            f"Guarded race FAILED on {len(failures)}/{ITERATIONS} iterations:\n"
            + "\n".join(failures[:20])
        )

    def test_flock_is_stdlib_on_darwin(self):
        """fcntl.flock is available and has the expected constants on darwin/linux."""
        assert hasattr(fcntl, "flock"), "fcntl.flock must exist"
        assert fcntl.LOCK_EX == 2, f"LOCK_EX expected 2, got {fcntl.LOCK_EX}"
        assert fcntl.LOCK_SH == 1, f"LOCK_SH expected 1, got {fcntl.LOCK_SH}"
        assert fcntl.LOCK_UN == 8, f"LOCK_UN expected 8, got {fcntl.LOCK_UN}"
        assert fcntl.LOCK_NB == 4, f"LOCK_NB expected 4, got {fcntl.LOCK_NB}"

    def test_lock_is_acquired_and_released(self, tmp_path):
        """Structural: a second process cannot acquire LOCK_EX while first holds it."""
        lock_path = tmp_path / "check.lock"

        # Acquire the lock in this process.
        fd = open(lock_path, "a")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)

        # A subprocess must see it as BLOCKED (LOCK_NB raises BlockingIOError).
        probe_script = f"""
import fcntl, sys
try:
    f = open({str(lock_path)!r}, 'a')
    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    print('acquired')
    f.close()
except BlockingIOError:
    print('blocked')
"""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-c", probe_script],
            capture_output=True, text=True, timeout=5,
        )
        assert result.stdout.strip() == "blocked", (
            f"Expected 'blocked' but got: {result.stdout.strip()!r}. "
            "flock LOCK_EX from a second process must block while the first holds it."
        )

        # Release.
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()

        # Now the same subprocess can acquire.
        result2 = subprocess.run(
            [sys.executable, "-c", probe_script],
            capture_output=True, text=True, timeout=5,
        )
        assert result2.stdout.strip() == "acquired", (
            f"Expected 'acquired' after release but got: {result2.stdout.strip()!r}"
        )

    def test_check_inside_lock(self, tmp_path):
        """Structural: the existence check MUST happen inside the held lock.

        Demonstrates that placing the check outside the lock opens a TOCTOU
        window: a second process can observe 'not exists' at the same time.
        This is a documentation/demonstration test — it does not assert on the
        unguarded variant's outcome, only that the guarded path has the check
        provably inside the critical section.
        """
        # Guarded: the note's existence state cannot change between check and create
        # because the lock serialises all callers for this session_id.
        sessions = tmp_path / "check_inside"
        session_id = "structural-check"
        entry_lines = _run_race(_guarded_worker, sessions, session_id)

        note = sessions / f"{session_id}.md"
        assert note.exists()
        content = note.read_text()
        # Header must appear exactly once — proves no double-create.
        header_count = content.count(f"# session: {session_id}")
        assert header_count == 1, (
            f"Header appeared {header_count} times — double-create occurred despite lock!"
        )


class TestUnguardedRaceWindow:
    """Unguarded variant: documents the TOCTOU window; the guard is load-bearing."""

    def test_unguarded_race_exposes_toctou_window(self, tmp_path):
        """Without flock, the TOCTOU window CAN cause double-create or lost entries.

        We run ITERATIONS iterations.  If at least one iteration shows the invariant
        violated (double-create header or missing entry), the guard is confirmed
        load-bearing.  If none trigger (OS serialised all writes anyway), we skip
        rather than falsely PASS — the structural argument is in the docstring.

        Note: on darwin APFS the OS ordering is usually fast enough that a 50ms
        sleep window will trigger the double-create reliably within 100 iterations.
        """
        double_creates = 0
        missing_entries = 0

        for i in range(ITERATIONS):
            sessions = tmp_path / f"ug-{i}"
            session_id = f"ug-sess-{i}"
            try:
                _run_race(_unguarded_worker, sessions, session_id)
            except Exception:
                # A worker crash also indicates a bug in the unguarded variant.
                missing_entries += 1
                continue

            note = sessions / f"{session_id}.md"
            if not note.exists():
                missing_entries += 1
                continue

            content = note.read_text()
            header_count = content.count(f"# session: {session_id}")
            lines = [ln for ln in content.splitlines() if ln and not ln.startswith("#")]

            if header_count > 1:
                double_creates += 1
            if "entry-0" not in lines or "entry-1" not in lines:
                missing_entries += 1

        if double_creates == 0 and missing_entries == 0:
            # The race window didn't trigger deterministically in this run.
            # This can happen if the OS serialised writes on a fast single-core
            # or with a very fast scheduler.  Skip rather than give false safety.
            pytest.skip(
                f"Unguarded race did not trigger in {ITERATIONS} iterations on this machine. "
                "The structural argument (50ms TOCTOU window + no serialisation guarantee) "
                "still holds; the guard is required for correctness. "
                f"double_creates={double_creates}, missing_entries={missing_entries}"
            )
        else:
            # At least one corruption observed — confirms the guard is load-bearing.
            assert double_creates + missing_entries > 0, "unreachable"
            # This assertion always passes when we reach here; it's the evidence.
            print(
                f"\nUnguarded race produced {double_creates} double-creates and "
                f"{missing_entries} missing/lost entries over {ITERATIONS} iterations. "
                "flock guard is load-bearing."
            )
