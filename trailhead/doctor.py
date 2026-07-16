"""Read-only install-state report for `trailhead doctor`.

The doctor does NOT validate or gate (the design assumes users know what they're
doing — there are no install-validity checks).  It simply reports what trailhead
has installed, discovered from on-disk state:

  - per harness: the registered marketplace + the installed tools (markers),
  - the CLI shim dir and whether each CLI-bearing tool (any tool whose manifest
    declares `cli_bin`) resolves on PATH,
  - the python3 version on PATH (informational).

``exit_code`` is always 0 unless the report itself crashes.

Injectability: ``which_runner`` and ``python_version_runner`` are injectable
so tests never shell out.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from trailhead.capabilities import ConfineError, ManifestError, cli_bearing_manifests
from trailhead.harness import HarnessError, get_harness
from trailhead.pathint import resolve_shim_dir
from trailhead.paths import state_dir
from trailhead.wire import default_manifest_paths


def _discover_cli_names(manifest_paths: dict[str, Path] | None = None) -> list[str]:
    """Every CLI-bearing tool name — discovered from each tool's manifest.

    Not gated by any install config flag: doctor reports on-disk/PATH reality
    regardless of what a config would install.

    Each tool's manifest is loaded independently, so a single broken manifest
    (malformed TOML, a confinement violation, a missing required field) only
    drops that one tool from the CLI list rather than crashing the report —
    doctor's contract is to always exit 0.
    """
    paths = manifest_paths if manifest_paths is not None else default_manifest_paths()
    names: list[str] = []
    for name, path in paths.items():
        try:
            bearing = cli_bearing_manifests({name: path})
        except (ManifestError, ConfineError):
            continue
        names.extend(bearing)
    return names


@dataclass
class DoctorResult:
    """Result of run_doctor()."""

    data: dict
    human_output: str
    exit_code: int


def _default_python_version_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _discover_harnesses(composed_base: Path) -> dict[str, dict]:
    """Report each composed harness tree's registration state via the harness seam.

    Registration state is read through the :class:`~trailhead.harness.base.Harness`
    (``is_registered`` / ``installed_tools`` / ``manifest_name``) rather than by
    re-deriving the harness's on-disk marker scheme here.  An unknown harness dir
    (no registered implementation) is still reported present, but its scheme can't
    be introspected through the seam, so its state reads as empty.

    ``manifest_name`` returns ``None`` both when the manifest is absent and when
    it's present but unparseable; ``marketplace_malformed`` disambiguates the two
    (via :meth:`~trailhead.harness.base.Harness.manifest_exists`) so
    ``_build_human`` can render "present but corrupt" distinctly from "not there".
    """
    out: dict[str, dict] = {}
    if not composed_base.is_dir():
        return out
    for hdir in sorted(composed_base.iterdir()):
        if not hdir.is_dir():
            continue
        try:
            harness = get_harness(hdir.name)
        except HarnessError:
            out[hdir.name] = {"registered": False, "installed": [], "marketplace": None}
            continue
        marketplace = harness.manifest_name(hdir)
        out[hdir.name] = {
            "registered": harness.is_registered(hdir),
            "installed": harness.installed_tools(hdir),
            "marketplace": marketplace,
            "marketplace_malformed": marketplace is None and harness.manifest_exists(hdir),
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
    manifest_paths: dict[str, Path] | None = None,
) -> DoctorResult:
    """Build a read-only report of what trailhead has installed. exit_code is 0."""
    _env = env if env is not None else dict(os.environ)
    _which = which_runner or shutil.which
    _pyrunner = python_version_runner or _default_python_version_runner

    composed_base = state_dir("trailhead", env=_env) / "composed"
    harnesses = _discover_harnesses(composed_base)

    shim_dir = resolve_shim_dir(env=_env)
    clis = {name: _which(name) for name in _discover_cli_names(manifest_paths)}

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
            if info.get("marketplace"):
                mkt = info["marketplace"]
            elif info.get("marketplace_malformed"):
                mkt = "(unreadable)"
            else:
                mkt = "(none)"
            lines.append(f"    marketplace: {mkt} ({reg})")
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
