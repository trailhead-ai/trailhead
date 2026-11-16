"""``lore health`` — a read-only report of vault health, one check at a time.

The four checks, in the order they print, are ``dangling-links`` (references that
point at no record), ``retired-links`` (live records that link to dropped or
superseded records), ``stale-tasks`` (unfinished tasks not updated within the
staleness window, ``--stale-days``, or tied to a dropped or superseded spec) and
``missing-paths`` (paths a live decision, ADR or area names that are on no
repository's default branch). The command builds a plain-data :class:`HealthReport` from the scan of every
configured vault and the registry of checks (:data:`CHECKS`), then renders it:
one summary row per vault per check, then one findings group per check, each
with its own ``--limit`` cap and action line. Nothing is written: no record, no
lock, no index, and no repository (no fetch).

The first three checks are all-or-nothing: one vault that could not be read skips
them for every vault. ``missing-paths`` skips per vault (no repository bound, an
unreadable repository, or the vault not fully read) and runs on the rest
(:attr:`CheckRun.skipped`): a skipped vault's row reads ``yes: <reason>`` with ``—``
counts, and only a check that skipped every selected vault prints ``skipped.``.
The repository views it judges against are built once, in :func:`build_report`,
through one seam (:data:`_repository_views`, which forwards ``env``) and reach every
check on :class:`RunContext`; the group prints one line per repository read
(:attr:`Section.repository_lines`), kept when every vault was skipped.

Each check runs inside its own exception boundary: one that raises is reported
as ``errored: <error type>`` on its rows and in its group, every other check
still renders, and the exit status stays 0. A later check is one more entry in
:data:`CHECKS`. The window and the clock reach every check through one
:class:`RunContext`; the clock is injectable through ``build_report(now=)``.

A malformed reference is reported with where it sits, never its text: the body
lines of a bad wikilink, or the entry positions of a bad sidecar entry
(:func:`_malformed_evidence`). The index line counts the records the index would
hold, sessions included, on every form it prints.

Record identity: a finding opens with ``<vault>/<kind>/<name>`` and the date. The
name is printed only when it is shaped like a record id (the shape
``health.references.resolve`` accepts, no slash); a name from disk that is not
prints as ``<vault>/<kind>/ (a record whose name is not a valid record id)`` and
no part of it is echoed. The date is the UTC date of ``updated-at`` when that
parses as a timestamp with an offset (:func:`..health.model.parse_timestamp`),
otherwise ``undated``. Findings sort by that parsed point in time, undated
first, and groups are separated by one blank line.

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
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..health import dangling as dangling_mod
from ..health import paths as paths_mod
from ..health import references as references_mod
from ..health import repos as repos_mod
from ..health import retired as retired_mod
from ..health import scan as scan_mod
from ..health import stale as stale_mod
from ..health.model import RETIRED_STATUSES, VaultScan, parse_timestamp
from .record import _printable

DEFAULT_LIMIT = 20
DEFAULT_STALE_DAYS = 30
MAX_STALE_DAYS = 36_500
BAD_NAME = "a record whose name is not a valid record id"

INDEX_MATCH = "match"
INDEX_MISMATCH = "mismatch"
INDEX_UNCONFIRMED = "unconfirmed"

# Seam: the report build reads vaults only through this.
_scan_vault = scan_mod.scan_vault


def _read_repository_views(selected: Sequence[str], *, env=None) -> dict[str, str | tuple]:
    """Per selected vault: the reason it has no repositories, or its repositories as read.

    Only the repositories of *selected* vaults are read, each distinct root once.
    """
    bound = repos_mod.vault_repositories(selected)
    read = repos_mod.read_repositories((r for b in bound.values() for r in b.roots), env=env)
    return {v: b.reason if b.reason is not None else tuple(read[r] for r in b.roots)
            for v, b in bound.items()}


# Seam: the report build reads repositories only through this.
_repository_views = _read_repository_views


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


@dataclass(frozen=True)
class RunContext:
    """What every check runner is given besides the scans: the clock, the staleness window and the repositories.

    ``now`` is an aware UTC datetime; ``stale_days`` is the window in days.
    ``repos`` is what the repository-view seam returned for the selected vaults
    (per vault, a reason string or the repositories read), built once;
    ``repos_error`` is the exception the seam raised, if it did, which the check
    that needs the views raises again inside its own boundary.
    """

    now: datetime
    stale_days: int
    repos: Mapping[str, str | Sequence] = field(default_factory=dict)
    repos_error: Exception | None = None


@dataclass
class Section:
    """One check's outcome.

    ``findings`` is ``None`` when the check was skipped or errored; ``lines`` is
    each finding rendered, in order. ``run`` is what the check returned, ``None`` when it raised and ``error`` holds the
    error type name. ``repository_lines`` are the lines printed under the heading
    before the findings.
    """

    check: Check
    run: CheckRun | None
    findings: list | None = None
    error: str | None = None
    lines: list[str] = field(default_factory=list)
    note: str | None = None
    repository_lines: list[str] = field(default_factory=list)


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
    ctx: RunContext


@dataclass(frozen=True)
class CheckRun:
    """What a check run produced: findings and per-vault judged counts, or the vaults at fault.

    ``skipped`` maps a vault the check did not judge to the reason, for a check that
    can skip one vault and run on the others; ``findings`` is ``None`` only when
    every selected vault was skipped.
    """

    findings: list | None
    judged: dict[str, int]
    at_fault: tuple[str, ...] = ()
    skipped: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Check:
    """One registry entry.

    ``run(scans, selected, ctx)`` runs the check and classifies its result through
    the check's own module at call time. ``render(finding)`` is one finding's line.
    ``skip_reason`` ends the sentence saying why a skipped check could not judge
    a target. ``action`` is the line that names what acts on the findings.
    ``heading_note(section, ctx)``, when set, returns text appended verbatim to the
    group heading (after ``oldest first``, or after ``no findings``), or ``None``
    for none. It is called for any check that ran, with or without findings, inside
    the check's exception boundary, and its result is stored on the section.
    ``repository_lines(section, ctx)``, when set, returns the lines printed under
    the heading before the findings, also inside the exception boundary, and is
    called whether or not the check skipped.
    """

    name: str
    run: Callable[[Sequence[VaultScan], Collection[str], RunContext], CheckRun]
    render: Callable[[object], str]
    skip_reason: str
    action: str
    heading_note: Callable[[Section, RunContext], str | None] | None = None
    repository_lines: Callable[[Section, RunContext], list[str]] | None = None


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


_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def _sort_key(f):
    """Undated first, then by the point in time ``updated_at`` names."""
    when = parse_timestamp(f.updated_at)
    return (when is not None, when or _EARLIEST, f.vault, f.kind, f.name)


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
def _run_dangling(scans, selected, ctx) -> CheckRun:
    return _check_run(dangling_mod.check_dangling(scans, selected),
                      dangling_mod.DanglingSkipped, dangling_mod.DanglingResult)


def _run_retired(scans, selected, ctx) -> CheckRun:
    return _check_run(retired_mod.check_retired(scans, selected),
                      retired_mod.RetiredSkipped, retired_mod.RetiredResult)


def _run_stale(scans, selected, ctx) -> CheckRun:
    return _check_run(stale_mod.check_stale(scans, selected, now=ctx.now, window_days=ctx.stale_days),
                      stale_mod.StaleSkipped, stale_mod.StaleResult)


PATHS_SKIP_TAIL = "so no path can be confirmed missing"
_NO_REPOSITORIES = {
    repos_mod.UNBOUND: "no repository is known for this vault (no camp group binds it); "
                       "bind it with [[lore_scopes]] in a camp group",
    repos_mod.CAMP_ABSENT: "no repository is known for this vault (camp is not installed)",
    repos_mod.CONFIG_UNREADABLE: "no repository is known for this vault "
                                 "(camp's group config could not be read); run camp status to see why",
}
_UNREADABLE_WHY = {
    repos_mod.MISSING: "its directory does not exist",
    repos_mod.NOT_A_REPOSITORY: "it is not a git repository",
    repos_mod.NO_DEFAULT_BRANCH: "it has no default branch",
    repos_mod.GIT_FAILED: "git could not read it",
}


def _paths_skip_reason(scan: VaultScan, scans: dict[str, VaultScan], skip) -> str:
    if skip.reason == paths_mod.NOT_FULLY_READ:
        return _skip_reason(scan, scans, (scan.vault,), PATHS_SKIP_TAIL)
    if skip.root is not None:
        return (f"repository {skip.root.name} could not be read, {PATHS_SKIP_TAIL}; "
                "clone or repair it, or remove it from the camp group")
    return _NO_REPOSITORIES[skip.reason]


def _run_paths(scans, selected, ctx) -> CheckRun:
    if ctx.repos_error is not None:
        raise ctx.repos_error
    result = paths_mod.check_missing_paths(scans, selected, ctx.repos)
    by_name = {s.vault: s for s in scans}
    skipped = {v: _paths_skip_reason(by_name[v], by_name, skip) for v, skip in result.skipped.items()}
    return CheckRun(None if skipped and not result.judged else list(result.findings),
                    dict(result.judged), skipped=skipped)


def build_report(only: str | None = None, *, env=None, now: datetime | None = None,
                 stale_days: int = DEFAULT_STALE_DAYS) -> HealthReport:
    """Scan every configured vault, run every check, and gather the result as data.

    Every vault is scanned even when *only* narrows the report, so a target in
    another vault still resolves. A check that raises is recorded as errored and
    does not stop the others. Every runner gets one :class:`RunContext`, built from
    *now* (the current UTC time when ``None``), *stale_days* and the repository
    views of the selected vaults, read once through :data:`_repository_views` with *env*.
    """
    now = now if now is not None else datetime.now(timezone.utc)
    every = scan_mod.resolve_vaults()
    if only is not None:
        scan_mod.resolve_vaults(only)
    scans = [_scan_vault(name, root) for name, root in every]
    by_name = {s.vault: s for s in scans}
    selected = [s.vault for s in scans if only is None or s.vault == only]
    try:
        ctx = RunContext(now, stale_days, _repository_views(selected, env=env))
    except Exception as exc:  # noqa: BLE001 - only missing-paths needs the views; it errors alone
        ctx = RunContext(now, stale_days, repos_error=exc)

    sections: list[Section] = []
    for check in CHECKS:
        try:
            run = check.run(scans, selected, ctx)
            findings = None if run.findings is None else sorted(run.findings, key=_sort_key)
            lines = [] if findings is None else [check.render(f) for f in findings]
            section = Section(check, run, findings, lines=lines)
            if check.heading_note is not None:
                section.note = check.heading_note(section, ctx)
            if check.repository_lines is not None:
                section.repository_lines = check.repository_lines(section, ctx)
        except Exception as exc:  # noqa: BLE001 - one check's failure must not take the report down
            sections.append(Section(check, None, error=type(exc).__name__))
            continue
        sections.append(section)

    rows: list[VaultRow] = []
    for v in selected:
        for sec in sections:
            name = sec.check.name
            if sec.error is not None:
                rows.append(VaultRow(v, name, None, None, None, sec.error))
            elif v in sec.run.skipped:
                rows.append(VaultRow(v, name, None, None, sec.run.skipped[v]))
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
        ctx=ctx,
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


MAX_NAMED_LOCATIONS = 10


def _malformed_evidence(field: str, locations: tuple[int, ...]) -> str:
    """Where a malformed reference sits, never its text.

    At most the first ``MAX_NAMED_LOCATIONS`` are named, then ``and N more``, so
    one finding stays one short line however many places are bad.

    A wikilink's locations are body lines, any other field's are entry
    positions; a field with no locations (``parent``) is a single value, so the
    field names the spot and no position is added.
    """
    if not locations:
        return "malformed reference"
    named = [str(n) for n in locations[:MAX_NAMED_LOCATIONS]]
    more = len(locations) - len(named)
    spots = _and_list(named) if not more else f"{', '.join(named)} and {more} more"
    if field == "wikilink":
        noun = "body line" if len(locations) == 1 else "body lines"
        return f"malformed reference on {noun} {spots}"
    noun = "entry" if len(locations) == 1 else "entries"
    return f"malformed reference at {noun} {spots}"


def _when(f) -> str:
    when = parse_timestamp(f.updated_at)
    return when.date().isoformat() if when is not None else "undated"


def _well_shaped(name: str) -> bool:
    return "/" not in name and references_mod.resolve(name, None) is not None


def _record_id(vault: str | None, kind: str, name: str) -> str:
    """``[vault/]kind/name``, or the fixed phrase in the name's place when it is not record-id shaped."""
    head = f"{_safe(vault)}/" if vault is not None else ""
    tail = _safe(name) if _well_shaped(name) else f" ({BAD_NAME})"
    return f"{head}{_safe(kind)}/{tail}"


