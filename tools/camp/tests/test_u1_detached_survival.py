"""U1 assumption probe: detached child survives parent os.execvp.

Tests that a child spawned with:
    subprocess.Popen(start_new_session=True, stdin=DEVNULL, stdout=logfile, stderr=logfile)
continues running to completion AFTER the parent process replaces itself via os.execvp.

This is the blocking gate on Slice 3 (async provisioning).  The entire "launch fast,
provision in background" architecture depends on this OS behaviour holding on macOS
with the repo venv Python.

Cleanup: remove this file after Slice 3 lands its real fork-then-exec integration test.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

# The repo venv Python — must match what camp actually runs under.
_VENV_PYTHON = "/Users/tduffield/code/trailhead/.venv/bin/python"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_parent_script(scratch: Path) -> Path:
    """Write parent.py into scratch dir and return its path.

    parent.py:
      1. Spawns child.py via Popen(start_new_session=True, stdin=DEVNULL,
         stdout/stderr → child.log).
      2. Immediately replaces itself with `python -c "pass"` via os.execvp
         so the original parent process image is gone before the child finishes.
    """
    sentinel = scratch / "sentinel.txt"
    logfile = scratch / "child.log"
    child_script = scratch / "child.py"
    parent_script = scratch / "parent.py"

    # Child: sleep, then write sentinel + log message.
    child_script.write_text(
        textwrap.dedent(f"""\
        import time, os
        time.sleep(0.8)
        sentinel = {str(sentinel)!r}
        with open(sentinel, "w") as f:
            f.write(f"child_pid={{os.getpid()}} done\\n")
        print("child completed", flush=True)
        """),
        encoding="utf-8",
    )

    # Parent: spawn child detached, then exec over itself immediately.
    parent_script.write_text(
        textwrap.dedent(f"""\
        import subprocess, sys, os
        from pathlib import Path

        child_script = {str(child_script)!r}
        logfile_path = {str(logfile)!r}

        with open(logfile_path, "w") as lf:
            proc = subprocess.Popen(
                [sys.executable, child_script],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=lf,
                stderr=lf,
            )
        # Parent replaces itself — original process image is gone.
        # The child must survive this.
        os.execvp(sys.executable, [sys.executable, "-c", "pass"])
        """),
        encoding="utf-8",
    )

    return parent_script


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDetachedChildSurvivesParentExec:
    """U1 survival probe: fork-then-exec."""

    def test_sentinel_appears_after_parent_exec(self, tmp_path: Path) -> None:
        """Child must write sentinel AFTER the parent os.execvp'd over itself."""
        scratch = tmp_path / "u1_probe"
        scratch.mkdir()

        parent_script = _write_parent_script(scratch)
        sentinel = scratch / "sentinel.txt"
        logfile = scratch / "child.log"

        # Launch the parent and wait for it to exit (exec'd over itself → exit 0).
        result = subprocess.run(
            [_VENV_PYTHON, str(parent_script)],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"Parent exited non-zero: {result.returncode}\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )

        # At this point the parent's process image is gone (exec'd to `python -c pass`).
        # Poll for the sentinel — child sleeps 0.8 s so we give it 5 s margin.
        deadline = time.monotonic() + 5.0
        while not sentinel.exists():
            if time.monotonic() > deadline:
                break
            time.sleep(0.05)

        assert sentinel.exists(), (
            "INVALIDATED: sentinel never appeared — detached child did not survive "
            "the parent os.execvp.  Slice 3's async-provisioning architecture cannot land."
        )

        sentinel_text = sentinel.read_text(encoding="utf-8")
        assert "done" in sentinel_text, f"Unexpected sentinel content: {sentinel_text!r}"

    def test_logfile_captured_child_output(self, tmp_path: Path) -> None:
        """Child's stdout must land in the logfile, not get lost after parent exec."""
        scratch = tmp_path / "u1_log_probe"
        scratch.mkdir()

        parent_script = _write_parent_script(scratch)
        sentinel = scratch / "sentinel.txt"
        logfile = scratch / "child.log"

        subprocess.run(
            [_VENV_PYTHON, str(parent_script)],
            capture_output=True,
            timeout=10,
        )

        # Wait for child to finish.
        deadline = time.monotonic() + 5.0
        while not sentinel.exists():
            if time.monotonic() > deadline:
                break
            time.sleep(0.05)

        assert logfile.exists(), "logfile was never created"
        log_text = logfile.read_text(encoding="utf-8")
        assert "child completed" in log_text, (
            f"Child output not captured in logfile.  log contents: {log_text!r}"
        )

    def test_child_runs_in_its_own_session(self, tmp_path: Path) -> None:
        """start_new_session=True must place the child in a different session/pgid.

        We can't observe the child's pgid after the parent has exec'd, so we prove
        this indirectly: write a parent that captures the child PID before exec'ing,
        then after the child's sentinel appears, compare its pgid against the parent's
        pgid (which by definition can't be the child's own pgid if start_new_session
        worked — the child is its own process group leader).
        """
        scratch = tmp_path / "u1_session_probe"
        scratch.mkdir()

        sentinel = scratch / "sentinel.txt"
        pid_file = scratch / "child_pid.txt"
        logfile = scratch / "session_child.log"
        child_script = scratch / "session_child.py"
        parent_script = scratch / "session_parent.py"

        # Child writes its own pid, pgid, and sid to the sentinel.
        child_script.write_text(
            textwrap.dedent(f"""\
            import time, os
            time.sleep(0.8)
            pid  = os.getpid()
            pgid = os.getpgid(0)
            sid  = os.getsid(0)
            with open({str(sentinel)!r}, "w") as f:
                f.write(f"pid={{pid}} pgid={{pgid}} sid={{sid}}\\n")
            print(f"child pid={{pid}} pgid={{pgid}} sid={{sid}}", flush=True)
            """),
            encoding="utf-8",
        )

        # Parent records child PID before exec'ing.
        parent_script.write_text(
            textwrap.dedent(f"""\
            import subprocess, sys, os

            with open({str(logfile)!r}, "w") as lf:
                proc = subprocess.Popen(
                    [sys.executable, {str(child_script)!r}],
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=lf,
                    stderr=lf,
                )
            # Record the child PID so the test can inspect it.
            with open({str(pid_file)!r}, "w") as pf:
                pf.write(str(proc.pid))

            os.execvp(sys.executable, [sys.executable, "-c", "pass"])
            """),
            encoding="utf-8",
        )

        subprocess.run(
            [_VENV_PYTHON, str(parent_script)],
            capture_output=True,
            timeout=10,
        )

        # Wait for child sentinel.
        deadline = time.monotonic() + 5.0
        while not sentinel.exists():
            if time.monotonic() > deadline:
                break
            time.sleep(0.05)

        assert sentinel.exists(), "Sentinel never appeared (child did not run)"
        assert pid_file.exists(), "Parent never recorded the child PID"

        sentinel_text = sentinel.read_text(encoding="utf-8")
        # Parse pgid and sid from the sentinel.
        parts = {}
        for token in sentinel_text.strip().split():
            k, _, v = token.partition("=")
            parts[k] = int(v)

        child_pid  = parts["pid"]
        child_pgid = parts["pgid"]
        child_sid  = parts["sid"]

        # With start_new_session=True the child is its own process group leader:
        # pgid == pid.
        assert child_pgid == child_pid, (
            f"Expected child pgid == child pid (own group leader) but got "
            f"pgid={child_pgid}, pid={child_pid}.  start_new_session may not have worked."
        )
        # And the child's session id equals its own pid (new session leader).
        assert child_sid == child_pid, (
            f"Expected child sid == child pid (new session leader) but got "
            f"sid={child_sid}, pid={child_pid}."
        )
