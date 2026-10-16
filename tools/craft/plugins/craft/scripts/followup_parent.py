#!/usr/bin/env python3
"""Follow-up parent: sort refined follow-ups into those that can be nested under
one follow-up parent and those that must be left out, then nest the confirmed ones
under one new parent.

Usage:
    followup_parent.py check <spec-name> <name>...
    followup_parent.py assemble <spec-name> --children <json-file>

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
as `depends-outside` with the entry as `dependency`. An entry that fails the
shape check, or holds `@`, makes the record malformed.

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
       follow-up parents are re-queried. A parent with this run's title is the
       create's: it is dropped through the undo path below, and the report (with
       `parent` set and `buildable` false) is printed with `parent-create-failed`
       (exit 2). With none found, `parent-create-failed` (exit 2) means nothing
       was written, and nothing is on stdout. Several with this run's title cannot
       be told apart from another run's: `concurrent-followup-parent`, nothing
       dropped, `subject:` naming them, nothing on stdout.
    2. Re-query live follow-up parents linked to the spec. Any other than the new
       one: undo, exit 2 `concurrent-followup-parent`, with `subject:` naming both.
    3. Nest each child in file order (`--parent`): re-read its revision immediately
       before the write (a mismatch is a failed nesting for that child), write, then
       confirm by a complete read that its `parent` is the new parent. Lore has no
       conditional write, so an edit landing between the re-read and the write is
       not caught.
    4. Flip the parent to `ready` and confirm by a complete read, only when every
       child is confirmed nested.
On any failed nesting, failed flip or concurrent parent, `assemble` undoes: each
child it wrote to is read back, and only one whose `parent` is the new parent is
un-nested (`--unset-parent`, in reverse order; a child under any other parent, or
none, is left alone, and a read that fails is an `undo-failed` entry, never a
guess), and the parent
is dropped (`--status dropped --diff`; the why-note rides the same invocation,
because `dropped` freezes the body). Each undo write is confirmed by a complete
read. The failure is `nesting-incomplete` (exit 2) for a failed nesting or flip.

Stdout JSON on exit 0 and on every exit 2 after the create (a create of unclear
outcome that left a parent included; a gather failure such as `search-truncated`
after the create reports the same way, once the undo has run):
- `parent`: the created parent's bare name;
- `nested`: the children confirmed nested when the run ends (empty after an undo
  that un-nested them all);
- `failed`: `{name, write}` per failed write: `nest` (the nesting write, or the
  revision re-read before it, failed), `confirm` (the write succeeded but the read
  shows the child not nested) or `ready` (name is the parent);
- `undone`: `{name, write}` per confirmed undo write: `un-nest` or `drop` (name is
  the parent);
- `undo-failed`: `{name, write, command}` per undo write that failed or did not
  read back. `command` is the exact `lore` command that completes it, built only
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
- Vault text enters the parent body only through the sanitizing above; the
  confirmed reasons come from the children file, never from the vault.
- No write precedes the three refusals, and the parent is created before any child
  is touched, so a refusal leaves the vault unchanged, as does a create that failed
  and left no parent behind.
- Any unexpected exception exits 2 with `reason-code: internal-error`; one that
  occurs after the create first undoes what the run wrote.

`check_followups(runner, spec, names)` is the same check as an importable
function, returning the structure the JSON carries; `read_revision(runner,
vault, name)` is the per-record revision read it uses.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import json
import re
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
    }
)

REFUSAL_REASONS = frozenset({"child-not-eligible", "child-changed", "followup-parent-exists"})

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


class ParentError(Exception):
    """A failure that maps to a stable `reason-code:`; carries no vault text."""

    def __init__(self, reason: str, subjects: tuple[str, ...] = ()):
        assert reason in REASON_CODES, reason
        super().__init__(reason)
        self.reason = reason
        self.subjects = subjects


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
            if "/" not in dep and dep not in records and dep not in done_outside:
                done_outside[dep] = _dependency_done(runner, vault, dep)

    accepted: set[str] = set()

    def finished(dep: str) -> bool:
        return "/" not in dep and (dep in accepted or done_outside.get(dep, False))

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
            left_out[name] = _left_out(name, "depends-outside", unfinished)

    eligible, files_by_name = [], {}
    for name in sorted(accepted):
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


def load_children(path: str) -> list[dict]:
    """The children file: a non-empty JSON list of `{name, reason, revision}`."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ParentError("invalid-children-file") from None
    if not isinstance(doc, list) or not doc:
        raise ParentError("invalid-children-file")
    children, seen = [], set()
    for item in doc:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("name", "reason", "revision")):
            raise ParentError("invalid-children-file")
        if not item["revision"] or item["name"] in seen:
            raise ParentError("invalid-children-file")
        if not gather._shape_ok(item["name"]):
            raise ParentError("invalid-followup-name")
        seen.add(item["name"])
        children.append({k: item[k] for k in ("name", "reason", "revision")})
    return children


def live_followup_parents(runner, vault: str, spec: str, spec_sidecar: dict) -> list[str]:
    """Sorted names of live follow-up parents linked to the spec: the same lookup
    that fills the gathering script's `followup-parents`."""
    g = gather._Gatherer(runner, vault, gather.vault_path(runner, vault))
    g.load_listing()
    return gather.live_parent_names(g.spec_linked(spec, spec_sidecar))


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


