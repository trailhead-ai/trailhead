"""``lore health`` — a read-only report of references that point at no record.

The command builds a plain-data :class:`HealthReport` from the scan of every
configured vault and the dangling-reference check, then renders it. Nothing is
written: no record, no lock, no index.

Output safety: every vault name, record id, field, target, skip reason, config
error and ``--vault`` echo is untrusted (a vault synced from elsewhere never had
its names validated, and OS error text carries paths). Each is passed through
:func:`_safe` — the shared control-character escaper — when a line is composed,
and :func:`_emit`, the only place the command writes to stdout or stderr,
escapes every line again, so no line can carry a newline or a terminal escape.

Exit codes: 0 whenever the report runs, with or without findings, a skipped
check or an index mismatch; 1 when the report cannot run (unreadable or invalid
config, unknown ``--vault``) or fails unexpectedly, always as one ``lore:`` line.
"""
from __future__ import annotations

import argparse
import shlex
import sys
from dataclasses import dataclass, field

from ..health import dangling as dangling_mod
from ..health import scan as scan_mod
from ..health.dangling import DanglingResult, DanglingSkipped, Finding
from ..health.model import VaultScan
from .record import _printable

DEFAULT_LIMIT = 20
CHECK_NAME = "dangling-links"

INDEX_MATCH = "match"
INDEX_MISMATCH = "mismatch"
INDEX_UNCONFIRMED = "unconfirmed"

_SCAN_FAULT_UNREADABLE = "unreadable"
_SCAN_FAULT_ESCAPING = "escaping"

# Seams: the report build reads vaults and the index only through these.
_scan_vault = scan_mod.scan_vault
_count_index_rows = scan_mod.count_index_rows


@dataclass
class VaultRow:
    """One summary row. ``records`` and ``findings`` are ``None`` when skipped."""

    vault: str
    records: int | None
    findings: int | None
    skipped_reason: str | None


@dataclass
class IndexLine:
    """Whether the records read match the index.

    ``mismatches`` holds ``(vault, read, indexed)`` per differing vault;
    ``reason`` says why a count could not be confirmed.
    """

    status: str
    read: int
    indexed: int | None = None
    mismatches: list[tuple[str, int, int]] = field(default_factory=list)
    reason: str | None = None
    immutable_snapshot: bool = False


@dataclass
class HealthReport:
    """Everything the rendering needs, as plain data. ``findings`` is ``None`` when skipped."""

    selected: list[str]
    configured: int
    narrowed: bool
    rows: list[VaultRow]
    unreadable: dict[str, int]
    outside_root: dict[str, int]
    index: IndexLine
    findings: list[Finding] | None


def _safe(value: object) -> str:
    return _printable(str(value))


def _num(n: int) -> str:
    return f"{n:,}"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{_num(n)} {singular if n == 1 else plural}"


def _vault_fault(scan: VaultScan) -> str | None:
    if scan.unreadable_reason is not None:
        return _SCAN_FAULT_UNREADABLE
    if scan.skipped_escaping > 0:
        return _SCAN_FAULT_ESCAPING
    return None


def _skip_reason(vault: str, scans: dict[str, VaultScan], at_fault: tuple[str, ...]) -> str:
    own = _vault_fault(scans[vault]) if vault in scans else None
    if own == _SCAN_FAULT_UNREADABLE:
        return "its directory could not be read"
    if own == _SCAN_FAULT_ESCAPING:
        return "records lie outside its vault root and were not read"
    names = ", ".join(at_fault)
    noun = "vault" if len(at_fault) == 1 else "vaults"
    return f"{noun} {names} could not be fully read, so no target can be confirmed missing"


def _sort_key(f: Finding):
    return (f.updated_at if isinstance(f.updated_at, str) else "", f.vault, f.kind, f.name)


