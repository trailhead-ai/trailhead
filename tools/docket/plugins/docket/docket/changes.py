"""The docket change commands: add, remove, top, attach, detach.

Each takes its values as separate argv (after `--`), runs the change pipeline in `docket.sync`, and
prints one JSON result document on stdout. Exit codes: 0 saved or unchanged, 2 refused (nothing
written), 3 saved but not synced, 4 lost to remote, 1 a write that failed part way (outcome `partial`).
Any other failure is a `docket: <message>` on stderr with exit 1.

Order commands. Both take every value as a separate argument after `--`.

    docket set-order -- <vault> was <REF...> now <REF...> [<vault> was <REF...> now <REF...>]...
    docket vault-order -- <vault>...

For set-order a vault is a token without a "/" and a REF always has one, so the groups need no further
separator; every REF in a group is in that group's vault. Example, reordering two vaults at once:

    docket set-order -- proj was proj/task/a proj/task/b now proj/task/b proj/task/a \\
                        other was other/task/x other/task/y now other/task/y other/task/x

Under one lock every named list is read and checked before anything is written. If any list no longer
stands as `was` says (another session changed it), the whole change is refused with exit 2, no list is
touched, and the result carries `"stale": [<vault>, ...]` naming each vault that moved so the caller can
reload and retry. A `now` that leaves out a current entry, repeats one or adds one that is not in the
list is refused the same way, without `stale`. Entry statuses are `moved` and `in-place`; `entry_count`
holds the length of each named list. A reorder keeps each entry's workspace.

vault-order stores the given vaults as the leading part of the vault order, refusing a vault that is
not configured and a vault named twice. Its result lists each vault with status `stored` and carries
`"order": [<vault>, ...]`, the effective order (the stored order, then the other vaults that have a
list, alphabetically; `docket.store.resolve_order`). `entry_count` is empty. After the second sync the
stored order is re-read; one the remote replaced is `lost-to-remote` (exit 4) and `order` shows what
stands.

Several lists are written one after another, each atomically (see `docket.store`), so a failure
between two writes leaves each file wholly old or wholly new, never partial, but the lists written
before it stay new. That is reported, never as success: outcome `partial`, exit 1, `"written": [...]`,
`"not_written": [...]` and a message naming both. Nothing is synced after a partial write; what was
written is committed by the next sync. A failure on the first write is an ordinary `docket:` error
with every list still old.

Result document:

    {"outcome": "saved" | "saved-not-synced" | "unchanged" | "refused" | "lost-to-remote",
     "command": "add",
     "message": "<one sentence>",
     "entries": [{"ref": "<vault>/<kind>/<slug>", "status": ...[, "workspace": "GROUP/SLUG"]}],
     "entry_count": {"<vault>": <entries in that vault's list>},
     ["sync_warning": "<why a sync failed>",]
     ["lists": {"<vault>": [{"kind", "slug"[, "workspace": {"group", "slug"}]}]}]}    (lost-to-remote only)

Entry statuses: added, already-present, removed, not-found, moved, already-at-top, attached,
already-attached, detached, not-attached, refused. `entry_count` counts every entry (finished or not) in each touched
vault's list after the change (as the list stands now, for lost-to-remote); whether an entry is live is known only to
the bare `docket` report (`live_count`, `over_limit`).

Commands are built by `make_commands(runner)`; the runner is how lore and git are reached, so tests
hand in a fake and the shipped table uses the real one.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from typing import Any, Callable, NamedTuple, Sequence

from docket import sync as pipeline
from docket.errors import DocketError
from docket.store import (
    DocketList, Entry, ListTooLargeError, Runner, Store, StoreError, UnreadableListError, UnsafeNameError,
    Workspace, default_runner, entry_doc,
)
from docket.validate import (
    Ref, Refused, check_kind, configured_vaults, parse_ref, parse_vault, parse_workspace, require_configured,
)

PARTIAL = "partial"
EXIT_CODES = {**pipeline.EXIT_CODES, PARTIAL: 1}

Lists = dict[str, DocketList]
Result = tuple[Lists, list[dict], list[pipeline.Expectation]]


def _entry(ref: Ref) -> Entry:
    return Entry(ref.kind, ref.slug)


def _keys(lst: DocketList) -> list[pipeline.Key]:
    return [e.key for e in lst.entries]


def _status(ref: Ref, status: str, **extra: Any) -> dict:
    return {"ref": ref.text, "status": status, **extra}


def _add(lists: Lists, p: argparse.Namespace) -> Result:
    refs: list[Ref] = p.refs
    anchor: Ref | None = p.anchor
    out = dict(lists)
    fresh: dict[str, list[Entry]] = {}
    seen = {v: set(_keys(lst)) for v, lst in lists.items()}
    entries = []
    for ref in refs:
        if ref.key in seen[ref.vault]:
            entries.append(_status(ref, "already-present"))
            continue
        seen[ref.vault].add(ref.key)
        fresh.setdefault(ref.vault, []).append(_entry(ref))
        entries.append(_status(ref, "added"))
    if anchor is not None and anchor.key not in _keys(lists[anchor.vault]):
        raise Refused(f"{anchor.text} is not in the docket, so nothing can go {p.where} it")
    expect = []
    for vault, new in fresh.items():
        current = list(lists[vault].entries)
        old_keys = _keys(lists[vault])
        new_keys = [e.key for e in new]
        if anchor is not None:
            at = old_keys.index(anchor.key) + (1 if p.where == "after" else 0)
            ahead, behind = (new_keys, [anchor.key]) if p.where == "before" else ([anchor.key], new_keys)
        elif p.where == "top":
            at, ahead, behind = 0, new_keys, old_keys
        else:
            at, ahead, behind = len(current), old_keys, new_keys
        out[vault] = DocketList(tuple(current[:at] + new + current[at:]))
        expect.append(pipeline.Expectation(vault, present=new_keys, order=new_keys, precede=[(ahead, behind)]))
    return out, entries, expect


def _remove(lists: Lists, p: argparse.Namespace) -> Result:
    out = dict(lists)
    gone: dict[str, list[pipeline.Key]] = {}
    entries = []
    for ref in p.refs:
        if ref.key in _keys(out[ref.vault]):
            out[ref.vault] = DocketList(tuple(e for e in out[ref.vault].entries if e.key != ref.key))
            gone.setdefault(ref.vault, []).append(ref.key)
            entries.append(_status(ref, "removed"))
        else:
            entries.append(_status(ref, "not-found"))
    return out, entries, [pipeline.Expectation(v, absent=keys) for v, keys in gone.items()]


def _top(lists: Lists, p: argparse.Namespace) -> Result:
    out = dict(lists)
    wanted: dict[str, set[pipeline.Key]] = {}
    for ref in p.refs:
        if ref.key in _keys(lists[ref.vault]):
            wanted.setdefault(ref.vault, set()).add(ref.key)
    expect = []
    for vault, keys in wanted.items():
        current = lists[vault].entries
        moved = [e for e in current if e.key in keys]
        rest = [e for e in current if e.key not in keys]
        out[vault] = DocketList(tuple(moved + rest))
        moved_keys = [e.key for e in moved]
        expect.append(pipeline.Expectation(vault, present=moved_keys, order=moved_keys,
                                           precede=[(moved_keys, [e.key for e in rest])]))
    entries = []
    for ref in p.refs:
        if ref.vault not in wanted or ref.key not in wanted[ref.vault]:
            entries.append(_status(ref, "not-found"))
        else:
            entries.append(_status(ref, "moved" if out[ref.vault] != lists[ref.vault] else "already-at-top"))
    return out, entries, expect


def _set_workspace(lists: Lists, ref: Ref, workspace: Workspace | None) -> tuple[Lists, bool]:
    lst = lists[ref.vault]
    if ref.key not in _keys(lst):
        raise Refused(f"{ref.text} is not in the docket", [_status(ref, "refused")])
    entries = tuple(dataclasses.replace(e, workspace=workspace) if e.key == ref.key else e
                    for e in lst.entries)
    return {**lists, ref.vault: DocketList(entries)}, entries != lst.entries


def _attach(lists: Lists, p: argparse.Namespace) -> Result:
    out, changed = _set_workspace(lists, p.ref, p.workspace)
    text = f"{p.workspace.group}/{p.workspace.slug}"
    entries = [_status(p.ref, "attached" if changed else "already-attached", workspace=text)]
    return out, entries, [pipeline.Expectation(p.ref.vault, workspaces={p.ref.key: p.workspace})]


def _detach(lists: Lists, p: argparse.Namespace) -> Result:
    out, changed = _set_workspace(lists, p.ref, None)
    entries = [_status(p.ref, "detached" if changed else "not-attached")]
    return out, entries, [pipeline.Expectation(p.ref.vault, workspaces={p.ref.key: None})]


class StaleOrder(Refused):
    """A set-order whose `was` no longer matches the stored list of each vault in `vaults`."""

    def __init__(self, vaults: Sequence[str]) -> None:
        super().__init__(f"{', '.join(vaults)} changed since it was read; reload the list and try again")
        self.vaults = list(vaults)


class PartialWrite(DocketError):
    """Some of several lists were written before a write failed."""

    def __init__(self, written: Sequence[str], not_written: Sequence[str], cause: Exception) -> None:
        super().__init__(f"wrote {', '.join(written)} but not {', '.join(not_written)} ({cause}); "
                         "what was written is not synced yet, run docket again or lore sync")
        self.written = list(written)
        self.not_written = list(not_written)


class OrderGroup(NamedTuple):
    vault: str
    was: list[tuple[str, str]]
    now: list[tuple[str, str]]


def _set_order(lists: Lists, p: argparse.Namespace) -> Result:
    stale = [g.vault for g in p.groups if _keys(lists[g.vault]) != g.was]
    if stale:
        raise StaleOrder(stale)
    for g in p.groups:
        current = _keys(lists[g.vault])
        if len(set(g.now)) != len(g.now):
            raise Refused(f"the new order for {g.vault} names an entry more than once")
        if set(g.now) != set(current):
            missing = [f"{k}/{s}" for k, s in current if (k, s) not in set(g.now)]
            extra = [f"{k}/{s}" for k, s in g.now if (k, s) not in set(current)]
            raise Refused(f"the new order for {g.vault} must name each entry of its list exactly once"
                          + (f"; missing {', '.join(missing)}" if missing else "")
                          + (f"; not in the list: {', '.join(extra)}" if extra else ""))
    out = dict(lists)
    entries = []
    expect = []
    for g in p.groups:
        by_key = {e.key: e for e in lists[g.vault].entries}
        out[g.vault] = DocketList(tuple(by_key[k] for k in g.now))
        for i, (kind, slug) in enumerate(g.now):
            status = "in-place" if g.was[i] == (kind, slug) else "moved"
            entries.append({"ref": f"{g.vault}/{kind}/{slug}", "status": status})
        expect.append(pipeline.Expectation(g.vault, present=g.now, order=g.now))
    return out, entries, expect


def _parse_set_order(args: argparse.Namespace) -> argparse.Namespace:
    words = list(args.words)
    groups: list[OrderGroup] = []
    refs: list[Ref] = []
    i = 0

    def take(group_vault: str, stop: Callable[[str], bool]) -> list[tuple[str, str]]:
        nonlocal i
        taken = []
        while i < len(words) and not stop(words[i]):
            ref = parse_ref(words[i])
            if ref.vault != group_vault:
                raise Refused(f"{ref.text} is not in vault {group_vault!r}; every REF of a group must be in its vault")
            refs.append(ref)
            taken.append(ref.key)
            i += 1
        return taken

    while i < len(words):
        vault = parse_vault(words[i])
        if i + 1 >= len(words) or words[i + 1] != "was":
            raise Refused(f"expected 'was' after vault {vault!r}")
        i += 2
        was = take(vault, lambda w: w == "now")
        if i >= len(words):
            raise Refused(f"vault {vault!r} has no 'now' list")
        i += 1
        now = take(vault, lambda w: "/" not in w)
        if not was or not now:
            raise Refused(f"vault {vault!r} needs at least one REF after both 'was' and 'now'")
        if len(set(was)) != len(was):
            raise Refused(f"the 'was' list for {vault!r} names an entry more than once")
        if any(g.vault == vault for g in groups):
            raise Refused(f"vault {vault!r} is named more than once")
        groups.append(OrderGroup(vault, was, now))
    return argparse.Namespace(groups=groups, refs=refs)


def _refs(values: Sequence[str]) -> list[Ref]:
    return [parse_ref(v) for v in values]


def _parse_add(args: argparse.Namespace) -> argparse.Namespace:
    refs = _refs(args.refs)
    anchor = parse_ref(args.before or args.after) if (args.before or args.after) else None
    if anchor is not None and any(r.vault != anchor.vault for r in refs):
        raise Refused(f"{anchor.text} is in vault {anchor.vault!r}; every REF must be in the same vault")
    where = "before" if args.before else "after" if args.after else "top" if args.top else "bottom"
    return argparse.Namespace(refs=refs, anchor=anchor, where=where)


def _parse_refs(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(refs=_refs(args.refs))


def _parse_attach(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(ref=parse_ref(args.ref), workspace=parse_workspace(args.workspace))


def _parse_detach(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(ref=parse_ref(args.ref))


def _refs_of(p: argparse.Namespace) -> list[Ref]:
    refs = list(getattr(p, "refs", [])) or [p.ref]
    anchor = getattr(p, "anchor", None)
    return refs + ([anchor] if anchor else [])


def _entry_count(lists: Lists, touched: Sequence[str]) -> dict[str, int]:
    return {v: len(lists[v].entries) for v in touched}


_MESSAGES = {
    pipeline.SAVED: "Saved and synced.",
    pipeline.UNCHANGED: "Nothing changed.",
    pipeline.SAVED_NOT_SYNCED: "Saved on this host, but not synced to the remote; run docket again or lore sync.",
    pipeline.LOST_TO_REMOTE: "The change did not survive the sync because another writer changed the same list; "
                             "the list now stands as shown.",
}


def _execute(name: str, args: argparse.Namespace, runner: Runner, store_kwargs: dict[str, Any]) -> int:
    parse, op = _OPS[name]
    doc: dict[str, Any] = {"command": name}
    try:
        parsed = parse(args)
        touched = list(dict.fromkeys(r.vault for r in _refs_of(parsed)))
        vaults = configured_vaults(runner)
        require_configured(vaults, touched)
        if name == "add":
            for ref in parsed.refs:
                check_kind(runner, ref)
        store = Store(vaults.default_path, **store_kwargs)

        def apply(s: Store) -> pipeline.Applied:
            current = {v: s.read_list(v) for v in touched}
            after, entries, expect = op(current, parsed)
            todo = [v for v in touched if after[v] != current[v]]
            for n, vault in enumerate(todo):
                try:
                    s.update_list(vault, lambda _c, new=after[vault]: new)
                except (OSError, StoreError) as exc:
                    if n:
                        raise PartialWrite(todo[:n], todo[n:], exc) from exc
                    if isinstance(exc, StoreError):
                        raise
                    raise StoreError(f"cannot write {vault}: {exc.strerror or exc}") from exc
            changed = bool(todo)
            return pipeline.Applied(changed, entries, {v: after[v] for v in touched}, expect)

        report = pipeline.run_pipeline(runner, store, vaults.default_name, apply)
        doc.update(_result(report, report.outcome, _entry_count(report.lists, touched)))
        if report.outcome == pipeline.LOST_TO_REMOTE:
            doc["lists"] = {v: [entry_doc(e) for e in lst.entries] for v, lst in report.lists.items()}
    except Refused as exc:
        doc.update(_refused(exc))
        if isinstance(exc, StaleOrder):
            doc["stale"] = exc.vaults
    except PartialWrite as exc:
        doc.update(outcome=PARTIAL, message=str(exc), entries=[], entry_count={}, written=exc.written,
                   not_written=exc.not_written)
    except (UnreadableListError, ListTooLargeError, UnsafeNameError) as exc:
        doc.update(_refused(exc))
    return _emit(doc)


def _result(report: pipeline.Report, outcome: str, entry_count: dict[str, int], **extra: Any) -> dict[str, Any]:
    doc: dict[str, Any] = dict(outcome=outcome, message=_MESSAGES[outcome], entries=report.applied.entries,
                               entry_count=entry_count, **extra)
    if report.sync_warning:
        doc["sync_warning"] = report.sync_warning
    return doc


def _refused(exc: Exception) -> dict[str, Any]:
    entries = exc.entries if isinstance(exc, Refused) else []
    return dict(outcome=pipeline.REFUSED, message=str(exc), entries=entries, entry_count={})


def _emit(doc: dict[str, Any]) -> int:
    print(json.dumps(doc, indent=2))
    return EXIT_CODES[doc["outcome"]]


def _execute_vault_order(args: argparse.Namespace, runner: Runner, store_kwargs: dict[str, Any]) -> int:
    doc: dict[str, Any] = {"command": "vault-order"}
    try:
        names = [parse_vault(v) for v in args.vaults]
        if len(set(names)) != len(names):
            raise Refused("a vault is named more than once")
        vaults = configured_vaults(runner)
        require_configured(vaults, names)
        store = Store(vaults.default_path, **store_kwargs)

        def apply(s: Store) -> pipeline.Applied:
            try:
                change = s.update_order(lambda _current: names)
            except OSError as exc:
                raise StoreError(f"cannot write the vault order: {exc.strerror or exc}") from exc
            return pipeline.Applied(change.changed, [{"ref": n, "status": "stored"} for n in names], {})

        report = pipeline.run_pipeline(runner, store, vaults.default_name, apply)
        outcome = report.outcome
        extra: dict[str, Any] = {}
        try:
            with store.locked():
                stands = list(store.read_order())
                extra["order"] = store.effective_order()
        except (OSError, StoreError) as exc:
            if outcome in (pipeline.SAVED, pipeline.SAVED_NOT_SYNCED):
                outcome = pipeline.SAVED_NOT_SYNCED
            note = f"could not re-read the stored order after the sync: {exc}"
            report.sync_warning = f"{report.sync_warning}; {note}" if report.sync_warning else note
        else:
            if outcome in (pipeline.SAVED, pipeline.SAVED_NOT_SYNCED) and stands != names:
                outcome = pipeline.LOST_TO_REMOTE
        doc.update(_result(report, outcome, {}, **extra))
    except (Refused, UnreadableListError, UnsafeNameError) as exc:
        doc.update(_refused(exc))
    return _emit(doc)


def _values(parser: argparse.ArgumentParser, dest: str = "refs", metavar: str = "REF") -> None:
    parser.add_argument(dest, nargs="+", metavar=metavar, help="values, after --")


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--top", action="store_true", help="add at the top instead of the bottom")
    where.add_argument("--before", metavar="REF", help="add just before this entry")
    where.add_argument("--after", metavar="REF", help="add just after this entry")
    _values(parser)


def _words(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("words", nargs="+", metavar="WORD",
                        help="<vault> was <REF...> now <REF...>, repeated per vault; after --")


def _vault_order_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("vaults", nargs="+", metavar="VAULT", help="vault names in the order wanted, after --")


def _attach_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("ref", metavar="REF")
    parser.add_argument("workspace", metavar="GROUP/SLUG")


def _detach_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("ref", metavar="REF")


_OPS: dict[str, tuple[Callable[[argparse.Namespace], argparse.Namespace], Callable[..., Result]]] = {
    "add": (_parse_add, _add),
    "remove": (_parse_refs, _remove),
    "top": (_parse_refs, _top),
    "attach": (_parse_attach, _attach),
    "detach": (_parse_detach, _detach),
    "set-order": (_parse_set_order, _set_order),
}

_HELP = {
    "add": "Add records to the docket (bottom by default); prints a JSON result.",
    "remove": "Remove entries from the docket; prints a JSON result.",
    "top": "Move entries to the top of their vault's list; prints a JSON result.",
    "attach": "Attach a camp workspace GROUP/SLUG to an entry; prints a JSON result.",
    "detach": "Detach an entry's workspace; prints a JSON result.",
    "set-order": "Reorder whole lists, refused if any moved since read; prints a JSON result.",
    "vault-order": "Store the order of the vaults; prints a JSON result.",
}

_ARGUMENTS: dict[str, Callable[[argparse.ArgumentParser], None]] = {
    "add": _add_arguments,
    "remove": _values,
    "top": _values,
    "attach": _attach_arguments,
    "detach": _detach_arguments,
    "set-order": _words,
    "vault-order": _vault_order_arguments,
}


def make_commands(runner: Runner = default_runner, **store_kwargs: Any) -> dict[str, Any]:
    """The change commands as a name -> `docket.cli.Command` table, reaching lore and git through `runner`."""
    from docket.cli import Command

    def build(name: str) -> Any:
        return Command(_HELP[name], _ARGUMENTS[name],
                       lambda args, name=name: _execute(name, args, runner, store_kwargs))

    commands = {name: build(name) for name in _OPS}
    commands["vault-order"] = Command(_HELP["vault-order"], _ARGUMENTS["vault-order"],
                                      lambda args: _execute_vault_order(args, runner, store_kwargs))
    return commands
