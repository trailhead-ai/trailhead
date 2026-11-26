"""What camp reports about workspaces, read through `camp list --all-hosts --all-groups --json`.

Camp prints notices on stderr and a JSON array of rows on stdout. A workspace row carries `group`,
`slug`, `host` and `state` (`running` when its tmux session is up, `unknown` when tmux did not answer).
A host camp could not ask is a row `{"ok": false, "host", "reason"}`; its reason is remote or transport
text, so only a fixed word for it leaves this module (`REASON_WORDS`).

A camp that cannot run, exits nonzero, times out or prints no array leaves workspace state unknown
(`CampState.rows` is None). Unknown is never reported as stopped: `running_on` answers None whenever the
rows cannot say, and an empty list only when camp answered and found the workspace running nowhere.
"""
from __future__ import annotations

import json
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


UNKNOWN = CampState(None, [])


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
        except ValueError:
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


def running_on(state: CampState, group: str, slug: str) -> list[str] | None:
    """The hosts the workspace is running on; None when camp's answer cannot say."""
    if state.rows is None:
        return None
    mine = [r for r in state.rows if r.get("group") == group and r.get("slug") == slug]
    running = sorted({r["host"] for r in mine if r.get("state") == "running" and isinstance(r.get("host"), str)})
    if not running and any(r.get("state") == "unknown" for r in mine):
        return None
    return running
