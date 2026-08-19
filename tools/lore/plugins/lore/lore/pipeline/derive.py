"""Lineage membership — which records are on the board, and why.

**Pure.** Every function here is a function of its arguments: no file is
opened, no path is resolved, no stream is written, and no clock or environment
is read. The walk hands over what it found and this module decides what that
means, so the same walk always derives the same board and the derivation can be
exercised on records that were never on a disk. A structural test holds the
purity by refusing this module the imports that would break it.

**Own-vault confinement — a security property, not a convenience.** A
``related: adr=`` edge resolves only against the adrs of the vault the record
itself came from. Membership is never looked up across a merged view of every
vault, because a merged lookup lets a record in a shared vault answer to an adr
name in a personal vault and so decide what the operator's own board shows.
Two vaults holding an adr of the same name therefore anchor two lineages that
never see each other, and an edge pointing outside its own vault does not
resolve at all — it surfaces as a flagged singleton rather than silently
vanishing or silently binding to a stranger's record.

**Membership rules.** A lineage is an adr root plus the non-terminal specs
whose own-vault edges point at it:

  - It renders while the root is ``draft`` — a draft root is itself the work —
    or while at least one non-terminal spec still points at it. An ``active``
    root nobody is deriving from has nothing in flight and does not render.
  - Terminal members are omitted from the rendered members. ``complete`` and
    ``superseded`` still count toward ``completed_count``; ``dropped`` does
    not, since abandoned work is not progress.
  - A ``dropped`` or ``superseded`` root marks each of its surviving seeds
    ``orphaned-seed``: the spec is live but its premise is gone.

Two singleton shapes join them, each rooted at the record itself: a spec whose
edge resolves to nothing (``unresolved-root``), and an ``open`` task labelled
onto the brainstorm route (``routed-task``). A singleton takes its own labels
and ``updated-at`` as the root's, so it tiers and orders on the same rules as
an adr-rooted lineage.

**Tiering.** :func:`split_tiers` splits an already-derived, already
recency-ordered list into the priority and recency tiers. A lineage joins the
priority tier when its root carries a ``priority`` label; an integer value
sorts ascending, any other value sorts after every integer one with its raw
text kept for display, and two lineages landing in the same rank keep the
recency order they arrived in — the newest first. A label on a member is
never consulted; only the root's own label decides. A root in a ``shared:
true`` vault has its ``priority`` label ignored for tiering entirely — a
binding constraint on this board, not a preference — even though the label
itself still projects untouched for display.

**Edge values are normalized, never trusted.** A value is stored as a bare
stem by convention only, so an optional leading ``adr/`` is stripped before
resolution and ``foo`` and ``adr/foo`` reach the same lineage. The raw value
survives on the record for display: on an edge that resolves to nothing, the
value that failed is the whole diagnostic.

**Gating is additive, never subtractive.** A ``spec``/``adr`` record's
``depends-on`` entries are evaluated against the design records of the vault it
came from, and a record with any unmet entry keeps its place in its lineage and
gains the ``gated`` flag plus the evaluator's reason. A dependency the operator
cannot see is one they cannot act on, so nothing is ever hidden for being
blocked. The evaluation is confined to one vault for the same reason membership
is: :func:`record.graph.evaluate_dependencies` is a pure function over whatever
graph it is handed and enforces no confinement of its own, so a graph spanning
two vaults would let a shared vault's record satisfy — and so silently un-gate —
a personal vault's dependency. The graph handed over is exactly one vault's own
walked sidecars, and no step here ever builds a wider one.

**A task's ``depends-on`` is a different grammar.** Task edges are bare task
names; the design evaluator parses qualified ``kind/name[@stage]`` ids and reads
a bare name as unresolvable. Evaluating a routed task's entries through it would
therefore manufacture a gating verdict out of a grammar mismatch, so a task's
stored entries project unevaluated — ``met`` is ``None``, with a reason saying
so — and a routed task is never gated.

**Sidecars carry whatever JSON was on disk.** A record synced in by git never
passed this CLI's validator, so every field read here is type-checked at the
point of use and a wrong-typed field reads as absent rather than raising. The
one place that guarantee is not this module's to give is the evaluator call: a
target whose ``status`` is a list is valid JSON and unhashable, and the
evaluator tests it for failure-set membership. Each record's evaluation is
therefore guarded as a whole, and a raise costs that record its place on the
board and yields one warning — never the vault, and never the command.
"""

