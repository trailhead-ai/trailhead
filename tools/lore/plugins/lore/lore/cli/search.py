"""``lore search`` — the KQL-subset query facade over the derived index."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .common import _resolve_config_path, _resolve_groups_dir


def cmd_search(args) -> int:
    """Run a KQL-subset query over the global derived index (the search facade).

    Parses → compiles → executes ONE read-only SQL query → renders. ``search`` is
    a PURE READER — it never writes, mutates, repairs, or rebuilds the index, and
    never bumps ``last-referenced-at``.

    Error hygiene: a parse/compile error prints ``lore search: <msg>`` on stderr
    (the reflected query token already XML-body-escaped by ``search.run_search``)
    and exits non-zero — never a traceback, never a silent empty result. A VALID
    query with zero matches prints an empty-result banner and exits 0.
    """
    from ..search import engine as search_mod
    from ..vault import config as vault_config_mod
    from ..vault import layers as layers_mod

    # Vault root(s) for the coarse staleness stat (best-effort; never fail search).
    # Search always spans the resolved layers (personal + any shared/group vaults);
    # it never takes an arbitrary path — vault access stays CLI-resolved.
    vault_roots: list[str] = []
    try:
        try:
            all_layers = layers_mod.resolve_layers(
                cwd=Path.cwd(),
                groups_dir=_resolve_groups_dir(),
            )
            vault_roots = [str(lay.root.resolve()) for lay in all_layers]
        except Exception:
            vault_roots = []
        if not vault_roots:
            vault_roots = [str(Path(vault_config_mod.resolve_active_vault()))]
    except Exception:
        vault_roots = []

    # Config-freshness signal: pass the current config.json mtime so
    # run_search can warn when the index was built against an older config. No
    # config → None → no config-staleness note (vanilla).
    config_mtime = None
    config_path = _resolve_config_path()
    if config_path.exists():
        try:
            config_mtime = config_path.stat().st_mtime
        except OSError:
            config_mtime = None

    text, code = search_mod.run_search(
        args.query,
        env=dict(os.environ),
        vault=None,
        vault_roots=vault_roots or None,
        limit=args.limit,
        as_json=getattr(args, "json", False),
        config_mtime=config_mtime,
    )
    if code == 0:
        print(text)
    else:
        print(text, file=sys.stderr)
    return code


def add_search_subparser(sub) -> None:
    """Register the ``search`` command parser."""
    p_search = sub.add_parser(
        "search",
        help="Query the global lore index with the KQL-subset facade",
    )
    p_search.add_argument(
        "query",
        help="KQL-subset query string (e.g. 'kind:spec and area:penny')",
    )
    p_search.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit structured JSON instead of the human banner",
    )
    p_search.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of results (default 20)",
    )
    p_search.set_defaults(func=cmd_search)
