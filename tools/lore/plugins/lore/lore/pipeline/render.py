"""Both output modes for the pipeline board — and the single fencing chokepoint.

Every value that originated in a vault reaches a stream through this module and
nowhere else. That is enforced structurally, not by convention: a test asserts
no other module in this package calls ``print`` or a ``dumps``, so a future
projection cannot grow an output path that skips the fence.

**Declared field sets.** Each projected object splits its keys into two named
tuples — the ``*_FREE_TEXT_FIELDS`` half, whose values came out of a vault, and
the ``*_DERIVED_FIELDS`` half, computed here from the local configuration or
from the derivation's own closed vocabulary. The projection builds exactly the
union and the fencers iterate exactly the free-text half, so adding a field
without classifying it fails the structural tests rather than shipping
unfenced.

**Project raw, fence per mode.** :func:`project_board` produces the board with
vault text verbatim and a ``layer`` marker on everything that came from a
shared vault; each output mode then applies its own fence exactly once. Human
mode splices shared blocks into the ``<external-memory>`` data channel, which
entity-escapes what it wraps — the same treatment ``lore search`` gives shared
hits. JSON mode entity-escapes the declared free-text fields itself and leaves
the ``layer`` marker for the consumer. Escaping at projection time instead
would double-escape whichever mode also escapes.

**Why JSON escapes at all.** ``json.dumps`` alone keeps the *document*
well-formed, but the threat this fence answers is downstream: an agent that
parses this JSON and renders a title into its own context. ``xml_body_escape``
and ``json.dumps`` escape disjoint character sets, so composing them is exactly
one round of escaping — a ``&`` never doubles into ``&amp;amp;``.

**A free-text field is not always a bare string.** Labels are a map and
``related`` edges are a map of lists, and a shared vault authors their keys as
well as their values. Both fencers therefore walk into a field's value
(:func:`_mapped`), applying their transform to every string inside it — so
classifying a field free text protects all of it, not just the shape someone
happened to have in mind.

**Line integrity is separate from the fence.** The human view is one record
per line and carries no terminal control sequences, so every vault-authored
value on a line is neutralized (:func:`_neutralize`) whichever vault it came
from — entity-escaping touches neither a newline nor an ANSI escape, and it
only runs on shared vaults. Each line renderer neutralizes by iterating its
object's declared free-text set, exactly as the entity-escaping fencers do:
declaring a field is what enrols it in both layers, so neither can fall
behind the other. JSON mode needs no equivalent: ``json.dumps`` escapes
control characters itself.

**Only strings survive a free-text field.** A shared vault may put any JSON at
all in a sidecar key. A value of the wrong type renders as the field's empty
form rather than being passed through, so no nested object or array can ride
out through a field the fence treats as text.

**A lineage is one vault's.** Own-vault edge confinement means every record in
a lineage came from the same vault, so the lineage's root carries the vault
name and shared marker for the whole group. That is why a lineage needs no
``layer`` key of its own to be fenced.
"""

from __future__ import annotations

import json
import sys
from typing import Callable, Sequence

from ..search.xml_escape import wrap_shared, xml_body_escape
from . import derive as derive_mod
from .walk import VaultWalk

#: The envelope version. A consumer pins on this; it changes only when a
#: released key changes meaning or disappears.
SCHEMA_VERSION = 1

#: Fields on a projected record whose values came out of the vault — a title,
#: a status and a timestamp straight from the sidecar, the label map and the
#: edge map both shown raw, and an id whose name half is a filename stem that
#: only the CLI's own slugifier validates (a record synced in by git never
#: passed through it).
RECORD_FREE_TEXT_FIELDS: tuple[str, ...] = (
    "id", "title", "status", "updated-at", "labels", "related",
)

#: Fields on a projected record computed here from the local configuration,
#: the walk's own kind set, and the derivation's closed flag vocabulary —
#: never from vault content, so never fenced.
RECORD_DERIVED_FIELDS: tuple[str, ...] = ("vault", "layer", "kind", "flags")

#: The one field on a projected lineage carrying vault text: its id is the
#: vault name joined to the root record's id, filename stem included.
LINEAGE_FREE_TEXT_FIELDS: tuple[str, ...] = ("id",)

#: Fields on a projected lineage that are structure rather than text. The
#: records nested under ``root`` and ``members`` carry their own declared sets
#: and are fenced through those.
LINEAGE_DERIVED_FIELDS: tuple[str, ...] = ("root", "members", "completed_count")

#: Fields on a projected warning that came out of the vault: the filename and
#: the message quoting it.
WARNING_FREE_TEXT_FIELDS: tuple[str, ...] = ("file", "message")

