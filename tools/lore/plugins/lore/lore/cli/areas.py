"""``lore areas`` / ``lore reindex`` — the area menu + derived-index rebuild."""
from __future__ import annotations

import sys
from pathlib import Path

from .common import _load_vault_config, _resolve_all_vaults_and_shared


def cmd_areas(args) -> int:
    """Print the full area menu (names, one-liners, keywords) to stdout.

    Spans **every configured non-shared vault**, not just the `default`-scope
    vault: resolves the whole-install vault set (and its shared-path set, from
    one `config.json` read) via `_resolve_all_vaults_and_shared` (built on the
    same enumeration `lore sync`/`lore status` use), drops any `shared: true`
    vault (the area menu is personal-scoped — a shared vault's content only
    reaches context through the explicitly-delimited `lore search` path), and
    drops any configured-but-absent root. `build_area_map_multi` merges the
    per-vault menus, deduping same-named areas config-order-first-wins, and
    already isolates a single root's `build_area_map` failure from the rest.

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
        all_vaults, shared_paths, error = _resolve_all_vaults_and_shared()
        roots = [
            path
            for _name, path in all_vaults
            if str(path.resolve()) not in shared_paths and path.exists()
        ]

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
