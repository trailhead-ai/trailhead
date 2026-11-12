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

    def test_the_pinned_script_follows_the_root_the_shims_are_built_from(self, tmp_path):
        env = _env(tmp_path)
        root = tmp_path / "other-root"
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "_outpost_gateway.py").write_text("print('from other root')\n")
        build_cli_shims(_tools(), env=env, root=root, outpost_enabled=True)
        shim = resolve_shim_dir(env=env) / "outpost-gateway"
        assert str(root / "bin" / "_outpost_gateway.py") in shim.read_text()

    def test_the_shim_runs_its_pinned_interpreter_not_what_the_session_puts_on_path(self, tmp_path):
        env = _env(tmp_path)
        build_cli_shims(_tools(), env=env, outpost_enabled=True)
        shim = resolve_shim_dir(env=env) / "outpost-gateway"
        marker = tmp_path / "ran-hostile-code"
        hostile_bin = tmp_path / "venv-bin"
        hostile_bin.mkdir()
        fake_python = hostile_bin / "python3"
        fake_python.write_text(f'#!/bin/sh\necho python3 >> "{marker}"\nexit 0\n')
        fake_python.chmod(0o755)
        planted = tmp_path / "planted"
        planted.mkdir()
        for module in ("sitecustomize", "json"):
            (planted / f"{module}.py").write_text(f'open("{marker}", "a").write("{module}\\n")\n')
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        (cwd / "json.py").write_text(f'open("{marker}", "a").write("cwd-json\\n")\n')
        run_env = {
            "HOME": env["HOME"],
            "PATH": os.pathsep.join([str(hostile_bin), "/usr/bin", "/bin"]),
            "PYTHONPATH": str(planted),
        }
        proc = subprocess.run(
            [str(shim), "--help"], capture_output=True, text=True, env=run_env, cwd=cwd, timeout=60
        )
        assert proc.returncode == 0, proc.stderr
        assert "run list" in proc.stdout
        assert not marker.exists(), marker.read_text()

    def test_an_installing_venv_that_is_a_copy_does_not_put_its_site_packages_in_front(self, tmp_path, monkeypatch):
        venv = tmp_path / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--copies", "--without-pip", str(venv)], check=True)
        marker = tmp_path / "ran-pth"
        (next((venv / "lib").glob("python*/site-packages"))).joinpath("evil.pth").write_text(
            f'import os; open("{marker}", "a").write("pth")\n'
        )
        monkeypatch.setattr("trailhead.pathint.sys.executable", str(venv / "bin" / "python"))
        env = _env(tmp_path)
        build_cli_shims(_tools(), env=env, outpost_enabled=True)
        proc = subprocess.run(
            [str(resolve_shim_dir(env=env) / "outpost-gateway"), "--help"],
            capture_output=True,
            text=True,
            env={"HOME": env["HOME"], "PATH": "/usr/bin:/bin"},
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "run list" in proc.stdout
        assert not marker.exists()

    def _env_seen_by_interpreter(self, tmp_path, monkeypatch, session_env):
        """Run the real shim against an interpreter that records the environment it is started with."""
        dump = tmp_path / "interpreter-env"
        fake = tmp_path / "fake-python"
        fake.write_text(f'#!/bin/sh\n/usr/bin/env > "{dump}"\n')
        fake.chmod(0o755)
        monkeypatch.setattr("trailhead.pathint.sys.executable", str(fake))
        env = _env(tmp_path)
        build_cli_shims(_tools(), env=env, outpost_enabled=True)
        proc = subprocess.run(
            [str(resolve_shim_dir(env=env) / "outpost-gateway"), "run", "list"],
            capture_output=True,
            text=True,
            env=session_env,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        return dict(line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line)

    def test_the_interpreter_is_started_without_the_dynamic_loader_variables(self, tmp_path, monkeypatch):
        seen = self._env_seen_by_interpreter(
            tmp_path,
            monkeypatch,
            {
                "HOME": str(tmp_path / "home"),
                "PATH": "/usr/bin:/bin",
                "LD_PRELOAD": "/nonexistent/evil.so",
                "LD_LIBRARY_PATH": "/nonexistent",
                "LD_AUDIT": "/nonexistent/audit.so",
                "DYLD_INSERT_LIBRARIES": "/nonexistent/evil.dylib",
                "DYLD_LIBRARY_PATH": "/nonexistent",
            },
        )
        assert not [name for name in seen if name.startswith(("LD_", "DYLD_"))], seen

    def test_the_interpreter_gets_only_what_the_command_reads_and_a_fixed_path(self, tmp_path, monkeypatch):
        spaced = tmp_path / "state dir"
        seen = self._env_seen_by_interpreter(
            tmp_path,
            monkeypatch,
            {
                "HOME": str(tmp_path / "home dir"),
                "PATH": f"{tmp_path}/hostile-bin:/usr/bin:/bin",
                "TRAILHEAD_STATE_DIR": str(spaced),
                "OUTPOST_STATE_DIR": str(spaced / "outpost"),
                "XDG_STATE_HOME": str(spaced / "xdg"),
                "SOME_SESSION_VARIABLE": "x",
            },
        )
        assert seen["HOME"] == str(tmp_path / "home dir")
        assert seen["TRAILHEAD_STATE_DIR"] == str(spaced)
        assert seen["OUTPOST_STATE_DIR"] == str(spaced / "outpost")
        assert seen["XDG_STATE_HOME"] == str(spaced / "xdg")
        assert seen["PATH"] == "/usr/bin:/bin"
        assert "SOME_SESSION_VARIABLE" not in seen

    def test_a_state_variable_the_session_did_not_set_is_not_passed_empty(self, tmp_path, monkeypatch):
        seen = self._env_seen_by_interpreter(
            tmp_path, monkeypatch, {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin"}
        )
        assert "TRAILHEAD_STATE_DIR" not in seen and "XDG_STATE_HOME" not in seen

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
            "Cfg",
            (),
            {
                "harnesses": [
                    ResolvedHarness(
                        name="claude_code",
                        plugins=[ResolvedPlugin(name=n, subagents={}, skills={}) for n in plugin_names],
                    )
                ]
            },
        )()

    def test_outpost_enabled_reads_the_resolved_plugin_selection(self):
        from trailhead.install import outpost_enabled

        assert outpost_enabled(self._cfg(["lore", "outpost"])) is True
        assert outpost_enabled(self._cfg(["lore", "camp"])) is False
