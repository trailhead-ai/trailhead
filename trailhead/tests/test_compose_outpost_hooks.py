"""The Outpost plugin's SessionStart hook: composed with the plugin, absent without it,
removed by uninstall, and its script finds and runs the trailhead CLI."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from trailhead.capabilities import load_manifest
from trailhead.compose import apply_plan, compose_plan
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

    def test_falls_back_to_trailhead_on_path(self, tmp_path):
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
        assert record.read_text().split() == ["gateway", "register", "--hook"]
        assert proc.returncode == 0

    def test_without_a_trailhead_cli_it_exits_zero_silently(self, tmp_path):
        proc = subprocess.run(
            [str(self._script(tmp_path))],
            input="{}",
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "TRAILHEAD_STATE_DIR": str(tmp_path / "empty"), "HOME": str(tmp_path)},
        )
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


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
        run_uninstall(env=env, assume_yes=True, runner=lambda args, **kw: None)
        assert not (plugin / "hooks" / "hooks.json").exists()
