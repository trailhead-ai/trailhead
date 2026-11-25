"""``lore health`` — a read-only report of vault health, one check at a time.

The six checks, in the order they print, are ``dangling-links`` (references that
point at no record), ``retired-links`` (live records that link to dropped or
superseded records), ``stale-tasks`` (unfinished tasks not updated within the
staleness window, ``--stale-days``, or tied to a dropped or superseded spec) and
``missing-paths`` (paths a live decision, ADR or area names that are on no
repository's default branch) and ``distill-queue`` (finished specs waiting to be
condensed into ADRs, decided by :func:`..health.distill_queue.build_distill_queue`, the
one definition ``lore distill-queue`` shares; deferred specs, which no task links to, are
counted in the heading rather than listed) and ``lesson-growth`` (the executor-dispatch
lessons per vault and the ones added within the same ``--stale-days`` window, informational; skips
per vault when a vault's lessons cannot all be counted). The command builds a plain-data
:class:`HealthReport` from the scan of every
configured vault and the registry of checks (:data:`CHECKS`), then renders it:
one summary row per vault per check, then one findings group per check, each
with its own ``--limit`` cap and action line. Nothing is written: no record, no
lock, no index, and no repository (no fetch).

The first three checks, and ``distill-queue``, are all-or-nothing: one vault that could not be read skips
them for every vault. ``missing-paths`` skips per vault (no repository bound, an
unreadable repository, or the vault not fully read) and runs on the rest
(:attr:`CheckRun.skipped`): a skipped vault's row reads ``yes: <reason>`` with ``—``
counts, and only a check that skipped every selected vault prints ``skipped.``.
The repository views it judges against are built once, in :func:`build_report`,
through one seam (:data:`_repository_views`, which forwards ``env``) and reach every
check on :class:`RunContext`; the group prints one line per repository read
(:attr:`Section.repository_lines`), kept when every vault was skipped.

``--repo PATH`` (repeatable, needs ``--vault``) names that vault's repositories for
``missing-paths``. The paths are operator-named directories, so they are untrusted: each is
resolved to an absolute path (``~`` expanded, a relative path taken from the cwd) and read only
through the hardened reader of :mod:`..health.repos`. They serve only when camp binds the vault
no repository; when it does, camp's are read and the group says the passed paths were not used.
The vault's view is then a :class:`~..health.repos.RepositoryView`, which keeps the paths passed
(``passed``) and whether they were used (``used``) as plain data.

Each check runs inside its own exception boundary: one that raises is reported
as ``errored: <error type>`` on its rows and in its group, every other check
still renders, and the exit status stays 0. A later check is one more entry in
:data:`CHECKS`. The window and the clock reach every check through one
:class:`RunContext`; the clock is injectable through ``build_report(now=)``.

A malformed reference is reported with where it sits, never its text: the body
lines of a bad wikilink, or the entry positions of a bad sidecar entry
(:func:`_malformed_evidence`). The index line counts the records the index would
hold, sessions included, on every form it prints.

``--json`` prints the same :class:`HealthReport` as one JSON document on stdout instead of the
text (:func:`json_document`; the shape is fixed by
``docs/design/lore-health-reports-lesson-growth-passed-repositories-and-json.md``),
with the same ``--vault``, ``--limit``, ``--all``, ``--stale-days`` and ``--repo``. A finding is
its identity, its date and its *evidence*: each :class:`Check` supplies the evidence once
(:attr:`Check.evidence`), and the text line (:func:`_line`) and the JSON ``evidence`` both print
that string, so they cannot disagree. A check that skipped only some vaults is ``ran`` with those
vaults in ``skipped``; ``status`` is ``skipped`` only when every selected vault was skipped. The
document is ``json.dumps(..., ensure_ascii=True)`` of values already escaped by :func:`_safe`, and
a run that cannot happen prints no JSON, only its one stderr line.

Untrusted values echoed into evidence (a reference target, a status, a reason, a branch, a path)
are cut by :func:`_cap` to :data:`MAX_EVIDENCE_FIELD` characters ending in ``…``, in both forms.

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
import json
import shlex
import sys
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..health import dangling as dangling_mod
from ..health import distill_queue as queue_mod
from ..health import lessons as lessons_mod
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
# The longest an untrusted value echoed into a finding's evidence may be, ellipsis included. It is
# longer than any record id in the live vaults, so a real target prints whole, and short enough
# that one finding stays a line a reader and an agent consumer can take in.
MAX_EVIDENCE_FIELD = 200
ELLIPSIS = "…"

INDEX_MATCH = "match"
INDEX_MISMATCH = "mismatch"
INDEX_UNCONFIRMED = "unconfirmed"

# Seam: the report build reads vaults only through this.
_scan_vault = scan_mod.scan_vault


# Seam: the report build reads repositories only through this.
def _repository_views(selected: Sequence[str], *, env=None,
                      passed: Mapping[str, Sequence[Path]] | None = None) -> dict[str, str | tuple]:
    """Per selected vault: the reason it has no repositories, or its repositories as read.

    Only the repositories of *selected* vaults are read, each distinct root once.
    *passed* maps a vault to the resolved paths the operator passed for it with ``--repo``:
    they are the vault's repositories only when camp gives it none, and that vault's view is
    then a :class:`~..health.repos.RepositoryView` saying which paths were passed and whether
    they were used.
    """
    passed = passed or {}
    bound = repos_mod.vault_repositories(selected, env=env)
    roots = {v: tuple(passed[v]) if v in passed and not b.roots else b.roots for v, b in bound.items()}
    read = repos_mod.read_repositories((r for rs in roots.values() for r in rs), env=env)
    views: dict[str, str | tuple] = {}
    for v, b in bound.items():
        if v in passed:
            views[v] = repos_mod.RepositoryView((read[r] for r in roots[v]), passed[v], not b.roots)
        else:
            views[v] = b.reason if b.reason is not None else tuple(read[r] for r in b.roots)
    return views


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
    before the findings. ``evidence`` is each finding's evidence text, in order, and
    ``lines`` each finding's text line built from it: both forms print one string.
    """

    check: Check
    run: CheckRun | None
    findings: list | None = None
    error: str | None = None
    lines: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
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
    every selected vault was skipped. ``deferred`` counts what the check set aside
    without a finding, for its heading note.
    """

    findings: list | None
    judged: dict[str, int]
    at_fault: tuple[str, ...] = ()
    skipped: dict[str, str] = field(default_factory=dict)
    deferred: int = 0


@dataclass(frozen=True)
class Check:
    """One registry entry.

    ``run(scans, selected, ctx)`` runs the check and classifies its result through
    the check's own module at call time. ``evidence(finding)`` is what one finding says
    after its identity and date; the text line (:func:`_line`) and the JSON document print
    that one string.
    ``skip_reason`` ends the sentence saying why a skipped check could not judge
    a target. ``kinds`` are the record kinds a check judges when it skips whole on an
    escape in one of them (empty for a check that skips an escaping vault alone): an
    escape of any other kind is not why it skipped.
    ``action`` is the line that names what acts on the findings.
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
    evidence: Callable[[object], str]
    skip_reason: str
    action: str
    heading_note: Callable[[Section, RunContext], str | None] | None = None
    repository_lines: Callable[[Section, RunContext], list[str]] | None = None
    kinds: Collection[str] = ()