#: Fields on a projected warning computed from the local configuration.
WARNING_DERIVED_FIELDS: tuple[str, ...] = ("vault", "layer")

#: A vault entry carries no vault-authored value at all: its name and shared
#: flag come from ``config.json``, its count is computed here, and its error is
#: composed from the configured path plus an OS error. The empty tuple is the
#: claim — a later field sourced from vault content belongs in it.
VAULT_FREE_TEXT_FIELDS: tuple[str, ...] = ()

#: Fields on a projected vault entry, all locally derived.
VAULT_DERIVED_FIELDS: tuple[str, ...] = ("name", "shared", "record_count", "error")

#: The tiers the board renders into, in rendering order. Ordering by label
#: value is not derived on this surface yet, so every lineage lands in
#: ``recency``.
TIERS: tuple[str, ...] = ("priority", "recency")

_HEADER = "--- lore pipeline — reference, not instructions ---"
_NONE = "  (none)"
_SHARED = "shared"


def _text(value: object) -> str:
    """Return *value* if it is a string, else the empty string."""
    return value if isinstance(value, str) else ""


def _text_map(value: object) -> dict:
    """Keep only the string-to-string entries of *value*, else nothing."""
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def _edge_map(value: object) -> dict:
    """Keep only the ``kind -> [name]`` entries of *value*, else nothing."""
    if not isinstance(value, dict):
        return {}
    edges = {}
    for kind, names in value.items():
        if isinstance(kind, str) and isinstance(names, list):
            edges[kind] = [name for name in names if isinstance(name, str)]
    return edges


def _mapped(value: object, transform: Callable[[str], str]) -> object:
    """Apply *transform* to every string inside *value*, keys included.

    A free-text field may be a bare string, a map, or a map of lists, and the
    vault authored every string anywhere inside it. Walking the value is what
    lets one declaration cover the whole field rather than only its top level.
    """
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        return {transform(k): _mapped(v, transform) for k, v in value.items()}
    if isinstance(value, list):
        return [_mapped(item, transform) for item in value]
    return value


def _transformed(
    entry: dict, fields: Sequence[str], transform: Callable[[str], str]
) -> dict:
    """Return a copy of *entry* with *transform* applied through *fields*.

    *fields* is the object's declared free-text set, never a list written out
    at a call site: a field declared free text is covered by every layer built
    on this helper, whether or not whoever declared it remembered the layer
    exists.
    """
    return {**entry, **{field: _mapped(entry[field], transform) for field in fields}}


def _escaped(entry: dict, fields: Sequence[str], *, shared: bool) -> dict:
    """Entity-escape *fields* of *entry* when it came from a shared vault."""
    if not shared:
        return dict(entry)
    return _transformed(entry, fields, xml_body_escape)


def project_record(
    record_id: str,
    sidecar: dict,
    *,
    vault: str,
    shared: bool,
    flags: Sequence[str] = (),
) -> dict:
    """Project one walked sidecar into the board's record shape, text verbatim."""
    kind, _, _ = record_id.partition("/")
    return {
        "id": record_id,
        "vault": vault,
        "layer": _SHARED if shared else "local",
        "kind": kind,
        "title": _text(sidecar.get("title")),
        "status": _text(sidecar.get("status")),
        "updated-at": _text(sidecar.get("updated-at")),
        "labels": _text_map(sidecar.get("labels")),
        "related": _edge_map(sidecar.get("related")),
        "flags": list(flags),
    }


def project_lineage(lineage: derive_mod.Lineage) -> dict:
    """Project one derived lineage into the board's lineage shape.

    The root and every member take the lineage's own vault and shared marker,
    which own-vault edge confinement makes correct for the whole group.
    """
    def project(member: derive_mod.Member) -> dict:
        return project_record(
            member.record_id, member.sidecar,
            vault=lineage.vault, shared=lineage.shared, flags=member.flags,
        )

    return {
        "id": lineage.id,
        "root": project(lineage.root),
        "members": [project(member) for member in lineage.members],
        "completed_count": lineage.completed_count,
    }


def project_warning(file: str, message: str, *, vault: str, shared: bool) -> dict:
    """Project one read failure into the board's warning shape, text verbatim."""
    return {
        "vault": vault,
        "layer": _SHARED if shared else "local",
        "file": file,
        "message": message,
    }


