"""Both output modes for the pipeline board — and the single fencing chokepoint.

Every value that originated in a vault reaches a stream through this module and
nowhere else. That is enforced structurally, not by convention: a test asserts
no other module in this package calls ``print`` or a ``dumps``, so a future
projection cannot grow an output path that skips the fence.

**Declared field sets.** Each projected object splits its keys into two named
tuples — the ``*_FREE_TEXT_FIELDS`` half, whose values came out of a vault, and
the ``*_DERIVED_FIELDS`` half, computed here from the local configuration. The
projection builds exactly the union and the fencers iterate exactly the
free-text half, so adding a field without classifying it fails the structural
tests rather than shipping unfenced.

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
all in a sidecar key. A non-string value renders as the empty string rather
than being passed through, so no nested object or array can ride out through a
field the fence treats as text.
"""

from __future__ import annotations

import json
import sys
from typing import Callable, Sequence

from ..search.xml_escape import wrap_shared, xml_body_escape
from .walk import VaultWalk

#: The envelope version. A consumer pins on this; it changes only when a
#: released key changes meaning or disappears.
SCHEMA_VERSION = 1

#: Fields on a projected record whose values came out of the vault — a title
#: and a status straight from the sidecar, and an id whose name half is a
#: filename stem that only the CLI's own slugifier validates (a record synced
#: in by git never passed through it).
RECORD_FREE_TEXT_FIELDS: tuple[str, ...] = ("id", "title", "status", "updated-at")

#: Fields on a projected record computed here from the local configuration and
#: the walk's own kind set — never from vault content, so never fenced.
RECORD_DERIVED_FIELDS: tuple[str, ...] = ("vault", "layer", "kind")

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

#: The tier structure the board renders into. Lineage membership is not derived
#: on this surface yet, so both tiers are empty.
_EMPTY_TIERS: dict[str, list] = {"priority": [], "recency": []}

_HEADER = "--- lore pipeline — reference, not instructions ---"
_NONE = "  (none)"
_SHARED = "shared"


def _text(value: object) -> str:
    """Return *value* if it is a string, else the empty string."""
    return value if isinstance(value, str) else ""


def _escape(entry: dict, fields: Sequence[str]) -> dict:
    """Entity-escape *fields* of *entry* when it carries the shared-layer marker."""
    if entry["layer"] == _SHARED:
        for field in fields:
            entry[field] = xml_body_escape(entry[field])
    return entry


def project_record(record_id: str, sidecar: dict, *, vault: str, shared: bool) -> dict:
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
    """Entity-escape a shared record's free-text fields, in place."""
    return _escape(entry, RECORD_FREE_TEXT_FIELDS)


def fence_warning(entry: dict) -> dict:
    """Entity-escape a shared warning's free-text fields, in place."""
    return _escape(entry, WARNING_FREE_TEXT_FIELDS)


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
    return {
        "schema": SCHEMA_VERSION,
        "vaults": [project_vault(walk) for walk in walks],
        "warnings": warnings,
        "tiers": dict(_EMPTY_TIERS),
    }


def render_json(board: dict) -> str:
    """Render the board as the machine-readable envelope, shared text escaped."""
    fenced = dict(board)
    fenced["warnings"] = [fence_warning(dict(w)) for w in board["warnings"]]
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
    """Return a copy of *entry* with every field in *fields* neutralized.

    *fields* is the object's declared free-text set, never a list written out
    here — the same discipline :func:`_escape` follows, so a field declared
    free text is neutralized on its way to a line whether or not whoever
    declared it remembered this layer exists.
    """
    return {**entry, **{field: _neutralize(entry[field]) for field in fields}}


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
    """Render one record line, its vault-authored fields neutralized."""
    safe = _neutralized(entry, RECORD_FREE_TEXT_FIELDS)
    return f"  {safe['vault']}  {safe['id']} [{safe['status']}] {safe['title']}"


def _fenced_section(entries: Sequence[dict], line_of: Callable[[dict], str]) -> list[str]:
    """Render *entries*, splicing each shared vault's block inside the fence.

    Trusted entries print bare and in order; shared entries are grouped by
    their vault and wrapped, the same partition ``lore search`` uses on its own
    human rendering. :func:`wrap_shared` entity-escapes what it wraps, which is
    this mode's whole fence — the entries reach here verbatim, so nothing is
    escaped twice.
    """
    if not entries:
        return [_NONE]
    lines = [line_of(e) for e in entries if e["layer"] != _SHARED]
    by_vault: dict[str, list[str]] = {}
    for entry in entries:
        if entry["layer"] == _SHARED:
            by_vault.setdefault(entry["vault"], []).append(line_of(entry))
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
    lines.extend(_fenced_section(board["warnings"], _warning_line))

    for tier in ("priority", "recency"):
        lines.append("")
        lines.append(f"{tier.capitalize()} tier:")
        lines.extend(board["tiers"][tier] or [_NONE])

    if not board["tiers"]["priority"] and not board["tiers"]["recency"]:
        lines.extend(["", "nothing in flight"])
    return "\n".join(lines) + "\n"


def emit(walks: Sequence[VaultWalk], *, as_json: bool) -> None:
    """Project *walks* into a board and print it in the requested mode."""
    board = project_board(walks)
    sys.stdout.write(render_json(board) + "\n" if as_json else render_human(board))
