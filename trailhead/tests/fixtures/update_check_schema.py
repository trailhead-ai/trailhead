"""Pinned schema for `trailhead update --check --json` (schema v2).

This is the producer contract the SessionStart hook delivery slice consumes:
its tests import these examples rather than re-deriving the shape. Each
example is the exact dict `trailhead.update.check_for_update` returns for a
canonical scenario producing that outcome — tests compare real output against
these fixtures, never restate the shape inline.

`changelog_delta` was added in schema v2: `{"available": bool, "lines":
list[str], "truncated": bool}`. `available` is false whenever the delta could
not be computed (no stamp, no resolvable remote, an errored diff invocation)
— the verdict fields (`outcome`, `commits_behind`) stay independently correct
even then, so a caller never sees a partial delta mistaken for a complete one.
"""

SCHEMA_VERSION = 2

_SHA = "a" * 40

_NO_DELTA = {"available": False, "lines": [], "truncated": False}
_EMPTY_DELTA = {"available": True, "lines": [], "truncated": False}

BEHIND_EXAMPLE = {
    "schema_version": SCHEMA_VERSION,
    "outcome": "behind",
    "commits_behind": 3,
    "installed_sha": _SHA,
    "reason": None,
    "changelog_delta": _EMPTY_DELTA,
}

OK_EXAMPLE = {
    "schema_version": SCHEMA_VERSION,
    "outcome": "ok",
    "commits_behind": 0,
    "installed_sha": _SHA,
    "reason": None,
    "changelog_delta": _EMPTY_DELTA,
}

UNANSWERABLE_NO_STAMP_EXAMPLE = {
    "schema_version": SCHEMA_VERSION,
    "outcome": "unanswerable",
    "commits_behind": None,
    "installed_sha": None,
    "reason": "no install provenance stamp found",
    "changelog_delta": _NO_DELTA,
}
