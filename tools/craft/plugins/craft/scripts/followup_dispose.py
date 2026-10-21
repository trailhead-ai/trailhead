#!/usr/bin/env python3
"""Follow-up dispose: drop gathered follow-ups of a spec under one reason, and merge
overlapping ones into a survivor.

Usage:
    followup_dispose.py dispose <spec-name> --dispositions <json-file> [--preview]

`<spec-name>` is the bare name of a spec record. Every follow-up name must match
`^[A-Za-z0-9._/-]+$`, must not start with `-` and must not contain a `..` path
segment; a name failing the shape check is `invalid-followup-name` (exit 2), refused
before any write.

The dispositions file is one JSON object, read through `followup_parent._read_json_file`
(a regular, non-symlink file of at most MAX_DISPOSITIONS_FILE_BYTES):
    {reason, drops: [{name, revision?}],
     merges: [{survivor: {name, revision}, absorbed: [{name, revision, intent}]}]}
`merges` may be absent or empty. It is `invalid-dispositions-file` (exit 2, nothing
written) when the shape is wrong, a name appears twice anywhere (drops and merges
together, a survivor among its own absorbed included), a revision is not a non-empty
string, a merge has no absorbed follow-up or lacks a survivor/absorbed revision, an
`intent` is missing or is empty once sanitized (`followup_parent._reason_text`: one line,
credentials redacted, markdown link/image/HTML/code characters removed), or drops are
present and the reason is empty once sanitized.

Membership is decided by a fresh gather of the spec's follow-ups, in process, never by
the file. A name not among the gathered `followups` is `not-a-gathered-followup` (exit 1,
vault unchanged, a `subject:` line naming it). A drop may be `open`, `ready` or `blocked`;
every merge member, survivor included, must be `open` or `ready`: a `blocked` one is
`not-mergeable` (exit 1, vault unchanged, `subject:` names it). Each record is read
completely before anything is written. A member that names a `revision` must match the
record's current one; a drop with no `revision` (a `blocked` follow-up, whose body is never
shown) must still be `blocked`. Either mismatch, or a status that differs from the gather's,
is `child-changed` (exit 1, vault unchanged, `subject:` names them).

Dependents are every task, in every configured vault, whose `depends-on` names a dropped
or absorbed follow-up, read from each vault's live `lore task list --vault <v> --json`,
never the reverse index. A listing that fails or is not a JSON list fails the run
(`lore-failed`, `lore-output-not-json`, exit 2, nothing written), `--preview` included:
only a successful read with no match means no dependents. An absorbed follow-up with a
dependent is `not-mergeable` (exit 1, vault unchanged): stderr carries `reason-code:`,
one `subject:` line naming each such absorbed follow-up, and one line per one of them,
`dependents <absorbed>: <vault>/<task> <vault>/<task> ...`, so the skill can offer the
merge the other way round. A survivor may have dependents.

`--preview` runs every check and writes nothing. Exit 0 prints
    {drops: [{name, status}],
     merges: [{survivor, status, reset-to-open, absorbed: [{name, status, note}],
               supersedes: [{name, by}]}],
     dependents}
`note` is the exact text appended to the survivor for that absorbed follow-up
(`## Merged from task/<name>`, a blank line, the sanitized intent, a newline);
`reset-to-open` is true when the survivor is `ready` and will go `open`; `supersedes` is
the supersedes planned. `dependents` maps each dropped name to a list of `{name, vault,
status}`.

Writes: every drop first, in name order, then each merge group in survivor-name order. Each
record is re-read immediately before its write; a status or revision that differs from what
this run read before the first write records `child-changed` in `failed` and skips that
write (`revision` hashes the body only, so status is compared as well).
    drop       `--status dropped --diff` appending `## Dropped` and `Reason: <reason>`
               (sanitized as `followup_parent.load_reason` sanitizes a parent's drop
               reason); the read-back must show the `dropped` status and that section.
    merge      one `--diff` write on the survivor appending a `## Merged from task/<name>`
               section per absorbed follow-up, with `--status open` on the same write when
               the survivor was `ready`. A section is skipped only when its heading and
               the identical intent line are already there together. The read-back must
               show every intent line and the status. Only then, one
               `lore record supersede task/<absorbed> --by task/<survivor> --vault <v>` per
               absorbed follow-up, each attempted whatever the others do, read back as
               `superseded` with the survivor's `supersedes` holding it.
A write counts as failed when lore exits non-zero, and also when it exits 0 but the
read-back does not show it. A note failure skips that group's supersedes; a failure in a
group or a drop never stops the rest.

Stdout on exit 0 and on every exit 2 after a write is one JSON object:
    dropped              {name, prior-status} per landed drop
    merged               {survivor, absorbed: [names], survivor-prior-status} per group that
                         fully landed
    reset-to-open        {name, prior-status} per survivor flipped `ready` -> `open`
    failed               {name, write, command[, reason]} per write that did not land;
                         `write` is `drop`, `merge-note` (`name` is the survivor) or
                         `supersede` (`name` is the absorbed follow-up). `command` is the
                         exact `lore` command that completes it, built from shape-checked
                         names, carrying no free text, and runnable as pasted; it is null
                         for `merge-note` (its text never goes on a command line) and, with
                         `reason: "child-changed"`, for any write the guard skipped
    incomplete-merges    {survivor, absorbed: [names]} per group with a failure; the operator
                         re-enters it with the still-gathered absorbed follow-ups

Exit codes: 0 done; 1 a refusal with the vault unchanged (`not-a-gathered-followup`,
`child-changed`, `not-mergeable`), stderr carrying `reason-code:` and `subject:` lines; 2
anything else, and `dispose-incomplete` when anything is in `failed` (`subject:` names the
failed follow-ups). Nothing parsed from `lore` is ever printed. Every `lore` call is an argv
list through the one injectable runner.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import followup_gather as gather  # noqa: E402
import followup_parent as parent  # noqa: E402

lore_runner = gather.lore_runner

REASON_CODES = frozenset(
    {
        "invalid-spec-name",
        "invalid-followup-name",
        "invalid-arguments",
        "no-vaults",
        "spec-not-found",
        "spec-ambiguous",
        "not-a-spec",
        "malformed-sidecar",
        "empty-sidecar",
        "followup-not-found",
        "lore-missing",
        "lore-timeout",
        "lore-failed",
        "lore-output-not-json",
        "search-truncated",
        "internal-error",
        "child-changed",
        "not-a-gathered-followup",
        "invalid-dispositions-file",
        "dispose-incomplete",
        "not-mergeable",
    }
)

REFUSAL_REASONS = frozenset({"child-changed", "not-a-gathered-followup", "not-mergeable"})

MAX_DISPOSITIONS_FILE_BYTES = 65536


class DisposeError(parent.ParentError):
    """A failure that maps to a stable `reason-code:`; carries no vault text."""

    def __init__(self, reason: str, subjects: tuple[str, ...] = (), details: tuple[str, ...] = ()):
        assert reason in REASON_CODES, reason
        Exception.__init__(self, reason)
        self.reason = reason
        self.subjects = subjects
        self.details = details
        self.state_unknown = False


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise DisposeError("invalid-arguments")


def _member(item, invalid) -> dict:
    """`{name, revision}` of a drop or merge member: `name` a string, `revision` absent or a
    non-empty string."""
    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
        raise invalid
    revision = item.get("revision")
    if revision is not None and (not isinstance(revision, str) or not revision):
        raise invalid
    return {"name": item["name"], "revision": revision}


def _merge_group(group, invalid) -> dict:
    if not isinstance(group, dict):
        raise invalid
    survivor = _member(group.get("survivor"), invalid)
    absorbed = group.get("absorbed")
    if survivor["revision"] is None or not isinstance(absorbed, list) or not absorbed:
        raise invalid
    members = []
    for item in absorbed:
        member = _member(item, invalid)
        intent = item.get("intent")
        if member["revision"] is None or not isinstance(intent, str):
            raise invalid
        member["intent"] = parent._reason_text(intent)
        if not member["intent"]:
            raise invalid
        members.append(member)
    return {"survivor": survivor, "absorbed": sorted(members, key=lambda m: m["name"])}


def load_dispositions(path: str) -> dict:
    """The validated dispositions file: `{reason, drops: [{name, revision?}], merges:
    [{survivor: {name, revision}, absorbed: [{name, revision, intent}]}]}`. `reason` is
    sanitized to one line like a parent's drop reason; each `intent` is sanitized by
    `followup_parent._reason_text`."""
    invalid = DisposeError("invalid-dispositions-file")
    doc = parent._read_json_file(path, MAX_DISPOSITIONS_FILE_BYTES, invalid)
    if not isinstance(doc, dict) or not isinstance(doc.get("reason"), str):
        raise invalid
    drops, merges = doc.get("drops"), doc.get("merges", [])
    if not isinstance(drops, list) or not isinstance(merges, list):
        raise invalid
    cleaned = [_member(item, invalid) for item in drops]
    groups = [_merge_group(group, invalid) for group in merges]
    names = [item["name"] for item in cleaned]
    for group in groups:
        names.append(group["survivor"]["name"])
        names.extend(member["name"] for member in group["absorbed"])
    if len(set(names)) != len(names):
        raise invalid
    if not all(gather._shape_ok(name) for name in names):
        raise DisposeError("invalid-followup-name")
    reason = parent._one_line(doc["reason"], parent.MAX_REASON_CHARS)
    if cleaned and not reason:
        raise invalid
    return {
        "reason": reason,
        "drops": sorted(cleaned, key=lambda item: item["name"]),
        "merges": sorted(groups, key=lambda group: group["survivor"]["name"]),
    }


def dependents_map(runner, names: list[str]) -> dict[str, list[dict]]:
    """For each of `names`, the tasks in every configured vault whose `depends-on` names
    it: `{name, vault, status}`, sorted by vault then name. Read from the live
    `lore task list --vault <v> --json` of each vault. A listing that cannot be read is an
    error, never an empty result."""
    wanted = set(names)
    found: dict[str, list[dict]] = {name: [] for name in names}
    for vault in gather.list_vaults(runner):
        listing = gather._call_json(runner, ["task", "list", "--vault", vault, "--json"])
        if not isinstance(listing, list):
            raise gather.GatherError("lore-output-not-json")
        for item in listing:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise gather.GatherError("lore-output-not-json")
            entries = item.get("depends-on", [])
            if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
                raise gather.GatherError("lore-output-not-json")
            if not gather._shape_ok(item["name"]):
                continue
            status = item.get("status")
            record = {
                "name": item["name"],
                "vault": vault,
                "status": gather.sanitize_text(str(status), parent.MAX_STATUS_CHARS),
            }
            for target in sorted(wanted & set(entries)):
                found[target].append(record)
    for dependents in found.values():
        dependents.sort(key=lambda d: (d["vault"], d["name"]))
    return found


def failure_record(name: str, write: str, argv: list[str] | None, reason: str | None = None) -> dict:
    """`{name, write, command}`: `command` is the `lore` command `argv` that completes the
    write, or null when `argv` is None. `reason` is added when given."""
    command = None if argv is None else parent._command(argv)
    record = {"name": name, "write": write, "command": command}
    if reason is not None:
        record["reason"] = reason
    return record


def baseline_of(runner, vault: str, name: str) -> dict:
    """The record's status, revision and body, read completely."""
    sidecar, body, revision = parent._read_task(runner, vault, name)
    return {"status": sidecar["status"], "revision": revision, "body": body}