def _ident(f) -> str:
    return _record_id(f.vault, f.kind, f.name)


def _dangling_line(f: dangling_mod.Finding) -> str:
    if f.malformed:
        what = f"{_safe(f.field)} → {_malformed_evidence(f.field, f.locations)}: not shaped like a record id"
    else:
        what = f"{_safe(f.field)} → {_safe(f.target)}: not found in any vault"
    return f"  {_ident(f)}  {_when(f)}  {what}"


MAX_NAMED_SUCCESSORS = 3
NONCONFORMING_SUCCESSOR = "a successor whose name is not a valid record id"


def _qualified(items, *, kind_name) -> list[str]:
    """``kind/name`` for each item, ``vault/kind/name`` where another item shares kind and name."""
    counts: dict[tuple[str, str], int] = {}
    for i in items:
        counts[kind_name(i)] = counts.get(kind_name(i), 0) + 1
    out = []
    for i in items:
        k, n = kind_name(i)
        out.append(_record_id(i.vault if counts[(k, n)] > 1 else None, k, n))
    return out


def _successor_phrase(c: retired_mod.RetiredCandidate) -> str:
    """What became of a superseded record; never carries a non-conforming name."""
    if not c.successors and not c.nonconforming_successors:
        return "superseded, and no record supersedes it"
    named = []
    ids = _qualified(c.successors, kind_name=lambda s: (s.kind, s.name))
    for s, ident in list(zip(c.successors, ids))[:MAX_NAMED_SUCCESSORS]:
        mark = f" (itself {s.status})" if s.status in RETIRED_STATUSES else ""
        named.append(f"{ident}{mark}")
    rest = len(c.successors) - len(named)
    listed = ", ".join(named) + f" and {_num(rest)} more" if rest else _and_list(named)
    if not c.nonconforming_successors:
        return f"superseded by {listed}"
    if not named:
        return f"superseded by {NONCONFORMING_SUCCESSOR}"
    return f"superseded by {listed}, plus {NONCONFORMING_SUCCESSOR}"


