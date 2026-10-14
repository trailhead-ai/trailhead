"""A spec's acceptance criteria and their change-history state.

Pure string and data functions: no I/O, no vault access. This module is the
single reader of which criteria a spec body declares and of which criteria its
sidecar ``change-history`` records as removed; every caller that needs either
goes through it.

Invariants:

- ``declared_criteria`` walks the body under its sole unmasked
  ``## Acceptance Criteria`` heading (case-insensitive, line-anchored). Fenced
  code and HTML comments are invisible, ``###`` sub-headings are grouping
  markers, and indented lines (wrapped prose, sub-bullets, loose-list
  continuations) fold into the top-level bullet they follow. Only bullets that
  open with ``**ACn.**`` are criteria; the returned text is the bullet block.
- Criterion identifiers are numbered and never reused. The next unused number
  is ``max(declared-ever) + 1`` where declared-ever is the old body's
  identifiers plus every identifier a history entry records as removed.
- An amend may remove criteria, edit text in place, and add criteria that run
  contiguously from the next unused number. It may not skip a number, reuse a
  removed identifier, renumber (text now under a different identifier), repeat
  an identifier, or add or remove a non-canonical identifier.
- Stored history is validated, never repaired: a history that is not a list, an
  entry that is not an object, or a ``criteria`` value that is anything other
  than ``{"removed": [<non-empty, unique, canonical ids>]}`` raises
  :class:`CriteriaError`. Silently dropping a malformed entry would let a
  removed identifier be reused.
- Error and refusal strings echo only canonical identifiers (``AC`` followed by
  a positive integer without leading zeros), never body text.

Lore carries its own copy of the criterion walk: plugins stand alone, so this
module must not import craft. A parity test in craft's suite holds the walk in
agreement with craft's ``parse_criteria_with_text``. Masking and line
splitting are shared with :mod:`lore.record.sections`.
"""
from __future__ import annotations

import re
from typing import Any

from lore.record.sections import _content, _mask, _split_lines

_HEADING_RE = re.compile(r"## Acceptance Criteria", re.IGNORECASE)
_CRITERION_RE = re.compile(r"^-\s+\*\*(AC\d+)\.\*\*")
_CANONICAL_RE = re.compile(r"AC[1-9][0-9]*")


class CriteriaError(ValueError):
    """A body's criteria section or a stored history cannot be read."""


def _number(identifier: str) -> int:
    return int(identifier[2:])


def _canonical(identifier: str) -> bool:
    return _CANONICAL_RE.fullmatch(identifier) is not None


def _entries(body: str) -> list[tuple[str | None, str]]:
    lines = [_content(raw) for raw in _split_lines(body)]
    masked = _mask(_split_lines(body))

    starts = [i for i, line in enumerate(lines) if not masked[i] and _HEADING_RE.fullmatch(line)]
    if not starts:
        raise CriteriaError("spec body has no '## Acceptance Criteria' heading")
    if len(starts) > 1:
        raise CriteriaError("spec body has more than one '## Acceptance Criteria' heading")

    n = len(lines)
    out: list[tuple[str | None, str]] = []
    i = starts[0] + 1
    while i < n:
        if masked[i]:
            i += 1
            continue
        line = lines[i]
        if line.startswith("## "):
            break
        if not line.startswith("- "):
            i += 1
            continue
        block = [line]
        j = i + 1
        while j < n:
            if masked[j]:
                j += 1
                continue
            nxt = lines[j]
            if nxt.startswith(("## ", "### ", "- ")):
                break
            if nxt.strip() == "":
                k = j + 1
                while k < n and (masked[k] or lines[k].strip() == ""):
                    k += 1
                if k < n and lines[k][:1] in (" ", "\t"):
                    block.append(nxt)
                    j += 1
                    continue
                break
            if nxt[:1] in (" ", "\t"):
                block.append(nxt)
                j += 1
                continue
            break
        m = _CRITERION_RE.match(line)
        out.append((m.group(1) if m else None, "\n".join(block)))
        i = j
    return out


def declared_criteria(body: str) -> list[tuple[str, str]]:
    """Identified criteria under the body's criteria heading, in document order.

    Raises :class:`CriteriaError` when the heading is missing or appears more
    than once unmasked.
    """
    return [(ident, text) for ident, text in _entries(body) if ident is not None]


def _recorded_removals(history: Any) -> dict[str, int]:
    """Each identifier *history* records as removed, mapped to the 1-based number
    of the first entry that recorded it."""
    if history is None:
        return {}
    if not isinstance(history, list):
        raise CriteriaError("change-history is not a list")
    removed: dict[str, int] = {}
    for number, entry in enumerate(history, start=1):
        if not isinstance(entry, dict):
            raise CriteriaError("a change-history entry is not an object")
        if "criteria" not in entry:
            continue
        crit = entry["criteria"]
        if not isinstance(crit, dict) or set(crit) != {"removed"}:
            raise CriteriaError("a change-history entry's criteria is not {'removed': [...]}")
        ids = crit["removed"]
        if not isinstance(ids, list) or not ids:
            raise CriteriaError("a change-history entry's criteria.removed is not a non-empty list")
        if len(set(map(str, ids))) != len(ids):
            raise CriteriaError("a change-history entry's criteria.removed repeats an identifier")
        for ident in ids:
            if not isinstance(ident, str) or not _canonical(ident):
                raise CriteriaError("a change-history entry's criteria.removed holds a non-canonical identifier")
            removed.setdefault(ident, number)
    return removed