from __future__ import annotations

from typing import NamedTuple, Sequence

from ..record import graph as graph_mod
from .walk import VaultWalk

#: Spec statuses that mean the work is over. Omitted from a lineage's rendered
#: members whichever way they got there.
TERMINAL_SPEC_STATUSES: frozenset[str] = frozenset({"complete", "superseded", "dropped"})

#: The terminal statuses that mean the work *finished*. ``dropped`` is
#: deliberately absent: it is abandonment, and counting it as progress would
#: overstate every lineage that ever gave up on a spec.
COMPLETED_SPEC_STATUSES: frozenset[str] = frozenset({"complete", "superseded"})

#: Root statuses that leave their surviving members without a live premise.
ORPHANING_ADR_STATUSES: frozenset[str] = frozenset({"dropped", "superseded"})

#: The label and value that route a task onto the board, and the one task
#: status at which it is still waiting there.
ROUTE_LABEL = "route"
BRAINSTORM_ROUTE = "brainstorm"
OPEN_TASK_STATUS = "open"

#: The optional prefix an edge value may carry ahead of the bare adr stem.
_ADR_PREFIX = "adr/"

#: The label whose value orders the priority tier. Read from a lineage's root
#: only — a label on a member never affects tiering.
PRIORITY_LABEL = "priority"

#: The derived flags this module assigns. They are its own closed vocabulary —
#: never vault content — which is why they need no fencing downstream.
ORPHANED_SEED = "orphaned-seed"
UNRESOLVED_ROOT = "unresolved-root"
ROUTED_TASK = "routed-task"
GATED = "gated"

#: The sidecar field carrying a record's dependency entries.
DEPENDS_ON_FIELD = "depends-on"

#: The reason a routed task's entries carry in place of a verdict. Authored
#: here, so it is closed vocabulary rather than vault content.
TASK_EDGE_REASON = "task dependency edges are not evaluated by this surface"


class Dependency(NamedTuple):
    """One ``depends-on`` entry's verdict, ready to project.

    The fields mirror :class:`record.graph.DependencyStatus` exactly — a test
    pins the two together — so an evaluator verdict converts without a mapping
    layer that could quietly drop a field. ``met`` widens to ``None`` for the
    one case the evaluator never sees: a task's entries, projected unevaluated.

    ``kind``, ``name``, ``stage`` and ``reason`` all derive from the entry the
    vault stored, so all four are vault-authored free text.
    """

    kind: str | None
    name: str | None
    stage: str | None
    met: bool | None
    reason: str
    reason_code: str | None


class Evaluation(NamedTuple):
    """What one record's ``depends-on`` evaluation yielded.

    ``gated`` is not derivable from ``dependencies`` by a consumer without
    re-encoding the rule that ``met is None`` does not block, so the verdict
    the flag is built from travels with the verdicts themselves.
    """

    dependencies: tuple[Dependency, ...]
    gated: bool


class DerivedWarning(NamedTuple):
    """One record the derivation itself could not finish, and why.

    Distinct in origin from the walk's own read failures — the file was read
    fine — but identical in shape and consequence, so both reach the board's
    ``warnings`` through the same projection. ``file`` names the sidecar by its
    on-disk path, whose name half is a filename stem this CLI's slugifier may
    never have seen, and ``message`` quotes an exception; both are treated as
    vault-authored free text downstream.
    """

    vault: str
    shared: bool
    file: str
    message: str


class Member(NamedTuple):
    """One record's place on the board: its id, its sidecar, and its flags.

    The sidecar rides along verbatim — this module reads it but never rewrites
    it, so the renderer projects from the same bytes the walk read.

    ``dependencies`` is the verdict per stored ``depends-on`` entry, in stored
    order, duplicates included: the evaluator returns one status per entry and
    that list passes through unaltered rather than collapsing into a map keyed
    by target, so the Nth verdict is the Nth stored entry.
    """

    record_id: str
    sidecar: dict
    flags: tuple[str, ...]
    dependencies: tuple[Dependency, ...] = ()