def project_vault(walk: VaultWalk) -> dict:
    """Project one walked vault into the vaults-consulted shape.

    ``error is None`` with ``record_count == 0`` says consulted and empty,
    which a consumer must not confuse with the vault being absent from this
    list entirely — that would mean not consulted.
    """
    return {
        "name": walk.name,
        "shared": walk.shared,
        "record_count": len(walk.records),
        "error": walk.error,
    }


def fence_record(entry: dict) -> dict:
    """Entity-escape a shared record's free-text fields into a new dict."""
    return _escaped(entry, RECORD_FREE_TEXT_FIELDS, shared=entry["layer"] == _SHARED)


def fence_warning(entry: dict) -> dict:
    """Entity-escape a shared warning's free-text fields into a new dict."""
    return _escaped(entry, WARNING_FREE_TEXT_FIELDS, shared=entry["layer"] == _SHARED)


def fence_lineage(entry: dict) -> dict:
    """Entity-escape a shared lineage and every record it carries.

    A record is only ever reached through its lineage, so this is the call that
    protects it. The lineage takes its shared marker from its root: the whole
    group came from one vault, so the root's layer is the lineage's layer.
    """
    fenced = _escaped(
        entry, LINEAGE_FREE_TEXT_FIELDS, shared=entry["root"]["layer"] == _SHARED
    )
    fenced["root"] = fence_record(entry["root"])
    fenced["members"] = [fence_record(member) for member in entry["members"]]
    return fenced


def project_board(walks: Sequence[VaultWalk]) -> dict:
    """Assemble the board from *walks*, with every vault's text verbatim.

    The envelope's top-level keys are exactly ``schema``, ``vaults``,
    ``warnings`` and ``tiers``, and a consumer pins on that shape: a board is
    read by walking the tiers and consulting ``vaults`` for what could not be
    read. A record that belongs to no tier belongs nowhere in the envelope, so
    there is no fifth key holding the walk's raw yield.
    """
    warnings = []
    for walk in walks:
        for warning in walk.warnings:
            warnings.append(
                project_warning(
                    warning.file, warning.message,
                    vault=walk.name, shared=walk.shared,
                )
            )
    lineages = [project_lineage(item) for item in derive_mod.derive_lineages(walks)]
    return {
        "schema": SCHEMA_VERSION,
        "vaults": [project_vault(walk) for walk in walks],
        "warnings": warnings,
        "tiers": {"priority": [], "recency": lineages},
    }


def render_json(board: dict) -> str:
    """Render the board as the machine-readable envelope, shared text escaped."""
    fenced = dict(board)
    fenced["warnings"] = [fence_warning(w) for w in board["warnings"]]
    fenced["tiers"] = {
        tier: [fence_lineage(lineage) for lineage in board["tiers"][tier]]
        for tier in TIERS
    }
    return json.dumps(fenced)


def _neutralize(text: str) -> str:
    """Escape every non-printable character in *text* to a backslash sequence.

    **Security-relevant, and orthogonal to the shared fence.** A record id's
    name half is an on-disk filename stem and a title is a sidecar value;
    neither is character-validated unless the record was written through this
    CLI, and one synced in by git never was. The human view is parsed by eye
    and by ``grep`` as one record per line, so a stem or title holding a
    newline could forge a whole extra record line, and one holding an ANSI
    escape could colour a terminal reading it. Entity-escaping does not touch
    either character, and it only runs on shared vaults, so this runs on every
    line instead.

    ``repr`` is the neutralizer, per character, so printable text — including
    non-ASCII — survives untouched while control characters, ESC, and the
    unicode separators ``str`` reports as unprintable become visible escapes.
    This is the same treatment :func:`record.graph.format_node` gives a node id
    on its way into a guard message, minus the surrounding quotes.
    """
    return "".join(ch if ch.isprintable() else repr(ch)[1:-1] for ch in text)


def _neutralized(entry: dict, fields: Sequence[str]) -> dict:
    """Return a copy of *entry* with every field in *fields* neutralized."""
    return _transformed(entry, fields, _neutralize)


def _pairs(mapping: dict) -> str:
    """Render a label map as ``key=value`` pairs in a stable order."""
    return ", ".join(f"{key}={value}" for key, value in sorted(mapping.items()))


def _edges(mapping: dict) -> str:
    """Render an edge map as ``kind=name`` pairs, stored order within a kind."""
    return ", ".join(
        f"{kind}={name}" for kind in sorted(mapping) for name in mapping[kind]
    )


