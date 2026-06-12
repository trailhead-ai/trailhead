"""Soak seam tests: landing's ported soak_health.py behavioral contract.

Ported from craft's test_soak_seam.py, retargeted to landing's own copy of the
probe (landing's soak is its OWN concern — it is NOT behind the provider
abstraction). The probe is a verbatim port; these tests lock the load-bearing
contract so a future edit can't regress it.

Test contract (Slice 4):
  - Soak inert default (D-3): no soak_health_command configured → prints the
    EXACT string 'soak: n/a — no health command configured', exits 0, spawns NO
    subprocess.
  - Soak runs + escalates (R-4): stubbed health command exits non-zero →
    soak escalates (exit 1). One-shot: stub is invoked exactly once.
  - Healthy: a command that exits 0 → soak exits 0.
  - S-1 no-shell: a soak_health_command containing '&&'/'$(...)' is passed
    literally (shlex.split → arg-list); metachars become string args, no subshell.
  - R-3 timeout: a never-returning command is killed after the timeout (via
    os.killpg over the whole process group, so a grandchild does not leak) and
    escalates (exit 1) without hanging.
  - Min-1: malformed soak_health_command (unbalanced quote) → clean exit 2 with
    an error message on stderr instead of a ValueError traceback.

Hermeticity: tmp_path-based stub commands; no network; stdlib only.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "plugins" / "landing" / "scripts"
SOAK_SCRIPT = SCRIPTS_DIR / "soak_health.py"

# The exact inert message — locked so runbooks referencing it don't silently drift.
INERT_MESSAGE = "soak: n/a — no health command configured"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_toml(tmp_path: Path, *, health_command: str | None = None) -> Path:
    """Write a minimal group TOML with the [release] block.

    If health_command is None, omits the soak_health_command key entirely
    (testing the inert-by-default path).
    """
    lines = ["[group]\nname = \"test-group\"\n\n[release]\n"]
    if health_command is not None:
        lines.append(f'soak_health_command = "{health_command}"\n')
    toml_path = tmp_path / "group.toml"
    toml_path.write_text("".join(lines), encoding="utf-8")
    return toml_path


def _write_stub_command(tmp_path: Path, *, exit_code: int = 0, body: str = "") -> str:
    """Write a shell stub script that exits with exit_code. Returns the command string."""
    stub = tmp_path / "stub.sh"
    stub.write_text(f"#!/bin/sh\n{body}\nexit {exit_code}\n", encoding="utf-8")
    stub.chmod(0o755)
    return str(stub)


def _run_soak(toml_path: Path, *, timeout_s: int = 30) -> subprocess.CompletedProcess:
    """Invoke soak_health.py with the given TOML and an explicit timeout."""
    return subprocess.run(
        [sys.executable, str(SOAK_SCRIPT), "--toml", str(toml_path),
         "--timeout", str(timeout_s)],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# D-3: Soak inert default
# ---------------------------------------------------------------------------

class TestSoakInertDefault:
    def test_no_health_command_exits_zero(self, tmp_path: Path) -> None:
        """D-3: soak_health.py with no soak_health_command exits 0 (inert by default)."""
        toml = _write_toml(tmp_path)
        r = _run_soak(toml)
        assert r.returncode == 0, (
            f"soak_health.py must exit 0 when no health command is configured (D-3 inert default);\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_no_health_command_prints_exact_na_message(self, tmp_path: Path) -> None:
        """D-3 (council Minor): the inert message is the EXACT locked string."""
        toml = _write_toml(tmp_path)
        r = _run_soak(toml)
        assert INERT_MESSAGE in r.stdout, (
            f"soak_health.py must print the exact inert message {INERT_MESSAGE!r} (D-3);\n"
            f"got stdout: {r.stdout!r}"
        )

    def test_no_health_command_no_subprocess_spawned(self, tmp_path: Path) -> None:
        """D-3: soak_health.py with no health command must not spawn any subprocess."""
        sentinel = tmp_path / "sentinel.txt"
        toml = _write_toml(tmp_path)
        _run_soak(toml)
        assert not sentinel.exists(), "soak_health.py spawned a subprocess in inert mode"

    def test_missing_release_block_also_exits_zero(self, tmp_path: Path) -> None:
        """D-3: a TOML with no [release] block at all still exits 0."""
        toml = tmp_path / "group.toml"
        toml.write_text("[group]\nname = \"x\"\n", encoding="utf-8")
        r = _run_soak(toml)
        assert r.returncode == 0, (
            f"soak_health.py must exit 0 when [release] block is absent (D-3);\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


# ---------------------------------------------------------------------------
# R-4: One-shot escalate on non-zero health result
# ---------------------------------------------------------------------------

class TestSoakRunsAndEscalates:
    def test_failing_health_command_exits_one(self, tmp_path: Path) -> None:
        """R-4: a non-zero health result causes soak_health.py to exit 1 (escalate)."""
        stub = _write_stub_command(tmp_path, exit_code=1)
        toml = _write_toml(tmp_path, health_command=stub)
        r = _run_soak(toml)
        assert r.returncode == 1, (
            f"soak_health.py must exit 1 when the health command fails (R-4 regression);\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_passing_health_command_exits_zero(self, tmp_path: Path) -> None:
        """A health command that exits 0 → soak_health.py exits 0 (healthy)."""
        stub = _write_stub_command(tmp_path, exit_code=0)
        toml = _write_toml(tmp_path, health_command=stub)
        r = _run_soak(toml)
        assert r.returncode == 0, (
            f"soak_health.py must exit 0 when the health command succeeds;\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_one_shot_no_retry(self, tmp_path: Path) -> None:
        """R-4: one non-zero health result → immediate escalate; stub invoked exactly once."""
        counter = tmp_path / "count.txt"
        counter.write_text("0", encoding="utf-8")
        stub = tmp_path / "count_stub.sh"
        stub.write_text(
            f"#!/bin/sh\n"
            f"c=$(cat '{counter}')\n"
            f"echo $((c + 1)) > '{counter}'\n"
            f"exit 1\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        toml = _write_toml(tmp_path, health_command=str(stub))
        r = _run_soak(toml)
        assert r.returncode == 1
        count = int(counter.read_text().strip())
        assert count == 1, (
            f"R-4: soak_health.py must invoke the health command exactly once on failure "
            f"(no retry), but it was invoked {count} times"
        )


# ---------------------------------------------------------------------------
# S-1: No-shell execution — metachars don't spawn subshells
# ---------------------------------------------------------------------------

class TestSoakNoShell:
    def test_metachar_ampersand_does_not_expand(self, tmp_path: Path) -> None:
        """S-1: '&&' in soak_health_command is NOT shell-expanded.

        Under shell=False + shlex.split: argv=['true', '&&', 'touch', '<sentinel>']
        — 'true' receives '&&', 'touch', '<sentinel>' as ignored args, but the
        sentinel is NEVER created because '&&' is a shell-only operator.
        """
        sentinel = tmp_path / "shell_sentinel.txt"
        bad_cmd = f"true && touch {sentinel}"
        toml = _write_toml(tmp_path, health_command=bad_cmd)
        _run_soak(toml)
        assert not sentinel.exists(), (
            "S-1: soak_health.py must NOT create the sentinel file — "
            "'&&' must be passed literally, not shell-expanded (shell=False is the guard)"
        )

    def test_dollar_paren_does_not_expand(self, tmp_path: Path) -> None:
        """S-1: '$()' in soak_health_command is NOT expanded as a subshell."""
        sentinel = tmp_path / "subshell_sentinel.txt"
        bad_cmd = f"echo $(touch {sentinel})"
        toml = _write_toml(tmp_path, health_command=bad_cmd)
        _run_soak(toml)
        assert not sentinel.exists(), (
            "S-1: soak_health.py must NOT expand $(...) — "
            "the subshell must never execute (shell=False is the guard)"
        )


# ---------------------------------------------------------------------------
# R-3: Timeout kills a never-returning command via os.killpg (whole group)
# ---------------------------------------------------------------------------

class TestSoakTimeout:
    def test_hung_command_is_killed_and_escalates(self, tmp_path: Path) -> None:
        """R-3: a never-returning health command is killed after the timeout and escalates."""
        stub = tmp_path / "hung_stub.sh"
        stub.write_text("#!/bin/sh\nsleep 10\n", encoding="utf-8")
        stub.chmod(0o755)
        toml = _write_toml(tmp_path, health_command=str(stub))

        start = time.monotonic()
        r = _run_soak(toml, timeout_s=1)
        elapsed = time.monotonic() - start

        assert elapsed < 8.0, (
            f"R-3: soak_health.py hung for {elapsed:.1f}s — the timeout must kill the command"
        )
        assert r.returncode == 1, (
            f"R-3: soak_health.py must exit 1 when the health command times out (escalate);\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_timeout_kills_whole_process_group_no_grandchild_leak(self, tmp_path: Path) -> None:
        """R-3 (council Important): os.killpg reaps the WHOLE process group.

        The stub spawns a grandchild ``sleep`` (behind a shell wrapper) that writes
        a sentinel only if it survives to completion. subprocess.run(timeout=) would
        SIGKILL only the direct child, leaving the grandchild alive to write the
        sentinel after the timeout. os.killpg(getpgid, SIGKILL) over the new session
        kills the grandchild too — so the sentinel must NEVER appear.
        """
        sentinel = tmp_path / "grandchild_sentinel.txt"
        # The wrapper backgrounds a grandchild that sleeps then writes the sentinel,
        # then the wrapper itself sleeps so the soak times out while both live.
        stub = tmp_path / "leak_stub.sh"
        stub.write_text(
            "#!/bin/sh\n"
            f"( sleep 3 && touch '{sentinel}' ) &\n"
            "sleep 10\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        toml = _write_toml(tmp_path, health_command=str(stub))

        r = _run_soak(toml, timeout_s=1)
        assert r.returncode == 1, (
            f"R-3: timed-out soak must escalate (exit 1); stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        # Wait past the grandchild's 3s sleep: if killpg failed to reap it, the
        # sentinel would appear here.
        time.sleep(4)
        assert not sentinel.exists(), (
            "R-3: a grandchild survived the timeout kill — os.killpg must terminate "
            "the entire process group (start_new_session=True + killpg), not just the "
            "direct child. A leaked grandchild is the hang/orphan bug the seam prevents."
        )

    def test_fast_command_not_killed(self, tmp_path: Path) -> None:
        """R-3: a fast-exiting health command is not killed by the timeout."""
        stub = _write_stub_command(tmp_path, exit_code=0)
        toml = _write_toml(tmp_path, health_command=stub)
        r = _run_soak(toml, timeout_s=5)
        assert r.returncode == 0, (
            f"R-3: a fast-exiting health command must not be killed by the timeout;\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


# ---------------------------------------------------------------------------
# Min-1: malformed soak_health_command → clean exit-2, not ValueError traceback
# ---------------------------------------------------------------------------

def _write_toml_raw_command(tmp_path: Path, raw_command: str) -> Path:
    """Write a TOML with soak_health_command as a TOML single-quoted literal string."""
    toml_path = tmp_path / "group.toml"
    toml_path.write_text(
        f"[group]\nname = \"test-group\"\n\n[release]\n"
        f"soak_health_command = '{raw_command}'\n",
        encoding="utf-8",
    )
    return toml_path


class TestSoakMalformedCommand:
    def test_unbalanced_quote_exits_two(self, tmp_path: Path) -> None:
        """Min-1: unbalanced quote in soak_health_command → exit 2, not a traceback."""
        toml = _write_toml_raw_command(tmp_path, 'echo "unterminated')
        r = _run_soak(toml)
        assert r.returncode == 2, (
            f"Min-1: malformed soak_health_command must exit 2 (named error), not "
            f"{r.returncode} (got stdout: {r.stdout!r}, stderr: {r.stderr!r})"
        )

    def test_unbalanced_quote_prints_error_to_stderr(self, tmp_path: Path) -> None:
        """Min-1: malformed soak_health_command must print a human-readable error to stderr."""
        toml = _write_toml_raw_command(tmp_path, 'echo "unterminated')
        r = _run_soak(toml)
        assert "malformed" in r.stderr.lower() or "error" in r.stderr.lower(), (
            f"Min-1: soak_health.py must print a descriptive error to stderr for a "
            f"malformed soak_health_command; got stderr: {r.stderr!r}"
        )

    def test_unbalanced_quote_no_traceback(self, tmp_path: Path) -> None:
        """Min-1: a malformed command must not produce a Python traceback."""
        toml = _write_toml_raw_command(tmp_path, 'echo "unterminated')
        r = _run_soak(toml)
        assert "Traceback" not in r.stderr, (
            f"Min-1: soak_health.py must not emit a ValueError traceback for a "
            f"malformed soak_health_command; got stderr: {r.stderr!r}"
        )
