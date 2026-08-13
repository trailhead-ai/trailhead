"""``lore areas`` / ``lore reindex`` — the area menu + derived-index rebuild."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .common import _load_vault_config, _resolve_all_vaults_and_shared


def cmd_areas(args) -> int:
    """Print the full area menu (names, one-liners, keywords) to stdout.

    Spans **every configured non-shared vault**, not just the `default`-scope
    vault: resolves the whole-install vault set (and its shared-name set, from
    one `config.json` read) via `_resolve_all_vaults_and_shared` (built on the
    same enumeration `lore sync`/`lore status` use), then
    `_dedupe_and_exclude_shared_roots` drops any configured-but-absent root
    and any root whose PHYSICAL directory (`os.stat` device+inode, not the
    path string) is claimed by a `shared: true` entry — the area menu is
    personal-scoped, a shared vault's content only reaches context through
    the explicitly-delimited `lore search` path, and that must hold even when
    a differently-cased or symlinked alias names the same directory under a
    non-shared entry. `build_area_map_multi` merges the surviving per-vault
    menus, deduping same-named areas config-order-first-wins, and already
    isolates a single root's `build_area_map` failure from the rest.

    Exit code is always 0 — must never fail a session. The one-line stderr
    signal fires only when nothing legitimately resolved: either the config
    read itself failed (malformed/wrong-shape config), vault resolution raised
    something outside that (e.g. an unresolvable ``XDG_CONFIG_HOME``/``HOME``),
    no root resolved at all (every configured root was absent/shared), or
    every resolved root's `build_area_map` call raised. A healthy vault that
    simply has zero areas defined is not an error — it renders the degraded
    "no areas" stdout line with a silent stderr, matching vanilla's
    byte-identical behavior. A single failing root among several (its
    `build_area_map` call raised) does not trigger the signal either, as long
    as another root produced entries — the surviving roots' areas render
    normally.

    The whole body runs under a single total guard: this is the command
    boundary, and nothing below it — vault/shared-path resolution, the merge —
    may ever escape as a traceback. `_resolve_all_vaults_and_shared` and
    `build_area_map_multi` already narrow the *expected* failure modes (a
    malformed config, a single root's `build_area_map` blowing up) to a
    reported error rather than a raise; this guard exists for the
    unenumerated rest (e.g. path resolution itself failing) so the contract
    holds regardless of what a callee below decides is worth its own typed
    error.
    """
    from ..search import area_map as area_map_mod

    _NO_AREAS_LINE = "No areas defined yet."

    try:
        all_vaults, shared_names, error = _resolve_all_vaults_and_shared()
        roots = _dedupe_and_exclude_shared_roots(all_vaults, shared_names)

        root_errors: list = []
        entries = area_map_mod.build_area_map_multi(roots, errors=root_errors)
    except Exception as exc:
        print(f"lore areas: could not resolve vault ({exc})", file=sys.stderr)
        print(_NO_AREAS_LINE)
        return 0

    if error is not None:
        print(f"lore areas: could not resolve vaults ({error})", file=sys.stderr)
    elif not entries and (not roots or root_errors):
        print("lore areas: no areas found in any configured vault", file=sys.stderr)

    if not entries:
        print(_NO_AREAS_LINE)
        return 0

    print(area_map_mod.render_area_menu(entries))
    return 0


def _dedupe_and_exclude_shared_roots(
    all_vaults: list, shared_names: set
) -> list:
    """Return the existing, non-shared vault roots — deduped by PHYSICAL identity.

    ``shared_names`` names which config entries are ``shared: true`` (the
    authoritative flag, carried straight from ``Vault.shared`` — see
    :func:`._resolve_all_vaults_and_shared`), but a name-keyed exclusion
    alone is not enough: two config entries can point at the exact same
    directory on disk under different path strings (different case on a
    case-insensitive filesystem, a symlink, a bind mount, ...), and only ONE
    of those entries need be the one marked ``shared: true``. Excluding by
    name alone would still hand the OTHER entry's (nominally non-shared) root
    straight to :func:`build_area_map_multi`, which would scan the very same
    directory and merge its area files into the menu anyway — reproducing
    the leak `shared: true` exists to prevent, just one alias removed.

    This resolves each candidate root's PHYSICAL identity via
    ``os.stat().st_dev``/``st_ino`` — the one comparison that is authoritative
    regardless of path string, case-folding, or symlink form — and treats a
    physical directory as shared if ANY vault entry naming it is
    ``shared: true``, independent of which entry a given root came from.
    Non-shared roots are additionally deduped to one entry per physical
    directory (first in config order) so an aliased non-shared vault is not
    scanned twice for no benefit.

    A root that no longer exists, or that a broken ``stat()`` call cannot
    identify (races, permission errors), is silently dropped — the existing
    ``path.exists()`` guard this replaces already tolerated absent roots the
    same way. ``exists()`` itself is called per-root inside its own
    ``try/except``: a root whose check raises (e.g. a symlink loop, or a
    permission error on a parent directory) must degrade like any other
    single-root failure — the surviving roots still render — rather than
    escaping to `cmd_areas`'s outer total guard and blanking the whole menu.
    """
    existing: list = []
    for name, path in all_vaults:
        try:
            if path.exists():
                existing.append((name, path))
        except OSError:
            continue

    identity_by_name: dict = {}
    shared_identities: set = set()
    for name, path in existing:
        try:
            st = os.stat(path)
        except OSError:
            continue
        identity = (st.st_dev, st.st_ino)
        identity_by_name[name] = identity
        if name in shared_names:
            shared_identities.add(identity)

    roots: list = []
    seen_identities: set = set()
    for name, path in existing:
        identity = identity_by_name.get(name)
        if identity is None or identity in shared_identities:
            continue
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        roots.append(path)

    return roots


def cmd_reindex(args) -> int:
    """Rebuild the derived SQLite index from the vault directory tree.

    Drops and repopulates the ``records`` table by scanning the resolved
    vault for ``<kind>/<name>.json`` + ``<kind>/<name>.md`` pairs.  Prints
    the number of indexed rows to **stdout** on success; errors go to
    **stderr**.  Exits 0.

    Text-wins / index-derived posture: the index is always
    rebuildable from the git-tracked text files.

    **Multi-vault + config-sourced ``shared``.** When a
    ``config.json`` exists & loads, reindex spans **all** configured vault roots
    and derives each vault's ``shared`` from its config flag (``shared_roots`` —
    the set of roots marked ``shared: true``), then stamps the ``config.json``
    mtime into the index meta so ``search`` can warn when the index is older than
    the config. With NO config it keeps today's single-active-vault behavior with
    the owned=first-vault heuristic (vanilla). The target is always config-resolved
    — there is no path override, since the active vault is fully determined by
    scoping.
    """
    count, error = run_reindex()
    if error is not None:
        print(f"error: reindex failed: {error}", file=sys.stderr)
        return 1
    print(count)
    return 0


def run_reindex() -> "tuple[int | None, str | None]":
    """Rebuild the derived index; return ``(row_count, None)`` or ``(None, error)``.

    The reusable core of :func:`cmd_reindex`, also called by ``lore sync`` after a
    pull lands records this device has never indexed. Prints nothing — each caller
    owns its own reporting, because the same rebuild is a whole command in one
    place and a footnote to a sync in the other.

    Holds EVERY configured vault's write lock (sorted-path order) across the
    rebuild — lore's one named all-vault serialization point. See the comment at
    the acquisition for why it is the only one and why it comes first.
    """
    from .. import locking
    from ..record import store as record_store_mod
    from ..search import index as index_store_mod
    from ..vault import config as vault_config_mod

    loaded = _load_vault_config()

    if loaded is not None:
        config_path, vaults = loaded
        # A configured-but-absent vault root must never reach the locking
        # helper: `_flock` mkdir's the lock file's parent as a side effect of
        # taking the lock, which would silently materialize a directory (and
        # a `.lore.lock`) the user never provisioned. Filtering here mirrors
        # `sync.py`'s `vault.exists()` guard.
        vaults = [v for v in vaults if Path(v.path).exists()]
        vault_roots = [str(v.path) for v in vaults]
        shared_roots = {
            str(v.path) for v in vaults if vault_config_mod.is_shared(v)
        }
    else:
        config_path = None
        single = Path(vault_config_mod.resolve_active_vault())
        vault_roots = [str(single)]
        shared_roots = None

    try:
        # THE global serialization point. The rebuild truncates the index and
        # rescans every vault from disk, so a write that lands between the
        # truncate and its vault's rescan vanishes from the index. Every
        # configured vault's write lock is therefore held for the whole rebuild.
        #
        # Locks BEFORE the index transaction, deliberately: the rebuild must not
        # be holding a SQLite transaction while it waits on a vault flock, or it
        # would deadlock against a writer holding that flock and waiting on the
        # index. ``cli.record`` does open its index connection before taking the
        # vault flock; that inversion is benign because opening holds no lock past
        # its own return (first-use provisioning commits its ``BEGIN IMMEDIATE``)
        # and every record statement runs after the flock is held — so no SQLite
        # lock is ever owned by a writer still waiting for a vault.
        #
        # This is the only all-vault acquisition in lore. It is bounded by local
        # disk-scan time, and a writer that waits on it past the notice threshold
        # says so on stderr rather than looking stuck.
        with locking.vault_write_locks(*vault_roots), \
                record_store_mod.index_transaction() as conn:
            count = index_store_mod.rebuild(
                vault_roots, conn, shared_roots=shared_roots
            )
            # Stamp the config mtime so search can flag a stale-config index.
            # No config → no stamp.
            if config_path is not None:
                try:
                    mtime = config_path.stat().st_mtime
                    index_store_mod.set_meta(conn, "config_mtime", repr(mtime))
                except OSError:
                    pass
            conn.commit()
    except Exception as exc:
        return None, str(exc)
    return count, None


def add_areas_subparsers(sub) -> None:
    """Register the ``areas`` and ``reindex`` command parsers."""
    p_areas = sub.add_parser(
        "areas",
        help="List all vault areas (names, one-liners, keywords) on demand",
    )
    p_areas.set_defaults(func=cmd_areas)

    p_reindex = sub.add_parser(
        "reindex",
        help="Rebuild the lore search index",
    )
    p_reindex.set_defaults(func=cmd_reindex)
