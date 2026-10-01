"""Pinned schema for `trailhead update --check --json` (schema v5).

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
"reason": str|null, "apply_preflight": null|{...}}` — how far the configured
outpost checkout is behind its own tracked branch. It is independent of the
top-level verdict, which is the trailhead install's alone.

`apply_preflight` (top level for the install, and inside `outpost` for that
part) says whether `trailhead update` would refuse the part. It is `null`
unless the part's outcome is `behind`; otherwise `{"verdict":
"clear"|"refused"|"unknown", "refusal":
null|"no_upstream"|"local_changes"|"diverged"}` with `refusal` non-null
exactly when `verdict` is `refused`. A part that is `ok` or `unanswerable`
carries `null`; an `outpost` of `null` has no preflight at all.
"""

SCHEMA_VERSION = 5

_SHA = "a" * 40

_NO_DELTA = {"available": False, "lines": [], "truncated": False}
_EMPTY_DELTA = {"available": True, "lines": [], "truncated": False}

CLEAR_PREFLIGHT = {"verdict": "clear", "refusal": None}
LOCAL_CHANGES_PREFLIGHT = {"verdict": "refused", "refusal": "local_changes"}
DIVERGED_PREFLIGHT = {"verdict": "refused", "refusal": "diverged"}
NO_UPSTREAM_PREFLIGHT = {"verdict": "refused", "refusal": "no_upstream"}
UNKNOWN_PREFLIGHT = {"verdict": "unknown", "refusal": None}

BEHIND_EXAMPLE = {
    "schema_version": SCHEMA_VERSION,
    "outcome": "behind",
    "commits_behind": 3,
    "install_commits_behind": 0,
    "installed_sha": _SHA,
    "reason": None,
    "changelog_delta": _EMPTY_DELTA,
    "apply_preflight": CLEAR_PREFLIGHT,
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
    "apply_preflight": None,
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
    "apply_preflight": None,
    "outpost": None,
}

BEHIND_REFUSED_EXAMPLE = {**BEHIND_EXAMPLE, "apply_preflight": LOCAL_CHANGES_PREFLIGHT}

OUTPOST_BEHIND_EXAMPLE = {
    **OK_EXAMPLE,
    "outpost": {
        "outcome": "behind",
        "commits_behind": 2,
        "reason": None,
        "apply_preflight": CLEAR_PREFLIGHT,
    },
}

OUTPOST_BEHIND_REFUSED_EXAMPLE = {
    **OK_EXAMPLE,
    "outpost": {
        "outcome": "behind",
        "commits_behind": 2,
        "reason": None,
        "apply_preflight": DIVERGED_PREFLIGHT,
    },
}

OUTPOST_OK_EXAMPLE = {
    **OK_EXAMPLE,
    "outpost": {
        "outcome": "ok",
        "commits_behind": 0,
        "reason": None,
        "apply_preflight": None,
    },
}
