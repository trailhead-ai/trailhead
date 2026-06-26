"""Vault resolution algorithm for lore layered vaults.

This module exposes a single pure function, :func:`resolve_vault`, that selects
the single destination vault for a ``lore record`` write given the scopes
supplied on the command line, the record's kind, and the loaded vault config.

**Precedence order** (highest → lowest)::

    repo > product > suite > team > default

**Resolution algorithm:**

1. For each scope in ``participating_scopes``, normalize the supplied name
   (``/`` → ``_`` via :func:`vault_config.normalize_vault_name`) and look up
   the configured vault that matches both ``scope`` AND the normalized name.
2. From the matched vaults, keep only those *eligible* for the record's kind:
   a vault is eligible iff it has **no** ``records`` allowlist (empty list means
   all kinds) OR the kind is explicitly in its allowlist.
3. Among eligible matches, choose the vault whose scope is highest in the
   precedence table.  If no non-default vault is eligible, return the
   ``default``-scope vault, which is the unconditional floor.

**Totality invariant:**  The result is always a :class:`vault_config.Vault` — it
never raises for "no match".  The ``default`` vault is always eligible because
config validation forbids a ``records`` allowlist on the default vault,
guaranteeing the eligible set is never empty.

**Scope-vs-frontmatter distinction:**  Resolution only considers scopes present in
``participating_scopes``.  Only the routing flags (``--team`` etc.) add entries to
this map; populating it is the CLI's responsibility, not this
module's.

Pure stdlib: no I/O.
"""
# NOTE: deliberately no ``from __future__ import annotations``. The lore test
# harness loads scripts via ``conftest.load_script`` (importlib without
# registering in sys.modules); under string annotations the stdlib dataclass
# machinery on 3.12+ looks the module up in sys.modules to resolve field
# annotations — same caution as vault_config.py.

from typing import NamedTuple

import vault_config as _vc

# ---------------------------------------------------------------------------
# Precedence table
# ---------------------------------------------------------------------------

#: Scope precedence, highest first.  The ``default`` scope sits last so it
#: acts as the unconditional floor.
_PRECEDENCE: tuple = ("repo", "product", "suite", "team", "default")


# ---------------------------------------------------------------------------
# Resolution result — elected vault + any skipped higher vault
# ---------------------------------------------------------------------------


class Resolution(NamedTuple):
    """The outcome of :func:`explain_resolution`.

    Attributes:
        chosen:        The elected :class:`vault_config.Vault` (always present —
                       resolution is total).
        skipped:       The highest-precedence vault that *matched* a supplied scope
                       but was **ineligible** for the kind (its allowlist excluded
                       the kind), and so was passed over for a lower-precedence
                       eligible vault.  ``None`` when nothing was skipped (the
                       chosen vault was the highest match, or the default floor was
                       reached with no higher match).
        skipped_reason: A human-readable reason the ``skipped`` vault was passed
                       over (``"kind not in allowlist"``), or ``None``.
    """

    chosen: object
    skipped: object
    skipped_reason: str | None


# ---------------------------------------------------------------------------
# resolve_vault
# ---------------------------------------------------------------------------


def resolve_vault(participating_scopes: dict, kind: str, config: list):
    """Select the single destination vault for a record write.

    Args:
        participating_scopes: ``{scope: name}`` map of scopes supplied via
            flags (``--repo X``, ``--team Y``, etc.).  Scopes absent from this
            map are **not** considered, regardless of what is in ``config``.
            Each value is normalized (``/`` → ``_``) before matching.
        kind:   The record kind string (e.g. ``"blob"``, ``"spec"``).
        config: The list of :class:`vault_config.Vault` instances from
            :func:`vault_config.load_config`.

    Returns:
        The highest-precedence eligible :class:`vault_config.Vault`.

    Invariants:
    - Never returns ``None`` and never raises for "no match" — the
      ``default``-scope vault is always eligible (totality).
    - Only vaults whose scope+name appear in ``participating_scopes`` are
      candidates (plus the default floor which is always tried last).
    - Precedence: ``repo > product > suite > team > default``.
    - Fall-through: a matched-but-ineligible vault is skipped; resolution
      continues to the next lower scope rather than jumping straight to
      default.

    Delegates to :func:`explain_resolution` (the single source of the selection
    walk) and returns only its ``chosen`` vault, so the two entry points can never
    drift apart in a security-relevant resolver.
    """
    return explain_resolution(participating_scopes, kind, config).chosen


def explain_resolution(participating_scopes: dict, kind: str, config: list) -> Resolution:
    """Resolve a vault AND report the highest matched-but-ineligible vault.

    Same selection as :func:`resolve_vault` (totality, precedence, fall-through),
    but returns a :class:`Resolution` so the CLI can print a routing-confirmation
    line that names *why* a higher-precedence vault was skipped — an author
    should never silently lose a record to allowlist fall-through.

    The ``skipped`` field is the **highest-precedence** vault that matched a supplied
    scope+name but was ineligible for *kind* (its ``records`` allowlist excluded the
    kind), so the chosen vault is a lower-precedence eligible one. ``resolve_vault``
    is kept intact (the resolve_vault contract); this is a strict superset used only by the
    CLI confirmation path.
    """
    default_vault = None
    config_by_scope_name: dict = {}
    for vault in config:
        if vault.scope == "default":
            default_vault = vault
        config_by_scope_name[(vault.scope, vault.name)] = vault

    normalized_participants: dict = {
        scope: _vc.normalize_vault_name(name) for scope, name in participating_scopes.items()
    }

    def _is_eligible(vault) -> bool:
        return not vault.records or kind in vault.records

    skipped = None
    skipped_reason: str | None = None

    for scope in _PRECEDENCE:
        if scope == "default":
            if default_vault is not None:
                return Resolution(default_vault, skipped, skipped_reason)
            continue

        if scope not in normalized_participants:
            continue

        supplied_name = normalized_participants[scope]
        vault = config_by_scope_name.get((scope, supplied_name))
        if vault is None:
            continue

        if _is_eligible(vault):
            return Resolution(vault, skipped, skipped_reason)

        # Matched but ineligible — record the FIRST (highest-precedence) such vault
        # as the reported skip, then fall through to the next scope.
        if skipped is None:
            skipped = vault
            skipped_reason = "kind not in allowlist"

    if default_vault is not None:  # pragma: no cover - same guard as resolve_vault
        return Resolution(default_vault, skipped, skipped_reason)

    raise RuntimeError(  # pragma: no cover
        "lore: explain_resolution found no eligible vault — config is missing a "
        "default-scope vault (this violates the totality invariant)"
    )
