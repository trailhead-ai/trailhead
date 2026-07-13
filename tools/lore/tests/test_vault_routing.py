"""Tests for config-driven routing + shared projection + orphan guard.

These cross-reference the ``test_record_store`` / ``test_index_store`` unit suites;
here we exercise the *wired* end-to-end behavior through the CLI subprocess, with a
``config.json`` placed under a tmp ``$XDG_CONFIG_HOME`` (Axiom 6).

Covers the test contract:

  - End-to-end routing: ``--team product-engineering --repo trailhead-ai/trailhead``
    lands in ``product-engineering``; no scope flags → default.
  - Routing confirmation line names the elected vault + scope (every create); names
    the fall-through reason on example-d.
  - The created record's index row carries the resolved vault's ``shared``: own-vault
    → 0, ``shared: true`` vault → 1; multiple own vaults all → 0.
  - A config edit flipping a vault's ``shared`` updates its rows after ``lore
    reindex``; a stale-config index surfaces the freshness warning.
  - ``lore record update <id-in-deleted-vault>`` → clear non-zero "vault not
    configured" error.
  - Vanilla regression: with NO config.json, create/reindex/update behave as before.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from conftest import (  # noqa: F401
    load_script,
    make_vault as _make_vault,
    run_cli as _run,
    write_default_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_index_store():
    return load_script("lore.search.index")


def _row_shared(state: Path, vault_path: str, kind: str, name: str):
    """Return the ``shared`` value of the index row, or None if absent."""
    mod = _load_index_store()
    conn = mod.open_index(env={"XDG_STATE_HOME": str(state)})
    try:
        row = conn.execute(
            "SELECT shared FROM records WHERE vault=? AND kind=? AND name=?",
            (vault_path, kind, name),
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else row[0]


def _all_rows(state: Path):
    mod = _load_index_store()
    conn = mod.open_index(env={"XDG_STATE_HOME": str(state)})
    try:
        rows = conn.execute("SELECT vault, kind, name, shared FROM records").fetchall()
    finally:
        conn.close()
    return rows


def _write_config(config_home: Path, vaults: list[dict]) -> Path:
    """Write a config.json under config_home/lore/ and return its path."""
    lore_cfg = config_home / "lore"
    lore_cfg.mkdir(parents=True, exist_ok=True)
    cfg_path = lore_cfg / "config.json"
    cfg_path.write_text(json.dumps({"vaults": vaults}, indent=2), encoding="utf-8")
    return cfg_path


def _spec_config_vaults(state: Path) -> list[dict]:
    """The worked-example-d config, with explicit paths under state/vaults."""
    root = state / "lore" / "vaults"
    return [
        {"name": "default", "scope": "default", "path": str(root / "default")},
        {
            "name": "trailhead-ai_trailhead",
            "scope": "repo",
            "records": ["decision", "spec", "task"],
            "path": str(root / "trailhead-ai_trailhead"),
        },
        {
            "name": "product-engineering",
            "scope": "team",
            "records": ["blob"],
            "path": str(root / "product-engineering"),
        },
    ]


def _vault_path(state: Path, name: str) -> str:
    return str(state / "lore" / "vaults" / name)


def _run_with_config(args, *, vault, state, config_home, stdin_text=None):
    return _run(
        args,
        vault=vault,
        state_dir=state,
        stdin_text=stdin_text,
        env_extra={"XDG_CONFIG_HOME": str(config_home)},
    )


# ---------------------------------------------------------------------------
# Config-driven routing + confirmation line
# ---------------------------------------------------------------------------


def test_routing_example_d_lands_in_team_vault(tmp_path):
    """``--team product-engineering --repo trailhead-ai/trailhead`` with --kind
    blob lands in the product-engineering vault (repo excludes blob, falls through)."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    _write_config(config_home, _spec_config_vaults(state))

    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Grape",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
            "--repo",
            "trailhead-ai/trailhead",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="grape body\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    assert record_id.startswith("blob/"), record_id

    # Lands physically in the product-engineering vault dir.
    pe_path = Path(_vault_path(state, "product-engineering"))
    assert (pe_path / "blob").exists()
    assert list((pe_path / "blob").glob("*.md")), "record body not in team vault"


# A scope value cannot be set without routing: the scope flags are unified so
# setting a scope always relocates the record. There is no generic ``--set``
# patch idiom and no frontmatter-only ``--set team=...`` path.


