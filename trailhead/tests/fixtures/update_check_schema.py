"""Pinned schema for `trailhead update --check --json` (schema v1).

This is the producer contract the SessionStart hook delivery slice consumes:
its tests import these examples rather than re-deriving the shape. Each
example is the exact dict `trailhead.update.check_for_update` returns for a
canonical scenario producing that outcome — tests compare real output against
these fixtures, never restate the shape inline.
"""

SCHEMA_VERSION = 1

_SHA = "a" * 40

BEHIND_EXAMPLE = {
    "schema_version": SCHEMA_VERSION,
    "outcome": "behind",
    "commits_behind": 3,
    "installed_sha": _SHA,
    "reason": None,
}

OK_EXAMPLE = {
    "schema_version": SCHEMA_VERSION,
    "outcome": "ok",
    "commits_behind": 0,
    "installed_sha": _SHA,
    "reason": None,
}

UNANSWERABLE_NO_STAMP_EXAMPLE = {
    "schema_version": SCHEMA_VERSION,
    "outcome": "unanswerable",
    "commits_behind": None,
    "installed_sha": None,
    "reason": "no install provenance stamp found",
}
