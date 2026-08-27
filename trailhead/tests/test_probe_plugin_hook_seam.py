"""ASSUMPTION PROBE — ephemeral, delete after use.

Resolves the unknown gating `task/trailhead-self-update-on-session-start`:
does a composed plugin's `hooks_json` SessionStart hook actually fire in a real
Claude Code session, does `${CLAUDE_PLUGIN_ROOT}` resolve to the COMPOSED
destination (not the source checkout), and does its `additionalContext`
envelope reach the agent?

This drives the real `trailhead.capabilities.load_manifest` +
`trailhead.compose.compose_plan`/`apply_plan` seam end-to-end — a "source"
tool package is composed into a "dest" plugin dir distinct from the source, so
a `${CLAUDE_PLUGIN_ROOT}` that names the source instead of the dest is
detectable. The composed tree is then registered with the REAL `claude` CLI
using `--scope local`, run with `cwd` pinned at a throwaway project directory
(never the live install, per Axiom 6 / this repo's CLAUDE.md) — this writes
only into `<throwaway-project>/.claude/settings.local.json`, never into the
operator's real `~/.claude`. `CLAUDE_CONFIG_DIR` is deliberately left
untouched: overriding it breaks `claude -p` login (measured directly — even
with the real `HOME`, `CLAUDE_CONFIG_DIR=<other dir>` yields "Not logged in ·
Please run /login" with no bearing on the plugin-composition seam under test),
so project/local scope is the sandboxing mechanism here, not a redirected
config dir. A headless `claude -p`, run with `cwd` inside that throwaway
project dir, is the observation channel for whether the hook's
`additionalContext` reaches the agent.

Skips outright if the `claude` CLI is not on PATH (mirrors
`test_claude_plugin_cli_contract.py`'s guard).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from trailhead.capabilities import load_manifest
from trailhead.compose import apply_plan, compose_plan

pytestmark = pytest.mark.skipif(
    shutil.which("claude") is None, reason="the claude CLI is not installed here"
)


def _build_source_tool(root: Path, marker: str) -> Path:
    """Build a throwaway tool package (capabilities.toml + plugins/probe/...)
    at `root`, declaring `hooks_json`. Returns the manifest path.
    """
    tool_root = root / "source-tool"
    plugin_root = tool_root / "plugins" / "probe"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "probe", "description": "probe fixture", "version": "0.0.1"})
    )

    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()

    # The emitter: prints the SessionStart additionalContext envelope carrying
    # the marker plus whatever it resolves as its own plugin root (both via the
    # CLAUDE_PLUGIN_ROOT env var AND its own on-disk location), so a mismatch
    # between "env says X" and "I am actually running from Y" is visible too.
    emit_py = hooks_dir / "emit.py"
    emit_py.write_text(
        "import json, os, sys\n"
        "plugin_root_env = os.environ.get('CLAUDE_PLUGIN_ROOT', '<unset>')\n"
        "self_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
        f"marker = {marker!r}\n"
        "ctx = (\n"
        f"    f'{{marker}} '\n"
        "    f'CLAUDE_PLUGIN_ROOT_ENV={plugin_root_env} '\n"
        "    f'SELF_DIR={self_dir}'\n"
        ")\n"
        "print(json.dumps({\n"
        "    'hookSpecificOutput': {\n"
        "        'hookEventName': 'SessionStart',\n"
        "        'additionalContext': ctx,\n"
        "    }\n"
        "}))\n"
        "sys.exit(0)\n"
    )
    emit_py.chmod(0o755)

    hooks_json = hooks_dir / "hooks.json"
    python_exe = sys.executable
    hooks_json.write_text(
        json.dumps(
            {
                "description": "probe SessionStart hook",
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": python_exe,
                                    "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/emit.py"],
                                    "timeout": 30,
                                }
                            ],
                        }
                    ]
                },
            }
        )
    )

    manifest_path = tool_root / "capabilities.toml"
    manifest_path.write_text(
        "[tool]\n"
        'name = "probe"\n'
        'hooks_json = "hooks/hooks.json"\n'
    )
    return manifest_path


def _run(
    args: list[str], cwd: Path, timeout: int, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=cwd, env=env or os.environ, capture_output=True, text=True, timeout=timeout
    )


def test_composed_plugin_session_start_hook_reaches_the_agent(tmp_path):
    marker = f"PROBE_MARKER_{uuid.uuid4().hex}"

    # ------------------------------------------------------------------
    # 1. Build a "source checkout" tool package and compose it into a
    #    DIFFERENT "composed" destination, exactly as trailhead's real
    #    install path does (capabilities.load_manifest -> compose_plan ->
    #    apply_plan). Source and dest are deliberately distinct paths so a
    #    ${CLAUDE_PLUGIN_ROOT} that names the source instead of the dest is
    #    detectable.
    # ------------------------------------------------------------------
    manifest_path = _build_source_tool(tmp_path, marker)
    manifest = load_manifest(manifest_path)
    assert manifest.hooks_json == "hooks/hooks.json"

    source_plugin_root = manifest.plugin_root.resolve()

    composed_root = tmp_path / "composed"
    dest_plugin_dir = composed_root / "plugins" / "probe"
    plan = compose_plan(manifest, subagents=None, skills=None, dest=dest_plugin_dir)
    apply_plan(plan)

    # Mechanical check (answers (b) partially, without running a session at all):
    # the composed dest actually has its own copy of hooks/emit.py, distinct
    # from the source file.
    composed_emit = dest_plugin_dir / "hooks" / "emit.py"
    assert composed_emit.is_file(), "composer did not ship the hooks_json-containing dir"
    assert composed_emit.resolve() != (source_plugin_root / "hooks" / "emit.py").resolve()

    # ------------------------------------------------------------------
    # 2. Register the COMPOSED tree as a marketplace/plugin scoped to a
    #    throwaway project directory (--scope local writes only into
    #    <project>/.claude/settings.local.json; the operator's real
    #    ~/.claude and its marketplace registrations are never touched).
    # ------------------------------------------------------------------
    (composed_root / ".claude-plugin").mkdir(parents=True)
    (composed_root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "trailhead-probe",
                "owner": {"name": "trailhead-probe"},
                "description": "Probe marketplace.",
                "plugins": [
                    {"name": "probe", "source": "./plugins/probe", "description": "probe fixture"}
                ],
            }
        )
    )

    project_dir = tmp_path / "throwaway-project"
    project_dir.mkdir()

    added = _run(
        ["claude", "plugin", "marketplace", "add", "--scope", "local", str(composed_root)],
        project_dir,
        60,
    )
    assert added.returncode == 0, f"marketplace add failed: {added.stderr}"

    installed = _run(
        ["claude", "plugin", "install", "probe@trailhead-probe", "--scope", "local", "-y"],
        project_dir,
        60,
    )
    assert installed.returncode == 0, f"plugin install failed: {installed.stderr}"

    settings_local = project_dir / ".claude" / "settings.local.json"
    assert settings_local.is_file(), (
        "expected --scope local to write .claude/settings.local.json inside the "
        "throwaway project dir, never the live ~/.claude"
    )

    # `claude plugin install` auto-enables; an explicit enable call on an
    # already-enabled plugin exits 1, so don't call it.

    # ------------------------------------------------------------------
    # 3. Headless session: does the SessionStart hook's additionalContext
    #    envelope actually reach the agent?
    # ------------------------------------------------------------------
    prompt = (
        "A SessionStart hook may have placed a block of additional context into "
        "your system context, starting with a token 'PROBE_MARKER_...' and "
        "continuing on the SAME line with 'CLAUDE_PLUGIN_ROOT_ENV=' and "
        "'SELF_DIR=' fields. If you see it, reply with ONLY that entire line, "
        "copied character-for-character including the CLAUDE_PLUGIN_ROOT_ENV and "
        "SELF_DIR fields — do not summarize, do not omit any part of it. If you "
        "do not see any such line, reply with exactly: NO_MARKER_SEEN"
    )
    session = _run(
        ["claude", "-p", prompt, "--model", "haiku"],
        project_dir,
        90,
    )

    assert session.returncode == 0, (
        f"headless session failed: stdout={session.stdout!r} stderr={session.stderr!r}"
    )

    output = session.stdout
    assert marker in output, (
        "(a)/(c) INVALIDATED: plugin-declared SessionStart hook's additionalContext "
        f"never reached the agent. Full session stdout:\n{output}\nstderr:\n{session.stderr}"
    )

    # (b): the plugin root the hook observed, both via env var and via its own
    # on-disk location, must be the COMPOSED dest — never the source checkout.
    assert str(dest_plugin_dir.resolve()) in output, (
        "(b) INVALIDATED: hook's observed CLAUDE_PLUGIN_ROOT does not name the "
        f"composed dest. Full session stdout:\n{output}"
    )
    assert str(source_plugin_root) not in output, (
        "(b) INVALIDATED: hook's observed plugin root leaked the SOURCE checkout "
        f"path instead of the composed dest. Full session stdout:\n{output}"
    )
