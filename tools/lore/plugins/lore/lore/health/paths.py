"""Missing-paths check: paths in live records that are on no repository's default branch.

Pure: takes the scan of every configured vault, the selected vault names and, per
selected vault, the view of its repositories; no git, no filesystem, no clock.
Record bodies are data.

**Judged records** are the live (``active``) ``decision``, ``adr`` and ``area``
records of each selected vault; specs, tasks and sessions are never judged.

**Candidates** are single-backtick inline code spans outside fenced code blocks
(located by 1-based body line) and the entries of the ``related-files-or-folders``
sidecar list (located by 1-based position; a non-list is ignored and a non-string
entry is skipped, but still occupies its position). A candidate is dropped unless
it is one token of at most :data:`MAX_PATH_CHARS` characters with no whitespace or
control characters, does not start with ``~``, ``/``, ``$`` or ``%``, holds no
``://``, does not start ``mailto:`` and holds none of ``* ? [ { < >`` (a ``<name>`` is a
placeholder, not a path). Printed paths are record text, so the one-token, bounded shape is what keeps them printable.

**Suffixes** are stripped before lookup: ``::...``, ``#...``, a line suffix (``:N``,
``:N:M``, ``:N-M``, ``:N,M`` and combinations) and a trailing ``/``. The finding prints the candidate as written.

**Gate and judgement:** the stripped candidate is a path only when its first
segment is a top-level entry of one of the vault's repositories (so ``lore health``
or ``config.toml`` are prose, not paths). It is reported when
:meth:`~lore.health.repos.Repository.contains` is false for every repository, which
counts a path beneath a submodule gitlink as present, and it is not the tail, from a
directory boundary, of a path on any repository (a path a record writes relative to a
package inside the repository, ``src/x.ts`` for ``services/auth/src/x.ts``, is present
when that tail exists; a deleted one is then missed only if another package holds the
same tail). One finding per record per
distinct path, at its first location.

**Skips are per vault**, and a skipped vault has no judged count and no findings
while the other vaults still run. ``repos`` maps a vault name to either the reason
string it has no repositories (``repos.UNBOUND`` and the like) or a tuple of
:class:`~lore.health.repos.Repository` / :class:`~lore.health.repos.Unreadable`
(a vault missing from the mapping, or with an empty tuple, is ``unbound``). A vault
is skipped for: not fully read (:data:`NOT_FULLY_READ`, which the report words as
it words the other checks' unfit vaults), no repositories (their reason), or any
unreadable repository (its reason and root). One unreadable repository skipping
its whole vault is deliberate: absence cannot be confirmed across every
repository without it, and a false missing path is worse than a visible skip.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .model import ScannedRecord, VaultScan, is_live
from .repos import UNBOUND, Repository, Unreadable

NOT_FULLY_READ = "not-fully-read"

JUDGED_KINDS = ("decision", "adr", "area")
BODY_LINE = "body line"
RELATED_FILE = "related file"
MAX_PATH_CHARS = 300

_INLINE_CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FORBIDDEN = re.compile(r"[\s\x00-\x1f\x7f*?\[{<>]")
_LINE_SUFFIX = re.compile(r":\d+(?:[-,:]\d+)*$")


@dataclass(frozen=True)
class MissingPath:
    """A path a record names that is on no repository's default branch."""

    vault: str
    kind: str
    name: str
    updated_at: str | None
    location: str
    number: int
    path: str


@dataclass(frozen=True)
class VaultSkip:
    """Why a vault was not judged; ``root`` names the unreadable repository, if one."""

    reason: str
    root: Path | None = None


@dataclass(frozen=True)
class PathsResult:
    """Findings, per judged vault the records judged, per skipped vault why."""

    findings: list[MissingPath] = field(default_factory=list)
    judged: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, VaultSkip] = field(default_factory=dict)


def check_missing_paths(
    scans: Sequence[VaultScan],
    selected: Collection[str],
    repos: Mapping[str, str | Sequence[Repository | Unreadable]],
) -> PathsResult:
    """Judge the live decisions, ADRs and areas of every selected vault against its repositories."""
    result = PathsResult()
    for scan in scans:
        if scan.vault not in selected:
            continue
        skip = _skip(scan, repos.get(scan.vault))
        if skip is not None:
            result.skipped[scan.vault] = skip
            continue
        repositories: Sequence[Repository] = repos[scan.vault]  # type: ignore[assignment]
        top_level = frozenset().union(*(r.top_level for r in repositories))
        tails = SuffixIndex(p for r in repositories for p in r.paths)
        judged = 0
        for record in scan.records:
            if record.kind not in JUDGED_KINDS or not is_live(record):
                continue
            judged += 1
            result.findings.extend(_missing(record, top_level, tails, repositories))
        result.judged[scan.vault] = judged
    return result


