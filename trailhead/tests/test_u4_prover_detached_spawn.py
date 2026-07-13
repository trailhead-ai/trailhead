"""
ASSUMPTION PROBE — U4 (detached spawn + pidfile semantics), blocking S8.

Not part of the permanent suite. This file proves/disproves the OS-level
assumption S8's lifecycle verbs (`trailhead outpost start|stop|status`) will
rest on, before that code gets written. The executor deletes this file once
S8 lands its own behavioral tests (it will reimplement the same coverage
against the real fake-daemon test contract, not against this throwaway
child/parent script pair).

Claim under test:
  1. subprocess.Popen(..., start_new_session=True) produces a child that
     SURVIVES the spawning ("parent") process exiting — no SIGHUP, no kill,
     keeps running and doing work — observed from a THIRD, unrelated process.
  2. os.kill(pid, signal.SIGTERM) gives that child a chance to shut down
     cleanly (a signal handler runs) and it exits without needing SIGKILL.
  3. The pid captured via Popen.pid keeps identifying the same process for
     the duration of the test (no reuse/aliasing risk in this timeframe),
     and liveness (os.kill(pid, 0)) reliably flips from "alive" to
     "ESRCH/dead" — the primitive S8's stale-pidfile detection depends on.

No real config/state dirs are touched (Axiom 6) — everything lives under
pytest's tmp_path.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

CHILD_SCRIPT = """\
import os
import signal
import sys
import time

heartbeat_path = sys.argv[1]
shutdown_path = sys.argv[2]


def handle_sigterm(signum, frame):
    with open(shutdown_path, "w") as sf:
        sf.write("clean-shutdown pid=%d\\n" % os.getpid())
    sys.exit(0)


signal.signal(signal.SIGTERM, handle_sigterm)

with open(heartbeat_path, "a") as f:
    while True:
        f.write("%f %d\\n" % (time.time(), os.getpid()))
        f.flush()
        time.sleep(0.2)
"""

PARENT_SCRIPT = """\
import subprocess
import sys

python = sys.argv[1]
child_script = sys.argv[2]
heartbeat_path = sys.argv[3]
shutdown_path = sys.argv[4]
pidfile_path = sys.argv[5]
log_path = sys.argv[6]

with open(log_path, "wb") as log:
    proc = subprocess.Popen(
        [python, child_script, heartbeat_path, shutdown_path],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )

with open(pidfile_path, "w") as pf:
    pf.write(str(proc.pid))

# This process (simulating the `trailhead outpost start` CLI invocation)
# exits here, immediately after spawning — the child must survive this.
"""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by someone else — not our case here, but alive.
        return True
    return True


def _wait_until(predicate, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_u4_detached_child_survives_parent_exit_and_sigterm_is_clean(tmp_path: Path):
    assert sys.platform == "darwin", "prover expected to run on macOS for this dev box"

    child_script = tmp_path / "child.py"
    parent_script = tmp_path / "parent.py"
    heartbeat_path = tmp_path / "heartbeat.log"
    shutdown_path = tmp_path / "shutdown.marker"
    pidfile_path = tmp_path / "child.pid"
    log_path = tmp_path / "child.stdout.log"

    child_script.write_text(CHILD_SCRIPT)
    parent_script.write_text(PARENT_SCRIPT)

    # --- Step 1: run the "parent" to completion. By the time subprocess.run()
    # returns, that process has fully exited — this is the literal moment
    # `trailhead outpost start` would return control to the shell.
    result = subprocess.run(
        [
            sys.executable,
            str(parent_script),
            sys.executable,
            str(child_script),
            str(heartbeat_path),
            str(shutdown_path),
            str(pidfile_path),
            str(log_path),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"parent script failed: {result.stderr}"
    assert pidfile_path.exists(), "parent did not write a pidfile before exiting"

    child_pid = int(pidfile_path.read_text().strip())

    # --- Step 2 (claim 1): from THIS process — a third, unrelated process
    # relative to the exited "parent" — confirm the child is alive and still
    # doing work (heartbeats advancing) well after the parent is gone.
    assert _wait_until(lambda: heartbeat_path.exists(), timeout=2.0), (
        "child never started writing heartbeats"
    )
    assert _pid_alive(child_pid), "child pid is not alive shortly after parent exit"

    first_snapshot = heartbeat_path.read_text()
    first_line_count = len(first_snapshot.splitlines())
    time.sleep(1.0)
    second_snapshot = heartbeat_path.read_text()
    second_line_count = len(second_snapshot.splitlines())

    assert second_line_count > first_line_count, (
        "heartbeat file did not grow — child is not still running/orphan-surviving"
    )
    assert _pid_alive(child_pid), "child pid died unexpectedly while parent-less"

    # --- Step 3 (claim 3, part A): the pid captured via Popen.pid still
    # identifies the SAME process — every heartbeat line the child wrote
    # reports its own os.getpid(), which must match what the parent recorded.
    reported_pids = {line.split()[1] for line in second_snapshot.splitlines()}
    assert reported_pids == {str(child_pid)}, (
        f"heartbeat pid mismatch: pidfile said {child_pid}, child reported {reported_pids}"
    )

    # --- Step 4 (claim 2): SIGTERM gives the child a chance to exit cleanly.
    os.kill(child_pid, signal.SIGTERM)

    exited = _wait_until(lambda: not _pid_alive(child_pid), timeout=3.0)
    assert exited, "child did not exit within 3s of SIGTERM — would require SIGKILL fallback"

    # Reap it so it doesn't linger as a zombie under this process tree, and
    # to make the alive-check below unambiguous (no PID owned by pytest itself).
    try:
        os.waitpid(child_pid, os.WNOHANG)
    except ChildProcessError:
        pass  # not our direct child (it's in a new session) — nothing to reap

    assert shutdown_path.exists(), "SIGTERM handler never ran — child did not get a clean shot"
    assert f"pid={child_pid}" in shutdown_path.read_text()

    # --- Step 5 (claim 3, part B): liveness check now reliably reports dead
    # via ESRCH — this is exactly the primitive S8's stale-pidfile detection
    # (dead pid in pidfile -> os.kill(pid, 0) raises ProcessLookupError) relies on.
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
