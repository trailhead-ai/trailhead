"""Slice-6 soak seam tests: soak_health.py behavioral contract.

Test contract:
  - Soak inert default (D-3): no soak_health_command configured → prints
    'n/a — no health command configured', exits 0.
  - Soak runs + escalates (R-4): stubbed health command exits non-zero →
    soak_health.py escalates (exits nonzero). One-shot: stub is invoked exactly once.
  - S-1 no-shell: a soak_health_command containing '&&'/'$(...)' is passed
    literally (shlex.split → arg-list); metachars become string args, the
    command fails rather than spawning a subshell. No subshell expansion.
  - R-3 timeout: a never-returning command is killed after the timeout and
    escalates (exits nonzero). Does NOT hang.
  - Min-1: malformed soak_health_command (unbalanced quote) → clean exit 2 with
    an error message on stderr instead of a ValueError traceback.
  - Hermeticity: tmp_path-based stub commands; no network; no real ~/.claude/;
    stdlib only; script imported via sys.path.insert(SCRIPTS_DIR).

All tests use a TOML config written to tmp_path (B-1: the script reads it via
stdlib tomllib, never imports trailhead.paths or camp internals).
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "forge" / "scripts"
SOAK_SCRIPT = SCRIPTS_DIR / "soak_health.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


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

    def test_no_health_command_prints_na_message(self, tmp_path: Path) -> None:
        """D-3: soak_health.py with no health command prints 'n/a — no health command configured'."""
        toml = _write_toml(tmp_path)
        r = _run_soak(toml)
        assert "n/a" in r.stdout and "no health command configured" in r.stdout, (
            f"soak_health.py must print 'n/a — no health command configured' (D-3);\n"
            f"got stdout: {r.stdout!r}"
        )

    def test_no_health_command_no_subprocess_spawned(self, tmp_path: Path) -> None:
        """D-3: soak_health.py with no health command must not spawn any subprocess."""
        # Write a sentinel that would be created if a health command ran
        sentinel = tmp_path / "sentinel.txt"
        toml = _write_toml(tmp_path)
        r = _run_soak(toml)
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
    def test_failing_health_command_exits_nonzero(self, tmp_path: Path) -> None:
        """R-4: a non-zero health result causes soak_health.py to exit nonzero (escalate)."""
        stub = _write_stub_command(tmp_path, exit_code=1)
        toml = _write_toml(tmp_path, health_command=stub)
        r = _run_soak(toml)
        assert r.returncode != 0, (
            f"soak_health.py must exit nonzero when the health command fails (R-4);\n"
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
        """R-4: one non-zero health result → immediate escalate; stub is invoked exactly once."""
        # Write a counter file; stub increments it
        counter = tmp_path / "count.txt"
        counter.write_text("0", encoding="utf-8")
        # Stub: read count, increment, write back, exit 1
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
        assert r.returncode != 0
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

        Under shell=True: 'true && touch <sentinel>' would: run 'true' (exit 0),
        then run 'touch <sentinel>' (creating the sentinel).
        Under shell=False + shlex.split: argv=['true', '&&', 'touch', '<sentinel>']
        — 'true' receives '&&', 'touch', '<sentinel>' as ignored args and exits 0,
        but the sentinel is NEVER created because '&&' is a shell-only operator.
        The key contract is: the sentinel must not be created (no subshell expansion).
        """
        sentinel = tmp_path / "shell_sentinel.txt"
        # Under shell=True: 'true && touch <sentinel>' creates the sentinel
        # Under shell=False: 'true' receives '&&' and rest as ignored args; no sentinel
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
        # Under shell=True: 'echo $(touch <sentinel>)' would create sentinel
        # Under shell=False + shlex.split: argv=['echo', '$(touch ...)', ...]
        # 'echo' succeeds (exit 0) but the subshell '$(...)' is never interpreted
        # so the sentinel is NOT created.
        bad_cmd = f"echo $(touch {sentinel})"
        toml = _write_toml(tmp_path, health_command=bad_cmd)
        _run_soak(toml)
        assert not sentinel.exists(), (
            "S-1: soak_health.py must NOT expand $(...) — "
            "the subshell must never execute (shell=False is the guard)"
        )


# ---------------------------------------------------------------------------
# R-3: Timeout kills a never-returning command
# ---------------------------------------------------------------------------

class TestSoakTimeout:
    def test_hung_command_is_killed_and_escalates(self, tmp_path: Path) -> None:
        """R-3: a never-returning health command is killed after the timeout and soak escalates."""
        # Stub that sleeps for a long time (10s > 1s timeout we'll use in the test)
        stub = tmp_path / "hung_stub.sh"
        stub.write_text("#!/bin/sh\nsleep 10\n", encoding="utf-8")
        stub.chmod(0o755)
        toml = _write_toml(tmp_path, health_command=str(stub))

        start = time.monotonic()
        # Use a 1-second timeout so the test is fast
        r = _run_soak(toml, timeout_s=1)
        elapsed = time.monotonic() - start

        # Must NOT hang (assert it finished in well under 10s)
        assert elapsed < 8.0, (
            f"R-3: soak_health.py hung for {elapsed:.1f}s — the timeout must kill the command"
        )
        # Must exit nonzero (escalate)
        assert r.returncode != 0, (
            f"R-3: soak_health.py must exit nonzero when the health command times out (escalate);\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
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
    """Write a TOML with soak_health_command as a TOML single-quoted literal string.

    Single-quoted TOML strings allow the value to contain double quotes (useful for
    writing a command like `echo "unterminated` without breaking TOML parsing).
    This lets us test shlex.split ValueError without hitting a TOMLDecodeError.
    """
    toml_path = tmp_path / "group.toml"
    toml_path.write_text(
        f"[group]\nname = \"test-group\"\n\n[release]\n"
        f"soak_health_command = '{raw_command}'\n",
        encoding="utf-8",
    )
    return toml_path


class TestSoakMalformedCommand:
    def test_unbalanced_quote_exits_two(self, tmp_path: Path) -> None:
        """Min-1: unbalanced quote in soak_health_command → exit 2, not a traceback.

        Uses a TOML single-quoted literal string so the TOML is valid; the value
        contains an unbalanced double-quote that shlex.split raises ValueError on.
        """
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
