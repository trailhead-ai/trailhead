"""Reference reading shared by the health checks.

Pure: reads the references a record makes (with where each sits) and builds one
candidate lookup from every scan, so a check resolves a reference without
rescanning. A body wikilink's location is its 1-based line in the body; a
list-field entry's is its 1-based position in that list (counting entries that
are not strings); ``parent`` has none.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..record.model import _KEBAB_SEGMENT, KINDS
from .model import ScannedRecord, VaultScan

#: Longest record name accepted as a reference target: a name becomes a
#: ``<name>.json`` file, and filesystems cap a file name at 255 bytes.
MAX_NAME_LENGTH = 255 - len(".json")

_NAME_RE = re.compile(_KEBAB_SEGMENT)
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]*)\]\]")
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_BACKTICK_RUN_RE = re.compile(r"`+")


@dataclass(frozen=True)
class Reference:
    """One reference a record makes.

    ``field`` is ``related <kind>``, ``supersedes``, ``depends-on``, ``parent``
    or ``wikilink``. ``target`` is the raw text, ``default_kind`` the kind a
    bare target means (``None`` for any), ``location`` the integer described in
    the module docstring.
    """

    field: str
    target: str
    default_kind: str | None
    location: int | None


def extract_wikilink_lines(body: str) -> list[tuple[str, int]]:
    """Return ``(target, 1-based line)`` of every ``[[...]]`` in *body* outside code.

    Skips fenced code (backtick or tilde, any length; an unclosed fence runs to
    the end), inline code spans of any backtick count, and indented code
    blocks. ``|alias`` and ``#heading`` are removed from each target. The line
    is where the link opens in the original body; code is blanked in place
    without dropping a newline, so the count is unaffected by it.
    """
    prose = _strip_inline_code(_strip_block_code(body))
    return [
        (re.split(r"[|#]", m.group(1), maxsplit=1)[0], prose.count("\n", 0, m.start()) + 1)
        for m in _WIKILINK_RE.finditer(prose)
    ]


def extract_wikilinks(body: str) -> list[str]:
    """Return the target text of every ``[[...]]`` in *body* outside code."""
    return [target for target, _ in extract_wikilink_lines(body)]


def _strip_block_code(body: str) -> str:
    out: list[str] = []
    fence: tuple[str, int] | None = None
    prev_blank = True
    prev_indented = False
    for line in body.split("\n"):
        if fence is not None:
            out.append("")
            stripped = line.strip()
            if (
                stripped
                and stripped[0] == fence[0]
                and len(stripped) >= fence[1]
                and set(stripped) == {fence[0]}
                and len(line) - len(line.lstrip(" ")) <= 3
            ):
                fence = None
            continue
        opener = _FENCE_OPEN_RE.match(line)
        if opener and not (opener.group(1)[0] == "`" and "`" in opener.group(2)):
            fence = (opener.group(1)[0], len(opener.group(1)))
            out.append("")
            prev_blank, prev_indented = False, False
            continue
        blank = not line.strip()
        indented = line.startswith(("    ", "\t")) and not blank
        if indented and (prev_blank or prev_indented):
            out.append("")
            prev_blank, prev_indented = False, True
            continue
        out.append(line)
        prev_blank = blank
        prev_indented = prev_indented and blank
    return "\n".join(out)


def _strip_inline_code(text: str) -> str:
    out: list[str] = []
    pos = 0
    runs = list(_BACKTICK_RUN_RE.finditer(text))
    by_width: dict[int, list[int]] = {}
    for index, run in enumerate(runs):
        by_width.setdefault(len(run.group()), []).append(index)
    next_of_width = dict.fromkeys(by_width, 0)
    i = 0
    while i < len(runs):
        run = runs[i]
        width = len(run.group())
        peers = by_width[width]
        cursor = next_of_width[width]
        while cursor < len(peers) and peers[cursor] <= i:
            cursor += 1
        next_of_width[width] = cursor
        if cursor == len(peers):
            i += 1
            continue
        close = runs[peers[cursor]]
        out.append(text[pos:run.start()])
        out.append(" " + "\n" * text.count("\n", run.start(), close.end()))
        pos = close.end()
        while i < len(runs) and runs[i].start() < pos:
            i += 1
    out.append(text[pos:])
    return "".join(out)


def resolve(raw: str, default_kind: str | None) -> tuple[str | None, str] | None:
    """Parse *raw* as ``[kind/]name``; ``None`` when it is not record-id shaped."""
    kind, sep, name = raw.rpartition("/")
    if sep:
        if kind not in KINDS:
            return None
    else:
        kind = default_kind
    if len(name) > MAX_NAME_LENGTH or not _NAME_RE.fullmatch(name):
        return None
    return kind, name


def iter_references(record: ScannedRecord) -> Iterable[Reference]:
    """Yield every reference in *record*."""
    yield from _sidecar_references(record)
    if isinstance(record.body, str):
        for target, line in extract_wikilink_lines(record.body):
            yield Reference("wikilink", target, None, line)


def _sidecar_references(record: ScannedRecord) -> Iterable[Reference]:
    sidecar = record.sidecar if isinstance(record.sidecar, dict) else {}

    related = sidecar.get("related")
    if isinstance(related, dict):
        for rel_kind, entries in related.items():
            if rel_kind in KINDS and isinstance(entries, list):
                for position, entry in enumerate(entries, 1):
                    if isinstance(entry, str):
                        yield Reference(f"related {rel_kind}", entry, rel_kind, position)

    supersedes = sidecar.get("supersedes")
    if isinstance(supersedes, list):
        for position, entry in enumerate(supersedes, 1):
            if isinstance(entry, str):
                yield Reference("supersedes", entry, None, position)

    depends_on = sidecar.get("depends-on")
    if record.kind in ("task", "spec", "adr") and isinstance(depends_on, list):
        for position, entry in enumerate(depends_on, 1):
            if isinstance(entry, str):
                if record.kind == "task":
                    yield Reference("depends-on", entry, "task", position)
                else:
                    yield Reference("depends-on", entry.partition("@")[0], None, position)

    parent = sidecar.get("parent")
    if record.kind == "task" and isinstance(parent, str):
        yield Reference("parent", parent, "task", None)


class CandidateIndex:
    """Every scan's records, indexed for resolving a reference; build once per run."""

    def __init__(self, scans: Sequence[VaultScan]) -> None:
        self._readable: dict[tuple[str, str], list[ScannedRecord]] = {}
        self._readable_by_name: dict[str, list[ScannedRecord]] = {}
        self._unreadable: set[tuple[str, str]] = set()
        self._unreadable_names: set[str] = set()
        self._claims: dict[tuple[str | None, str], list[ScannedRecord]] = {}
        for scan in scans:
            for record in scan.records:
                self._readable.setdefault((record.kind, record.name), []).append(record)
                self._readable_by_name.setdefault(record.name, []).append(record)
                claimed: set[tuple[str | None, str]] = set()
                for ref in _sidecar_references(record):
                    if ref.field != "supersedes":
                        continue
                    parsed = resolve(ref.target, ref.default_kind)
                    if parsed is not None and parsed not in claimed:
                        claimed.add(parsed)
                        self._claims.setdefault(parsed, []).append(record)
            for gone in scan.unreadable:
                self._unreadable.add((gone.kind, gone.name))
                self._unreadable_names.add(gone.name)

    def lookup(self, kind: str | None, name: str) -> tuple[tuple[ScannedRecord, ...], bool]:
        """Return the readable records *kind*/*name* can resolve to, and whether
        an unreadable record could also be it. A ``None`` kind matches by name alone."""
        if kind is None:
            return tuple(self._readable_by_name.get(name, ())), name in self._unreadable_names
        return tuple(self._readable.get((kind, name), ())), (kind, name) in self._unreadable

    def superseded_by(self, kind: str, name: str) -> tuple[ScannedRecord, ...]:
        """Return every record, in any vault, whose ``supersedes`` names *kind*/*name*
        (a bare ``supersedes`` entry names every kind)."""
        return tuple(self._claims.get((kind, name), ())) + tuple(self._claims.get((None, name), ()))


def build_candidates(scans: Sequence[VaultScan]) -> CandidateIndex:
    """Build the run's candidate lookup from every scan."""
    return CandidateIndex(scans)