def test_routing_no_scope_flags_lands_in_default(tmp_path):
    """No scope flags → default floor."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    _write_config(config_home, _spec_config_vaults(state))

    r = _run_with_config(
        ["record", "create", "--kind", "blob", "--title", "Apple", "--keyword", "foo"],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="apple body\n",
    )
    assert r.returncode == 0, r.stderr
    default_path = Path(_vault_path(state, "default"))
    assert list((default_path / "blob").glob("*.md"))


def test_record_id_is_sole_stdout_line(tmp_path):
    """The routing confirmation must NOT pollute stdout — RECORD_ID stays parseable."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    _write_config(config_home, _spec_config_vaults(state))

    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Apple",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr
    # Exactly one stdout line — the RECORD_ID.
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0].startswith("blob/")


def test_routing_confirmation_names_vault_and_scope(tmp_path):
    """Routing confirmation line names the elected vault + scope on every create."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    _write_config(config_home, _spec_config_vaults(state))

    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Apple",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr
    assert "Routed to vault: product-engineering (team)" in r.stderr


def test_routing_confirmation_names_fallthrough_reason(tmp_path):
    """The higher repo vault is skipped (blob not in allowlist); the
    confirmation names the fall-through reason."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    _write_config(config_home, _spec_config_vaults(state))

    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Grape",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
            "--repo",
            "trailhead-ai/trailhead",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr
    assert "trailhead-ai_trailhead" in r.stderr
    assert "excluded" in r.stderr.lower()


# ---------------------------------------------------------------------------
# Shared threaded into the index write
# ---------------------------------------------------------------------------


def test_own_vault_record_indexed_shared_zero(tmp_path):
    """An own-vault (shared: false) record → index row shared=0."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    _write_config(config_home, _spec_config_vaults(state))

    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Grape",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    _, name = record_id.split("/", 1)
    pe_path = _vault_path(state, "product-engineering")
    assert _row_shared(state, pe_path, "blob", name) == 0


def test_shared_true_vault_record_indexed_shared_one(tmp_path):
    """A ``shared: true`` vault's record → index row shared=1."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    vaults = _spec_config_vaults(state)
    # Mark the team vault shared: true.
    for v in vaults:
        if v["name"] == "product-engineering":
            v["shared"] = True
    _write_config(config_home, vaults)

    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Grape",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    _, name = record_id.split("/", 1)
    pe_path = _vault_path(state, "product-engineering")
    assert _row_shared(state, pe_path, "blob", name) == 1


# ---------------------------------------------------------------------------
# Update-path shared trust: the resolved vault's
# shared flag is threaded through BOTH the auto-move and in-place index writes,
# so an update never silently un-fences (or fails to fence) a record's row.
# ---------------------------------------------------------------------------


