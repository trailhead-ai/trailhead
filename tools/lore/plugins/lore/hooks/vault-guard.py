#!/usr/bin/env python3
"""Vault write-protection PreToolUse guard.

Claude Code invokes this hook before every file-mutating tool call (matcher
``Edit|Write|MultiEdit|NotebookEdit``). It reads the PreToolUse JSON payload from
stdin, resolves the write target and the guarded vault root(s) to their REAL paths
via ``os.path.realpath`` (symlink-transparent), and **denies** the tool call when
the real target is at or under any real vault root.

The write target is read from ``tool_input.file_path`` (Edit/Write/MultiEdit) OR
``tool_input.notebook_path`` (NotebookEdit), whichever is present — the guard
treats both keys as the path source so no covered tool can slip a path past it.

Deny mechanism: **exit code 2**. Claude Code treats a
nonzero exit of exactly 2 as a hard block and shows this hook's stderr to the
model as the reason; stdout is ignored. Exit 0 = allow / defer.

Execution-time canonicalization: the real paths are
resolved on EVERY invocation, never snapshotted at install time. So if the
``default`` vault is a symlink that the user retargets after ``lore init``, the
guard always covers the symlink's *current* real target — the deny set is never
stale.

Case-insensitive filesystems (macOS): ``os.path.realpath`` preserves the input
case, so on a case-insensitive FS an alternate-case spelling of a guarded path
would name the same file yet evade a case-sensitive prefix check. The guard
casefolds BOTH the resolved target and the resolved root before comparing —
harmless on case-sensitive Linux (an over-deny on a case-variant is the safe
direction for a guard).

Environment:
  LORE_VAULT_GUARD_ROOT
    NEWLINE-separated list of vault roots to guard. The delimiter is ``\n`` (a
    byte that cannot appear in a POSIX path) rather than ``os.pathsep`` (``:``),
    so a vault path containing a literal ':' is not corrupted. ``lore init`` sets
    this in the resolved settings.json ``env`` block, pointing at the absolute
    ``$XDG_STATE_HOME/lore/vaults`` directory (covers ``vaults/**`` canonically)
    plus ``vaults/default`` (so the symlink's real target is resolved). A missing
    or empty value means "nothing to guard" → allow, but a WARNING is emitted to
    stderr so a silently-unguarded vault is an observable signal, not silent.

Accepted out-of-scope (no code fix possible): ``Bash``-mediated writes to the
vault (``> file``, ``tee``, ``sed -i``, ``cp``, ``mv``) are opaque at PreToolUse
time — they carry no ``file_path`` and Bash is not in the matcher — so they are
NOT covered by this runtime hook. They are covered only by the agent-rules
prohibition.

Fail-open posture: a malformed stdin payload or any unexpected error allows the
tool call (exit 0) rather than blocking every covered call in the session. The
guard's job is to protect the vault, not to become a single point of failure that
bricks the editor; the vault subtree is the narrow, well-defined target and a
parse failure means we cannot identify the target anyway.
"""

from __future__ import annotations

import json
import os
import sys


# The root list delimiter. A newline cannot appear in a POSIX path, so it never
# corrupts a vault root whose path contains ':' (which ``os.pathsep`` would).
_ROOT_DELIM = "\n"


def _real(path: str) -> str:
    """Resolve *path* to its real path, casefolded for case-insensitive FS safety."""
    return os.path.realpath(path).casefold()


def _is_under(target: str, root: str) -> bool:
    """True if *target* is *root* itself or lives under it.

    Both arguments are expected to be already ``_real``-resolved (and thus
    casefolded), so the comparison is case-insensitive — denying an alternate-case
    spelling that names the same file on a case-insensitive filesystem.
    """
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
    if isinstance(tool_input, dict):
        # Edit/Write/MultiEdit use file_path; NotebookEdit uses notebook_path.
        file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    else:
        file_path = ""
    if not file_path:
        # A covered tool with a malformed/unexpected payload carrying no path —
        # we cannot identify a target, so fail open (Bash never reaches here: it
        # is not in the matcher).
        return 0

    real_target = _real(file_path)

    roots_env = os.environ.get("LORE_VAULT_GUARD_ROOT", "")
    roots = [r for r in roots_env.split(_ROOT_DELIM) if r]

    if not roots:
        print(
            "lore vault guard: LORE_VAULT_GUARD_ROOT not set — vault is unguarded.",
            file=sys.stderr,
        )
        return 0

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
