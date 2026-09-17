"""The workspace command group: ``list`` (alias ``ls``), ``activate``, ``pwd``.

These verbs all act on an ALREADY-EXISTING workspace resolved from the group:
list the group's workspaces, activate a member for the session, or print a
workspace's resolved path. (Workspace *creation* — ``new`` — lives in ``group``.)
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

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
    and `render_list_row_human` both key off of.
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


def render_list_row_human(row: dict) -> str:
    """One answered `camp list` row, human-rendered — the ``slug state
    workspace_path`` line, from a JSON-shaped row rather than an entry.

    An unmanaged row (`slug` is `None`) prints its `tmux_session` in the
    first column instead — the same rule `render_workspace_list` applies
    locally, so a relayed or merged unmanaged row reads identically to one
    rendered on this machine.

    The one place a relayed or merged `camp list` row is turned into its
    human line: both `_cmd_ls_host_cli`'s `--host` callback and the
    `-a`/`--all-hosts` merged renderer in `cli/dispatch.py` print one row at
    a time through it. Raises `KeyError` on a row missing a key it needs, so
    a caller can degrade that one row.
    """
    from ..launch.recovery import printable_path

    first = row["slug"] if row["slug"] is not None else row["tmux_session"]
    return (
        f"{printable_path(first)} {row['state']} "
        f"{printable_path(row['workspace_path'])}"
    )


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
    to do with a total failure, exactly as `_sessions_live_answer` already
    does for `camp sessions`' own `-a` path.

    Always reads tmux at :data:`~camp.launch.inventory.DisclosureScope.WIDENED`
    — this IS the `-a`/`--all-hosts` axis, which has already opted into
    seeing leftover sessions named rather than merely counted, regardless of
    whether the group axis was itself widened. Leftover sessions are
    host-wide, not per-group, so merging several groups' answers dedupes
    them by `tmux_session` name rather than repeating one leftover once per
    group that happened to enumerate it.
    """
    from ..launch.inventory import DisclosureScope
    from ..provision.lifecycle import cmd_ls_group, load_answerable_groups
    from .common import _groups_dir

    if not all_groups:
        listing = cmd_ls_group(group, env=None, scope=DisclosureScope.WIDENED)
        return _project_list_rows(listing.entries + listing.unmanaged), [], 0

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

    entries: list[dict] = []
    seen_unmanaged: set[str] = set()
    for g in groups:
        listing = cmd_ls_group(g, env=None, scope=DisclosureScope.WIDENED)
        entries.extend(listing.entries)
        for u in listing.unmanaged:
            if u["tmux_session"] in seen_unmanaged:
                continue
            seen_unmanaged.add(u["tmux_session"])
            entries.append(u)
    entries.sort(key=lambda e: e.get("group") or "")

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

    Prints one 'slug state abs-path' line per workspace to stdout; exits 0.
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
    from ..launch.inventory import DisclosureScope
    from ..provision.lifecycle import (
        answerable_groups_or_refuse,
        cmd_ls_group,
        render_workspace_list,
    )
    from .common import _groups_dir

    parser = CampParser(verb="list")
    parser.add_argument("--json", action="store_true")
    as_json = parser.parse_args(args).json

    groups, unparsable = answerable_groups_or_refuse(_groups_dir(), verb="list")

    if not groups:
        print("camp list: no groups configured — nothing to list", file=sys.stderr)
        render_workspace_list([], as_json=as_json, group_failures=unparsable)
        return

    entries: list[dict] = []
    seen_unmanaged: set[str] = set()
    notice_printed = False
    for group in groups:
        listing = cmd_ls_group(group, env=env, scope=DisclosureScope.WIDENED, tmux=tmux)
        if listing.notice and not notice_printed:
            print(listing.notice, file=sys.stderr)
            notice_printed = True
        entries.extend(listing.entries)
        for u in listing.unmanaged:
            if u["tmux_session"] in seen_unmanaged:
                continue
            seen_unmanaged.add(u["tmux_session"])
            entries.append(u)
    entries.sort(key=lambda e: e.get("group") or "")

    render_workspace_list(entries, as_json=as_json, group_failures=unparsable)


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
        for row in rows:
            if not row.get("ok"):
                continue
            # Version skew across the operator's two machines is the
            # expected steady state for this feature, not an edge case — a
            # remote camp of a different version can answer with a row that
            # omits a key this rendering depends on. Degrade that ONE row
            # rather than let it take the whole answer down; the well-formed
            # rows around it still print.
            try:
                rendered = render_list_row_human(row)
            except KeyError as e:
                print(
                    f"camp list: host {host_name!r} sent a workspace row "
                    f"missing {e.args[0]!r} — skipping",
                    file=sys.stderr,
                )
                continue
            print(rendered)

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