def _safe(value: object) -> str:
    return _printable(str(value))


def _cap(value: object) -> str:
    """*value* escaped as :func:`_safe` does, cut to :data:`MAX_EVIDENCE_FIELD` characters ending in an ellipsis."""
    text = _safe(value)
    return text if len(text) <= MAX_EVIDENCE_FIELD else text[:MAX_EVIDENCE_FIELD - 1] + ELLIPSIS


def _num(n: int) -> str:
    return f"{n:,}"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{_num(n)} {singular if n == 1 else plural}"


MIB = 1024 * 1024


def scan_budget(max_scan_mib: int | None) -> int:
    """The per-vault scan budget in bytes for a ``--max-scan-mib`` value (default when ``None``)."""
    return scan_mod.DEFAULT_MAX_SCAN_BYTES if max_scan_mib is None else max_scan_mib * MIB


def _mib(size: int) -> str:
    return f"{size / MIB:g} MiB"


def budget_exceeded(scan: VaultScan) -> str:
    """Why *scan* was not read; shared with ``lore distill-queue``."""
    return f"records exceed the report's size budget of {_mib(scan.budget_bytes)}"


def budget_phrase(scan: VaultScan) -> str:
    return f"its {budget_exceeded(scan)}; pass --max-scan-mib to raise it"


