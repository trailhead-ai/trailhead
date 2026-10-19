#!/usr/bin/env python3
"""Follow-up parent: sort refined follow-ups into those that can be nested under
one follow-up parent and those that must be left out, then nest the confirmed ones
under one new parent.

Usage:
    followup_parent.py check <spec-name> [--parent <parent>] <name>...
    followup_parent.py assemble <spec-name> --children <json-file>
    followup_parent.py resume <spec-name> <parent> --children <json-file>
    followup_parent.py state <spec-name> <parent>
    followup_parent.py drop <spec-name> <parent> --reason-file <path>

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
  carries `dependency`, the shape-checked `depends-on` entry that is unfinished, as written
  (a bare task name, or a qualified entry);
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
qualified `kind/name` is never done: lore's runnable check treats any entry that
is not a bare task name as unfinished, and its CLI refuses to write one, so it
only arrives through an edited or synced sidecar. Such a follow-up is left out
as `depends-outside`, as is one whose entry is anything but a bare task name
(`@` and every other unsafe shape included). `dependency` is the entry as written
when it passes the shape check; otherwise it is omitted, so nothing unchecked is
emitted. A `depends-on` that is not a list of strings makes the record malformed.

`check --parent <parent>` names a follow-up parent of the spec (`not-a-followup-parent`
otherwise, as `state` decides): a dependency on a child that parent already holds counts as
inside the set, exactly as `resume` decides it, so the pre-resume check agrees with what
`resume` accepts. The held children are evaluated with the set (they must still be eligible
to count) but are never reported themselves.

When several rules apply, the left-out reason is the first of: `is-a-parent`,
`already-nested`, `routed-to-plan`, `routed-to-brainstorm`,
`unresolved-question`, `not-ready`, `depends-outside`.

Exit codes of `check`:
    0  the JSON object is on stdout.
    2  could not check; nothing is written to stdout. stderr carries one
       `reason-code:` line, one of REASON_CODES.

`assemble` takes `--children`, a JSON file holding a non-empty list of
`{name, reason, revision}`: the follow-up's bare name, the selection reason the
operator saw, and the `revision` that `check` emitted and the operator confirmed.
A file that is not that list (duplicate names included) is `invalid-children-file`;
a name failing the shape check is `invalid-followup-name`.

Before any write, `assemble` re-runs the eligibility check for every child, compares
each child's current revision with the confirmed one, and looks for a live
follow-up parent linked to the spec (the lookup behind `followup_gather.py`'s
`followup-parents`). Any refusal exits 1 with the vault unchanged, and stderr
carries the `reason-code:` line and a `subject:` line of the shape-checked names
at fault:
    child-not-eligible      a child is no longer eligible (not `ready`, nested
                            elsewhere, routed, unresolved, or depending on
                            unfinished work);
    child-changed           a child's revision differs from the confirmed one;
    followup-parent-exists  a live follow-up parent is already linked to the spec.
`revision` is a body hash, so a metadata edit is caught only by the eligibility
re-run.

The parent body follows the plan template: a Goal saying the spec is context and
none of its criteria are this change's to meet (no coverage field), a Delta design
with one `### task/<name>` section per child carrying its `**Selection reason:**`,
Given Axioms, Known Unknowns, and the template's `## Flow-out` section copied when
the script runs. Each reason is collapsed to one line (so no reason text can start a
line of its own), stripped of control and invisible format characters, cut to
MAX_REASON_CHARS characters with a trailing `…` marker, and credential-redacted.

The writes run in this order, and an exit-2 outcome after the create prints the
report (below) on stdout and one `reason-code:` line on stderr:
    1. `lore record create` the parent at `open`, titled `Build the follow-ups of
       <spec-name> chosen <YYYY-MM-DD>`, with `--label craft/followup-parent` and
       `--related spec=<spec-name>`, body on stdin. The created name is read from
       lore's one-line stdout (`task/<name>`) and shape-checked, never derived from
       the title: lore never refuses or overwrites a colliding name, it suffixes
       `-2`, `-3`, ... Anything else (a failed create, such as a name too long for
       the filesystem, or unexpected output) leaves the outcome unclear, so the live
       follow-up parents are re-queried. Whatever it finds, nothing is dropped or
       nested (a same-titled parent may be another run's, so none can be proven
       this run's) and nothing is printed on stdout: `parent-create-failed`
       (exit 2), with a `subject:` line naming every live parent carrying this
       run's title, or none when none does. If the re-query itself fails, the
       exit is 2 with that failure's reason code, a `parent-state: unknown` line
       on stderr, and nothing on stdout.
    2. Re-query live follow-up parents linked to the spec. Any other than the new
       one: undo, exit 2 `concurrent-followup-parent`, with `subject:` naming both.
    3. Nest each child in file order (`--parent`): re-read its revision immediately
       before the write (a mismatch is a failed nesting for that child), write, then
       confirm by a complete read that its `parent` is the new parent. Lore has no
       conditional write, so an edit landing between the re-read and the write is
       not caught.
    4. Flip the parent to `ready` and confirm by a complete read, only when every
       child is confirmed nested.
Creating the parent is the point of no return, and `assemble` rolls forward from there:
a failed nesting, a failed flip, a failed re-query or an unexpected exception leaves the
parent `open`, holding whatever nested, and is `nesting-incomplete` (exit 2) for a failed
nesting or flip, the failure's own code otherwise (`internal-error` for an exception).
`resume` finishes it. The one exception is `concurrent-followup-parent`: this run's own
parent holds no child yet, so it is removed through `un_nest_and_drop` (`--status dropped
--diff`; the why-note rides the same invocation, because `dropped` freezes the body),
confirmed by a complete read.

The shared nesting guard (`assemble` and `resume` nest through one function): immediately
before each `--parent` write the child's sidecar and revision are re-read, and the child is
written only when the revision is the confirmed one, it has no `parent` and its status is
`ready`; otherwise it is a failed nesting (`nest`) and is not written, so a child another
parent took in the window is never re-parented.

Stdout JSON on exit 0 and on every exit 2 after the create (a create of unclear
outcome that left a parent included; a gather failure such as `search-truncated`
or an `internal-error` after the create reports the same way, with the parent left
`open`):
- `parent`: the created parent's bare name;
- `nested`: the children this run confirmed nested;
- `failed`: `{name, write}` per failed write: `nest` (the nesting write, or the
  revision re-read before it, failed), `confirm` (the write succeeded but the read
  shows the child not nested) or `ready` (name is the parent);
- `undone`: `{name, write}` per confirmed undo write; only the concurrent-parent
  removal writes one (`drop`, name the parent), so it is empty otherwise;
- `undo-failed`: `{name, write, command}` per undo write that failed or did not
  read back; empty unless that removal failed. `command` is the exact `lore` command that completes it, built only
  from shape-checked names (the drop command carries no why-note);
- `buildable`: true only on exit 0.

Invariants and security posture:
- Every `lore` call is an argv list run without a shell, with a timeout, through
  one injectable runner. A body (the parent's, or the drop diff) is the only stdin;
  every other call gets closed stdin, since a metadata-only `lore record update`
  errors on a stdin that is open but silent.
- `lore`'s stdout and stderr are vault-derived and are never printed: a failure
  prints its `reason-code:`, plus a `subject:` line of names that passed the shape
  check, and nothing parsed from `lore`.
- Vault text enters the parent body only through the sanitizing above. `assemble`'s
  reasons come from the children file; `resume` re-reads each reason from the
  vault-writable body and passes it through the same sanitizing.
- No write precedes the three refusals, and the parent is created before any child
  is touched, so a refusal leaves the vault unchanged, as does a create that failed
  and left no parent behind.
- Any unexpected exception exits 2 with `reason-code: internal-error`; one that
  occurs after the create leaves the parent `open` and prints the report.

`state` is read-only. It resolves the spec's vault, reads `<parent>` completely and
refuses with exit 1 `not-a-followup-parent` (vault untouched, a `subject:` line naming
the parent, nothing on stdout) unless the record is a task carrying
`craft/followup-parent` and linked to the spec (either side names the other, the
lookup behind `followup_gather.py`'s `followup-parents`); a spec record named as the
parent is refused the same way. Any lookup failure exits 2 with the existing reason
codes. On exit 0 it prints one JSON object:
- `parent`, `status`: the parent's bare name and status;
- `listed`: the shape-checked names of the body's `### task/<name>` headings, in body
  order; `skipped` counts the headings whose name fails the shape check;
- `held`: `{name, status}` for every task whose `parent` is this one, found in the
  live `lore task list --json` and each confirmed by a complete read, sorted by name;
- `unheld`: listed minus held, body order; `unlisted`: held minus listed, sorted.
`parse_listing(body)` returns `(names, skipped)` and `held_children(runner, vault,
parent_name)` the `held` list, as importable functions.

`drop` un-nests the unfinished children of a live follow-up parent and then drops it.
`--reason-file` is a regular, non-symlink file of at most MAX_REASON_FILE_BYTES holding
one JSON string; anything else, or a reason empty once sanitized, is
`invalid-reason-file` (exit 2, nothing written). The reason is folded to one line,
stripped of control and invisible format characters, cut to MAX_REASON_CHARS and
credential-redacted before it is appended to the parent as a `## Dropped` section.
Refusals before any write exit 1 with a `subject:` line: `not-a-followup-parent` (as
`state` decides) and `parent-not-live` (status `done`, `dropped` or `superseded`). The
parent's status is re-read immediately before the first write; one that differs from
what `state` reported is `parent-changed` (exit 1, nothing written), such as a build
that started meanwhile. Each held child whose status is `open`, `ready`, `in-progress`
or `blocked` is un-nested in name order (`--unset-parent`, then a complete read
confirming it has no parent); its sidecar is re-read immediately before, and a child no
longer under this parent is skipped and reported in `kept`. A `done`, `dropped` or
`superseded` child stays nested and is in `kept`. Only when every un-nest is confirmed
is the parent dropped (`--status dropped --diff`, confirmed by a complete read). A
failed un-nest leaves the parent live; either failure is exit 2 `drop-incomplete`, and
running `drop` again completes it. Stdout JSON on exit 0 and on every `drop-incomplete`:
- `parent`: the parent's bare name;
- `un-nested`: the children confirmed free, in the order done;
- `kept`: `{name, status}` per child left nested;
- `failed`: `{name, write, command}` per failed write: `un-nest`, or `drop` (name is the
  parent). `command` is the exact `lore` command that completes it, built only from
  shape-checked names; the drop command carries no reason;
- `reset`: `{name, command}` per un-nested child that was `in-progress`; the command
  sets it back to `ready`, since gathering never sees an `in-progress` follow-up;
- `dropped`: true only on exit 0.

`un_nest_and_drop(runner, vault, parent_name, children, note)` is the one
un-nest-then-drop implementation, an importable function: `drop` and `assemble`'s
concurrent-parent removal (which un-nests nothing) both run on it. `children` are bare
names, un-nested in the order given; `note` is the paragraph written under `## Dropped`.

`resume` finishes an `open` follow-up parent. `--children` is a JSON file holding a list,
possibly empty, of `{name, revision}`: the listed-but-unheld follow-ups the operator
confirmed, each at the `revision` that `check` emitted (anything else, or a repeated name,
is `invalid-children-file`; a name failing the shape check is `invalid-followup-name`).
Refusals before any write exit 1 with the vault unchanged and a `subject:` line:
    not-a-followup-parent   as `state` decides;
    parent-not-resumable    the parent's status is not `open`;
    followup-parent-exists  another live follow-up parent is linked to the spec;
    child-not-listed        a file name is not in `state`'s `unheld`;
    unlisted-child          the parent holds a task its body does not list (`state`'s
                            `unlisted`); resume never makes a parent `ready` over a child
                            it does not list, so the operator un-nests it by hand or drops
                            the parent;
    child-not-eligible      a file child fails the `check` eligibility re-run, or a held
                            child (one already under this parent) fails the same rules
                            (not `ready`, routed, an unresolved question, an unfinished
                            dependency) or no longer names this parent. A dependency on a
                            child the parent holds, or on another file child, is finished;
    child-changed           a file child's revision differs from the confirmed one;
    nothing-to-build        no name would remain listed: the parent holds none of its
                            listed children and the file is empty.
The writes run in this order, and every exit 2 after the first prints the report on
stdout and one `reason-code:` line on stderr, leaving the parent `open`:
    1. Re-render the whole body with `render_body` from the names still listed (the
       file children plus the held children, in body order), each `**Selection
       reason:**` read from the current body and sanitized as `render_body` does, so
       nothing but the template's shape survives; an unheld name absent from the file
       is dropped from the list. One full-body update, confirmed by reading
       the whole body back and finding it equal to the rendered one (`lore`
       stores a body byte for byte, so nothing is normalized; lore has no conditional
       write, so an edit landing between the read and this write is lost; a refused or
       unconfirmed write is a failed `list`, with nothing nested).
    2. Nest each file child in file order through the shared guard above.
    3. Flip to `ready` only when every name still listed is held, confirmed by a
       complete read, and the flip is confirmed by a complete read.
Stdout JSON: `parent`; `removed`: the unheld names dropped from the list; `nested`: the
file children this run confirmed nested; `failed`: `{name, write}` with `write` one of
`list` (name is the parent: the body rewrite was refused or did not read back whole), `nest`, `confirm`, or
`ready` (name is the parent: the held set could not be confirmed complete, or the flip did
not read back); `buildable`: true only on exit 0. A failure is `nesting-incomplete`, or its
own code (`internal-error`, a `lore` failure code); running `resume` again completes it.

`check_followups(runner, spec, names)` is the same check as an importable
function, returning the structure the JSON carries; `read_revision(runner,
vault, name)` is the per-record revision read it uses.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import followup_gather as gather  # noqa: E402

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
        "internal-error",
        "invalid-children-file",
        "child-not-eligible",
        "child-changed",
        "followup-parent-exists",
        "concurrent-followup-parent",
        "parent-create-failed",
        "nesting-incomplete",
        "search-truncated",
        "not-a-followup-parent",
        "parent-not-live",
        "parent-changed",
        "invalid-reason-file",
        "drop-incomplete",
        "parent-not-resumable",
        "child-not-listed",
        "nothing-to-build",
        "unlisted-child",
    }
)

REFUSAL_REASONS = frozenset(
    {
        "child-not-eligible",
        "child-changed",
        "followup-parent-exists",
        "not-a-followup-parent",
        "parent-not-live",
        "parent-changed",
        "parent-not-resumable",
        "child-not-listed",
        "nothing-to-build",
        "unlisted-child",
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
MAX_REASON_CHARS = 500
TEMPLATE_PATH = _SCRIPT_DIR.parent / "templates" / "plan.md"
ROUTE_LABEL = "route"
ROUTE_REASONS = {"plan": "routed-to-plan", "brainstorm": "routed-to-brainstorm"}
PARENT_LABELS = (gather.FOLLOWUP_PARENT_LABEL, gather.SLICE_LABEL)
_UNRESOLVED_RE = re.compile(r"^## Refine — unresolved[ \t]*$", re.MULTILINE)
_FIELD_END = r"(?=^\*\*[^*\n]+:\*\*|^#{1,6} |\Z)"
_BACKTICKED_RE = re.compile(r"`([^`\n]+)`")
_LISTING_HEADING_RE = re.compile(r"^### task/(.*?)[ \t]*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6} ", re.MULTILINE)
_SELECTION_REASON_RE = re.compile(r"^\*\*Selection reason:\*\*(.*?)(?:\n[ \t]*\n|\Z)", re.MULTILINE | re.DOTALL)
MAX_STATUS_CHARS = 40
MAX_REASON_FILE_BYTES = 4096
UNFINISHED_STATUSES = frozenset({"open", "ready", "in-progress", "blocked"})
NOT_LIVE_STATUSES = frozenset({"done", "dropped", "superseded"})


class ParentError(Exception):
    """A failure that maps to a stable `reason-code:`; carries no vault text."""

    def __init__(self, reason: str, subjects: tuple[str, ...] = (), state_unknown: bool = False):
        assert reason in REASON_CODES, reason
        super().__init__(reason)
        self.reason = reason
        self.subjects = subjects
        self.state_unknown = state_unknown


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


def _status(sidecar: dict) -> str:
    """The sidecar's status, sanitized and bounded for the report."""
    return gather.sanitize_text(str(sidecar["status"]), MAX_STATUS_CHARS)