def reread_for_write(runner, vault: str, name: str, baseline: dict) -> str:
    """The record's body, read immediately before a write. Raises `child-changed` when its
    status or revision differs from `baseline`: the revision hashes the body only, so a
    moved status is caught only by comparing it too."""
    now = baseline_of(runner, vault, name)
    if now["status"] != baseline["status"] or now["revision"] != baseline["revision"]:
        raise DisposeError("child-changed", (name,))
    return now["body"]


def _dropped_note(reason: str) -> str:
    return f"Reason: {reason}"


def _appending_diff(old: str, sections: list[str]) -> str:
    """The unified diff that appends each of `sections` to `old`, a blank line before each."""
    new = old.rstrip("\n") + "".join(f"\n\n{section}" for section in sections)
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a", "b"))


def _drop_landed(runner, vault: str, name: str, note: str) -> bool:
    try:
        sidecar, body, _ = parent._read_task(runner, vault, name)
    except (parent.ParentError, gather.GatherError):
        return False
    return sidecar["status"] == "dropped" and f"\n## Dropped\n\n{note}\n" in f"\n{body}\n"


def drop_one(runner, vault: str, name: str, baseline: dict, note: str) -> dict | None:
    """Drop `name` with `note` appended as a `## Dropped` section, on one
    `--status dropped --diff` write. Returns None when the read-back shows the drop landed,
    otherwise the `failed` record."""
    argv = parent._update(vault, name, "--status", "dropped")
    try:
        old = reread_for_write(runner, vault, name, baseline)
    except DisposeError:
        return failure_record(name, "drop", None, "child-changed")
    except (parent.ParentError, gather.GatherError):
        return failure_record(name, "drop", argv)
    diff = _appending_diff(old, [f"## Dropped\n\n{note}\n"])
    done = parent._write(runner, [*argv, "--diff"], stdin=diff)
    if done is None or done.returncode != 0 or not _drop_landed(runner, vault, name, note):
        return failure_record(name, "drop", argv)
    return None


