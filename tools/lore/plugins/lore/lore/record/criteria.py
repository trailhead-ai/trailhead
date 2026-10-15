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
- A history entry's ``criteria`` object carries any non-empty subset of
  ``removed``, ``reopened`` and ``closed`` (each a non-empty list of unique
  canonical ids; no id in both ``reopened`` and ``closed``), plus ``ledger`` (a
  list of unique task ids, each matching :data:`TASK_ID_RE`) exactly when
  ``reopened`` is present. :func:`criteria_problem` is the one definition of that
  shape; the write-side validator in :mod:`lore.record.model` calls it too.
  A reopen's ``ledger`` is the stored body's ledger entry task ids at that moment,
  so coverage recorded before it can be told from coverage recorded after.
- Stored history is validated, never repaired: a history that is not a list, an
  entry that is not an object, or a ``criteria`` value that breaks the shape
  above raises :class:`CriteriaError`. Silently dropping a malformed entry would
  let a removed identifier be reused. An entry with no ``criteria`` key is skipped;
  this module never judges ``diff``.
- ``criteria_state`` folds the marks in history order: per id the latest reopen
  or close wins, a removal clears any mark, and only ids the body declares now
  are kept.
- ``ledger_entries`` is lore's copy of craft's ``parse_ledger_entries``: for each
  canonical bullet in the sole unmasked ``## Slices`` section whose whole span
  (the bullet plus its indented continuations) ends in a parenthetical opening
  with `` `task/<id>` `` it returns ``(task_id, covers, partial)``. A second
  unmasked ``## Slices`` heading or a malformed coverage token raises
  :class:`CriteriaError`, as craft's raises ``ValueError``.
- Error and refusal strings echo only canonical identifiers (``AC`` followed by
  a positive integer of at most nine digits without leading zeros), never body
  text. Anything longer is non-canonical, so no integer conversion ever sees an
  unbounded string.

