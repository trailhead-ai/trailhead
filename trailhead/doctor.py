"""Doctor rollup for trailhead — aggregates per-tool doctor outputs.

D-6 design:
  For each wired tool, run `<tool> doctor --json` if the tool exposes it
  (camp does; lore does not today).  A tool with no doctor verb gets a
  "no doctor — n/a" entry; the rollup never crashes on a missing verb.

R-3 (binding):
  Each per-tool subprocess gets a wall-clock timeout (5 s).  Both
  subprocess.TimeoutExpired and json.JSONDecodeError are caught → a named
  per-tool error entry.  The rollup always completes cleanly regardless of
  individual tool failures.

R-2 drift check:
  Compare the config's declared active capabilities against what skills dirs
  are present in the composed dest.  Declared-but-absent and
  present-but-undeclared both surface as doctor findings.

U-2 python check:
  Verify that the `python3` on PATH is ≥ 3.10 (trailhead/paths.py uses X|Y
  union annotations which require 3.10+; macOS system python is 3.9.6).

A-9 hygiene:
  --json for machine reads; human output groups by tool.  Errors → stderr;
  summary/values → stdout.

Injectability (B-3 / hermeticity):
  doctor_runner: Callable(args: list[str], *, timeout: int) → CompletedProcess
    used to run `<tool> doctor --json`.  Tests always pass a stub.
  python_version_runner: Callable(cmd: list[str]) → CompletedProcess
    used to probe python3 --version.  Tests always pass a stub.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from trailhead.config import load_config
from trailhead.paths import state_dir

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DOCTOR_TIMEOUT = 5  # seconds per tool subprocess

# Tools known to have a doctor verb
_TOOLS_WITH_DOCTOR = frozenset({"camp"})

_PYTHON_MIN_MAJOR = 3
_PYTHON_MIN_MINOR = 10


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class DoctorResult:
    """Result of run_doctor().

    Attributes:
        data:         The aggregate JSON-friendly dict (for --json output).
        human_output: The human-readable multiline string.
        exit_code:    0 if all checks passed; 1 if any failed.
    """

    data: dict
    human_output: str
    exit_code: int


# ---------------------------------------------------------------------------
# Injectable defaults
# ---------------------------------------------------------------------------


def _default_doctor_runner(args: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    """Default per-tool doctor subprocess runner.

    Runs `<tool> doctor --json` using subprocess.run with a wall-clock timeout.
    """
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _default_python_version_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    """Default python3 --version runner."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Per-tool doctor probe
# ---------------------------------------------------------------------------


def _probe_tool_doctor(
    tool: str,
    runner: Callable,
    *,
    tool_bin: Optional[str] = None,
) -> dict:
    """Run `<tool> doctor --json` and return a structured entry.

    Returns a dict with at least a 'status' key:
      - {"status": "ok", "checks": [...], "any_failed": bool}
      - {"status": "no_doctor"}
      - {"status": "timeout", "error": "..."}
      - {"status": "parse_error", "error": "..."}
      - {"status": "error", "error": "...", "returncode": int}

    Never raises — all failures are captured as named entries (R-3).
    """
    if tool not in _TOOLS_WITH_DOCTOR:
        return {"status": "no_doctor"}

    bin_name = tool_bin or tool
    args = [bin_name, "doctor", "--json"]

    try:
        result = runner(args, timeout=_DOCTOR_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "error": f"{tool} doctor timed out after {_DOCTOR_TIMEOUT}s",
        }
    except FileNotFoundError:
        return {"status": "no_doctor"}

    # Try to parse the JSON regardless of returncode (a failing check may exit 1
    # but still emit valid JSON with its checks array)
    raw_stdout = result.stdout or ""
    try:
        parsed = json.loads(raw_stdout)
    except json.JSONDecodeError as exc:
        return {
            "status": "parse_error",
            "error": f"{tool} doctor returned malformed JSON: {exc}",
            "returncode": result.returncode,
        }

    # Valid JSON parsed — extract checks and any_failed
    checks = parsed.get("checks", [])
    any_failed = not parsed.get("pass", True)

    return {
        "status": "ok",
        "checks": checks,
        "any_failed": any_failed,
    }


# ---------------------------------------------------------------------------
# Python version check (U-2)
# ---------------------------------------------------------------------------