LINKS_BACK = "it links back to this record, so if this record replaced it, record that"
LINKS_BACK_REFUSED = (
    "it links back to this record, but this pairing cannot be recorded as a supersession"
)


def _supersede_command(f: retired_mod.Finding, c: retired_mod.RetiredCandidate, shared: bool) -> str | None:
    """``lore record supersede`` for *c* by the linker, or ``None`` when no safe command exists.

    No command is built where the supersede verb's own pairing guard would refuse the
    linker as successor (``c.supersede_allowed``).

    ``--vault`` locates both records in one vault, so it can only name the vault they
    share; two records in different vaults can be told apart only when neither name
    exists anywhere else."""
    if not (c.supersede_allowed and _well_shaped(f.name) and _well_shaped(c.name)):
        return None
    cmd = f"lore record supersede {shlex.quote(f'{c.kind}/{c.name}')} --by {shlex.quote(f'{f.kind}/{f.name}')}"
    if shared or f.linker_duplicated:
        if f.vault != c.vault:
            return None
        cmd += f" --vault {shlex.quote(c.vault)}"
    return cmd


def _candidate_status(f: retired_mod.Finding, c: retired_mod.RetiredCandidate, shared: bool) -> str:
    if c.status == "dropped":
        return "dropped"
    phrase = _successor_phrase(c)
    if not c.links_back:
        return phrase
    cmd = _supersede_command(f, c, shared)
    if not c.supersede_allowed:
        return f"{phrase}; {LINKS_BACK_REFUSED}"
    return f"{phrase}; {LINKS_BACK}" + (f": `{_safe(cmd)}`" if cmd else "")


