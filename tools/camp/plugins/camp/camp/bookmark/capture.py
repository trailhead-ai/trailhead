"""``camp bookmark`` — record the CURRENT harness session under a short ref.

The command answers one question later: "which session was I in when I was working
on this workspace, and where is its transcript?". It writes exactly one record per
workspace into the global store (see :mod:`camp.bookmark.store`).

Preconditions, each failing on its own terms so a user knows which one to fix:

1. **cwd is inside a camp workspace** — the workspace identifies WHAT is being
   bookmarked, and its root is also the key the harness needs to find the
   transcript. It is resolved from cwd rather than a ``--name`` flag on purpose:
   the session being captured is the one running in this directory.
2. **a session id is exported** — camp does not launch the harness and cannot
   invent an id; the harness publishes it in the environment.
3. **the transcript resolves** — the harness seam either hands back an existing
   file or ``None``. A bookmark pointing at a transcript that is not there is
   worse than a refusal, so ``None`` refuses.

Ref rules: the default ref is the workspace slug; refs match
``^[a-z0-9][a-z0-9._-]{0,63}$`` (a ref is typed by hand and appears in shell
commands, so it stays lowercase, short, and free of shell metacharacters). A ref
already held by a DIFFERENT workspace is refused rather than stolen. Re-capturing
the same workspace updates its record in place — a new session id and transcript,
the note replaced, ``created_at`` preserved.
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

from . import store

#: Refs are typed by hand and pasted into shell commands: lowercase, bounded, and
#: free of anything a shell or a path would interpret.
_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_REF_BODY_RE = re.compile(r"^[a-z0-9._-]*$")
_REF_MAX_LEN = 64

#: Where the harness publishes the id of the running session. camp resolves it
#: generically here (rather than in a harness module) because it is read from the
#: environment camp itself is running in — there is no harness object to ask.
_SESSION_ID_ENV_VARS = ("CLAUDE_CODE_SESSION_ID",)

_USAGE = "usage: camp bookmark [--ref <ref>] [--note <text>]"


class BookmarkError(Exception):
    """A refused capture. The message is the user-facing line, already prefixed."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_workspace(group: dict, env: dict[str, str] | None) -> tuple[str, Path]:
    """Return (slug, workspace_root) for the cwd, or refuse."""
    from ..group.manifest import workspace_dir
    from ..group.resolve import resolve_from_cwd, GroupResolutionError

    group_name = group["group"]["name"]
    try:
        _, slug = resolve_from_cwd(Path.cwd(), [group], env=env)
    except GroupResolutionError:
        slug = None
    if not slug:
        raise BookmarkError(
            "camp bookmark: this is not a camp workspace — "
            "run it from inside the workspace whose session you want to bookmark"
        )
    return slug, workspace_dir(group_name, slug, env=env)


def _resolve_session_id(env: dict[str, str] | None) -> str:
    """Return the running session's id, or refuse naming the variable to export."""
    import os

    source = env if env is not None else os.environ
    for name in _SESSION_ID_ENV_VARS:
        value = source.get(name)
        if value:
            return value
    raise BookmarkError(
        f"camp bookmark: {_SESSION_ID_ENV_VARS[0]} is not set — "
        "there is no harness session to bookmark from this shell"
    )


def _resolve_transcript(
    group: dict, session_id: str, workspace: Path, env: dict[str, str] | None
) -> Path:
    """Ask the harness seam where the transcript is, or refuse.

    A harness with no transcript support and a harness whose transcript is simply
    gone both answer ``None``; the user-facing message is the same either way,
    since neither can be bookmarked.
    """
    from trailhead.harness import get_harness, HarnessError

    from ..launch.profile import resolve_harness_profile

    binary = Path(resolve_harness_profile(group).binary).name
    try:
        harness = get_harness(binary)
    except HarnessError:
        # An unrecognized harness has no transcript layout camp can ask about —
        # the same "unresolvable" outcome as a harness that answers None.
        resolved = None
    else:
        resolved = harness.session_transcript_path(session_id, workspace, env=env)
    if resolved is None:
        raise BookmarkError(
            f"camp bookmark: the transcript for session {session_id} cannot be resolved "
            f"for workspace {workspace} — nothing to bookmark"
        )
    return Path(resolved).resolve()


