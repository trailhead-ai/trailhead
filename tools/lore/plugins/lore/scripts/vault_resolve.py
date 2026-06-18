"""Vault resolution algorithm for lore layered vaults (Slice 2, S4).

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
config validation (Slice 1) forbids a ``records`` allowlist on the default vault,
guaranteeing the eligible set is never empty.

**Scope-vs-frontmatter distinction:**  Resolution only considers scopes present in
``participating_scopes``.  ``--set scope=X`` (frontmatter-only) must NOT add an
entry to this map; that enforcement is the CLI's responsibility (Slice 4 / S2),
not this module's.

Pure stdlib: no I/O.  References: Slice 2, S4 plan.
"""
# NOTE: deliberately no ``from __future__ import annotations``. The lore test
# harness loads scripts via ``conftest.load_script`` (importlib without
# registering in sys.modules); under string annotations the stdlib dataclass
# machinery on 3.12+ looks the module up in sys.modules to resolve field
# annotations — same caution as vault_config.py (Slice 1 gotcha).

import vault_config as _vc

# ---------------------------------------------------------------------------
# Precedence table
# ---------------------------------------------------------------------------

#: Scope precedence, highest first.  The ``default`` scope sits last so it
#: acts as the unconditional floor.
_PRECEDENCE: tuple = ("repo", "product", "suite", "team", "default")


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
    """
    # Build a lookup: {scope: Vault} from config (one vault per scope by the
    # config invariant — globally unique names; scopes are distinct per vault).
    # For the default vault we keep it aside as the guaranteed floor.
    default_vault = None
    config_by_scope_name: dict = {}  # (scope, normalized_name) -> Vault
    config_default: dict = {}        # scope -> Vault for non-participated match

    for vault in config:
        if vault.scope == "default":
            default_vault = vault
        config_by_scope_name[(vault.scope, vault.name)] = vault

    # Normalize every supplied scope value once.
    normalized_participants: dict = {
        scope: _vc.normalize_vault_name(name)
        for scope, name in participating_scopes.items()
    }

    def _is_eligible(vault) -> bool:
        """True iff vault accepts ``kind`` (no allowlist or kind in allowlist)."""
        return not vault.records or kind in vault.records

    # Walk precedence from highest to lowest; return the first eligible match.
    for scope in _PRECEDENCE:
        if scope == "default":
            # Floor: always eligible (config invariant forbids records on default).
            if default_vault is not None:
                return default_vault
            # Should never reach here on a valid config, but be safe.
            continue

        if scope not in normalized_participants:
            # This scope was not supplied via a flag — skip it entirely.
            continue

        supplied_name = normalized_participants[scope]
        vault = config_by_scope_name.get((scope, supplied_name))
        if vault is None:
            # Supplied scope+name has no matching configured vault — fall through.
            continue

        if _is_eligible(vault):
            return vault
        # Matched but ineligible (kind not in allowlist) — fall through to the
        # next scope in precedence order, not directly to default.

    # Should be unreachable on a valid config (default floor is always eligible),
    # but guard defensively.
    if default_vault is not None:
        return default_vault

    raise RuntimeError(  # pragma: no cover
        "lore: resolve_vault found no eligible vault — config is missing a "
        "default-scope vault (this violates the Slice 1 invariant)"
    )
