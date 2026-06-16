"""Write/update the SessionStart hook entry in .claude/settings.json.

All writes use json.load/json.dump (never f-strings) so:
  - Paths containing spaces or quotes round-trip correctly.
  - Existing unrelated keys are preserved.
  - Idempotent: re-running adds NO duplicate entries (match on command string).

Hook entries written:
  SessionStart  → "${CAMP_BIN:-<abs_camp_bin>} session-bootstrap"
  env.CAMP_BIN   → <abs_camp_bin>  (absolute default; ${CAMP_BIN:-…} lets user override)

The WorktreeRemove wiring was dropped in Slice 2: camp owns teardown via
`camp rm`, per the unified-workspace ADR. The `worktree-cleanup` handler is
retained (still invocable) but no longer auto-wired into member settings.
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
        return json.loads(settings_path.read_text())
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


def _has_command(hook_list: list, command: str) -> bool:
    """Return True if `command` already appears in any hook entry in hook_list."""
    for entry in hook_list:
        for h in entry.get("hooks", []):
            if h.get("command") == command:
                return True
    return False


def _upsert_hook(data: dict, event: str, command: str) -> None:
    """Ensure `command` appears exactly once under hooks[event].

    If an entry with this exact command already exists, leave it untouched.
    Otherwise, append a new entry { "hooks": [ { "type": "command", "command": <cmd> } ] }.
    """
    hooks = data.setdefault("hooks", {})
    hook_list = hooks.setdefault(event, [])

    if _has_command(hook_list, command):
        return  # Already present — idempotent

    hook_list.append({
        "hooks": [
            {"type": "command", "command": command},
        ]
    })


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
