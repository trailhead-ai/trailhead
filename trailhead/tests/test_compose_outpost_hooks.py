"""The Outpost plugin's SessionStart hook: composed with the plugin, absent without it,
removed by uninstall, and its script finds and runs the trailhead CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from trailhead.capabilities import load_manifest
from trailhead.compose import apply_plan, compose_plan
from trailhead.install import build_cli_shims, resolve_cli_tools
from trailhead.harness import harness_process
from trailhead.tests._gateway_support import (
    REGISTRATION_AUTHORITY,
    StubOutpost,
    register_port_env,
    run_under_harness,
    write_authority,
)
from trailhead.tests._harness_process_support import make_fake_harness
from trailhead.uninstall import run_uninstall

REPO_ROOT = Path(__file__).resolve().parents[2]


def _compose(tool: str, dest: Path) -> None:
    manifest = load_manifest(REPO_ROOT / "tools" / tool / "capabilities.toml")
    apply_plan(compose_plan(manifest, {}, {}, dest))


def _session_start_commands(plugin: Path) -> list[str]:
    hooks = json.loads((plugin / "hooks" / "hooks.json").read_text())
    return [
        h["command"]
        for entry in hooks["hooks"]["SessionStart"]
        for h in entry["hooks"]
        if h.get("type") == "command"
    ]


class TestCompose:
    def test_outpost_plugin_carries_the_hook_and_its_script(self, tmp_path):
        dest = tmp_path / "plugins" / "outpost"
        _compose("outpost", dest)
        hooks = json.loads((dest / "hooks" / "hooks.json").read_text())
        (entry,) = hooks["hooks"]["SessionStart"]
        assert entry["matcher"] == "startup|resume|clear|compact"
        (command,) = _session_start_commands(dest)
        script = Path(command.strip('"').replace("${CLAUDE_PLUGIN_ROOT}", str(dest)))
        assert script == dest / "hooks" / "gateway-register"
        assert os.access(script, os.X_OK)

    def test_hook_timeout_fits_the_session_start_budget(self, tmp_path):
        dest = tmp_path / "plugins" / "outpost"
        _compose("outpost", dest)
        hooks = json.loads((dest / "hooks" / "hooks.json").read_text())
        (hook,) = [h for e in hooks["hooks"]["SessionStart"] for h in e["hooks"]]
        assert hook["timeout"] <= 3

    def test_no_other_tool_composes_the_gateway_hook(self, tmp_path):
        others = [
            d.name
            for d in (REPO_ROOT / "tools").iterdir()
            if (d / "capabilities.toml").exists() and d.name != "outpost"
        ]
        assert others
        for tool in others:
            dest = tmp_path / "plugins" / tool
            _compose(tool, dest)
            assert not (dest / "hooks" / "gateway-register").exists()
            hooks_file = dest / "hooks" / "hooks.json"
            commands = _session_start_commands(dest) if hooks_file.exists() else []
            assert not any("gateway-register" in c for c in commands), tool


class TestScript:
    def _script(self, tmp_path) -> Path:
        dest = tmp_path / "plugins" / "outpost"
        _compose("outpost", dest)
        return dest / "hooks" / "gateway-register"

    def _fake_cli(self, path: Path, record: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'#!/bin/sh\necho "$@" >> "{record}"\necho noise\nexit 3\n')
        path.chmod(0o755)

    def test_runs_the_shim_under_the_trailhead_state_dir_with_the_hook_flag(self, tmp_path):
        record = tmp_path / "args.txt"
        self._fake_cli(tmp_path / "state" / "bin" / "trailhead", record)
        proc = subprocess.run(
            [str(self._script(tmp_path))],
            input="{}",
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "TRAILHEAD_STATE_DIR": str(tmp_path / "state"), "HOME": str(tmp_path)},
        )
        assert record.read_text().split() == ["gateway", "register", "--hook"]
        assert (proc.returncode, proc.stdout) == (0, "")

    def test_never_runs_a_trailhead_found_on_path_when_the_shim_is_missing(self, tmp_path):
        record = tmp_path / "args.txt"
        self._fake_cli(tmp_path / "onpath" / "trailhead", record)
        proc = subprocess.run(
            [str(self._script(tmp_path))],
            input="{}",
            capture_output=True,
            text=True,
            env={
                "PATH": f"{tmp_path / 'onpath'}:/usr/bin:/bin",
                "TRAILHEAD_STATE_DIR": str(tmp_path / "empty"),
                "HOME": str(tmp_path),
            },
        )
        assert not record.exists()
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
        (line,) = (tmp_path / "empty" / "gateway" / "register.log").read_text().splitlines()
        assert line.endswith(" - cli_not_found")

    def test_a_shim_that_is_not_executable_is_not_run(self, tmp_path):
        record = tmp_path / "args.txt"
        shim = tmp_path / "state" / "bin" / "trailhead"
        self._fake_cli(shim, record)
        shim.chmod(0o644)
        proc = subprocess.run(
            [str(self._script(tmp_path))],
            input="{}",
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "TRAILHEAD_STATE_DIR": str(tmp_path / "state"), "HOME": str(tmp_path)},
        )
        assert not record.exists()
        assert (proc.returncode, proc.stdout) == (0, "")
        assert (tmp_path / "state" / "gateway" / "register.log").read_text().endswith(" - cli_not_found\n")

    def test_without_a_trailhead_cli_it_exits_zero_silently_and_leaves_a_trace(self, tmp_path):
        proc = subprocess.run(
            [str(self._script(tmp_path))],
            input="{}",
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "TRAILHEAD_STATE_DIR": str(tmp_path / "empty"), "HOME": str(tmp_path)},
        )
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
        (line,) = (tmp_path / "empty" / "gateway" / "register.log").read_text().splitlines()
        assert line.endswith(" - cli_not_found")

    def test_a_trace_that_cannot_be_written_leaves_the_exit_and_output_untouched(self, tmp_path):
        state = tmp_path / "ro"
        state.mkdir()
        state.chmod(0o500)
        try:
            proc = subprocess.run(
                [str(self._script(tmp_path))],
                input="{}",
                capture_output=True,
                text=True,
                env={"PATH": "/usr/bin:/bin", "TRAILHEAD_STATE_DIR": str(state), "HOME": str(tmp_path)},
            )
        finally:
            state.chmod(0o700)
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")

    def test_after_a_real_install_the_hook_runs_the_cli_install_wrote(self, tmp_path):
        state = tmp_path / "state"
        env = {"TRAILHEAD_STATE_DIR": str(state), "HOME": str(tmp_path)}
        tools = resolve_cli_tools({"camp": True, "lore": True, "portage": True})
        build_cli_shims(tools, env=env, outpost_enabled=True)
        launcher = make_fake_harness(tmp_path / "fake")
        proc = run_under_harness(
            launcher,
            str(self._script(tmp_path)),
            env={
                **env,
                "OUTPOST_STATE_DIR": str(tmp_path / "outpost-empty"),
                "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
            },
            on_start=lambda pid: None,
            stdin="{}",
        )
        assert (proc.returncode, proc.stdout) == (0, "")
        (line,) = (state / "gateway" / "register.log").read_text().splitlines()
        assert line.split()[2] == "authority_missing"


class TestUninstall:
    def test_uninstall_removes_the_composed_tree_that_carried_the_hook(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        env = {
            **os.environ,
            "TRAILHEAD_STATE_DIR": str(tmp_path),
            "HOME": str(home),
            "TRAILHEAD_CLAUDE_DIR": str(tmp_path / "claude-dir"),
        }
        plugin = tmp_path / "composed" / "claude_code" / "plugins" / "outpost"
        _compose("outpost", plugin)
        (plugin / ".claude-plugin").mkdir(exist_ok=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text("{}")
        claude_dir = tmp_path / "claude-dir"
        claude_dir.mkdir()
        (claude_dir / ".trailhead-registered").write_text("{}")
        (claude_dir / ".trailhead-installed-outpost").write_text("{}")
        assert (plugin / "hooks" / "gateway-register").is_file()
        calls: list[list[str]] = []

        def runner(args, **kw):
            calls.append(list(args))

        run_uninstall(env=env, assume_yes=True, runner=runner)
        assert ["claude", "plugin", "uninstall", "outpost@trailhead"] in [c[:4] for c in calls]
        assert not (plugin / "hooks" / "hooks.json").exists()


class TestRealInstallReachesOutpost:
    def test_the_hook_registers_through_the_shim_install_wrote(self, tmp_path):
        state = tmp_path / "state"
        env = {"TRAILHEAD_STATE_DIR": str(state), "HOME": str(tmp_path)}
        build_cli_shims(resolve_cli_tools({"camp": True}), env=env, outpost_enabled=True)
        outpost_state = tmp_path / "outpost-state"
        write_authority(outpost_state)
        launcher = make_fake_harness(tmp_path / "fake")
        dest = tmp_path / "plugins" / "outpost"
        _compose("outpost", dest)
        found = []
        with StubOutpost() as stub:
            proc = run_under_harness(
                launcher,
                str(dest / "hooks" / "gateway-register"),
                env={
                    **env,
                    **register_port_env(stub.port),
                    "OUTPOST_STATE_DIR": str(outpost_state),
                    "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
                },
                on_start=lambda pid: found.append(harness_process(start_pid=pid)),
                stdin="{}",
            )
        assert (proc.returncode, proc.stdout) == (0, "")
        (seen,) = stub.requests
        assert (seen.method, seen.path) == ("POST", "/gateway/sessions")
        assert seen.headers["authorization"] == f"Bearer {REGISTRATION_AUTHORITY}"
        assert json.loads(seen.body) == {"pid": found[0][0], "start_time": found[0][1]}