Lore carries its own copy of the criterion walk: plugins stand alone, so this
module must not import craft. A parity test in craft's suite holds the walk in
agreement with craft's ``parse_criteria_with_text``, and another holds
``ledger_entries`` in agreement with ``parse_ledger_entries``. Masking and line
splitting are shared with :mod:`lore.record.sections`.
"""
from __future__ import annotations

import re
from typing import Any

from lore.record.sections import _content, _mask, _split_lines

_HEADING_RE = re.compile(r"## Acceptance Criteria", re.IGNORECASE)
_CRITERION_RE = re.compile(r"^-\s+\*\*(AC\d+)\.\*\*")
_CANONICAL_RE = re.compile(r"AC[1-9][0-9]{0,8}")
TASK_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
_MARK_KEYS = ("removed", "reopened", "closed")
_CRITERIA_KEYS = frozenset((*_MARK_KEYS, "ledger"))
_SLICES_RE = re.compile(r"## Slices", re.IGNORECASE)
_LEDGER_NONCANONICAL_MARKER_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s")
_LEDGER_TRAILING_PAREN_RE = re.compile(r"\(([^()]*)\)\s*$")
_LEDGER_FIELDS_RE = re.compile(r"^`task/[^`]+`, closed [^,]+(?:, covers (?P<covers>.+))?$")
_LEDGER_PARTIAL_SPLIT_RE = re.compile(r", partially covers (?P<partial>.+)$")
_LEDGER_TASK_ID_RE = re.compile(r"^`task/([^`]+)`")
_COVERS_RE = re.compile(r"\AAC\d+(,\ ?AC\d+)*\Z")


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


def valid_task_id(value: object) -> bool:
    """True when *value* is a string of the shape :data:`TASK_ID_RE`."""
    return isinstance(value, str) and TASK_ID_RE.fullmatch(value) is not None


def _ids_problem(name: str, ids: object) -> str | None:
    if not isinstance(ids, list) or not ids:
        return f"'{name}' must be a non-empty list of criterion identifiers"
    if not all(isinstance(i, str) and _canonical(i) for i in ids):
        return f"'{name}' must hold identifiers of the form AC1, AC2, ... (no leading zeros, at most nine digits)"
    if len(set(ids)) != len(ids):
        return f"'{name}' must not repeat an identifier"
    return None


def criteria_problem(crit: object) -> str | None:
    """Why *crit* is not a valid ``change-history`` ``criteria`` object, or ``None``.

    The message names no value from *crit*.
    """
    if not isinstance(crit, dict) or not crit:
        return "must be a non-empty object"
    if not set(crit) <= _CRITERIA_KEYS:
        return "has an unsupported key: only removed, reopened, closed and ledger are allowed"
    if not set(crit) & set(_MARK_KEYS):
        return "must carry at least one of removed, reopened, closed"
    for name in _MARK_KEYS:
        if name in crit and (problem := _ids_problem(name, crit[name])):
            return problem
    if set(crit.get("reopened", ())) & set(crit.get("closed", ())):
        return "must not name an identifier in both 'reopened' and 'closed'"
    if ("ledger" in crit) != ("reopened" in crit):
        return "must carry 'ledger' exactly when it carries 'reopened'"
    if "ledger" in crit:
        ledger = crit["ledger"]
        if not isinstance(ledger, list):
            return "'ledger' must be a list of task ids"
        if not all(valid_task_id(t) for t in ledger):
            return "'ledger' must hold task ids matching [a-z0-9][a-z0-9._-]*"
        if len(set(ledger)) != len(ledger):
            return "'ledger' must not repeat a task id"
    return None


def has_mark(crit: object) -> bool:
    """True when *crit* is a valid criteria object carrying ``reopened`` or ``closed``."""
    return criteria_problem(crit) is None and bool({"reopened", "closed"} & set(crit))


def _fold(history: Any) -> tuple[dict[str, int], dict[str, list[str] | None]]:
    """``(removed, marks)`` after folding *history* in order.

    ``removed`` maps each identifier any entry records as removed to the 1-based
    number of the most recent entry that recorded it. ``marks`` maps each
    identifier whose latest mark is live to its reopen snapshot, or ``None`` for
    a close; a removal clears the identifier's mark.
    """
    if history is None:
        return {}, {}
    if not isinstance(history, list):
        raise CriteriaError("change-history is not a list")
    removed: dict[str, int] = {}
    marks: dict[str, list[str] | None] = {}
    for number, entry in enumerate(history, start=1):
        if not isinstance(entry, dict):
            raise CriteriaError("a change-history entry is not an object")
        if "criteria" not in entry:
            continue
        crit = entry["criteria"]
        if (problem := criteria_problem(crit)) is not None:
            raise CriteriaError(f"a change-history entry's criteria {problem}")
        for ident in crit.get("reopened", ()):
            marks[ident] = list(crit["ledger"])
        for ident in crit.get("closed", ()):
            marks[ident] = None
        for ident in crit.get("removed", ()):
            removed[ident] = number
            marks.pop(ident, None)
    return removed, marks


def _recorded_removals(history: Any) -> dict[str, int]:
    return _fold(history)[0]


def criteria_state(body: str, history: list | None) -> dict:
    """``{"declared", "removed", "reopened", "closed"}`` for *body* under *history*.

    ``declared`` is the identifiers the body declares, in document order.
    ``removed`` is every identifier any history entry records as removed that the
    body does not declare now, in numeric order. ``reopened`` maps each declared
    identifier whose latest mark is a reopen to the ledger task ids recorded
    with it; ``closed`` lists the declared identifiers whose latest mark is a
    close, in numeric order.
    """
    recorded, marks = _fold(history)
    declared = [ident for ident, _ in declared_criteria(body)]
    live = set(declared)
    gone = recorded.keys() - live
    ordered = sorted((i for i in marks if i in live), key=_number)
    return {
        "declared": declared,
        "removed": sorted(gone, key=_number),
        "reopened": {i: marks[i] for i in ordered if marks[i] is not None},
        "closed": [i for i in ordered if marks[i] is None],
    }


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

    removed_now = (
        []
        if new_status == "duplicate"
        else sorted((i for i in old_set - new_set if _canonical(i)), key=_number)
    )

    ever = [_number(i) for i in old_set | recorded.keys() if _canonical(i)]
    base = max(ever, default=0) + 1
    next_unused = f"AC{base}"

    seen: set[str] = set()
    repeated: set[str] = set()
    for ident in new_ids:
        (repeated if ident in seen else seen).add(ident)
    for ident in sorted(repeated, key=lambda s: (0, _number(s)) if _canonical(s) else (1, 0)):
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
                f"relabelled: moving text to a new number takes two amends, first remove the "
                f"old criterion, then add the next number ({next_unused})"
            )

    return removed_now, refusals


def _select_structured_span(block: list[str]) -> list[str]:
    n = len(block)
    selected = [block[0]]
    k = 1
    while k < n:
        line = block[k]
        if not line.strip():
            j = k + 1
            while j < n and not block[j].strip():
                j += 1
            if j < n and block[j][:1] in (" ", "\t"):
                selected.extend(block[k:j])
                k = j
                continue
            break
        if line[:1] in (" ", "\t"):
            selected.append(line)
            k += 1
            continue
        break
    return selected


def _parse_covers(value: str) -> list[str]:
    if not _COVERS_RE.match(value):
        raise CriteriaError("a ledger coverage token is not a valid identifier list")
    identifiers = [t.lstrip(" ") for t in value.split(",")]
    if len(set(identifiers)) != len(identifiers):
        raise CriteriaError("a ledger coverage token repeats an identifier")
    return identifiers


def _entry_fields(text: str) -> tuple[str, list[str], list[str]] | None:
    paren = _LEDGER_TRAILING_PAREN_RE.search(text)
    if paren is None:
        return None
    paren_text = paren.group(1)
    task = _LEDGER_TASK_ID_RE.match(paren_text)
    if task is None:
        return None
    partial_match = _LEDGER_PARTIAL_SPLIT_RE.search(paren_text)
    if partial_match is not None:
        partial_value = partial_match.group("partial")
        head = paren_text[: partial_match.start()]
    else:
        partial_value = None
        head = paren_text
    fields = _LEDGER_FIELDS_RE.match(head)
    covers_value = fields.group("covers") if fields else None
    covers = _parse_covers(covers_value) if covers_value else []
    partial = _parse_covers(partial_value) if partial_value else []
    return task.group(1), covers, partial


def ledger_entries(body: str) -> list[tuple[str, list[str], list[str]]]:
    """``(task_id, covers, partial)`` for each canonical entry of the ``## Slices`` ledger.

    Entries come back in document order. A body with no unmasked ``## Slices``
    heading gives ``[]``. Raises :class:`CriteriaError` for a second unmasked
    heading or a malformed ``covers`` / ``partially covers`` token.
    """
    raw = _split_lines(body)
    lines = [_content(r) for r in raw]
    masked = _mask(raw)
    starts = [i for i, line in enumerate(lines) if not masked[i] and _SLICES_RE.fullmatch(line)]
    if not starts:
        return []
    if len(starts) > 1:
        raise CriteriaError("spec body has more than one '## Slices' heading")

    blocks: list[tuple[bool, list[str]]] = []
    for i in range(starts[0] + 1, len(lines)):
        if masked[i]:
            continue
        line = lines[i]
        if line.startswith("## "):
            break
        if line.startswith("- "):
            blocks.append((True, [line]))
        elif _LEDGER_NONCANONICAL_MARKER_RE.match(line):
            blocks.append((False, [line]))
        elif blocks:
            blocks[-1][1].append(line)

    entries: list[tuple[str, list[str], list[str]]] = []
    for canonical, block in blocks:
        if not canonical:
            continue
        text = " ".join(line.strip() for line in _select_structured_span(block) if line.strip())
        parsed = _entry_fields(text)
        if parsed is not None:
            entries.append(parsed)
    return entries


def _id_refusal(ident: object) -> str:
    if isinstance(ident, str):
        m = re.fullmatch(r"[Aa][Cc]0*([1-9][0-9]{0,8})", ident)
        if m:
            return f"{ident} is not a criterion identifier; use AC{m.group(1)}"
    return "a value is not a criterion identifier; identifiers are AC1, AC2, ..."


def check_criteria_marks(
    stored_body: str,
    new_body: str,
    history: list | None,
    reopen: list[str],
    close: list[str],
) -> tuple[dict[str, list[str]], list[str]]:
    """``(voided, refusals)`` for reopening *reopen* and closing *close*.

    ``voided`` maps each reopened identifier to the task ids of the entries in
    *stored_body*'s ledger that name it, covering or partially, in ledger order;
    an identifier no entry names is left out. ``refusals`` are messages that name
    only canonical identifiers, each with its fix. Raises :class:`CriteriaError`
    only for a malformed *history*.
    """
    recorded, marks = _fold(history)
    refusals: list[str] = []
    if not reopen and not close:
        return {}, refusals

    seen: set[str] = set()
    for ident in [*reopen, *close]:
        if not isinstance(ident, str) or not _canonical(ident):
            refusals.append(_id_refusal(ident))
    for ident in sorted(set(reopen) & set(close), key=lambda s: (0, _number(s)) if _canonical(s) else (1, 0)):
        if _canonical(ident):
            refusals.append(f"{ident} is named to reopen and to close; choose one")
    for ident in [*reopen, *close]:
        if isinstance(ident, str) and _canonical(ident) and ident not in set(reopen) & set(close):
            if ident in seen:
                refusals.append(f"{ident} is named more than once; name it once")
            seen.add(ident)
    canonical = [i for i in dict.fromkeys([*reopen, *close]) if isinstance(i, str) and _canonical(i)]

    try:
        declared = {i for i, _ in declared_criteria(new_body)}
    except CriteriaError as exc:
        refusals.append(f"the amended body's criteria cannot be read: {exc}")
        declared = None
    if declared is not None:
        for ident in canonical:
            if ident not in declared:
                refusals.append(
                    f"{ident} is not declared in the amended body; name a criterion the spec declares"
                )

    try:
        ledger = ledger_entries(stored_body)
    except CriteriaError as exc:
        refusals.append(f"the stored ledger cannot be read: {exc}")
        return {}, refusals

    if reopen and any(not valid_task_id(task) for task, _, _ in ledger):
        refusals.append(
            "cannot reopen: a ledger entry in the stored body has a task id outside "
            "[a-z0-9][a-z0-9._-]*; correct that entry first"
        )

    named: dict[str, list[str]] = {}
    for task, covers, partial in ledger:
        for ident in dict.fromkeys([*covers, *partial]):
            if task not in named.setdefault(ident, []):
                named[ident].append(task)

    for ident in close:
        if not (isinstance(ident, str) and _canonical(ident)) or ident in reopen:
            continue
        if ident in marks and marks[ident] is None:
            refusals.append(f"{ident} is already closed; nothing to do")
        elif ident not in named:
            refusals.append(
                f"{ident} cannot be closed: no slice has covered it, so the close would not count. "
                "Cover it in a slice, or remove the criterion by amend"
            )

    voided = {i: named[i] for i in dict.fromkeys(reopen) if isinstance(i, str) and i in named and _canonical(i)}
    return voided, refusals
