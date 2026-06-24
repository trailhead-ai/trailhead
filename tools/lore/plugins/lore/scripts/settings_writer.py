"""Idempotent settings.json helper for the lore plugin (Slice 2, S5).

Mirrors the pattern from camp's ``tools/camp/plugins/camp/scripts/hooks_writer.py``:
stdlib ``json.load/dump`` only (no f-strings, no hand-rolled serializer), preserves
unrelated keys, atomic write via tempfile + os.replace.

No cross-plugin import: this module is lore-only.

Public API
----------
upsert_hook(settings_path, event, command, *, matcher=None)
    Ensure *command* appears exactly once under hooks[event]. Appends when absent;
    leaves existing entry untouched when present. Idempotent.

remove_hook(settings_path, event, command)
    Remove any hook entry whose command matches *command* under hooks[event].
    If no such entry exists the call is a no-op (no write). Idempotent.

upsert_permission_deny(settings_path, rule)
    Ensure *rule* appears exactly once in ``permissions.deny``. Defense-in-depth
    only (Slice 3): a coarse static prefix deny backing the runtime PreToolUse
    guard. Idempotent; preserves unrelated permission rules and keys.

set_env_var(settings_path, name, value)
    Set ``env[name] = value`` in the settings file. Used to give the vault-guard
    hook its ``LORE_VAULT_GUARD_ROOT``. Idempotent (no write when unchanged);
    preserves unrelated env keys.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load(settings_path: Path) -> dict:
    """Load settings.json, returning {} when ABSENT.

    A present-but-unparseable settings file raises ``ValueError`` rather than
    returning {} — silently treating a corrupt file as empty would let a
    subsequent ``_save`` clobber the user's entire settings.json (data loss).
    Axiom 6: never corrupt the live install. Callers surface this as a clean
    named error.
    """
    if not settings_path.is_file():
        return {}
    try:
        return json.loads(settings_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"could not read existing settings at {settings_path}: {exc}") from exc


def _save(settings_path: Path, data: dict) -> None:
    """Atomically write *data* to *settings_path* as indented JSON."""
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


def _has_command(hook_list: list, command: str) -> bool:
    """Return True if *command* already appears in any hook entry in *hook_list*."""
    for entry in hook_list:
        for h in entry.get("hooks", []):
            if h.get("command") == command:
                return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def upsert_hook(
    settings_path: Path,
    event: str,
    command: str,
    *,
    matcher: str | None = None,
) -> None:
    """Ensure *command* appears exactly once under hooks[event].

    If an entry with this exact command already exists, leave it untouched.
    Otherwise append a new entry:
        {"hooks": [{"type": "command", "command": <command>}]}
    When *matcher* is given it is set on the new entry; idempotency keys on the
    command string regardless of matcher.

    Preserves all unrelated keys and events. Writes atomically.

    Args:
        settings_path: Path to the settings.json (or settings.local.json) file.
        event:         Hook event name (e.g. ``"PreToolUse"``).
        command:       The shell command string to register.
        matcher:       Optional tool-name pattern (e.g. ``"Edit|Write"``).
    """
    data = _load(settings_path)
    hooks = data.setdefault("hooks", {})
    hook_list = hooks.setdefault(event, [])

    if _has_command(hook_list, command):
        return

    entry: dict = {"hooks": [{"type": "command", "command": command}]}
    if matcher is not None:
        entry["matcher"] = matcher
    hook_list.append(entry)
    _save(settings_path, data)


def remove_hook(settings_path: Path, event: str, command: str) -> None:
    """Remove any hook entry whose command matches *command* under hooks[event].

    If the target command is not present, this is a no-op (no file write).
    Preserves all unrelated keys and events. When the event's hook list becomes
    empty after removal, the event key is removed from ``hooks``. If ``hooks``
    itself becomes empty, it is retained (preserves the top-level key).

    Writes atomically.

    Args:
        settings_path: Path to the settings.json (or settings.local.json) file.
        event:         Hook event name (e.g. ``"SessionStart"``).
        command:       The shell command string to remove.
    """
    data = _load(settings_path)
    hooks = data.get("hooks", {})
    hook_list = hooks.get(event, [])

    new_list = [
        entry
        for entry in hook_list
        if not any(h.get("command") == command for h in entry.get("hooks", []))
    ]

    if len(new_list) == len(hook_list):
        return  # Nothing changed — skip write

    if new_list:
        hooks[event] = new_list
    else:
        hooks.pop(event, None)

    _save(settings_path, data)


def upsert_permission_deny(settings_path: Path, rule: str) -> None:
    """Ensure *rule* appears exactly once in ``permissions.deny``.

    Defense-in-depth only (Slice 3, S5): the static ``permissions.deny`` prefix
    cannot cover an arbitrary symlink's real target, so it is breadth-only — the
    runtime PreToolUse vault-guard hook is the mandatory primary mechanism. This
    coarse rule (``Write(//abs/.../vaults/**)`` — note the ``//`` double-slash for
    absolute paths) adds belt-and-braces breadth on top of the hook.

    If the rule is already present, leave it untouched. Preserves all unrelated
    keys and permission rules. Writes atomically.

    Args:
        settings_path: Path to the settings.json (or settings.local.json) file.
        rule:          The permission rule string (e.g. ``"Write(//x/vaults/**)"``).
    """
    data = _load(settings_path)
    permissions = data.setdefault("permissions", {})
    deny = permissions.setdefault("deny", [])

    if rule in deny:
        return

    deny.append(rule)
    _save(settings_path, data)


def set_env_var(settings_path: Path, name: str, value: str) -> None:
    """Set ``env[name] = value`` in the settings file.

    No-op (no file write) when the value is already set. Preserves all unrelated
    env keys and top-level keys. Writes atomically.

    Args:
        settings_path: Path to the settings.json (or settings.local.json) file.
        name:          The environment variable name.
        value:         The environment variable value.
    """
    data = _load(settings_path)
    env = data.setdefault("env", {})

    if env.get(name) == value:
        return

    env[name] = value
    _save(settings_path, data)