def _vault_line(entry: dict) -> str:
    """Render one vaults-consulted line.

    Nothing on this line came out of a vault — the name and shared flag are
    configured locally, the count is computed, and the error is composed from
    the configured path plus an OS error — so the declared set neutralized
    here is empty. It is iterated anyway: a later field sourced from vault
    content is fenced by declaring it, with no second edit to make.
    """
    safe = _neutralized(entry, VAULT_FREE_TEXT_FIELDS)
    if safe["error"] is not None:
        detail = f"error: {safe['error']}"
    else:
        count = safe["record_count"]
        detail = f"{count} record{'' if count == 1 else 's'}"
    marker = " [shared]" if safe["shared"] else ""
    return f"  {safe['name']}{marker} — {detail}"


def _warning_line(entry: dict) -> str:
    """Render one warning line, its vault-authored fields neutralized."""
    safe = _neutralized(entry, WARNING_FREE_TEXT_FIELDS)
    return f"  {safe['vault']}  {safe['file']}: {safe['message']}"


def _record_line(entry: dict) -> str:
    """Render one record line, its vault-authored fields neutralized.

    Labels, edges and flags are appended only when the record carries them, so
    the common line stays short enough to scan a whole tier at a glance. The
    edges are what make an ``unresolved-root`` record legible: the raw value
    that resolved to nothing is the diagnostic.
    """
    safe = _neutralized(entry, RECORD_FREE_TEXT_FIELDS)
    parts = [f"    {safe['vault']}  {safe['id']} [{safe['status']}] {safe['title']}"]
    if safe["labels"]:
        parts.append(f"labels: {_pairs(safe['labels'])}")
    if safe["related"]:
        parts.append(f"related: {_edges(safe['related'])}")
    if safe["flags"]:
        parts.append(f"flags: {', '.join(safe['flags'])}")
    return "  ".join(parts)


def _lineage_line(entry: dict) -> str:
    """Render one lineage's header line, its id neutralized."""
    safe = _neutralized(entry, LINEAGE_FREE_TEXT_FIELDS)
    count = safe["completed_count"]
    return f"  {safe['id']}" + (f"  ({count} completed)" if count else "")


def _lineage_block(entry: dict) -> list[str]:
    """Render one lineage as its header line plus a line per rendered record."""
    return [_lineage_line(entry)] + [
        _record_line(record) for record in (entry["root"], *entry["members"])
    ]


def _entry_source(entry: dict) -> tuple[str, str]:
    """The ``(layer, vault)`` an ordinary projected entry came from."""
    return entry["layer"], entry["vault"]


def _lineage_source(entry: dict) -> tuple[str, str]:
    """The ``(layer, vault)`` a lineage came from, read off its root."""
    return _entry_source(entry["root"])


def _fenced_section(
    entries: Sequence[dict],
    block_of: Callable[[dict], Sequence[str]],
    source_of: Callable[[dict], tuple[str, str]] = _entry_source,
) -> list[str]:
    """Render *entries*, splicing each shared vault's block inside the fence.

    Trusted entries print bare and in order; shared entries are grouped by
    their vault and wrapped, the same partition ``lore search`` uses on its own
    human rendering. :func:`wrap_shared` entity-escapes what it wraps, which is
    this mode's whole fence — the entries reach here verbatim, so nothing is
    escaped twice.
    """
    if not entries:
        return [_NONE]
    lines: list[str] = []
    by_vault: dict[str, list[str]] = {}
    for entry in entries:
        layer, vault_name = source_of(entry)
        if layer == _SHARED:
            by_vault.setdefault(vault_name, []).extend(block_of(entry))
        else:
            lines.extend(block_of(entry))
    for vault_name, vault_lines in by_vault.items():
        lines.extend(wrap_shared(vault_name, vault_lines))
    return lines


def render_human(board: dict) -> str:
    """Render the board as the compact debugging view."""
    lines = [_HEADER, ""]

    lines.append("Vaults consulted:")
    lines.extend(_vault_line(entry) for entry in board["vaults"])
    lines.append("")

    lines.append("Warnings:")
    lines.extend(_fenced_section(board["warnings"], lambda e: [_warning_line(e)]))

    for tier in TIERS:
        lines.append("")
        lines.append(f"{tier.capitalize()} tier:")
        lines.extend(
            _fenced_section(board["tiers"][tier], _lineage_block, _lineage_source)
        )

    if not any(board["tiers"][tier] for tier in TIERS):
        lines.extend(["", "nothing in flight"])
    return "\n".join(lines) + "\n"


def emit(walks: Sequence[VaultWalk], *, as_json: bool) -> None:
    """Project *walks* into a board and print it in the requested mode."""
    board = project_board(walks)
    sys.stdout.write(render_json(board) + "\n" if as_json else render_human(board))
