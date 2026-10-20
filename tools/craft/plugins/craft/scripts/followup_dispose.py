#!/usr/bin/env python3
"""Follow-up dispose: drop gathered follow-ups of a spec under one reason.

Usage:
    followup_dispose.py dispose <spec-name> --dispositions <json-file> [--preview]

`<spec-name>` is the bare name of a spec record. Every follow-up name must match
`^[A-Za-z0-9._/-]+$`, must not start with `-` and must not contain a `..` path
segment; a name failing the shape check is `invalid-followup-name` (exit 2), refused
before any write.

The dispositions file is one JSON object, `{reason, drops: [{name, revision?}],
merges: [...]}`, read through `followup_parent._read_json_file` (a regular,
non-symlink file of at most MAX_DISPOSITIONS_FILE_BYTES). `merges` may be absent or empty;
a non-empty `merges` is `invalid-dispositions-file`, because this entry point only drops. It is
`invalid-dispositions-file` (exit 2, nothing written) when the shape is wrong, a name is
repeated anywhere, a revision is not a non-empty string, or drops are present and the
reason is empty once sanitized.

Membership is decided by a fresh gather of the spec's follow-ups, in process, never by
the file. A name not among the gathered `followups` is `not-a-gathered-followup` (exit 1,
vault unchanged, a `subject:` line naming it). A drop may be `open`, `ready` or `blocked`.
Each drop's record is read completely before anything is written. A drop that names a
`revision` must match the record's current one; a drop with no `revision` (a `blocked`
follow-up, whose body is never shown) must still be `blocked`. Either mismatch is
`child-changed` (exit 1, vault unchanged, `subject:` names them).

Dependents are every task, in every configured vault, whose `depends-on` names a dropped
follow-up, read from each vault's live `lore task list --vault <v> --json`, never the
reverse index. A listing that fails or is not a JSON list fails the run (`lore-failed`,
`lore-output-not-json`, exit 2, nothing written), `--preview` included: only a successful
read with no match means no dependents.

`--preview` runs every check and writes nothing. Exit 0 prints `{drops: [{name, status}],
merges, dependents}`; `dependents` maps each dropped name to a list of `{name, vault,
status}`.

Writes: each drop, in name order, is re-read immediately before its write. A status or
revision that differs from what this run read before the first write records
`child-changed` in `failed` and skips that drop (`revision` hashes the body only, so
status is compared as well). Otherwise it gets `--status dropped --diff` appending a
`## Dropped` section carrying `Reason: <reason>`, sanitized exactly as
`followup_parent.load_reason` sanitizes a parent's drop reason, then a complete read that
must show both the `dropped` status and that section. A drop counts as failed when lore
exits non-zero, and also when it exits 0 but the read-back does not show it.

Stdout on exit 0 and on every exit 2 after a write is one JSON object:
    dropped              {name, prior-status} per landed drop
    merged               empty in this version
    reset-to-open        empty in this version
    failed               {name, write, command[, reason]} per drop that did not land;
                         `command` is the status-only `lore` command that completes it,
                         built from shape-checked names and carrying no reason, and is null
                         with `reason: "child-changed"` for a drop the guard skipped
    incomplete-merges    empty in this version

Exit codes: 0 done; 1 a refusal with the vault unchanged (`not-a-gathered-followup`,
`child-changed`), stderr carrying `reason-code:` and `subject:` lines; 2 anything else, and
`dispose-incomplete` when any drop is in `failed`. Nothing parsed from `lore` is ever
printed. Every `lore` call is an argv list through the one injectable runner.
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
    }
)

REFUSAL_REASONS = frozenset({"child-changed", "not-a-gathered-followup"})

MAX_DISPOSITIONS_FILE_BYTES = 65536


class DisposeError(parent.ParentError):
    """A failure that maps to a stable `reason-code:`; carries no vault text."""

    def __init__(self, reason: str, subjects: tuple[str, ...] = ()):
        assert reason in REASON_CODES, reason
        Exception.__init__(self, reason)
        self.reason = reason
        self.subjects = subjects
        self.state_unknown = False


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise DisposeError("invalid-arguments")


def load_dispositions(path: str) -> dict:
    """The validated dispositions file: `{reason, drops: [{name, revision?}]}`, `reason`
    sanitized to one line like a parent's drop reason."""
    invalid = DisposeError("invalid-dispositions-file")
    doc = parent._read_json_file(path, MAX_DISPOSITIONS_FILE_BYTES, invalid)
    if not isinstance(doc, dict) or not isinstance(doc.get("reason"), str):
        raise invalid
    drops, merges = doc.get("drops"), doc.get("merges", [])
    if not isinstance(drops, list) or not isinstance(merges, list) or merges:
        raise invalid
    seen: set[str] = set()
    cleaned = []
    for item in drops:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise invalid
        revision = item.get("revision")
        if revision is not None and (not isinstance(revision, str) or not revision):
            raise invalid
        name = item["name"]
        if name in seen:
            raise invalid
        seen.add(name)
        cleaned.append({"name": name, "revision": revision})
    for item in cleaned:
        if not gather._shape_ok(item["name"]):
            raise DisposeError("invalid-followup-name")
    reason = parent._one_line(doc["reason"], parent.MAX_REASON_CHARS)
    if cleaned and not reason:
        raise invalid
    return {"reason": reason, "drops": sorted(cleaned, key=lambda item: item["name"])}


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


