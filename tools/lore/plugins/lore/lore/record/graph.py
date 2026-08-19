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

Design-dependency section: this module also carries the pure design side of a
second, unrelated dependency grammar — the ``depends-on`` sidecar entries a
``spec``/``adr`` record may carry, distinct from the task graph above. An entry
is a qualified id, ``kind/name``, optionally staged with ``@<status>`` (e.g.
``spec/foo@ready``); :func:`parse_dependency` splits one entry, never raising,
and :func:`evaluate_dependencies` reads a ``{qualified_id: sidecar}`` design
graph to report met/unmet with a reason per entry, in the order given. This
section imports :mod:`lore.record.model` for ``STATUS_VOCAB`` (to derive each
kind's success chain) — a one-directional dependency; ``model`` imports nothing
from here. The purity contract is unchanged: no file reads, no index, nothing
here ever raises on malformed input — a design graph missing a target, or a
target whose status is not the record's own true state, is just data.

The design graph also gets its own stage-blind cycle detection and reverse
scan — :func:`find_design_dependency_cycle` and :func:`design_dependents` —
mirroring :func:`find_dependency_cycle` and :func:`dependents` from the task
section above, keyed by qualified id instead of bare name and with any
``@stage`` tail stripped before a target is compared. Both entry points share
the same underlying DFS (:func:`_find_cycle_over_edges`) rather than
duplicating it; the task entry point's signature and behavior are unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from . import model as model_mod

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
    return _find_cycle_over_edges(depends_on_edges(graph), start)


def _find_cycle_over_edges(
    edges: dict[str, list[str]], start: str | None
) -> list[str] | None:
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


# ---------------------------------------------------------------------------
# design dependency grammar + evaluator (spec/adr ``depends-on``)
# ---------------------------------------------------------------------------

#: Kinds whose ``depends-on`` entries this grammar parses. ``task`` deliberately
#: stays outside it — task edges are bare names, owned by the section above.
DESIGN_KINDS: frozenset[str] = frozenset({"spec", "adr"})

#: Statuses that mean a design record failed rather than progressed, shared
#: across every design kind. A target at one of these is never "met", no
#: matter what stage a dependency entry asked for.
FAILURE_STATUSES: frozenset[str] = frozenset({"superseded", "dropped"})

#: Per-kind success chain, derived from :data:`model.STATUS_VOCAB` by dropping
#: the trailing failure statuses — never hand-listed, so a ``STATUS_VOCAB`` edit
#: moves the chain automatically. Order is preserved from ``STATUS_VOCAB``.
SUCCESS_CHAINS: dict[str, tuple[str, ...]] = {
    kind: tuple(status for status in model_mod.STATUS_VOCAB[kind] if status not in FAILURE_STATUSES)
    for kind in DESIGN_KINDS
}

#: :func:`parse_dependency` error codes — the closed public set
#: :attr:`ParsedDependency.error` is drawn from, one per rejected entry
#: shape. Public because the guard policy branches on them by name.
ERR_NO_KIND = "no-kind"
ERR_TASK_KIND = "task-kind"
ERR_UNKNOWN_KIND = "unknown-kind"
ERR_UNKNOWN_STAGE = "unknown-stage"
ERR_FAILURE_STAGE = "failure-stage"

#: :func:`evaluate_dependencies` reason codes for an unmet dependency.
REASON_MISSING = "missing"
REASON_SHORT_OF_STAGE = "short-of-stage"
REASON_TARGET_FAILED = "target-failed"


class ParsedDependency(NamedTuple):
    """One parsed ``depends-on`` entry from a spec/adr sidecar.

    ``kind``/``name`` are populated whenever the entry could be split on a
    ``/`` (even when that split is itself the rejection, e.g. an unknown
    kind), so a caller reporting the rejection still has something to name.
    ``stage`` is the ``@``-tail verbatim, or ``None`` when the entry carried
    none — including on error, and including on an otherwise-valid unqualified
    entry (never normalized to the chain end; that normalization is the
    evaluator's job, not the parser's). ``error`` is ``None`` for an accepted
    entry, else one of the closed :data:`ERR_NO_KIND` / :data:`ERR_TASK_KIND` /
    :data:`ERR_UNKNOWN_KIND` / :data:`ERR_UNKNOWN_STAGE` / :data:`ERR_FAILURE_STAGE`
    set, one code per rejected shape.
    """

    kind: str | None
    name: str | None
    stage: str | None
    error: str | None


def parse_dependency(entry: object) -> ParsedDependency:
    """Split one ``depends-on`` entry into ``kind/name[@stage]``, never raising.

    Accepts ``kind/name`` and ``kind/name@stage`` where ``kind`` is a design
    kind (``spec``/``adr``) and, when given, ``stage`` is a status on that
    kind's success chain. Rejects, with a distinct ``error`` code each:

    - a bare name with no ``/`` (``no-kind``) — also the catch-all for any
      entry too malformed to split (not a string, an empty kind or name half);
    - a ``task/`` target (``task-kind``) — task edges are a separate grammar,
      never this one;
    - any other kind prefix outside :data:`DESIGN_KINDS` (``unknown-kind``);
    - a ``@stage`` absent from the target kind's ``STATUS_VOCAB`` entirely
      (``unknown-stage``);
    - a ``@stage`` that names a failure status (``failure-stage``) — a
      dependency cannot require a status that means the target failed.
    """
    if not isinstance(entry, str) or "/" not in entry:
        name = entry if isinstance(entry, str) else None
        return ParsedDependency(None, name, None, ERR_NO_KIND)
    kind_part, _, rest = entry.partition("/")
    name_part, has_stage, stage_part = rest.partition("@")
    stage = stage_part if has_stage else None
    if not kind_part or not name_part:
        return ParsedDependency(kind_part or None, name_part or None, stage, ERR_NO_KIND)
    if kind_part == "task":
        return ParsedDependency(kind_part, name_part, stage, ERR_TASK_KIND)
    if kind_part not in DESIGN_KINDS:
        return ParsedDependency(kind_part, name_part, stage, ERR_UNKNOWN_KIND)
    if stage is not None:
        if stage not in model_mod.STATUS_VOCAB.get(kind_part, ()):
            return ParsedDependency(kind_part, name_part, stage, ERR_UNKNOWN_STAGE)
        if stage in FAILURE_STATUSES:
            return ParsedDependency(kind_part, name_part, stage, ERR_FAILURE_STAGE)
    return ParsedDependency(kind_part, name_part, stage, None)


class DependencyStatus(NamedTuple):
    """The met/unmet verdict for one ``depends-on`` entry against a design graph.

    ``reason`` is always a non-empty, human-readable string; ``reason_code`` is
    ``None`` when ``met`` is ``True`` and otherwise one of ``"missing"``
    (no target at that qualified id), ``"short-of-stage"`` (the target exists
    but its status is earlier on the chain than required, or is not on the
    chain/failure-set at all — a malformed sidecar reads unmet, conservatively),
    or ``"target-failed"`` (the target's status is a failure status, regardless
    of what stage the entry asked for).
    """

    kind: str | None
    name: str | None
    stage: str | None
    met: bool
    reason: str
    reason_code: str | None


def evaluate_dependencies(
    design_graph: dict[str, dict], entries: Sequence[str]
) -> list[DependencyStatus]:
    """Evaluate each ``depends-on`` *entries* string against *design_graph*.

    *design_graph* is a ``{"kind/name": sidecar}`` dict — the vault's spec/adr
    sidecars, source of truth, in the same shape the task graph above takes
    (just keyed by qualified id instead of bare name). Returns one
    :class:`DependencyStatus` per entry, in the order given — never a map keyed
    by the raw entry, so duplicate entries each get their own status. An entry
    that fails :func:`parse_dependency` reads unmet with ``reason_code ==
    "missing"``, since no target can be resolved for it. Reason strings
    interpolate the target's qualified id (kind and name) exactly as they
    appear in the entry — no escaping, no charset check — a stem that reached
    the vault by any route other than the CLI's own slugifier round-trips
    unaltered; a caller rendering the reason somewhere unsafe must escape it.
    """
    return [_evaluate_one(design_graph, entry) for entry in entries]


def _evaluate_one(design_graph: dict[str, dict], entry: str) -> DependencyStatus:
    parsed = parse_dependency(entry)
    if parsed.error is not None:
        return DependencyStatus(
            parsed.kind,
            parsed.name,
            parsed.stage,
            False,
            f"{entry!r} is not a resolvable dependency ({parsed.error})",
            REASON_MISSING,
        )
    qualified_id = f"{parsed.kind}/{parsed.name}"
    target = design_graph.get(qualified_id)
    if target is None:
        return DependencyStatus(
            parsed.kind,
            parsed.name,
            parsed.stage,
            False,
            f"{qualified_id} was not found in the vault",
            REASON_MISSING,
        )
    status = target.get("status")
    if status in FAILURE_STATUSES:
        return DependencyStatus(
            parsed.kind,
            parsed.name,
            parsed.stage,
            False,
            f"{qualified_id} is {status}",
            REASON_TARGET_FAILED,
        )
    chain = SUCCESS_CHAINS[parsed.kind]
    required_stage = parsed.stage if parsed.stage is not None else chain[-1]
    if status not in chain:
        return DependencyStatus(
            parsed.kind,
            parsed.name,
            parsed.stage,
            False,
            f"{qualified_id} has status {status!r}, which is not on the {parsed.kind} success chain",
            REASON_SHORT_OF_STAGE,
        )
    if chain.index(status) < chain.index(required_stage):
        return DependencyStatus(
            parsed.kind,
            parsed.name,
            parsed.stage,
            False,
            f"{qualified_id} is at {status!r}, short of required stage {required_stage!r}",
            REASON_SHORT_OF_STAGE,
        )
    return DependencyStatus(
        parsed.kind,
        parsed.name,
        parsed.stage,
        True,
        f"{qualified_id} is at {status!r}, satisfying {required_stage!r}",
        None,
    )


# ---------------------------------------------------------------------------
# design-graph cycle/dependents — stage-blind, sharing the task DFS
# ---------------------------------------------------------------------------


def _design_deps(sidecar: dict) -> list[str]:
    """Return *sidecar*'s ``depends-on`` targets, each reduced to its qualified
    id — any ``@stage`` tail stripped, ``kind/name`` kept as given.

    Deliberately does not route through :func:`parse_dependency`: cycle
    detection and the dependents scan only need the ``kind/name`` half of an
    entry, not a validity verdict on its stage. An entry that is not even
    shaped like ``kind/name`` (no ``/``, or a ``task/`` target) still reduces
    to *something* here — it simply never matches a real node in the design
    graph, so it behaves exactly like a dangling target.
    """
    value = sidecar.get("depends-on")
    if not isinstance(value, list):
        return []
    return [item.partition("@")[0] for item in value if isinstance(item, str)]


def design_depends_on_edges(design_graph: dict[str, dict]) -> dict[str, list[str]]:
    """Return ``{qualified_id: [qualified_id, …]}`` with every ``@stage`` tail
    stripped — the design-graph analogue of :func:`depends_on_edges`."""
    return {qid: _design_deps(sidecar) for qid, sidecar in design_graph.items()}


def find_design_dependency_cycle(
    design_graph: dict[str, dict], start: str | None = None
) -> list[str] | None:
    """Return a stage-blind ``depends-on`` cycle over *design_graph*, or ``None``.

    *design_graph* is keyed by qualified id (``kind/name``, e.g. ``"spec/foo"``)
    the same shape :func:`evaluate_dependencies` takes. A dependency entry's
    ``@stage`` tail is stripped before comparison, so ``spec/a@ready`` and
    ``spec/a`` reduce to the same node and a cycle through differently-staged
    edges is still found. Kind is never stripped — ``spec/foo`` and ``adr/foo``
    stay distinct nodes throughout.

    Shares the same DFS as :func:`find_dependency_cycle`, over
    :func:`design_depends_on_edges` instead of :func:`depends_on_edges`, so the
    two entry points differ only in how an edge target is reduced to a node
    id. Same *start*-scoping, same misattribution guard, same closed-path
    shape, and the same dangling-target-is-a-leaf behavior — see
    :func:`find_dependency_cycle` for the full contract.
    """
    return _find_cycle_over_edges(design_depends_on_edges(design_graph), start)


def design_dependents(design_graph: dict[str, dict], qualified_id: str) -> list[str]:
    """Return the qualified ids whose ``depends-on`` contains *qualified_id*,
    sorted — the design-graph analogue of :func:`dependents`.

    Matches on the full qualified id with any ``@stage`` tail stripped from
    each candidate edge, so an entry like ``spec/foo@ready`` still counts as a
    dependent of ``spec/foo``. Kind is part of the match — ``spec/foo``'s
    dependents never include an edge that targets ``adr/foo``.
    """
    return sorted(
        qid for qid, sidecar in design_graph.items() if qualified_id in _design_deps(sidecar)
    )
