"""Group-aware lifecycle commands for camp.

These functions replace the SIBLING_REPOS-constant-based implementations in
spine.py with group-config-driven equivalents. They operate on the central
manifest and the group member list.

cmd_status_group(group, slug, env):
    Fleet view (slug=None) or scoped (slug=<name>) status across group members.

cmd_ls_group(group, env):
    List all worktrees for the group (reads the central state dir).

cmd_sync_group(group, env):
    Sync canonical member repos to latest (fetch + ff-only).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..gitutil import _git_branch_drift, _git_is_dirty, _git_out, _git_repo_status
from ..group.resolve import central_state_dir
from ..group.manifest import (
    ManifestError,
    manifest_path_for,
    read_central_manifest,
    reconcile_lock,
)
from ..launch.inventory import (
    DisclosureScope,
    STATE_NONE,
    STATE_UNMANAGED,
    Workspace,
    classify_sessions,
    format_path,
    format_state,
    format_unmanaged_summary,
)
from ..launch.naming import workspace_session_name
from ..launch.tmux import Tmux, _Unanswered


def _list_group_worktrees(
    group: dict[str, Any],
    env: dict[str, str] | None = None,
) -> list[tuple[str, Path]]:
    """Return list of (slug, manifest_path) for all created worktrees in the group."""
    group_name = group["group"]["name"]
    state_dir = central_state_dir(group_name, env=env)
    worktrees_dir = state_dir / "worktrees"

    results: list[tuple[str, Path]] = []
    if not worktrees_dir.is_dir():
        return results

    for entry in sorted(worktrees_dir.iterdir()):
        mpath = entry / "manifest.json"
        if mpath.is_file():
            results.append((entry.name, mpath))
    return results


def host_claimed_session_names(*, env: dict[str, str] | None = None) -> set[str]:
    """Every tmux session name a camp workspace ANYWHERE on this host
    derives — the claiming set
    :func:`~camp.launch.inventory.classify_sessions` tests a leftover
    candidate against.

    This is the I/O half of keeping the claiming set wider than the
    reporting set (see that function's module docstring): a group-scoped
    listing reports one group's rows but must not call a sibling group's
    live session a leftover, because the operator's remedy for a leftover
    kills it.

    Walks the camp state root — ``state_dir("camp")/<group>/worktrees/<slug>/``,
    the layout :func:`~camp.group.resolve.central_state_dir` and
    :func:`_list_group_worktrees` already define — rather than loading group
    configs, so a workspace still counts as claiming its name even when its
    group's TOML is missing or unparsable, and no TOML is parsed to answer a
    question that only needs two directory names. A non-group file at the
    root (camp keeps lockfiles there) has no ``worktrees`` directory and is
    skipped.

    Returns NAMES only, and the group-scoped caller uses them only to
    suppress a row it would otherwise have printed — never to print one —
    so reading a sibling group's directory names narrows this answer rather
    than widening its disclosure.
    """
    import trailhead.paths as _paths  # lazy: guard already ran at entry point

    kwargs: dict[str, Any] = {}
    if env is not None:
        kwargs["env"] = env
    root = _paths.state_dir("camp", **kwargs)
    if not root.is_dir():
        return set()

    names: set[str] = set()
    for group_dir in sorted(root.iterdir()):
        worktrees_dir = group_dir / "worktrees"
        if not worktrees_dir.is_dir():
            continue
        for entry in sorted(worktrees_dir.iterdir()):
            if (entry / "manifest.json").is_file():
                names.add(workspace_session_name(group_dir.name, entry.name))
    return names


# ---------------------------------------------------------------------------
# Public command functions
# ---------------------------------------------------------------------------


def cmd_status_group(
    group: dict[str, Any],
    slug: str | None = None,
    *,
    env: dict[str, str] | None = None,
    stale_days: int | None = None,
) -> dict[str, Any]:
    """Return status for one (slug) or all (slug=None) worktrees in the group.

    Fleet view (slug=None): lists all worktrees found in the central state dir.
    Scoped (slug=<name>): returns info for that worktree only.

    Returns:
        {
            "worktrees": [
                {
                    "slug": str,
                    "branch": str,
                    "manifest_path": str,
                    "members": [git-status-per-member],
                }
            ]
        }

    *stale_days*, when given, additionally annotates each worktree with
    ``idle_days`` and ``stale`` against that threshold. Opt-in because it costs
    a ``git log`` per member; its absence is what makes ``--stale`` observable
    rather than a flag that changes nothing.
    """
    if slug is not None:
        # Scoped: one worktree
        group_name = group["group"]["name"]
        from ..group.manifest import manifest_path_for

        mpath = manifest_path_for(group_name, slug, env=env)
        try:
            data = read_central_manifest(mpath)
        except ManifestError:
            raise
        entries = [(slug, mpath, data)]
    else:
        # Fleet view: all worktrees for this group
        pairs = _list_group_worktrees(group, env=env)
        entries = []
        for slug_name, mpath in pairs:
            try:
                data = read_central_manifest(mpath)
                entries.append((slug_name, mpath, data))
            except ManifestError:
                pass

    worktrees = []
    for slug_name, mpath, data in entries:
        member_statuses = []
        for m in data.get("members", []):
            wt_path = Path(m["worktree_path"])
            st = _git_repo_status(wt_path)
            member_statuses.append({"name": m["name"], **st})

        entry = {
            "slug": slug_name,
            "branch": data.get("branch", ""),
            "manifest_path": str(mpath),
            "members": member_statuses,
            "dev_env_instance": None,
            "fire_state": None,
        }
        if stale_days is not None:
            from ..spine import stale_verdict

            idle_days, stale = stale_verdict(
                [Path(m["worktree_path"]) for m in data.get("members", [])],
                threshold_days=stale_days,
            )
            entry["idle_days"] = idle_days
            entry["stale"] = stale
        worktrees.append(entry)

    return {"worktrees": worktrees}


@dataclass
class GroupListing:
    """:func:`cmd_ls_group`'s whole answer: workspace rows annotated with
    tmux session state, plus what the classifier learned about leftover
    (unmanaged) tmux sessions.

    ``entries`` mirrors the pre-existing per-workspace dict shape
    (``slug``/``branch``/``manifest_path``/``group``/``workspace_path``)
    with three additions: ``state``, ``window_count``, and ``tmux_session``
    — the workspace's own derived session name, always present regardless
    of ``state`` because deriving it needs no tmux read
    (:func:`~camp.launch.naming.workspace_session_name` is pure).

    ``unmanaged`` holds one dict per leftover session, in the same shape a
    workspace row uses (``slug: None``, ``workspace_path: None`` — the row
    owns no path, and says so the same way it says it owns no slug) so a
    caller merging it into ``entries`` for a widened answer needs no
    special case. It is populated only when *scope* was
    :data:`~camp.launch.inventory.DisclosureScope.WIDENED`; ``unmanaged_count``
    always states the true count regardless of scope, so a caller comparing
    the two scopes' counts is comparing the same fact — see
    :func:`~camp.launch.inventory.classify_sessions`.

    ``notice``, when not ``None``, is the one stderr line owed when tmux
    never answered the enumeration at all (distinct from tmux answering "no
    server running", which is silently ``STATE_NONE`` with no notice).
    """

    entries: list[dict[str, Any]]
    unmanaged: list[dict[str, Any]]
    unmanaged_count: int
    notice: str | None


#: The stderr line owed when tmux's enumeration never answered at all.
#: Carries no tmux stderr text of its own because neither seam can recover
#: it: `Tmux.list_sessions` collapses every non-"no server" non-zero exit to
#: the UNANSWERED sentinel without preserving it (see `launch/stop.py`).
_TMUX_UNANSWERED_NOTICE = "camp list: tmux did not answer — session state is unknown"


def cmd_ls_group(
    group: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    scope: DisclosureScope = DisclosureScope.GROUP,
    tmux: Any | None = None,
) -> GroupListing:
    """Return every worktree for the group, annotated with tmux session state.

    Reads tmux exactly once (via the injectable *tmux* seam, defaulting to a
    real :class:`~camp.launch.stop.Tmux`) and runs
    :func:`~camp.launch.inventory.classify_sessions` against the group's own
    workspaces, so every one of `camp list`'s four local axes gains the
    state column from this one wiring.

    Reports rows for this group's workspaces, but claims sessions against
    :func:`host_claimed_session_names` — the whole host's. tmux is
    host-wide, so without that a sibling group's live session is claimed by
    nobody here and is reported as a leftover to clean up, which the
    operator does by killing it. *scope* controls whether leftover
    (unmanaged) sessions come back as named rows
    (:data:`~camp.launch.inventory.DisclosureScope.WIDENED`) or only as a
    count (:data:`~camp.launch.inventory.DisclosureScope.GROUP`, the
    default) — see :class:`GroupListing`.
    """
    tmux = tmux if tmux is not None else Tmux()
    group_name = group["group"]["name"]

    pairs = _list_group_worktrees(group, env=env)
    entries: list[dict[str, Any]] = []
    workspaces: list[Workspace] = []
    for slug_name, mpath in pairs:
        try:
            data = read_central_manifest(mpath)
        except ManifestError:
            continue
        # mpath is <workspace_dir>/manifest.json, so its parent IS the
        # workspace dir — no need to recompute workspace_dir() (which
        # re-runs central_state_dir) per row.
        ws_path = str(mpath.parent)
        entries.append(
            {
                "slug": slug_name,
                "branch": data.get("branch", ""),
                "manifest_path": str(mpath),
                "group": data.get("group", ""),
                "workspace_path": ws_path,
                "tmux_session": workspace_session_name(group_name, slug_name),
            }
        )
        workspaces.append(Workspace(group=group_name, slug=slug_name, path=ws_path))

    listing = tmux.list_sessions()
    classification = classify_sessions(
        workspaces,
        listing,
        scope=scope,
        host_claimed_names=host_claimed_session_names(env=env),
    )

    by_slug = {row.slug: row for row in classification.workspaces}
    for e in entries:
        row = by_slug[e["slug"]]
        e["state"] = row.state
        # A running session with no windows exists in no circumstance, so 0
        # is the true count for a workspace with no session — a null there
        # would only invite a caller to special-case it. An unknown row
        # keeps its null: nothing about that workspace was observed.
        e["window_count"] = 0 if row.state == STATE_NONE else row.windows

    unmanaged = [
        {
            "slug": None,
            "branch": "",
            "manifest_path": None,
            "group": None,
            "workspace_path": None,
            "state": STATE_UNMANAGED,
            "window_count": u.windows,
            "tmux_session": u.name,
        }
        for u in classification.unmanaged
    ]

    notice = _TMUX_UNANSWERED_NOTICE if isinstance(listing, _Unanswered) else None

    return GroupListing(
        entries=entries,
        unmanaged=unmanaged,
        unmanaged_count=classification.unmanaged_count,
        notice=notice,
    )


# Fixed JSON schema for `camp list --json`, emitted identically by BOTH entry
# points so a parser never KeyErrors switching between them.
_LIST_JSON_KEYS = (
    "ok",
    "slug",
    "branch",
    "workspace_path",
    "group",
    "state",
    "window_count",
    "tmux_session",
)


def unmanaged_count_row(count: int) -> dict[str, Any]:
    """The one `--json` row a group-scoped answer carries in place of naming
    its leftover sessions: the fixed `_LIST_JSON_KEYS` schema with every
    field null, plus `unmanaged_count`.

    Built here, and read by both `camp list --json` surfaces that answer at
    :data:`~camp.launch.inventory.DisclosureScope.GROUP` — this module's
    `render_workspace_list` and `cli/workspace.py`'s `local_list_answer` —
    so the two can never drift on what a count row looks like.
    """
    return {
        **{key: None for key in _LIST_JSON_KEYS},
        "ok": True,
        "branch": "",
        "unmanaged_count": count,
    }


def render_workspace_list(
    entries: list[dict[str, Any]],
    *,
    as_json: bool,
    group_failures: list[str] | None = None,
    unmanaged_count: int = 0,
) -> None:
    """Single renderer for `camp list`/`ls` output — consulted by BOTH dispatchers
    (cli/camp's group-aware `_cmd_ls_group_cli` and spine.main's no-group `cmd_ls`)
    so the human + --json surface is identical regardless of cwd.

    Each entry must carry `slug` and `workspace_path`; `branch`, `group`,
    `state`, `window_count`, and `tmux_session` are optional (`group` is
    `None` for the standalone fallback, which has no group to derive a
    session name from — those rows render `-` for `state`). An entry whose
    `slug` is `None` is an unmanaged row: its `tmux_session` prints in the
    slug column instead, and its `workspace_path` is `None`, which the
    human rendering — and only the human rendering — states as `-`. Output:
      - human: one `slug state workspace_path` line per entry (state in the
        middle so the path stays the line's last field), with a running
        session's window count in the state field (`running:3`, via
        `format_state`); empty → no stdout. Every tmux-supplied name goes through `printable_path` so a
        stray control character cannot forge a second line.
      - --json: a list of {ok, slug, branch, workspace_path, group, state,
        window_count, tmux_session} dicts (the fixed _LIST_JSON_KEYS
        schema), every success row carrying `ok: true`; empty → `[]`.

    The renderer PROJECTS each entry onto the fixed schema (ignoring any
    source-specific extras like manifest_path), so the two data models — group
    central manifests vs. the legacy worktree registry — surface one stable shape.

    *group_failures* — the `--all-groups` caller's unparsable-config detail
    strings (already stated on stderr by
    :func:`answerable_groups_or_refuse`) — appends one ``{"ok": False,
    "group": None, "reason": detail}`` row per entry to the JSON array ONLY:
    a parser reading a complete-looking array would otherwise be silently
    missing a group. Every row in the array carries `ok`, so a consumer
    distinguishes a workspace row from a failure row by that one field
    alone, never by testing whether `row["slug"]` would raise — the same
    discriminator a failed credential store's row uses in
    `camp sessions --json`. Never rendered on the human path, which already
    has the same information on stderr; folding it into the
    `slug state workspace_path` lines would have nothing to print a slug or
    path for.

    *unmanaged_count* — the group-scoped leftover count
    (:attr:`GroupListing.unmanaged_count`), for a caller that did NOT fold
    the leftover sessions themselves into `entries` (a
    :data:`~camp.launch.inventory.DisclosureScope.GROUP` answer). When
    positive: human output gains one extra summary line
    (`format_unmanaged_summary`); --json gains one extra row carrying the
    fixed key set with every field null, plus ``unmanaged_count`` — a
    consumer tells it from a workspace row by that key's presence, never by
    a key a workspace row would have had. Zero (the default, and always the caller's choice for
    a WIDENED answer where the rows already name the leftovers) adds
    nothing.
    """
    import json as _json

    from ..launch.recovery import printable_path

    if as_json:
        rows = [
            {
                "ok": True,
                "slug": e["slug"],
                "branch": e.get("branch", ""),
                "workspace_path": e["workspace_path"],
                "group": e.get("group"),
                "state": e.get("state"),
                "window_count": e.get("window_count"),
                "tmux_session": e.get("tmux_session"),
            }
            for e in entries
        ]
        rows += [
            {"ok": False, "group": None, "reason": detail}
            for detail in (group_failures or [])
        ]
        if unmanaged_count:
            rows.append(unmanaged_count_row(unmanaged_count))
        print(_json.dumps(rows))
        return

    for e in entries:
        first = e["slug"] if e.get("slug") is not None else e.get("tmux_session")
        state = format_state(e.get("state"), e.get("window_count"))
        path = format_path(e["workspace_path"])
        print(f"{printable_path(first)} {state} {printable_path(path)}")
    if unmanaged_count:
        print(format_unmanaged_summary(unmanaged_count))


def load_answerable_groups(groups_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Load every ``*.toml`` in *groups_dir*, degrading a config that fails to
    parse instead of failing the whole load.

    Shared by the two ``--all-groups`` verbs (`camp list`, `camp sessions`) so
    a widened answer never falls back to zero rows over ONE sibling group's
    broken config — mirrors `cli/group.py`'s `_cmd_groups_cli` degrade idiom,
    generalized here since both cross-group callers need it.

    Returns ``(groups, skipped)``: `groups` is every config that parsed, in
    filename order; `skipped` is one already-formatted ``"<path>: <detail>"``
    string per config that failed to load — malformed TOML, non-UTF-8 bytes, a
    directory wearing a `.toml` name, or an unreadable file — so a caller can
    print its own ``camp <verb>: <detail> — skipping`` notice without
    reimplementing the load-failure classification.

    An empty or missing *groups_dir* returns ``([], [])`` — no groups
    configured is not a load failure; the caller states that itself.
    """
    from ..group.config import GroupConfigError, GroupConfigNotFound, load_group

    groups: list[dict[str, Any]] = []
    skipped: list[str] = []
    if not groups_dir.is_dir():
        return groups, skipped

    for toml_file in sorted(groups_dir.glob("*.toml")):
        try:
            groups.append(load_group(toml_file))
        except (
            GroupConfigError,
            GroupConfigNotFound,
            UnicodeDecodeError,
            OSError,
        ) as e:
            message = str(e).strip()
            detail = message.splitlines()[0] if message else e.__class__.__name__
            if str(toml_file) not in detail:
                detail = f"{toml_file}: {detail}"
            skipped.append(detail)
    return groups, skipped


def answerable_groups_or_refuse(
    groups_dir: Path, *, verb: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """:func:`load_answerable_groups`, plus the two notices a cross-group answer owes.

    The cross-group verbs (`camp list`, `camp sessions`) narrow their answer
    for the same reasons and must say so in the same words, so both the
    per-config skip line and the every-config-unparsable refusal are stated
    here once rather than at each verb:

    * one ``camp <verb>: <detail> — skipping`` line per config that failed to
      parse, naming it, while every group that DID parse still answers;
    * a refusal — nonzero exit, a stated reason — when configs were present
      and NONE of them parsed. Zero rows would be indistinguishable from "no
      groups are configured", which is a different and false statement, so
      this case must not answer at all.

    Returns ``(groups, skipped)`` — the groups that parsed, and the SAME
    per-config detail strings already printed to stderr above, so a `--json`
    caller can also fold each one into an in-band ``ok: false`` row instead
    of leaving it stderr-only: a parser reading a complete-looking array is
    otherwise silently missing a group, exactly the gap the `ok`
    discriminator exists to close for a failed credential store one level
    down. An EMPTY *groups* list is therefore exactly one situation:
    *groups_dir* holds no configs at all (``skipped`` is then empty too).
    That case is left to the caller to state, because the two verbs word it
    differently and reach it at different points in their own flow.
    """
    groups, skipped = load_answerable_groups(groups_dir)
    for detail in skipped:
        print(f"camp {verb}: {detail} — skipping", file=sys.stderr)
    if not groups and skipped:
        print(
            f"camp {verb}: could not answer for any configured group — "
            "every group config failed to parse; fix a config above and re-run",
            file=sys.stderr,
        )
        sys.exit(1)
    return groups, skipped


def _provision_member_and_flip(
    group: dict[str, Any],
    slug: str,
    member: dict[str, Any],
    entry: dict[str, Any],
    mpath: Path,
    *,
    env: dict[str, str] | None,
    retrying_ready: bool = False,
) -> tuple[dict[str, Any], list[Any] | None]:
    """Run one member's per-member provision under the held reconcile lock and
    flip its manifest state accordingly.

    Returns (result, task_results): `result` is the per-member entry for
    cmd_setup_group's return map ({"provision_state", "reason"?}); `task_results`
    is the list of TaskResults on the success path (so the caller can classify a
    retry outcome), or None when provisioning raised.

    A required-task failure (TaskError) flips the member to failed and persists
    the partial results; a git fetch/add failure flips it to failed with a
    reason; an optional-task failure leaves the member ready with the failed task
    recorded and warned. The caller MUST already hold the .reconcile.lock.

    `retrying_ready=True` marks this call as a retry of an outstanding OPTIONAL
    task on a member that is ALREADY ready (a required-task failure already
    keeps a member out of ready in the first place, so this call can only ever
    be chasing an optional task). Before this flag existed, `camp setup` never
    touched an already-ready member at all, so it could never regress one; the
    outstanding-task retry must preserve that invariant — a git fetch timeout or
    any other exception encountered DURING the retry is infrastructure noise
    incidental to the retry attempt, not a reason to demote an already-known-good
    member. So in this mode every exception branch leaves provision_state
    "ready" (persisting partial TaskError results into `tasks` if any were
    produced) and warns to stderr instead of flipping to failed; the returned
    result carries an internal `_retry_failed` marker (popped by the caller) so
    `cmd_setup_group` can still classify the outcome as "still-failing".
    """
    from ..group.manifest import flip_member_state_unlocked
    from . import provision
    from .reconcile import (
        _completed_from_tasks_map,
        _tasks_map_from_results,
        _warn_optional_task_failures,
    )
    from .tasks import TaskError

    name = member["name"]
    completed = _completed_from_tasks_map(entry.get("tasks"))
    tasks_kwarg: dict[str, Any] | None = None
    try:
        task_results = provision.provision_member(
            group, slug, member, completed=completed, env=env
        )
    except subprocess.TimeoutExpired as e:
        reason = f"git fetch timeout after {e.timeout}s"
    except TaskError as e:
        # A required task failed: persist its (and any prior) results alongside
        # the failed state so `camp status` shows the task.
        reason = str(e)
        tasks_kwarg = _tasks_map_from_results(e.results)
    except Exception as e:
        reason = str(e)
    else:
        _warn_optional_task_failures(task_results, name)
        flip_member_state_unlocked(
            mpath, name, "ready", tasks=_tasks_map_from_results(task_results)
        )
        return {"provision_state": "ready"}, task_results

    # Reached only when provisioning raised — one of the three `except` clauses
    # above set `reason` (and, for TaskError, `tasks_kwarg`).
    if retrying_ready:
        # A retry of an outstanding OPTIONAL task on an already-ready member
        # never demotes it — see the docstring above. A required-task failure
        # can't actually reach here (defensive only), but its partial results
        # are still persisted rather than silently dropped.
        if tasks_kwarg:
            flip_member_state_unlocked(mpath, name, "ready", tasks=tasks_kwarg)
        print(
            f"camp: retry for member {name!r} hit {reason} — member remains "
            "ready; run `camp status` for details.",
            file=sys.stderr,
        )
        return {"provision_state": "ready", "_retry_failed": True}, None

    flip_kwargs: dict[str, Any] = {"reason": reason}
    if tasks_kwarg:
        flip_kwargs["tasks"] = tasks_kwarg
    flip_member_state_unlocked(mpath, name, "failed", **flip_kwargs)
    return {"provision_state": "failed", "reason": reason}, None


def cmd_setup_group(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Foreground provisioning: complete/restart member worktrees idempotently.

    Holds the slug-scoped .reconcile.lock for the whole operation so a concurrent
    background provisioner serializes (no torn manifest, no double-add). For each
    non-ready member it runs the per-member provision (fetch+add+tasks), persists
    the per-task results into the member's `tasks` map, and flips the manifest
    pending→ready or →failed+reason. A required-task failure flips the member to
    failed; an optional-task failure leaves the member ready, records the failed
    task, and warns on stderr. Best-effort: one member failing never blocks the
    others.

    Ready-member retry: a ready member that still carries a failed or never-run
    provision-phase task (an optional failure leaves a member ready) is
    re-provisioned so those outstanding tasks re-run — an already-ok task is
    skipped (run-once). A ready member whose tasks are all ok (or that has no
    tasks) is a TRUE no-op: it is not re-provisioned at all (no git fetch, no
    manifest write). Each ready member carries a `retry` outcome in the result:
        "none"          all tasks ok — nothing retried
        "fixed"         outstanding tasks retried and now all ok
        "still-failing" retried but a task is still failed — OR the retry
                        attempt itself hit an infrastructure error (git fetch
                        timeout, or any other exception from provisioning); a
                        ready member is NEVER demoted to failed by that noise,
                        since a required-task failure already keeps a member
                        out of ready in the first place, so a ready member can
                        only ever be chasing an optional task here.
    Pending/failed members provisioned normally do NOT carry a `retry` field.

    Activate-phase retry (execution mode: SYNCHRONOUS — `camp setup` waits for
    it, unlike `camp activate`'s handoff to a detached process). After the
    provision-phase pass above releases `.reconcile.lock`, every boot-ready
    member carrying an activate-phase task recorded "failed" has that task
    retried in a SECOND pass, by calling `run_activate_tasks_in_background`
    (provision/activation.py) in-process — the same body `camp activate`'s
    detached run executes, cleanup-first, against the manifest's persisted
    per-task state. That function itself takes `.reconcile.lock` only for its
    brief closing manifest persist, never across the task subprocess, so this
    retry never holds the lock for the retried task's duration: a concurrent
    `camp rm`, `camp activate`, or another slug's reconcile is never blocked
    by it. This second pass runs OUTSIDE (after) the `with reconcile_lock`
    block above rather than nested inside it — `.reconcile.lock` is not
    reentrant, so nesting would deadlock the moment the retry tried to persist
    its own result.

    Returns {"slug", "members": {name: {"provision_state", "reason"?, "retry"?}}}.
    """
    from .activation import run_activate_tasks_in_background
    from .reconcile import _has_outstanding_activate_tasks, _has_outstanding_provision_tasks

    group_name = group["group"]["name"]
    mpath = manifest_path_for(group_name, slug, env=env)
    member_by_name = {m["name"]: m for m in group["members"]}

    results: dict[str, Any] = {}

    with reconcile_lock(mpath.parent):
        data = read_central_manifest(mpath)
        for entry in data.get("members", []):
            name = entry["name"]
            member = member_by_name.get(name)
            if member is None:
                # Manifest lists a member no longer in the group config.
                continue

            if entry.get("provision_state") == "ready":
                if not _has_outstanding_provision_tasks(member, entry.get("tasks")):
                    # No failed/never-run tasks — a true no-op, not re-provisioned.
                    results[name] = {"provision_state": "ready", "retry": "none"}
                    continue
                # Re-run outstanding tasks in place, then classify the outcome.
                result, task_results = _provision_member_and_flip(
                    group, slug, member, entry, mpath, env=env, retrying_ready=True
                )
                retry_failed = result.pop("_retry_failed", False)
                still_failing = (
                    retry_failed
                    or result["provision_state"] != "ready"
                    or any(r.state == "failed" for r in (task_results or []))
                )
                result["retry"] = "still-failing" if still_failing else "fixed"
                results[name] = result
                continue

            result, _ = _provision_member_and_flip(group, slug, member, entry, mpath, env=env)
            results[name] = result

    # Second pass: activate-phase retry, deliberately outside the lock above.
    data = read_central_manifest(mpath)
    for entry in data.get("members", []):
        name = entry["name"]
        member = member_by_name.get(name)
        if member is None or entry.get("provision_state") != "ready":
            continue
        if not _has_outstanding_activate_tasks(member, entry.get("tasks")):
            continue
        run_activate_tasks_in_background(group, slug, name, env=env)

    return {"slug": slug, "members": results}


def provision_status_code(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Return (exit_code, report) for the provision state of a workspace.

    Exit codes (so the in-session agent can branch programmatically):
        0  all members ready
        2  some members pending (none failed)
        3  any member failed (failed takes precedence over pending)

    Exit codes are driven purely by each member's provision_state, NOT by
    individual task states, and NOT by work_state: a ready member with a
    failed OPTIONAL task stays exit 0 (a failed REQUIRED task already flips
    the member itself to failed), and a member whose work-enabling tasks are
    still installing or have failed never moves the exit code — that fact is
    surfaced only via the report's `work_code` and each member's `work_state`.
    Each member carries its persisted per-task state map (`tasks`, always
    present — an empty dict when the member has no tasks) so callers can surface
    per-task detail without changing exit-code semantics.

    Each member also carries branch-drift facts computed fresh (no fetch) from
    its worktree against its configured `base` (default `origin/main`):
    `branch`, `base`, `ahead`, `behind` (commit counts, `None` when the
    worktree is absent or `base` doesn't resolve locally), and `upstream`
    (`"ok"` / `"gone"` / `"none"`). These never influence `code` or `work_code`.

    report = {
        "slug", "code", "work_code",
        "members": [{
            "name", "provision_state", "work_state", "tasks", "reason"?,
            "branch", "base", "ahead", "behind", "upstream",
        }],
    }, where `tasks` is the manifest's {task-name: {"state", "reason"?}} map and
    `work_code` is a distinct 0/2/3-style rollup over each member's work_state
    (see manifest.work_state_for_member) — 0 when every member is work-ready or
    WORK_STATE_NOT_APPLICABLE, 2 when any is pending, 3 when any is failed
    (failed takes precedence, mirroring `code`). `work_code` never influences
    `code` or the process exit status.
    """
    from ..group.manifest import work_state_for_member

    group_name = group["group"]["name"]
    mpath = manifest_path_for(group_name, slug, env=env)
    data = read_central_manifest(mpath)
    member_config_by_name = {m["name"]: m for m in group.get("members", [])}

    members = []
    any_failed = False
    any_pending = False
    any_work_failed = False
    any_work_pending = False
    for entry in data.get("members", []):
        state = entry.get("provision_state", "pending")
        work_state = work_state_for_member(entry)
        member_config = member_config_by_name.get(entry["name"], {})
        base = member_config.get("base") or "origin/main"
        drift = _git_branch_drift(Path(entry["worktree_path"]), base)
        m: dict[str, Any] = {
            "name": entry["name"],
            "provision_state": state,
            "work_state": work_state,
            "tasks": entry.get("tasks") or {},
            "branch": drift["branch"],
            "base": base,
            "ahead": drift["ahead"],
            "behind": drift["behind"],
            "upstream": drift["upstream"],
        }
        if state == "failed":
            any_failed = True
            if entry.get("reason"):
                m["reason"] = entry["reason"]
        elif state != "ready":
            any_pending = True

        if work_state == "failed":
            any_work_failed = True
        elif work_state == "pending":
            any_work_pending = True
        members.append(m)

    if any_failed:
        code = 3
    elif any_pending:
        code = 2
    else:
        code = 0

    if any_work_failed:
        work_code = 3
    elif any_work_pending:
        work_code = 2
    else:
        work_code = 0

    return code, {"slug": slug, "code": code, "work_code": work_code, "members": members}


def status_header(report: dict[str, Any]) -> str:
    """Derive `camp status`'s workspace-level header state from a
    `provision_status_code` report.

    Boot-readiness (`report["code"]`) takes precedence over work-readiness
    (`report["work_code"]`): work can't even begin until every member
    materializes, so a workspace that hasn't finished booting reads as
    "provisioning" or "failed" regardless of work state. Only once every
    member is boot-ready does the header reflect work-readiness:

        code == 3               -> "failed"
        code == 2               -> "provisioning"
        code == 0, work_code 0  -> "ready"
        code == 0, work_code 2  -> "ready, work pending"
        code == 0, work_code 3  -> "ready, work failed"
    """
    code = report["code"]
    work_code = report.get("work_code", 0)
    if code == 3:
        return "failed"
    if code == 2:
        return "provisioning"
    if work_code == 3:
        return "ready, work failed"
    if work_code == 2:
        return "ready, work pending"
    return "ready"


def wait_for_provisioning_ready(
    group: dict[str, Any],
    slug: str,
    *,
    env: dict[str, str] | None = None,
    interval: float,
    timeout: float,
    sleep: Any,
) -> tuple[str, dict[str, Any]]:
    """Poll provisioning state until ready, failed, or timeout.

    Polls `provision_status_code` (the same manifest state it reads) at a fixed
    `interval` up to a bounded `timeout`, elapsed time tracked purely by counting
    sleeps rather than a wall clock — so tests can inject a fake `sleep` and never
    actually sleep. A killed provisioner leaves members `pending` forever with no
    liveness signal, so this wait is always bounded: it never polls unboundedly.

    Returns (outcome, report):
        outcome: "ready" | "failed" | "timed-out"
        report: the last provision_status_code report, plus a "message" key on
            timeout naming `camp status <slug>` as the way to check current state.

    Already-all-ready returns immediately without calling `sleep`. Any member
    `failed` returns failed immediately, also without sleeping further.
    """
    elapsed = 0.0
    while True:
        code, report = provision_status_code(group, slug, env=env)
        if code == 3:
            return "failed", report
        if code == 0:
            return "ready", report

        if elapsed >= timeout:
            report = dict(report)
            report["message"] = (
                f"timed out waiting for workspace '{slug}' to finish provisioning; "
                f"run `camp status {slug}` to check current state"
            )
            return "timed-out", report

        sleep(interval)
        elapsed += interval


def cmd_sync_group(
    group: dict[str, Any],
    *,
    force: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Sync canonical member repos to latest origin/main.

    Safe by default: dirty or off-main members are skipped.
    force=True: hard-reset to origin/main.

    Returns:
        {
            "status": "ok" | "ok_with_warnings",
            "members": {
                "<name>": {"action": "ff" | "skip-dirty" | "skip-off-main" | "absent" | ...}
            }
        }
    """
    members_result: dict[str, Any] = {}
    errors = 0

    for member in group["members"]:
        name = member["name"]
        repo_root = Path(member["repo_root"])

        if not (repo_root / ".git").exists() and not (repo_root / ".git").is_file():
            members_result[name] = {"action": "absent"}
            continue

        # Fetch
        subprocess.run(
            ["git", "-C", str(repo_root), "fetch", "origin", "--quiet"],
            capture_output=True,
            text=True,
            check=False,
        )

        is_dirty = _git_is_dirty(repo_root)
        branch = _git_out(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        on_main = branch == "main"

        if not force and is_dirty:
            members_result[name] = {"action": "skip-dirty"}
            continue
        if not force and not on_main:
            members_result[name] = {"action": "skip-off-main", "branch": branch}
            continue

        if force:
            subprocess.run(
                ["git", "-C", str(repo_root), "checkout", "main", "--quiet"],
                capture_output=True,
                text=True,
                check=False,
            )
            r = subprocess.run(
                ["git", "-C", str(repo_root), "reset", "--hard", "origin/main"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            r = subprocess.run(
                ["git", "-C", str(repo_root), "merge", "--ff-only", "origin/main"],
                capture_output=True,
                text=True,
                check=False,
            )

        if r.returncode != 0:
            members_result[name] = {"action": "error", "error": r.stderr.strip()}
            errors += 1
        else:
            members_result[name] = {"action": "ff" if not force else "reset-force"}

    status = "ok" if errors == 0 else "ok_with_warnings"
    return {"status": status, "members": members_result}
