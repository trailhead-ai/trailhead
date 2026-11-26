"""The docket change commands: add, remove, top, attach, detach.

Each takes its values as separate argv (after `--`), runs the change pipeline in `docket.sync`, and
prints one JSON result document on stdout. Exit codes: 0 saved or unchanged, 2 refused (nothing
written), 3 saved but not synced, 4 lost to remote. Any other failure is a `docket: <message>` on stderr
with exit 1.

Result document:

    {"outcome": "saved" | "saved-not-synced" | "unchanged" | "refused" | "lost-to-remote",
     "command": "add",
     "message": "<one sentence>",
     "entries": [{"ref": "<vault>/<kind>/<slug>", "status": ...[, "workspace": "GROUP/SLUG"]}],
     "live_count": {"<vault>": <entries in that vault's list>},
     ["sync_warning": "<why a sync failed>",]
     ["lists": {"<vault>": [{"kind", "slug"[, "workspace": {"group", "slug"}]}]}]}    (lost-to-remote only)

Entry statuses: added, already-present, removed, not-found, moved, already-at-top, attached,
already-attached, detached, not-attached, refused. `live_count` counts the entries in each touched
vault's list after the change (as the list stands now, for lost-to-remote).

Commands are built by `make_commands(runner)`; the runner is how lore and git are reached, so tests
hand in a fake and the shipped table uses the real one.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from typing import Any, Callable, Sequence

from docket import sync as pipeline
from docket.errors import DocketError
from docket.store import (
    DocketList, Entry, ListTooLargeError, Runner, Store, UnreadableListError, UnsafeNameError, Workspace,
    default_runner,
)
from docket.validate import Ref, Refused, check_kind, configured_vaults, parse_ref, parse_workspace, require_configured

Lists = dict[str, DocketList]
Result = tuple[Lists, list[dict], list[pipeline.Expectation]]


def _entry(ref: Ref) -> Entry:
    return Entry(ref.kind, ref.slug)


def _keys(lst: DocketList) -> list[pipeline.Key]:
    return [(e.kind, e.slug) for e in lst.entries]


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
        old_keys = [(e.kind, e.slug) for e in current]
        new_keys = [(e.kind, e.slug) for e in new]
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
            out[ref.vault] = DocketList(tuple(e for e in out[ref.vault].entries if (e.kind, e.slug) != ref.key))
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
        moved = [e for e in current if (e.kind, e.slug) in keys]
        rest = [e for e in current if (e.kind, e.slug) not in keys]
        out[vault] = DocketList(tuple(moved + rest))
        moved_keys = [(e.kind, e.slug) for e in moved]
        expect.append(pipeline.Expectation(vault, present=moved_keys, order=moved_keys,
                                           precede=[(moved_keys, [(e.kind, e.slug) for e in rest])]))
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
    entries = tuple(dataclasses.replace(e, workspace=workspace) if (e.kind, e.slug) == ref.key else e
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


def _live_count(lists: Lists, touched: Sequence[str]) -> dict[str, int]:
    return {v: len(lists[v].entries) for v in touched}


def _entry_doc(entry: Entry) -> dict:
    doc: dict[str, Any] = {"kind": entry.kind, "slug": entry.slug}
    if entry.workspace is not None:
        doc["workspace"] = {"group": entry.workspace.group, "slug": entry.workspace.slug}
    return doc


_MESSAGES = {
    pipeline.SAVED: "Saved and synced.",
    pipeline.UNCHANGED: "Nothing changed.",
    pipeline.SAVED_NOT_SYNCED: "Saved on this host, but not synced to the remote; run docket again or lore sync.",
    pipeline.LOST_TO_REMOTE: "The change did not survive the sync because another host changed the same list; "
                             "the list now stands as shown.",
}


def _execute(name: str, args: argparse.Namespace, runner: Runner, store_kwargs: dict[str, Any]) -> int:
    parse, op = _OPS[name]
    doc: dict[str, Any] = {"command": name}
    try:
        parsed = parse(args)
        touched = list(dict.fromkeys(r.vault for r in _refs_of(parsed)))
        vaults = configured_vaults(runner)
        require_configured(vaults, _refs_of(parsed))
        if name == "add":
            for ref in parsed.refs:
                check_kind(runner, ref)
        store = Store(vaults.default_path, **store_kwargs)

        def apply(s: Store) -> pipeline.Applied:
            current = {v: s.read_list(v) for v in touched}
            after, entries, expect = op(current, parsed)
            changed = False
            for vault in touched:
                if after[vault] != current[vault]:
                    s.update_list(vault, lambda _c, new=after[vault]: new)
                    changed = True
            return pipeline.Applied(changed, entries, {v: after[v] for v in touched}, expect)

        report = pipeline.run_pipeline(runner, store, vaults.default_name, apply)
        doc.update(outcome=report.outcome, message=_MESSAGES[report.outcome], entries=report.applied.entries,
                   live_count=_live_count(report.lists, touched))
        if report.sync_warning:
            doc["sync_warning"] = report.sync_warning
        if report.outcome == pipeline.LOST_TO_REMOTE:
            doc["lists"] = {v: [_entry_doc(e) for e in lst.entries] for v, lst in report.lists.items()}
    except Refused as exc:
        doc.update(outcome=pipeline.REFUSED, message=str(exc), entries=exc.entries, live_count={})
    except (UnreadableListError, ListTooLargeError, UnsafeNameError) as exc:
        doc.update(outcome=pipeline.REFUSED, message=str(exc), entries=[], live_count={})
    print(json.dumps(doc, indent=2))
    return pipeline.EXIT_CODES[doc["outcome"]]


def _values(parser: argparse.ArgumentParser, dest: str = "refs", metavar: str = "REF") -> None:
    parser.add_argument(dest, nargs="+", metavar=metavar, help="values, after --")


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--top", action="store_true", help="add at the top instead of the bottom")
    where.add_argument("--before", metavar="REF", help="add just before this entry")
    where.add_argument("--after", metavar="REF", help="add just after this entry")
    _values(parser)


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
}

_HELP = {
    "add": "Add records to the docket (bottom by default); prints a JSON result.",
    "remove": "Remove entries from the docket; prints a JSON result.",
    "top": "Move entries to the top of their vault's list; prints a JSON result.",
    "attach": "Attach a camp workspace GROUP/SLUG to an entry; prints a JSON result.",
    "detach": "Detach an entry's workspace; prints a JSON result.",
}

_ARGUMENTS: dict[str, Callable[[argparse.ArgumentParser], None]] = {
    "add": _add_arguments,
    "remove": _values,
    "top": _values,
    "attach": _attach_arguments,
    "detach": _detach_arguments,
}


def make_commands(runner: Runner = default_runner, **store_kwargs: Any) -> dict[str, Any]:
    """The change commands as a name -> `docket.cli.Command` table, reaching lore and git through `runner`."""
    from docket.cli import Command

    def build(name: str) -> Any:
        return Command(_HELP[name], _ARGUMENTS[name],
                       lambda args, name=name: _execute(name, args, runner, store_kwargs))

    return {name: build(name) for name in _OPS}
