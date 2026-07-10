"""The status / metadata command group: ``--version``, ``--which``, ``status``.

``_cmd_version`` / ``_cmd_which`` print the resolved binary path (and, for
version, the active group if resolvable from cwd). ``_cmd_status_group_cli`` is
the group-aware status report: a per-member provision-state view with structured
0/2/3 exit codes when a slug resolves, or the fleet-wide git-status table
otherwise.
"""
from __future__ import annotations

import sys

from .dispatch import _SELF, _VERSION, _slug_from_args_or_cwd


def _cmd_version() -> None:
    print(f"camp {_VERSION}")
    print(f"binary: {_SELF}")
    _print_active_group()


def _print_active_group() -> None:
    """Print the active group (and slug, if any) for the --version header.

    Delegates to `_resolve_group_for_command` — the same resolver group-aware
    commands use — instead of reimplementing config loading and --group
    override parsing. That resolver raises GroupConfigError for a malformed
    config; --version reports it without exiting non-zero (printing the
    version must never hard-fail), unlike `camp status` and other group
    commands, which surface a config error as a hard failure.
    """
    import sys as _sys
    from pathlib import Path

    from .dispatch import _resolve_group_for_command

    try:
        group, _env = _resolve_group_for_command(_sys.argv[1:])
    except Exception as e:
        print(f"group: (config error: {e})")
        return
    if group is None:
        print("group: (not resolved from cwd)")
        return

    from ..group.resolve import resolve_from_cwd

    try:
        group_name, slug = resolve_from_cwd(Path.cwd(), [group])
        print(f"group: {group_name}" + (f" (slug: {slug})" if slug else " (fleet view)"))
    except Exception:
        print("group: (not resolved from cwd)")


def _cmd_which() -> None:
    print(str(_SELF))


def _cmd_status_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
    dry_run: bool,
) -> None:
    """camp status [--name <slug>] [--json]

    Scoped (a slug resolvable via --name or cwd): emit the per-member
    provision-state report with STRUCTURED EXIT CODES so the in-session agent can
    branch programmatically — 0=all ready, 2=some pending, 3=some failed. --json
    prints the stable report shape on stdout.

    Text output is line-oriented and STABLE for agent parsing:
        camp status: <slug> — provisioning
          <member>: <provision_state>[ (<reason>)]
            <task-name>: <state>          # one per task, manifest insertion order

    Each member line is indented two spaces; its per-task sub-lines are indented
    four spaces and appear in the member's manifest task-insertion order. A
    member with no tasks emits no sub-lines. Per-task detail never changes the
    exit code — a ready member with a failed OPTIONAL task stays exit 0 with the
    failed task listed. Task-level failure reasons are omitted from the text
    (they may be multi-line stderr excerpts); read --json for the full map.

    Fleet view (no slug): the git-status table across all worktrees (exit 0).
    """
    import json as _json
    from ..provision.lifecycle import cmd_status_group, provision_status_code

    as_json = "--json" in args
    filtered = [a for a in args if a != "--json"]

    # Resolve a slug from --name or cwd; if found, emit the provision-state view.
    slug = _slug_from_args_or_cwd(filtered, group, verb="status", allow_none=True, env=env)

    if slug is not None:
        try:
            code, report = provision_status_code(group, slug, env=env)
        except Exception as e:
            print(f"camp status: {e}", file=sys.stderr)
            sys.exit(1)

        if as_json:
            print(_json.dumps(report))
        else:
            print(f"camp status: {slug} — provisioning")
            for m in report["members"]:
                line = f"  {m['name']}: {m['provision_state']}"
                if m.get("reason"):
                    line += f" ({m['reason']})"
                print(line)
                for task_name, info in (m.get("tasks") or {}).items():
                    print(f"    {task_name}: {info.get('state', '?')}")
        sys.exit(code)

    try:
        result = cmd_status_group(group, slug=None, env=env)
    except Exception as e:
        print(f"camp status: {e}", file=sys.stderr)
        sys.exit(1)

    if as_json:
        print(_json.dumps(result))
        return

    worktrees = result.get("worktrees", [])
    if not worktrees:
        print("camp status: no active worktrees — use 'camp <slug>' to create one")
        return

    print(f"{'SLUG':<24}  {'BRANCH':<30}  MEMBERS")
    print("-" * 72)
    for wt in worktrees:
        slug = wt.get("slug", "?")
        branch = wt.get("branch", "")
        members = wt.get("members", [])
        parts = []
        for m in members:
            name_str = m.get("name", "?")
            if not m.get("present", True):
                parts.append(f"{name_str}[MISSING]")
            else:
                dirty = m.get("dirty_files", 0)
                ahead = m.get("unpushed_commits", 0)
                flags = ""
                if dirty:
                    flags += f" +{dirty}dirty"
                if ahead:
                    flags += f" +{ahead}ahead"
                parts.append(f"{name_str}{flags}")
        repo_str = "  ".join(parts) if parts else "(no members)"
        print(f"{slug:<24}  {branch:<30}  {repo_str}")
