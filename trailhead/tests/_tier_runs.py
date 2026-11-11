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


def collect(*args: str, cwd: Path = REPO) -> frozenset[str]:
    result = run_pytest("--collect-only", "-q", *args, cwd=cwd)
    assert result.returncode in (0, 5), result.stdout + result.stderr
    return frozenset(line for line in result.stdout.splitlines() if _ID.match(line))


@functools.cache
def collected_ids(*args: str) -> frozenset[str]:
    return collect(*args)


def tier_paths(tier: str, config: Path = REPO / "pyproject.toml") -> list[str]:
    """The paths the tier's CI job collects, from the one table that names them."""
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "test-area-args"), "--tier", tier, "--config", str(config)],
        capture_output=True, text=True, check=True,
    ).stdout
    return out.split()


def tier_ids_outside_its_paths(tier: str, config: Path, cwd: Path) -> frozenset[str]:
    """Tests carrying the tier's marker that the tier's CI job, collecting only its paths, never runs."""
    everywhere = collect("-c", str(config), "--rootdir", str(cwd), "-m", tier, str(cwd), cwd=cwd)
    scoped = collect(
        "-c", str(config), "--rootdir", str(cwd), "-m", tier, *(str(cwd / p) for p in tier_paths(tier, config)),
        cwd=cwd,
    )
    return everywhere - scoped
