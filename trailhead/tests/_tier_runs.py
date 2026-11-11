"""Run pytest as a subprocess against this repository's real config and read its collection."""

from __future__ import annotations

import functools
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TIERS = ("chromium", "seam_real")
_ID = re.compile(r"^\S+\.py::\S")


def run_pytest(*args: str, cwd: Path = REPO) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-n", "0", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
    )


@functools.cache
def collected_ids(*args: str) -> frozenset[str]:
    result = run_pytest("--collect-only", "-q", *args)
    assert result.returncode in (0, 5), result.stdout + result.stderr
    return frozenset(line for line in result.stdout.splitlines() if _ID.match(line))