def test_update_automove_into_shared_vault_indexes_shared_one(tmp_path):
    """`update --team <shared-vault>` relocates the record INTO a ``shared: true``
    vault → the destination index row carries shared=1 (not the default 0); the
    old vault's row is gone."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    vaults = _spec_config_vaults(state)
    for v in vaults:
        if v["name"] == "product-engineering":
            v["shared"] = True
    _write_config(config_home, vaults)

    # Create a blob in the default vault (no scope flag → default, shared=0).
    r = _run_with_config(
        ["record", "create", "--kind", "blob", "--title", "Grape", "--keyword", "foo"],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    _, name = record_id.split("/", 1)
    default_path = _vault_path(state, "default")
    assert _row_shared(state, default_path, "blob", name) == 0

    # Move it into the shared product-engineering vault.
    r2 = _run_with_config(
        ["record", "update", record_id, "--team", "product-engineering"],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    assert r2.returncode == 0, r2.stderr
    assert "moved:" in r2.stdout

    pe_path = _vault_path(state, "product-engineering")
    # Destination row is fenced (shared=1); the old default row is gone (no orphan).
    assert _row_shared(state, pe_path, "blob", name) == 1
    assert _row_shared(state, default_path, "blob", name) is None


def test_update_in_place_preserves_shared_trust(tmp_path):
    """An in-place `update` of a record living in a ``shared: true`` vault must NOT
    reset its index row to shared=0 — the trust flag is recomputed from the
    resolved vault, not defaulted."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    vaults = _spec_config_vaults(state)
    for v in vaults:
        if v["name"] == "product-engineering":
            v["shared"] = True
    _write_config(config_home, vaults)

    # Create a blob directly in the shared team vault (shared=1).
    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Grape",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    _, name = record_id.split("/", 1)
    pe_path = _vault_path(state, "product-engineering")
    assert _row_shared(state, pe_path, "blob", name) == 1

    # In-place update (no scope change → same vault); the row must stay shared=1.
    r2 = _run_with_config(
        ["record", "update", record_id, "--keyword", "bar"],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    assert r2.returncode == 0, r2.stderr
    assert "moved:" not in r2.stdout
    assert _row_shared(state, pe_path, "blob", name) == 1


# ---------------------------------------------------------------------------
# Multi-vault reindex, config-sourced shared, freshness
# ---------------------------------------------------------------------------


def test_reindex_flip_shared_updates_rows(tmp_path):
    """A config edit flipping a vault's shared flag updates its rows after reindex."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    vaults = _spec_config_vaults(state)
    cfg_path = _write_config(config_home, vaults)

    # Create a record in the team vault (shared: false → shared=0).
    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Grape",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr
    _, name = r.stdout.strip().split("/", 1)
    pe_path = _vault_path(state, "product-engineering")
    assert _row_shared(state, pe_path, "blob", name) == 0

    # Flip product-engineering to shared: true and reindex.
    for v in vaults:
        if v["name"] == "product-engineering":
            v["shared"] = True
    cfg_path.write_text(json.dumps({"vaults": vaults}, indent=2), encoding="utf-8")

    r2 = _run_with_config(
        ["reindex"],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    assert r2.returncode == 0, r2.stderr
    assert _row_shared(state, pe_path, "blob", name) == 1


def test_reindex_spans_all_configured_vaults(tmp_path):
    """reindex with config present ingests every configured vault, not just one."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    vaults = _spec_config_vaults(state)
    _write_config(config_home, vaults)

    # Put one record in the repo vault and one in the team vault.
    _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "spec",
            "--title",
            "RepoSpec",
            "--keyword",
            "foo",
            "--repo",
            "trailhead-ai/trailhead",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="a\n",
    )
    _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "TeamBlob",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="b\n",
    )

    r = _run_with_config(["reindex"], vault=vault, state=state, config_home=config_home)
    assert r.returncode == 0, r.stderr

    rows = _all_rows(state)
    vault_cols = {row[0] for row in rows}
    assert _vault_path(state, "trailhead-ai_trailhead") in vault_cols
    assert _vault_path(state, "product-engineering") in vault_cols


def test_stale_config_index_surfaces_freshness_warning(tmp_path):
    """An index built against an older config than the current config.json surfaces a
    config-staleness warning on search (stderr)."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    vaults = _spec_config_vaults(state)
    cfg_path = _write_config(config_home, vaults)

    # Build the index against the current config.
    r = _run_with_config(["reindex"], vault=vault, state=state, config_home=config_home)
    assert r.returncode == 0, r.stderr

    # Now edit the config out-of-band (newer mtime than the index stamp).
    time.sleep(0.05)
    for v in vaults:
        if v["name"] == "product-engineering":
            v["shared"] = True
    os.utime(cfg_path)  # bump mtime
    cfg_path.write_text(json.dumps({"vaults": vaults}, indent=2), encoding="utf-8")
    future = time.time() + 10
    os.utime(cfg_path, (future, future))

    r2 = _run_with_config(
        ["search", "kind:blob"],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    out = r2.stdout + r2.stderr
    assert "config" in out.lower() and "reindex" in out.lower()


# ---------------------------------------------------------------------------
# Orphan-ID guard
# ---------------------------------------------------------------------------
#
# The ``record update --move-to`` orphan-guard test was removed with the
# ``--move-to`` flag: relocation is now an automatic byproduct of a scope-flag
# change and the destination is only ever a config-declared vault root (resolved
# via the create-side resolver), so the explicit-unconfigured-destination path
# the guard protected does not exist.
# The ``delete`` orphan path below is unaffected and stays.


def test_delete_in_removed_vault_is_unreachable(tmp_path):
    """``lore record delete`` after the record's vault was removed from config.

    Delete resolves the target vault via config (symmetric with create) using the
    routing flags + the record's kind — NOT $LORE_VAULT. A record that was routed
    to a now-removed team vault therefore resolves to the ``default`` floor, where
    it does not exist, and delete fails cleanly with a not-found error rather than
    acting on an orphaned target.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    vaults = _spec_config_vaults(state)
    cfg_path = _write_config(config_home, vaults)

    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Grape",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()

    # Remove the team vault from config — the record's directory still exists on
    # disk, but it is not a configured destination.
    vaults = [v for v in vaults if v["name"] != "product-engineering"]
    cfg_path.write_text(json.dumps({"vaults": vaults}, indent=2), encoding="utf-8")

    # Delete with no routing flags resolves to the default vault, where the
    # record is not present → clean non-zero not-found, not a silent orphan act.
    r2 = _run_with_config(
        ["record", "delete", record_id],
        vault=Path(_vault_path(state, "product-engineering")),
        state=state,
        config_home=config_home,
    )
    assert r2.returncode != 0
    assert "not found" in (r2.stdout + r2.stderr).lower()


