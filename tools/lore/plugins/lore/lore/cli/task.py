"""``lore task`` — read-only task-graph views.

A thin renderer over ``record/graph.py`` (the pure edge-map/query module) and
``record/guards.py``'s task-sidecar loader + ``cli/record.py``'s multi-vault
record locator — this module reuses all three rather than re-deriving graph
traversal or vault resolution. It never writes; it is the read counterpart to
the ``--parent``/``--depends-on`` write-time guards in ``record/guards.py``.
"""
from __future__ import annotations

import sys

from ..record import guards as guards_mod
from .record import _find_current_record_location


def _render_task_graph(graph: dict, root: str) -> str:
    """Render the containment subtree rooted at *root* as indented plain text.

    One line per task, indented by containment depth: ``NAME [status]``, with
    a trailing `` (runnable)`` marker and/or `` depends-on: dep (status), …``
    suffix when applicable. Traversal follows ``parent`` edges via
    :func:`graph.children` (root's descendants only) — a task's
    ``depends-on`` targets are rendered as an edge annotation on its own line,
    never as separate subtree nodes, even when the target also happens to live
    in this vault's task graph.

    The ``(runnable)`` marker is restricted to *leaves of the whole graph*
    (:func:`graph.leaves`), not merely leaves of this subtree — a node
    that is itself ``ready`` with every dependency ``done`` but has children
    of its own is never marked runnable (only leaf tasks are directly
    actionable).

    A defensive ``visited`` guard prevents infinite recursion if the on-disk
    sidecars somehow encode a ``parent`` cycle (write-time guards prevent this
    in the normal path, but this is a read-only view over whatever is on
    disk).
    """
    from ..record import graph as graph_mod

    runnable_leaves = set(graph_mod.runnable(graph)) & set(graph_mod.leaves(graph))
    lines: list[str] = []
    visited: set[str] = set()

    # Build the parent -> children adjacency once, up front, instead of calling
    # graph_mod.children (a full graph scan + sort) per node visited — an O(V^2)
    # pattern for a V-node subtree. Inverting parent_edges() and sorting each
    # bucket once reproduces children()'s exact ordering per parent.
    children_map: dict[str, list[str]] = {}
    for child, parent in graph_mod.parent_edges(graph).items():
        children_map.setdefault(parent, []).append(child)
    for kids in children_map.values():
        kids.sort()

    def _status(name: str) -> str:
        return graph.get(name, {}).get("status", "unknown")

    def _depends_on_suffix(name: str) -> str:
        deps = graph.get(name, {}).get("depends-on") or []
        if not deps:
            return ""
        rendered = ", ".join(f"{dep} ({_status(dep)})" for dep in deps)
        return f" depends-on: {rendered}"

    def _walk(name: str, depth: int) -> None:
        if name in visited:
            return
        visited.add(name)
        marker = " (runnable)" if name in runnable_leaves else ""
        lines.append(f"{'  ' * depth}{name} [{_status(name)}]{marker}{_depends_on_suffix(name)}")
        for child in children_map.get(name, []):
            _walk(child, depth + 1)

    _walk(root, 0)
    return "\n".join(lines)


def _cmd_task_graph(args) -> int:
    """``lore task graph NAME`` — print the containment subtree rooted at NAME.

    NAME is ordinarily a bare task name (resolved as ``task/NAME``); it may
    also be given as an explicit ``<kind>/<name>`` (the same grammar
    ``record show`` uses) so a caller who names the wrong kind (e.g. an
    ``area`` record) gets a specific "not a task" error rather than a
    misleading "no task named" one. Resolution scans every configured vault
    (:func:`record._find_current_record_location`), matching how every other
    read/update path locates a record without routing flags.
    """
    from ..record import store as record_store_mod

    name = getattr(args, "name", None)
    record_id = name if "/" in name else f"task/{name}"

    try:
        location = _find_current_record_location(record_id)
    except record_store_mod.RecordNotFoundError:
        print(f"error: no task named {name!r}", file=sys.stderr)
        return 1
    except record_store_mod.InvalidRecordIdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if location.kind != "task":
        print(
            f"error: {location.record_id} is a {location.kind!r} record, not a task",
            file=sys.stderr,
        )
        return 1

    graph = guards_mod.load_task_sidecars(location.vault_root)
    print(_render_task_graph(graph, location.name))
    return 0


def cmd_task(args) -> int:
    """Dispatch ``lore task <action>`` — today, only ``graph``."""
    action = getattr(args, "task_action", None)
    if action == "graph":
        return _cmd_task_graph(args)
    print(
        f"lore task: unknown action {action!r}. Use 'lore task graph'.",
        file=sys.stderr,
    )
    return 1


def add_task_subparser(sub) -> None:
    """Register the ``task`` command parser and its ``graph`` action."""
    p_task = sub.add_parser(
        "task",
        help="Read-only task-graph views (containment + depends-on)",
    )
    p_task_sub = p_task.add_subparsers(dest="task_action", required=True)

    p_task_graph = p_task_sub.add_parser(
        "graph",
        help="Render the containment subtree + depends-on edges rooted at NAME",
    )
    p_task_graph.add_argument(
        "name",
        metavar="NAME",
        help="Task name to root the graph at (or an explicit <kind>/<name>)",
    )
    p_task_graph.set_defaults(func=cmd_task)
