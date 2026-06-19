#!/usr/bin/env python3
"""Vault write-protection PreToolUse guard (Slice 3, S5).

Claude Code invokes this hook before every Edit/Write tool call (matcher
``Edit|Write``). It reads the PreToolUse JSON payload from stdin, resolves the
write target and the guarded vault root(s) to their REAL paths via
``os.path.realpath`` (symlink-transparent), and **denies** the tool call when the
real target is at or under any real vault root.

Deny mechanism (KU1, VALIDATED Slice 0): **exit code 2**. Claude Code treats a
nonzero exit of exactly 2 as a hard block and shows this hook's stderr to the
model as the reason; stdout is ignored. Exit 0 = allow / defer.

Execution-time canonicalization (council Reliability): the real paths are
resolved on EVERY invocation, never snapshotted at install time. So if the
``default`` vault is a symlink that the user retargets after ``lore init``, the
guard always covers the symlink's *current* real target — the deny set is never
stale.

Environment:
  LORE_VAULT_GUARD_ROOT
    Colon-separated list of vault roots to guard. ``lore init`` sets this in the
    resolved settings.json ``env`` block, pointing at the absolute
    ``$XDG_STATE_HOME/lore/vaults`` directory (covers ``vaults/**`` canonically)
    plus ``vaults/default`` (so the symlink's real target is resolved). A missing
    or empty value means "nothing to guard" → allow.

Fail-open posture: a malformed stdin payload or any unexpected error allows the
tool call (exit 0) rather than blocking every Edit/Write in the session. The
guard's job is to protect the vault, not to become a single point of failure that
bricks the editor; the vault subtree is the narrow, well-defined target and a
parse failure means we cannot identify the target anyway.
"""
from __future__ import annotations

import json
import os
import sys


def _real(path: str) -> str:
    return os.path.realpath(path)


def _is_under(target: str, root: str) -> bool:
    """True if *target* is *root* itself or lives under it."""
    return target == root or target.startswith(root + os.sep)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, OSError):
        # Cannot parse the payload → cannot identify a target → fail open.
        return 0

    if not isinstance(payload, dict):
        return 0

    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path", "") if isinstance(tool_input, dict) else ""
    if not file_path:
        return 0  # No file target (e.g. Bash) — not our concern.

    real_target = _real(file_path)

    roots_env = os.environ.get("LORE_VAULT_GUARD_ROOT", "")
    roots = [r for r in roots_env.split(":") if r]

    for raw_root in roots:
        real_root = _real(raw_root)
        if _is_under(real_target, real_root):
            print(
                "lore vault guard: refusing to write to "
                f"{file_path!r} (resolved: {real_target!r}) — it is inside the "
                f"lore vault at {raw_root!r} (resolved: {real_root!r}). The vault "
                "is CLI-managed; use `lore` commands to write records, never a "
                "direct file edit.",
                file=sys.stderr,
            )
            return 2  # Deny: exit 2 blocks the tool call.

    return 0  # Allow.


if __name__ == "__main__":
    sys.exit(main())