def _section(name: str, intent: str) -> str:
    return f"## Merged from task/{name}\n\n{intent}\n"


def _has_section(body: str, section: str) -> bool:
    return f"\n{section}" in f"\n{body}"


def _note_landed(runner, vault: str, group: dict, expect_status: str) -> bool:
    """True when the survivor shows every absorbed intent line under its heading and the
    status the write was to leave."""
    try:
        sidecar, body, _ = parent._read_task(runner, vault, group["survivor"]["name"])
    except (parent.ParentError, gather.GatherError):
        return False
    return sidecar["status"] == expect_status and all(
        _has_section(body, _section(m["name"], m["intent"])) for m in group["absorbed"]
    )


def note_one(runner, vault: str, group: dict, baseline: dict) -> dict | None:
    """Write the group's `## Merged from` sections to the survivor on one `--diff` write
    (with `--status open` when the survivor was `ready`). Returns None when the read-back
    shows them, otherwise the `failed` record."""
    survivor = group["survivor"]["name"]
    try:
        old = reread_for_write(runner, vault, survivor, baseline)
    except DisposeError:
        return failure_record(survivor, "merge-note", None, "child-changed")
    except (parent.ParentError, gather.GatherError):
        return failure_record(survivor, "merge-note", None)
    missing = [m for m in group["absorbed"] if not _has_section(old, _section(m["name"], m["intent"]))]
    reopen = baseline["status"] == "ready"
    expect = "open" if reopen else baseline["status"]
    if missing or reopen:
        flags = ["--status", "open"] if reopen else []
        stdin = None
        if missing:
            stdin = _appending_diff(old, [_section(m["name"], m["intent"]) for m in missing])
            flags.append("--diff")
        done = parent._write(runner, parent._update(vault, survivor, *flags), stdin=stdin)
        if done is None or done.returncode != 0:
            return failure_record(survivor, "merge-note", None)
    if not _note_landed(runner, vault, group, expect):
        return failure_record(survivor, "merge-note", None)
    return None