class Lineage(NamedTuple):
    """One rendered group: a root, its surviving members, and its progress.

    ``id`` is ``<vault>:<root record id>`` — the vault qualifier is always
    present, singletons included, because two vaults may hold the same record
    id and a bare id would merge them.

    ``vault`` and ``shared`` describe the one vault every record here came
    from; own-vault confinement is what makes a single pair correct for the
    whole lineage.

    ``recency`` is the newest ``updated-at`` across the root and the surviving
    members — the empty string when none of them carries one, which sorts a
    lineage nobody has stamped to the end rather than failing the ordering.
    """

    id: str
    vault: str
    shared: bool
    root: Member
    members: tuple[Member, ...]
    completed_count: int
    recency: str


class Derivation(NamedTuple):
    """Everything one derivation produced: the board's lineages and its losses.

    The two travel together because a record dropped mid-derivation is only
    honest as a pair — the lineage it is missing from, and the warning saying
    why. Returning the lineages alone would let a record vanish from the board
    with nothing anywhere to say it ever existed.
    """

    lineages: tuple[Lineage, ...]
    warnings: tuple[DerivedWarning, ...]


def normalize_edge(value: str) -> str:
    """Strip an optional leading ``adr/`` from an edge *value*.

    The value names an adr by bare stem by convention, but a prefixed value
    stores just as cleanly, so both spellings must reach the same target — an
    unnormalized read would report a perfectly good edge as dangling.
    """
    return value[len(_ADR_PREFIX):] if value.startswith(_ADR_PREFIX) else value


def _string(sidecar: dict, key: str) -> str:
    """Read *key* off *sidecar* as text, treating any other type as absent."""
    value = sidecar.get(key)
    return value if isinstance(value, str) else ""


def _adr_edges(sidecar: dict) -> list[str]:
    """The record's raw ``related: adr=`` values, in stored order."""
    related = sidecar.get("related")
    if not isinstance(related, dict):
        return []
    values = related.get("adr")
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, str)]


def _label(sidecar: dict, key: str) -> str:
    """Read one label value off *sidecar*, treating any other type as absent."""
    labels = sidecar.get("labels")
    if not isinstance(labels, dict):
        return ""
    value = labels.get(key)
    return value if isinstance(value, str) else ""


def _kind(record_id: str) -> str:
    kind, _, _ = record_id.partition("/")
    return kind


def _recency(sidecars: Sequence[dict]) -> str:
    """The newest ``updated-at`` across *sidecars*, or the empty string."""
    return max((_string(sidecar, "updated-at") for sidecar in sidecars), default="")


def _entries(sidecar: dict) -> list:
    """The record's stored ``depends-on`` entries, in stored order.

    Entries are not filtered by type: the evaluator parses any object without
    raising, and dropping a malformed entry here would silently shorten a list
    a consumer reads positionally.
    """
    entries = sidecar.get(DEPENDS_ON_FIELD)
    return list(entries) if isinstance(entries, list) else []


def _unevaluated(entry: object) -> Dependency:
    """Project one task entry without a verdict, mirroring the parser's shape.

    ``name`` carries the stored entry when it is text and nothing when it is
    not — the same reading :func:`record.graph.parse_dependency` gives an entry
    it cannot split — so no non-string value rides out through a field the
    renderer fences as text.
    """
    return Dependency(
        None, entry if isinstance(entry, str) else None, None,
        None, TASK_EDGE_REASON, None,
    )


def _evaluate(record_id: str, sidecar: dict, graph: dict[str, dict]) -> Evaluation:
    """Evaluate one record's entries against *graph*, and say whether it gates.

    *graph* is the record's own vault's walked sidecars and nothing else. Only
    an unmet verdict gates: an unevaluated task entry is ``met is None``, which
    is not a claim that anything is blocked.
    """
    entries = _entries(sidecar)
    if not entries:
        return Evaluation((), False)
    if _kind(record_id) == "task":
        return Evaluation(tuple(_unevaluated(entry) for entry in entries), False)
    dependencies = tuple(
        Dependency(*status)
        for status in graph_mod.evaluate_dependencies(graph, entries)
    )
    return Evaluation(
        dependencies, any(dependency.met is False for dependency in dependencies)
    )


