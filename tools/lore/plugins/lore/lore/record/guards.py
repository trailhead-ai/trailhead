"""Task-graph guard policy — orchestrates the pure graph checks over a write.

The decision layer between the on-disk task sidecars and the pure algorithms in
:mod:`graph`. For a create/update/delete it loads the vault's task sidecars,
overlays the in-flight record, decides WHICH checks run for the operation's shape,
and classifies each outcome as a blocking error (nothing is written) or a
non-blocking notice (printed only on a successful op). The cycle/loop/containment
math lives in :mod:`graph` (pure, dict-in/dict-out); this module owns the
vault I/O and the error-vs-notice policy — a no-op for every non-``task`` kind.

Guard-message shape: every line — blocking error, non-blocking warning, ritual
reminder — is formatted through :func:`graph.format_guard_message` so agents
parse one machine-parseable ``graph-guard [<guard>]: <message>`` shape off stderr.

Design-graph loader: :func:`load_design_sidecars` reads the vault's spec/adr
sidecars into the ``{"kind/name": sidecar}`` shape :mod:`graph`'s design-side
functions consume — the read half of the same source-of-truth contract
:func:`load_task_sidecars` gives the task graph.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import graph as graph_mod
from . import store as record_store_mod

# A ``## Flow-out`` markdown heading (the completion-ritual section). Matched
# loosely — any heading level ≥ 2, case-insensitive — so the reminder fires only
# when the parent body genuinely lacks the knowledge-flow-out checklist.
_FLOW_OUT_RE = re.compile(r"(?im)^\s*#{2,}\s+flow-out\b")


def body_has_flow_out(body: str) -> bool:
    """True iff *body* contains a ``## Flow-out`` section heading."""
    return bool(_FLOW_OUT_RE.search(body or ""))


def load_task_sidecars(vault_root: str) -> dict[str, dict]:
    """Read every task sidecar under ``<vault_root>/task/`` → ``{name: sidecar}``.

    The source-of-truth read for the graph guards — sidecars, never the index.
    A malformed or unreadable sidecar is skipped (best-effort: the guard degrades
    to not seeing that node rather than failing the whole write).
    """
    task_dir = Path(vault_root) / "task"
    graph: dict[str, dict] = {}
    if not task_dir.is_dir():
        return graph
    for sidecar_path in task_dir.glob("*.json"):
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            graph[sidecar_path.stem] = data
    return graph


def load_design_sidecars(vault_root: str) -> dict[str, dict]:
    """Read every spec/adr sidecar under ``<vault_root>/{spec,adr}/`` →
    ``{"kind/name": sidecar}`` — the design-graph analogue of
    :func:`load_task_sidecars`.

    Directory-scoped the same way its task counterpart is: each of
    :data:`graph.DESIGN_KINDS` gets its own ``<vault_root>/<kind>/*.json`` glob,
    keyed by ``<kind>/<stem>`` so a ``spec/foo`` and an ``adr/foo`` never
    collide — the collision qualified-id keying exists to prevent. A missing
    ``spec/`` or ``adr/`` directory contributes nothing; a malformed or
    unreadable sidecar is skipped (best-effort, same as the task loader).
    """
    graph: dict[str, dict] = {}
    for kind in graph_mod.DESIGN_KINDS:
        kind_dir = Path(vault_root) / kind
        if not kind_dir.is_dir():
            continue
        for sidecar_path in kind_dir.glob("*.json"):
            try:
                data = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                graph[f"{kind}/{sidecar_path.stem}"] = data
    return graph


def confine_edge_reference(value: str, vault_root: str) -> str | None:
    """Return a guard-error string if *value* is an unsafe task reference, else None.

    ``--parent``/``--depends-on`` values are record names, so they flow through the
    SAME name-resolution/confinement guard every RECORD_ID-bearing op uses
    (:func:`record_store.confine_record_id`): a ``..`` segment, absolute
    component, NUL byte, or empty/degenerate segment is rejected before the value
    is ever written. Existence is deliberately NOT checked — referential integrity
    is not enforced (a dangling edge is valid, per the record model's shape-only
    contract).
    """
    if not value:
        return graph_mod.format_guard_message("edge-reference", "empty task reference")
    try:
        record_store_mod.confine_record_id(f"task/{value}", vault_root)
    except record_store_mod.InvalidRecordIdError as exc:
        return graph_mod.format_guard_message(
            "edge-reference", f"unsafe task reference {value!r}: {exc}"
        )
    return None