def _landed_parent(runner, vault: str, spec: str, spec_sidecar: dict, title: str) -> str | None:
    """The follow-up parent a create of unclear outcome left behind, if any. No live
    parent existed before the create, so any live one now with this run's title
    is its. More than one with that title cannot be told apart from another run's."""
    mine = [
        name
        for name in live_followup_parents(runner, vault, spec, spec_sidecar)
        if _read_task(runner, vault, name)[0].get("title") == title
    ]
    if len(mine) > 1:
        raise ParentError("concurrent-followup-parent", tuple(mine))
    return mine[0] if mine else None


def _read_ok(read) -> bool:
    try:
        return bool(read())
    except (ParentError, gather.GatherError):
        return False


def _update(vault: str, name: str, *flags: str) -> list[str]:
    return ["record", "update", f"task/{name}", "--vault", vault, *flags]


def _command(argv: list[str]) -> str:
    return " ".join(["lore", *argv])


def _undo(runner, vault: str, parent_name: str, attempted: list[str], reason: str, report: dict) -> None:
    for name in reversed(attempted):
        argv = _update(vault, name, "--unset-parent")
        try:
            current = _read_task(runner, vault, name)[0].get("parent")
        except (ParentError, gather.GatherError):
            report["undo-failed"].append({"name": name, "write": "un-nest", "command": _command(argv)})
            continue
        if current != parent_name:
            continue
        _write(runner, argv)
        if _read_ok(lambda: not _read_task(runner, vault, name)[0].get("parent")):
            report["undone"].append({"name": name, "write": "un-nest"})
        else:
            report["undo-failed"].append({"name": name, "write": "un-nest", "command": _command(argv)})

    dropped = False
    try:
        old = _read_task(runner, vault, parent_name)[1]
    except (ParentError, gather.GatherError):
        old = None
    if old is not None:
        new = old.rstrip("\n") + f"\n\n## Dropped\n\nAssembly undone: {reason}.\n"
        diff = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a", "b"))
        _write(runner, _update(vault, parent_name, "--status", "dropped", "--diff"), stdin=diff)
        dropped = _read_ok(lambda: _read_task(runner, vault, parent_name)[0]["status"] == "dropped")
    if dropped:
        report["undone"].append({"name": parent_name, "write": "drop"})
    else:
        command = _command(_update(vault, parent_name, "--status", "dropped"))
        report["undo-failed"].append({"name": parent_name, "write": "drop", "command": command})


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
    report: dict = {"parent": parent_name, "nested": [], "failed": [], "undone": [], "undo-failed": []}
    if parent_name is None:
        parent_name = _landed_parent(runner, vault, spec, spec_sidecar, title)
        if parent_name is None:
            raise ParentError("parent-create-failed")
        report["parent"] = parent_name
        _undo(runner, vault, parent_name, [], "its create did not report success", report)
        report["buildable"] = False
        return report, "parent-create-failed", ()
    attempted: list[str] = []
    reason, subjects = None, ()
    try:
        rival = [n for n in live_followup_parents(runner, vault, spec, spec_sidecar) if n != parent_name]
        if rival:
            reason, subjects = "concurrent-followup-parent", tuple(sorted([parent_name, *rival]))
        else:
            _nest_all(runner, vault, parent_name, children, attempted, report)
            if not report["failed"]:
                _flip(runner, vault, parent_name, report)
            if report["failed"]:
                reason = "nesting-incomplete"
    except (ParentError, gather.GatherError) as err:
        reason = err.reason
    except Exception:
        _undo(runner, vault, parent_name, attempted, "internal-error", {"undone": [], "undo-failed": []})
        raise
    if reason is not None:
        _undo(runner, vault, parent_name, attempted, reason, report)
        report["nested"] = []
    report["buildable"] = reason is None
    return report, reason, subjects


def _nest_all(runner, vault: str, parent_name: str, children: list[dict], attempted: list[str], report: dict) -> None:
    for child in children:
        name = child["name"]
        failure = {"name": name, "write": "nest"}
        try:
            if read_revision(runner, vault, name) != child["revision"]:
                report["failed"].append(failure)
                return
        except (ParentError, gather.GatherError):
            report["failed"].append(failure)
            return
        done = _write(runner, _update(vault, name, "--parent", parent_name))
        if done is not None and done.returncode != 0:
            report["failed"].append(failure)
            return
        attempted.append(name)
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


def _refuse(code: int, reason: str, subjects: tuple = ()) -> int:
    assert reason in REASON_CODES, reason
    print(f"reason-code: {reason}", file=sys.stderr)
    if subjects:
        print(f"subject: {' '.join(subjects)}", file=sys.stderr)
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
        build = commands.add_parser("assemble", help="nest confirmed follow-ups under one new parent")
        build.add_argument("spec_name", help="bare spec record name (the part after spec/)")
        build.add_argument("--children", required=True, help="JSON file: a list of {name, reason, revision}")
        runner = runner or lore_runner
        reason = None
        subjects: tuple = ()
        try:
            args = parser.parse_args(argv)
            if args.command == "check":
                result = check_followups(runner, args.spec_name, args.names)
            else:
                if not gather._shape_ok(args.spec_name):
                    raise ParentError("invalid-spec-name")
                children = load_children(args.children)
                result, reason, subjects = assemble(
                    runner, args.spec_name, children, today or datetime.date.today()
                )
        except ParentError as err:
            return _refuse(1 if err.reason in REFUSAL_REASONS else 2, err.reason, err.subjects)
        except gather.GatherError as err:
            return _refuse(2, err.reason)
        print(json.dumps(result))
        return 0 if reason is None else _refuse(2, reason, subjects)
    except Exception:
        return _refuse(2, "internal-error")


if __name__ == "__main__":
    sys.exit(main())