def _evaluate_vault(
    walk: VaultWalk,
) -> tuple[dict[str, Evaluation], list[DerivedWarning]]:
    """Evaluate every record in *walk* against *walk*'s own records.

    The graph is the walk's mapping itself, so no sidecar is read a second time
    and no mapping spanning two vaults is ever built.

    The guard is deliberately broad. Every enumerated failure the evaluator can
    hit is already data rather than an exception, so anything that does raise
    here is by definition unenumerated — and the board degrading to one missing
    record is always a better answer than a traceback where the operator
    expected their work.
    """
    evaluations: dict[str, Evaluation] = {}
    warnings: list[DerivedWarning] = []
    for record_id in sorted(walk.records):
        try:
            evaluations[record_id] = _evaluate(
                record_id, walk.records[record_id], walk.records
            )
        except Exception as exc:
            warnings.append(
                DerivedWarning(
                    walk.name, walk.shared, f"{record_id}.json",
                    f"dependency evaluation failed: {exc}",
                )
            )
    return evaluations, warnings


def _member(
    record_id: str,
    sidecar: dict,
    flags: tuple[str, ...],
    evaluations: dict[str, Evaluation],
) -> Member:
    """Build a member, appending :data:`GATED` when its evaluation says so."""
    evaluation = evaluations[record_id]
    return Member(
        record_id, sidecar,
        flags + ((GATED,) if evaluation.gated else ()),
        evaluation.dependencies,
    )


def _singleton(
    walk: VaultWalk,
    record_id: str,
    sidecar: dict,
    flag: str,
    evaluations: dict[str, Evaluation],
) -> Lineage:
    """A one-record lineage rooted at the record itself."""
    return Lineage(
        id=f"{walk.name}:{record_id}",
        vault=walk.name,
        shared=walk.shared,
        root=_member(record_id, sidecar, (flag,), evaluations),
        members=(),
        completed_count=0,
        recency=_string(sidecar, "updated-at"),
    )


def _resolve_specs(
    walk: VaultWalk, adr_ids: set[str]
) -> tuple[dict[str, list[str]], dict[str, int], list[str]]:
    """Route every spec's edges to their own-vault targets.

    Returns the surviving seeds per adr, the completed count per adr, and the
    specs whose edges left them without a resolvable root. Each edge is routed
    independently, so a spec with one resolving and one dangling edge both
    joins a lineage and reports the dangling one.

    Two of a record's edges naming the same target — ``foo`` and ``adr/foo``
    are one target after normalization — route once. A record is in a lineage
    or it is not; listing it twice would also double its lineage's completed
    count, turning a redundant edge into false progress.
    """
    seeds: dict[str, list[str]] = {adr_id: [] for adr_id in adr_ids}
    completed: dict[str, int] = {adr_id: 0 for adr_id in adr_ids}
    unresolved: list[str] = []

    for record_id in sorted(walk.records):
        if _kind(record_id) != "spec":
            continue
        sidecar = walk.records[record_id]
        status = _string(sidecar, "status")
        terminal = status in TERMINAL_SPEC_STATUSES
        dangling = False
        for target in dict.fromkeys(
            f"adr/{normalize_edge(value)}" for value in _adr_edges(sidecar)
        ):
            if target not in adr_ids:
                dangling = True
            elif not terminal:
                seeds[target].append(record_id)
            elif status in COMPLETED_SPEC_STATUSES:
                completed[target] += 1
        if dangling and not terminal:
            unresolved.append(record_id)

    return seeds, completed, unresolved


