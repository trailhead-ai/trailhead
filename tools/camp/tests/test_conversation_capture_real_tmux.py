"""Real-tmux end-to-end proof that the session-start hook camp's plugin
declares records a conversation against the workspace window it started in.

The plugin is composed the way an install composes it — camp's own
`capabilities.toml` through `trailhead.compose` into a throwaway plugin tree,
which carries no copy of the camp CLI — and the hook command is read from the
composed hooks file and run the way a harness runs a hook: through a shell,
with `${CLAUDE_PLUGIN_ROOT}` naming the composed tree and the session-start
payload on stdin, from inside a pane of a REAL marked workspace session on a
throwaway `-L` tmux socket. The hook reaches the camp CLI the two ways an
operator's machine offers it: the shim `trailhead install` writes
(`trailhead.pathint.create_shims`), or a standalone `camp` on `PATH`. tmux
itself supplies `TMUX_PANE` and the socket to the hook, exactly as it would
under a real harness; nothing about the pane, the session's marks, or the
window record is stood in for.

`_REAL_TMUX` is captured at import time, before conftest's `_sandbox_tmux`
rewrites `PATH`, and a `tmux` wrapper first on `PATH` redirects every call —
this process's own and the hook subprocess's — onto the isolated socket.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

_REAL_TMUX = shutil.which("tmux")

pytestmark = pytest.mark.real_home


def _session_start_command(composed: Path) -> tuple[str, Path]:
    """Compose camp's plugin into *composed* exactly as an install does, and
    answer the SessionStart hook command its hooks file declares and the
    plugin root `${CLAUDE_PLUGIN_ROOT}` names — the composed tree."""
    from trailhead.capabilities import load_manifest
    from trailhead.compose import apply_plan, compose_plan

    manifest = load_manifest(_REPO_ROOT / "tools" / "camp" / "capabilities.toml")
    apply_plan(compose_plan(manifest, {}, {}, composed))
    hooks = json.loads((composed / manifest.hooks_json).read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entry in hooks["hooks"]["SessionStart"]
        for hook in entry["hooks"]
        if hook.get("type") == "command"
    ]
    assert len(commands) == 1, commands
    return commands[0], composed


@pytest.fixture(params=["trailhead-shim", "camp-on-path"])
def camp_session(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real marked workspace session on an isolated server, for a group
    whose harness is the REAL Claude Code one (no `[harness]` block), with
    the camp CLI reachable the way the fixture's param names."""
    from camp.launch.naming import workspace_session_name
    from camp.launch.session import SessionEnvironment
    from camp.launch.tmux import Tmux
    from camp.launch.workspace_session import WorkspaceSessionOutcome, create_workspace_session

    sock = f"camp_capture_e2e_{os.getpid()}_{id(tmp_path)}"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "tmux"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        f"os.execv({_REAL_TMUX!r}, [{_REAL_TMUX!r}, '-L', {sock!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)

    home = tmp_path / "home"
    home.mkdir()
    config_dir = tmp_path / "config"
    (config_dir / "groups").mkdir(parents=True)
    (config_dir / "groups" / "capture.toml").write_text(
        '[group]\nname = "capture"\n\n[[members]]\nname = "m"\nrepo_root = "/tmp/capture-fake"\n',
        encoding="utf-8",
    )
    venv_bin = Path(sys.executable).resolve().parent
    path = [str(bin_dir), str(venv_bin), "/usr/bin", "/bin"]
    env = {
        "HOME": str(home),
        "CAMP_CONFIG_DIR": str(config_dir),
        "CAMP_STATE_DIR": str(tmp_path / "state"),
        "TRAILHEAD_STATE_DIR": str(tmp_path / "trailhead-state"),
        "SHELL": "/bin/sh",
    }
    camp_bin = _PLUGIN_DIR / "bin" / "camp"
    if request.param == "trailhead-shim":
        from trailhead.pathint import create_shims

        create_shims({"camp": camp_bin}, str(_REPO_ROOT), env=env)
    else:
        path.insert(0, str(camp_bin.parent))
    env["PATH"] = os.pathsep.join(path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    from camp.group.manifest import workspace_dir

    ws = workspace_dir("capture", "feat", env=env)
    (ws / "member").mkdir(parents=True)
    result = create_workspace_session(
        "capture", "feat", ws, env=env, tmux=Tmux(), session_env=SessionEnvironment()
    )
    assert result.outcome is WorkspaceSessionOutcome.CREATED, result
    try:
        yield sock, workspace_session_name("capture", "feat"), ws, tmp_path
    finally:
        subprocess.run([_REAL_TMUX, "-L", sock, "kill-server"], capture_output=True, timeout=5)


def _run_hook_in_a_new_window(sock, session, cwd: Path, tmp_path: Path, session_id: str) -> str:
    """Open a window rooted at *cwd* whose pane runs the plugin's declared
    SessionStart command with a Claude Code SessionStart payload on stdin,
    wait for it to finish, and return the window id tmux assigned."""
    command, plugin_root = _session_start_command(tmp_path / "composed" / session_id)
    payload = json.dumps(
        {"session_id": session_id, "hook_event_name": "SessionStart", "source": "startup"}
    )
    done = tmp_path / f"done-{session_id}"
    script = tmp_path / f"hook-{session_id}.sh"
    script.write_text(
        f"printf '%s' {shlex.quote(payload)} | "
        f"CLAUDE_PLUGIN_ROOT={shlex.quote(str(plugin_root))} sh -c {shlex.quote(command)}\n"
        f"touch {shlex.quote(str(done))}\n"
        "sleep 30\n",
        encoding="utf-8",
    )
    created = subprocess.run(
        [_REAL_TMUX, "-L", sock, "new-window", "-P", "-F", "#{window_id}",
         "-t", f"={session}", "-c", str(cwd), f"sh {script}"],
        capture_output=True, text=True, timeout=5,
    )
    assert created.returncode == 0, created.stderr
    deadline = time.monotonic() + 15
    while not done.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert done.exists(), "the hook command never finished"
    return created.stdout.strip()


@pytest.mark.skipif(_REAL_TMUX is None, reason="no tmux binary on PATH (captured at import time)")
def test_the_plugins_session_start_hook_records_the_conversation_against_its_window(camp_session):
    from camp.group.window_record import WindowEntry, read_window_record, window_record_path_for

    sock, session, ws, tmp_path = camp_session

    window_id = _run_hook_in_a_new_window(sock, session, ws / "member", tmp_path, "sess-e2e-1")

    record = read_window_record(window_record_path_for(ws))
    assert record.status == "ok"
    assert record.entries == (
        WindowEntry(
            window_id=window_id,
            name=record.entries[0].name,
            cwd="member",
            conversation_id="sess-e2e-1",
        ),
    )
