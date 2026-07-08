"""Pure task-graph model: edge maps, cycle/loop detection, containment queries.

The unified ``task`` kind carries two graph edges in its sidecar: ``depends-on``
(a ``list[str]`` of task names it is blocked by) and ``parent`` (a ``str`` task
name it hangs under). This module is the single, **pure** interpreter of those
edges — every function takes a ``{name: sidecar}`` dict (the vault's task
sidecars, source of truth) and returns plain data. It never reads files, never
touches the index, and never raises on malformed input: a missing edge target is
simply an absent node (referential integrity is *not* enforced here, matching the
record model's shape-only contract).

The CLI's create/update/delete guards load the task sidecars off disk, overlay
the in-flight record, and call into this module to decide whether a write would
introduce a ``depends-on`` cycle or a ``parent`` ancestor loop, and to list a
task's children/dependents for the completion and dependent-warning guards.

Guard-error shape: every graph guard — blocking error, non-blocking warning, and
the flow-out ritual reminder — is formatted through :func:`format_guard_message`
so all of them share one machine-parseable ``graph-guard [<guard>]: <message>``
shape on stderr (agents parse the bracketed guard tag to react programmatically).
"""

from __future__ import annotations

from collections.abc import Sequence

#: Statuses that satisfy the parent-completion guard — a child in any of these is
#: "settled" and does not block its parent from completing.
TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "dropped", "superseded"})

#: The stable prefix every graph-guard message carries (agents grep for it).
GUARD_ERROR_PREFIX = "graph-guard"


def format_guard_message(
    guard: str, message: str, offenders: Sequence[str] = ()
) -> str:
    """Format one graph-guard line in the shared machine-parseable shape.

    Shape: ``graph-guard [<guard>]: <message>`` with an optional ``: a, b``
    offender tail when ``offenders`` is non-empty. All graph guards emit through
    here so agents parse a single stable prefix plus a bracketed ``<guard>`` tag
    off stderr — regardless of whether the line is a blocking error, a
    non-blocking warning, or the flow-out reminder.
    """
    tail = ""
    if offenders:
        tail = ": " + ", ".join(offenders)
    return f"{GUARD_ERROR_PREFIX} [{guard}]: {message}{tail}"


def _deps(sidecar: dict) -> list[str]:
    value = sidecar.get("depends-on")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _parent(sidecar: dict) -> str | None:
    value = sidecar.get("parent")
    return value if isinstance(value, str) else None


# ---------------------------------------------------------------------------
# edge maps + queries
# ---------------------------------------------------------------------------


def depends_on_edges(graph: dict[str, dict]) -> dict[str, list[str]]:
    """Return ``{name: [dep, …]}`` for every node (empty list when it has none)."""
    return {name: _deps(sidecar) for name, sidecar in graph.items()}


def parent_edges(graph: dict[str, dict]) -> dict[str, str]:
    """Return ``{name: parent}`` for nodes that declare a ``parent`` (others omitted)."""
    edges: dict[str, str] = {}
    for name, sidecar in graph.items():
        parent = _parent(sidecar)
        if parent is not None:
            edges[name] = parent
    return edges


def children(graph: dict[str, dict], name: str) -> list[str]:
    """Return the names whose ``parent`` is *name*, sorted."""
    return sorted(n for n, sc in graph.items() if _parent(sc) == name)


def dependents(graph: dict[str, dict], name: str) -> list[str]:
    """Return the names whose ``depends-on`` contains *name*, sorted."""
    return sorted(n for n, sc in graph.items() if name in _deps(sc))


def leaves(graph: dict[str, dict]) -> list[str]:
    """Return the names with no children (containment leaves), sorted."""
    parented = {p for p in parent_edges(graph).values()}
    return sorted(n for n in graph if n not in parented)


def non_terminal_children(graph: dict[str, dict], name: str) -> list[str]:
    """Return *name*'s children whose status is not terminal, sorted.

    The parent-completion guard's predicate: a parent may only complete when this
    list is empty (every direct child is ``done``/``dropped``/``superseded``).
    """
    return sorted(
        c for c in children(graph, name)
        if graph[c].get("status") not in TERMINAL_STATUSES
    )


def runnable(graph: dict[str, dict]) -> list[str]:
    """Return the names that are ``ready`` with every dependency ``done``, sorted.

    A missing dependency target is treated as *not* done (conservative), so a
    ready task blocked on a dangling reference is not reported runnable.
    """
    result: list[str] = []
    for name, sidecar in graph.items():
        if sidecar.get("status") != "ready":
            continue
        if all(graph.get(dep, {}).get("status") == "done" for dep in _deps(sidecar)):
            result.append(name)
    return sorted(result)


# ---------------------------------------------------------------------------
# cycle / ancestor-loop detection
# ---------------------------------------------------------------------------


def find_dependency_cycle(
    graph: dict[str, dict], start: str | None = None
) -> list[str] | None:
    """Return a ``depends-on`` cycle as a closed path, or ``None`` if acyclic.

    Follows ``depends-on`` edges. When *start* is given, only cycles reachable
    from that node are searched (the write-guard case: only the in-flight node's
    edges changed) — and a cycle is only reported if it actually **contains**
    *start*, so a pre-existing cycle elsewhere in the graph (reachable from, but
    not passing through, *start*) is never misattributed to the in-flight write.
    The returned path repeats its entry node at both ends — e.g. ``["a", "b",
    "a"]`` for ``a → b → a`` — so a caller can render the loop verbatim.
    Dangling dependency targets are treated as edge-free leaves.
    """
    edges = depends_on_edges(graph)
    origins = [start] if start is not None else list(edges)
    visited: set[str] = set()
    for origin in origins:
        found = _dfs_cycle(origin, edges, [], set(), visited)
        if found is not None:
            if start is not None and start not in found:
                continue
            return found
    return None


def _dfs_cycle(
    node: str,
    edges: dict[str, list[str]],
    path: list[str],
    on_path: set[str],
    visited: set[str],
) -> list[str] | None:
    path.append(node)
    on_path.add(node)
    for nxt in edges.get(node, []):
        if nxt in on_path:
            return path[path.index(nxt):] + [nxt]
        if nxt not in visited:
            found = _dfs_cycle(nxt, edges, path, on_path, visited)
            if found is not None:
                return found
    on_path.discard(node)
    path.pop()
    visited.add(node)
    return None


def find_ancestor_loop(graph: dict[str, dict], start: str) -> list[str] | None:
    """Return a ``parent`` ancestor loop as a closed path, or ``None``.

    Walks the ``parent`` chain up from *start*. A revisited node closes a loop —
    ``["a", "a"]`` for a self-parent, ``["a", "b", "a"]`` for a two-level loop.
    A chain that terminates (a node with no ``parent``, including a dangling
    target absent from the graph) is loop-free.
    """
    path = [start]
    seen = {start}
    current = start
    while True:
        parent = _parent(graph.get(current, {}))
        if parent is None:
            return None
        if parent in seen:
            return path[path.index(parent):] + [parent]
        path.append(parent)
        seen.add(parent)
        current = parent
