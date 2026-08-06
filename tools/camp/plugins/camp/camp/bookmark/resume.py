"""``camp resume <ref>`` — hand the shell what it needs to re-enter a session.

camp does not start, stop, or replace processes. It answers a question and stops:
*where would I have to be, and what would I have to run, to get back into the
session this bookmark names?* The shell integration wrapper reads that answer and
does the two things camp will not: change directory, and exec.

The answer is a two-line machine contract on stdout::

    <absolute workspace root>          # line 1 — where to cd
    <shell-quoted command>             # line 2 — what to run there

Line 1 is a bare, unquoted path: the wrapper reads it as a whole line and uses it
directly, so quoting it would corrupt it. The one character that contract cannot
express is a newline, which is refused rather than emitted (a truncated cd target
is worse than a refusal).

Line 2 is quoted with POSIX shell rules and is authoritative in BOTH wrapper
dialects — a non-POSIX shell must hand it to ``sh``, never evaluate it natively,
or its own metacharacters would be re-interpreted inside quoting that never
anticipated them.

Why cd at all: the harness indexes sessions by the directory they started in, so
running the command from anywhere else finds no session. The workspace root is
that directory, which is exactly why it is line 1 rather than a comment.

The command on line 2 comes from the harness seam, whole. camp never assembles,
edits, or re-quotes it — it only quotes the tokens it was handed for transport
across a line of text. A harness that answers ``None`` (no resume concept, or an
id it will not accept) makes the bookmark unresumable, and camp says so.

Every failure is reported on stderr with a non-zero exit BEFORE any machine line
reaches stdout, so a wrapper reading stdout can never act on a partial answer:

1. **shell integration inactive** — without the wrapper the two lines are inert
   text, and printing them would look like success while nothing happened.
2. **unknown ref** — named, with a pointer at the listing.
3. **workspace gone** — the directory to cd into no longer exists.
4. **transcript gone** — retention cleanup reached it; the bookmark is now a
   pointer to nothing and the fix is to drop it.
5. **harness cannot resume** — the seam declined.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from . import store

_USAGE = "usage: camp resume <ref>"

#: Exported by the shell integration around an intercepted invocation. Its
#: absence means the wrapper is not there to consume what this command prints.
_SHELL_INTEGRATION_ENV_VAR = "CAMP_SHELL_INTEGRATION"

_UNSUPPORTED = "camp: resume unsupported for this harness"


class ResumeError(Exception):
    """A refused resume. The message is the user-facing text, already prefixed."""


def _require_shell_integration(env: dict[str, str] | None) -> None:
    source = env if env is not None else os.environ
    if not source.get(_SHELL_INTEGRATION_ENV_VAR):
        raise ResumeError(
            "camp resume: the camp shell integration is not active in this shell — "
            'run \'eval "$(trailhead shellenv)"\' and retry\n'
            "  without it camp can print where to go, but nothing can take you there"
        )


def _load_bookmark(ref: str, env: dict[str, str] | None) -> dict:
    record = store.get_by_ref(ref, env=env)
    if record is None:
        raise ResumeError(
            f"camp resume: no bookmark named {ref!r}\n"
            "  run 'camp bookmark ls' to see what is bookmarked"
        )
    return record


def _resolve_workspace(record: dict, env: dict[str, str] | None) -> Path:
    """Return the absolute directory to cd into, or refuse.

    The workspace is DERIVED from the record's (group, slug) rather than stored:
    it is the same pure resolution capture used, and re-deriving it means a
    relocated state dir resolves correctly instead of resuming a stale path.
    """
    from ..group.manifest import workspace_dir

    ref = record["ref"]
    workspace = workspace_dir(record["group"], record["slug"], env=env)
    if not workspace.is_dir():
        raise ResumeError(
            f"camp resume: the workspace for bookmark {ref!r} is gone "
            f"({workspace})\n"
            f"  run 'camp bookmark rm {ref}' to drop the stale bookmark"
        )
    workspace = workspace.resolve()
    if "\n" in str(workspace):
        raise ResumeError(
            f"camp resume: the workspace path for bookmark {ref!r} contains a "
            "newline and cannot be handed to the shell integration"
        )
    return workspace


def _require_transcript(record: dict) -> None:
    """Refuse a bookmark whose transcript the harness has since cleaned up.

    camp only ever CHECKS the stored path — it never re-derives or repairs one.
    Deriving a transcript location is the harness's knowledge, and it already
    answered that question once, at capture time.
    """
    ref = record["ref"]
    transcript = Path(record["transcript_path"])
    if not transcript.exists():
        raise ResumeError(
            f"camp resume: the session transcript for bookmark {ref!r} is gone "
            f"({transcript}) — the harness's retention cleanup has likely reached it\n"
            f"  run 'camp bookmark rm {ref}' to drop the stale bookmark"
        )


def _resolve_argv(group: dict, session_id: str) -> list[str]:
    """Ask the harness seam for the command, or refuse.

    An unrecognized harness and a recognized harness that declines are the same
    outcome for a user — neither yields a command — so both raise the one message.
    """
    from . import harness_for

    harness = harness_for(group)
    argv = harness.session_resume(session_id) if harness else None
    if not argv:
        raise ResumeError(_UNSUPPORTED)
    return list(argv)


def resolve_resume(
    *, group: dict, ref: str, env: dict[str, str] | None = None
) -> tuple[Path, list[str]]:
    """Return (workspace_root, argv) for *ref*, or raise :class:`ResumeError`.

    The checks run in the order a user can act on them: the shell integration
    first (nothing else matters without it), then the bookmark, then the two
    things it points at, then the harness.
    """
    _require_shell_integration(env)
    record = _load_bookmark(ref, env)
    workspace = _resolve_workspace(record, env)
    _require_transcript(record)
    argv = _resolve_argv(group, record["session_id"])
    return workspace, argv


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_resume(args: list[str], group: dict, env: dict[str, str] | None) -> None:
    """``camp resume <ref>`` — print the two-line machine contract for the wrapper.

    Nothing reaches stdout until every check has passed, so a failure is always a
    clean stderr line and an empty stdout.
    """
    from ..spine import _consume_flag_value

    rest = list(args)
    _consume_flag_value(rest, "--group")  # already resolved upstream; drop it
    try:
        if not rest:
            raise ResumeError(f"camp resume: a ref is required\n  {_USAGE}")
        ref, rest = rest[0], rest[1:]
        if rest:
            raise ResumeError(
                f"camp resume: unexpected argument {rest[0]!r}\n  {_USAGE}"
            )
        workspace, argv = resolve_resume(group=group, ref=ref, env=env)
    except (ResumeError, store.BookmarkStoreError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(str(workspace))
    print(shlex.join(argv))
