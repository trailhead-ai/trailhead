"""Graph guard policy — orchestrates the pure graph checks over a write.

The decision layer between the on-disk record sidecars and the pure algorithms in
:mod:`graph`. For a create/update/delete it loads the vault's sidecars, overlays
the in-flight record, decides WHICH checks run for the operation's shape, and
classifies each outcome as a blocking error (nothing is written) or a
non-blocking notice (printed only on a successful op). The cycle/loop/containment
math lives in :mod:`graph` (pure, dict-in/dict-out); this module owns the vault
I/O and the error-vs-notice policy.

Two graphs, one entry point: :func:`evaluate_task_guards` polices the ``task``
graph (bare task names, ``depends-on`` plus ``parent`` containment) and
:func:`evaluate_design_guards` the spec/adr design graph (qualified
``kind/name[@stage]`` dependencies, no containment).
:func:`evaluate_graph_guards` is the dispatcher every caller uses — it routes by
kind and is a ``([], [])`` no-op for a kind that carries neither graph. No kind
carries both, so exactly one policy ever runs.

Guard-message shape: every line — blocking error, non-blocking warning, ritual
reminder — is formatted through :func:`graph.format_guard_message` so agents
parse one machine-parseable ``graph-guard [<guard>]: <message>`` shape off stderr.
A guard with a counterpart on the other graph carries a namespaced tag
(``design-depends-on-cycle`` vs ``depends-on-cycle``) so the bracketed tag alone
says which graph rejected the write.

**Output-neutralization posture.** Node ids in these messages are filename
stems, and a stem is only character-validated when its record was written
through the CLI — a record synced into a ``shared: true`` vault by git never
was. Every node id therefore reaches a message through
:func:`graph.format_node` / :func:`graph.format_node_path` (or a plain ``!r``),
never raw, on BOTH graphs. Without that, a stem carrying a newline plus a
counterfeit ``graph-guard [...]`` line would forge an extra verdict on stderr
and a stem carrying an ANSI escape would colour a terminal reading it — both
against a threat model in which agents drive this CLI and parse its stderr.

Merged record vs. supplied values: every guard judges the merged record — what
would land on disk — except the task-edge FORM check, which judges only the
``depends-on`` values the write itself supplies (``supplied_depends_on``). That
rule postdates the data it reads, so a record already holding a prefixed entry
would otherwise be frozen: every later write to it, down to a plain status
flip, would be rejected for a value that write never touched. Confinement is
never narrowed this way — an unsafe reference is unsafe whoever wrote it.

Edge confinement: :func:`confine_edge_reference` is kind-parameterized — it
confines a bare record NAME under a caller-supplied kind, never a hardcoded one.
Its ordering contract is security-relevant: an edge grammar that qualifies its
own target must be split and kind-validated before confinement runs, so a
rejection always names the part that was actually unsafe.

Design-graph loader: :func:`load_design_sidecars` reads the vault's spec/adr
sidecars into the ``{"kind/name": sidecar}`` shape :mod:`graph`'s design-side
functions consume — the read half of the same source-of-truth contract
:func:`load_task_sidecars` gives the task graph. Both are warning-dropping
views of :func:`load_kind_sidecars_with_warnings`, which is public for a
read-only caller that must REPORT what it could not read rather than degrade
silently; keeping one implementation is what keeps the kind-directory and
sidecars-never-bodies invariants from drifting between them.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import graph as graph_mod
from . import store as record_store_mod

# A ``## Flow-out`` markdown heading (the completion-ritual section). Matched
# loosely — any heading level ≥ 2, case-insensitive — so the reminder fires only
# when the parent body genuinely lacks the knowledge-flow-out checklist.
_FLOW_OUT_RE = re.compile(r"(?im)^\s*#{2,}\s+flow-out\b")

# Sidecars are small metadata, not bulk content — every sidecar across every
# configured vault measured under 1.3 KB. 1 MiB gives that real distribution
# roughly 800x headroom for legitimate growth while still refusing a
# multi-hundred-MB payload before it is ever read into memory: a size this far
# outside the observed range is read as hostile or corrupt, not as data worth
# waiting on.
_MAX_SIDECAR_BYTES = 1_048_576


def body_has_flow_out(body: str) -> bool:
    """True iff *body* contains a ``## Flow-out`` section heading."""
    return bool(_FLOW_OUT_RE.search(body or ""))


