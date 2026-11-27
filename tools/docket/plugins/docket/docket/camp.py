"""What camp reports about workspaces, read through `camp list --all-hosts --all-groups --json`.

Camp prints notices on stderr and a JSON array of rows on stdout. A workspace row carries `group`,
`slug`, `host` and `state` (`running` when its tmux session is up, `unknown` when tmux did not answer).
A host camp could not ask is a row `{"ok": false, "host", "reason"}`; its reason is remote or transport
text, so only a fixed word for it leaves this module (`REASON_WORDS`).

When camp cannot run, a file holding Outpost's gateway reply (`docket --workspace-state FILE`) can answer
instead (`from_outpost`).

A camp that cannot run, exits nonzero, times out or prints no array leaves workspace state unknown
(`CampState.rows` is None). Unknown is never reported as stopped: `running_on` answers None whenever the
rows cannot say, and an empty list only when camp answered and found the workspace running nowhere.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from typing import Any, NamedTuple

from docket.store import Runner

CAMP_ARGV = ["camp", "list", "--all-hosts", "--all-groups", "--json"]

TIMED_OUT = "timed out"
UNREACHABLE = "unreachable"
DID_NOT_ANSWER = "did not answer"
REASON_WORDS = (TIMED_OUT, UNREACHABLE, DID_NOT_ANSWER)


class Unanswered(NamedTuple):
    host: str
    reason: str


class CampState(NamedTuple):
    rows: list[dict[str, Any]] | None
    unanswered: list[Unanswered]
    from_outpost: bool = False


UNKNOWN = CampState(None, [])
OUTPOST = "outpost"
OUTPOST_SILENT = CampState(None, [Unanswered(OUTPOST, DID_NOT_ANSWER)])
MAX_OUTPOST_BYTES = 256 * 1024
MAX_OUTPOST_DEPTH = 6


def reason_word(reason: Any) -> str:
    text = reason if isinstance(reason, str) else ""
    if text.startswith("unreachable"):
        return UNREACHABLE
    if "did not finish" in text:
        return TIMED_OUT
    return DID_NOT_ANSWER


def _array(stdout: str) -> list[Any] | None:
    for candidate in [stdout, *(line for line in stdout.splitlines() if line.startswith("["))]:
        try:
            doc = json.loads(candidate)
        except (ValueError, RecursionError):
            continue
        if isinstance(doc, list):
            return doc
    return None


def fetch(runner: Runner) -> CampState:
    try:
        proc = runner(CAMP_ARGV)
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    if proc.returncode != 0:
        return UNKNOWN
    doc = _array(proc.stdout or "")
    if doc is None:
        return UNKNOWN
    rows: list[dict[str, Any]] = []
    unanswered: list[Unanswered] = []
    for row in doc:
        if not isinstance(row, dict):
            continue
        if row.get("ok") is False:
            host = row.get("host")
            entry = Unanswered(host if isinstance(host, str) else "", reason_word(row.get("reason")))
            if entry not in unanswered:
                unanswered.append(entry)
        else:
            rows.append(row)
    return CampState(rows, unanswered)


def _depth(doc: Any) -> int:
    deepest, level = 0, [doc]
    while level:
        deepest += 1
        level = [child for node in level
                 for child in (node.values() if isinstance(node, dict) else node if isinstance(node, list) else ())]
    return deepest


def _outpost_document(path: str) -> Any:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = handle.read(MAX_OUTPOST_BYTES + 1)
    if len(raw) > MAX_OUTPOST_BYTES:
        return None
    doc = json.loads(raw)
    return doc if _depth(doc) <= MAX_OUTPOST_DEPTH else None


def from_outpost(path: str) -> CampState:
    """The workspace state in the file at `path`, Outpost's gateway reply, treated as untrusted input.

    A file that is missing, not a regular file, over `MAX_OUTPOST_BYTES`, nested deeper than
    `MAX_OUTPOST_DEPTH`, not JSON, of the wrong shape or `available: false` reads as unknown, and the state
    then names Outpost among the hosts that did not answer.
    """
    try:
        doc = _outpost_document(path)
    except (OSError, ValueError, RecursionError):
        doc = None
    if not (isinstance(doc, dict) and doc.get("available") is True and isinstance(doc.get("rows"), list)
            and isinstance(doc.get("unanswered", []), list)):
        return OUTPOST_SILENT
    rows = doc["rows"]
    if not all(isinstance(r, dict) and isinstance(r.get("host"), str) and isinstance(r.get("group"), str)
               and isinstance(r.get("slug"), str) and isinstance(r.get("state"), str) for r in rows):
        return OUTPOST_SILENT
    unanswered: list[Unanswered] = []
    for row in doc.get("unanswered", []):
        if not isinstance(row, dict):
            continue
        host, reason = row.get("host"), row.get("reason")
        word = reason if reason in REASON_WORDS else reason_word(reason)
        entry = Unanswered(host if isinstance(host, str) else "", word)
        if entry not in unanswered:
            unanswered.append(entry)
    return CampState(rows, unanswered, from_outpost=True)


def running_on(state: CampState, group: str, slug: str) -> list[str] | None:
    """The hosts the workspace is running on; None when the answer cannot say.

    Outpost's cached state only ever says where a workspace is running: a workspace it shows running nowhere is
    unknown, never empty."""
    if state.rows is None:
        return None
    mine = [r for r in state.rows if r.get("group") == group and r.get("slug") == slug]
    running = sorted({r["host"] for r in mine if r.get("state") == "running" and isinstance(r.get("host"), str)})
    if not running and (state.from_outpost or any(r.get("state") == "unknown" for r in mine)):
        return None
    return running