def read_revision(runner, vault: str, name: str) -> str:
    """The revision `lore record show --json` reports for `task/<name>`."""
    return _read_task(runner, vault, name)[2]


def _field(body: str, label: str) -> str:
    match = re.search(
        rf"^\*\*{label}:\*\*(.*?){_FIELD_END}", body, re.MULTILINE | re.DOTALL
    )
    return match.group(1) if match else ""


def _one_line(text: str, limit: int) -> str:
    """`text` folded onto one line, then sanitized and cut to `limit` characters."""
    return gather.sanitize_text(" ".join(text.split()), limit)


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
        names.append(entry)
    return sorted(set(names))


def _bare_task_name(entry: str) -> bool:
    return "/" not in entry and gather._shape_ok(entry)


def _static_verdict(sidecar: dict, body: str, expected_parent: str | None = None) -> str | None:
    labels = gather._labels(sidecar)
    if any(label in labels for label in PARENT_LABELS):
        return "is-a-parent"
    if (sidecar.get("parent") or None) != expected_parent:
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


def _classify(
    runner, vault: str, records: dict[str, tuple[dict, str, str]], held: frozenset[str], parent: str | None
) -> tuple[set[str], dict[str, dict]]:
    """(accepted, left_out) over `records`, the `(sidecar, body, revision)` of each name.
    A name in `held` is a child already under `parent`: it must still name that parent
    and passes every other rule a follow-up does, and a dependency among the records
    counts as inside the set, a held child's included."""
    left_out: dict[str, dict] = {}
    candidates: dict[str, list[str]] = {}
    for name, (sidecar, body, _) in records.items():
        reason = _static_verdict(sidecar, body, parent if name in held else None)
        if reason is not None:
            left_out[name] = _left_out(name, reason)
        else:
            candidates[name] = _task_dependencies(sidecar)

    done_outside: dict[str, bool] = {}
    for deps in candidates.values():
        for dep in deps:
            if _bare_task_name(dep) and dep not in records and dep not in done_outside:
                done_outside[dep] = _dependency_done(runner, vault, dep)

    accepted: set[str] = set()

    def finished(dep: str) -> bool:
        return _bare_task_name(dep) and (dep in accepted or done_outside.get(dep, False))

    progressed = True
    while progressed:
        progressed = False
        for name, deps in candidates.items():
            if name not in accepted and all(finished(dep) for dep in deps):
                accepted.add(name)
                progressed = True
    for name, deps in candidates.items():
        if name not in accepted:
            unfinished = next(dep for dep in deps if not finished(dep))
            left_out[name] = _left_out(
                name, "depends-outside", unfinished if gather._shape_ok(unfinished) else None
            )
    return accepted, left_out


