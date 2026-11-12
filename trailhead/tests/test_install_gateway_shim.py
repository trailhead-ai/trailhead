"""The `outpost-gateway` shim: built beside `ranger` when the Outpost tool is enabled."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from trailhead.install import build_cli_shims, resolve_cli_tools
from trailhead.install_config import ResolvedHarness, ResolvedPlugin
from trailhead.pathint import resolve_shim_dir


def _env(tmp_path: Path) -> dict[str, str]:
    return {"HOME": str(tmp_path / "home"), "XDG_STATE_HOME": str(tmp_path / "state")}


def _tools() -> dict[str, Path]:
    return resolve_cli_tools({"camp": True, "lore": True, "portage": True})


class TestShimBuild:
    def test_outpost_enabled_writes_a_shim_that_runs_help(self, tmp_path):
        env = _env(tmp_path)
        build_cli_shims(_tools(), env=env, outpost_enabled=True)
        shim = resolve_shim_dir(env=env) / "outpost-gateway"
        assert shim.exists()
        run_env = {
            **os.environ,
            "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
        }
        proc = subprocess.run([str(shim), "--help"], capture_output=True, text=True, env=run_env, timeout=60)
        assert proc.returncode == 0, proc.stderr
        assert "outpost-gateway" in proc.stdout and "run list" in proc.stdout

    def test_outpost_disabled_writes_no_shim(self, tmp_path):
        env = _env(tmp_path)
        build_cli_shims(_tools(), env=env, outpost_enabled=False)
        shim_dir = resolve_shim_dir(env=env)
        assert (shim_dir / "ranger").exists()
        assert not (shim_dir / "outpost-gateway").exists()

    def test_default_is_no_gateway_shim(self, tmp_path):
        env = _env(tmp_path)
        build_cli_shims(_tools(), env=env)
        assert not (resolve_shim_dir(env=env) / "outpost-gateway").exists()


class TestInstallPipeline:
    def _cfg(self, plugin_names):
        return type(
            "Cfg", (), {"harnesses": [ResolvedHarness(name="claude_code", plugins=[ResolvedPlugin(name=n, subagents={}, skills={}) for n in plugin_names])]}
        )()

    def test_outpost_enabled_reads_the_resolved_plugin_selection(self):
        from trailhead.install import outpost_enabled

        assert outpost_enabled(self._cfg(["lore", "outpost"])) is True
        assert outpost_enabled(self._cfg(["lore", "camp"])) is False
