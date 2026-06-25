"""Single source of truth for camp's verb dispatch taxonomy (FIX 9).

Both entry points — cli/camp (`_dispatch_group_command`) and spine.main — consult
these tables instead of inlining the verb literals, so adding / renaming / disabling
a verb is a one-place edit (no drift across the two dispatchers, RESERVED, and the
group-aware router).

Tables:
  DISABLED_VERBS   — verbs hidden from help that print the standard disabled
                     message and exit non-zero. The cmd_<verb> BODIES in spine
                     (cmd_code/cmd_sweep/cmd_restock) are intentionally retained;
                     this set only governs the DISPATCH decision.
  LEGACY_REDIRECTS  — renamed verbs → their replacement (old → new).
  NEEDS_GROUP_VERBS — verbs whose real behavior lives on the group-aware path in
                      cli/camp; reaching them via spine.main means no group
                      resolved, so spine emits a "configure / pass --group" error.
"""

from __future__ import annotations

# Verbs disabled while the worktree flow stabilizes.
DISABLED_VERBS = frozenset({"restock", "sweep", "code", "fire"})

# Renamed verbs: old verb → replacement verb.
LEGACY_REDIRECTS: dict[str, str] = {
    "open": "ai",
    "break": "rm",
    "init": "group",
}

# Verbs whose real implementation requires a resolved group (the group-aware path
# in cli/camp). When spine.main is reached for one of these, no group resolved —
# emit the per-verb "needs a group" error. Two message shapes, by historical
# wording: "ai"/"setup" point the user at configuring a group; the rest emit the
# standard "pass --group" error.
NEEDS_GROUP_VERBS = frozenset({"ai", "rm", "pwd", "enter", "setup"})

_NEEDS_GROUP_CONFIGURE = frozenset({"ai", "setup"})


def needs_group_message(verb: str) -> str:
    """Return the exact spine-fallback error message for a NEEDS_GROUP verb."""
    if verb in _NEEDS_GROUP_CONFIGURE:
        return (
            f"camp {verb}: no camp group resolves from this directory.\n"
            "  Run 'camp group <name> --member NAME=PATH ...' to configure one, "
            "or pass --group <name>."
        )
    return (
        f"camp {verb}: no group resolved from cwd — "
        "pass --group <name> or run from inside a group member directory"
    )