# ---------------------------------------------------------------------------
# Regression: default-vault records are reachable by update/delete with a config
# present (create routes to the config default vault, NOT $LORE_VAULT).
# ---------------------------------------------------------------------------


def test_default_record_updatable_and_deletable_with_config(tmp_path):
    """A no-scope record created with a config present routes to the ``default``
    config vault; update and delete must reach it THERE (config resolution),
    not in the active ``$LORE_VAULT`` directory (which is a different path).

    This is the create-vs-update/delete location split: create routes no-scope
    records into ``state/vaults/default``, and update/delete resolve via config,
    symmetric with create — so a just-created record is reachable.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    _write_config(config_home, _spec_config_vaults(state))
    default_path = Path(_vault_path(state, "default"))
    # The active vault and the default config vault are deliberately different dirs.
    assert Path(vault).resolve() != default_path.resolve()

    # Create with NO scope flags → routes to the default config vault.
    r = _run_with_config(
        ["record", "create", "--kind", "blob", "--title", "Apple", "--keyword", "foo"],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="apple body\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    assert list((default_path / "blob").glob("*.md")), "record not in default vault"

    # Update (metadata-only, no flags) must reach the record in the default vault.
    r2 = _run_with_config(
        ["record", "update", record_id, "--keyword", "bar"],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    assert r2.returncode == 0, f"update should reach default-vault record: {r2.stderr}"

    # Delete (no flags) must also reach it.
    r3 = _run_with_config(
        ["record", "delete", record_id],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    assert r3.returncode == 0, f"delete should reach default-vault record: {r3.stderr}"
    assert not list((default_path / "blob").glob("*.md")), "record not deleted"


def test_scoped_record_updatable_and_deletable_with_same_flags(tmp_path):
    """A record routed to a scoped vault via ``--team`` on create is reachable by
    update and delete when the SAME routing flag is supplied — proving update/delete
    flag plumbing resolves to the scoped vault, not the default floor.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    _write_config(config_home, _spec_config_vaults(state))
    pe_path = Path(_vault_path(state, "product-engineering"))

    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Grape",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="grape body\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    assert list((pe_path / "blob").glob("*.md")), "record not in team vault"

    # Update WITH the same --team flag reaches the scoped record.
    r2 = _run_with_config(
        ["record", "update", record_id, "--keyword", "bar", "--team", "product-engineering"],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    assert r2.returncode == 0, f"scoped update should reach the record: {r2.stderr}"

    # Delete WITH the same --team flag reaches the scoped record.
    r3 = _run_with_config(
        ["record", "delete", record_id, "--team", "product-engineering"],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    assert r3.returncode == 0, f"scoped delete should reach the record: {r3.stderr}"
    assert not list((pe_path / "blob").glob("*.md")), "scoped record not deleted"


def test_colliding_record_disambiguated_by_flag(tmp_path):
    """The same ``<kind>/<name>`` created in two different vaults is ambiguous to
    the no-flag scan (first configured match wins); an explicit scope flag must
    still pick out the specific copy in that vault — the escape hatch
    ``_resolve_record_op_vault``'s docstring promises for this exact case.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    _write_config(config_home, _spec_config_vaults(state))

    # Same kind + title → same slug ("blob/grape") in two different vaults.
    r_default = _run_with_config(
        ["record", "create", "--kind", "blob", "--title", "Grape", "--keyword", "foo"],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="default vault body\n",
    )
    assert r_default.returncode == 0, r_default.stderr
    record_id = r_default.stdout.strip()

    r_team = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Grape",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="team vault body\n",
    )
    assert r_team.returncode == 0, r_team.stderr
    assert r_team.stdout.strip() == record_id, "expected the same slug in both vaults"

    # No-flag show hits the scan's first configured match (default vault, per
    # _spec_config_vaults' ordering) — not a contract this test locks in beyond
    # "some deterministic copy", but assert it for documentation.
    r_scan = _run_with_config(
        ["record", "show", record_id],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    assert r_scan.returncode == 0, r_scan.stderr
    assert "default vault body" in r_scan.stdout

    # The explicit --team flag disambiguates to the team-vault copy.
    r_flag = _run_with_config(
        ["record", "show", record_id, "--team", "product-engineering"],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    assert r_flag.returncode == 0, r_flag.stderr
    assert "team vault body" in r_flag.stdout


def test_scoped_record_show_reachable_without_flags(tmp_path):
    """``record show`` on a record ``create`` routed to a non-default vault must
    find it WITHOUT re-supplying the routing flag — the bug this test guards:
    ``show`` used to resolve the vault from ONLY the current invocation's flags
    (defaulting to the floor vault when none were given), while ``search`` and
    ``update`` located the record correctly via a config-driven scan. A caller
    that reads a RECORD_ID off ``search`` output has no reason to know — or
    re-supply — the scope it was created with.
    """
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    _write_config(config_home, _spec_config_vaults(state))

    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Grape",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="grape body\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()

    # Show with NO scope flags must still find the scoped record.
    r2 = _run_with_config(
        ["record", "show", record_id],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    assert r2.returncode == 0, f"show should reach the scoped record: {r2.stderr}"
    assert "grape body" in r2.stdout


def test_scoped_record_delete_reachable_without_flags(tmp_path):
    """``record delete`` on a scoped record must also work without re-supplying
    the routing flag (symmetric with the ``show`` fix above and with ``update``,
    which already scans for the record's current vault)."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "config"
    _write_config(config_home, _spec_config_vaults(state))
    pe_path = Path(_vault_path(state, "product-engineering"))

    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Grape",
            "--keyword",
            "foo",
            "--team",
            "product-engineering",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="grape body\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    assert list((pe_path / "blob").glob("*.md")), "record not in team vault"

    # Delete with NO scope flags must still find and remove the scoped record.
    r2 = _run_with_config(
        ["record", "delete", record_id],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    assert r2.returncode == 0, f"delete should reach the scoped record: {r2.stderr}"
    assert not list((pe_path / "blob").glob("*.md")), "scoped record not deleted"


# ---------------------------------------------------------------------------
# Vanilla regression: no config.json present → today's behavior
# ---------------------------------------------------------------------------


def test_vanilla_no_config_create_uses_floor_vault(tmp_path):
    """With NO config.json, create lands in the floor vault (config-only
    resolution, no $LORE_VAULT — the silent floor is state/lore/vaults/default)."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "empty_config"  # no config.json here
    config_home.mkdir()
    floor = Path(_vault_path(state, "default"))

    r = _run_with_config(
        [
            "record",
            "create",
            "--kind",
            "blob",
            "--title",
            "Plain",
            "--keyword",
            "foo",
            "--team",
            "whatever",
        ],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()
    kind, name = record_id.split("/", 1)
    # Body is in the floor vault (the no-config resolution target).
    assert (floor / kind / f"{name}.md").exists()
    # Index row under the floor vault, shared=0.
    assert _row_shared(state, str(floor), kind, name) == 0


def test_vanilla_no_config_update_delete_no_orphan_guard(tmp_path):
    """With NO config.json, update + delete behave as before (no orphan guard)."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "empty_config"
    config_home.mkdir()

    r = _run_with_config(
        ["record", "create", "--kind", "blob", "--title", "Plain", "--keyword", "foo"],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr
    record_id = r.stdout.strip()

    r_upd = _run_with_config(
        ["record", "update", record_id, "--keyword", "bar"],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="new\n",
    )
    assert r_upd.returncode == 0, r_upd.stderr

    r_del = _run_with_config(
        ["record", "delete", record_id],
        vault=vault,
        state=state,
        config_home=config_home,
    )
    assert r_del.returncode == 0, r_del.stderr


def test_vanilla_no_config_reindex_single_vault(tmp_path):
    """With NO config.json, reindex uses the single floor vault (config-only
    resolution resolves to state/lore/vaults/default)."""
    vault, state = _make_vault(tmp_path)
    config_home = tmp_path / "empty_config"
    config_home.mkdir()
    floor = Path(_vault_path(state, "default"))

    r = _run_with_config(
        ["record", "create", "--kind", "blob", "--title", "Plain", "--keyword", "foo"],
        vault=vault,
        state=state,
        config_home=config_home,
        stdin_text="x\n",
    )
    assert r.returncode == 0, r.stderr

    r2 = _run_with_config(["reindex"], vault=vault, state=state, config_home=config_home)
    assert r2.returncode == 0, r2.stderr
    rows = _all_rows(state)
    assert {row[0] for row in rows} == {str(floor)}
