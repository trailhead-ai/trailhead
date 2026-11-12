"""Fake-harness helper for tests of the harness-process seam.

A fake harness is a real executable copied to ``<root>/claude/versions/<name>``
and launched through a symlink named ``claude``: the kernel takes a process's
comm from the name it was exec'd by, and ``/proc/<pid>/exe`` resolves to the
real file, so the fake matches the shape the real harness shows.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def make_fake_harness(root: Path, *, versions_dir: str = "claude/versions") -> Path:
    """Return a ``claude``-named launcher whose exe lives in ``<root>/<versions_dir>/9.9.9``."""
    exe_dir = root / versions_dir
    exe_dir.mkdir(parents=True, exist_ok=True)
    exe = exe_dir / "9.9.9"
    shutil.copy(shutil.which("sh") or "/bin/sh", exe)
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / "claude"
    launcher.symlink_to(exe)
    return launcher


def run_under_fake_harness(
    launcher: Path, script: str, *, intermediate: bool = True
) -> tuple[subprocess.CompletedProcess[str], int]:
    """Run shell *script* as a child of a ``claude``-named process.

    Returns the completed process (its stdout is the script's) and the fake's pid.
    With *intermediate*, the script runs under an extra ``/bin/sh`` hop.
    """
    inner = f"/bin/sh -c '{script}; :'" if intermediate else script
    proc = subprocess.Popen(
        [str(launcher), "-c", f"{inner}; :"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    out, err = proc.communicate()
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err), proc.pid