def _skip(scan: VaultScan, view: str | Sequence[Repository | Unreadable] | None) -> VaultSkip | None:
    if not scan.readable_for(JUDGED_KINDS):
        return VaultSkip(NOT_FULLY_READ)
    if isinstance(view, str):
        return VaultSkip(view)
    if not view:
        return VaultSkip(UNBOUND)
    for item in view:
        if isinstance(item, Unreadable):
            return VaultSkip(item.reason, item.root)
    return None


def _missing(
    record: ScannedRecord, top_level: frozenset[str], tails: SuffixIndex,
    repositories: Sequence[Repository],
) -> list[MissingPath]:
    found: list[MissingPath] = []
    seen: set[str] = set()
    for location, number, written in _candidates(record):
        bare = _bare_path(written)
        if bare is None or bare.split("/", 1)[0] not in top_level or bare in seen:
            continue
        seen.add(bare)
        if not tails.has_tail(bare) and not any(r.contains(bare) for r in repositories):
            found.append(MissingPath(record.vault, record.kind, record.name,
                                     record.updated_at, location, number, written))
    return found


class SuffixIndex:
    """Every path of a vault's repositories, answering "is this the tail of one?".

    A path is stored once, under its last two segments, so memory grows with the
    number of paths and not with their depth. A candidate of one or two segments is
    answered by the key alone (an exact segment match is a directory boundary);
    only a candidate of three or more is compared, and only against the paths that
    share its last two segments, so a bucket of a shared basename is never scanned
    for a candidate whose parent segment differs. ``comparisons`` counts those
    comparisons and ``entries`` the paths held.
    """

    def __init__(self, paths: Iterable[str]):
        self._by_last_two: dict[tuple[str, str | None], set[str]] = {}
        self._basenames: set[str] = set()
        self.comparisons = 0
        for path in paths:
            head, _, last = path.rpartition("/")
            parent = head.rpartition("/")[2] if head else None
            self._by_last_two.setdefault((last, parent), set()).add(path)
            self._basenames.add(last)

    @property
    def entries(self) -> int:
        return sum(len(bucket) for bucket in self._by_last_two.values())

    def has_tail(self, candidate: str) -> bool:
        """True when *candidate* is a path, or the tail of one from a directory boundary."""
        if "/" not in candidate:
            return candidate in self._basenames
        head, _, last = candidate.rpartition("/")
        parent, nested = head.rpartition("/")[2], "/" in head
        bucket = self._by_last_two.get((last, parent))
        if not bucket:
            return False
        if not nested:
            return True
        suffix = "/" + candidate
        for path in bucket:
            self.comparisons += 1
            if path == candidate or path.endswith(suffix):
                return True
        return False


def _candidates(record: ScannedRecord):
    fence: str | None = None
    for number, line in enumerate(record.body.split("\n"), start=1):
        opener = _FENCE.match(line)
        if fence is not None:
            if opener and opener.group(1)[0] == fence[0] and len(opener.group(1)) >= len(fence) \
                    and not line.strip().strip(fence[0]):
                fence = None
            continue
        if opener and not (opener.group(1)[0] == "`" and "`" in line[opener.end():]):
            fence = opener.group(1)
            continue
        for match in _INLINE_CODE.finditer(line):
            yield BODY_LINE, number, match.group(1)
    related = record.sidecar.get("related-files-or-folders") if isinstance(record.sidecar, dict) else None
    if isinstance(related, list):
        for number, entry in enumerate(related, start=1):
            if isinstance(entry, str):
                yield RELATED_FILE, number, entry


def _bare_path(written: str) -> str | None:
    """The lookup form of a candidate, or ``None`` when it is not shaped like a path."""
    if (
        not written
        or len(written) > MAX_PATH_CHARS
        or _FORBIDDEN.search(written)
        or written[0] in "~/$%"
        or "://" in written
        or written.startswith("mailto:")
    ):
        return None
    bare = written.split("::", 1)[0].split("#", 1)[0]
    bare = _LINE_SUFFIX.sub("", bare).rstrip("/")
    return bare or None
