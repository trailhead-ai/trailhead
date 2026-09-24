"""The workspace command group: ``list`` (alias ``ls``), ``activate``, ``pwd``.

These verbs all act on an ALREADY-EXISTING workspace resolved from the group:
list the group's workspaces, activate a member for the session, or print a
workspace's resolved path. (Workspace *creation* — ``new`` — lives in ``group``.)
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Callable

from .dispatch import _slug_from_name_or_cwd
from .parser import CampParser, group_verb_parser

if TYPE_CHECKING:
    from ..host.config import Host


def _project_list_rows(entries: list[dict]) -> list[dict]:
    """Project `cmd_ls_group`'s entries onto the fixed `camp list` JSON row
    shape — the same projection `render_workspace_list` applies inline,
    factored out so the `-a`/`--all-hosts` local answer below can build the
    same row shape without printing. An unmanaged entry's `slug` is `None`
    — passed through as-is, the same discriminator `render_workspace_list`
    and `render_list_rows_human` both key off of.
    """
    return [
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


def render_list_rows_human(
    rows: list[dict],
    *,
    show_group: bool,
    on_missing: Callable[[str], None],
    now: float | None = None,
) -> list[str]:
    """Answered `camp list` rows, human-rendered — the same table
    `render_workspace_list` prints locally, from JSON-shaped rows rather than
    entries, returned as lines for the caller to print or indent.

    The one place a relayed or merged `camp list` answer is turned into its
    human lines: `_cmd_ls_host_cli`'s `--host` callback renders a machine's
    whole answer through it, and the `-a`/`--all-hosts` merged renderer in
    `cli/dispatch.py` renders each machine's block through it. Cells come
    from `camp.launch.inventory.workspace_row_cells`, which escapes every
    peer-supplied field, so a relayed control character cannot forge a row.

    A leftover-count row (`unmanaged_count` present — the group-scoped
    answer's summary, which names no leftover) renders as that summary line
    after the table. A row missing `slug` or `workspace_path` is skipped,
    with *on_missing* called with the missing key so the caller can say
    which machine sent it; the well-formed rows around it still render.
    *now* is the instant `last_touched` renders relative to; `None` reads
    the real clock.
    """
    import time

    from ..launch.inventory import (
        WORKSPACE_TABLE_HEADERS,
        WORKSPACE_TABLE_HEADERS_WITH_GROUP,
        format_unmanaged_summary,
        workspace_row_cells,
    )
    from ..launch.recovery import printable_path
    from ..launch.table import render_table

    resolved_now = now if now is not None else time.time()
    cells: list[list[str]] = []
    summaries: list[str] = []
    for row in rows:
        if "unmanaged_count" in row:
            summaries.append(printable_path(format_unmanaged_summary(row["unmanaged_count"])))
            continue
        try:
            cells.append(workspace_row_cells(row, now=resolved_now, show_group=show_group))
        except KeyError as e:
            on_missing(e.args[0])
    headers = WORKSPACE_TABLE_HEADERS_WITH_GROUP if show_group else WORKSPACE_TABLE_HEADERS
    return render_table(headers, cells) + summaries


def _merged_widened_entries(
    groups: list[dict], *, env: dict[str, str] | None, tmux: Any | None = None
) -> tuple[list[dict], list[str]]:
    """Every group's rows read at
    :data:`~camp.launch.inventory.DisclosureScope.WIDENED`, merged into one
    group-sorted list, alongside each group's tmux-unanswered notice.

    The one place the two widened local answers — `local_list_answer`'s
    `--all-groups` branch and `_cmd_ls_all_groups_cli` — merge several
    groups' listings, so they can never drift on how a leftover session is
    counted. Leftover (unmanaged) sessions are host-wide, not per-group, so
    they are deduped by `tmux_session` name: one leftover appears once,
    however many of the groups enumerated it, and they sort after every
    workspace row. This dedup is only ever between leftover rows — a
    session that belongs to one of the merged groups is not a leftover in
    ANY group's listing (`cmd_ls_group` claims against the whole host), so
    it reaches this merge exactly once, as its own group's workspace row.
    Notices are returned rather
    than printed, because one of the two callers must not print at all.
    """
    from ..launch.inventory import DisclosureScope
    from ..provision.lifecycle import cmd_ls_group

    entries: list[dict] = []
    notices: list[str] = []
    seen_unmanaged: set[str] = set()
    for group in groups:
        listing = cmd_ls_group(group, env=env, scope=DisclosureScope.WIDENED, tmux=tmux)
        if listing.notice:
            notices.append(listing.notice)
        entries.extend(listing.entries)
        for u in listing.unmanaged:
            if u["tmux_session"] in seen_unmanaged:
                continue
            seen_unmanaged.add(u["tmux_session"])
            entries.append(u)
    # A leftover session belongs to no group, so it sorts AFTER every
    # workspace row rather than floating above them on an empty group name:
    # the rows a widened listing was asked for come first.
    entries.sort(key=lambda e: (e.get("group") is None, e.get("group") or ""))
    return entries, notices


def local_list_answer(
    group: dict | None, *, all_groups: bool
) -> tuple[list[dict], list[str], int]:
    """The value-returning local answer for `camp list`, reused by the
    `-a`/`--all-hosts` wiring in `cli/dispatch.py`: never prints, never
    exits.

    ``all_groups=False`` answers for *group* alone — the same rows
    `_cmd_ls_group_cli` prints via `cmd_ls_group`. ``all_groups=True``
    answers for every group :func:`~camp.provision.lifecycle.load_answerable_groups`
    can load — the value-returning sibling of
    :func:`~camp.provision.lifecycle.answerable_groups_or_refuse`, used
    here (never that helper) because a `sys.exit`-on-refusal is not
    something a value-returning answer this function's caller still needs to
    merge with other machines' answers can afford — the caller decides what
    to do with a total failure.

    Reads tmux at the scope the GROUP axis asked for: a leftover session
    belongs to no group — its name predates the group-qualified scheme and
    cannot be attributed to one — so narrowing to one group and naming one
    would put another group's project names into this answer, which is the
    disclosure boundary `docs/design/cross-group-cross-account-listing.md`
    already narrowed group-scoped session queries for. `-a` widens the
    MACHINE axis, not the group axis, and the remote half of this same
    answer is narrowed by that filter
    (:func:`~camp.host.merge.merge_all_hosts_answer`'s `group=`) — so
    naming this machine's leftovers here would also make the policy differ
    between this machine and a peer inside one command. The narrow branch
    therefore counts them (:func:`~camp.provision.lifecycle.unmanaged_count_row`,
    the same row the group-scoped `camp list --json` carries), and only the
    `--all-groups` branch — which crosses groups by construction — names
    them, merged through :func:`_merged_widened_entries`.

    Notices are carried, never dropped: an unanswerable tmux renders every
    row `unknown`, and the notice is the only thing that says why.
    """
    from ..launch.inventory import DisclosureScope
    from ..provision.lifecycle import (
        cmd_ls_group,
        load_answerable_groups,
        unmanaged_count_row,
    )
    from .common import _groups_dir

    if not all_groups:
        listing = cmd_ls_group(group, env=None, scope=DisclosureScope.GROUP)
        rows = _project_list_rows(listing.entries)
        if listing.unmanaged_count:
            rows.append(unmanaged_count_row(listing.unmanaged_count))
        return rows, ([listing.notice] if listing.notice else []), 0

    notices: list[str] = []
    groups, unparsable = load_answerable_groups(_groups_dir())
    for detail in unparsable:
        notices.append(f"camp list: {detail} — skipping")
    if not groups and unparsable:
        notices.append(
            "camp list: could not answer for any configured group — "
            "every group config failed to parse; fix a config above and re-run"
        )
        return [], notices, 1
    if not groups:
        notices.append("camp list: no groups configured — nothing to list")
        return [], notices, 0

    entries, tmux_notices = _merged_widened_entries(groups, env=None)
    notices.extend(tmux_notices)

    rows = _project_list_rows(entries)
    rows += [{"ok": False, "group": None, "reason": d} for d in unparsable]
    return rows, notices, 0


def _cmd_ls_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
    *,
    tmux: Any | None = None,
) -> None:
    """camp list [--json]  (alias: ls)

    Prints a WORKSPACE / SESSIONS / LAST TOUCHED table to stdout; exits 0.
    Empty group → no stdout, exit 0. Reads tmux (once, via `cmd_ls_group`) to
    annotate each row with its session state — a tmux read, not a harness
    exec or a state mutation.

    Human + --json output is produced by the SHARED render_workspace_list,
    the same renderer spine.main's no-group `cmd_ls` uses, so the surface is
    identical regardless of cwd. Leftover (unmanaged) tmux sessions are
    counted, never named, at this group-scoped axis — see
    `cmd_ls_group`'s default `DisclosureScope.GROUP`.

    *tmux* — the same injectable seam `cmd_ls_group` itself exposes,
    threaded through so an in-process test can drive tmux state without a
    real subprocess; `None` (the default) means the real `Tmux()`.
    """
    from ..provision.lifecycle import cmd_ls_group, render_workspace_list

    parser = group_verb_parser("list")
    parser.add_argument("--json", action="store_true")
    as_json = parser.parse_args(args).json

    listing = cmd_ls_group(group, env=env, tmux=tmux)
    if listing.notice:
        print(listing.notice, file=sys.stderr)
    render_workspace_list(
        listing.entries, as_json=as_json, unmanaged_count=listing.unmanaged_count
    )


def _cmd_ls_all_groups_cli(
    args: list[str], env: dict[str, str] | None, *, tmux: Any | None = None
) -> None:
    """camp list --all-groups/-g [--json] — every configured group's workspaces, merged.

    Reached only from ``cli/dispatch.py``'s early `--all-groups`/`-g` handling,
    before any single group is resolved — this answers for EVERY group
    :func:`camp.group.config.load_all_groups` can load, never the group cwd
    would have resolved to.

    Rows are ordered by group name, then by each group's own
    :func:`~camp.provision.lifecycle.cmd_ls_group` order (already slug-sorted)
    — a stable sort over the merged list, so widening the answer never
    reorders what a single-group `camp list` already prints for that group's
    rows.

    Renders through the SAME :func:`~camp.provision.lifecycle.render_workspace_list`
    every other `camp list`/`ls` surface uses, so the human + `--json` shape is
    identical to the single-group and no-group-configured cases.

    A group config camp cannot parse is skipped BY NAME on stderr rather than
    failing the whole answer (:func:`~camp.provision.lifecycle.answerable_groups_or_refuse`)
    — one broken sibling must not blank every other group's rows. With every
    configured group unparsable, or with none configured at all, this NEVER
    falls through to the legacy standalone-worktree registry `spine.py`'s
    no-group `cmd_ls` reads (that fallback is `spine.main`'s, reached only
    when this option is absent and no group resolves from cwd) — it states
    the reason on stderr instead, exiting nonzero only when every group
    failed to parse.
    """
    from ..provision.lifecycle import (
        answerable_groups_or_refuse,
        render_workspace_list,
    )
    from .common import _groups_dir

    parser = CampParser(verb="list")
    parser.add_argument("--json", action="store_true")
    as_json = parser.parse_args(args).json

    groups, unparsable = answerable_groups_or_refuse(_groups_dir(), verb="list")

    if not groups:
        print("camp list: no groups configured — nothing to list", file=sys.stderr)
        render_workspace_list([], as_json=as_json, group_failures=unparsable, show_group=True)
        return

    entries, notices = _merged_widened_entries(groups, env=env, tmux=tmux)
    if notices:
        print(notices[0], file=sys.stderr)

    render_workspace_list(
        entries, as_json=as_json, group_failures=unparsable, show_group=True
    )


def _cmd_ls_host_cli(
    args: list[str], host: "Host", host_name: str, *, connect_timeout: float | None = None
) -> None:
    """camp list --host <name> [--json] — every group's workspaces on one
    declared remote machine, relayed through the SSH transport.

    Reached ONLY from ``cli/dispatch.py``'s ``--host`` handling in
    ``_dispatch_host_command``, after the name has resolved to a declared
    `Host` — the same shape ``_cmd_ls_all_groups_cli`` is reached in for the
    ``--all-groups`` axis. The far side is ALWAYS invoked with the
    all-groups + ``--json`` form (never narrowed by the local ``--group`` a
    remote invocation must never carry — refused earlier in `main()`), so a
    remote answer always spans that machine's groups.

    ``connect_timeout`` is the operator's resolved value
    (`camp.host.config.connect_timeout_seconds()`, read once by
    `main()`'s ``--host`` handling and passed down); ``None`` (a direct call
    with no caller-supplied value) falls back to the transport's own
    documented default.

    Delegates everything downstream of "what argv to send" and "how to print
    an ok row" to :func:`camp.host.relay.relay_all_groups` — the shared
    seam every `--host` verb dispatches through — which owns the transport
    call, outcome classification, and every rendering except this verb's own
    "how do I print one answered row" callback.
    """
    from ..host.relay import relay_all_groups
    from ..host.transport import DEFAULT_CONNECT_TIMEOUT_SECONDS

    if connect_timeout is None:
        connect_timeout = DEFAULT_CONNECT_TIMEOUT_SECONDS

    parser = CampParser(verb="list")
    parser.add_argument("--json", action="store_true")
    as_json = parser.parse_args(args).json

    def _render_human_rows(rows: list[dict]) -> None:
        # Version skew across the operator's two machines is the expected
        # steady state for this feature, not an edge case — a remote camp of
        # a different version can answer with a row that omits a key this
        # rendering depends on. That ONE row is skipped with a notice; the
        # well-formed rows around it still print.
        def _skip(key: str) -> None:
            print(
                f"camp list: host {host_name!r} sent a workspace row "
                f"missing {key!r} — skipping",
                file=sys.stderr,
            )

        # The far side always answers for every group, so the table names
        # each row's group.
        for line in render_list_rows_human(
            [row for row in rows if row.get("ok")], show_group=True, on_missing=_skip
        ):
            print(line)

    relay_all_groups(
        "list",
        host,
        host_name,
        ["list", "--all-groups", "--json"],
        as_json=as_json,
        render_human_rows=_render_human_rows,
        connect_timeout=connect_timeout,
    )


def _cmd_activate_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
) -> None:
    """camp activate <member> [--name <slug>] [--background]

    Marks the member activated and returns WITHOUT waiting for its
    activate-phase tasks: any outstanding work-enabling work is handed to the
    detached provisioner (spawn_detached_provisioner) rather than run inline.
    The operator gets the member's CLAUDE.md right away regardless of whether
    that work has finished, plus one feedback line naming what camp observed —
    tasks freshly queued, an activation already in progress, work already
    complete, a retry of previously failed work, or a member with no
    activate-phase task declared.

    `--background` is what the detached provisioner itself invokes: it runs
    only the guarded task execution (run_activate_tasks_in_background) — no
    doc, no feedback line — and exits.
    """
    from ..spine import _die
    from ..provision.activation import (
        activate_member,
        run_activate_tasks_in_background,
        MemberNotReadyError,
    )
    from ..group.config import GroupConfigError
    from ..launch.profile import resolve_harness_profile

    parser = group_verb_parser("activate")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--name", metavar="SLUG")
    parser.add_argument("member", nargs="?")
    parsed = parser.parse_args(args)

    # The workspace is resolved BEFORE the member name is checked for, so an
    # invocation that names neither still reports the slug problem first, which
    # is the order the two messages are written to be read in.
    slug = _slug_from_name_or_cwd(
        group, verb="activate", name=parsed.name, env=env
    )

    if parsed.member is None:
        _die("camp activate: a member name is required\n  usage: camp activate <member>")

    background = parsed.background
    member_name = parsed.member

    if background:
        try:
            run_activate_tasks_in_background(group, slug, member_name, env=env)
        except Exception as e:
            # Never let a detached run crash to a raw traceback in its
            # logfile — nobody is waiting on this process's exit code.
            print(f"camp activate --background: {e}", file=sys.stderr)
            sys.exit(1)
        return

    profile = resolve_harness_profile(group)

    try:
        activate_member(group, slug, member_name, env=env, profile=profile)
    except MemberNotReadyError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except GroupConfigError as e:
        print(f"camp activate: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        _die(f"camp activate: {e}")


def _cmd_pwd_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
) -> None:
    """camp pwd <slug> — print the resolved workspace path on stdout (exactly one line).

    Security contract: stdout carries ONLY the path; diagnostics go to stderr.
    """
    from ..spine import _resolve_slug
    from ..launch.shell_integration import cmd_pwd, WorkspaceNotFoundError

    parser = group_verb_parser("pwd")
    parser.add_argument("slug", nargs="?")
    parsed = parser.parse_args(args)

    if parsed.slug is None:
        print("camp pwd: a slug is required\n  usage: camp pwd <slug>", file=sys.stderr)
        sys.exit(1)

    slug = _resolve_slug(parsed.slug, context="pwd")

    try:
        ws_dir = cmd_pwd(group, slug, env=env)
    except WorkspaceNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    # Print the path exactly once, no trailing whitespace, no newline other than
    # the one print() appends.
    print(str(ws_dir))