def evaluate_task_guards(
    *,
    kind: str,
    name: str,
    sidecar: dict,
    body: str,
    vault_root: str,
    status_set: str | None,
    deleting: bool = False,
) -> tuple[list[str], list[str]]:
    """Evaluate the task graph guards for a create/update/delete.

    Returns ``(errors, notices)``:

      - ``errors`` block the operation (nothing is written): a ``depends-on``
        cycle, a ``parent`` ancestor loop, an unsafe edge reference, or a
        parent-completion violation (``--status done`` with non-terminal
        children). Each is a machine-parseable ``graph-guard [...]`` stderr line.
      - ``notices`` are non-blocking, printed only on a successful op: the
        dependent-warning (a depended-on task going ``dropped``/``superseded`` or
        being deleted) and the flow-out reminder (a parent completed without a
        ``## Flow-out`` section).

    A no-op — ``([], [])`` — for every non-``task`` kind, so no other kind is
    touched by any of these guards.
    """
    if kind != "task":
        return [], []

    # Delete only warns about dependents; it is never blocked.
    if deleting:
        graph = load_task_sidecars(vault_root)
        deps = graph_mod.dependents(graph, name)
        notices: list[str] = []
        if deps:
            notices.append(
                graph_mod.format_guard_message(
                    "dependents",
                    f"task {name!r} deleted but still depended on",
                    offenders=deps,
                )
            )
        return [], notices

    errors: list[str] = []

    # Edge references are confined before the graph is built — a malformed value
    # must never be written, and a traversal-shaped name must never reach disk.
    references: list[str] = []
    parent = sidecar.get("parent")
    if isinstance(parent, str):
        references.append(parent)
    deps_field = sidecar.get("depends-on")
    if isinstance(deps_field, list):
        references.extend(d for d in deps_field if isinstance(d, str))
    for ref in references:
        msg = confine_edge_reference(ref, vault_root)
        if msg:
            errors.append(msg)
    if errors:
        return errors, []

    # The vault-wide sidecar load (and the overlay of the in-flight record onto
    # it) is deferred until a guard actually needs the graph — memoized here so
    # every guard below shares one load. A node with no outgoing parent/
    # depends-on edges can never be the entry point of a NEW cycle or ancestor
    # loop, so a plain status-only update (no references, no done/dropped/
    # superseded transition) never touches the vault-wide glob+parse at all.
    graph: dict[str, dict] | None = None

    def _graph() -> dict[str, dict]:
        nonlocal graph
        if graph is None:
            graph = load_task_sidecars(vault_root)
            graph[name] = sidecar
        return graph

    if references:
        cycle = graph_mod.find_dependency_cycle(_graph(), start=name)
        if cycle:
            errors.append(
                graph_mod.format_guard_message(
                    "depends-on-cycle",
                    f"task {name!r} would create a dependency cycle: " + " -> ".join(cycle),
                )
            )
        loop = graph_mod.find_ancestor_loop(_graph(), name)
        if loop:
            errors.append(
                graph_mod.format_guard_message(
                    "parent-loop",
                    f"task {name!r} would create a parent ancestor loop: " + " -> ".join(loop),
                )
            )

    if status_set == "done":
        open_children = graph_mod.non_terminal_children(_graph(), name)
        if open_children:
            errors.append(
                graph_mod.format_guard_message(
                    "parent-completion",
                    f"cannot set task {name!r} to done — non-terminal children remain",
                    offenders=open_children,
                )
            )

    if errors:
        return errors, []

    notices = []
    if status_set in ("dropped", "superseded"):
        deps = graph_mod.dependents(_graph(), name)
        if deps:
            notices.append(
                graph_mod.format_guard_message(
                    "dependents",
                    f"task {name!r} set to {status_set} but still depended on",
                    offenders=deps,
                )
            )
    if (
        status_set == "done"
        and graph_mod.children(_graph(), name)
        and not body_has_flow_out(body)
    ):
        notices.append(
            graph_mod.format_guard_message(
                "flow-out",
                f"task {name!r} completed without a '## Flow-out' section — "
                f"capture the knowledge flow-out",
            )
        )
    return errors, notices
