"""Pinned schema for `trailhead update --check --json` (schema v4).

This is the producer contract the SessionStart hook delivery slice consumes:
its tests import these examples rather than re-deriving the shape. Each
example is the exact dict `trailhead.update.check_for_update` returns for a
canonical scenario producing that outcome — tests compare real output against
these fixtures, never restate the shape inline.

`commits_behind` counts how far the checkout is behind its tracked remote;
`install_commits_behind` counts how far the wired install is behind the
checkout. Either being nonzero makes the outcome `behind`.

`changelog_delta` is `{"available": bool, "lines": list[str], "truncated":
bool}`. `available` is false whenever the delta could
not be computed (no stamp, no resolvable remote, an errored diff invocation)
— the verdict fields (`outcome`, `commits_behind`) stay independently correct
even then, so a caller never sees a partial delta mistaken for a complete one.

`outpost` is `null` when no outpost checkout is configured, else
`{"outcome": "ok"|"behind"|"unanswerable", "commits_behind": int|null,
"reason": str|null}` — how far the configured outpost checkout is behind its
own tracked branch. It is independent of the top-level verdict, which is the
trailhead install's alone.
"""

SCHEMA_VERSION = 4

_SHA = "a" * 40

_NO_DELTA = {"available": False, "lines": [], "truncated": False}
_EMPTY_DELTA = {"available": True, "lines": [], "truncated": False}

BEHIND_EXAMPLE = {
    "schema_version": SCHEMA_VERSION,
    "outcome": "behind",
    "commits_behind": 3,
    "install_commits_behind": 0,
    "installed_sha": _SHA,
    "reason": None,
    "changelog_delta": _EMPTY_DELTA,
    "outpost": None,
}

OK_EXAMPLE = {
    "schema_version": SCHEMA_VERSION,
    "outcome": "ok",
    "commits_behind": 0,
    "install_commits_behind": 0,
    "installed_sha": _SHA,
    "reason": None,
    "changelog_delta": _EMPTY_DELTA,
    "outpost": None,
}

UNANSWERABLE_NO_STAMP_EXAMPLE = {
    "schema_version": SCHEMA_VERSION,
    "outcome": "unanswerable",
    "commits_behind": None,
    "install_commits_behind": None,
    "installed_sha": None,
    "reason": "no install provenance stamp found",
    "changelog_delta": _NO_DELTA,
    "outpost": None,
}

OUTPOST_BEHIND_EXAMPLE = {
    **OK_EXAMPLE,
    "outpost": {"outcome": "behind", "commits_behind": 2, "reason": None},
}
