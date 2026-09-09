"""Test contract: verb alias machinery + canonical normalizer.

verb_taxonomy gains an alias layer (VERB_ALIASES + canonical_verb) consulted by
both dispatchers before dispatch, preserving the FIX-9 single-source-of-truth
pattern. The canonical verb surface is renamed (ai→new, enter→activate) and the
new aliases (rm→remove, ls→list) resolve to their canonical verbs.

Contract:
- VERB_ALIASES maps the short aliases rm→remove, ls→list.
- canonical_verb normalizes an alias to its canonical verb (identity otherwise).
- Every canonical needs-group verb reaches the needs-group path from a cwd where
  no group resolves, and `kill` reaches its own handler there instead — it
  addresses a session by ref and is served from any cwd.
- LEGACY_REDIRECTS points directly at the renamed canonicals (open→new,
  break→remove, init→group, ai→new, enter→activate) — never at a removed verb
  (the dispatcher does not support chained redirects).
- The repo carries no live `camp ai`/`camp enter`/`camp cd` invocation strings in
  README/hook templates.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # trailhead root
_PLUGIN_DIR = _REPO_ROOT / "tools" / "camp" / "plugins" / "camp"
_CLI_CAMP = _PLUGIN_DIR / "cli" / "camp"

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))


def _run(args: list[str], *, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the real camp CLI — the consumer that actually reads these tables."""
    base_env = {**os.environ}
    if env:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, str(_CLI_CAMP), *args],
        capture_output=True,
        text=True,
        env=base_env,
    )


@pytest.fixture()
def groupless_env(tmp_path: Path) -> dict[str, str]:
    """Env where no group resolves, so verb routing is what decides the outcome."""
    (tmp_path / "groups").mkdir(parents=True)
    return {"CAMP_CONFIG_DIR": str(tmp_path), "CAMP_STATE_DIR": str(tmp_path / "state")}


# ---------------------------------------------------------------------------
# VERB_ALIASES + canonical_verb
# ---------------------------------------------------------------------------


def test_canonical_verb_normalizes_aliases() -> None:
    """canonical_verb resolves an alias to its canonical verb."""
    from camp.workspace.verb_taxonomy import canonical_verb

    assert canonical_verb("rm") == "remove"
    assert canonical_verb("ls") == "list"


def test_canonical_verb_identity_for_non_aliases() -> None:
    """canonical_verb returns the verb unchanged when it is not an alias."""
    from camp.workspace.verb_taxonomy import canonical_verb

    for verb in ("new", "remove", "activate", "list", "setup", "status", "pwd", "group"):
        assert canonical_verb(verb) == verb


# ---------------------------------------------------------------------------
# NEEDS_GROUP_VERBS — canonical set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb", ["new", "remove", "pwd", "activate", "setup", "launch", "sessions"]
)
def test_needs_group_verb_asks_for_a_group_rather_than_erroring_as_a_slug(
    verb: str, groupless_env: dict[str, str]
) -> None:
    """Run from a cwd where no group resolves, each canonical verb must reach the
    needs-group path. The bare-slug error means the dispatcher did not recognise
    it as a verb at all — which is the failure NEEDS_GROUP_VERBS exists to
    prevent."""
    combined = _run([verb], env=groupless_env)
    text = (combined.stdout + combined.stderr).lower()
    assert "group" in text, f"{verb!r} did not reach the needs-group path: {text!r}"


def test_kill_reaches_its_handler_from_a_cwd_where_no_group_resolves(
    groupless_env,
) -> None:
    """`camp kill <ref>` names a session, and the session names everything else,
    so a group resolving from cwd is not a precondition. Driven through the CLI:
    the two ways this breaks — a needs-group refusal, or falling through to the
    bare-slug error — are both invisible to a table-membership assertion.
    """
    out = _run(["kill", "no-such-ref"], env=groupless_env)
    combined = out.stdout + out.stderr
    assert "bare slug dispatch is no longer supported" not in combined, combined
    assert "no camp group" not in combined and "no group resolved" not in combined, combined
    assert combined.startswith("camp kill:"), combined


# ---------------------------------------------------------------------------
# LEGACY_REDIRECTS — direct, never chained
# ---------------------------------------------------------------------------


def test_legacy_redirect_keys_classify_as_legacy() -> None:
    """Each retired verb still resolves — as `legacy`, so the dispatcher can
    redirect it rather than falling through to the bare-slug error."""
    from camp.workspace.verb_taxonomy import LEGACY_REDIRECTS, resolve_verb

    assert LEGACY_REDIRECTS, "no legacy redirects declared — nothing under test"
    for old_verb in LEGACY_REDIRECTS:
        assert resolve_verb(old_verb) == (old_verb, "legacy"), (
            f"{old_verb!r} is declared a legacy redirect but the resolver does not "
            "classify it as one"
        )


