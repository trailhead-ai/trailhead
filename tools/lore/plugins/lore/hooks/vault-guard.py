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

  LORE_VAULT_GUARD_EXEMPT
    NEWLINE-separated list of absolute path patterns naming subtrees that are
    NOT record trees and may be written directly. Consulted only AFTER a root
    match: a target that no root covers was already allowed and never reaches
    this list. A target at — or anywhere under — a directory a pattern names is
    allowed; everything else under a guarded root stays denied.

    Patterns are canonicalized exactly like the roots (``realpath`` + casefold,
    on every invocation) and matched SEGMENT-WISE: ``*`` stands for exactly one
    path segment and never spans a separator, so ``<vaults>/*/sites`` covers a
    vault's top-level ``sites`` directory but not a ``sites`` directory nested
    inside a record tree. ``lore init`` writes exactly that pattern, carving the
    per-vault static-site zone out of the deny.

    Fail-closed: a missing or empty value means NO exemption — the guard denies
    the whole vault subtree, which is the pre-exemption behavior. A malformed
    line (blank, or not an absolute path) is skipped individually; it never
    raises and never widens the exemption.

    One case the exemption cannot cover: when ``vaults/default`` is a symlink
    pointing outside the vaults root, the guard still denies writes through it
    (that symlink is wired as a guard root in its own right and is realpath-
    resolved on every call), but a pattern anchored at the vaults root cannot
    match the escaped real path — so a ``sites`` write through such a symlink is
    blocked. That is the fail-closed direction and no command creates such a
    symlink; a vault adopted from outside the vaults root needs its own
    exemption pattern.

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

import fnmatch
import json
import os
import sys


# The root list delimiter. A newline cannot appear in a POSIX path, so it never
# corrupts a vault root whose path contains ':' (which ``os.pathsep`` would).
_ROOT_DELIM = "\n"

# The exemption pattern list uses the same delimiter, for the same reason.
_EXEMPT_DELIM = "\n"


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


def _matches_pattern(target: str, pattern: str) -> bool:
    """True if *target* is the directory *pattern* names, or lives under it.

    Both arguments are expected to be already ``_real``-resolved. Matching is
    segment-wise — the pattern's segments are compared one-for-one against the
    target's leading segments — so a ``*`` stands for exactly one segment and
    can never swallow a separator the way a whole-path ``fnmatch`` would. That
    is what keeps ``<vaults>/*/sites`` from matching ``<vault>/task/x/sites``.

    A target with MORE segments than the pattern matches on its leading
    segments: that is the "at or under" half of the contract.
    """
    pattern_parts = pattern.split(os.sep)
    target_parts = target.split(os.sep)
    if len(target_parts) < len(pattern_parts):
        return False
    return all(
        fnmatch.fnmatchcase(t, p) for t, p in zip(target_parts, pattern_parts)
    )


def _is_exempt(real_target: str) -> bool:
    """True if *real_target* sits in a subtree LORE_VAULT_GUARD_EXEMPT carves out.

    Unset or empty → False (fail closed: no exemption, deny the whole subtree).
    A line that is blank or not an absolute path is skipped rather than treated
    as a match, so one malformed entry cannot widen or disable the guard.
    """
    patterns = os.environ.get("LORE_VAULT_GUARD_EXEMPT", "").split(_EXEMPT_DELIM)

    for raw_pattern in patterns:
        raw_pattern = raw_pattern.strip()
        if not raw_pattern or not os.path.isabs(raw_pattern):
            continue
        try:
            # ``realpath`` canonicalizes the pattern's literal leading segments
            # (resolving any symlink among them) and leaves the glob segments
            # untouched, since they name nothing on disk.
            real_pattern = _real(raw_pattern)
        except (OSError, ValueError):
            continue
        if _matches_pattern(real_target, real_pattern):
            return True

    return False


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
            # Exemptions are consulted only here, inside a root match: a target
            # no root covers was already allowed and never gets this far.
            if _is_exempt(real_target):
                return 0
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