def _check_python_version(python_runner: Callable) -> dict:
    """Check that python3 on PATH is ≥ 3.10 (U-2).

    Returns a doctor check dict:
      {"check": "python3_version", "description": "...", "pass": bool, "details": "..."}
    """
    description = f"python3 ≥ {_PYTHON_MIN_MAJOR}.{_PYTHON_MIN_MINOR} on PATH"

    try:
        result = python_runner(["python3", "--version"])
    except FileNotFoundError:
        return {
            "check": "python3_version",
            "description": description,
            "pass": False,
            "details": (
                "python3 not found on PATH — trailhead/paths.py requires Python ≥ "
                f"{_PYTHON_MIN_MAJOR}.{_PYTHON_MIN_MINOR} (uses X|Y union annotations)"
            ),
        }

    version_str = (result.stdout or "").strip()
    # "Python 3.11.4" → parse version
    try:
        parts = version_str.replace("Python ", "").split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return {
            "check": "python3_version",
            "description": description,
            "pass": False,
            "details": f"could not parse python3 version: {version_str!r}",
        }

    ok = (major, minor) >= (_PYTHON_MIN_MAJOR, _PYTHON_MIN_MINOR)
    return {
        "check": "python3_version",
        "description": description,
        "pass": ok,
        "details": (
            version_str
            if ok
            else (
                f"{version_str} — trailhead/paths.py requires Python ≥ "
                f"{_PYTHON_MIN_MAJOR}.{_PYTHON_MIN_MINOR} (uses X|Y union annotations); "
                f"macOS system python3 (/usr/bin/python3) is often 3.9.x"
            )
        ),
    }


# ---------------------------------------------------------------------------
# R-2 drift check
# ---------------------------------------------------------------------------


def _check_drift(
    wired_tools: dict[str, set[str]],
    *,
    env: dict[str, str] | None,
) -> list[dict]:
    """Compare config's declared capabilities against the composed dest dirs.

    Returns a list of drift check dicts (one per tool that was examined).
    Each entry: {"check": "drift:<tool>", "pass": bool, "declared": [...],
                 "present": [...], "drift_absent": [...], "drift_extra": [...]}
    """
    _env = env if env is not None else dict(os.environ)
    composed_root = state_dir("trailhead", env=_env) / "composed"

    cfg = load_config(env=_env)
    drift_checks = []

    for tool, declared_caps in wired_tools.items():
        dest = composed_root / tool / "plugins" / tool / "skills"

        # Skills present in the filesystem
        if dest.is_dir():
            present_caps = {d.name for d in dest.iterdir() if d.is_dir()}
        else:
            # No composed dest at all — not yet wired; skip drift check
            # (drift is only meaningful after a successful wire has run)
            present_caps = None

        if present_caps is None:
            # No dest → no drift to report
            drift_checks.append({
                "check": f"drift:{tool}",
                "description": f"config↔filesystem coherence for {tool}",
                "pass": True,
                "declared": sorted(set(cfg.capabilities.get(tool, [])) | set(declared_caps)),
                "present": [],
                "note": "not yet composed",
            })
            continue

        # Declared in config (may differ from wired_tools if config is stale)
        config_caps = set(cfg.capabilities.get(tool, []))
        # Use the union for comparison: anything declared in config or wired_tools
        all_declared = config_caps | set(declared_caps)

        drift_absent = sorted(all_declared - present_caps)
        drift_extra = sorted(present_caps - all_declared)

        has_drift = bool(drift_absent or drift_extra)
        check: dict = {
            "check": f"drift:{tool}",
            "description": f"config↔filesystem coherence for {tool}",
            "pass": not has_drift,
            "declared": sorted(all_declared),
            "present": sorted(present_caps),
        }
        if drift_absent:
            check["drift_absent"] = drift_absent
        if drift_extra:
            check["drift_extra"] = drift_extra

        drift_checks.append(check)

    return drift_checks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_doctor(
    *,
    as_json: bool = False,
    wired_tools: dict[str, set[str]] | None = None,
    doctor_runner: Callable | None = None,
    python_version_runner: Callable | None = None,
    env: dict[str, str] | None = None,
) -> DoctorResult:
    """Aggregate per-tool doctor outputs into a rollup result.

    Args:
        as_json:               Whether to format output as JSON.
        wired_tools:           Mapping of tool name → set of caps.  If None,
                               loads from config.  Tools not in wired_tools are
                               skipped.
        doctor_runner:         Injectable subprocess runner for per-tool doctor
                               calls.  Defaults to real subprocess.run.
        python_version_runner: Injectable runner for `python3 --version`.
                               Defaults to real subprocess.run.
        env:                   Env dict for path resolution (hermeticity).

    Returns:
        DoctorResult with data (JSON-friendly dict), human_output, and exit_code.
    """
    _env = env if env is not None else dict(os.environ)
    _doctor_runner = doctor_runner or _default_doctor_runner
    _python_runner = python_version_runner or _default_python_version_runner

    if wired_tools is None:
        cfg = load_config(env=_env)
        wired_tools = {tool: set(caps) for tool, caps in cfg.capabilities.items()}

    # ----------------------------------------------------------------
    # Step 1: Python version check (U-2)
    # ----------------------------------------------------------------
    global_checks = [_check_python_version(_python_runner)]

    # ----------------------------------------------------------------
    # Step 2: Per-tool doctor probes
    # ----------------------------------------------------------------
    tools_data: dict[str, dict] = {}
    any_failed = False

    for tool in wired_tools:
        entry = _probe_tool_doctor(tool, _doctor_runner)
        tools_data[tool] = entry

        status = entry.get("status")
        if status == "no_doctor":
            pass  # not a failure
        elif status == "ok":
            if entry.get("any_failed"):
                any_failed = True
        else:
            # timeout, parse_error, error → counts as failure
            any_failed = True

    # ----------------------------------------------------------------
    # Step 3: Drift check (R-2)
    # ----------------------------------------------------------------
    drift_checks = _check_drift(wired_tools, env=_env)
    for dc in drift_checks:
        if not dc.get("pass", True):
            any_failed = True

    # Propagate any_failed from global checks
    for gc in global_checks:
        if not gc.get("pass", True):
            any_failed = True

    # ----------------------------------------------------------------
    # Step 4: Build result data
    # ----------------------------------------------------------------
    data = {
        "any_failed": any_failed,
        "checks": global_checks,
        "tools": tools_data,
        "drift_checks": drift_checks,
    }

    human_lines = _build_human_output(data, global_checks, tools_data, drift_checks)
    human_output = "\n".join(human_lines)

    exit_code = 1 if any_failed else 0
    return DoctorResult(data=data, human_output=human_output, exit_code=exit_code)


