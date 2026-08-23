"""Write/update the SessionStart hook entry in .claude/settings.json.

All writes use json.load/json.dump (never f-strings) so:
  - Paths containing spaces or quotes round-trip correctly.
  - Existing unrelated keys are preserved.
  - Idempotent: re-running adds NO duplicate entries (match on command string).

Hook entries written:
  SessionStart  → "${CAMP_BIN:-<abs_camp_bin>} session-bootstrap"
  env.CAMP_BIN   → <abs_camp_bin>  (absolute default; ${CAMP_BIN:-…} lets user override)

WorktreeRemove is not wired here: camp owns teardown via `camp rm` under the
unified-workspace layout. The `worktree-cleanup` handler stays invocable but is
not auto-wired into member settings.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_settings(settings_path: Path) -> dict:
    """Load existing settings.json, or return an empty dict if absent/unreadable."""
    if not settings_path.is_file():
        return {}
    try:
        return json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_settings(settings_path: Path, data: dict) -> None:
    """Atomically write data to settings_path as JSON."""
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(settings_path.parent), prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, str(settings_path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _session_start_command(camp_bin: str) -> str:
    """Return the SessionStart hook command string."""
    return f"${{CAMP_BIN:-{camp_bin}}} session-bootstrap"


def _workspace_session_start_command(camp_bin: str) -> str:
    """Return the workspace SessionStart hook command string."""
    return f"${{CAMP_BIN:-{camp_bin}}} setup --status"


def _workspace_inject_drain_command(camp_bin: str) -> str:
    """Return the workspace PostToolUse inject-drain hook command string."""
    return f"${{CAMP_BIN:-{camp_bin}}} inject --drain"


def _has_command(hook_list: list, command: str) -> bool:
    """Return True if `command` already appears in any hook entry in hook_list."""
    for entry in hook_list:
        for h in entry.get("hooks", []):
            if h.get("command") == command:
                return True
    return False


def _upsert_hook(data: dict, event: str, command: str, *, matcher: str | None = None) -> None:
    """Ensure `command` appears exactly once under hooks[event].

    If an entry with this exact command already exists, leave it untouched.
    Otherwise, append a new entry { "hooks": [ { "type": "command", "command": <cmd> } ] }.
    When `matcher` is given (e.g. PostToolUse → "Bash"), it is set on the new
    entry; idempotency keys on the command string regardless of matcher.
    """
    hooks = data.setdefault("hooks", {})
    hook_list = hooks.setdefault(event, [])

    if _has_command(hook_list, command):
        return  # Already present — idempotent

    entry: dict = {"hooks": [{"type": "command", "command": command}]}
    if matcher is not None:
        entry["matcher"] = matcher
    hook_list.append(entry)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def write_workspace_hooks(workspace_dir: Path, camp_bin: str) -> None:
    """Write/update the workspace-dir SessionStart hook in <workspace_dir>/.claude/settings.json.

    The SessionStart hook fires `camp setup --status` so every (re)entry to the
    workspace dir makes the agent aware of in-flight/failed member provisioning.

    Idempotent: re-running adds NO duplicate entries.
    Existing unrelated keys are preserved.

    Args:
        workspace_dir: Absolute path to the workspace root directory.
        camp_bin:      Absolute path to the camp binary.
    """
    settings_path = workspace_dir / ".claude" / "settings.json"
    data = _load_settings(settings_path)

    ss_cmd = _workspace_session_start_command(camp_bin)
    _upsert_hook(data, "SessionStart", ss_cmd)

    _save_settings(settings_path, data)


def write_workspace_inject_hook(workspace_dir: Path, camp_bin: str) -> None:
    """Write/update the workspace-dir PostToolUse → `camp inject --drain` hook.

    Installed only when the resolved inject strategy is "claude-hook": a
    PostToolUse hook with NO matcher drains the workspace inject queue (member
    docs from `camp activate`, and settlement/failure notices from the
    activate-phase provisioner) into the session via additionalContext on the
    NEXT tool call, whatever that tool is. Omitting the matcher key is the
    documented, canonical way to fire a hook on every occurrence of its event
    (equivalent to `"*"`/`""`, per Claude Code's hook matcher semantics) — a
    Bash-only matcher would miss a session that is following the capability
    report's own advice to prefer Grep/Glob over Bash while work is
    outstanding, which is exactly the session a settlement/failure notice
    needs to reach. The drain is cheap on an empty queue (exits 0, no output),
    so firing on every tool call costs a process spawn against a
    missing-directory check.

    Idempotent: re-running adds NO duplicate entries. Existing unrelated keys
    (including the SessionStart hook) are preserved.

    Args:
        workspace_dir: Absolute path to the workspace root directory.
        camp_bin:      Absolute path to the camp binary.
    """
    settings_path = workspace_dir / ".claude" / "settings.json"
    data = _load_settings(settings_path)

    drain_cmd = _workspace_inject_drain_command(camp_bin)
    _upsert_hook(data, "PostToolUse", drain_cmd)

    _save_settings(settings_path, data)


def has_inject_drain_hook(workspace_dir: Path) -> bool:
    """Return True if a PostToolUse `inject --drain` hook is installed.

    Reads <workspace_dir>/.claude/settings.json (via the shared loader, so an
    absent/unreadable file → False) and looks for any PostToolUse hook whose
    command contains "inject --drain". This is the marker that the claude-hook
    drain channel is actually wired — without it, an enqueued doc is never drained.
    """
    settings_path = workspace_dir / ".claude" / "settings.json"
    data = _load_settings(settings_path)
    post_tool_use = data.get("hooks", {}).get("PostToolUse", [])
    for entry in post_tool_use:
        for h in entry.get("hooks", []):
            if "inject --drain" in (h.get("command") or ""):
                return True
    return False


def write_hooks_for_member(repo_root: Path, camp_bin: str) -> None:
    """Write/update camp hook entries in <repo_root>/.claude/settings.json.

    Idempotent: re-running produces NO duplicate entries.
    Existing unrelated keys are preserved.

    Args:
        repo_root: Absolute path to the member's repo root.
        camp_bin:  Absolute path to the camp binary (written as CAMP_BIN default).
    """
    settings_path = repo_root / ".claude" / "settings.json"
    data = _load_settings(settings_path)

    ss_cmd = _session_start_command(camp_bin)
    _upsert_hook(data, "SessionStart", ss_cmd)

    # Write env.CAMP_BIN (absolute default path; ${CAMP_BIN:-…} lets user override)
    env_block = data.setdefault("env", {})
    env_block["CAMP_BIN"] = camp_bin

    _save_settings(settings_path, data)
