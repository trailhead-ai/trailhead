"""`trailhead install` builds a `docket` shim on PATH when install_docket_cli is on, and none when it is off."""

from __future__ import annotations

import subprocess
from pathlib import Path

from trailhead.install import build_cli_shims, resolve_cli_tools
from trailhead.install_config import resolve_config
from trailhead.pathint import resolve_shim_dir


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "PATH": "/usr/bin:/bin",
    }


def _install(tmp_path: Path, toml: str) -> dict[str, str]:
    cfg_path = tmp_path / "install.toml"
    cfg_path.write_text(toml)
    cfg = resolve_config(config_path=cfg_path, detected_harnesses=["claude_code"])
    env = _env(tmp_path)
    build_cli_shims(resolve_cli_tools(cfg.cli_flags), env=env)
    return env


def test_install_docket_cli_true_builds_a_shim_that_runs_the_tool(tmp_path):  # inert-gate: allow seam smoke: install -> shim -> docket CLI wiring has no input to vary
    env = _install(tmp_path, "install_docket_cli = true\nplugins = []\n")
    shim = resolve_shim_dir(env=env) / "docket"
    proc = subprocess.run([str(shim), "--help"], capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "usage: docket" in proc.stdout


def test_install_docket_cli_false_builds_no_shim(tmp_path):
    env = _install(tmp_path, "install_docket_cli = false\nplugins = []\n")
    shim_dir = resolve_shim_dir(env=env)
    assert (shim_dir / "camp").exists()
    assert not (shim_dir / "docket").exists()