def criteria_state(body: str, history: list | None) -> dict:
    """``{"declared": [...], "removed": [...]}`` for *body* under *history*.

    ``declared`` is the identifiers the body declares, in document order.
    ``removed`` is every identifier any history entry records as removed that the
    body does not declare now, in numeric order.
    """
    recorded = _recorded_removals(history)
    declared = [ident for ident, _ in declared_criteria(body)]
    gone = recorded.keys() - set(declared)
    return {"declared": declared, "removed": sorted(gone, key=_number)}


def _safe_entries(body: str) -> tuple[str, list[tuple[str | None, str]]]:
    """``(status, entries)`` where status is ``ok``, ``none`` or ``duplicate``."""
    try:
        return "ok", _entries(body)
    except CriteriaError:
        pass
    lines = _split_lines(body)
    masked = _mask(lines)
    count = sum(1 for i, raw in enumerate(lines) if not masked[i] and _HEADING_RE.fullmatch(_content(raw)))
    return ("duplicate" if count > 1 else "none"), []


def _body_text(text: str) -> str:
    return " ".join(_CRITERION_RE.sub("", text, count=1).split())


def check_criteria_amend(
    old_body: str, new_body: str, history: list | None
) -> tuple[list[str], list[str]]:
    """``(removed_now, refusals)`` for an amend from *old_body* to *new_body*.

    ``removed_now`` is the canonical identifiers declared before and absent
    after, in numeric order; it is computed whether or not there are refusals.
    ``refusals`` are messages naming only canonical identifiers. Raises
    :class:`CriteriaError` only for a malformed *history*.
    """
    recorded = _recorded_removals(history)
    old_status, old_entries = _safe_entries(old_body)
    new_status, new_entries = _safe_entries(new_body)

    old_ids = [i for i, _ in old_entries if i is not None]
    new_ids = [i for i, _ in new_entries if i is not None]
    old_set, new_set = set(old_ids), set(new_ids)

    refusals: list[str] = []
    if new_status == "duplicate":
        refusals.append("the new body has more than one '## Acceptance Criteria' heading")
    elif new_status == "none" and old_status != "none":
        refusals.append("the new body has no '## Acceptance Criteria' heading")

    removed_now = sorted((i for i in old_set - new_set if _canonical(i)), key=_number)

    ever = [_number(i) for i in old_set | recorded.keys() if _canonical(i)]
    base = max(ever, default=0) + 1
    next_unused = f"AC{base}"

    seen: set[str] = set()
    repeated: set[str] = set()
    for ident in new_ids:
        (repeated if ident in seen else seen).add(ident)
    for ident in sorted(repeated, key=lambda s: (not _canonical(s), _number(s))):
        if _canonical(ident):
            refusals.append(f"{ident} is declared more than once")
        else:
            refusals.append("a non-canonical criterion identifier is declared more than once")

    if any(not _canonical(i) for i in new_set - old_set):
        refusals.append("a non-canonical criterion identifier was added; identifiers are AC1, AC2, ...")
    if any(not _canonical(i) for i in old_set - new_set):
        refusals.append("a non-canonical criterion identifier was removed; it can only be left unchanged")

    added = sorted((i for i in new_set - old_set if _canonical(i)), key=_number)
    fresh: list[str] = []
    for ident in added:
        if ident in recorded:
            refusals.append(
                f"{ident} was removed earlier and cannot be reused; the next unused number is "
                f"{next_unused}. If it was removed by mistake, restore it with "
                f"`lore record amend --revert-to {recorded[ident]}`"
            )
        else:
            fresh.append(ident)
    expected = {f"AC{base + k}" for k in range(len(fresh))}
    gap = base
    while f"AC{gap}" in fresh:
        gap += 1
    for ident in fresh:
        if ident not in expected:
            refusals.append(f"{ident} is not the next unused number; the next unused is AC{gap}")

    old_text_ids: dict[str, set[str]] = {}
    for ident, text in old_entries:
        if ident is not None and _canonical(ident):
            norm = _body_text(text)
            if norm:
                old_text_ids.setdefault(norm, set()).add(ident)
    flagged: set[str] = set()
    for ident, text in new_entries:
        if ident is None or not _canonical(ident) or ident in flagged:
            continue
        owners = old_text_ids.get(_body_text(text))
        if owners and ident not in owners:
            flagged.add(ident)
            refusals.append(
                f"{ident} carries text that belongs to a different criterion; criteria are never "
                f"relabelled: remove one and add the next number ({next_unused}) instead"
            )

    return removed_now, refusals
