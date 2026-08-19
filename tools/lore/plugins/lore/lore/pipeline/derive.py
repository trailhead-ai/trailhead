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

**Sidecars carry whatever JSON was on disk.** A record synced in by git never
passed this CLI's validator, so every field read here is type-checked at the
point of use and a wrong-typed field reads as absent rather than raising.
"""

from __future__ import annotations

from typing import NamedTuple, Sequence

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


class Member(NamedTuple):
    """One record's place on the board: its id, its sidecar, and its flags.

    The sidecar rides along verbatim — this module reads it but never rewrites
    it, so the renderer projects from the same bytes the walk read.
    """

    record_id: str
    sidecar: dict
    flags: tuple[str, ...]


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


def _singleton(walk: VaultWalk, record_id: str, sidecar: dict, flag: str) -> Lineage:
    """A one-record lineage rooted at the record itself."""
    return Lineage(
        id=f"{walk.name}:{record_id}",
        vault=walk.name,
        shared=walk.shared,
        root=Member(record_id, sidecar, (flag,)),
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


def _vault_lineages(walk: VaultWalk) -> list[Lineage]:
    """Every lineage one vault anchors, resolved entirely within that vault."""
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
            Member(seed_id, walk.records[seed_id], member_flags) for seed_id in seed_ids
        )
        lineages.append(
            Lineage(
                id=f"{walk.name}:{adr_id}",
                vault=walk.name,
                shared=walk.shared,
                root=Member(adr_id, root_sidecar, ()),
                members=members,
                completed_count=completed[adr_id],
                recency=_recency([root_sidecar, *(m.sidecar for m in members)]),
            )
        )

    lineages.extend(
        _singleton(walk, record_id, walk.records[record_id], UNRESOLVED_ROOT)
        for record_id in unresolved
    )
    for record_id in sorted(walk.records):
        sidecar = walk.records[record_id]
        if (
            _kind(record_id) == "task"
            and _string(sidecar, "status") == OPEN_TASK_STATUS
            and _label(sidecar, ROUTE_LABEL) == BRAINSTORM_ROUTE
        ):
            lineages.append(_singleton(walk, record_id, sidecar, ROUTED_TASK))
    return lineages


def derive_lineages(walks: Sequence[VaultWalk]) -> list[Lineage]:
    """Derive every vault's lineages, newest first.

    Each vault is derived on its own and the results are concatenated, which is
    what keeps the resolution own-vault: no step of this function ever holds a
    mapping spanning two vaults. The ordering sorts by recency descending with
    the lineage id as the tiebreak, so two lineages stamped at the same moment
    keep a stable order across invocations instead of following whatever order
    the directory listing happened to produce.
    """
    lineages = [lineage for walk in walks for lineage in _vault_lineages(walk)]
    lineages.sort(key=lambda lineage: lineage.id)
    lineages.sort(key=lambda lineage: lineage.recency, reverse=True)
    return lineages


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
