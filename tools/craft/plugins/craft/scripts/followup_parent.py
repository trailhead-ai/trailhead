#!/usr/bin/env python3
"""Follow-up parent: sort refined follow-ups into those that can be nested under
one follow-up parent and those that must be left out.

Usage:
    followup_parent.py check <spec-name> <name>...

`<spec-name>` is the bare name of a spec record and each `<name>` the bare name
of a task record. Every name must match `^[A-Za-z0-9._/-]+$`, must not start
with `-` and must not contain a `..` path segment; anything else is refused
before `lore` is ever invoked. A name starting with `-` is only reachable after
a `--` separator.

`check` resolves the spec's vault, reads every named follow-up completely
(`lore record show --json`) and prints one JSON object on stdout:
- `eligible`: `{name, revision, delivers, files}` per eligible follow-up, sorted
  by name. `revision` is exactly the value `lore record show --json` reports
  as `revision` for that record. `delivers` and `files` are the text of the
  record's `**Delivers:**` and `**Files:**` fields (newlines folded to spaces),
  with control and invisible format characters stripped, cut to
  MAX_FIELD_CHARS characters with a trailing `…` marker when longer, and
  credential spans redacted;
- `left-out`: `{name, reason}` per follow-up that is not eligible, sorted by
  name. `reason` is one of LEFT_OUT_REASONS. A `depends-outside` entry also
  carries `dependency`, the bare, shape-checked name of the unfinished task;
- `overlaps`: `{file, names}` for each backticked token in the `**Files:**`
  field of more than one eligible follow-up (sorted by file; names sorted). It
  is a hint: a path written in plain prose is not seen.

A follow-up is eligible only when its status is `ready`, it has no `parent`, it
carries no `route` label and neither `craft/slice-parent` nor
`craft/followup-parent`, its body has no `## Refine — unresolved` heading, and
every task it `depends-on` is `done` or is itself eligible in this set. A
`dropped`, `superseded` or missing dependency is unfinished. Eligibility among
siblings is the least fixpoint: a follow-up joins the eligible set once all its
dependencies are done or already eligible, so a follow-up depending on a
left-out sibling, or on a cycle, is left out. A `depends-on` entry holding a
qualified `kind/name` (a spec or adr dependency) is not a task dependency and
is ignored; any other entry that is not a bare task name makes the record
malformed.

When several rules apply, the left-out reason is the first of: `is-a-parent`,
`already-nested`, `routed-to-plan`, `routed-to-brainstorm`,
`unresolved-question`, `not-ready`, `depends-outside`.

Exit codes:
    0  the JSON object is on stdout.
    2  could not check; nothing is written to stdout. stderr carries one
       `reason-code:` line, one of REASON_CODES.

Invariants and security posture:
- Every `lore` call is an argv list run without a shell, with a timeout and no
  stdin, through one injectable runner.
- `lore`'s stdout and stderr are vault-derived and are never printed: a failure
  prints its `reason-code:` and nothing parsed from `lore`.
- Any unexpected exception exits 2 with `reason-code: internal-error`.

`check_followups(runner, spec, names)` is the same check as an importable
function, returning the structure the JSON carries; `read_revision(runner,
vault, name)` is the per-record revision read it uses.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import followup_gather as gather  # noqa: E402

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
        "internal-error",
    }
)

LEFT_OUT_REASONS = frozenset(
    {
        "unresolved-question",
        "routed-to-plan",
        "routed-to-brainstorm",
        "not-ready",
        "already-nested",
        "is-a-parent",
        "depends-outside",
    }
)

MAX_FIELD_CHARS = 2000
ROUTE_LABEL = "route"
ROUTE_REASONS = {"plan": "routed-to-plan", "brainstorm": "routed-to-brainstorm"}
PARENT_LABELS = (gather.FOLLOWUP_PARENT_LABEL, gather.SLICE_LABEL)
_UNRESOLVED_RE = re.compile(r"^## Refine — unresolved[ \t]*$", re.MULTILINE)
_FIELD_END = r"(?=^\*\*[^*\n]+:\*\*|^#{1,6} |\Z)"
_BACKTICKED_RE = re.compile(r"`([^`\n]+)`")


class ParentError(Exception):
    """A failure that maps to a stable `reason-code:`; carries no vault text."""

    def __init__(self, reason: str):
        assert reason in REASON_CODES, reason
        super().__init__(reason)
        self.reason = reason


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise ParentError("invalid-arguments")


def _left_out(name: str, reason: str, dependency: str | None = None) -> dict:
    assert reason in LEFT_OUT_REASONS, reason
    item = {"name": name, "reason": reason}
    if dependency is not None:
        item["dependency"] = dependency
    return item


def _read_task(runner, vault: str, name: str) -> tuple[dict, str, str]:
    """(sidecar, body, revision) of `task/<name>`."""
    done = gather._call(runner, ["record", "show", "--json", "--vault", vault, f"task/{name}"])
    if done.returncode != 0:
        if "record not found" in done.stderr:
            raise ParentError("followup-not-found")
        raise ParentError("lore-failed")
    sidecar = gather._parse_sidecar(done.stdout)
    doc = json.loads(done.stdout)
    body, revision = doc.get("body"), doc.get("revision")
    if (
        sidecar["kind"] != "task"
        or not isinstance(body, str)
        or not isinstance(revision, str)
        or not revision
    ):
        raise ParentError("malformed-sidecar")
    return sidecar, body, revision


def read_revision(runner, vault: str, name: str) -> str:
    """The revision `lore record show --json` reports for `task/<name>`."""
    return _read_task(runner, vault, name)[2]


def _field(body: str, label: str) -> str:
    match = re.search(
        rf"^\*\*{label}:\*\*(.*?){_FIELD_END}", body, re.MULTILINE | re.DOTALL
    )
    return match.group(1) if match else ""


def _clean_field(text: str) -> str:
    return gather.sanitize_text(" ".join(text.split()), MAX_FIELD_CHARS)


def _task_dependencies(sidecar: dict) -> list[str]:
    entries = sidecar.get("depends-on")
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ParentError("malformed-sidecar")
    names = []
    for entry in entries:
        if not isinstance(entry, str):
            raise ParentError("malformed-sidecar")
        if "/" in entry:
            continue
        if "@" in entry or not gather._shape_ok(entry):
            raise ParentError("malformed-sidecar")
        names.append(entry)
    return sorted(set(names))


def _static_verdict(sidecar: dict, body: str) -> str | None:
    labels = gather._labels(sidecar)
    if any(label in labels for label in PARENT_LABELS):
        return "is-a-parent"
    if sidecar.get("parent"):
        return "already-nested"
    if ROUTE_LABEL in labels:
        return ROUTE_REASONS.get(labels[ROUTE_LABEL], "not-ready")
    if _UNRESOLVED_RE.search(body):
        return "unresolved-question"
    if sidecar["status"] != "ready":
        return "not-ready"
    return None


def _dependency_done(runner, vault: str, name: str) -> bool:
    sidecar = gather._show(runner, vault, f"task/{name}")
    return sidecar is not None and sidecar["kind"] == "task" and sidecar["status"] == "done"


def check_followups(runner, spec: str, names: list[str]) -> dict:
    """Sort the named follow-ups of `spec` into eligible and left out."""
    if not gather._shape_ok(spec):
        raise ParentError("invalid-spec-name")
    if not all(gather._shape_ok(n) for n in names):
        raise ParentError("invalid-followup-name")
    names = sorted(set(names))
    vault, _ = gather.resolve_spec(runner, spec)

    records = {n: _read_task(runner, vault, n) for n in names}
    left_out: dict[str, dict] = {}
    candidates: dict[str, list[str]] = {}
    for name, (sidecar, body, _) in records.items():
        reason = _static_verdict(sidecar, body)
        if reason is not None:
            left_out[name] = _left_out(name, reason)
        else:
            candidates[name] = _task_dependencies(sidecar)

    done_outside: dict[str, bool] = {}
    for deps in candidates.values():
        for dep in deps:
            if dep not in records and dep not in done_outside:
                done_outside[dep] = _dependency_done(runner, vault, dep)

    accepted: set[str] = set()
    progressed = True
    while progressed:
        progressed = False
        for name, deps in candidates.items():
            if name not in accepted and all(
                dep in accepted or done_outside.get(dep, False) for dep in deps
            ):
                accepted.add(name)
                progressed = True
    for name, deps in candidates.items():
        if name not in accepted:
            unfinished = next(
                dep for dep in deps if dep not in accepted and not done_outside.get(dep, False)
            )
            left_out[name] = _left_out(name, "depends-outside", unfinished)

    eligible, files_by_name = [], {}
    for name in sorted(accepted):
        _, body, revision = records[name]
        raw_files = _field(body, "Files")
        eligible.append(
            {
                "name": name,
                "revision": revision,
                "delivers": _clean_field(_field(body, "Delivers")),
                "files": _clean_field(raw_files),
            }
        )
        files_by_name[name] = {
            gather.sanitize_title(token.strip())
            for token in _BACKTICKED_RE.findall(raw_files)
            if token.strip() and not any(c.isspace() for c in token.strip())
        }
    claimed: dict[str, list[str]] = {}
    for name, files in files_by_name.items():
        for file in files:
            claimed.setdefault(file, []).append(name)
    overlaps = [
        {"file": file, "names": sorted(owners)}
        for file, owners in sorted(claimed.items())
        if len(owners) > 1
    ]
    return {
        "eligible": eligible,
        "left-out": [left_out[n] for n in sorted(left_out)],
        "overlaps": overlaps,
    }


def _refuse(code: int, reason: str) -> int:
    assert reason in REASON_CODES, reason
    print(f"reason-code: {reason}", file=sys.stderr)
    return code


def main(argv: list[str] | None = None, runner=None) -> int:
    try:
        parser = _Parser(
            prog="followup_parent.py", description="Sort refined follow-ups for nesting."
        )
        commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
        check = commands.add_parser("check", help="sort follow-ups into eligible and left out")
        check.add_argument("spec_name", help="bare spec record name (the part after spec/)")
        check.add_argument("names", nargs="+", help="bare task record names")
        runner = runner or gather.lore_runner
        try:
            args = parser.parse_args(argv)
            result = check_followups(runner, args.spec_name, args.names)
        except ParentError as err:
            return _refuse(2, err.reason)
        except gather.GatherError as err:
            return _refuse(2, err.reason)
        print(json.dumps(result))
        return 0
    except Exception:
        return _refuse(2, "internal-error")


if __name__ == "__main__":
    sys.exit(main())