def _supersede_landed(runner, vault: str, survivor: str, absorbed: str) -> bool:
    try:
        sidecar, _, _ = parent._read_task(runner, vault, absorbed)
        survivor_sidecar, _, _ = parent._read_task(runner, vault, survivor)
    except (parent.ParentError, gather.GatherError):
        return False
    links = survivor_sidecar.get("supersedes")
    return sidecar["status"] == "superseded" and isinstance(links, list) and f"task/{absorbed}" in links


def supersede_one(runner, vault: str, survivor: str, absorbed: str, baseline: dict) -> dict | None:
    """Supersede `absorbed` by `survivor`. Returns None when the read-back shows
    `superseded` and the survivor's link, otherwise the `failed` record."""
    argv = ["record", "supersede", f"task/{absorbed}", "--by", f"task/{survivor}", "--vault", vault]
    try:
        reread_for_write(runner, vault, absorbed, baseline)
    except DisposeError:
        return failure_record(absorbed, "supersede", None, "child-changed")
    except (parent.ParentError, gather.GatherError):
        return failure_record(absorbed, "supersede", argv)
    done = parent._write(runner, argv)
    if done is None or done.returncode != 0 or not _supersede_landed(runner, vault, survivor, absorbed):
        return failure_record(absorbed, "supersede", argv)
    return None