def check_followups(runner, spec: str, names: list[str], parent: str | None = None) -> dict:
    """Sort the named follow-ups of `spec` into eligible and left out. With `parent`, a
    follow-up depending on a child that follow-up parent already holds counts that
    dependency as inside the set, exactly as `resume` decides it."""
    if not gather._shape_ok(spec):
        raise ParentError("invalid-spec-name")
    if not all(gather._shape_ok(n) for n in names):
        raise ParentError("invalid-followup-name")
    if parent is not None and not gather._shape_ok(parent):
        raise ParentError("invalid-followup-name")
    names = sorted(set(names))
    vault, _ = gather.resolve_spec(runner, spec)

    held: frozenset[str] = frozenset()
    if parent is not None:
        held = frozenset(item["name"] for item in parent_state(runner, spec, parent)["held"]) - set(names)
    records = {n: _read_task(runner, vault, n) for n in [*names, *sorted(held)]}
    accepted, left_out = _classify(runner, vault, records, held, parent)
    return _report(records, names, accepted, left_out)


def _report(records: dict, names: list[str], accepted: set[str], left_out: dict[str, dict]) -> dict:
    """The `check` structure for `names` alone, out of the `_classify` verdicts."""
    left_out = {n: item for n, item in left_out.items() if n in names}
    eligible, files_by_name = [], {}
    for name in sorted(accepted & set(names)):
        _, body, revision = records[name]
        raw_files = _field(body, "Files")
        eligible.append(
            {
                "name": name,
                "revision": revision,
                "delivers": _one_line(_field(body, "Delivers"), MAX_FIELD_CHARS),
                "files": _one_line(raw_files, MAX_FIELD_CHARS),
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


def _flow_out_section() -> str:
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    start = re.search(r"^## Flow-out[ \t]*$", text, re.MULTILINE)
    if start is None:
        raise ValueError("template has no Flow-out section")
    end = re.search(r"^## ", text[start.end() :], re.MULTILINE)
    return text[start.start() : start.end() + end.start()] if end else text[start.start() :]


def render_body(spec: str, children: list[dict]) -> str:
    """The follow-up parent's body: the plan template's shape, one section per
    child carrying its sanitized, single-line selection reason."""
    sections = "".join(
        f"### task/{child['name']}\n\n**Selection reason:** {_one_line(child['reason'], MAX_REASON_CHARS)}\n\n"
        for child in children
    )
    return (
        f"**Goal:** Build the follow-ups below, each already refined to ready. `spec/{spec}` is context "
        "only: none of its acceptance criteria are this change's to meet.\n\n"
        "**Delta design:** Build these follow-ups, each nested under this parent:\n\n"
        f"{sections}"
        "**Given Axioms (ground truth this plan rests on):**\n\n"
        "- Each child was refined to `ready` and passed the dependency check before it was nested.\n\n"
        "**Known Unknowns:**\n\n"
        "- None.\n\n"
        f"{_flow_out_section()}"
    )


def _load_children_file(path: str, keys: tuple[str, ...], allow_empty: bool) -> list[dict]:
    """A JSON list of objects each carrying the string `keys` (a non-empty
    `revision`, a unique, shape-checked `name`), empty only when `allow_empty`."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ParentError("invalid-children-file") from None
    if not isinstance(doc, list) or not (doc or allow_empty):
        raise ParentError("invalid-children-file")
    children, seen = [], set()
    for item in doc:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in keys):
            raise ParentError("invalid-children-file")
        if not item["revision"] or item["name"] in seen:
            raise ParentError("invalid-children-file")
        if not gather._shape_ok(item["name"]):
            raise ParentError("invalid-followup-name")
        seen.add(item["name"])
        children.append({k: item[k] for k in keys})
    return children


def load_children(path: str) -> list[dict]:
    """The children file: a non-empty JSON list of `{name, reason, revision}`."""
    return _load_children_file(path, ("name", "reason", "revision"), allow_empty=False)


def _loaded_gatherer(runner, vault: str):
    """A gatherer over `vault` with the live `lore task list --json` loaded."""
    g = gather._Gatherer(runner, vault, gather.vault_path(runner, vault))
    g.load_listing()
    return g


def live_followup_parents(runner, vault: str, spec: str, spec_sidecar: dict) -> list[str]:
    """Sorted names of live follow-up parents linked to the spec: the same lookup
    that fills the gathering script's `followup-parents`."""
    return gather.live_parent_names(_loaded_gatherer(runner, vault).spec_linked(spec, spec_sidecar))


def _write(runner, argv: list[str], stdin: str | None = None):
    """The finished write, or None when `lore` could not be run (a write that may
    or may not have landed)."""
    try:
        return runner(argv, stdin=stdin)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _created_name(done) -> str | None:
    if done is None or done.returncode != 0:
        return None
    kind, _, name = done.stdout.strip().partition("/")
    return name if kind == "task" and gather._shape_ok(name) else None


def _live_parents_titled(runner, vault: str, spec: str, spec_sidecar: dict, title: str) -> list[str]:
    """Names of the live follow-up parents carrying `title`."""
    return [
        name
        for name in live_followup_parents(runner, vault, spec, spec_sidecar)
        if gather._shape_ok(name) and _read_task(runner, vault, name)[0].get("title") == title
    ]


def _read_ok(read) -> bool:
    try:
        return bool(read())
    except (ParentError, gather.GatherError):
        return False


def _update(vault: str, name: str, *flags: str) -> list[str]:
    return ["record", "update", f"task/{name}", "--vault", vault, *flags]


def _command(argv: list[str]) -> str:
    return " ".join(["lore", *argv])


def un_nest_and_drop(runner, vault: str, parent_name: str, children: list[str], note: str) -> dict:
    """The one un-nest-then-drop implementation. Un-nests each of `children` in the
    order given, then drops `parent_name` with `note` appended as a `## Dropped`
    section (the why-note rides the same `--status dropped --diff` write, because
    `dropped` freezes the body). Each child's sidecar is re-read immediately before
    its un-nest: one no longer under this parent is left alone and reported in
    `kept`, and a read that fails is a failed write, never a guess. Every write is
    confirmed by a complete read. The parent is dropped only when every un-nest is
    confirmed. Returns `un-nested`, `kept`
    (`{name, status}`), `failed` (`{name, write, command}`, `command` the exact
    `lore` command that completes the write, carrying no note), `reset` (`{name,
    command}` for each un-nested child that was `in-progress`) and `dropped`."""
    report: dict = {"un-nested": [], "kept": [], "failed": [], "reset": [], "dropped": False}
    for name in children:
        argv = _update(vault, name, "--unset-parent")
        failure = {"name": name, "write": "un-nest", "command": _command(argv)}
        try:
            sidecar = _read_task(runner, vault, name)[0]
        except (ParentError, gather.GatherError):
            report["failed"].append(failure)
            continue
        status = _status(sidecar)
        if sidecar.get("parent") != parent_name:
            report["kept"].append({"name": name, "status": status})
            continue
        _write(runner, argv)
        if _read_ok(lambda: not _read_task(runner, vault, name)[0].get("parent")):
            report["un-nested"].append(name)
            if status == "in-progress":
                command = _command(_update(vault, name, "--status", "ready"))
                report["reset"].append({"name": name, "command": command})
        else:
            report["failed"].append(failure)
    if report["failed"]:
        return report

    try:
        old = _read_task(runner, vault, parent_name)[1]
    except (ParentError, gather.GatherError):
        old = None
    if old is not None:
        new = old.rstrip("\n") + f"\n\n## Dropped\n\n{note}\n"
        diff = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a", "b"))
        _write(runner, _update(vault, parent_name, "--status", "dropped", "--diff"), stdin=diff)
        report["dropped"] = _read_ok(lambda: _read_task(runner, vault, parent_name)[0]["status"] == "dropped")
    if not report["dropped"]:
        command = _command(_update(vault, parent_name, "--status", "dropped"))
        report["failed"].append({"name": parent_name, "write": "drop", "command": command})
    return report


def assemble(runner, spec: str, children: list[dict], today: datetime.date) -> tuple[dict, str | None, tuple]:
    """Nest the confirmed children under one new follow-up parent. Raises ParentError
    for a refusal or a failed create, before anything is written. Otherwise returns
    (report, reason, subjects): `reason` is None when the parent is buildable."""
    names = [c["name"] for c in children]
    result = check_followups(runner, spec, names)
    current = {item["name"]: item["revision"] for item in result["eligible"]}
    gone = tuple(sorted(n for n in names if n not in current))
    if gone:
        raise ParentError("child-not-eligible", gone)
    changed = tuple(sorted(c["name"] for c in children if current[c["name"]] != c["revision"]))
    if changed:
        raise ParentError("child-changed", changed)
    vault, spec_sidecar = gather.resolve_spec(runner, spec)
    live = live_followup_parents(runner, vault, spec, spec_sidecar)
    if live:
        raise ParentError("followup-parent-exists", tuple(live))

    body = render_body(spec, children)
    title = f"Build the follow-ups of {spec} chosen {today.isoformat()}"
    created = _write(
        runner,
        ["record", "create", "--kind", "task", "--title", title, "--vault", vault, "--status", "open",
         "--label", gather.FOLLOWUP_PARENT_LABEL, "--related", f"spec={spec}"],
        stdin=body,
    )
    parent_name = _created_name(created)
    if parent_name is None:
        try:
            mine = _live_parents_titled(runner, vault, spec, spec_sidecar, title)
        except (ParentError, gather.GatherError) as err:
            raise ParentError(err.reason, state_unknown=True) from None
        raise ParentError("parent-create-failed", tuple(sorted(mine)))
    report: dict = {"parent": parent_name, "nested": [], "failed": [], "undone": [], "undo-failed": []}
    reason, subjects = None, ()
    try:
        rival = [n for n in live_followup_parents(runner, vault, spec, spec_sidecar) if n != parent_name]
        if rival:
            reason, subjects = "concurrent-followup-parent", tuple(sorted([parent_name, *rival]))
            done = un_nest_and_drop(runner, vault, parent_name, [], f"Assembly undone: {reason}.")
            if done["dropped"]:
                report["undone"].append({"name": parent_name, "write": "drop"})
            report["undo-failed"].extend(done["failed"])
        else:
            _nest_children(runner, vault, parent_name, children, report)
            if not report["failed"]:
                _flip(runner, vault, parent_name, report)
            if report["failed"]:
                reason = "nesting-incomplete"
    except (ParentError, gather.GatherError) as err:
        reason = err.reason
    except Exception:
        reason = "internal-error"
    report["buildable"] = reason is None
    return report, reason, subjects


def _nest_children(runner, vault: str, parent_name: str, children: list[dict], report: dict) -> None:
    """Nest each child under `parent_name` in order, stopping at the first failure.
    Immediately before each `--parent` write the child's sidecar and revision are
    re-read and must show the confirmed revision, no `parent` and status `ready`;
    otherwise the child is a failed nesting and is not written, so a child another
    parent took in the window is never re-parented. Each write is confirmed by a
    complete read. Appends to `report["nested"]` and `report["failed"]`."""
    for child in children:
        name = child["name"]
        failure = {"name": name, "write": "nest"}
        try:
            sidecar, _, revision = _read_task(runner, vault, name)
        except (ParentError, gather.GatherError):
            report["failed"].append(failure)
            return
        if revision != child["revision"] or sidecar.get("parent") or sidecar["status"] != "ready":
            report["failed"].append(failure)
            return
        done = _write(runner, _update(vault, name, "--parent", parent_name))
        if done is not None and done.returncode != 0:
            report["failed"].append(failure)
            return
        if not _read_ok(lambda: _read_task(runner, vault, name)[0].get("parent") == parent_name):
            report["failed"].append({"name": name, "write": "confirm" if done is not None else "nest"})
            return
        report["nested"].append(name)


def _flip(runner, vault: str, parent_name: str, report: dict) -> None:
    done = _write(runner, _update(vault, parent_name, "--status", "ready"))
    if done is None or done.returncode != 0 or not _read_ok(
        lambda: _read_task(runner, vault, parent_name)[0]["status"] == "ready"
    ):
        report["failed"].append({"name": parent_name, "write": "ready"})


def parse_listing(body: str) -> tuple[list[str], int]:
    """The shape-checked names of the `### task/<name>` headings `render_body` writes,
    in body order (a repeated name once), and the count of headings skipped for a
    name that fails the shape check."""
    names: list[str] = []
    skipped = 0
    for match in _LISTING_HEADING_RE.finditer(body):
        name = match.group(1)
        if not gather._shape_ok(name):
            skipped += 1
        elif name not in names:
            names.append(name)
    return names, skipped


def held_children(runner, vault: str, parent_name: str, listing: dict[str, dict] | None = None) -> list[dict]:
    """`{name, status}` of every task whose `parent` is `parent_name`, sorted by name:
    the candidates come from the live `lore task list --json` (`listing`, when the
    caller already loaded it) and each is confirmed by a complete read."""
    if listing is None:
        listing = _loaded_gatherer(runner, vault).listing
    held = []
    for name in sorted(n for n, item in listing.items() if item.get("parent") == parent_name):
        sidecar = _read_task(runner, vault, name)[0]
        if sidecar.get("parent") == parent_name:
            held.append({"name": name, "status": _status(sidecar)})
    return held


def parent_state(runner, spec: str, parent_name: str) -> dict:
    """What the follow-up parent `parent_name` of `spec` lists and what it holds.
    Read-only. Raises ParentError `not-a-followup-parent` unless the record is a task
    carrying the marker and linked to the spec."""
    if not gather._shape_ok(spec):
        raise ParentError("invalid-spec-name")
    if not gather._shape_ok(parent_name):
        raise ParentError("invalid-followup-name")
    vault, spec_sidecar = gather.resolve_spec(runner, spec)
    not_a_parent = ParentError("not-a-followup-parent", (parent_name,))
    if gather._show(runner, vault, f"spec/{parent_name}") is not None:
        raise not_a_parent
    sidecar, body, _ = _read_task(runner, vault, parent_name)
    if gather.FOLLOWUP_PARENT_LABEL not in gather._labels(sidecar):
        raise not_a_parent
    g = _loaded_gatherer(runner, vault)
    if parent_name not in g.spec_linked(spec, spec_sidecar):
        raise not_a_parent
    listed, skipped = parse_listing(body)
    held = held_children(runner, vault, parent_name, g.listing)
    held_names = {item["name"] for item in held}
    return {
        "parent": parent_name,
        "status": _status(sidecar),
        "listed": listed,
        "held": held,
        "unheld": [n for n in listed if n not in held_names],
        "unlisted": sorted(held_names - set(listed)),
        "skipped": skipped,
    }


def load_reason(path: str) -> str:
    """The drop reason: the one JSON string in the regular, non-symlink file at
    `path`, at most MAX_REASON_FILE_BYTES long, sanitized to one line and cut to
    MAX_REASON_CHARS. A file that is anything else, or whose reason is empty once
    sanitized, is `invalid-reason-file`."""
    invalid = ParentError("invalid-reason-file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise invalid from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise invalid
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_REASON_FILE_BYTES + 1)
        if len(raw) > MAX_REASON_FILE_BYTES:
            raise invalid
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        raise invalid from None
    finally:
        os.close(fd)
    if not isinstance(doc, str):
        raise invalid
    reason = _one_line(doc, MAX_REASON_CHARS)
    if not reason:
        raise invalid
    return reason


def drop_parent(runner, spec: str, parent_name: str, reason: str) -> tuple[dict, str | None, tuple]:
    """Un-nest every unfinished child of the live follow-up parent `parent_name`,
    then drop it with the sanitized `reason`. Raises ParentError for a refusal, before
    anything is written. Otherwise returns (report, reason-code, subjects): the code is
    None when the parent is dropped."""
    state = parent_state(runner, spec, parent_name)
    if state["status"] in NOT_LIVE_STATUSES:
        raise ParentError("parent-not-live", (parent_name,))
    vault, _ = gather.resolve_spec(runner, spec)
    current = _status(_read_task(runner, vault, parent_name)[0])
    if current != state["status"]:
        raise ParentError("parent-changed", (parent_name,))
    held = state["held"]
    unfinished = [item["name"] for item in held if item["status"] in UNFINISHED_STATUSES]
    done = un_nest_and_drop(runner, vault, parent_name, unfinished, reason)
    kept = [item for item in held if item["status"] not in UNFINISHED_STATUSES] + done["kept"]
    report = {
        "parent": parent_name,
        "un-nested": done["un-nested"],
        "kept": sorted(kept, key=lambda item: item["name"]),
        "failed": done["failed"],
        "reset": done["reset"],
        "dropped": done["dropped"],
    }
    return report, (None if done["dropped"] and not done["failed"] else "drop-incomplete"), (parent_name,)


def load_resume_children(path: str) -> list[dict]:
    """The resume children file: a JSON list, possibly empty, of `{name, revision}`."""
    return _load_children_file(path, ("name", "revision"), allow_empty=True)


def listed_reasons(body: str) -> dict[str, str]:
    """Each `### task/<name>` section's raw `**Selection reason:**` paragraph, by
    shape-checked name (the first section of a repeated name). Vault text: callers
    sanitize it before it is written anywhere."""
    headings = list(_LISTING_HEADING_RE.finditer(body))
    reasons: dict[str, str] = {}
    for match in headings:
        name = match.group(1)
        if not gather._shape_ok(name) or name in reasons:
            continue
        rest = body[match.end() :]
        stop = _HEADING_RE.search(rest)
        section = rest[: stop.start()] if stop else rest
        found = _SELECTION_REASON_RE.search(section)
        reasons[name] = found.group(1) if found else ""
    return reasons


def resume_parent(runner, spec: str, parent_name: str, children: list[dict]) -> tuple[dict, str | None, tuple]:
    """Finish the `open` follow-up parent `parent_name`: nest the confirmed `children`
    (the listed follow-ups it does not hold) and make it `ready` once every name it
    still lists is held. Raises ParentError for a refusal, before anything is written.
    Otherwise returns (report, reason-code, subjects): the code is None when the
    parent is buildable."""
    names = [c["name"] for c in children]
    state = parent_state(runner, spec, parent_name)
    if state["status"] != "open":
        raise ParentError("parent-not-resumable", (parent_name,))
    vault, spec_sidecar = gather.resolve_spec(runner, spec)
    rivals = [n for n in live_followup_parents(runner, vault, spec, spec_sidecar) if n != parent_name]
    if rivals:
        raise ParentError("followup-parent-exists", tuple(sorted(rivals)))
    if state["unlisted"]:
        raise ParentError("unlisted-child", tuple(state["unlisted"]))
    unlisted_here = tuple(sorted(n for n in names if n not in state["unheld"]))
    if unlisted_here:
        raise ParentError("child-not-listed", unlisted_here)
    held = state["held"]
    held_names = {item["name"] for item in held}
    records = {n: _read_task(runner, vault, n) for n in [*names, *sorted(held_names)]}
    accepted, _ = _classify(runner, vault, records, frozenset(held_names), parent_name)
    gone = {n for n in records if n not in accepted}
    if gone:
        raise ParentError("child-not-eligible", tuple(sorted(gone)))
    current = {n: records[n][2] for n in names}
    changed = tuple(sorted(c["name"] for c in children if current[c["name"]] != c["revision"]))
    if changed:
        raise ParentError("child-changed", changed)
    remaining = [n for n in state["listed"] if n in held_names or n in names]
    if not remaining:
        raise ParentError("nothing-to-build", (parent_name,))
    removed = [n for n in state["unheld"] if n not in names]

    report: dict = {"parent": parent_name, "removed": [], "nested": [], "failed": [], "buildable": False}
    reason = None
    try:
        old_body = _read_task(runner, vault, parent_name)[1]
        reasons = listed_reasons(old_body)
        body = render_body(spec, [{"name": n, "reason": reasons.get(n, "")} for n in remaining])
        _write(runner, _update(vault, parent_name), stdin=body)
        if _read_ok(lambda: _read_task(runner, vault, parent_name)[1] == body):
            report["removed"] = removed
            _nest_children(runner, vault, parent_name, children, report)
            if not report["failed"]:
                if _read_ok(lambda: set(remaining) <= {i["name"] for i in held_children(runner, vault, parent_name)}):
                    _flip(runner, vault, parent_name, report)
                else:
                    report["failed"].append({"name": parent_name, "write": "ready"})
        else:
            report["failed"].append({"name": parent_name, "write": "list"})
        if report["failed"]:
            reason = "nesting-incomplete"
    except (ParentError, gather.GatherError) as err:
        reason = err.reason
    except Exception:
        reason = "internal-error"
    report["buildable"] = reason is None
    return report, reason, (parent_name,)


def _refuse(code: int, reason: str, subjects: tuple = (), state_unknown: bool = False) -> int:
    assert reason in REASON_CODES, reason
    print(f"reason-code: {reason}", file=sys.stderr)
    if subjects:
        print(f"subject: {' '.join(subjects)}", file=sys.stderr)
    if state_unknown:
        print("parent-state: unknown", file=sys.stderr)
    return code


def main(argv: list[str] | None = None, runner=None, today: datetime.date | None = None) -> int:
    try:
        parser = _Parser(
            prog="followup_parent.py", description="Sort refined follow-ups and nest them under one parent."
        )
        commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
        check = commands.add_parser("check", help="sort follow-ups into eligible and left out")
        check.add_argument("spec_name", help="bare spec record name (the part after spec/)")
        check.add_argument("names", nargs="+", help="bare task record names")
        check.add_argument(
            "--parent", help="bare name of a follow-up parent whose held children count as inside the set"
        )
        build = commands.add_parser("assemble", help="nest confirmed follow-ups under one new parent")
        build.add_argument("spec_name", help="bare spec record name (the part after spec/)")
        build.add_argument("--children", required=True, help="JSON file: a list of {name, reason, revision}")
        state = commands.add_parser("state", help="report what a follow-up parent lists and holds")
        state.add_argument("spec_name", help="bare spec record name (the part after spec/)")
        state.add_argument("parent", help="bare name of the follow-up parent task")
        drop = commands.add_parser("drop", help="un-nest a parent's unfinished children, then drop it")
        drop.add_argument("spec_name", help="bare spec record name (the part after spec/)")
        drop.add_argument("parent", help="bare name of the follow-up parent task")
        drop.add_argument("--reason-file", required=True, help="JSON file holding one string: why it is dropped")
        resume = commands.add_parser("resume", help="finish an open follow-up parent")
        resume.add_argument("spec_name", help="bare spec record name (the part after spec/)")
        resume.add_argument("parent", help="bare name of the follow-up parent task")
        resume.add_argument("--children", required=True, help="JSON file: a list of {name, revision}")
        runner = runner or lore_runner
        reason = None
        subjects: tuple = ()
        try:
            args = parser.parse_args(argv)
            if args.command == "check":
                result = check_followups(runner, args.spec_name, args.names, args.parent)
            elif args.command == "state":
                result = parent_state(runner, args.spec_name, args.parent)
            elif args.command == "resume":
                if not gather._shape_ok(args.spec_name):
                    raise ParentError("invalid-spec-name")
                if not gather._shape_ok(args.parent):
                    raise ParentError("invalid-followup-name")
                files = load_resume_children(args.children)
                result, reason, subjects = resume_parent(runner, args.spec_name, args.parent, files)
            elif args.command == "drop":
                if not gather._shape_ok(args.spec_name):
                    raise ParentError("invalid-spec-name")
                if not gather._shape_ok(args.parent):
                    raise ParentError("invalid-followup-name")
                note = load_reason(args.reason_file)
                result, reason, subjects = drop_parent(runner, args.spec_name, args.parent, note)
            else:
                if not gather._shape_ok(args.spec_name):
                    raise ParentError("invalid-spec-name")
                children = load_children(args.children)
                result, reason, subjects = assemble(
                    runner, args.spec_name, children, today or datetime.date.today()
                )
        except ParentError as err:
            return _refuse(1 if err.reason in REFUSAL_REASONS else 2, err.reason, err.subjects, err.state_unknown)
        except gather.GatherError as err:
            return _refuse(2, err.reason)
        print(json.dumps(result))
        return 0 if reason is None else _refuse(2, reason, subjects)
    except Exception:
        return _refuse(2, "internal-error")


if __name__ == "__main__":
    sys.exit(main())
