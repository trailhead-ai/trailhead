"""The docket report: every vault's list in vault order, the All view and the finished entries, as one JSON document.

Bare `docket` and `docket report` print it. The report only reads: it takes no lock, never syncs and
never writes, so bare `docket` is safe to run anywhere, including under a sandbox that forbids writes.

Document:

    {"fetched_at": "<UTC time the default vault last fetched>" | null,
     "vaults": [{"name", "entries": [entry...], "live_count", "over_limit"[, "error"]}],   # effective vault order
     "all": [entry...],        # live entries interleaved: each vault's first, then each vault's second, ...
     "finished": [entry...],   # entries whose status is done, dropped, superseded or complete
     "hosts_unanswered": [{"reason": "timed out" | "unreachable" | "did not answer", "data": {"host"}}]}

    entry = {"ref": "<vault>/<kind>/<slug>", "kind", "missing", "finished", "status_unknown",
             "workspace": {"group", "slug"} | null,
             "data": {"title", "status"[, "running_on": [host...] | null]}}

`live_count` counts the entries of a vault that are not finished; `over_limit` is true above 25. A vault
whose list file cannot be read has `error` (fixed text naming only the file's path), no entries and
live_count 0; the other vaults still report. `missing` is true only when `lore record show` exits 1 with
"record not found"; a record whose lookup failed, timed out, printed something unreadable or fell past
the lookup cap or the total time budget is `status_unknown` and stays live. `running_on` is camp's host
list for the attached workspace, or null when camp's state is unknown (see `docket.camp`).

Everything derived from a record or from camp is cleaned (`docket.clean`) and appears only under
`data`. Each entry is looked up once and its document shared by every view that lists it.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from docket import camp
from docket.clean import clean
from docket.errors import DocketError
from docket.store import Entry, Runner, Store, StoreError, UnreadableListError
from docket.validate import configured_vaults, show_record

OVER_LIMIT = 25
FINISHED_STATUSES = frozenset({"done", "dropped", "superseded", "complete"})
LOOKUP_TIMEOUT_SECONDS = 10
CAMP_TIMEOUT_SECONDS = 30
LOOKUP_BUDGET_SECONDS = 20.0
LOOKUP_CAP = 200
_NOT_FOUND = "record not found"


def _timed_runner(seconds: int) -> Runner:
    def run(argv: Any) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(list(argv), capture_output=True, text=True, timeout=seconds)

    return run


class _Lookup:
    """The outcome of one `lore record show`: found (title, status), missing, or unknown."""

    def __init__(self, title: str | None = None, status: str | None = None, *, missing: bool = False,
                 unknown: bool = False) -> None:
        self.title, self.status, self.missing, self.unknown = title, status, missing, unknown


def _text(value: Any) -> str | None:
    return clean(value) if isinstance(value, str) else None


def _lookup(runner: Runner, vault: str, entry: Entry) -> _Lookup:
    try:
        proc = show_record(runner, vault, entry.kind, entry.slug)
    except (OSError, subprocess.SubprocessError):
        return _Lookup(unknown=True)
    if proc.returncode == 1 and _NOT_FOUND in (proc.stderr or ""):
        return _Lookup(missing=True)
    if proc.returncode != 0:
        return _Lookup(unknown=True)
    try:
        sidecar = json.loads(proc.stdout)["sidecar"]
        return _Lookup(_text(sidecar.get("title")), _text(sidecar.get("status")))
    except (ValueError, KeyError, TypeError, AttributeError):
        return _Lookup(unknown=True)


class _Resolver:
    """Looks records up under a count cap and a total time budget; past either, an entry is unknown."""

    def __init__(self, runner: Runner, budget: float, cap: int, clock: Callable[[], float]) -> None:
        self._runner, self._budget, self._cap, self._clock = runner, budget, cap, clock
        self._started = clock()
        self._calls = 0

    def lookup(self, vault: str, entry: Entry) -> _Lookup:
        if self._calls >= self._cap or self._clock() - self._started >= self._budget:
            return _Lookup(unknown=True)
        self._calls += 1
        return _lookup(self._runner, vault, entry)


def _entry_doc(vault: str, entry: Entry, found: _Lookup, state: camp.CampState) -> dict:
    status = found.status
    data: dict[str, Any] = {"title": found.title, "status": status}
    workspace = None
    if entry.workspace is not None:
        workspace = {"group": entry.workspace.group, "slug": entry.workspace.slug}
        hosts = camp.running_on(state, entry.workspace.group, entry.workspace.slug)
        data["running_on"] = None if hosts is None else [clean(h) for h in hosts]
    return {"ref": f"{vault}/{entry.kind}/{entry.slug}", "kind": entry.kind, "missing": found.missing,
            "finished": status is not None and status.lower() in FINISHED_STATUSES,
            "status_unknown": found.unknown, "workspace": workspace, "data": data}


def _fetched_at(vault_path: Path) -> str | None:
    try:
        mtime = (vault_path / ".git" / "FETCH_HEAD").stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _interleave(columns: list[list[dict]]) -> list[dict]:
    longest = max((len(c) for c in columns), default=0)
    return [c[i] for i in range(longest) for c in columns if i < len(c)]


def _lookup_all(resolver: _Resolver, lists: dict[str, list[Entry] | None]) -> dict[tuple[str, int], _Lookup]:
    """Look every entry up once, tier by tier across the vaults, so a tight budget starves the deepest entries."""
    found: dict[tuple[str, int], _Lookup] = {}
    longest = max((len(entries) for entries in lists.values() if entries), default=0)
    for tier in range(longest):
        for vault, entries in lists.items():
            if entries and tier < len(entries):
                found[vault, tier] = resolver.lookup(vault, entries[tier])
    return found


def build_report(runner: Runner, camp_runner: Runner, *, budget: float = LOOKUP_BUDGET_SECONDS,
                 cap: int = LOOKUP_CAP, clock: Callable[[], float] = time.monotonic) -> dict:
    vaults = configured_vaults(runner)
    store = Store(vaults.default_path)
    try:
        order = store.effective_order()
    except UnreadableListError:
        raise DocketError(f"cannot read the vault order file under {vaults.default_path}/sites/docket") from None
    state = camp.fetch(camp_runner)
    resolver = _Resolver(runner, budget, cap, clock)
    lists: dict[str, list[Entry] | None] = {}
    for vault in order:
        try:
            lists[vault] = list(store.read_list(vault).entries)
        except StoreError:
            lists[vault] = None
    found = _lookup_all(resolver, lists)
    docs: list[dict] = []
    columns: list[list[dict]] = []
    for vault in order:
        doc: dict[str, Any] = {"name": vault}
        if lists[vault] is not None:
            entries = [_entry_doc(vault, e, found[vault, i], state) for i, e in enumerate(lists[vault])]
        else:
            doc.update(entries=[], live_count=0, over_limit=False,
                       error=f"cannot read {store.vault_root}/sites/docket/lists/{vault}.json")
            docs.append(doc)
            columns.append([])
            continue
        live = [e for e in entries if not e["finished"]]
        doc.update(entries=entries, live_count=len(live), over_limit=len(live) > OVER_LIMIT)
        docs.append(doc)
        columns.append(live)
    return {
        "fetched_at": _fetched_at(vaults.default_path),
        "vaults": docs,
        "all": _interleave(columns),
        "finished": [e for d in docs for e in d["entries"] if e["finished"]],
        "hosts_unanswered": [{"reason": u.reason, "data": {"host": clean(u.host)}} for u in state.unanswered],
    }


def make_command(runner: Runner | None = None, **options: Any) -> Any:
    """The `report` command; `runner` (default: real lore and camp, each call under a timeout) reaches lore and camp."""
    from docket.cli import Command

    def run(_args: argparse.Namespace) -> int:
        report = build_report(runner or _timed_runner(LOOKUP_TIMEOUT_SECONDS),
                              runner or _timed_runner(CAMP_TIMEOUT_SECONDS), **options)
        print(json.dumps(report, indent=2))
        return 0

    return Command("Print every vault's docket list, the All view and the finished entries as JSON.",
                   lambda parser: None, run)
