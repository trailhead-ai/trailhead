"""``lore pipeline`` — the cross-vault board of in-flight design work.

Read-only. Nothing is written, nothing is claimed, and the index is never
touched: the board is derived from the configured vaults' sidecars on every
invocation.

**What the board carries.** One lineage per in-flight design root: an adr with
the non-terminal specs whose own-vault ``related: adr=`` edges point at it,
plus the singletons — a spec whose edge resolves to nothing, and an ``open``
task routed to brainstorm. Membership is recomputed from the sidecars on every
invocation and stored nowhere.

**Vault set.** The configured vaults and the set of ``shared: true`` names both
come from a single :func:`common._resolve_all_vaults_and_shared` call, so the
two views cannot disagree and the shared filter cannot fail open. Camp-group
layer resolution is a different notion of "shared" and is deliberately not on
this path.

**Exit-code contract.** Nonzero means the board could not be derived at all,
never that some part of it is missing:

  - ``config.json`` present but unparseable — no board is rendered, since the
    vault list that call falls back to is a synthetic floor, not the operator's
    configured set. A **missing** ``config.json`` is not this case: that is a
    vanilla install, and the board renders over the floor vault and exits 0.
  - An unknown ``--vault`` name — refused before any vault directory is opened,
    so a typo never renders a plausible-looking board over the wrong set.
  - No configured vault readable at all — the board still renders, carrying
    every vault's error marker, but the exit is nonzero.

The walk and the render are guarded as a whole: the walk answers every
enumerated failure with a marker rather than a raise, and this guard covers the
unenumerated rest, because a traceback is never this command's failure shape.

**A zero exit does not mean the board is complete.** A vault that could not be
read degrades the board rather than blanking it, so a consumer — a script, or
an agent parsing ``--json`` — must inspect each ``vaults[]`` entry's ``error``
before trusting ``tiers``. This is the deliberate trade: one broken vault must
not cost the operator the other three.

**``flags`` is the authoritative gating signal.** A record's ``depends-on``
objects carry the evaluator's per-entry detail, and their ``met`` field is
three-valued: ``true``, ``false``, and ``null`` for a routed task's entries,
which this surface projects without evaluating because task edges are a
different grammar. A consumer testing ``met`` for falsiness therefore reads a
routed task as blocked when nothing is blocking it. The record's ``flags``
array is the signal to branch on: a record that anything blocks carries
``gated``, and a record that carries ``gated`` is always still on the board,
with the reason beside it.
"""
from __future__ import annotations

import sys

from ..pipeline import render as render_mod
from ..pipeline import walk as walk_mod
from .common import _resolve_all_vaults_and_shared


def cmd_pipeline(args) -> int:
    """``lore pipeline [--vault NAME ...] [--json]`` — render the board."""
    vaults, shared_names, error = _resolve_all_vaults_and_shared()
    if error is not None:
        print(f"lore: {error}", file=sys.stderr)
        return 1

    selected = getattr(args, "vault", None)
    if selected:
        configured = {name for name, _ in vaults}
        unknown = sorted(set(selected) - configured)
        if unknown:
            named = ", ".join(repr(name) for name in unknown)
            print(f"lore: no configured vault named {named}", file=sys.stderr)
            return 1
        wanted = set(selected)
        vaults = [(name, path) for name, path in vaults if name in wanted]

    try:
        walks = walk_mod.walk_vaults(vaults, shared_names)
        render_mod.emit(walks, as_json=bool(getattr(args, "json", False)))
    except Exception as exc:
        print(f"lore: could not render the pipeline board ({exc})", file=sys.stderr)
        return 1

    if walks and all(walk.error is not None for walk in walks):
        print("lore: no configured vault could be read", file=sys.stderr)
        return 1
    return 0


def add_pipeline_subparser(sub) -> None:
    """Register the ``pipeline`` command parser."""
    parser = sub.add_parser(
        "pipeline",
        help="Cross-vault board of in-flight design work (read-only)",
    )
    parser.add_argument(
        "--vault", action="append", default=None, metavar="NAME",
        help="Walk only this configured vault (repeatable). An unknown name is "
             "refused before any vault is opened.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit the board as a JSON envelope. A zero exit does not mean the "
             "board is complete: inspect every vaults[] entry's error field "
             "before trusting tiers. A depends-on entry's met field is "
             "per-entry evaluator detail and is null on a routed task; a "
             "record's flags is the authoritative gating signal.",
    )
    parser.set_defaults(func=cmd_pipeline)
