"""``lore health`` — a read-only report of vault health, one check at a time.

The checks, in the order they print, are ``dangling-links`` (references that
point at no record) and ``retired-links`` (live records that link to dropped or
superseded records). The command builds a plain-data :class:`HealthReport` from
the scan of every configured vault and the registry of checks (:data:`CHECKS`),
then renders it: one summary row per vault per check, then one findings group
per check, each with its own ``--limit`` cap and action line. Nothing is
written: no record, no lock, no index.

Each check runs inside its own exception boundary: one that raises is reported
as ``errored: <error type>`` on its rows and in its group, every other check
still renders, and the exit status stays 0. A later check is one more entry in
:data:`CHECKS`.

A malformed reference is reported with where it sits, never its text: the body
lines of a bad wikilink, or the entry positions of a bad sidecar entry
(:func:`_malformed_evidence`). The index line counts the records the index would
hold, sessions included, on every form it prints.

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
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field

from ..health import dangling as dangling_mod
from ..health import retired as retired_mod
from ..health import scan as scan_mod
from ..health.model import VaultScan
from .record import _printable

DEFAULT_LIMIT = 20

INDEX_MATCH = "match"
INDEX_MISMATCH = "mismatch"
INDEX_UNCONFIRMED = "unconfirmed"

# Seam: the report build reads vaults only through this.
_scan_vault = scan_mod.scan_vault


@dataclass
class VaultRow:
    """One summary row of one check on one vault.

    ``records`` and ``findings`` are ``None`` when the check was skipped
    (``skipped_reason``) or errored (``error``, the error type name).
    """

    vault: str
    check: str
    records: int | None
    findings: int | None
    skipped_reason: str | None
    error: str | None = None


@dataclass
class IndexLine:
    """Whether the records the index would hold match the index.

    ``read`` is that count on every status, sessions included. ``mismatches``
    holds ``(vault, read, indexed)`` per differing vault;
    ``reason`` says why a count could not be confirmed.
    """

    status: str
    read: int
    indexed: int | None = None
    mismatches: list[tuple[str, int, int]] = field(default_factory=list)
    reason: str | None = None


@dataclass
class Section:
    """One check's outcome.

    ``findings`` is ``None`` when the check was skipped or errored. ``run`` is
    what the check returned, ``None`` when it raised and ``error`` holds the
    error type name.
    """

    check: Check
    run: CheckRun | None
    findings: list | None = None
    error: str | None = None


@dataclass
class HealthReport:
    """Everything the rendering needs, as plain data: one section per check, in registry order."""

    selected: list[str]
    configured: int
    narrowed: bool
    rows: list[VaultRow]
    unreadable: dict[str, int]
    outside_root: dict[str, int]
    index: IndexLine
    sections: list[Section]


@dataclass(frozen=True)
class CheckRun:
    """What a check run produced: findings and per-vault judged counts, or the vaults at fault."""

    findings: list | None
    judged: dict[str, int]
    at_fault: tuple[str, ...] = ()


@dataclass(frozen=True)
class Check:
    """One registry entry.

    ``run(scans, selected)`` runs the check and classifies its result through the
    check's own module at call time. ``render(finding)`` is one finding's line.
    ``skip_reason`` ends the sentence saying why a skipped check could not judge
    a target. ``action`` is the line that names what acts on the findings.
    """

    name: str
    run: Callable[[Sequence[VaultScan], Collection[str]], CheckRun]
    render: Callable[[object], str]
    skip_reason: str
    action: str


def _safe(value: object) -> str:
    return _printable(str(value))


def _num(n: int) -> str:
    return f"{n:,}"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{_num(n)} {singular if n == 1 else plural}"


def _skip_reason(scan: VaultScan, scans: dict[str, VaultScan], at_fault: tuple[str, ...], tail: str) -> str:
    if scan.unreadable_reason == scan_mod.REASON_VAULT_MISSING:
        return "its directory does not exist"
    if scan.unreadable_reason is not None:
        return "its directory could not be read"
    if scan.skipped_escaping:
        return "records lie outside its vault root and were not read"
    names = ", ".join(at_fault)
    noun = "vault" if len(at_fault) == 1 else "vaults"
    unreadable = all(scans[name].unreadable_reason is not None for name in at_fault)
    state = "could not be read" if unreadable else "could not be fully read"
    return f"{noun} {names} {state}, {tail}"


def _sort_key(f):
    return (f.updated_at or "", f.vault, f.kind, f.name)


def _index_line(selected_scans: list[VaultScan], env=None) -> IndexLine:
    held = sum(s.indexed for s in selected_scans)
    unfit = [s.vault for s in selected_scans if not s.fully_read]
    if unfit:
        return IndexLine(INDEX_UNCONFIRMED, held, reason=f"vault {', '.join(unfit)} was not fully read")
    counts = scan_mod.count_index_rows([s.root for s in selected_scans], env=env)
    bad = next((c for c in counts.values() if c.status != scan_mod.INDEX_OK), None)
    if bad is not None:
        reason = "no index found" if bad.status == scan_mod.INDEX_ABSENT else "the index could not be read"
        return IndexLine(INDEX_UNCONFIRMED, held, reason=reason)
    mismatches = [
        (s.vault, s.indexed, counts[s.root].count)
        for s in selected_scans
        if counts[s.root].count != s.indexed
    ]
    indexed = sum(c.count for c in counts.values())
    status = INDEX_MISMATCH if mismatches else INDEX_MATCH
    return IndexLine(status, held, indexed, mismatches)


def _check_run(result, skipped: type, completed: type) -> CheckRun:
    if isinstance(result, skipped):
        return CheckRun(None, {}, result.vaults)
    assert isinstance(result, completed)
    return CheckRun(list(result.findings), dict(result.judged))


# Each runner classifies through the module that produced the result, looked up
# at call time: an in-place reload of that module replaces its classes, and a
# name bound at import would then name a stale class.
def _run_dangling(scans, selected) -> CheckRun:
    return _check_run(dangling_mod.check_dangling(scans, selected),
                      dangling_mod.DanglingSkipped, dangling_mod.DanglingResult)


def _run_retired(scans, selected) -> CheckRun:
    return _check_run(retired_mod.check_retired(scans, selected),
                      retired_mod.RetiredSkipped, retired_mod.RetiredResult)


def build_report(only: str | None = None, *, env=None) -> HealthReport:
    """Scan every configured vault, run every check, and gather the result as data.

    Every vault is scanned even when *only* narrows the report, so a target in
    another vault still resolves. A check that raises is recorded as errored and
    does not stop the others.
    """
    every = scan_mod.resolve_vaults()
    if only is not None:
        scan_mod.resolve_vaults(only)
    scans = [_scan_vault(name, root) for name, root in every]
    by_name = {s.vault: s for s in scans}
    selected = [s.vault for s in scans if only is None or s.vault == only]

    sections: list[Section] = []
    for check in CHECKS:
        try:
            run = check.run(scans, selected)
        except Exception as exc:  # noqa: BLE001 - one check's failure must not take the report down
            sections.append(Section(check, None, error=type(exc).__name__))
            continue
        findings = None if run.findings is None else sorted(run.findings, key=_sort_key)
        sections.append(Section(check, run, findings))

    rows: list[VaultRow] = []
    for v in selected:
        for sec in sections:
            name = sec.check.name
            if sec.error is not None:
                rows.append(VaultRow(v, name, None, None, None, sec.error))
            elif sec.findings is None:
                reason = _skip_reason(by_name[v], by_name, sec.run.at_fault, sec.check.skip_reason)
                rows.append(VaultRow(v, name, None, None, reason))
            else:
                n = sum(1 for f in sec.findings if f.vault == v)
                rows.append(VaultRow(v, name, sec.run.judged.get(v, 0), n, None))

    chosen = [by_name[v] for v in selected]
    return HealthReport(
        selected=selected,
        configured=len(scans),
        narrowed=only is not None and len(scans) != len(selected),
        rows=rows,
        unreadable={s.vault: len(s.unreadable) for s in chosen if s.unreadable},
        outside_root={s.vault: s.skipped_escaping for s in chosen if s.skipped_escaping},
        index=_index_line(chosen, env=env),
        sections=sections,
    )


def _table(rows: list[VaultRow]) -> list[str]:
    cells = [("vault", "check", "records", "findings", "skipped")]
    for r in rows:
        if r.error is not None:
            skipped = f"errored: {_safe(r.error)}"
        else:
            skipped = f"yes: {_safe(r.skipped_reason)}" if r.skipped_reason else "no"
        cells.append((
            _safe(r.vault),
            _safe(r.check),
            "—" if r.records is None else _num(r.records),
            "—" if r.findings is None else _num(r.findings),
            skipped,
        ))
    widths = [max(len(c[i]) for c in cells) for i in range(4)]
    return ["  ".join(c[i].ljust(widths[i]) for i in range(4)) + "  " + c[4] for c in cells]


def _and_list(items: Sequence[str]) -> str:
    """*items* as prose: ``a``, ``a and b``, ``a, b and c``."""
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _malformed_evidence(field: str, locations: tuple[int, ...]) -> str:
    """Where a malformed reference sits, never its text.

    A wikilink's locations are body lines, any other field's are entry
    positions; a field with no locations (``parent``) is a single value, so the
    field names the spot and no position is added.
    """
    if not locations:
        return "malformed reference"
    if field == "wikilink":
        noun = "body line" if len(locations) == 1 else "body lines"
        return f"malformed reference on {noun} {_and_list([str(n) for n in locations])}"
    noun = "entry" if len(locations) == 1 else "entries"
    return f"malformed reference at {noun} {_and_list([str(n) for n in locations])}"


def _when(f) -> str:
    return _safe(f.updated_at[:10]) if f.updated_at else "undated"


def _ident(f) -> str:
    return f"{_safe(f.vault)}/{_safe(f.kind)}/{_safe(f.name)}"


def _dangling_line(f: dangling_mod.Finding) -> str:
    if f.malformed:
        what = f"{_safe(f.field)} → {_malformed_evidence(f.field, f.locations)}: not shaped like a record id"
    else:
        what = f"{_safe(f.field)} → {_safe(f.target)}: not found in any vault"
    return f"  {_ident(f)}  {_when(f)}  {what}"


MAX_NAMED_SUCCESSORS = 3
NONCONFORMING_SUCCESSOR = "a successor whose name is not a valid record id"


def _successor_phrase(c: retired_mod.RetiredCandidate) -> str:
    """What became of a superseded record; never carries a non-conforming name."""
    if not c.successors and not c.nonconforming_successors:
        return "superseded, and no record supersedes it"
    named = []
    for s in c.successors[:MAX_NAMED_SUCCESSORS]:
        mark = f" (itself {s.status})" if s.status in retired_mod.RETIRED_STATUSES else ""
        named.append(f"{_safe(s.kind)}/{_safe(s.name)}{mark}")
    rest = len(c.successors) - len(named)
    listed = ", ".join(named) + f" and {_num(rest)} more" if rest else _and_list(named)
    if not c.nonconforming_successors:
        return f"superseded by {listed}"
    if not named:
        return f"superseded by {NONCONFORMING_SUCCESSOR}"
    return f"superseded by {listed}, plus {NONCONFORMING_SUCCESSOR}"


def _candidate_status(c: retired_mod.RetiredCandidate) -> str:
    return "dropped" if c.status == "dropped" else _successor_phrase(c)


def _retired_line(f: retired_mod.Finding) -> str:
    if len(f.retired) == 1:
        what = _candidate_status(f.retired[0])
    else:
        what = "; ".join(
            f"{_safe(c.kind)}/{_safe(c.name)} {_candidate_status(c)}" for c in f.retired)
    return f"  {_ident(f)}  {_when(f)}  {_safe(f.field)} → {_safe(f.target)}: {what}"


# The checks, in the order they print. A new check is one more entry.
CHECKS: tuple[Check, ...] = (
    Check(
        name="dangling-links",
        run=_run_dangling,
        render=_dangling_line,
        skip_reason="so no target can be confirmed missing",
        action="Act on these with /lore:correct: point the reference at the record "
               "that replaced its target, or remove it.",
    ),
    Check(
        name="retired-links",
        run=_run_retired,
        render=_retired_line,
        skip_reason="so no target's status can be confirmed",
        action="Act on these with /lore:correct: point a superseded link at a named live "
               "successor; remove a link to a dropped record, or to one with no live "
               "successor, unless it still applies.",
    ),
)


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
                f"{_plural(ix.read, 'record', 'records')} the index would hold (sessions included)."]
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

    for sec in report.sections:
        lines += _group(sec, limit, vault_arg)
    return lines


def _group(sec: Section, limit: int | None, vault_arg: str | None) -> list[str]:
    name = sec.check.name
    if sec.error is not None:
        return [f"{name} — errored: {_safe(sec.error)}."]
    findings = sec.findings
    if findings is None:
        return [f"{name} — skipped."]
    if not findings:
        return [f"{name} — no findings."]
    total = len(findings)
    out = [f"{name} — {_plural(total, 'finding', 'findings')}, oldest first"]
    shown = findings if limit is None else findings[:limit]
    out += [sec.check.render(f) for f in shown]
    rest = total - len(shown)
    if rest:
        scope = f" --vault {_safe(shlex.quote(vault_arg))}" if vault_arg is not None else ""
        out.append(f"  {_num(rest)} more. Run `lore health{scope} --all` to see every "
                   "finding, or `--limit <n>` for more.")
    out.append(sec.check.action)
    return out


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
        help="Report dangling and retired links, across every vault (read-only)",
        description=(
            "Read every record in every configured vault and run two checks. "
            "dangling-links reports references (related, supersedes, depends-on, parent, "
            "[[wikilinks]]) that point at no record in any vault. retired-links reports "
            "live records that link to a dropped or superseded record. Read-only. "
            "Exit status: 0 whenever the report "
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
