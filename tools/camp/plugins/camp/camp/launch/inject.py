"""Mid-session context-injection queue + drain (claude-hook strategy).

The claude-hook inject strategy decouples writers that know something a live
session should learn (`camp activate`, and the activate-phase provisioner) from
the Claude Code PostToolUse hook (which fires on the next tool call). Writers
ENQUEUE to a queue ROOT directory — `enqueue_doc`/`enqueue_notice`/`drain_queue`
all take that root as their argument and read/write `<root>/.camp/inject_queue/`
beneath it. Production callers pass `central_queue_dir(group, slug)` as that
root: `central_state_dir(group)/inject_queues/<slug>/` — under camp's central
state dir, NOT inside the workspace dir a member's worktree is a sibling of.

That placement is deliberate: a task step's subprocess runs with cwd = the
member worktree and full filesystem access as the same OS user (see
provision/tasks.py). Putting the queue at `<workspace>/.camp/inject_queue` (a
one-hop-up sibling of every member worktree) made it a predictable write
target for any process running inside a member's checkout — including a
malicious transitive dependency's postinstall script — with no provenance
check standing between "a file showed up in the queue" and "it gets
concatenated into a live agent's context". Rooting the queue under the central
state dir instead removes that write path: nothing a task step naturally
produces or is told about points there. `central_queue_dir` creates the
directory with owner-only (0700) permissions.

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
advice to avoid Bash still gets its notices) locates the queue for the current
session — via `--workspace <root>` when given explicitly, else by mapping the
session's cwd to (group, slug) with `resolve_group_slug_from_cwd` and calling
`central_queue_dir` — reads it, drops any notice older than
`NOTICE_MAX_AGE_SECONDS`, emits the Claude Code additionalContext JSON contract:

    {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                            "additionalContext": "<queued doc(s)>"}}

then CLEARS the queue (including any dropped-as-stale entries — undelivered
news is discarded, not left to linger). An EMPTY queue emits NOTHING (exit 0)
so it adds no per-tool-call noise. The drain is resilient — it must never crash
a tool call, so any internal error → exit 0 with no output.

Pre-existing workspaces may still have a queue at the old, now-abandoned
`<workspace>/.camp/inject_queue` location. It is never read again — not even
once, to "drain it out" — because a file sitting there is exactly what the
attack this module now closes would have planted: reading it, even one last
time, would still be trusting file presence over provenance. Any such leftover
file simply sits inert until the workspace itself is torn down.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

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

# Segment central_queue_dir nests under central_state_dir(group), sibling of
# "worktrees" — outside the directory tree any member worktree lives under.
_INJECT_QUEUES_SEGMENT = "inject_queues"

# central_queue_dir creates its directory owner-only: same-OS-user task steps
# aren't stopped by this alone (see module docstring — the real defense is
# relocation, not permissions), but it costs nothing and matches the "removes
# the write capability" intent for any reader outside the owning user.
_OWNER_ONLY_MODE = 0o700

_WORKTREES_SEGMENT = "worktrees"


def queue_dir_for(workspace_dir: Path) -> Path:
    """Return the inject-queue directory beneath the given queue root (may not
    exist yet). The root is opaque to this function — production code passes
    `central_queue_dir(group, slug)`; tests may pass any directory."""
    return Path(workspace_dir) / ".camp" / "inject_queue"


def central_queue_dir(
    group_name: str,
    slug: str,
    *,
    env: dict[str, str] | None = None,
    platform: str | None = None,
) -> Path:
    """Return (creating if absent) the inject-queue root for (group_name, slug).

    `central_state_dir(group_name)/inject_queues/<slug>/` — sibling of
    `worktrees/`, so NOT inside the workspace dir any member worktree lives
    under. Created with owner-only (0700) permissions. See the module
    docstring for why this placement is the fix, not merely a reorganization.
    """
    from ..group.resolve import central_state_dir

    kwargs: dict[str, Any] = {}
    if env is not None:
        kwargs["env"] = env
    if platform is not None:
        kwargs["platform"] = platform
    root = central_state_dir(group_name, **kwargs) / _INJECT_QUEUES_SEGMENT / slug
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, _OWNER_ONLY_MODE)
    return root


def resolve_group_slug_from_cwd(cwd: Path, camp_state_dir: Path) -> tuple[str, str] | None:
    """Map `cwd` to (group, slug) by pure state-dir path arithmetic.

    Mirrors step 1 of `camp.group.resolve.resolve_from_cwd` — cwd relative to
    camp_state_dir in the shape `<group>/worktrees/<slug>/...` — but skips
    loading group configs to confirm the group is actually configured: this
    runs on the hidden per-tool-call drain path and only needs to locate a
    queue directory, not validate the group. Handles cwd at any depth under
    the member worktree in one relative_to() call, no upward walk needed.

    Returns None when cwd isn't under camp_state_dir in that shape (e.g. a
    canonical member repo outside the unified workspace layout, or no camp
    state dir at all) — the caller treats that as nothing to drain.
    """
    try:
        rel_parts = Path(cwd).resolve().relative_to(Path(camp_state_dir).resolve()).parts
    except ValueError:
        return None
    if len(rel_parts) >= 3 and rel_parts[1] == _WORKTREES_SEGMENT:
        return rel_parts[0], rel_parts[2]
    return None


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