def load_kind_sidecars_with_warnings(
    vault_root: str, kind: str
) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    """Read every ``<vault_root>/<kind>/*.json`` sidecar → ``({stem: sidecar}, warnings)``.

    The single directory-scoped sidecar read in this codebase — sidecars, never
    the index, and never the ``.md`` body beside them, so every value returned
    is one atomically-written file's whole contents rather than a pair that a
    concurrent vault git-sync could tear apart.

    Nothing here raises. A missing kind directory contributes nothing at all
    (an absent kind is not a fault). Every other failure — an unlistable
    directory, an unreadable file, a file over :data:`_MAX_SIDECAR_BYTES`,
    JSON nested deep enough to exceed Python's recursion limit, invalid JSON,
    or JSON that is not an object — contributes one
    ``(vault_relative_path, message)`` warning and no entry, so a caller that
    wants to report the loss can, while a caller that only wants the nodes
    degrades to not seeing that one node. This is a walked read across every
    configured vault, potentially run by every teammate on content none of
    them authored, so a single hostile or corrupt file must cost only itself
    — never the read as a whole.

    A file that vanishes between the directory listing and the open (the torn
    read a vault git-sync pull produces) surfaces here as an unreadable-file
    warning, never as an exception out of the loop — one lost file never costs
    the directory its remaining records.

    An oversized file is caught on its ``stat`` alone, before any read: the
    size check is a prevention, not a recovery, so a multi-hundred-MB sidecar
    never enters memory in the first place. A file that stays under that
    ceiling but is pathologically *shaped* — JSON nested far deeper than any
    legitimate sidecar ever would be — is still read, and its parse is
    guarded against :class:`RecursionError` specifically: Python's recursive-
    descent JSON decoder walks one stack frame per nesting level, and that is
    the exception it raises when the interpreter's recursion limit is hit.
    :class:`MemoryError` is deliberately NOT caught alongside it — recovering
    from it is unsafe in general (the interpreter may already be too low on
    memory for the warning append or the next loop iteration to succeed), and
    the size ceiling above is what actually forecloses the memory-exhaustion
    vector, rather than a per-file catch racing the interpreter's own state.

    The dict-only filter is what makes the returned mapping type-safe for the
    graph algorithms: a sidecar holding a JSON array or scalar is reported and
    dropped rather than handed on as a graph node.
    """
    kind_dir = Path(vault_root) / kind
    warnings: list[tuple[str, str]] = []
    try:
        names = sorted(
            entry.name
            for entry in os.scandir(kind_dir)
            if entry.name.endswith(".json")
        )
    except (FileNotFoundError, NotADirectoryError):
        return {}, warnings
    except OSError as exc:
        warnings.append((f"{kind}/", f"cannot list directory: {exc}"))
        return {}, warnings

    sidecars: dict[str, dict] = {}
    for name in names:
        sidecar_path = kind_dir / name
        relative = f"{kind}/{name}"
        try:
            size = sidecar_path.stat().st_size
        except OSError as exc:
            warnings.append((relative, f"unreadable sidecar: {exc}"))
            continue
        if size > _MAX_SIDECAR_BYTES:
            warnings.append(
                (relative, f"sidecar too large ({size} bytes, max {_MAX_SIDECAR_BYTES})")
            )
            continue
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except OSError as exc:
            warnings.append((relative, f"unreadable sidecar: {exc}"))
            continue
        except ValueError as exc:
            warnings.append((relative, f"invalid JSON: {exc}"))
            continue
        except RecursionError as exc:
            warnings.append((relative, f"sidecar JSON nested too deep: {exc}"))
            continue
        if not isinstance(data, dict):
            warnings.append((relative, "sidecar is not a JSON object"))
            continue
        sidecars[sidecar_path.stem] = data
    return sidecars, warnings


