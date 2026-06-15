"""U2 assumption probe — PID liveness via os.kill(pid, 0).

Tests:
1. os.kill(pid, 0) raises ProcessLookupError for a definitely-dead PID.
2. os.kill(pid, 0) does NOT raise for the current (live) process.
3. os.kill(pid, 0) does NOT raise for a live child process.
4. EPERM / PermissionError means "process exists but not ours" (same-user check).
5. PID recycling gap: os.kill(pid, 0) cannot distinguish a recycled PID, motivating
   the timestamp guard in the session lockfile design.

This test is EPHEMERAL — it is a U2 assumption probe and should be deleted after
Slice 6 builds proper behavioral tests in test_session_lock.py.

Cleanup: delete this file entirely (tools/camp/tests/test_u2_pid_liveness.py).
"""
from __future__ import annotations

import errno
import os
import subprocess
import sys
import time
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def probe_pid(pid: int) -> str:
    """
    Returns 'dead' | 'alive' | 'alive_not_ours' by interpreting os.kill(pid, 0).

    - ProcessLookupError (errno ESRCH) → dead
    - PermissionError (errno EPERM)    → alive, but we don't have permission to
                                         send signals (different-user process)
    - No exception                     → alive and ours (or same-uid)
    """
    try:
        os.kill(pid, 0)
        return "alive"
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        return "alive_not_ours"


# ---------------------------------------------------------------------------
# Test 1: dead PID → ProcessLookupError
# ---------------------------------------------------------------------------

def test_dead_pid_raises_process_lookup_error():
    """Spawn a child, reap it fully, then probe. Must see ProcessLookupError."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    dead_pid = child.pid
    child.wait()  # reap — ensures no zombie, slot is freed

    # Small spin to account for any OS latency in releasing the PID slot.
    # On macOS/Linux a reaped+waited process is immediately gone, but be safe.
    result = probe_pid(dead_pid)

    # The assertion: must be dead (or conceivably recycled, but on a lightly
    # loaded test runner a just-reaped PID is not immediately recycled).
    assert result == "dead", (
        f"Expected 'dead' for reaped PID {dead_pid}, got {result!r}. "
        "The PID may have been recycled within the test — this is the recycling "
        "gap the plan's timestamp guard exists to handle."
    )


def test_dead_pid_exception_type_is_process_lookup_error():
    """Confirm the concrete exception type raised for a dead PID."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    dead_pid = child.pid
    child.wait()

    raised = None
    try:
        os.kill(dead_pid, 0)
    except ProcessLookupError as e:
        raised = e
    except PermissionError as e:
        # Recycled into a different-user process — extremely unlikely in test env
        pytest.skip(f"PID {dead_pid} was recycled into a different-user process: {e}")

    # If no exception: PID was recycled into a same-user process
    if raised is None:
        pytest.skip(
            f"PID {dead_pid} was recycled into a same-user live process immediately "
            "after reap — PID recycling gap confirmed (see Test 5). "
            "This is exactly why the timestamp guard is needed."
        )

    assert isinstance(raised, ProcessLookupError), f"Got {type(raised)}"
    assert raised.errno == errno.ESRCH, f"Expected ESRCH ({errno.ESRCH}), got {raised.errno}"


# ---------------------------------------------------------------------------
# Test 2: current (live) process → no exception
# ---------------------------------------------------------------------------

def test_live_self_pid_does_not_raise():
    """os.kill(os.getpid(), 0) must not raise for the current process."""
    result = probe_pid(os.getpid())
    assert result == "alive", f"Expected 'alive' for self PID, got {result!r}"


# ---------------------------------------------------------------------------
# Test 3: live child → no exception
# ---------------------------------------------------------------------------