def merge_one(runner, vault: str, group: dict, baselines: dict, report: dict) -> None:
    """Run one merge group: the note, then (only if it landed) a supersede per absorbed
    follow-up, each attempted whatever the others do. Records into `report`."""
    survivor = group["survivor"]["name"]
    names = [m["name"] for m in group["absorbed"]]
    failures = []
    failure = note_one(runner, vault, group, baselines[survivor])
    if failure is None:
        if baselines[survivor]["status"] == "ready":
            report["reset-to-open"].append({"name": survivor, "prior-status": "ready"})
        for name in names:
            failure = supersede_one(runner, vault, survivor, name, baselines[name])
            if failure is not None:
                failures.append(failure)
    else:
        failures.append(failure)
    report["failed"].extend(failures)
    if failures:
        report["incomplete-merges"].append({"survivor": survivor, "absorbed": names})
    else:
        report["merged"].append(
            {"survivor": survivor, "absorbed": names, "survivor-prior-status": baselines[survivor]["status"]}
        )


def _moved(item: dict, baseline: dict) -> bool:
    """Whether a member moved since the dispositions file was written: its `revision` no
    longer matches, or a revisionless drop is no longer `blocked`."""
    if item["revision"] is not None:
        return baseline["revision"] != item["revision"]
    return baseline["status"] != "blocked"


def _preview_group(group: dict, baselines: dict) -> dict:
    survivor = group["survivor"]["name"]
    status = baselines[survivor]["status"]
    return {
        "survivor": survivor,
        "status": status,
        "reset-to-open": status == "ready",
        "absorbed": [
            {
                "name": m["name"],
                "status": baselines[m["name"]]["status"],
                "note": _section(m["name"], m["intent"]),
            }
            for m in group["absorbed"]
        ],
        "supersedes": [{"name": m["name"], "by": survivor} for m in group["absorbed"]],
    }


