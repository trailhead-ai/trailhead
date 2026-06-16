"""Mid-session context-injection queue + drain — Slice 9 (claude-hook strategy).

The claude-hook inject strategy decouples `camp enter` (which knows the member
and where its doc lives) from the Claude Code PostToolUse hook (which fires on the
next tool call). `camp enter` ENQUEUES the member doc to:

    <workspace>/.camp/inject_queue/<unique>.md

One file per enqueue, so multiple `camp enter`s before a drain are never lost.

The hidden `camp inject --drain` (wired as the PostToolUse hook, matcher Bash)
reads the queue, emits the Claude Code additionalContext JSON contract:

    {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                            "additionalContext": "<queued doc(s)>"}}

then CLEARS the queue. An EMPTY queue emits NOTHING (exit 0) so it adds no
per-tool-call noise. The drain is resilient — it must never crash a tool call, so
any internal error → exit 0 with no output.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

# Joiner between multiple queued docs in a single drain.
_DOC_SEPARATOR = "\n\n---\n\n"


def queue_dir_for(workspace_dir: Path) -> Path:
    """Return the inject-queue directory for a workspace (may not exist yet)."""
    return Path(workspace_dir) / ".camp" / "inject_queue"


def enqueue_doc(workspace_dir: Path, doc: str) -> Path:
    """Write `doc` to a fresh unique file in the workspace inject queue.

    One file per enqueue so concurrent / repeated `camp enter`s before a drain
    are not lost. Returns the path of the written queue file.
    """
    qdir = queue_dir_for(workspace_dir)
    qdir.mkdir(parents=True, exist_ok=True)
    qfile = qdir / f"{uuid.uuid4().hex}.md"
    qfile.write_text(doc)
    return qfile


def drain_queue(workspace_dir: Path) -> int:
    """Drain the inject queue → emit the PostToolUse JSON contract, then clear.

    Reads every queued doc (sorted for stable ordering), emits the Claude Code
    additionalContext JSON to stdout, and deletes the queue files. An empty queue
    emits nothing. Resilient: any internal error → exit 0, no output (never crash
    a tool call). Returns the exit code (always 0).
    """
    try:
        qdir = queue_dir_for(workspace_dir)
        if not qdir.is_dir():
            return 0

        files = sorted(p for p in qdir.iterdir() if p.is_file())
        if not files:
            return 0

        docs = [f.read_text() for f in files]
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