def _skip_reason(scan: VaultScan, scans: dict[str, VaultScan], at_fault: tuple[str, ...], tail: str,
                 kinds: Collection[str]) -> str:
    if scan.unreadable_reason == scan_mod.REASON_VAULT_MISSING:
        return "its directory does not exist"
    if scan.unreadable_reason == scan_mod.REASON_OVER_BUDGET:
        return budget_phrase(scan)
    if scan.unreadable_reason is not None:
        return "its directory could not be read"
    if scan.vault in at_fault and scan.skipped_escaping and not scan.readable_for(kinds):
        return ESCAPE_SKIP_REASON
    holders = tuple(n for n in at_fault if scans[n].readable_for(queue_mod.JUDGED_KINDS)
                    and queue_mod.holds_unreadable_judged_record(scans[n]))
    incomplete = tuple(n for n in at_fault if n not in holders)
    causes = []
    over = tuple(n for n in incomplete if scans[n].unreadable_reason == scan_mod.REASON_OVER_BUDGET)
    incomplete = tuple(n for n in incomplete if n not in over)
    if over:
        noun = "vault" if len(over) == 1 else "vaults"
        causes.append(f"{noun} {', '.join(over)} exceeded the report's size budget (pass --max-scan-mib to raise it)")
    if incomplete:
        noun = "vault" if len(incomplete) == 1 else "vaults"
        unreadable = all(scans[name].unreadable_reason is not None for name in incomplete)
        state = "could not be read" if unreadable else "could not be fully read"
        causes.append(f"{noun} {', '.join(incomplete)} {state}")
    if holders:
        noun, verb = ("vault", "holds") if len(holders) == 1 else ("vaults", "hold")
        causes.append(f"{noun} {', '.join(holders)} {verb} a spec or task record that could not be read")
    return f"{' and '.join(causes)}, {tail}"


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


ESCAPE_SKIP_REASON = "records lie outside its vault root and were not read"


def _check_run(result, skipped: type, completed: type) -> CheckRun:
    if isinstance(result, skipped):
        return CheckRun(None, {}, result.vaults)
    assert isinstance(result, completed)
    return CheckRun(list(result.findings), dict(result.judged))


def _per_vault_run(findings, judged: Mapping[str, int], skipped: dict[str, str]) -> CheckRun:
    """The run of a check that skips some vaults alone and judges the rest.

    ``findings`` is ``None`` only when vaults were skipped and none was judged.
    """
    return CheckRun(None if skipped and not judged else list(findings), dict(judged), skipped=skipped)


def _escape_aware_run(result, skipped: type, completed: type) -> CheckRun:
    """Like :func:`_check_run` for a check that skips a vault holding an escape alone."""
    run = _check_run(result, skipped, completed)
    if run.findings is None:
        return run
    return _per_vault_run(run.findings, run.judged, {v: ESCAPE_SKIP_REASON for v in result.skipped})


# Each runner classifies through the module that produced the result, looked up
# at call time: an in-place reload of that module replaces its classes, and a
# name bound at import would then name a stale class.
def _run_dangling(scans, selected, ctx) -> CheckRun:
    return _escape_aware_run(dangling_mod.check_dangling(scans, selected),
                      dangling_mod.DanglingSkipped, dangling_mod.DanglingResult)


