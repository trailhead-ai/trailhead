#!/usr/bin/env python3
"""Found-work decision log — records, before it acts, each call a build makes
about work it finds, and reports those calls back.

Usage:
    found_work.py decide [--session-id ID] < decision.json
    found_work.py report --parent <task id> [--session-id ID]

`decide` reads ONE JSON document on stdin — never a command-line argument, so no
finding text ever appears on a command line:

    {"decision": "folded"|"deferred"|"separate"|"brainstorm"|"dropped",
     "parent": "task/<id>", "spec": "spec/<id>",
     "finding": {"title", "what", "where": [paths], "found", "security": bool},
     "reason": "<why>"}

Pipeline, in this order, for every decision:
    validate -> scrub -> neutralize -> idempotency check -> prepare ->
    capture the decision line -> write -> capture the result line

Each decision kind is a `Handler(prepare, write)`. `prepare(decision, runner)`
runs before the capture and returns the record the decision concerns (the
decision line's `record:`); `write(decision, runner, record)` runs only after the
decision line was captured with exit 0 and returns a `WriteResult`. `folded` and
`dropped` write nothing: their `written` result names the build's parent task.
`deferred`, `separate` and `brainstorm` are refused `not-implemented` until a
handler for them is registered in `HANDLERS`. The `runner` is
`runner(argv, stdin=None) -> subprocess.CompletedProcess` running `lore <argv>`;
tests inject a fake.

Both log lines are captured with `lore session candidate --phase Build`, one
candidate body, one line, every field neutralized:

    found-work: <decision> | parent: <id> | record: <id>[#F<n>] | finding: <title> | where: <paths> | why: <reason>
    found-work-result: <written|failed> | parent: <id> | finding: <title> | record: <id>[#F<n>]
        | reason-code: <code|none>

(kinds `found-work` and `found-work-result`; `where` is the paths joined by
", "; an empty `where` or `why` is `-`.)

Validation (refusals, exit 1, `reason-code:` on stderr):
    malformed-input, no-spec (missing or empty `spec`), unsafe-id (parent or spec
    outside `^[A-Za-z0-9._/-]+$`), path-outside-repository (a `where` path that
    is absolute, has a `..` segment, starts with `-`, or holds NUL or a
    backslash), drop-without-reason, security-detail-without-location (a
    `security: true` finding needs a `path:line` in `where`), not-implemented.
A security finding has its `what` and `found` replaced with a fixed
"security finding — see location" before anything is scrubbed, captured or
printed.

Scrub: `criterion_gate.scrub_credential_shaped` runs over every text field
(title, what, each where path, found, reason). The scrub is a best-effort regex
list, not a guarantee that no secret survives; the security posture is that no
finding text is ever placed on a command line, and security findings carry a
location only. Neutralize: control characters, line breaks and other Unicode
separators collapse to a space and every `|` is escaped to `\\|`, so no field can
end a line or forge a field.

Idempotency: a log that already pairs a decision line for the same parent, title
and `where` with a result line makes `decide` print that result line and write
and capture nothing.

Exit codes for `decide`:
    0  done — the result line is printed on stdout.
    1  refused input — `reason-code:` on stderr, nothing captured.
    2  capture-failed — nothing written; the scrubbed finding is printed in full
       on stdout so the caller can carry it into its report. Also used when the
       decision landed but its result line could not be captured (the result
       line is printed instead).
    3  write-failed (reserved for write handlers): the write returned a
       reason-code; a `failed` result line is captured and printed.

`report --parent <id>` reads the session log (`lore session show`), parses only
well-formed lines, from entries of the right kind, whose parent equals the
argument exactly, and shape-checks every id before printing it. Output: first,
under `NEEDS_OPERATOR_HEADING`, each `failed` result marked not written (what
happened, what to do) and each brainstorm task with its
`/craft:brainstorm task/<id>` command; then each decision as a plain sentence;
or exactly `NO_DECISIONS_LINE`. A session that does not exist yet reads as no
decisions; any other failure to read it exits 2 `session-unreadable` rather than
claiming there were none.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from criterion_gate import scrub_credential_shaped  # noqa: E402

REASON_CODES = frozenset(
    {
        "malformed-input",
        "no-spec",
        "unsafe-id",
        "path-outside-repository",
        "drop-without-reason",
        "security-detail-without-location",
        "not-implemented",
        "capture-failed",
        "session-unreadable",
    }
)

EXIT_DONE = 0
EXIT_REFUSED = 1
EXIT_CAPTURE_FAILED = 2
EXIT_WRITE_FAILED = 3

KNOWN_DECISIONS = ("folded", "deferred", "separate", "brainstorm", "dropped")
NEEDS_OPERATOR_HEADING = "Needs the operator:"
NO_DECISIONS_LINE = "The build made no decisions about found work."
SECURITY_REPLACEMENT = "security finding — see location"
LORE_TIMEOUT_SECONDS = 60

_ID_RE = re.compile(r"[A-Za-z0-9._/-]+")
_RECORD_RE = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+(?:#F[1-9][0-9]*)?")
_TASK_RECORD_RE = re.compile(r"task/[A-Za-z0-9._-]+")
_CODE_RE = re.compile(r"[a-z0-9-]+")
_LOCATION_RE = re.compile(r".+:[0-9]+(?:-[0-9]+)?")
_ENTRY_HEADER_RE = re.compile(r"- candidate \S+ kind=(\S+) phase=\S+")
_NEUTRALIZE_CATEGORIES = {"Cc", "Cf", "Zl", "Zp", "Zs"}


class Refusal(Exception):
    def __init__(self, message: str, reason_code: str):
        assert reason_code in REASON_CODES, reason_code
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class Decision:
    kind: str
    parent: str
    spec: str
    title: str
    what: str
    where: tuple[str, ...]
    found: str
    security: bool
    reason: str

    @property
    def where_text(self) -> str:
        return ", ".join(self.where) or "-"

    @property
    def why_text(self) -> str:
        return self.reason or "-"


@dataclass(frozen=True)
class WriteResult:
    record: str
    reason_code: str | None = None


@dataclass(frozen=True)
class Handler:
    prepare: Callable[[Decision, Callable], str]
    write: Callable[[Decision, Callable, str], WriteResult]


def _no_write(decision: Decision, runner, record: str) -> WriteResult:
    return WriteResult(record)


HANDLERS: dict[str, Handler] = {
    "folded": Handler(prepare=lambda d, r: d.parent, write=_no_write),
    "dropped": Handler(prepare=lambda d, r: d.parent, write=_no_write),
}


def lore_runner(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    kwargs = {"stdin": subprocess.DEVNULL} if stdin is None else {"input": stdin}
    return subprocess.run(
        ["lore", *argv], capture_output=True, text=True, timeout=LORE_TIMEOUT_SECONDS, shell=False, **kwargs
    )


def _call(runner, argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    try:
        return runner(argv, stdin)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(argv, 127, "", str(e))


def _session_args(session_id: str | None) -> list[str]:
    return ["--session-id", session_id] if session_id else []


# --- validate / scrub / neutralize -------------------------------------------


def _neutralize(text: str) -> str:
    collapsed = "".join(
        " " if unicodedata.category(ch) in _NEUTRALIZE_CATEGORIES or ch.isspace() else ch for ch in text
    )
    return " ".join(collapsed.split()).replace("|", "\\|")


def _clean(text: str) -> str:
    return _neutralize(scrub_credential_shaped(text))


def _path_ok(path: str) -> bool:
    return not (
        not path
        or path.startswith("/")
        or path.startswith("-")
        or "\x00" in path
        or "\\" in path
        or ".." in path.split("/")
    )


def _str_field(value, name: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise Refusal(f"{name} must be a string", "malformed-input")
    return value


def build_decision(raw: str) -> Decision:
    try:
        doc = json.loads(raw)
    except ValueError:
        raise Refusal("stdin is not a JSON document", "malformed-input")
    if not isinstance(doc, dict):
        raise Refusal("the decision must be a JSON object", "malformed-input")
    kind = doc.get("decision")
    if kind not in KNOWN_DECISIONS:
        raise Refusal(f"decision must be one of {', '.join(KNOWN_DECISIONS)}", "malformed-input")
    finding = doc.get("finding")
    if not isinstance(finding, dict):
        raise Refusal("finding must be an object", "malformed-input")
    parent = doc.get("parent")
    if not isinstance(parent, str):
        raise Refusal("parent must be a string", "malformed-input")
    title = _str_field(finding.get("title"), "finding.title").strip()
    if not title:
        raise Refusal("finding.title is required", "malformed-input")
    what = _str_field(finding.get("what"), "finding.what")
    found = _str_field(finding.get("found"), "finding.found")
    where_raw = finding.get("where", [])
    if not isinstance(where_raw, list) or not all(isinstance(w, str) for w in where_raw):
        raise Refusal("finding.where must be a list of paths", "malformed-input")
    security = finding.get("security", False)
    if not isinstance(security, bool):
        raise Refusal("finding.security must be a boolean", "malformed-input")
    reason = _str_field(doc.get("reason"), "reason").strip()
    spec = _str_field(doc.get("spec"), "spec").strip()

    if not spec:
        raise Refusal("a found-work decision needs a spec; only spec-linked builds log found work", "no-spec")
    for label, value in (("parent", parent), ("spec", spec)):
        if not _ID_RE.fullmatch(value):
            raise Refusal(f"{label} id must match {_ID_RE.pattern}", "unsafe-id")
    for path in where_raw:
        if not _path_ok(path):
            raise Refusal("every where path must be repository-relative", "path-outside-repository")
    if kind == "dropped" and not reason:
        raise Refusal("a dropped decision needs a reason", "drop-without-reason")
    if security:
        if not any(_LOCATION_RE.fullmatch(w) for w in where_raw):
            raise Refusal("a security finding needs a path:line location in where", "security-detail-without-location")
        what = found = SECURITY_REPLACEMENT

    return Decision(
        kind=kind,
        parent=parent,
        spec=spec,
        title=_clean(title),
        what=_clean(what),
        where=tuple(_clean(w) for w in where_raw),
        found=_clean(found),
        security=security,
        reason=_clean(reason),
    )


def decision_line(d: Decision, record: str) -> str:
    return (
        f"found-work: {d.kind} | parent: {d.parent} | record: {record} | finding: {d.title}"
        f" | where: {d.where_text} | why: {d.why_text}"
    )


def result_line(d: Decision, record: str, reason_code: str | None) -> str:
    status = "failed" if reason_code else "written"
    return (
        f"found-work-result: {status} | parent: {d.parent} | finding: {d.title}"
        f" | record: {record} | reason-code: {reason_code or 'none'}"
    )


def finding_text(d: Decision) -> str:
    return "\n".join(
        [
            "capture-failed: the decision was not logged and nothing was written; carry this finding into the report.",
            f"decision: {d.kind}",
            f"parent: {d.parent}",
            f"spec: {d.spec}",
            f"title: {d.title}",
            f"what: {d.what}",
            f"where: {d.where_text}",
            f"found: {d.found}",
            f"security: {'true' if d.security else 'false'}",
            f"reason: {d.why_text}",
        ]
    )


# --- reading the log ----------------------------------------------------------


@dataclass
class Logged:
    kind: str
    parent: str
    record: str
    title: str
    where: str
    why: str
    result_status: str | None = None
    result_record: str | None = None
    result_code: str | None = None
    result_line: str | None = None


def _fields(line: str, prefix: str, keys: list[str]) -> list[str] | None:
    parts = line.split(" | ")
    if len(parts) != len(keys) + 1 or not parts[0].startswith(prefix):
        return None
    values = [parts[0][len(prefix):]]
    for part, key in zip(parts[1:], keys):
        if not part.startswith(key + ": "):
            return None
        values.append(part[len(key) + 2:])
    return values


def parse_log(body: str) -> list[Logged]:
    entries: list[Logged] = []
    kind = None
    for raw in body.splitlines():
        header = _ENTRY_HEADER_RE.fullmatch(raw)
        if header:
            kind = header.group(1)
            continue
        if not raw.startswith("  "):
            kind = None
            continue
        line = raw[2:]
        if kind == "found-work":
            f = _fields(line, "found-work: ", ["parent", "record", "finding", "where", "why"])
            if f and f[0] in KNOWN_DECISIONS and _ID_RE.fullmatch(f[1]) and _RECORD_RE.fullmatch(f[2]) and f[3]:
                entries.append(Logged(f[0], f[1], f[2], f[3], f[4], f[5]))
        elif kind == "found-work-result":
            f = _fields(line, "found-work-result: ", ["parent", "finding", "record", "reason-code"])
            if not (f and f[0] in ("written", "failed") and _ID_RE.fullmatch(f[1])):
                continue
            if not (_RECORD_RE.fullmatch(f[3]) and _CODE_RE.fullmatch(f[4])):
                continue
            for e in reversed(entries):
                if e.parent == f[1] and e.title == f[2] and e.result_status is None:
                    e.result_status, e.result_record, e.result_code, e.result_line = f[0], f[3], f[4], line
                    break
    return entries


def _read_log(runner, session_id: str | None) -> list[Logged]:
    done = _call(runner, ["session", "show", *_session_args(session_id)])
    if done.returncode != 0:
        if "no session record resolved" in (done.stderr or ""):
            return []
        raise Refusal("the session log could not be read", "session-unreadable")
    return parse_log(done.stdout)


# --- decide -------------------------------------------------------------------


def _capture(runner, kind: str, line: str, session_id: str | None) -> bool:
    done = _call(runner, ["session", "candidate", "--kind", kind, "--phase", "Build", *_session_args(session_id)], line)
    return done.returncode == 0


def decide(raw: str, runner, session_id: str | None, handlers: dict[str, Handler]) -> tuple[int, str]:
    d = build_decision(raw)
    handler = handlers.get(d.kind)
    if handler is None:
        raise Refusal(f"the {d.kind} decision cannot be carried out yet", "not-implemented")

    try:
        log = _read_log(runner, session_id)
    except Refusal:
        log = None
    if log:
        for e in log:
            if e.parent == d.parent and e.title == d.title and e.where == d.where_text and e.result_line:
                return EXIT_DONE, e.result_line

    record = handler.prepare(d, runner)
    if not _capture(runner, "found-work", decision_line(d, record), session_id):
        return EXIT_CAPTURE_FAILED, finding_text(d)

    outcome = handler.write(d, runner, record)
    line = result_line(d, outcome.record or record, outcome.reason_code)
    if not _capture(runner, "found-work-result", line, session_id):
        return EXIT_CAPTURE_FAILED, line
    return (EXIT_WRITE_FAILED if outcome.reason_code else EXIT_DONE), line


# --- report -------------------------------------------------------------------

_FAILURE_GUIDE = {
    "shared-layer-prior": (
        "the spec lives in a shared vault, which this build cannot write to.",
        "Add the entry to the spec by hand, or make it a task.",
    ),
}
_FAILURE_DEFAULT = (
    "the write did not land.",
    "Look at the finding in the session log and file it by hand.",
)


def _because(why: str) -> str:
    return "" if why == "-" else f", because {why}"


def _sentence(e: Logged) -> str:
    record = e.result_record or e.record
    if e.kind == "folded":
        return f"Folded into this build: {e.title}{_because(e.why)}."
    if e.kind == "dropped":
        return f"Dropped: {e.title}{_because(e.why)}."
    if e.kind == "deferred":
        return f"Deferred to {record}: {e.title}{_because(e.why)}."
    if e.kind == "separate":
        return f"Made a separate task {record}: {e.title}{_because(e.why)}."
    return f"Needs a brainstorm: {e.title}{_because(e.why)}."


def report(parent: str, runner, session_id: str | None) -> str:
    entries = [e for e in _read_log(runner, session_id) if e.parent == parent]
    if not entries:
        return NO_DECISIONS_LINE
    operator: list[str] = []
    plain: list[str] = []
    for e in entries:
        if e.result_status == "failed":
            what, todo = _FAILURE_GUIDE.get(e.result_code, _FAILURE_DEFAULT)
            operator.append(f"Not written: {e.title} ({e.kind}): {what} {todo}")
        elif e.result_status is None and e.kind not in ("folded", "dropped"):
            operator.append(
                f"Result not logged: {e.title} ({e.kind}). Check whether the write landed before relying on it."
            )
        else:
            if e.kind == "brainstorm" and e.result_record and _TASK_RECORD_RE.fullmatch(e.result_record):
                operator.append(f"Needs a brainstorm: {e.title}. Run `/craft:brainstorm {e.result_record}`.")
            plain.append(_sentence(e))
    lines: list[str] = []
    if operator:
        lines += [NEEDS_OPERATOR_HEADING, *operator, ""]
    lines += plain
    return "\n".join(lines).rstrip("\n")


# --- CLI ----------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refusal(message, "malformed-input")


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="found_work.py")
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)
    d = sub.add_parser("decide")
    d.add_argument("--session-id", default=None)
    r = sub.add_parser("report")
    r.add_argument("--parent", required=True)
    r.add_argument("--session-id", default=None)
    return p


def main(argv=None, *, stdin: str | None = None, runner=None, handlers=None) -> int:
    runner = runner or lore_runner
    handlers = HANDLERS if handlers is None else handlers
    try:
        args = _parser().parse_args(argv)
        if args.command == "decide":
            raw = sys.stdin.read() if stdin is None else stdin
            code, text = decide(raw, runner, args.session_id, handlers)
            print(text)
            if code == EXIT_CAPTURE_FAILED:
                print("reason-code: capture-failed", file=sys.stderr)
            return code
        if not _ID_RE.fullmatch(args.parent):
            raise Refusal(f"parent id must match {_ID_RE.pattern}", "unsafe-id")
        print(report(args.parent, runner, args.session_id))
        return EXIT_DONE
    except Refusal as r:
        print(f"reason: {r}", file=sys.stderr)
        print(f"reason-code: {r.reason_code}", file=sys.stderr)
        return EXIT_CAPTURE_FAILED if r.reason_code == "session-unreadable" else EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