def _retired_line(f: retired_mod.Finding) -> str:
    shared = len({(c.kind, c.name) for c in f.retired}) < len(f.retired)
    if len(f.retired) == 1:
        what = _candidate_status(f, f.retired[0], shared)
    else:
        ids = _qualified(f.retired, kind_name=lambda c: (c.kind, c.name))
        what = "; ".join(f"{i} {_candidate_status(f, c, shared)}" for i, c in zip(ids, f.retired))
    return f"  {_ident(f)}  {_when(f)}  {_safe(f.field)} → {_safe(f.target)}: {what}"


MAX_NAMED_SPECS = 3
STALE_ACTION = (
    "Act on these: if the work already landed, mark the task done; if it still applies, "
    "resume it with /craft:execute; if it was abandoned, drop it with a note saying why "
    "(lore record update task/<name> --status dropped --diff). "
    "The id is task/<name>, without the vault prefix."
)


def _spec_phrase(specs: Sequence[stale_mod.RetiredSpec]) -> str:
    ids = _qualified(specs, kind_name=lambda s: ("spec", s.name))
    named = [f"{i}, which is {_safe(s.status)}" for s, i in list(zip(specs, ids))[:MAX_NAMED_SPECS]]
    rest = len(specs) - len(named)
    return "; ".join(named) + (f" and {_num(rest)} more" if rest else "")


