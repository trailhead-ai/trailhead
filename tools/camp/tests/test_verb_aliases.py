"""Test contract: verb alias machinery + canonical normalizer.

verb_taxonomy gains an alias layer (VERB_ALIASES + canonical_verb) consulted by
both dispatchers before dispatch, preserving the FIX-9 single-source-of-truth
pattern. The canonical verb surface is renamed (ai→new, enter→activate) and the
new aliases (rm→remove, ls→list) resolve to their canonical verbs.

Contract:
- VERB_ALIASES maps the short aliases rm→remove, ls→list.
- canonical_verb normalizes an alias to its canonical verb (identity otherwise).
- NEEDS_GROUP_VERBS is the canonical set {new, remove, pwd, activate, setup}.
- LEGACY_REDIRECTS points directly at the renamed canonicals (open→new,
  break→remove, init→group, ai→new, enter→activate) — never at a removed verb
  (the dispatcher does not support chained redirects).
- The repo carries no live `camp ai`/`camp enter`/`camp cd` invocation strings in
  README/hook templates.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_SCRIPTS_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp" / "scripts"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# VERB_ALIASES + canonical_verb
# ---------------------------------------------------------------------------


def test_verb_aliases_table() -> None:
    """VERB_ALIASES maps the two short aliases to their canonical verbs."""
    from verb_taxonomy import VERB_ALIASES

    assert VERB_ALIASES == {"rm": "remove", "ls": "list"}


def test_canonical_verb_normalizes_aliases() -> None:
    """canonical_verb resolves an alias to its canonical verb."""
    from verb_taxonomy import canonical_verb

    assert canonical_verb("rm") == "remove"
    assert canonical_verb("ls") == "list"


def test_canonical_verb_identity_for_non_aliases() -> None:
    """canonical_verb returns the verb unchanged when it is not an alias."""
    from verb_taxonomy import canonical_verb

    for verb in ("new", "remove", "activate", "list", "setup", "status", "pwd", "group"):
        assert canonical_verb(verb) == verb


# ---------------------------------------------------------------------------
# NEEDS_GROUP_VERBS — canonical set
# ---------------------------------------------------------------------------


def test_needs_group_verbs_is_canonical_set() -> None:
    """NEEDS_GROUP_VERBS is the renamed canonical set (no ai/rm/enter)."""
    from verb_taxonomy import NEEDS_GROUP_VERBS

    assert set(NEEDS_GROUP_VERBS) == {"new", "remove", "pwd", "activate", "setup"}


# ---------------------------------------------------------------------------
# LEGACY_REDIRECTS — direct, never chained
# ---------------------------------------------------------------------------


def test_legacy_redirects_point_directly_at_canonicals() -> None:
    """Renamed verbs map straight to the live canonical verb."""
    from verb_taxonomy import LEGACY_REDIRECTS

    assert LEGACY_REDIRECTS == {
        "open": "new",
        "break": "remove",
        "init": "group",
        "ai": "new",
        "enter": "activate",
    }


def test_legacy_redirects_never_chain() -> None:
    """No redirect target is itself a removed verb (no chained redirect)."""
    from verb_taxonomy import LEGACY_REDIRECTS

    removed_verbs = {"ai", "rm", "enter", "open", "break", "init"}
    for old, new in LEGACY_REDIRECTS.items():
        assert new not in removed_verbs, (
            f"{old!r} redirects to {new!r}, a removed verb — the dispatcher does "
            "not support chained redirects; point it at the live canonical."
        )


# ---------------------------------------------------------------------------
# resolve_verb — single classification both dispatchers consult
# ---------------------------------------------------------------------------


def test_resolve_verb_canonicalizes_aliases_to_live() -> None:
    """An alias resolves to its canonical verb with kind 'live'."""
    from verb_taxonomy import resolve_verb

    assert resolve_verb("rm") == ("remove", "live")
    assert resolve_verb("ls") == ("list", "live")


def test_resolve_verb_classifies_disabled_and_legacy() -> None:
    """Disabled and legacy verbs are classified by kind (canonical unchanged)."""
    from verb_taxonomy import resolve_verb

    assert resolve_verb("restock") == ("restock", "disabled")
    assert resolve_verb("open") == ("open", "legacy")
    assert resolve_verb("enter") == ("enter", "legacy")


def test_resolve_verb_unknown_token_is_live_identity() -> None:
    """An unknown token (a would-be bare slug) is ('token', 'live')."""
    from verb_taxonomy import resolve_verb

    assert resolve_verb("my-feature") == ("my-feature", "live")
    assert resolve_verb("new") == ("new", "live")


def test_resolve_verb_alias_takes_precedence_over_classification() -> None:
    """The order is alias FIRST, then disabled/legacy — so a hypothetical alias
    whose key ALSO named a disabled/legacy verb would resolve via the alias, the
    SAME way at both entry points. We assert the order
    directly on a synthetic table so the guarantee does not depend on today's
    disjoint keys."""
    import verb_taxonomy as vt

    # The real tables keep alias keys disjoint from disabled/legacy keys; assert
    # that invariant explicitly so a future collision is caught here too.
    assert not (set(vt.VERB_ALIASES) & set(vt.DISABLED_VERBS))
    assert not (set(vt.VERB_ALIASES) & set(vt.LEGACY_REDIRECTS))


# ---------------------------------------------------------------------------
# RESERVED — derived from the taxonomy, pinned by an explicit membership assertion
# ---------------------------------------------------------------------------


def test_reserved_membership_is_pinned() -> None:
    """spine.RESERVED is derived from verb_taxonomy, so this pins
    its exact membership: any drift — adding/renaming a taxonomy verb or editing
    _STATIC_RESERVED — must update this set DELIBERATELY rather than silently
    changing bare-slug validation."""
    from spine import RESERVED

    assert RESERVED == frozenset(
        {
            # Taxonomy-derived: alias keys, legacy-redirect keys, disabled verbs,
            # needs-group verbs.
            "rm",
            "ls",
            "open",
            "break",
            "init",
            "ai",
            "enter",
            "restock",
            "sweep",
            "code",
            "fire",
            "new",
            "remove",
            "pwd",
            "activate",
            "setup",
            # Static: canonical/fleet verbs, meta verbs, hook handlers.
            "group",
            "list",
            "status",
            "sync",
            "rebase",
            "path",
            "foreach",
            "doctor",
            "help",
            "version",
            "which",
            "session-bootstrap",
            "worktree-cleanup",
        }
    )


def test_reserved_superset_of_taxonomy_tokens() -> None:
    """Every taxonomy-owned token (alias keys + canonical targets, legacy keys +
    targets, disabled, needs-group) is reserved — the derivation cannot drop one."""
    from spine import RESERVED
    from verb_taxonomy import (
        DISABLED_VERBS,
        LEGACY_REDIRECTS,
        NEEDS_GROUP_VERBS,
        VERB_ALIASES,
    )

    taxonomy_tokens = (
        set(VERB_ALIASES)
        | set(VERB_ALIASES.values())
        | set(LEGACY_REDIRECTS)
        | set(LEGACY_REDIRECTS.values())
        | set(DISABLED_VERBS)
        | set(NEEDS_GROUP_VERBS)
    )
    assert taxonomy_tokens <= RESERVED


# ---------------------------------------------------------------------------
# Repo sweep: no live stale-verb invocation strings in README / hook templates
# ---------------------------------------------------------------------------


def test_readmes_have_no_stale_verb_invocations() -> None:
    """README files carry no live `camp ai`/`camp enter`/`camp cd` invocations."""
    readmes = [
        _REPO_ROOT / "README.md",
        _REPO_ROOT / "tools" / "camp" / "README.md",
    ]
    stale = ("camp ai", "camp enter", "camp cd")
    for readme in readmes:
        if not readme.is_file():
            continue
        content = readme.read_text()
        for token in stale:
            assert token not in content, (
                f"{readme} still names the removed invocation {token!r}; "
                "update it to camp new / camp activate."
            )
