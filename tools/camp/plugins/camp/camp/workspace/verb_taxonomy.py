"""Single source of truth for camp's verb dispatch taxonomy.

Both entry points — cli/camp (`_dispatch_group_command`) and spine.main — consult
these tables instead of inlining the verb literals, so adding / renaming / disabling
a verb is a one-place edit (no drift across the two dispatchers, RESERVED, and the
group-aware router).

Tables:
  VERB_ALIASES     — short alias → canonical verb (rm→remove, ls→list). Consulted
                     by both dispatchers via canonical_verb() before dispatch, so the
                     rest of the dispatch machinery only ever sees canonical names.
  DISABLED_VERBS   — verbs hidden from help that print the standard disabled
                     message and exit non-zero. Dispatch is intercepted before
                     any command body runs, so these verbs have no handler in
                     spine — this set is the sole thing that governs them.
  LEGACY_REDIRECTS  — renamed verbs → their live canonical replacement (old → new).
                     All targets must be live canonical verbs; the dispatcher does
                     not support chained redirects, so no target may itself be a
                     removed verb.
  NEEDS_GROUP_VERBS — verbs whose real behavior lives on the group-aware path in
                      cli/camp; reaching them via spine.main means no group
                      resolved, so spine emits a "configure / pass --group" error.
"""

from __future__ import annotations

# Verbs disabled while the worktree flow stabilizes.
DISABLED_VERBS = frozenset({"restock", "sweep", "code", "fire"})

# Short aliases → their canonical verb. Consulted by both dispatchers via
# canonical_verb() before dispatch; the rest of the dispatch machinery only
# ever sees the canonical name.
VERB_ALIASES: dict[str, str] = {
    "rm": "remove",
    "ls": "list",
}


def canonical_verb(verb: str) -> str:
    """Return the canonical verb for an alias, or the verb unchanged."""
    return VERB_ALIASES.get(verb, verb)


def resolve_verb(raw: str) -> tuple[str, str]:
    """Resolve a raw verb token to (canonical, kind) in ONE defined order.

    The single classification BOTH dispatchers consult — cli/camp's group-aware
    router and spine.main's fallback — so a token resolves the SAME way regardless
    of entry point. The order is fixed: alias-canonicalize FIRST, then classify the
    canonical verb. `kind` is one of:
      "disabled" — canonical ∈ DISABLED_VERBS  (emit the disabled message).
      "legacy"   — canonical ∈ LEGACY_REDIRECTS (redirect to its target).
      "live"     — anything else (a real verb, or an unknown bare-slug token).

    Centralizing the order here keeps both dispatchers consistent: they historically applied
    the alias table at DIFFERENT positions (spine before the disabled/legacy checks,
    cli/camp after), so a future alias whose key collides with a disabled/legacy verb
    would resolve oppositely in the two entrypoints. Inert today — VERB_ALIASES keys
    are disjoint from DISABLED_VERBS and LEGACY_REDIRECTS — but now defined once.
    """
    canonical = VERB_ALIASES.get(raw, raw)
    if canonical in DISABLED_VERBS:
        return canonical, "disabled"
    if canonical in LEGACY_REDIRECTS:
        return canonical, "legacy"
    return canonical, "live"


# Renamed verbs: old verb → live canonical replacement.
# All targets are live canonical verbs; the dispatcher does not support chained
# redirects, so no target may be a removed verb (e.g. ai/rm/enter are removed).
LEGACY_REDIRECTS: dict[str, str] = {
    "open": "new",
    "break": "remove",
    "init": "group",
    "ai": "new",
    "enter": "activate",
}

# Verbs whose real implementation requires a resolved group (the group-aware path
# in cli/camp). When spine.main is reached for one of these, no group resolved —
# emit the per-verb "needs a group" error. Two message shapes, by historical
# wording: "new"/"setup" point the user at configuring a group; the rest emit the
# standard "pass --group" error.
NEEDS_GROUP_VERBS = frozenset({"new", "remove", "pwd", "activate", "setup", "bookmark"})

_NEEDS_GROUP_CONFIGURE = frozenset({"new", "setup"})


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


def bare_slug_message(token: str) -> str:
    """Return the standard 'bare slug dispatch removed' error for a stray token.

    Bare-slug dispatch (`camp foo` meaning the slug `foo`) is gone; any
    non-RESERVED, non-verb token errors with this message. Both dispatchers
    (cli/camp's group-aware router and spine.main's fallback) emit it, so it lives
    here as the single source of truth rather than being duplicated.
    """
    return (
        f"camp: bare slug dispatch is no longer supported.\n"
        f"  Use 'camp new {token}' to create or enter a workspace."
    )