def test_legacy_redirect_targets_resolve_live() -> None:
    """Every redirect points at a verb the resolver reports as live — which is
    what 'never chained' means operationally: follow the redirect once and you
    land on something that dispatches."""
    from camp.workspace.verb_taxonomy import LEGACY_REDIRECTS, resolve_verb

    for old_verb, target in LEGACY_REDIRECTS.items():
        head = target.split()[0]
        canonical, kind = resolve_verb(head)
        assert kind == "live", (
            f"{old_verb!r} redirects to {head!r}, which resolves {kind!r} — the "
            "dispatcher does not support chained redirects; point it at a live verb"
        )
        assert canonical == head


# ---------------------------------------------------------------------------
# resolve_verb — single classification both dispatchers consult
# ---------------------------------------------------------------------------


def test_resolve_verb_canonicalizes_aliases_to_live() -> None:
    """An alias resolves to its canonical verb with kind 'live'."""
    from camp.workspace.verb_taxonomy import resolve_verb

    assert resolve_verb("rm") == ("remove", "live")
    assert resolve_verb("ls") == ("list", "live")


def test_resolve_verb_classifies_disabled_and_legacy() -> None:
    """Disabled and legacy verbs are classified by kind (canonical unchanged)."""
    from camp.workspace.verb_taxonomy import resolve_verb

    assert resolve_verb("restock") == ("restock", "disabled")
    assert resolve_verb("open") == ("open", "legacy")
    assert resolve_verb("enter") == ("enter", "legacy")


def test_resolve_verb_unknown_token_is_live_identity() -> None:
    """An unknown token (a would-be bare slug) is ('token', 'live')."""
    from camp.workspace.verb_taxonomy import resolve_verb

    assert resolve_verb("my-feature") == ("my-feature", "live")
    assert resolve_verb("new") == ("new", "live")


def test_alias_resolution_wins_over_every_other_classification() -> None:
    """An alias key resolves to its canonical verb, live — never to `disabled`
    or `legacy`. Asserted by running the resolver on every alias key, so the
    guarantee holds however the other tables grow."""
    from camp.workspace.verb_taxonomy import VERB_ALIASES, resolve_verb

    assert VERB_ALIASES, "no aliases declared — nothing under test"
    for alias, canonical in VERB_ALIASES.items():
        assert resolve_verb(alias) == (canonical, "live"), (
            f"alias {alias!r} did not win resolution — got {resolve_verb(alias)!r}"
        )


# ---------------------------------------------------------------------------
# RESERVED — derived from the taxonomy, pinned by an explicit membership assertion
# ---------------------------------------------------------------------------


def _taxonomy_tokens() -> set[str]:
    from camp.workspace.verb_taxonomy import (
        DISABLED_VERBS,
        LEGACY_REDIRECTS,
        NEEDS_GROUP_VERBS,
        VERB_ALIASES,
    )

    return (
        set(VERB_ALIASES)
        | set(VERB_ALIASES.values())
        | set(LEGACY_REDIRECTS)
        | {target.split()[0] for target in LEGACY_REDIRECTS.values()}
        | set(DISABLED_VERBS)
        | set(NEEDS_GROUP_VERBS)
    )


def test_no_reserved_token_is_dispatched_as_a_bare_slug(groupless_env) -> None:
    """RESERVED exists to stop a token being taken for a workspace slug. Run the
    real CLI on every reserved token and require that none of them reaches the
    bare-slug error — that error IS the failure the set prevents.

    Asserted through the CLI rather than over set membership, because membership
    is only a proxy: a token could be in RESERVED and still fall through if the
    dispatcher stopped consulting the set.
    """
    from camp.spine import RESERVED

    assert RESERVED, "RESERVED is empty — nothing under test"
    offenders = []
    for token in sorted(RESERVED - {"which"}):  # see the xfail below
        out = _run([token], env=groupless_env)
        if "bare slug dispatch is no longer supported" in (out.stdout + out.stderr):
            offenders.append(token)
    assert not offenders, (
        f"reserved tokens dispatched as bare slugs: {offenders} — RESERVED no "
        "longer protects them"
    )


def test_every_taxonomy_token_is_protected_from_bare_slug_dispatch(groupless_env) -> None:
    """The derivation cannot silently drop a taxonomy-owned token: each one, run
    through the CLI, must avoid the bare-slug error."""
    tokens = _taxonomy_tokens()
    assert tokens, "taxonomy declares no tokens — nothing under test"
    offenders = [
        t
        for t in sorted(tokens)
        if "bare slug dispatch is no longer supported"
        in (lambda r: r.stdout + r.stderr)(_run([t], env=groupless_env))
    ]
    assert not offenders, (
        f"taxonomy tokens fell through to bare-slug dispatch: {offenders} — the "
        "RESERVED derivation dropped them"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "`which` is in RESERVED and in the dispatcher's skip-group-resolve set, "
        "but only the `--which` FLAG has a handler. The bare token falls through "
        "every branch to the bare-slug error (and exits 0 while printing it). "
        "Membership in RESERVED never made the dispatcher route it. Remove this "
        "xfail when `camp which` dispatches."
    ),
)
def test_which_is_not_dispatched_as_a_bare_slug(groupless_env) -> None:
    out = _run(["which"], env=groupless_env)
    assert "bare slug dispatch is no longer supported" not in (out.stdout + out.stderr)
