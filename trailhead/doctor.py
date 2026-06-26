"""Read-only install-state report for `trailhead doctor`.

The doctor does NOT validate or gate (the design assumes users know what they're
doing — there are no install-validity checks).  It simply reports what trailhead
has installed, discovered from on-disk state:

  - per harness: the registered marketplace + the installed tools (markers),
  - the camp/lore CLI shim dir and whether `camp`/`lore` resolve on PATH,
  - the python3 version on PATH (informational).

``exit_code`` is always 0 unless the report itself crashes.

Injectability: ``which_runner`` and ``python_version_runner`` are injectable
so tests never shell out.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from trailhead.pathint import resolve_shim_dir
from trailhead.paths import state_dir

_REGISTERED_MARKER = ".trailhead-registered"
_INSTALLED_MARKER_PREFIX = ".trailhead-installed-"
_CLI_NAMES = ("camp", "lore")


@dataclass
class DoctorResult:
    """Result of run_doctor()."""

    data: dict
    human_output: str
    exit_code: int


def _default_python_version_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _discover_harnesses(composed_base: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not composed_base.is_dir():
        return out
    for hdir in sorted(composed_base.iterdir()):
        if not hdir.is_dir():
            continue
        installed = sorted(
            f.name[len(_INSTALLED_MARKER_PREFIX) :]
            for f in hdir.iterdir()
            if f.is_file() and f.name.startswith(_INSTALLED_MARKER_PREFIX)
        )
        marketplace_name = None
        mkt = hdir / ".claude-plugin" / "marketplace.json"
        if mkt.exists():
            try:
                marketplace_name = json.loads(mkt.read_text()).get("name")
            except (OSError, json.JSONDecodeError):
                marketplace_name = "(unreadable)"
        out[hdir.name] = {
            "registered": (hdir / _REGISTERED_MARKER).exists(),
            "installed": installed,
            "marketplace": marketplace_name,
        }
    return out


def _python_version(python_runner: Callable) -> str:
    try:
        result = python_runner(["python3", "--version"])
    except FileNotFoundError:
        return "not found on PATH"
    return (result.stdout or result.stderr or "").strip() or "unknown"


def run_doctor(
    *,
    as_json: bool = False,
    env: dict[str, str] | None = None,
    which_runner: Callable[[str], Optional[str]] | None = None,
    python_version_runner: Callable | None = None,
) -> DoctorResult:
    """Build a read-only report of what trailhead has installed. exit_code is 0."""
    _env = env if env is not None else dict(os.environ)
    _which = which_runner or shutil.which
    _pyrunner = python_version_runner or _default_python_version_runner

    composed_base = state_dir("trailhead", env=_env) / "composed"
    harnesses = _discover_harnesses(composed_base)

    shim_dir = resolve_shim_dir(env=_env)
    clis = {name: _which(name) for name in _CLI_NAMES}

    data = {
        "harnesses": harnesses,
        "shim_dir": str(shim_dir),
        "shim_dir_present": shim_dir.exists(),
        "clis": clis,
        "python3_version": _python_version(_pyrunner),
    }

    return DoctorResult(data=data, human_output=_build_human(data), exit_code=0)


def _build_human(data: dict) -> str:
    lines = ["trailhead doctor (read-only report):", ""]

    harnesses = data["harnesses"]
    if not harnesses:
        lines.append("  no harnesses installed")
    else:
        for hname, info in harnesses.items():
            lines.append(f"  {hname}:")
            reg = "registered" if info["registered"] else "not registered"
            lines.append(f"    marketplace: {info.get('marketplace') or '(none)'} ({reg})")
            installed = ", ".join(info["installed"]) or "(none)"
            lines.append(f"    installed: {installed}")
    lines.append("")

    lines.append("  CLIs on PATH:")
    for name, resolved in data["clis"].items():
        lines.append(f"    {name}: {resolved or 'not on PATH'}")
    lines.append(
        f"  shim dir: {data['shim_dir']} ({'present' if data['shim_dir_present'] else 'absent'})"
    )
    lines.append(f"  python3: {data['python3_version']}")

    return "\n".join(lines)
