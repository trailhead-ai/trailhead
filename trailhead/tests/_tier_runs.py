"""Run pytest as a subprocess against this repository's real config, and read the tier paths."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TIERS = ("chromium", "seam_real")


def run_pytest(*args: str, cwd: Path = REPO) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-n", "0", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
    )


def tier_paths(tier: str, config: Path = REPO / "pyproject.toml") -> list[str]:
    """The paths the tier's CI job collects, from the one table that names them."""
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "test-area-args"), "--tier", tier, "--config", str(config)],
        capture_output=True, text=True, check=True,
    ).stdout
    return out.split()

