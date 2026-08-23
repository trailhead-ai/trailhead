"""The workspace command group: ``list`` (alias ``ls``), ``activate``, ``pwd``.

These verbs all act on an ALREADY-EXISTING workspace resolved from the group:
list the group's workspaces, activate a member for the session, or print a
workspace's resolved path. (Workspace *creation* — ``new`` — lives in ``group``.)
"""
from __future__ import annotations

import sys

from .dispatch import _slug_from_args_or_cwd


def _cmd_ls_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
) -> None:
    """camp list [--json]  (alias: ls)

    Prints one 'slug abs-path' line per workspace to stdout; exits 0.
    Empty group → no stdout, exit 0. Pure read: no state mutation, no harness exec.

    Human + --json output is produced by the SHARED render_workspace_list,
    the same renderer spine.main's no-group `cmd_ls` uses, so the surface is
    identical regardless of cwd.
    """
    from ..provision.lifecycle import cmd_ls_group, render_workspace_list

    as_json = "--json" in args
    entries = cmd_ls_group(group, env=env)
    render_workspace_list(entries, as_json=as_json)


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

    background = "--background" in args
    filtered = [a for a in args if a != "--background"]
    slug = _slug_from_args_or_cwd(filtered, group, verb="activate", env=env)

    if not filtered:
        _die("camp activate: a member name is required\n  usage: camp activate <member>")

    member_name = filtered[0]

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
    from ..spine import _resolve_slug, _consume_flag_value
    from ..launch.shell_integration import cmd_pwd, WorkspaceNotFoundError

    filtered = list(args)
    _consume_flag_value(filtered, "--group")  # already resolved upstream; drop it

    if not filtered:
        print("camp pwd: a slug is required\n  usage: camp pwd <slug>", file=sys.stderr)
        sys.exit(1)

    slug = _resolve_slug(filtered[0], context="pwd")

    try:
        ws_dir = cmd_pwd(group, slug, env=env)
    except WorkspaceNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    # Print the path exactly once, no trailing whitespace, no newline other than
    # the one print() appends.
    print(str(ws_dir))