def failure_record(vault: str, name: str, write: str, flags: tuple[str, ...] | None, reason: str | None = None) -> dict:
    """`{name, write, command}`: `command` is the status-only `lore` command made of
    `flags` that completes the write, or null when `flags` is None. `reason` is added
    when given."""
    command = None if flags is None else parent._command(parent._update(vault, name, *flags))
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
    flags = ("--status", "dropped")
    try:
        old = reread_for_write(runner, vault, name, baseline)
    except DisposeError:
        return failure_record(vault, name, "drop", None, "child-changed")
    except (parent.ParentError, gather.GatherError):
        return failure_record(vault, name, "drop", flags)
    new = old.rstrip("\n") + f"\n\n## Dropped\n\n{note}\n"
    diff = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a", "b"))
    done = parent._write(runner, parent._update(vault, name, *flags, "--diff"), stdin=diff)
    if done is None or done.returncode != 0 or not _drop_landed(runner, vault, name, note):
        return failure_record(vault, name, "drop", flags)
    return None


def dispose(runner, spec: str, dispositions: dict, preview: bool) -> tuple[dict, str | None, tuple]:
    """Drop the gathered follow-ups named by `dispositions`. Raises DisposeError (or a
    GatherError) for a refusal or a failure before anything is written. Otherwise returns
    (report, reason-code, subjects): the code is None when every drop landed."""
    vault, spec_sidecar = gather.resolve_spec(runner, spec)
    gathered = {
        item["name"]: item["status"]
        for item in gather.gather_followups(runner, vault, spec, spec_sidecar)["followups"]
    }
    drops = dispositions["drops"]
    strangers = tuple(item["name"] for item in drops if item["name"] not in gathered)
    if strangers:
        raise DisposeError("not-a-gathered-followup", strangers)

    baselines = {item["name"]: baseline_of(runner, vault, item["name"]) for item in drops}
    changed = tuple(
        item["name"]
        for item in drops
        if baselines[item["name"]]["status"] != gathered[item["name"]]
        or (
            baselines[item["name"]]["revision"] != item["revision"]
            if item["revision"] is not None
            else baselines[item["name"]]["status"] != "blocked"
        )
    )
    if changed:
        raise DisposeError("child-changed", changed)

    names = [item["name"] for item in drops]
    dependents = dependents_map(runner, names)
    if preview:
        planned = [{"name": n, "status": baselines[n]["status"]} for n in names]
        return {"drops": planned, "merges": [], "dependents": dependents}, None, ()

    note = _dropped_note(dispositions["reason"])
    report: dict = {"dropped": [], "merged": [], "reset-to-open": [], "failed": [], "incomplete-merges": []}
    for name in names:
        failure = drop_one(runner, vault, name, baselines[name], note)
        if failure is None:
            report["dropped"].append({"name": name, "prior-status": baselines[name]["status"]})
        else:
            report["failed"].append(failure)
    if report["failed"]:
        return report, "dispose-incomplete", tuple(f["name"] for f in report["failed"])
    return report, None, ()


def _refuse(code: int, reason: str, subjects: tuple = ()) -> int:
    assert reason in REASON_CODES, reason
    print(f"reason-code: {reason}", file=sys.stderr)
    if subjects:
        print(f"subject: {' '.join(subjects)}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None, runner=None) -> int:
    try:
        parser = _Parser(prog="followup_dispose.py", description="Drop gathered follow-ups under one reason.")
        commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
        run = commands.add_parser("dispose", help="drop the follow-ups a dispositions file names")
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
            return _refuse(1 if err.reason in REFUSAL_REASONS else 2, err.reason, err.subjects)
        except gather.GatherError as err:
            return _refuse(2, err.reason)
        print(json.dumps(result))
        return 0 if reason is None else _refuse(2, reason, subjects)
    except Exception:
        return _refuse(2, "internal-error")


if __name__ == "__main__":
    sys.exit(main())
