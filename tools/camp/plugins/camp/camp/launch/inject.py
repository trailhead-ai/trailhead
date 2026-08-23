"""Mid-session context-injection queue + drain (claude-hook strategy).

The claude-hook inject strategy decouples writers that know something a live
session should learn (`camp activate`, and the activate-phase provisioner) from
the Claude Code PostToolUse hook (which fires on the next tool call). Writers
ENQUEUE to:

    <workspace>/.camp/inject_queue/<unique>.md

One file per enqueue, so multiple writes before a drain are never lost. The
queue has two kinds of entries, and they are NOT treated identically:

  - a member doc (`enqueue_doc`) — `camp activate` handing over a member's
    CLAUDE.md. Delivered however old it is; never age-filtered.
  - a notice (`enqueue_notice` + `build_notice_body`) — the activate-phase
    provisioner reporting that a member's work settled or failed. Camp-authored,
    templated fields only (never task stdout/stderr — see `build_notice_body`),
    and subject to the staleness guard below.

The hidden `camp inject --drain` (wired as a PostToolUse hook with NO matcher,
so it fires after every tool call — a session following the capability report's
advice to avoid Bash still gets its notices) reads the queue, drops any notice
older than `NOTICE_MAX_AGE_SECONDS`, emits the Claude Code additionalContext
JSON contract:

    {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                            "additionalContext": "<queued doc(s)>"}}

then CLEARS the queue (including any dropped-as-stale entries — undelivered
news is discarded, not left to linger). An EMPTY queue emits NOTHING (exit 0)
so it adds no per-tool-call noise. The drain is resilient — it must never crash
a tool call, so any internal error → exit 0 with no output.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

# Joiner between multiple queued docs in a single drain.
_DOC_SEPARATOR = "\n\n---\n\n"

# Zero-pad time_ns so filenames are lexically sortable in enqueue order.
# 19 digits covers nanosecond timestamps well past the year 2200.
_NS_WIDTH = 19

# Filename suffix that marks a queue file as a notice (subject to the staleness
# guard) rather than a member doc (never age-filtered). Both share the same
# time_ns-prefixed naming scheme, so sorting the queue directory by filename
# still yields overall enqueue order across both kinds of entry.
_NOTICE_SUFFIX = ".notice.md"

# How long a notice stays worth delivering. Long enough that a notice written
# a few tool calls before the next drain still arrives; short enough that a
# notice written while no session was live does not surface days later as
# stale news.
NOTICE_MAX_AGE_SECONDS = 3600


def queue_dir_for(workspace_dir: Path) -> Path:
    """Return the inject-queue directory for a workspace (may not exist yet)."""
    return Path(workspace_dir) / ".camp" / "inject_queue"


def find_workspace_root(start: Path) -> Path:
    """Walk UP from `start` to the nearest ancestor containing a `.camp/` dir.

    The workspace root is marked by its `.camp/` directory; member worktrees are
    subdirs of it, so a walk-up from `<workspace>/<member>/...` reliably finds
    `<workspace>`. If no ancestor has a `.camp/`, return `start` unchanged (the
    caller drains it as today — a no-op-safe fallback).
    """
    start = Path(start)
    for candidate in (start, *start.parents):
        if (candidate / ".camp").is_dir():
            return candidate
    return start


def enqueue_doc(workspace_dir: Path, doc: str) -> Path:
    """Write `doc` to a fresh unique file in the workspace inject queue.

    One file per enqueue so concurrent / repeated `camp activate`s before a drain
    are not lost. The filename is prefixed with a zero-padded monotonic
    nanosecond timestamp so `sorted()` yields enqueue order; the uuid suffix
    keeps filenames unique even if two enqueues land on the same `time_ns`.
    Returns the path of the written queue file.
    """
    qdir = queue_dir_for(workspace_dir)
    qdir.mkdir(parents=True, exist_ok=True)
    prefix = str(time.time_ns()).zfill(_NS_WIDTH)
    qfile = qdir / f"{prefix}-{uuid.uuid4().hex}.md"
    qfile.write_text(doc, encoding="utf-8")
    return qfile


def build_notice_body(*, member: str, phase: str, task: str | None, consequence: str) -> str:
    """Assemble a camp-authored notice body from templated fields only.

    Every argument is a plain string camp itself controls the shape of; no
    task-supplied stdout/stderr is ever passed in or read here. This is what
    makes an injection-shaped notice body impossible by construction — the
    function has no capability to include anything beyond what its own
    signature accepts. `consequence` is a canned statement of what the
    settlement means for the operator; where they need the underlying task
    output, `consequence` cites where to read it (`camp status`, the
    provisioner logfile) rather than inlining it.
    """
    lines = [f"# camp: {phase}-phase work for `{member}`", ""]
    if task:
        lines.append(f"Task: {task}")
    lines.append(consequence)
    return "\n".join(lines) + "\n"


def enqueue_notice(workspace_dir: Path, body: str) -> Path:
    """Write a notice `body` to a fresh unique file in the workspace inject queue.

    Distinguished from a member doc (`enqueue_doc`) by the `.notice.md`
    filename suffix, so `drain_queue` can apply the staleness guard to
    notices only. Shares the same time_ns-prefixed naming scheme as
    `enqueue_doc`, so enqueue order across both writers is preserved when the
    queue directory is sorted by filename.
    """
    qdir = queue_dir_for(workspace_dir)
    qdir.mkdir(parents=True, exist_ok=True)
    prefix = str(time.time_ns()).zfill(_NS_WIDTH)
    qfile = qdir / f"{prefix}-{uuid.uuid4().hex}{_NOTICE_SUFFIX}"
    qfile.write_text(body, encoding="utf-8")
    return qfile


def _is_stale_notice(path: Path, *, now: float) -> bool:
    """True if `path` is a notice file older than NOTICE_MAX_AGE_SECONDS.

    A member doc (no `_NOTICE_SUFFIX`) is never considered stale — the
    staleness guard applies to notices only.
    """
    if not path.name.endswith(_NOTICE_SUFFIX):
        return False
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    return (now - mtime) > NOTICE_MAX_AGE_SECONDS


def drain_queue(workspace_dir: Path) -> int:
    """Drain the inject queue → emit the PostToolUse JSON contract, then clear.

    Reads every queued entry in enqueue order (filenames are time-prefixed so
    `sorted()` yields the order they were enqueued), drops any notice older
    than NOTICE_MAX_AGE_SECONDS (a member doc is never dropped for age), emits
    the Claude Code additionalContext JSON to stdout for what remains, and
    deletes every queue file — stale or not, the queue is always cleared. An
    empty queue, or a queue whose only entries were stale notices, emits
    nothing. Resilient: any internal error → exit 0, no output (never crash a
    tool call). Returns the exit code (always 0).
    """
    try:
        qdir = queue_dir_for(workspace_dir)
        if not qdir.is_dir():
            return 0

        files = sorted(p for p in qdir.iterdir() if p.is_file())
        if not files:
            return 0

        now = time.time()
        deliverable = [f for f in files if not _is_stale_notice(f, now=now)]

        if deliverable:
            docs = [f.read_text(encoding="utf-8") for f in deliverable]
            combined = _DOC_SEPARATOR.join(docs)

            payload = {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": combined,
                }
            }
            sys.stdout.write(json.dumps(payload))

        for f in files:
            try:
                f.unlink()
            except OSError:
                pass
        return 0
    except Exception:
        # Never crash a tool call.
        return 0