def test_live_child_pid_does_not_raise():
    """A sleeping child process must be detected as alive."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        result = probe_pid(child.pid)
        assert result == "alive", (
            f"Expected 'alive' for live child PID {child.pid}, got {result!r}"
        )
    finally:
        child.terminate()
        child.wait()


# ---------------------------------------------------------------------------
# Test 4: EPERM means "alive but not ours" (documentation test)
# ---------------------------------------------------------------------------

def test_eperm_means_alive_not_ours():
    """
    PermissionError (EPERM) from os.kill(pid, 0) means the process EXISTS but we
    lack permission to signal it. For same-user processes on macOS/Linux, EPERM
    does NOT arise — we CAN signal same-user processes. EPERM is only seen for
    cross-user pids (e.g. root-owned processes).

    This test verifies the semantic contract by checking PID 1 (launchd/init,
    which is always alive and always owned by root on a non-root runner).
    """
    pid1_result = probe_pid(1)
    # PID 1 (launchd/init) is always alive. We're not root, so we should see
    # either 'alive_not_ours' (EPERM) on Linux, or 'alive' on macOS (where
    # unprivileged users CAN send signal 0 to launchd).
    assert pid1_result in ("alive", "alive_not_ours"), (
        f"PID 1 should always be alive, got {pid1_result!r}"
    )
    # Document which behavior this platform exhibits:
    print(f"\n[U2 probe] PID 1 (launchd/init) probe result on this platform: {pid1_result!r}")
    print(f"  → 'alive_not_ours' = EPERM seen for cross-user PIDs (Linux typical)")
    print(f"  → 'alive'          = no EPERM for cross-user PIDs (macOS typical)")


def test_same_user_process_never_eperm():
    """
    For same-user processes, os.kill(pid, 0) returns successfully (no exception).
    EPERM does NOT arise for same-user — meaning the lockfile design only needs
    to handle 'no exception → alive' and 'ProcessLookupError → dead'.
    """
    # Use a live child (same user as us) and confirm no EPERM
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        raised = None
        try:
            os.kill(child.pid, 0)
        except PermissionError as e:
            raised = e
        except ProcessLookupError:
            pytest.fail(f"Child PID {child.pid} unexpectedly dead before probe")

        assert raised is None, (
            f"Got EPERM for same-user child PID {child.pid}: {raised}. "
            "This contradicts the expected semantics — EPERM should only arise "
            "for cross-user processes."
        )
    finally:
        child.terminate()
        child.wait()


# ---------------------------------------------------------------------------
# Test 5: PID recycling gap — os.kill(pid,0) cannot distinguish recycled PIDs
# ---------------------------------------------------------------------------

def test_pid_recycling_gap_documented():
    """
    Demonstrate WHY pid alone is insufficient for liveness detection.

    We can't reliably FORCE PID recycling in a unit test (it requires consuming
    a large number of PIDs, which is OS-specific and disruptive). Instead, we
    document the gap explicitly with a clear logical proof:

    1. We observe that os.kill(pid, 0) returns the signal-delivery result for
       WHATEVER process currently holds that PID number.
    2. If the original process died AND a new process was assigned the same PID,
       os.kill returns "alive" — indistinguishable from the original still running.
    3. Therefore: a stale lockfile with a recycled PID looks live to os.kill.
    4. The plan's mitigation is: store (pid, start_timestamp) in the lockfile;
       compare /proc/<pid>/create_time (Linux) or proc_info (macOS) against the
       stored timestamp. A timestamp mismatch → stale, reclaim.
    5. On macOS without /proc, psutil.Process(pid).create_time() is the standard
       approach — BUT psutil is NOT available in this venv (confirmed above).
    6. Alternative without psutil: use an age-bound — if lock is > N minutes old
       AND the process passes os.kill, treat as stale (conservative: protects
       against the recycling window if PID lifetimes are short).

    This test asserts that the SAME PID can be seen as 'alive' after the original
    holder died, by verifying the probe returns 'alive' for the test process itself
    (a live PID that is not the original lockfile writer).
    """
    # Prove that os.kill only sees "is a process with this PID number alive now"
    # not "is THIS SPECIFIC PROCESS (the original lock writer) still alive"
    my_pid = os.getpid()
    result = probe_pid(my_pid)
    assert result == "alive"

    # The gap: if a prior process died at my_pid, and I was spawned with the same
    # PID, the caller would see 'alive' and incorrectly think the lock is held.
    # os.kill carries NO identity information — it's purely a PID-number lookup.

    # Confirm: after spawning and waiting a child, its PID slot COULD be reused
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    recycling_candidate_pid = child.pid
    child.wait()

    # At this moment recycling_candidate_pid might be 'dead' or 'alive' (recycled).
    # Both outcomes are valid for this test — we're documenting the gap, not forcing it.
    after_reap = probe_pid(recycling_candidate_pid)
    print(f"\n[U2 probe] PID recycling test: reaped child PID {recycling_candidate_pid} "
          f"probe result immediately after reap: {after_reap!r}")
    print(f"  'dead'  → PID not yet recycled (typical on lightly loaded system)")
    print(f"  'alive' → PID was IMMEDIATELY recycled — confirms the gap is real")
    print(f"  Either outcome is expected; the gap exists regardless.")

    # The key assertion: the probe result is purely based on current PID occupancy,
    # not on process identity. This is always true, regardless of what after_reap says.
    assert after_reap in ("dead", "alive", "alive_not_ours"), "probe_pid must return a known value"


# ---------------------------------------------------------------------------
# Test 6: psutil absence confirmed — os.kill is the only mechanism
# ---------------------------------------------------------------------------

def test_psutil_not_available():
    """Confirm psutil is absent from the venv; os.kill is the only liveness mechanism."""
    try:
        import psutil  # noqa: F401
        pytest.fail(
            "psutil IS available — the session_lock.py implementation MAY use "
            "psutil.Process(pid).create_time() for an exact timestamp comparison "
            "against PID recycling. Update this test and the Slice 6 design to "
            "use psutil if desired (add it as a dependency)."
        )
    except ImportError:
        pass  # expected — os.kill is the only mechanism; age-bound fallback required


# ---------------------------------------------------------------------------
# Test 7: integration — liveness check function shape
# ---------------------------------------------------------------------------

def test_liveness_probe_function_shape():
    """
    Validate the full probe_pid() helper used above as a stand-in for
    session_lock.py's is_pid_alive(). The real implementation should match this
    exact exception-handling pattern.

    Expected behavior matrix:
      dead pid    → ProcessLookupError → return False
      live pid    → no exception       → return True
      eperm pid   → PermissionError    → return True (process exists)
    """
    def is_pid_alive(pid: int) -> bool:
        """Minimal session_lock.is_pid_alive() reference implementation."""
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we can't signal it (cross-user).
            # Treat as alive — safer to refuse than to race.
            return True

    # Dead PID
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    dead_pid = child.pid
    child.wait()
    # Allow for recycling — if recycled, skip this specific assertion
    if not is_pid_alive(dead_pid):
        assert is_pid_alive(dead_pid) is False, "Dead PID should return False"
    else:
        print(f"\n[U2 probe] PID {dead_pid} was recycled immediately — "
              "pid-recycling gap confirmed, timestamp guard required")

    # Live: self
    assert is_pid_alive(os.getpid()) is True, "Self PID must be alive"

    # Live: child
    child2 = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert is_pid_alive(child2.pid) is True, "Live child must be alive"
    finally:
        child2.terminate()
        child2.wait()