def _index_line(selected_scans: list[VaultScan], env=None) -> IndexLine:
    read = sum(len(s.records) for s in selected_scans)
    unfit = [s.vault for s in selected_scans if _vault_fault(s) is not None]
    if unfit:
        return IndexLine(INDEX_UNCONFIRMED, read, reason=f"vault {', '.join(unfit)} was not fully read")
    counts = _count_index_rows([s.root for s in selected_scans], env=env)
    bad = next((c for c in counts.values() if c.status != scan_mod.INDEX_OK), None)
    if bad is not None:
        reason = "no index found" if bad.status == scan_mod.INDEX_ABSENT else "the index could not be read"
        return IndexLine(INDEX_UNCONFIRMED, read, reason=reason)
    snapshot = any(c.immutable_snapshot for c in counts.values())
    mismatches = [
        (s.vault, len(s.records), counts[s.root].count)
        for s in selected_scans
        if counts[s.root].count != len(s.records)
    ]
    indexed = sum(c.count for c in counts.values())
    status = INDEX_MISMATCH if mismatches else INDEX_MATCH
    return IndexLine(status, read, indexed, mismatches, immutable_snapshot=snapshot)


def build_report(only: str | None = None, *, env=None) -> HealthReport:
    """Scan every configured vault, run the check, and gather the result as data.

    Every vault is scanned even when *only* narrows the report, so a target in
    another vault still resolves.
    """
    every = scan_mod.resolve_vaults()
    if only is not None:
        scan_mod.resolve_vaults(only)
    scans = [_scan_vault(name, root) for name, root in every]
    by_name = {s.vault: s for s in scans}
    selected = [s.vault for s in scans if only is None or s.vault == only]
    result = dangling_mod.check_dangling(scans, selected)

    rows: list[VaultRow] = []
    findings: list[Finding] | None
    if isinstance(result, DanglingSkipped):
        findings = None
        for v in selected:
            rows.append(VaultRow(v, None, None, _skip_reason(v, by_name, result.vaults)))
    else:
        assert isinstance(result, DanglingResult)
        findings = sorted(result.findings, key=_sort_key)
        for v in selected:
            n = sum(1 for f in findings if f.vault == v)
            rows.append(VaultRow(v, result.judged.get(v, 0), n, None))

    chosen = [by_name[v] for v in selected]
    return HealthReport(
        selected=selected,
        configured=len(scans),
        narrowed=only is not None and len(scans) != len(selected),
        rows=rows,
        unreadable={s.vault: len(s.unreadable) for s in chosen if s.unreadable},
        outside_root={s.vault: s.skipped_escaping for s in chosen if s.skipped_escaping},
        index=_index_line(chosen, env=env),
        findings=findings,
    )


def _table(rows: list[VaultRow]) -> list[str]:
    cells = [("vault", "check", "records", "findings", "skipped")]
    for r in rows:
        skipped = f"yes: {_safe(r.skipped_reason)}" if r.skipped_reason else "no"
        cells.append((
            _safe(r.vault),
            CHECK_NAME,
            "—" if r.records is None else _num(r.records),
            "—" if r.findings is None else _num(r.findings),
            skipped,
        ))
    widths = [max(len(c[i]) for c in cells) for i in range(4)]
    return ["  ".join(c[i].ljust(widths[i]) for i in range(4)) + "  " + c[4] for c in cells]


def _finding_line(f: Finding) -> str:
    date = _safe(f.updated_at[:10]) if isinstance(f.updated_at, str) and f.updated_at else "undated"
    ident = f"{_safe(f.vault)}/{_safe(f.kind)}/{_safe(f.name)}"
    if f.malformed:
        what = f"{_safe(f.field)} → malformed reference: not shaped like a record id"
    else:
        what = f"{_safe(f.field)} → {_safe(f.target)}: not found in any vault"
    return f"  {ident}  {date}  {what}"


def _index_lines(ix: IndexLine) -> list[str]:
    caveat = "The index count is a snapshot and can lag a running writer."
    if ix.status == INDEX_MATCH:
        out = [f"Records read match the index (sessions included): "
               f"{_num(ix.read)} read, {_num(ix.indexed)} indexed."]
    elif ix.status == INDEX_MISMATCH:
        parts = "; ".join(f"{_safe(v)}: {_num(r)} read, {_num(i)} indexed" for v, r, i in ix.mismatches)
        out = [f"Records read differ from the index for {parts}. "
               "Run `lore reindex`, then run the report again."]
    else:
        return [f"Could not confirm the count against the index: {_safe(ix.reason)}. "
                f"{_plural(ix.read, 'record', 'records')} read."]
    if ix.immutable_snapshot:
        out.append(caveat)
    return out


