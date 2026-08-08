"""``lore task list`` — flat listing of every task record in a named vault.

Read-only, built directly on :func:`guards.load_task_sidecars` (the same
source-of-truth sidecar load the graph guards and ``lore task graph`` use)
plus :func:`graph.children` for the containment back-edge. It never touches
the index and never writes.
"""
from __future__ import annotations

from . import graph as graph_mod
from . import guards as guards_mod


def list_tasks(
    vault_root: str, *, statuses: list[str] | None = None, runnable_only: bool = False
) -> list[dict]:
    """Return every task in *vault_root* as a flat list of listing entries.

    Each entry carries exactly seven keys: ``name``, ``status``,
    ``created-at``, ``updated-at``, ``parent``, ``depends-on``, ``children``.
    ``parent`` is ``None`` (never simply absent) when the task declares no
    parent; ``depends-on``/``children`` default to ``[]``. ``children`` is
    derived from ``parent`` back-edges (:func:`graph.children`) — containment,
    not ``depends-on``.

    Sorted oldest-first by ``created-at``, with a name tiebreak on equal (or
    missing) timestamps, so the order is total and deterministic even over a
    sidecar that omits the field.

    ``statuses``, when given, keeps only tasks whose ``status`` is a member —
    the CLI's repeatable ``--status`` filter. A malformed sidecar is already
    excluded upstream by :func:`guards.load_task_sidecars` (skipped, not
    raised), so this function never sees it.

    ``runnable_only``, when true, keeps only tasks in :func:`graph.runnable`'s
    result (``ready`` status with every ``depends-on`` target ``done``;
    parent/child containment is untouched — a runnable parent still lists).
    Mutually exclusive with ``statuses`` at the CLI layer, not enforced here.
    """
    graph = guards_mod.load_task_sidecars(vault_root)
    wanted = set(statuses) if statuses else None
    runnable_names = set(graph_mod.runnable(graph)) if runnable_only else None

    entries: list[dict] = []
    for name, sidecar in graph.items():
        status = sidecar.get("status")
        if wanted is not None and status not in wanted:
            continue
        if runnable_names is not None and name not in runnable_names:
            continue
        entries.append({
            "name": name,
            "status": status,
            "created-at": sidecar.get("created-at"),
            "updated-at": sidecar.get("updated-at"),
            "parent": sidecar.get("parent"),
            "depends-on": list(sidecar.get("depends-on") or []),
            "children": graph_mod.children(graph, name),
        })

    entries.sort(key=lambda e: (e["created-at"] or "", e["name"]))
    return entries
