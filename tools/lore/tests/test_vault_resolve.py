"""Tests for the vault resolution algorithm — ``vault_resolve.py``.

Covers the test contract:

- Four worked resolution examples (a–d), using a representative config as a
  fixture.
- (a) no scopes → default.
- (b) ``--team marketing`` with no matching vault → default (fall-through).
- (c) only flag-supplied scopes participate (a non-supplied scope is absent from
  the input map → not considered) → default.
- (d) ``repo`` outranks ``team``, but repo allowlist ``[decision, spec, plan]``
  excludes ``blob`` → fall through to the eligible ``team`` vault
  ``product-engineering``; AND ``--repo trailhead-ai/trailhead`` (with ``/``)
  normalizes to match stored ``trailhead-ai_trailhead``.
- Highest-precedence selection among multiple eligibles.
- Default floor when all higher scopes ineligible.
- Eligible set never empty (totality invariant).
- 3-scope / 2-ineligible fall-through.
"""

from conftest import load_script


def vr():
    return load_script("lore.vault.resolve")


# ---------------------------------------------------------------------------
# Worked config fixture
# ---------------------------------------------------------------------------
#
# Config used in all worked-example tests:
#
#   { "vaults": [
#       { "name": "default",               "scope": "default" },
#       { "name": "trailhead-ai_trailhead", "scope": "repo",
#         "records": ["decision","spec","plan"] },
#       { "name": "product-engineering",    "scope": "team",  "records": ["blob"] }
#   ]}


def _make_vault(name, scope, records=None, shared=False):
    """Build a lightweight Vault-like namedtuple via vault_config for these tests."""
    vc = load_script("lore.vault.config")
    from pathlib import Path

    return vc.Vault(
        name=name,
        scope=scope,
        path=Path(f"/tmp/vaults/{name}"),
        records=list(records) if records else [],
        shared=shared,
    )


def _spec_config():
    """Return the worked config as a list[Vault]."""
    return [
        _make_vault("default", "default"),
        _make_vault("trailhead-ai_trailhead", "repo", records=["decision", "spec", "plan"]),
        _make_vault("product-engineering", "team", records=["blob"]),
    ]


# ---------------------------------------------------------------------------
# Worked example (a): no scopes → default
# ---------------------------------------------------------------------------


def test_worked_a_no_scopes_resolves_to_default():
    """(a) No scope flags → only the default floor is eligible."""
    mod = vr()
    config = _spec_config()
    result = mod.resolve_vault({}, "blob", config)
    assert result.name == "default"
    assert result.scope == "default"


# ---------------------------------------------------------------------------
# Worked example (b): --team marketing with no matching vault → default
# ---------------------------------------------------------------------------


def test_worked_b_team_marketing_no_match_resolves_to_default():
    """(b) ``--team marketing`` participates but no vault matches; fall through."""
    mod = vr()
    config = _spec_config()
    # team scope is supplied, but the name "marketing" is not in config
    result = mod.resolve_vault({"team": "marketing"}, "blob", config)
    assert result.name == "default"
    assert result.scope == "default"


# ---------------------------------------------------------------------------
# Worked example (c): a scope absent from participating_scopes → default
# ---------------------------------------------------------------------------


def test_worked_c_absent_team_scope_resolves_to_default():
    """(c) resolution only sees the participating_scopes map — if ``team`` is
    absent from it, the product-engineering vault is never considered."""
    mod = vr()
    config = _spec_config()
    # participating_scopes is empty
    result = mod.resolve_vault({}, "blob", config)
    assert result.name == "default"
    assert result.scope == "default"


# ---------------------------------------------------------------------------
# Worked example (d): repo outranks team but repo allowlist excludes blob
# ---------------------------------------------------------------------------


def test_worked_d_repo_excluded_falls_through_to_team():
    """(d) Both scopes participate. Repo outranks team but excludes blob;
    fall through to product-engineering (team), which accepts blob."""
    mod = vr()
    config = _spec_config()
    result = mod.resolve_vault(
        {"team": "product-engineering", "repo": "trailhead-ai_trailhead"},
        "blob",
        config,
    )
    assert result.name == "product-engineering"
    assert result.scope == "team"


def test_worked_d_repo_slash_normalizes_to_match_stored():
    """(d) ``--repo trailhead-ai/trailhead`` (with ``/``) normalizes to
    ``trailhead-ai_trailhead`` and matches the stored vault."""
    mod = vr()
    config = _spec_config()
    # Raw flag value with '/' — resolver normalizes before matching
    result = mod.resolve_vault(
        {"team": "product-engineering", "repo": "trailhead-ai/trailhead"},
        "blob",
        config,
    )
    assert result.name == "product-engineering"
    assert result.scope == "team"


def test_worked_d_repo_eligible_kind_resolves_to_repo():
    """Symmetry: with kind=spec (in repo's allowlist), repo wins."""
    mod = vr()
    config = _spec_config()
    result = mod.resolve_vault(
        {"team": "product-engineering", "repo": "trailhead-ai/trailhead"},
        "spec",
        config,
    )
    assert result.name == "trailhead-ai_trailhead"
    assert result.scope == "repo"


# ---------------------------------------------------------------------------
# Highest-precedence selection among multiple eligibles
# ---------------------------------------------------------------------------