def _stale_line(f: stale_mod.Finding) -> str:
    reasons = []
    if f.undated:
        reasons.append("no readable last-updated time")
    if f.age_days is not None:
        reasons.append(f"last updated {_num(f.age_days)} days ago")
    if f.retired_specs:
        reasons.append(f"linked to {_spec_phrase(f.retired_specs)}")
    return f"  {_ident(f)}  {_when(f)}  {_safe(f.status)}  {'; '.join(reasons)}"


def _stale_note(sec: Section, ctx: RunContext) -> str:
    window = f" (window: {ctx.stale_days} days)"
    counts = Counter(
        "undated" if f.undated else "tied to a retired spec" if f.retired_specs else "stale on age"
        for f in sec.findings or ()
    )
    parts = [f"{_num(counts[reason])} {reason}"
             for reason in ("stale on age", "tied to a retired spec", "undated") if counts[reason]]
    return window + (": " + ", ".join(parts) if parts else "")


def _missing_line(f: paths_mod.MissingPath) -> str:
    return (f"  {_ident(f)}  {_when(f)}  {_safe(f.location)} {f.number}: "
            f"`{_safe(f.path)}` is on no repository's default branch")


def _repository_line(vault: str, item, now: datetime) -> str:
    head = f"  {_safe(vault)}: repository {_safe(item.root.name)}: "
    if isinstance(item, repos_mod.Unreadable):
        return f"{head}could not be read ({_UNREADABLE_WHY[item.reason]})"
    branch = _safe(item.branch)
    where = f"local {branch} (no remote-tracking default branch)" if item.local else f"origin/{branch}"
    if item.fetched_at is None:
        return f"{head}{where}, no fetch recorded"
    days = max(0, (now - item.fetched_at).days)
    return (f"{head}{where}, last fetched {item.fetched_at:%Y-%m-%d %H:%M} UTC "
            f"({_plural(days, 'day', 'days')} ago)")


