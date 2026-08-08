"""The `camp rm` bookmark delete guard.

A bookmark points at a harness session that was started from one workspace, and
that session can only be resumed from the workspace it started in. Tearing the
workspace down therefore orphans the bookmark — so `camp rm` refuses, names what
would be lost, and asks for `--force` (the same posture as the dirty-worktree
block in ``provision/reconcile.py``).

Ordering is the whole contract:

- **Reject before teardown.** The check runs in the pre-teardown slot of
  ``camp rm``, so a refused removal has torn down nothing.
- **Clean up only after teardown.** With ``--force``, the workspace's bookmark
  entries are dropped only once teardown reported success. A teardown that
  failed partway leaves the entries in the store, where `camp bookmark ls`
  renders them as ``workspace gone`` — visible and hand-removable, rather than
  silently deleted alongside a workspace that is still half-present.
- **Never block a re-attempt.** A bookmark whose workspace directory is already
  gone blocks nothing. Otherwise the entry left behind by an interrupted
  teardown would wedge the very command that finishes the job.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from . import store
from .render import format_age


def blocking_bookmarks(
    group: str,
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return the bookmarks that should block removal of workspace (*group*, *slug*).

    Empty when the workspace directory no longer exists — there is nothing left
    to orphan, and a stranded entry must not wedge a re-attempted removal.
    """
    from ..group.manifest import workspace_dir

    if not workspace_dir(group, slug, env=env).exists():
        return []
    return [
        record
        for record in store.list_bookmarks(env=env)
        if record.get("group") == group and record.get("slug") == slug
    ]


def render_block(
    slug: str,
    records: list[dict[str, Any]],
    *,
    now: dt.datetime | None = None,
) -> str:
    """Render the refusal message naming every bookmark that would be orphaned."""
    now = now or dt.datetime.now(dt.timezone.utc)
    count = len(records)
    noun = "bookmark" if count == 1 else "bookmarks"
    subject = "the saved session" if count == 1 else "the saved sessions"
    lines = [
        f"camp remove: workspace {slug!r} still has {count} {noun}; "
        f"removing it would orphan {subject}:"
    ]
    for record in records:
        age = format_age(record.get("updated_at", ""), now=now)
        note = record.get("note") or "(no note)"
        lines.append(f"  {record['ref']}  {age}  {note}")
    lines.append(
        "  run `camp bookmark rm <ref>` to drop a bookmark first, "
        "or re-run with --force to remove both"
    )
    return "\n".join(lines)


def clear_workspace_bookmarks(
    group: str,
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> list[str]:
    """Delete every bookmark pointing at (*group*, *slug*); return the removed refs.

    Call ONLY after teardown succeeded — see the module contract.
    """
    removed: list[str] = []
    with store.transaction(env=env) as bookmarks:
        for ref in sorted(bookmarks):
            record = bookmarks[ref]
            if record.get("group") == group and record.get("slug") == slug:
                removed.append(ref)
        for ref in removed:
            del bookmarks[ref]
    return removed