def _run_retired(scans, selected, ctx) -> CheckRun:
    return _escape_aware_run(retired_mod.check_retired(scans, selected),
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


def _paths_skip_reason(scan: VaultScan, scans: dict[str, VaultScan], skip, view) -> str:
    if skip.reason == paths_mod.NOT_FULLY_READ:
        return _skip_reason(scan, scans, (scan.vault,), PATHS_SKIP_TAIL, paths_mod.JUDGED_KINDS)
    if skip.root is not None:
        remedy = ("pass a repository's working directory with --repo"
                  if isinstance(view, repos_mod.RepositoryView) and view.used
                  else "clone or repair it, or remove it from the camp group")
        return f"repository {_safe(skip.root)} could not be read, {PATHS_SKIP_TAIL}; {remedy}"
    return _NO_REPOSITORIES[skip.reason]


def _run_paths(scans, selected, ctx) -> CheckRun:
    if ctx.repos_error is not None:
        raise ctx.repos_error
    result = paths_mod.check_missing_paths(scans, selected, ctx.repos)
    by_name = {s.vault: s for s in scans}
    skipped = {v: _paths_skip_reason(by_name[v], by_name, skip, ctx.repos.get(v))
               for v, skip in result.skipped.items()}
    return _per_vault_run(result.findings, result.judged, skipped)


DISTILL_SKIP_TAIL = "so no spec's ADR or tasks can be confirmed"


def _run_distill(scans, selected, ctx) -> CheckRun:
    result = queue_mod.build_distill_queue(scans)
    if isinstance(result, queue_mod.DistillSkipped):
        return CheckRun(None, {}, result.at_fault)
    assert isinstance(result, queue_mod.DistillQueue)
    chosen = set(selected)
    judged = {s.vault: sum(1 for r in s.records if r.kind == "spec") for s in scans if s.vault in chosen}
    return CheckRun([i for i in result.waiting if i.vault in chosen], judged,
                    deferred=sum(1 for i in result.deferred if i.vault in chosen))


def _distill_evidence(item: queue_mod.QueueItem) -> str:
    text = f"{_cap(item.status)}  {_cap(item.reason)}"
    return f"{text}; {_cap(item.adr_note)}" if item.adr_note else text


def _distill_line(item: queue_mod.QueueItem) -> str:
    """One queued spec's line; ``lore distill-queue`` prints its items through this too."""
    return _line(item, _distill_evidence(item))


def _distill_note(sec: Section, ctx: RunContext) -> str | None:
    n = sec.run.deferred
    if not n:
        return None
    return (f" ({_num(n)} deferred: no linked task; list them with lore distill-queue "
            "and distill each with /craft:distill spec/<name>)")


LESSONS_SKIP_TAIL = "so its lessons cannot all be counted"


def _lessons_skip_reason(scan: VaultScan, scans: dict[str, VaultScan], code: str) -> str:
    if code == lessons_mod.UNREADABLE_LESSON:
        return f"vault {_safe(scan.vault)} holds a lesson record that could not be read, {LESSONS_SKIP_TAIL}"
    reason = _skip_reason(scan, scans, (scan.vault,), LESSONS_SKIP_TAIL, (lessons_mod.KIND,))
    return reason if reason.endswith(LESSONS_SKIP_TAIL) else f"{reason}, {LESSONS_SKIP_TAIL}"


def _run_lessons(scans, selected, ctx) -> CheckRun:
    result = lessons_mod.check_lessons(scans, selected, now=ctx.now, window_days=ctx.stale_days)
    by_name = {s.vault: s for s in scans}
    skipped = {v: _lessons_skip_reason(by_name[v], by_name, code) for v, code in result.skipped.items()}
    return _per_vault_run(result.findings, result.judged, skipped)


def _lesson_evidence(f: lessons_mod.Finding) -> str:
    return f"added {_plural(f.age_days, 'day', 'days')} ago"


def _percent(added: int, records: int) -> int:
    return (200 * added + records) // (2 * records)


def _lesson_note(sec: Section, ctx: RunContext) -> str:
    added = Counter(f.vault for f in sec.findings or ())
    totals = []
    for vault, records in sec.run.judged.items():
        share = f" ({_percent(added[vault], records)}%)" if records else ""
        totals.append(f"{_safe(vault)} {_plural(records, 'lesson', 'lessons')}, {_num(added[vault])} added{share}")
    return f" (window: {ctx.stale_days} days): " + "; ".join(totals)


def build_report(only: str | None = None, *, env=None, now: datetime | None = None,
                 stale_days: int = DEFAULT_STALE_DAYS,
                 max_scan_mib: int | None = None,
                 repo_paths: Sequence[str] = ()) -> HealthReport:
    """Scan every configured vault, run every check, and gather the result as data.

    Every vault is scanned even when *only* narrows the report, so a target in
    another vault still resolves. A check that raises is recorded as errored and
    does not stop the others. Every runner gets one :class:`RunContext`, built from
    *now* (the current UTC time when ``None``), *stale_days* and the repository
    views of the selected vaults, read once through :data:`_repository_views` with *env*.
    Each vault is read up to *max_scan_mib* MiB (:data:`scan_mod.DEFAULT_MAX_SCAN_BYTES`
    when ``None``); a vault past it is not fully read. *repo_paths* are the ``--repo`` paths
    of the vault *only* names: resolved against the cwd (``~`` against ``HOME`` of *env*),
    they are that vault's repositories when camp binds it none.
    """
    now = now if now is not None else datetime.now(timezone.utc)
    every = scan_mod.resolve_vaults()
    if only is not None:
        scan_mod.resolve_vaults(only)
    scans = [_scan_vault(name, root, max_bytes=scan_budget(max_scan_mib)) for name, root in every]
    by_name = {s.vault: s for s in scans}
    selected = [s.vault for s in scans if only is None or s.vault == only]
    passed = {only: tuple(dict.fromkeys(repos_mod.resolve_passed_root(p, env) for p in repo_paths))} \
        if repo_paths else {}
    try:
        ctx = RunContext(now, stale_days, _repository_views(selected, env=env, passed=passed))
    except Exception as exc:  # noqa: BLE001 - only missing-paths needs the views; it errors alone
        ctx = RunContext(now, stale_days, repos_error=exc)

    sections: list[Section] = []
    for check in CHECKS:
        try:
            run = check.run(scans, selected, ctx)
            findings = None if run.findings is None else sorted(run.findings, key=_sort_key)
            evidence = [] if findings is None else [check.evidence(f) for f in findings]
            lines = [_line(f, e) for f, e in zip(findings or (), evidence)]
            section = Section(check, run, findings, lines=lines, evidence=evidence)
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
                reason = _skip_reason(by_name[v], by_name, sec.run.at_fault, sec.check.skip_reason, sec.check.kinds)
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


def _date(f) -> str | None:
    when = parse_timestamp(f.updated_at)
    return when.date().isoformat() if when is not None else None


def _when(f) -> str:
    return _date(f) or "undated"


def _well_shaped(name: str) -> bool:
    return "/" not in name and references_mod.resolve(name, None) is not None


def _record_id(vault: str | None, kind: str, name: str) -> str:
    """``[vault/]kind/name``, or the fixed phrase in the name's place when it is not record-id shaped."""
    head = f"{_safe(vault)}/" if vault is not None else ""
    tail = _safe(name) if _well_shaped(name) else f" ({BAD_NAME})"
    return f"{head}{_safe(kind)}/{tail}"


def _ident(f) -> str:
    return _record_id(f.vault, f.kind, f.name)


def _line(f, evidence: str) -> str:
    """One finding's text line: its identity, its date, then its *evidence*."""
    return f"  {_ident(f)}  {_when(f)}  {evidence}"


def _dangling_evidence(f: dangling_mod.Finding) -> str:
    if f.malformed:
        return f"{_cap(f.field)} → {_malformed_evidence(f.field, f.locations)}: not shaped like a record id"
    return f"{_cap(f.field)} → {_cap(f.target)}: not found in any vault"


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
        return ("superseded, and no readable record supersedes it" if c.successor_unconfirmed
                else "superseded, and no record supersedes it")
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


def _retired_evidence(f: retired_mod.Finding) -> str:
    shared = len({(c.kind, c.name) for c in f.retired}) < len(f.retired)
    if len(f.retired) == 1:
        what = _candidate_status(f, f.retired[0], shared)
    else:
        ids = _qualified(f.retired, kind_name=lambda c: (c.kind, c.name))
        what = "; ".join(f"{i} {_candidate_status(f, c, shared)}" for i, c in zip(ids, f.retired))
    return f"{_cap(f.field)} → {_cap(f.target)}: {what}"


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


def _vault_arg_ok(vault: str) -> bool:
    """Whether *vault* is a name ``--vault`` resolves as written: the one a configured vault carries."""
    from ..vault import config as vault_config_mod
    from ..vault import layers as layers_mod

    try:
        layers_mod.validate_layer_name(vault)
    except layers_mod.LayerConfinementError:
        return False
    return vault.isprintable() and vault_config_mod.normalize_vault_name(vault) == vault


def _drop_command(f: stale_mod.Finding) -> str | None:
    """``lore record update`` dropping *f* in its own vault, or ``None`` when no safe command exists."""
    if not (_well_shaped(f.name) and _vault_arg_ok(f.vault)):
        return None
    return (f"lore record update {shlex.quote(f'task/{f.name}')} --status dropped --diff "
            f"--vault {shlex.quote(f.vault)}")


def _shared_name_remedy(f: stale_mod.Finding) -> str:
    where = ", ".join(_safe(v) for v in f.duplicated_in)
    cmd = _drop_command(f)
    if cmd is None:
        return (f"a task of this name is also in vault {where}; to drop this one, "
                "pass --vault with this task's vault to lore record update")
    return f"a task of this name is also in vault {where}; to drop this one, run `{_safe(cmd)}`"


def _stale_evidence(f: stale_mod.Finding) -> str:
    reasons = []
    if f.undated:
        reasons.append("no readable last-updated time")
    if f.age_days is not None:
        reasons.append(f"last updated {_num(f.age_days)} days ago")
    if f.retired_specs:
        reasons.append(f"linked to {_spec_phrase(f.retired_specs)}")
    if f.duplicated_in:
        reasons.append(_shared_name_remedy(f))
    return f"{_cap(f.status)}  {'; '.join(reasons)}"


def _stale_note(sec: Section, ctx: RunContext) -> str:
    window = f" (window: {ctx.stale_days} days)"
    counts = Counter(
        "undated" if f.undated else "tied to a retired spec" if f.retired_specs else "stale on age"
        for f in sec.findings or ()
    )
    parts = [f"{_num(counts[reason])} {reason}"
             for reason in ("stale on age", "tied to a retired spec", "undated") if counts[reason]]
    return window + (": " + ", ".join(parts) if parts else "")


def _missing_evidence(f: paths_mod.MissingPath) -> str:
    return f"{_safe(f.location)} {f.number}: `{_cap(f.path)}` is on no repository's default branch"


def _age_days(item: repos_mod.Repository, now: datetime) -> int:
    """Whole days since *item* was last fetched, never negative; *item* has a fetch time."""
    return max(0, (now - item.fetched_at).days)


def _repository_line(vault: str, item, now: datetime, passed: bool = False) -> str:
    head = f"  {_safe(vault)}: repository {_safe(item.root)}{' (passed with --repo)' if passed else ''}: "
    if isinstance(item, repos_mod.Unreadable):
        return f"{head}could not be read ({_UNREADABLE_WHY[item.reason]})"
    branch = _cap(item.branch)
    where = f"local {branch} (no remote-tracking default branch)" if item.local else f"origin/{branch}"
    if item.fetched_at is None:
        return f"{head}{where}, no fetch recorded"
    return (f"{head}{where}, last fetched {item.fetched_at:%Y-%m-%d %H:%M} UTC "
            f"({_plural(_age_days(item, now), 'day', 'days')} ago)")


def _repository_lines(sec: Section, ctx: RunContext) -> list[str]:
    lines: list[str] = []
    for vault, view in ctx.repos.items():
        if isinstance(view, str):
            continue
        passed = isinstance(view, repos_mod.RepositoryView)
        if passed and not view.used:
            lines.append(f"  {_safe(vault)}: --repo ignored: camp binds this vault's repositories")
        lines += [_repository_line(vault, item, ctx.now, passed and view.used) for item in view]
    return lines


# The checks, in the order they print. A new check is one more entry.
CHECKS: tuple[Check, ...] = (
    Check(
        name="dangling-links",
        run=_run_dangling,
        evidence=_dangling_evidence,
        skip_reason="so no target can be confirmed missing",
        action="Act on these with /lore:correct: point the reference at the record "
               "that replaced its target, or remove it.",
    ),
    Check(
        name="retired-links",
        run=_run_retired,
        evidence=_retired_evidence,
        skip_reason="so no target's status can be confirmed",
        action="Act on these with /lore:correct: point a superseded link at a named live "
               "successor; for a superseded record that links back, run the lore record supersede "
               "command shown; remove a link to a dropped record, or to one with no live "
               "successor, unless it still applies.",
    ),
    Check(
        name="stale-tasks",
        run=_run_stale,
        evidence=_stale_evidence,
        skip_reason="so no spec's status can be confirmed",
        kinds=("task",),
        action=STALE_ACTION,
        heading_note=_stale_note,
    ),
    Check(
        name="missing-paths",
        run=_run_paths,
        evidence=_missing_evidence,
        skip_reason=PATHS_SKIP_TAIL,
        kinds=paths_mod.JUDGED_KINDS,
        action="Act on these with /lore:correct: point the record at where the code now lives, "
               "or, if the feature it describes was removed, drop or supersede the record. "
               "If a repository line above is old, fetch it first; a path added on a branch "
               "not yet merged clears once it merges.",
        repository_lines=_repository_lines,
    ),
    Check(
        name="distill-queue",
        run=_run_distill,
        evidence=_distill_evidence,
        skip_reason=DISTILL_SKIP_TAIL,
        kinds=queue_mod.JUDGED_KINDS,
        action="Act on these with /craft:distill: a sweep takes every waiting spec, including any "
               "not shown; /craft:distill spec/<name> takes one. "
               "The id is spec/<name>, without the vault prefix.",
        heading_note=_distill_note,
    ),
    Check(
        name="lesson-growth",
        run=_run_lessons,
        evidence=_lesson_evidence,
        skip_reason=LESSONS_SKIP_TAIL,
        kinds=(lessons_mod.KIND,),
        action="These are informational, not problems to fix. If lessons pile up faster than they "
               "are folded, run /craft:consolidate: it merges lessons that prevent the same failure.",
        heading_note=_lesson_note,
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


def _finding_json(f, evidence: str) -> dict:
    """One finding as data: the record's identity, its date, and the *evidence* its text line ends with.

    ``name`` is ``None`` for a name that is not record-id shaped, which is then never echoed.
    """
    return {
        "vault": _safe(f.vault),
        "kind": _safe(f.kind),
        "name": _safe(f.name) if _well_shaped(f.name) else None,
        "date": _date(f),
        "evidence": evidence,
    }


def _repository_json(item, now: datetime, passed: bool) -> dict:
    """One repository as read: its root, whether ``--repo`` named it, and its default branch or why it could not
    be read."""
    out = {"root": _safe(item.root), "passed": passed, "branch": None, "local": None,
           "fetched_at": None, "age_days": None, "unreadable_reason": None}
    if isinstance(item, repos_mod.Unreadable):
        return {**out, "unreadable_reason": _safe(item.reason)}
    out.update(branch=_cap(item.branch), local=item.local)
    if item.fetched_at is not None:
        out.update(fetched_at=item.fetched_at.isoformat(), age_days=_age_days(item, now))
    return out


def _repositories_json(sec: Section, ctx: RunContext) -> list[dict]:
    """Per selected vault the group's repository lines are made of: its repositories, the ``--repo`` paths set
    aside, or why it has none."""
    if sec.check.repository_lines is None:
        return []
    out = []
    for vault, view in ctx.repos.items():
        entry = {"vault": _safe(vault), "no_repositories_reason": None, "ignored_passed": [], "roots": []}
        if isinstance(view, str):
            entry["no_repositories_reason"] = _safe(view)
        else:
            used = isinstance(view, repos_mod.RepositoryView) and view.used
            if isinstance(view, repos_mod.RepositoryView) and not view.used:
                entry["ignored_passed"] = [_safe(p) for p in view.passed]
            entry["roots"] = [_repository_json(item, ctx.now, used) for item in view]
        out.append(entry)
    return out


def _check_json(sec: Section, report: HealthReport, limit: int | None) -> dict:
    name = sec.check.name
    skipped = {_safe(r.vault): _safe(r.skipped_reason)
               for r in report.rows if r.check == name and r.skipped_reason}
    out = {"name": name, "status": "ran", "skipped": skipped, "error": None, "note": None,
           "repositories": [], "total": None, "shown": None, "findings": [], "action": None}
    if sec.error is not None:
        return {**out, "status": "errored", "error": _safe(sec.error)}
    out["repositories"] = _repositories_json(sec, report.ctx)
    if sec.findings is None:
        return {**out, "status": "skipped"}
    shown = len(sec.findings) if limit is None else min(limit, len(sec.findings))
    return {**out,
            "note": _safe(sec.note).strip() or None if sec.note else None,
            "total": len(sec.findings), "shown": shown,
            "findings": [_finding_json(f, e) for f, e in zip(sec.findings[:shown], sec.evidence)],
            "action": sec.check.action if sec.findings else None}


def json_document(report: HealthReport, limit: int | None) -> dict:
    """*report* as the ``--json`` document; *limit* ``None`` shows every finding.

    Built from the same :class:`HealthReport` the text form renders, and each finding's
    ``evidence`` is the string its text line carries, so the two forms cannot disagree.
    Every untrusted string goes through :func:`_safe` first, as in the text form.
    """
    ix = report.index
    return {
        "format": 1,
        "vaults": [_safe(v) for v in report.selected],
        "configured": report.configured,
        "window_days": report.ctx.stale_days,
        "summary": [{"vault": _safe(r.vault), "check": r.check, "records": r.records, "findings": r.findings,
                     "skipped": None if r.skipped_reason is None else _safe(r.skipped_reason),
                     "error": None if r.error is None else _safe(r.error)} for r in report.rows],
        "unreadable": {_safe(v): n for v, n in report.unreadable.items()},
        "outside_root": {_safe(v): n for v, n in report.outside_root.items()},
        "index": {"status": ix.status, "read": ix.read, "indexed": ix.indexed,
                  "mismatches": [{"vault": _safe(v), "read": r, "indexed": i} for v, r, i in ix.mismatches],
                  "reason": None if ix.reason is None else _safe(ix.reason)},
        "checks": [_check_json(sec, report, limit) for sec in report.sections],
    }


def render_json(report: HealthReport, limit: int | None) -> list[str]:
    """The ``--json`` document as lines: ASCII only, every control character escaped."""
    return json.dumps(json_document(report, limit), ensure_ascii=True, indent=2).splitlines()


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
    if args.repo and only is None:
        _emit(["lore: cannot run the health report: "
               "--repo needs --vault to name the vault the repositories belong to"], sys.stderr)
        return 1
    try:
        report = build_report(only, stale_days=args.stale_days, max_scan_mib=args.max_scan_mib,
                              repo_paths=args.repo or ())
        _emit(render_json(report, limit) if args.json else render(report, limit, only), sys.stdout)
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


def add_max_scan_mib(parser) -> None:
    """Add ``--max-scan-mib`` to *parser*; ``lore distill-queue`` takes the same flag."""
    parser.add_argument(
        "--max-scan-mib", type=_positive_int, default=None, metavar="N",
        help=f"Read at most N MiB of records per vault before skipping it "
             f"(default {scan_mod.DEFAULT_MAX_SCAN_BYTES // MIB})")


def add_health_subparser(sub) -> None:
    """Register the ``health`` command parser."""
    p = sub.add_parser(
        "health",
        help=(
            "Report dangling links, retired links, stale tasks, missing paths, specs "
            "waiting to be distilled and lesson growth, across every vault (read-only)"
        ),
        description=(
            "Read every record in every configured vault and run six checks. "
            "dangling-links reports references (related, supersedes, depends-on, parent, "
            "[[wikilinks]]) that point at no record in any vault. retired-links reports "
            "live records that link to a dropped or superseded record. stale-tasks reports "
            "unfinished tasks not updated within the staleness window, or linked to a "
            "dropped or superseded spec. missing-paths reports paths a live decision, ADR or area "
            "names that are on no default branch of the repositories its vault is bound to (by "
            "[[lore_scopes]] in a camp group); it is skipped for one vault, not all, when that vault "
            "has no repository, an unreadable one, or was not fully read; --repo PATH (repeatable, "
            "needs --vault) names that vault's repositories when camp binds it none. distill-queue reports the "
            "specs waiting to be distilled into ADRs, oldest first, to act on with /craft:distill; "
            "specs no task links to are counted in its heading and listed by lore distill-queue; "
            "it is skipped for every vault when any vault was not fully read. lesson-growth counts the "
            "executor-dispatch lessons (kind lesson, label craft/dispatch-lesson=executor, not dropped "
            "or superseded) per vault and lists those added within the staleness window, as "
            "information rather than problems; it is skipped for one vault, not all, when that vault's "
            "lessons cannot all be read. --json prints the whole report as one JSON document instead "
            "of text; a check that skipped some vaults is 'ran' with those vaults in its 'skipped' "
            "map. Read-only: nothing is "
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
                   help=f"Window for stale-tasks and lesson-growth, 1 to {MAX_STALE_DAYS:,} days "
                        f"(default {DEFAULT_STALE_DAYS})")
    p.add_argument("--all", action="store_true", default=False,
                   help="Show every finding (wins over --limit)")
    p.add_argument("--repo", action="append", default=None, metavar="PATH",
                   help="A repository of the vault named with --vault, for missing-paths when camp "
                        "binds that vault none (repeatable; needs --vault; ~ is expanded, a relative "
                        "path is taken from the current directory)")
    p.add_argument("--json", action="store_true", default=False,
                   help="Print the whole report as one JSON document on stdout instead of text "
                        "(same --vault, --limit, --all, --stale-days and --repo)")
    add_max_scan_mib(p)
    p.set_defaults(func=cmd_health)