def test_highest_precedence_wins_when_multiple_eligible():
    """When both repo and team match and are eligible, repo (higher precedence) wins."""
    mod = vr()
    config = [
        _make_vault("default", "default"),
        _make_vault("my-repo", "repo"),  # no allowlist → all kinds eligible
        _make_vault("my-team", "team"),  # no allowlist → all kinds eligible
    ]
    result = mod.resolve_vault({"repo": "my-repo", "team": "my-team"}, "spec", config)
    assert result.name == "my-repo"
    assert result.scope == "repo"


def test_team_wins_over_default_when_only_team_supplied():
    """team scope vault eligible and supplied → team wins over default."""
    mod = vr()
    config = [
        _make_vault("default", "default"),
        _make_vault("my-team", "team"),  # no allowlist → all kinds eligible
    ]
    result = mod.resolve_vault({"team": "my-team"}, "session", config)
    assert result.name == "my-team"
    assert result.scope == "team"


# ---------------------------------------------------------------------------
# Default floor when all higher scopes ineligible
# ---------------------------------------------------------------------------


def test_default_floor_when_all_higher_ineligible():
    """All non-default vaults have allowlists that exclude the kind; default wins."""
    mod = vr()
    config = [
        _make_vault("default", "default"),
        _make_vault("my-repo", "repo", records=["spec"]),
        _make_vault("my-team", "team", records=["plan"]),
    ]
    # blob is not in either allowlist
    result = mod.resolve_vault({"repo": "my-repo", "team": "my-team"}, "blob", config)
    assert result.name == "default"
    assert result.scope == "default"


# ---------------------------------------------------------------------------
# Totality invariant — eligible set is never empty
# ---------------------------------------------------------------------------


def test_totality_default_floor_always_eligible():
    """Even with every supplied scope excluded by allowlists, default is returned."""
    mod = vr()
    config = [
        _make_vault("default", "default"),
        _make_vault("repo-a", "repo", records=["spec"]),
        _make_vault("team-a", "team", records=["plan"]),
        _make_vault("product-a", "product", records=["decision"]),
    ]
    result = mod.resolve_vault(
        {"repo": "repo-a", "team": "team-a", "product": "product-a"},
        "blob",
        config,
    )
    assert result.scope == "default"


# ---------------------------------------------------------------------------
# 3-scope / 2-ineligible fall-through
# ---------------------------------------------------------------------------


def test_three_scopes_two_ineligible_falls_through_to_third():
    """3 supplied scopes: repo and product ineligible (allowlists exclude kind),
    team eligible → resolves to team vault (not default)."""
    mod = vr()
    config = [
        _make_vault("default", "default"),
        _make_vault("my-repo", "repo", records=["spec", "plan"]),
        _make_vault("my-product", "product", records=["spec", "plan"]),
        _make_vault("my-team", "team", records=["blob"]),
    ]
    result = mod.resolve_vault(
        {"repo": "my-repo", "product": "my-product", "team": "my-team"},
        "blob",
        config,
    )
    assert result.name == "my-team"
    assert result.scope == "team"


def test_three_scopes_all_match_highest_eligible_wins():
    """3 supplied scopes all eligible → highest precedence (repo) wins."""
    mod = vr()
    config = [
        _make_vault("default", "default"),
        _make_vault("my-repo", "repo"),
        _make_vault("my-product", "product"),
        _make_vault("my-team", "team"),
    ]
    result = mod.resolve_vault(
        {"repo": "my-repo", "product": "my-product", "team": "my-team"},
        "blob",
        config,
    )
    assert result.name == "my-repo"
    assert result.scope == "repo"


# ---------------------------------------------------------------------------
# Scope not in config at all is simply absent (no error)
# ---------------------------------------------------------------------------


def test_scope_not_in_participating_does_not_error():
    """A scope not in participating_scopes is simply not considered."""
    mod = vr()
    config = [
        _make_vault("default", "default"),
        _make_vault("my-team", "team"),
    ]
    # Only repo supplied, but no repo vault exists — falls to default
    result = mod.resolve_vault({"repo": "nonexistent"}, "blob", config)
    assert result.scope == "default"


# ---------------------------------------------------------------------------
# Default vault itself is always eligible (no allowlist by config invariant)
# ---------------------------------------------------------------------------


def test_default_vault_eligible_for_any_kind():
    """Default vault (empty records) accepts every kind."""
    mod = vr()
    config = [_make_vault("default", "default")]
    for kind in (
        "blob",
        "spec",
        "plan",
        "session",
        "decision",
        "area",
        "backlog",
        "collaboration",
        "lesson",
    ):
        result = mod.resolve_vault({}, kind, config)
        assert result.scope == "default", f"kind={kind!r} should resolve to default"


# ---------------------------------------------------------------------------
# Suite scope precedence (suite sits between product and team)
# ---------------------------------------------------------------------------


def test_suite_outranks_team():
    """Suite scope has higher precedence than team."""
    mod = vr()
    config = [
        _make_vault("default", "default"),
        _make_vault("my-suite", "suite"),
        _make_vault("my-team", "team"),
    ]
    result = mod.resolve_vault({"suite": "my-suite", "team": "my-team"}, "blob", config)
    assert result.name == "my-suite"
    assert result.scope == "suite"


def test_product_outranks_suite():
    """Product scope has higher precedence than suite."""
    mod = vr()
    config = [
        _make_vault("default", "default"),
        _make_vault("my-product", "product"),
        _make_vault("my-suite", "suite"),
    ]
    result = mod.resolve_vault({"product": "my-product", "suite": "my-suite"}, "blob", config)
    assert result.name == "my-product"
    assert result.scope == "product"