def _repository_lines(sec: Section, ctx: RunContext) -> list[str]:
    return [_repository_line(vault, item, ctx.now)
            for vault, view in ctx.repos.items() if not isinstance(view, str)
            for item in view]


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
               "successor; for a superseded record that links back, run the lore record supersede "
               "command shown; remove a link to a dropped record, or to one with no live "
               "successor, unless it still applies.",
    ),
    Check(
        name="stale-tasks",
        run=_run_stale,
        render=_stale_line,
        skip_reason="so no spec's status can be confirmed",
        action=STALE_ACTION,
        heading_note=_stale_note,
    ),
    Check(
        name="missing-paths",
        run=_run_paths,
        render=_missing_line,
        skip_reason=PATHS_SKIP_TAIL,
        action="Act on these with /lore:correct: point the record at where the code now lives, "
               "or, if the feature it describes was removed, drop or supersede the record. "
               "If a repository line above is old, fetch it first; a path added on a branch "
               "not yet merged clears once it merges.",
        repository_lines=_repository_lines,
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

    groups = [_group(sec, limit, vault_arg, report.ctx) for sec in report.sections]
    for i, g in enumerate(groups):
        lines += ([""] if i else []) + g
    return lines


def _group(sec: Section, limit: int | None, vault_arg: str | None, ctx: RunContext) -> list[str]:
    name = sec.check.name
    if sec.error is not None:
        return [f"{name} — errored: {_safe(sec.error)}."]
    findings = sec.findings
    if findings is None:
        return [f"{name} — skipped.", *sec.repository_lines]
    note = sec.note
    if not findings:
        return [f"{name} — no findings{_safe(note) if note else ''}.", *sec.repository_lines]
    total = len(findings)
    out = [f"{name} — {_plural(total, 'finding', 'findings')}, oldest first"
           + (_safe(note) if note else ""), *sec.repository_lines]
    shown = findings if limit is None else findings[:limit]
    out += sec.lines[:len(shown)]
    rest = total - len(shown)
    if rest:
        scope = f" --vault {_safe(shlex.quote(vault_arg))}" if vault_arg is not None else ""
        if ctx.stale_days != DEFAULT_STALE_DAYS:
            scope += f" --stale-days {ctx.stale_days}"
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
        report = build_report(only, stale_days=args.stale_days)
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


def _stale_days(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid whole number of days: {text!r}") from None
    if not 1 <= value <= MAX_STALE_DAYS:
        raise argparse.ArgumentTypeError(f"must be from 1 to {MAX_STALE_DAYS:,} days, got {value}")
    return value


def add_health_subparser(sub) -> None:
    """Register the ``health`` command parser."""
    p = sub.add_parser(
        "health",
        help="Report dangling links, retired links, stale tasks and missing paths, across every vault (read-only)",
        description=(
            "Read every record in every configured vault and run four checks. "
            "dangling-links reports references (related, supersedes, depends-on, parent, "
            "[[wikilinks]]) that point at no record in any vault. retired-links reports "
            "live records that link to a dropped or superseded record. stale-tasks reports "
            "unfinished tasks not updated within the staleness window, or linked to a "
            "dropped or superseded spec. missing-paths reports paths a live decision, ADR or area "
            "names that are on no default branch of the repositories its vault is bound to (by "
            "[[lore_scopes]] in a camp group); it is skipped for one vault, not all, when that vault "
            "has no repository, an unreadable one, or was not fully read. Read-only: nothing is "
            "fetched. "
            "Exit status: 0 whenever the report "
            "runs, with or without findings; 1 when it cannot run (unreadable config, "
            "unknown --vault) or fails unexpectedly."
        ),
    )
    p.add_argument("--vault", default=None, metavar="NAME",
                   help="Report only this vault; targets are still looked up in every vault")
    p.add_argument("--limit", type=_positive_int, default=DEFAULT_LIMIT, metavar="N",
                   help=f"Show up to N findings per check (default {DEFAULT_LIMIT})")
    p.add_argument("--stale-days", type=_stale_days, default=DEFAULT_STALE_DAYS, metavar="N",
                   help=f"Staleness window for tasks, 1 to {MAX_STALE_DAYS:,} days "
                        f"(default {DEFAULT_STALE_DAYS})")
    p.add_argument("--all", action="store_true", default=False,
                   help="Show every finding (wins over --limit)")
    p.set_defaults(func=cmd_health)