def _vault_lineages(
    walk: VaultWalk,
    evaluations: dict[str, Evaluation],
) -> list[Lineage]:
    """Every lineage one vault anchors, resolved entirely within that vault.

    *walk* carries only the records whose evaluation finished; the rest are
    already accounted for as warnings, so nothing here needs to know they
    existed.
    """
    adr_ids = {rid for rid in walk.records if _kind(rid) == "adr"}
    seeds, completed, unresolved = _resolve_specs(walk, adr_ids)

    lineages: list[Lineage] = []
    for adr_id in sorted(adr_ids):
        root_sidecar = walk.records[adr_id]
        root_status = _string(root_sidecar, "status")
        seed_ids = seeds[adr_id]
        if root_status != "draft" and not seed_ids:
            continue
        member_flags = (ORPHANED_SEED,) if root_status in ORPHANING_ADR_STATUSES else ()
        members = tuple(
            _member(seed_id, walk.records[seed_id], member_flags, evaluations)
            for seed_id in seed_ids
        )
        lineages.append(
            Lineage(
                id=f"{walk.name}:{adr_id}",
                vault=walk.name,
                shared=walk.shared,
                root=_member(adr_id, root_sidecar, (), evaluations),
                members=members,
                completed_count=completed[adr_id],
                recency=_recency([root_sidecar, *(m.sidecar for m in members)]),
            )
        )

    lineages.extend(
        _singleton(walk, record_id, walk.records[record_id], UNRESOLVED_ROOT, evaluations)
        for record_id in unresolved
    )
    for record_id in sorted(walk.records):
        sidecar = walk.records[record_id]
        if (
            _kind(record_id) == "task"
            and _string(sidecar, "status") == OPEN_TASK_STATUS
            and _label(sidecar, ROUTE_LABEL) == BRAINSTORM_ROUTE
        ):
            lineages.append(
                _singleton(walk, record_id, sidecar, ROUTED_TASK, evaluations)
            )
    return lineages


def derive_board(walks: Sequence[VaultWalk]) -> Derivation:
    """Derive every vault's lineages, newest first, plus what was lost deriving them.

    Each vault is evaluated and derived on its own and the results are
    concatenated, which is what keeps both the edge resolution and the
    dependency evaluation own-vault: no step of this function ever holds a
    mapping spanning two vaults. The ordering sorts by recency descending with
    the lineage id as the tiebreak, so two lineages stamped at the same moment
    keep a stable order across invocations instead of following whatever order
    the directory listing happened to produce.
    """
    lineages: list[Lineage] = []
    warnings: list[DerivedWarning] = []
    for walk in walks:
        evaluations, vault_warnings = _evaluate_vault(walk)
        warnings.extend(vault_warnings)
        derived = walk._replace(
            records={
                record_id: sidecar
                for record_id, sidecar in walk.records.items()
                if record_id in evaluations
            }
        )
        lineages.extend(_vault_lineages(derived, evaluations))
    lineages.sort(key=lambda lineage: lineage.id)
    lineages.sort(key=lambda lineage: lineage.recency, reverse=True)
    return Derivation(tuple(lineages), tuple(warnings))


def _root_priority(lineage: Lineage) -> str:
    """The root's raw ``priority`` label, or empty when absent or ignored.

    A ``shared: true`` vault's labels never influence ordering, so a shared
    root reads as unlabeled here even though :func:`project_record` still
    shows the label verbatim — the ignored marker that explains why lives in
    the renderer, not here.
    """
    if lineage.shared:
        return ""
    return _label(lineage.root.sidecar, PRIORITY_LABEL)


def _priority_rank(lineage: Lineage) -> tuple[int, int]:
    """Sort key ranking every integer-valued priority ahead of every other one.

    The raw label is attacker- or typo-influenced text, so parsing it is the
    one place this could raise; catching it here keeps the comparison itself
    total over a mix of integer and non-integer priorities.
    """
    try:
        return (0, int(_root_priority(lineage)))
    except ValueError:
        return (1, 0)


def split_tiers(lineages: Sequence[Lineage]) -> tuple[list[Lineage], list[Lineage]]:
    """Split already-derived, recency-ordered *lineages* into the two tiers.

    *lineages* arrives newest first. The recency tier is a plain filter over
    it, so it keeps that order untouched. The priority tier starts from the
    same order and layers one more, stable sort of rank on top — integer
    values ascending, every other value after them — so two lineages that
    land in the same rank keep the recency order they already had, the newest
    one first.
    """
    priority = [lineage for lineage in lineages if _root_priority(lineage)]
    recency = [lineage for lineage in lineages if not _root_priority(lineage)]
    priority.sort(key=_priority_rank)
    return priority, recency