def _load_kind_sidecars(vault_root: str, kind: str) -> dict[str, dict]:
    """Read every ``<vault_root>/<kind>/*.json`` sidecar → ``{stem: sidecar}``.

    The warning-dropping view of :func:`load_kind_sidecars_with_warnings` both
    public loaders below are built from: a guard degrades to not seeing a
    malformed node rather than failing the whole write, and has no output
    channel to report the loss on.
    """
    return load_kind_sidecars_with_warnings(vault_root, kind)[0]


def load_task_sidecars(vault_root: str) -> dict[str, dict]:
    """Read every task sidecar under ``<vault_root>/task/`` → ``{name: sidecar}``.

    The source-of-truth read for the graph guards — sidecars, never the index.
    A malformed or unreadable sidecar is skipped (best-effort: the guard degrades
    to not seeing that node rather than failing the whole write).
    """
    return _load_kind_sidecars(vault_root, "task")


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
    return {
        f"{kind}/{stem}": sidecar
        for kind in graph_mod.DESIGN_KINDS
        for stem, sidecar in _load_kind_sidecars(vault_root, kind).items()
    }


def confine_edge_reference(value: str, vault_root: str, kind: str = "task") -> str | None:
    """Return a guard-error string if *value* is an unsafe *kind* reference, else None.

    ``--parent``/``--depends-on`` values are record names, so they flow through the
    SAME name-resolution/confinement guard every RECORD_ID-bearing op uses
    (:func:`record_store.confine_record_id`): a ``..`` segment, absolute
    component, NUL byte, or empty/degenerate segment is rejected before the value
    is ever written. Existence is deliberately NOT checked — referential integrity
    is not enforced (a dangling edge is valid, per the record model's shape-only
    contract).

    *value* is a bare record NAME and *kind* is the record kind it is confined
    under — together they form the ``<kind>/<name>`` RECORD_ID handed to the
    store guard. **Security-relevant ordering contract:** a caller whose edge
    grammar qualifies the target itself (``kind/name[@stage]``) MUST split the
    entry and validate its kind BEFORE calling here, then pass the two halves
    separately. Confining a still-qualified, still-staged string would attribute
    every rejection to the raw entry, so a bad name would read as a kind or
    stage error and a rejection would not name the part that was actually
    unsafe. Both the message and the confined RECORD_ID name *kind*, so a
    rejection always identifies the surface the value was confined against.
    """
    if not value:
        return graph_mod.format_guard_message("edge-reference", f"empty {kind} reference")
    try:
        record_store_mod.confine_record_id(f"{kind}/{value}", vault_root)
    except record_store_mod.InvalidRecordIdError as exc:
        return graph_mod.format_guard_message(
            "edge-reference", f"unsafe {kind} reference {value!r}: {exc}"
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
    supplied_depends_on: list[str] | None = None,
    parent_supplied: bool = True,
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

    *sidecar* is the MERGED record — what would land on disk — so most guards
    judge the whole record. *supplied_depends_on* narrows exactly one of them:
    it is the list of ``depends-on`` values this write newly supplies, and only
    those are judged by the edge-FORM check below. ``None`` (the default) means
    "every entry is newly supplied", which is what a create passes and what
    keeps every other caller at today's semantics. *parent_supplied* is the
    ``parent`` analogue — ``False`` when this write leaves the stored ``parent``
    alone, ``True`` (the default) when it sets one. Confinement is deliberately
    NOT narrowed: an unsafe reference is unsafe whoever wrote it.

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
    deps_field = sidecar.get("depends-on")
    deps = [d for d in deps_field if isinstance(d, str)] if isinstance(deps_field, list) else []
    references: list[str] = []
    parent = sidecar.get("parent")
    if isinstance(parent, str):
        references.append(parent)
    references.extend(deps)
    for ref in references:
        msg = confine_edge_reference(ref, vault_root)
        if msg:
            errors.append(msg)
    if errors:
        return errors, []

    # Task ``depends-on`` targets and a task's ``parent`` are BARE task names. The
    # graph matches a stored value byte-for-byte against a task's stem with no
    # prefix normalization, so a qualified or staged value writes clean and then
    # reads as a detached node — an edge the operator believes exists and that
    # nothing ever traverses. A prefixed ``parent`` is the sharper of the two: the
    # child renders detached and the parent renders childless, so a task graph
    # silently loses a whole subtree. Both are rejected here, after confinement,
    # so a traversal-shaped value still reports the containment breach it is.
    #
    # Scoped to what the write SUPPLIES, not to what the merged record holds: a
    # record stored before this check existed may carry a prefixed entry, and
    # judging those would block every later write to it — a plain ``--status``
    # flip included, stranding the record with no route to repair. Grandfathering
    # them keeps ``--unset-depends-on`` (the repair) available too, since a
    # removed entry is not in the merged list at all.
    form_checked = (
        deps if supplied_depends_on is None else [d for d in deps if d in supplied_depends_on]
    )
    for dep in form_checked:
        if "/" in dep or "@" in dep:
            errors.append(
                graph_mod.format_guard_message(
                    "task-edge-form",
                    f"depends-on entry {dep!r} must be a bare task name — "
                    f"'/' and '@' are not part of the task-edge grammar",
                )
            )
    if parent_supplied and isinstance(parent, str) and ("/" in parent or "@" in parent):
        errors.append(
            graph_mod.format_guard_message(
                "task-edge-form",
                f"parent {parent!r} must be a bare task name — "
                f"'/' and '@' are not part of the task-edge grammar",
            )
        )
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
                    f"task {name!r} would create a dependency cycle: "
                    + graph_mod.format_node_path(cycle),
                )
            )
        loop = graph_mod.find_ancestor_loop(_graph(), name)
        if loop:
            errors.append(
                graph_mod.format_guard_message(
                    "parent-loop",
                    f"task {name!r} would create a parent ancestor loop: "
                    + graph_mod.format_node_path(loop),
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


#: The design kinds spelled as a sorted list, for error messages (computed once).
_DESIGN_KIND_LIST: list[str] = sorted(graph_mod.DESIGN_KINDS)


def _design_parse_error(entry: object, parsed: graph_mod.ParsedDependency) -> str:
    """Render one rejected ``depends-on`` entry as a blocking guard message.

    Branches on the closed set of ``error`` codes :func:`graph.parse_dependency`
    returns — the codes are part of ``ParsedDependency.error``'s contract, so
    every one of them gets its own named guard line rather than a shared
    "malformed entry". Grammar rejections carry the ``design-edge-form`` tag,
    stage-vocabulary rejections ``design-edge-stage``; a stage rejection always
    spells out the *target* kind's usable stages, not just the violation.
    """
    if parsed.error == graph_mod.ERR_NO_KIND:
        return graph_mod.format_guard_message(
            "design-edge-form",
            f"depends-on entry {entry!r} must be a qualified target "
            f"'<kind>/<name>[@<stage>]' with kind one of {_DESIGN_KIND_LIST}",
        )
    if parsed.error == graph_mod.ERR_TASK_KIND:
        return graph_mod.format_guard_message(
            "design-edge-form",
            f"depends-on entry {entry!r} targets a task — a design dependency "
            f"may only target {_DESIGN_KIND_LIST}",
        )
    if parsed.error == graph_mod.ERR_UNKNOWN_KIND:
        return graph_mod.format_guard_message(
            "design-edge-form",
            f"depends-on entry {entry!r} names unknown dependency kind "
            f"{parsed.kind!r} — must be one of {_DESIGN_KIND_LIST}",
        )
    stages = list(graph_mod.SUCCESS_CHAINS[parsed.kind])
    if parsed.error == graph_mod.ERR_UNKNOWN_STAGE:
        return graph_mod.format_guard_message(
            "design-edge-stage",
            f"depends-on entry {entry!r} names stage {parsed.stage!r}, which is not "
            f"a status of kind {parsed.kind!r} — valid {parsed.kind} stages: {stages}",
        )
    return graph_mod.format_guard_message(
        "design-edge-stage",
        f"depends-on entry {entry!r} requires stage {parsed.stage!r}, which means the "
        f"target failed — valid {parsed.kind} stages: {stages}",
    )


def _design_edge_error(entry: object, vault_root: str) -> str | None:
    """Return a blocking guard message for *entry*, or ``None`` if it is safe.

    The ordering is the whole point: :func:`graph.parse_dependency` splits the
    ``@stage`` tail off and validates the kind FIRST, and only the surviving
    bare name is confined — under the entry's own kind, not a hardcoded one. An
    entry whose name is traversal-shaped therefore reports a confinement breach
    against that name, never a kind or stage error that would point the operator
    at the wrong half of the value.

    The one-level check runs LAST, after confinement, for the same reason: a
    name is split off the FIRST ``/``, so a nested name and a traversal both
    carry a second slash, and a traversal must keep reporting the containment
    breach it actually is. A name that survives confinement and still carries a
    ``/`` is rejected here — the documented grammar is ``kind/name[@stage]``,
    one level, and :func:`load_design_sidecars` globs one level, so a nested
    name resolves to no sidecar that can ever exist: not a dangling edge (which
    is valid) but a permanently unresolvable one.
    """
    parsed = graph_mod.parse_dependency(entry)
    if parsed.error is not None:
        return _design_parse_error(entry, parsed)
    msg = confine_edge_reference(parsed.name, vault_root, kind=parsed.kind)
    if msg is not None:
        return msg
    if "/" in parsed.name:
        return graph_mod.format_guard_message(
            "design-edge-form",
            f"depends-on entry {entry!r} names {parsed.name!r} — a dependency name is "
            f"one path segment, so '<kind>/<name>[@<stage>]' carries exactly one '/'",
        )
    return None


#: Wikilink pattern matching one renamed record's stem, independent of
#: :func:`record.rename.rewrite_body`'s own copy. See
#: :func:`compute_stem_rewrite` for why this is a deliberate duplication.
def _stem_wikilink_pattern(kind: str, old_stem: str) -> re.Pattern:
    return re.compile(
        r"\[\[(" + re.escape(kind) + r"/)?" + re.escape(old_stem) + r"(\|[^\]\n]*)?\]\]"
    )


def compute_stem_rewrite(body: str, kind: str, old_stem: str, new_stem: str) -> str:
    """Rewrite every exact-stem wikilink to *old_stem*, and nothing else.

    This is a deliberately independent reimplementation of
    :func:`record.rename.rewrite_body` — the guard that keys a rename's
    ``active``-adr exemption on this function must not trust the SAME
    computation the rename sweep already performed, or a bug (or an accidental
    widening) in that one copy would silently widen the exemption too. Two
    independent implementations of "pure stem substitution" must agree before
    a rename-path body change against an ``active`` adr is allowed to land.
    """
    return _stem_wikilink_pattern(kind, old_stem).sub(
        lambda m: f"[[{m.group(1) or ''}{new_stem}{m.group(2) or ''}]]", body
    )


#: The ``adr`` statuses under which the body is frozen. ``draft`` is the only
#: status an adr can hold while its body is still editable; every other status
#: in :data:`model.STATUS_VOCAB` is reachable only by activating the record or
#: by an exit from ``active``, so the whole set carries a decision that has been
#: committed. Freezing the exits, not just ``active``, is what makes the freeze
#: hold: enforcement keyed on ``active`` alone is defeated by first moving the
#: record out of ``active`` and then editing it under the unenforced status.
FROZEN_ADR_STATUSES: frozenset[str] = frozenset({"active", "superseded", "dropped"})


def check_frozen_adr_status_transition(
    *,
    kind: str,
    name: str,
    prior_status: str | None,
    status_set: str | None,
) -> str | None:
    """Block an ``adr`` in a frozen status (:data:`FROZEN_ADR_STATUSES`) being
    flipped back to ``draft`` — the other half of body immutability.

    Freezing the body under every frozen status is not sufficient on its own:
    ``draft`` is editable by design, so a write that returns a frozen record to
    ``draft`` would launder the enforcement away and re-open the body to the
    very next write. Blocking that one transition closes the set: ``draft`` is
    the only exit from the frozen statuses, so with it blocked a record that has
    left ``draft`` can never re-enter it, and its body is frozen for good.

    Every other transition stays open — ``active`` → ``superseded`` / ``dropped``
    is the supersession flow, and the target status has no bearing on the body
    freeze, which is keyed on the PRIOR status alone.
    """
    if kind != "adr":
        return None
    if prior_status not in FROZEN_ADR_STATUSES:
        return None
    if status_set != "draft":
        return None
    return graph_mod.format_guard_message(
        "adr-frozen-status",
        f"{graph_mod.format_node(f'{kind}/{name}')} is {prior_status} — it cannot "
        "return to draft. Its body is immutable and reopening the record would "
        "lift that. Write a new adr and supersede this one instead.",
    )


def check_active_adr_body_immutable(
    *,
    kind: str,
    name: str,
    prior_status: str | None,
    prior_body: str | None,
    new_body: str,
    allowed_body: str | None = None,
) -> str | None:
    """Block a body-changing write against an ``adr`` whose PRIOR on-disk status
    was one of :data:`FROZEN_ADR_STATUSES` — the structural counterpart to the
    "convention-enforced, not CLI-enforced" immutability that used to be
    documented and nowhere checked.

    The frozen set is every status but ``draft``, not ``active`` alone: a
    supersession or a drop is a legitimate exit from ``active``, and a record
    that took one still holds a decision that was committed. Enforcing only
    while the record sits in ``active`` would make each exit a laundering path —
    take it, then edit the frozen body under a status nothing enforces. Paired
    with :func:`check_frozen_adr_status_transition` (which blocks the return to
    ``draft``), the set is closed and the body freeze is permanent.

    Returns a formatted ``graph-guard`` message, or ``None`` when the write may
    proceed. A ``None`` *prior_body* is the no-enforcement default: the caller
    is a seam with nothing prior to compare against (a fresh create, or a seam
    that has not been taught to thread its prior body in), and enforcing
    immutability against a record that does not yet exist would be nonsensical.

    Keyed on the record's PRIOR status, never the status this write applies —
    a ``--status superseded`` flip that changes nothing else must still
    succeed, and a body change riding the same flip must still be rejected: the
    freeze belongs to the record's committed state, not to whatever status the
    write happens to set.

    Both bodies are compared through :func:`store.neutralize_fences` before the
    equality check — the same normalization :func:`store.validate_stamp_neutralize`
    always applies before anything reaches disk. Comparing the raw, pre-write
    values would let a metadata-only write's body argument diverge textually
    from the stored body (e.g. resupplying content with a live
    ``<external-memory>`` fence that neutralizes to what is already stored)
    while still landing as a byte-for-byte no-op; comparing post-neutralization
    values judges exactly what will actually reach disk.

    *allowed_body*, when given, is a second value the normalized *new_body* may
    match instead of *prior_body* — the rename sweep's narrow exemption (a pure
    stem-substitution rewrite is not an edit). It is deliberately not the mere
    presence of a flag: the caller must independently derive it (see
    :func:`compute_stem_rewrite`) and it is checked with the same equality
    rigor as *prior_body*, so a change that happens to differ from both is
    still rejected.
    """
    if kind != "adr":
        return None
    if prior_status not in FROZEN_ADR_STATUSES:
        return None
    if prior_body is None:
        return None
    new_norm = record_store_mod.neutralize_fences(new_body)
    prior_norm = record_store_mod.neutralize_fences(prior_body)
    if new_norm == prior_norm:
        return None
    if allowed_body is not None and new_norm == record_store_mod.neutralize_fences(allowed_body):
        return None
    return graph_mod.format_guard_message(
        "adr-active-immutable",
        f"{graph_mod.format_node(f'{kind}/{name}')} is {prior_status} — its body is "
        "immutable. Supersede it; do not edit it directly. Flip --status "
        "superseded with --related adr=<successor> naming the record that "
        "replaces it.",
    )


def evaluate_design_guards(
    *,
    kind: str,
    name: str,
    sidecar: dict,
    vault_root: str,
    status_set: str | None,
    deleting: bool = False,
    body: str = "",
    prior_status: str | None = None,
    prior_body: str | None = None,
) -> tuple[list[str], list[str]]:
    """Evaluate the design graph guards for a spec/adr create/update/delete.

    Returns ``(errors, notices)`` in the same shape :func:`evaluate_task_guards`
    does, and is likewise a no-op — ``([], [])`` — for every kind outside
    :data:`graph.DESIGN_KINDS`:

      - ``errors`` block the operation (nothing is written): the frozen-adr
        body-immutability check (see :func:`check_active_adr_body_immutable`)
        and the frozen-adr return-to-``draft`` check that keeps it from being
        laundered away (see :func:`check_frozen_adr_status_transition`);
        every ``depends-on`` entry that fails the ``kind/name[@stage]`` grammar
        (a name carrying its own ``/`` included — the grammar is one level) or
        the target kind's stage vocabulary, an entry whose name breaks vault
        confinement, and a stage-blind dependency cycle over the qualified-id
        design graph.
      - ``notices`` are non-blocking, printed only on a successful op: the
        dependent warning raised when this record flips to a failure status or is
        deleted while other design records still depend on it.

    Existence is deliberately NOT checked: a dependency on a record that does not
    exist yet is a valid write (the record model's shape-only contract), so only
    a cycle — a statement about edges that DO resolve — can block.

    Tags are namespaced against their task-graph counterparts
    (``design-depends-on-cycle`` vs ``depends-on-cycle``, ``design-dependents``
    vs ``dependents``) so the bracketed tag alone identifies which graph rejected
    a write. The confinement guard keeps the shared ``edge-reference`` tag: it is
    one guard with one meaning on both graphs, and its message names the kind it
    confined under.
    """
    if kind not in graph_mod.DESIGN_KINDS:
        return [], []

    qualified_id = f"{kind}/{name}"

    # Delete only warns about dependents; it is never blocked.
    if deleting:
        deps = graph_mod.design_dependents(load_design_sidecars(vault_root), qualified_id)
        notices: list[str] = []
        if deps:
            notices.append(
                graph_mod.format_guard_message(
                    "design-dependents",
                    f"{graph_mod.format_node(qualified_id)} deleted but still depended on",
                    offenders=deps,
                )
            )
        return [], notices

    errors: list[str] = []

    transition_msg = check_frozen_adr_status_transition(
        kind=kind,
        name=name,
        prior_status=prior_status,
        status_set=status_set,
    )
    if transition_msg:
        return [transition_msg], []

    immutable_msg = check_active_adr_body_immutable(
        kind=kind,
        name=name,
        prior_status=prior_status,
        prior_body=prior_body,
        new_body=body,
    )
    if immutable_msg:
        return [immutable_msg], []

    # Every entry is parsed, kind-validated and confined before the graph is
    # built — a malformed or traversal-shaped value must never reach disk, and
    # the vault-wide load must not be paid for a write that is already rejected.
    entries_field = sidecar.get("depends-on")
    entries = entries_field if isinstance(entries_field, list) else []
    for entry in entries:
        msg = _design_edge_error(entry, vault_root)
        if msg:
            errors.append(msg)
    if errors:
        return errors, []

    # Same lazy load as the task guards: a record with no outgoing edges can
    # never be the entry point of a NEW cycle, so a plain status update that is
    # not a failure transition never touches the vault-wide glob+parse.
    design_graph: dict[str, dict] | None = None

    def _graph() -> dict[str, dict]:
        nonlocal design_graph
        if design_graph is None:
            design_graph = load_design_sidecars(vault_root)
            design_graph[qualified_id] = sidecar
        return design_graph

    if entries:
        cycle = graph_mod.find_design_dependency_cycle(_graph(), start=qualified_id)
        if cycle:
            errors.append(
                graph_mod.format_guard_message(
                    "design-depends-on-cycle",
                    f"{graph_mod.format_node(qualified_id)} would create a dependency cycle: "
                    + graph_mod.format_node_path(cycle),
                )
            )
    if errors:
        return errors, []

    notices = []
    if status_set in graph_mod.FAILURE_STATUSES:
        deps = graph_mod.design_dependents(_graph(), qualified_id)
        if deps:
            notices.append(
                graph_mod.format_guard_message(
                    "design-dependents",
                    f"{graph_mod.format_node(qualified_id)} set to {status_set} "
                    f"but still depended on",
                    offenders=deps,
                )
            )
    return errors, notices


def evaluate_graph_guards(
    *,
    kind: str,
    name: str,
    sidecar: dict,
    body: str,
    vault_root: str,
    status_set: str | None,
    deleting: bool = False,
    supplied_depends_on: list[str] | None = None,
    parent_supplied: bool = True,
    prior_body: str | None = None,
    prior_status: str | None = None,
) -> tuple[list[str], list[str]]:
    """Route a create/update/delete to the graph guards for its *kind*.

    One signature for every caller: ``task`` goes to :func:`evaluate_task_guards`,
    a design kind to :func:`evaluate_design_guards`, and any other kind is the
    same ``([], [])`` no-op both of those return on their own — no kind carries
    two graphs, so exactly one policy ever runs. ``body`` is consumed by the
    task policy (the flow-out ritual reminder has no design counterpart) and,
    for an ``adr``, by the ``active``-immutability check. *supplied_depends_on*
    and *parent_supplied* are task-only: the task-edge form check is the one
    guard that grandfathers already-stored edges, because it is the one guard
    whose rule postdates the data it reads. Every design ``depends-on`` entry on disk was written
    under the design grammar, so that policy judges the merged record whole.

    *prior_body* / *prior_status* are the record's on-disk values BEFORE this
    write — consumed only by the design policy's ``active``-immutability check,
    and both default to ``None``: a caller that has not been taught to thread
    them in (a fresh create, or a seam with no natural "prior" to read) gets no
    enforcement, never a false rejection, on that seam. See
    :func:`check_active_adr_body_immutable`.
    """
    if kind == "task":
        return evaluate_task_guards(
            kind=kind,
            name=name,
            sidecar=sidecar,
            body=body,
            vault_root=vault_root,
            status_set=status_set,
            deleting=deleting,
            supplied_depends_on=supplied_depends_on,
            parent_supplied=parent_supplied,
        )
    if kind in graph_mod.DESIGN_KINDS:
        return evaluate_design_guards(
            kind=kind,
            name=name,
            sidecar=sidecar,
            body=body,
            vault_root=vault_root,
            status_set=status_set,
            deleting=deleting,
            prior_status=prior_status,
            prior_body=prior_body,
        )
    return [], []
