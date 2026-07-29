"""``lore areas`` / ``lore reindex`` — the area menu + derived-index rebuild."""
from __future__ import annotations

import sys
from pathlib import Path

from .common import _load_vault_config


def cmd_areas(args) -> int:
    """Print the full area menu (names, one-liners, keywords) to stdout.

    Exit code is always 0 — must never fail a session.
    Prints a one-line stderr signal and a degraded stdout "no areas" line when
    the resolved vault path does not exist on disk, or when build_area_map
    raises. Empty or absent areas/ dir prints a friendly "no areas" line.
    """
    from ..search import area_map as area_map_mod
    from ..vault import config as vault_config_mod

    _NO_AREAS_LINE = "No areas defined yet."

    try:
        vault = Path(vault_config_mod.resolve_active_vault())
        if not vault.exists():
            raise FileNotFoundError(vault)
    except Exception as exc:
        print(f"lore areas: could not resolve vault ({exc})", file=sys.stderr)
        print(_NO_AREAS_LINE)
        return 0

    try:
        entries = area_map_mod.build_area_map(vault)
    except Exception as exc:
        print(f"lore areas: could not build area map ({exc})", file=sys.stderr)
        print(_NO_AREAS_LINE)
        return 0

    menu = area_map_mod.render_area_menu(entries)
    if menu:
        print(menu)
    else:
        print(_NO_AREAS_LINE)
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
    """
    from ..record import store as record_store_mod
    from ..search import index as index_store_mod
    from ..vault import config as vault_config_mod

    loaded = _load_vault_config()

    if loaded is not None:
        config_path, vaults = loaded
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
        with record_store_mod.index_transaction() as conn:
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