def render(report: HealthReport, limit: int | None, vault_arg: str | None) -> list[str]:
    """Render *report*; *limit* ``None`` shows every finding."""
    n = len(report.selected)
    head = f"lore health — {_plural(n, 'vault', 'vaults')}"
    if report.narrowed:
        head += f" (targets looked up in all {report.configured})"
    lines = [head, ""]
    lines += _table(report.rows)
    lines.append("")
    for v in report.selected:
        if v in report.unreadable:
            c = report.unreadable[v]
            lines.append(f"{_safe(v)}: " + (
                "1 record could not be read and was not checked." if c == 1
                else f"{_num(c)} records could not be read and were not checked."))
        if v in report.outside_root:
            c = report.outside_root[v]
            lines.append(f"{_safe(v)}: " + (
                "1 record lies outside the vault root and was not read." if c == 1
                else f"{_num(c)} records lie outside the vault root and were not read."))
    if report.unreadable or report.outside_root:
        lines.append("")
    lines += _index_lines(report.index)
    lines.append("")

    findings = report.findings
    if findings is None:
        lines.append(f"{CHECK_NAME} — skipped.")
    elif not findings:
        lines.append(f"{CHECK_NAME} — no findings.")
    else:
        total = len(findings)
        lines.append(f"{CHECK_NAME} — {_plural(total, 'finding', 'findings')}, oldest first")
        shown = findings if limit is None else findings[:limit]
        lines += [_finding_line(f) for f in shown]
        rest = total - len(shown)
        if rest:
            scope = f" --vault {_safe(shlex.quote(vault_arg))}" if vault_arg is not None else ""
            lines.append(f"  {_num(rest)} more. Run `lore health{scope} --all` to see every "
                         "finding, or `--limit <n>` for more.")
        lines.append("Act on these with /lore:correct: point the reference at the record "
                     "that replaced its target, or remove it.")
    return lines


def _emit(lines: list[str], stream) -> None:
    """The one place the command writes output: each line is escaped on the way out."""
    for line in lines:
        print(_safe(line), file=stream)


def _refusal(exc: scan_mod.HealthResolutionError, only: str | None) -> str:
    if only is not None and exc.configured:
        detail = (f"no vault named '{_safe(only)}' is configured "
                  f"(configured: {', '.join(_safe(n) for n in exc.configured)})")
    else:
        detail = _safe(exc)
    return f"lore: cannot run the health report: {detail}"


def cmd_health(args) -> int:
    """Print the vault health report; see the module docstring for the exit contract."""
    only = args.vault
    limit = None if args.all else args.limit
    try:
        report = build_report(only)
        _emit(render(report, limit, only), sys.stdout)
        return 0
    except scan_mod.HealthResolutionError as exc:
        _emit([_refusal(exc, only)], sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - the contract is one line, never a traceback
        _emit([f"lore: health report failed: {type(exc).__name__}: {exc}"], sys.stderr)
        return 1


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid positive integer: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value}")
    return value


def add_health_subparser(sub) -> None:
    """Register the ``health`` command parser."""
    p = sub.add_parser(
        "health",
        help="Report references that point at no record, across every vault (read-only)",
        description=(
            "Read every record in every configured vault and report references "
            "(related, supersedes, depends-on, parent, [[wikilinks]]) that point at "
            "no record in any vault. Read-only. Exit status: 0 whenever the report "
            "runs, with or without findings; 1 when it cannot run (unreadable config, "
            "unknown --vault) or fails unexpectedly."
        ),
    )
    p.add_argument("--vault", default=None, metavar="NAME",
                   help="Report only this vault; targets are still looked up in every vault")
    p.add_argument("--limit", type=_positive_int, default=DEFAULT_LIMIT, metavar="N",
                   help=f"Show up to N findings per check (default {DEFAULT_LIMIT})")
    p.add_argument("--all", action="store_true", default=False,
                   help="Show every finding (wins over --limit)")
    p.set_defaults(func=cmd_health)