def dispose(runner, spec: str, dispositions: dict, preview: bool) -> tuple[dict, str | None, tuple]:
    """Drop and merge the gathered follow-ups named by `dispositions`. Raises DisposeError
    (or a GatherError) for a refusal or a failure before anything is written. Otherwise
    returns (report, reason-code, subjects): the code is None when every write landed."""
    vault, spec_sidecar = gather.resolve_spec(runner, spec)
    gathered = {
        item["name"]: item["status"]
        for item in gather.gather_followups(runner, vault, spec, spec_sidecar)["followups"]
    }
    drops, groups = dispositions["drops"], dispositions["merges"]
    members = [(item, False) for item in drops]
    for group in groups:
        members.append((group["survivor"], True))
        members.extend((m, True) for m in group["absorbed"])
    strangers = tuple(item["name"] for item, _ in members if item["name"] not in gathered)
    if strangers:
        raise DisposeError("not-a-gathered-followup", strangers)

    baselines = {item["name"]: baseline_of(runner, vault, item["name"]) for item, _ in members}
    shifted = tuple(
        item["name"]
        for item, _ in members
        if baselines[item["name"]]["status"] != gathered[item["name"]]
    )
    if shifted:
        raise DisposeError("child-changed", shifted)
    blocked = tuple(
        item["name"] for item, merging in members if merging and baselines[item["name"]]["status"] == "blocked"
    )
    if blocked:
        raise DisposeError("not-mergeable", blocked)
    changed = tuple(item["name"] for item, _ in members if _moved(item, baselines[item["name"]]))
    if changed:
        raise DisposeError("child-changed", changed)

    names = [item["name"] for item in drops]
    absorbed = [m["name"] for group in groups for m in group["absorbed"]]
    dependents = dependents_map(runner, names + absorbed)
    held = [name for name in absorbed if dependents[name]]
    if held:
        lines = tuple(
            f"dependents {name}: " + " ".join(f"{d['vault']}/{d['name']}" for d in dependents[name])
            for name in held
        )
        raise DisposeError("not-mergeable", tuple(held), lines)
    if preview:
        planned = [{"name": n, "status": baselines[n]["status"]} for n in names]
        merges = [_preview_group(group, baselines) for group in groups]
        return {"drops": planned, "merges": merges, "dependents": {n: dependents[n] for n in names}}, None, ()

    note = _dropped_note(dispositions["reason"])
    report: dict = {"dropped": [], "merged": [], "reset-to-open": [], "failed": [], "incomplete-merges": []}
    for name in names:
        failure = drop_one(runner, vault, name, baselines[name], note)
        if failure is None:
            report["dropped"].append({"name": name, "prior-status": baselines[name]["status"]})
        else:
            report["failed"].append(failure)
    for group in groups:
        merge_one(runner, vault, group, baselines, report)
    if report["failed"]:
        return report, "dispose-incomplete", tuple(dict.fromkeys(f["name"] for f in report["failed"]))
    return report, None, ()


def _refuse(code: int, reason: str, subjects: tuple = (), details: tuple = ()) -> int:
    assert reason in REASON_CODES, reason
    print(f"reason-code: {reason}", file=sys.stderr)
    if subjects:
        print(f"subject: {' '.join(subjects)}", file=sys.stderr)
    for line in details:
        print(line, file=sys.stderr)
    return code


def main(argv: list[str] | None = None, runner=None) -> int:
    try:
        parser = _Parser(
            prog="followup_dispose.py",
            description="Drop gathered follow-ups under one reason, and merge overlapping ones into a survivor.",
        )
        commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
        run = commands.add_parser("dispose", help="drop and merge the follow-ups a dispositions file names")
        run.add_argument("spec_name", help="bare spec record name (the part after spec/)")
        run.add_argument("--dispositions", required=True, help="JSON file: {reason, drops, merges}")
        run.add_argument("--preview", action="store_true", help="run every check and write nothing")
        runner = runner or lore_runner
        reason, subjects = None, ()
        try:
            args = parser.parse_args(argv)
            if not gather._shape_ok(args.spec_name):
                raise DisposeError("invalid-spec-name")
            dispositions = load_dispositions(args.dispositions)
            result, reason, subjects = dispose(runner, args.spec_name, dispositions, args.preview)
        except parent.ParentError as err:
            code = 1 if err.reason in REFUSAL_REASONS else 2
            return _refuse(code, err.reason, err.subjects, getattr(err, "details", ()))
        except gather.GatherError as err:
            return _refuse(2, err.reason)
        print(json.dumps(result))
        return 0 if reason is None else _refuse(2, reason, subjects)
    except Exception:
        return _refuse(2, "internal-error")


if __name__ == "__main__":
    sys.exit(main())
