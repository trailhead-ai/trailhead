#!/usr/bin/env python3
"""Thin, configurable soak health probe.

Reads `soak_health_command` from the `[release]` block of the group TOML
(path passed as an explicit --toml arg, per B-1 forge self-containment).

Execution contract (D-3 / S-1 / R-3 / R-4):
  - Default (no command configured): prints 'soak: n/a — no health command
    configured' and exits 0. No subprocess is spawned (D-3 inert-by-default).
  - Command configured: runs it via shlex.split → Popen(args, shell=False) —
    NEVER shell=True/os.system/f-string concat (S-1 no-shell). Group-TOML docs
    state the value is an arg-list, not a shell expression.
  - Timeout (R-3): uses Popen + start_new_session=True + os.killpg, NOT
    subprocess.run(timeout=). subprocess.run only SIGKILLs the direct child —
    a grandchild (e.g. `sleep` behind a shell wrapper) survives, holds the
    caller's inherited pipe open, and causes a hang. killpg reaps the whole
    process group. Not using runner_protocol.run for the same reason:
    runner_protocol hardcodes capture_output + a fixed 60s — incompatible with
    interactive soak commands and with the user-configurable timeout here.
  - One-shot escalate (R-4): one non-zero/timeout result → immediate escalate,
    no retry, no flake-tolerance. Exit nonzero on regression.

Usage:
    soak_health.py --toml <group-toml-path> [--timeout <seconds>]

Exit codes:
    0  healthy or inert (no command configured)
    1  regression — health command exited nonzero or timed out
    2  error — TOML unreadable / malformed soak_health_command / unexpected exception
"""
from __future__ import annotations

import argparse
import os
import shlex
import signal
import subprocess
import sys
import tomllib
from pathlib import Path


_DEFAULT_TIMEOUT = 120  # seconds


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Thin configurable soak health probe for a camp group deploy.",
    )
    p.add_argument(
        "--toml",
        required=True,
        metavar="PATH",
        help="Absolute path to the group TOML file containing the [release] block.",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=_DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=f"Timeout in seconds for the health command (default: {_DEFAULT_TIMEOUT}). "
             "A hung command is killed after this duration and treated as a regression.",
    )
    return p


def _load_health_command(toml_path: Path) -> str | None:
    """Return soak_health_command from [release] block, or None if absent."""
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise RuntimeError(f"Cannot read group TOML: {toml_path}: {e}") from e
    release = data.get("release", {})
    cmd = release.get("soak_health_command")
    if not cmd or not str(cmd).strip():
        return None
    return str(cmd).strip()


def run_soak(toml_path: Path, timeout_s: int) -> int:
    """Execute the soak health probe. Returns the exit code to use."""
    try:
        cmd = _load_health_command(toml_path)
    except RuntimeError as e:
        print(f"soak: error — {e}", file=sys.stderr)
        return 2

    if cmd is None:
        print("soak: n/a — no health command configured")
        return 0

    # S-1: always use shlex.split → arg-list; NEVER shell=True
    try:
        args = shlex.split(cmd)
    except ValueError as e:
        print(
            f"soak: error — malformed soak_health_command: {e}",
            file=sys.stderr,
        )
        return 2

    try:
        proc = subprocess.Popen(
            args,
            shell=False,  # S-1: explicit no-shell
            # Start in a new session so killpg terminates the entire process
            # group including grandchildren (e.g. a `sleep` behind a shell
            # wrapper). subprocess.run(timeout=) only SIGKILLs the direct
            # child — grandchildren survive and hold the pipe open, hanging
            # the caller.
            start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout_s)  # R-3: configurable timeout
        except subprocess.TimeoutExpired:
            # R-3: hung command is killed after timeout → escalate.
            # Kill the entire process group to reap grandchildren.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass  # already exited
            proc.wait()  # reap the shell process
            print(
                f"soak: regression — health command timed out after {timeout_s}s (R-3)",
                file=sys.stderr,
            )
            return 1
    except FileNotFoundError as e:
        # The command itself does not exist (e.g. a literal metachar string is
        # not an executable) — this is a failure, escalate.
        print(f"soak: regression — health command not found: {e}", file=sys.stderr)
        return 1

    # R-4: one-shot escalate — no retry
    if proc.returncode != 0:
        print(
            f"soak: regression — health command exited {proc.returncode} (R-4 one-shot escalate)",
            file=sys.stderr,
        )
        return 1

    print("soak: healthy")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run_soak(Path(args.toml), args.timeout)


if __name__ == "__main__":
    sys.exit(main())
