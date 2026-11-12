"""Dangling-reference check: references that point at no record in any vault.

Pure: takes the scan of every configured vault and returns data. A reference
dangles when no record with that kind and name exists in any configured vault;
a bare name dangles only when no record of any kind has it. When any vault was
not fully read the check is skipped, because nothing can then be confirmed
missing.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field

from ..record.model import _KEBAB_SEGMENT, KINDS
from .model import ScannedRecord, VaultScan

SESSION_KIND = "session"

#: Longest record name accepted as a reference target: a name becomes a
#: ``<name>.json`` file, and filesystems cap a file name at 255 bytes.
MAX_NAME_LENGTH = 255 - len(".json")

_NAME_RE = re.compile(_KEBAB_SEGMENT)
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]*)\]\]")
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_BACKTICK_RUN_RE = re.compile(r"`+")


@dataclass(frozen=True)
class Finding:
    """One reference that dangles, or is not shaped like a record id.

    ``field`` is ``related <kind>``, ``supersedes``, ``depends-on``, ``parent``
    or ``wikilink``. ``target`` is ``<kind>/<name>`` (a bare name for a bare
    reference) and is ``None`` when ``malformed``: the offending text is never
    carried.
    """

    vault: str
    kind: str
    name: str
    updated_at: str | None
    field: str
    target: str | None
    malformed: bool = False


@dataclass(frozen=True)
class DanglingResult:
    """Findings, and per selected vault the number of records judged."""

    findings: list[Finding] = field(default_factory=list)
    judged: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DanglingSkipped:
    """The check could not run; ``vaults`` names the vaults at fault."""

    vaults: tuple[str, ...]


def extract_wikilinks(body: str) -> list[str]:
    """Return the target text of every ``[[...]]`` in *body* outside code.

    Skips fenced code (backtick or tilde, any length; an unclosed fence runs to
    the end), inline code spans of any backtick count, and indented code
    blocks. ``|alias`` and ``#heading`` are removed from each target.
    """
    prose = _strip_inline_code(_strip_block_code(body))
    return [re.split(r"[|#]", m.group(1), maxsplit=1)[0] for m in _WIKILINK_RE.finditer(prose)]


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
    i = 0
    while i < len(runs):
        run = runs[i]
        width = len(run.group())
        close = next((r for r in runs[i + 1:] if len(r.group()) == width), None)
        if close is None:
            i += 1
            continue
        out.append(text[pos:run.start()])
        out.append(" ")
        pos = close.end()
        while i < len(runs) and runs[i].start() < pos:
            i += 1
    out.append(text[pos:])
    return "".join(out)


def _resolve(raw: str, default_kind: str | None) -> tuple[str | None, str] | None:
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


def _references(record: ScannedRecord) -> Iterable[tuple[str, str, str | None]]:
    """Yield ``(field, raw target, default kind)`` for every reference in *record*."""
    sidecar = record.sidecar if isinstance(record.sidecar, dict) else {}

    related = sidecar.get("related")
    if isinstance(related, dict):
        for rel_kind, entries in related.items():
            if rel_kind in KINDS and isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, str):
                        yield f"related {rel_kind}", entry, rel_kind

    supersedes = sidecar.get("supersedes")
    if isinstance(supersedes, list):
        for entry in supersedes:
            if isinstance(entry, str):
                yield "supersedes", entry, None

    depends_on = sidecar.get("depends-on")
    if record.kind in ("task", "spec", "adr") and isinstance(depends_on, list):
        for entry in depends_on:
            if isinstance(entry, str):
                if record.kind == "task":
                    yield "depends-on", entry, "task"
                else:
                    yield "depends-on", entry.partition("@")[0], None

    parent = sidecar.get("parent")
    if record.kind == "task" and isinstance(parent, str):
        yield "parent", parent, "task"

    if isinstance(record.body, str):
        for target in extract_wikilinks(record.body):
            yield "wikilink", target, None


def check_dangling(
    scans: Sequence[VaultScan], selected: Collection[str]
) -> DanglingResult | DanglingSkipped:
    """Check every selected vault's records against the records of all *scans*."""
    at_fault = tuple(
        s.vault for s in scans if s.unreadable_reason is not None or s.skipped_escaping > 0
    )
    if at_fault:
        return DanglingSkipped(at_fault)

    exists: set[tuple[str, str]] = set()
    for s in scans:
        exists.update((r.kind, r.name) for r in s.records)
        exists.update((u.kind, u.name) for u in s.unreadable)
    names = {name for _, name in exists}

    result = DanglingResult()
    for s in scans:
        if s.vault not in selected:
            continue
        judged = 0
        for record in s.records:
            if record.kind == SESSION_KIND:
                continue
            judged += 1
            seen: set[tuple[str, str]] = set()
            for field_label, raw, default_kind in _references(record):
                parsed = _resolve(raw, default_kind)
                if parsed is None:
                    result.findings.append(_finding(record, field_label, None))
                    continue
                kind, name = parsed
                target = f"{kind}/{name}" if kind else name
                if (field_label, target) in seen:
                    continue
                seen.add((field_label, target))
                if (kind, name) not in exists if kind else name not in names:
                    result.findings.append(_finding(record, field_label, target))
        result.judged[s.vault] = judged
    return result


def _finding(record: ScannedRecord, field_label: str, target: str | None) -> Finding:
    return Finding(
        record.vault, record.kind, record.name, record.updated_at, field_label, target,
        malformed=target is None,
    )