# ---------------------------------------------------------------------------
# Human output builder
# ---------------------------------------------------------------------------


def _build_human_output(
    data: dict,
    global_checks: list[dict],
    tools_data: dict[str, dict],
    drift_checks: list[dict],
) -> list[str]:
    """Build a human-readable grouped output."""
    lines = []

    lines.append("trailhead doctor:")
    lines.append("")

    # Global checks
    if global_checks:
        lines.append("  global:")
        for check in global_checks:
            status = "PASS" if check.get("pass") else "FAIL"
            lines.append(f"    [{status}] {check.get('description', check.get('check'))}")
            if not check.get("pass") and check.get("details"):
                lines.append(f"           {check['details']}")
        lines.append("")

    # Per-tool
    for tool, entry in tools_data.items():
        status = entry.get("status")
        lines.append(f"  {tool}:")
        if status == "no_doctor":
            lines.append(f"    no doctor (n/a)")
        elif status == "ok":
            checks = entry.get("checks", [])
            if not checks:
                lines.append("    (no checks)")
            for check in checks:
                c_status = "PASS" if check.get("pass") else "FAIL"
                lines.append(f"    [{c_status}] {check.get('description', check.get('check', '?'))}")
                if not check.get("pass") and check.get("details"):
                    lines.append(f"           {check['details']}")
        elif status == "timeout":
            lines.append(f"    [ERROR] {entry.get('error', 'timed out')}")
        elif status == "parse_error":
            lines.append(f"    [ERROR] {entry.get('error', 'malformed JSON')}")
        else:
            lines.append(f"    [ERROR] {entry.get('error', 'unknown error')}")
        lines.append("")

    # Drift checks
    if drift_checks:
        drift_failures = [dc for dc in drift_checks if not dc.get("pass", True)]
        if drift_failures:
            lines.append("  drift (config↔filesystem):")
            for dc in drift_failures:
                tool = dc.get("check", "").replace("drift:", "")
                absent = dc.get("drift_absent", [])
                extra = dc.get("drift_extra", [])
                if absent:
                    lines.append(f"    [{tool}] declared-but-absent: {', '.join(absent)}")
                if extra:
                    lines.append(f"    [{tool}] present-but-undeclared: {', '.join(extra)}")
            lines.append("")

    overall = "PASS" if not data.get("any_failed") else "FAIL"
    lines.append(f"overall: {overall}")

    return lines