def _validate_ref(ref: str) -> None:
    """Raise BookmarkError naming the first thing wrong with *ref*."""
    if _REF_RE.match(ref):
        return
    if not ref:
        raise BookmarkError(f"camp bookmark: a ref may not be empty\n  {_USAGE}")
    for ch in ref:
        if not _REF_BODY_RE.match(ch):
            raise BookmarkError(
                f"camp bookmark: invalid ref {ref!r} — the character {ch!r} is not allowed; "
                "a ref uses lowercase letters, digits, '.', '_' and '-'"
            )
    if len(ref) > _REF_MAX_LEN:
        raise BookmarkError(
            f"camp bookmark: invalid ref {ref!r} — a ref is at most {_REF_MAX_LEN} characters"
        )
    raise BookmarkError(
        f"camp bookmark: invalid ref {ref!r} — the character {ref[0]!r} may not start a ref; "
        "a ref starts with a lowercase letter or a digit"
    )


def capture(
    *,
    group: dict,
    ref: str | None,
    note: str | None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Capture the current session as a bookmark and return the stored record.

    ``ref`` of ``None`` means "default to the workspace slug" — which also selects
    the friendlier collision message, since the user did not choose the colliding
    name themselves.
    """
    group_name = group["group"]["name"]
    slug, workspace = _resolve_workspace(group, env)
    session_id = _resolve_session_id(env)

    defaulted = ref is None
    ref = slug if defaulted else ref
    _validate_ref(ref)

    # Resolve the transcript BEFORE opening the transaction: it touches the
    # filesystem and can refuse, and a refusal must not hold the store lock.
    transcript = _resolve_transcript(group, session_id, workspace, env)

    now = _now()
    with store.transaction(env=env) as bookmarks:
        held = bookmarks.get(ref)
        if held is not None and (held.get("group"), held.get("slug")) != (group_name, slug):
            hint = (
                "\n  pass --ref <ref> to bookmark this workspace under another name"
                if defaulted
                else ""
            )
            raise BookmarkError(
                f"camp bookmark: ref {ref!r} already points at workspace "
                f"{held.get('group')}/{held.get('slug')}{hint}"
            )

        # One workspace holds at most one bookmark: capturing under a new ref
        # RENAMES the existing record rather than forking a second pointer at the
        # same workspace.
        previous = held
        prior_ref = store.ref_for_workspace(bookmarks, group_name, slug)
        if prior_ref is not None and prior_ref != ref:
            previous = bookmarks.pop(prior_ref)

        record = {
            "ref": ref,
            "group": group_name,
            "slug": slug,
            "session_id": session_id,
            "transcript_path": str(transcript),
            "note": note or "",
            "created_at": (previous or {}).get("created_at") or now,
            "updated_at": now,
        }
        bookmarks[ref] = record
    return record


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _consume_value(args: list[str], flag: str) -> str | None:
    """Consume ``<flag> <value>`` from *args* in place; refuse a value-less flag."""
    if flag not in args:
        return None
    i = args.index(flag)
    if i + 1 >= len(args):
        raise BookmarkError(f"camp bookmark: {flag} requires a value\n  {_USAGE}")
    value = args[i + 1]
    del args[i : i + 2]
    return value


def cmd_bookmark(args: list[str], group: dict, env: dict[str, str] | None) -> None:
    """``camp bookmark [--ref <ref>] [--note <text>]`` — capture the current session.

    Every refusal exits non-zero with a single ``camp bookmark: …`` line on stderr;
    a corrupt store surfaces its own named error rather than a traceback.
    """
    rest = list(args)
    try:
        ref = _consume_value(rest, "--ref")
        note = _consume_value(rest, "--note")
        if rest:
            raise BookmarkError(
                f"camp bookmark: unexpected argument {rest[0]!r}\n  {_USAGE}"
            )
        record = capture(group=group, ref=ref, note=note, env=env)
    except (BookmarkError, store.BookmarkStoreError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(f"camp: bookmarked {record['group']}/{record['slug']} as {record['ref']}")
