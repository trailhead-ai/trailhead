"""The workspace-lifecycle command group: ``setup``, ``sync``, ``remove``, ``rebase``.

These verbs mutate (or read the provision state of) an existing group's
workspaces: run the per-member provisioner (``setup``, plus its read-only
``--status`` mode), sync members to their base (``sync``), tear a workspace down
(``remove``, alias ``rm``), or rebase a workspace's members (``rebase``).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .dispatch import _slug_from_args_or_cwd


def _cmd_setup_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
    dry_run: bool,
) -> None:
    """camp setup [<slug>] [--background] [--json]

    ONE code path for foreground completion and the detached provisioner:
    --background is what `camp new` spawns (start_new_session, log → setup.log); it
    runs the same per-member provisioning as the foreground invocation, flipping
    each member pending→ready or →failed under the slug-scoped .reconcile.lock.
    The slug is resolved from the positional arg, --name, or cwd.

    A member that was already ready carries a `retry` outcome, appended to its
    text line so a no-op is distinguishable from a repaired or still-broken retry:
      - `retry=none`          already ready, all tasks ok — nothing re-run
      - `retry=fixed`         outstanding tasks re-run and now all ok
      - `retry=still-failing` re-run but a task is still failed

    `camp setup` is also the documented recovery path for a FAILED
    activate-phase task (`npm ci`, a graph build, ...). That retry runs
    SYNCHRONOUSLY — this call waits for it — but never holds .reconcile.lock
    across the retried task's subprocess: see cmd_setup_group's docstring for
    the lock-scope contract.
    """
    import json as _json
    from ..provision.lifecycle import cmd_setup_group
    from ..spine import _consume_flag_value, _die

    # Read-only status mode (the SessionStart hook runs `camp setup --status`).
    # Parsed BEFORE slug resolution so `--status` is never normalized into a slug.
    # NEVER mutates (no reconcile, no worktree add, no manifest write) and ALWAYS
    # exits 0 — a non-zero SessionStart hook exit surfaces as a warning every time.
    if "--status" in args:
        _cmd_setup_status_cli(args, group, env)
        return

    # Reject the removed --retry flag with a legible error.
    if "--retry" in args:
        _die(
            "camp setup: --retry flag has been removed. "
            "Run 'camp setup' (no flag) to retry pending/failed members."
        )

    background = "--background" in args
    as_json = "--json" in args
    filtered = [a for a in args if a not in ("--background", "--json")]
    _consume_flag_value(filtered, "--group")  # already resolved upstream; drop it
    slug = _slug_from_args_or_cwd(
        filtered, group, verb="setup", consume_positional=True, env=env
    )

    if dry_run:
        print(f"[dry-run] would provision worktree {slug!r}", file=sys.stderr)
        return

    try:
        result = cmd_setup_group(group, slug, env=env)
    except Exception as e:
        _die(f"camp setup: {e}")

    if as_json:
        print(_json.dumps(result))
        return

    members = result.get("members", {})
    label = "provisioned (background)" if background else "provisioned"
    print(f"camp setup: {slug} — {label}", file=sys.stderr)
    for nm, info in members.items():
        line = f"  {nm}: {info.get('provision_state', '?')}"
        if info.get("reason"):
            line += f" ({info['reason']})"
        if info.get("retry"):
            line += f" [retry={info['retry']}]"
        print(line, file=sys.stderr)


def _cmd_setup_status_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
) -> None:
    """camp setup --status — read-only provision-status summary for the SessionStart hook.

    Resolves the slug from --name or cwd (the hook runs in the workspace dir),
    prints a concise human-readable summary to STDOUT (so the hook surfaces it as
    session context), and ALWAYS exits 0. It never mutates: no reconcile, no
    worktree add, no manifest write, no junk dir. The structured 0/2/3 exit codes
    stay on `camp status` for direct agent use, NOT on `--status`.
    """
    from ..provision.lifecycle import provision_status_code

    filtered = [a for a in args if a not in ("--status", "--background", "--json")]
    from ..spine import _consume_flag_value
    _consume_flag_value(filtered, "--group")  # already resolved upstream; drop it

    slug = _slug_from_args_or_cwd(
        filtered, group, verb="setup", consume_positional=True, allow_none=True, env=env
    )

    if slug is None:
        print("camp setup --status: no workspace resolved from cwd")
        return

    try:
        _code, report = provision_status_code(group, slug, env=env)
    except Exception as e:
        # Read-only + never-fail contract: report the issue, still exit 0.
        print(f"camp setup --status: {slug} — status unavailable ({e})")
        return

    members = report.get("members", [])
    ready = sum(1 for m in members if m.get("provision_state") == "ready")
    print(f"camp setup --status: {slug} — {ready}/{len(members)} ready")
    for m in members:
        line = f"  {m['name']}: {m['provision_state']}"
        if m.get("reason"):
            line += f" ({m['reason']})"
        print(line)


def _cmd_sync_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
    dry_run: bool,
) -> None:
    """camp sync [--force] [--json]"""
    import json as _json
    from ..provision.lifecycle import cmd_sync_group

    as_json = "--json" in args
    force = "--force" in args

    if dry_run:
        print(f"[dry-run] would sync group {group['group']['name']!r}", file=sys.stderr)
        return

    result = cmd_sync_group(group, force=force, env=env)

    if as_json:
        print(_json.dumps(result))
        return

    status = result.get("status", "?")
    members = result.get("members", {})
    print(f"camp sync: status={status}")
    for name, info in members.items():
        print(f"  {name}: {info.get('action', '?')}")


def _cmd_remove_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
    dry_run: bool,
) -> None:
    """camp remove [--force] [--name <slug>]  (alias: rm)

    No session-finalization or vault-flush call and no session-liveness
    precondition — there is no session lock. Confinement and the dirty-tree
    block live in reconcile_break, which also serializes concurrent same-slug
    removals on the slug reconcile lock.

    Output contract (mirrors `camp new`): stdout carries AT MOST one line — the
    absolute repo_root of the group's FIRST member, emitted only when removal
    fully succeeded AND the invoking cwd was inside the removed workspace, so
    the shellenv `camp()` wrapper can `cd "$(camp rm)"` out of the deleted
    directory. Every confirmation and diagnostic goes to stderr. On any failure
    (including partial removal) stdout is EMPTY and the exit is nonzero — the
    wrapper stays put.
    """
    from ..group.manifest import workspace_dir
    from ..provision.reconcile import reconcile_break
    from ..spine import _consume_flag_value, _die

    force = "--force" in args
    filtered = [a for a in args if a != "--force"]
    _consume_flag_value(filtered, "--group")  # already resolved upstream; drop it
    slug = _slug_from_args_or_cwd(
        filtered, group, verb="remove", consume_positional=True, env=env
    )

    # Classify the cwd BEFORE teardown: afterwards the cwd inode may no longer
    # exist (Path.cwd() raises), and the answer decides whether stdout carries
    # the return path. Only a removal that pulls the directory out from under
    # the caller warrants teleporting their shell; `camp rm --name other` run
    # from elsewhere must leave the shell where it is.
    ws_dir = workspace_dir(group["group"]["name"], slug, env=env)
    try:
        cwd_inside_ws = Path.cwd().resolve().is_relative_to(ws_dir.resolve())
    except OSError:
        cwd_inside_ws = False

    # Session guard: refuse BEFORE teardown (and before the dry-run early
    # return) so a rejected removal — real or previewed — has torn down
    # nothing. Reporting a refusal is not a mutation, so checking it ahead of
    # the dry-run return keeps dry-run non-mutating while making a would-be
    # refusal visible instead of silently promising a removal that would
    # actually be blocked.
    #
    # A workspace that still holds a resumable session is a conversation this
    # removal would destroy irreversibly. Nothing is stored — the answer is
    # recomputed from the harness's own two seams each time — and an
    # enumeration camp could not complete is a REFUSAL, never an empty answer.
    resolved_env = dict(env) if env is not None else dict(os.environ)
    if not force:
        from ..launch import teardown_guard
        from .session import _addressable_harnesses, _parsable_groups

        session_groups = _parsable_groups()
        try:
            transcripts, live = teardown_guard.gather_pool(
                _addressable_harnesses(session_groups), env=resolved_env
            )
            holding = teardown_guard.blocking_sessions(
                ws_dir,
                transcripts=transcripts,
                live_records=live,
                groups=session_groups,
                env=resolved_env,
            )
        except teardown_guard.EnumerationUnavailable as e:
            _die(
                f"camp remove: {e} — removal is irreversible, so camp refuses "
                "rather than assume the workspace is empty; re-run with --force "
                "to remove it anyway"
            )
        if holding:
            print(teardown_guard.render_block(slug, holding), file=sys.stderr)
            sys.exit(1)

    if dry_run:
        print(f"[dry-run] would remove worktree {slug!r} for group {group['group']['name']!r}",
              file=sys.stderr)
        return

    try:
        # ManifestError is an Exception; the single handler covers it.
        result = reconcile_break(group, slug, env=env, force=force)
    except Exception as e:
        _die(f"camp remove: {e}")

    removed = result.get("removed", [])
    errors = result.get("errors", [])

    # Per-member removal failures come back as status="ok_with_errors" with
    # the un-removed members still listed in the manifest. Reporting "removed" +
    # exit 0 here would tell a scripted caller teardown succeeded when it did not.
    # Surface the failures on stderr, suppress the success line, and exit nonzero.
    if result.get("status") == "ok_with_errors" or errors:
        if removed:
            print(
                f"camp remove: partially removed worktree {slug!r} "
                f"(removed: {', '.join(removed)})",
                file=sys.stderr,
            )
        else:
            print(f"camp remove: failed to remove worktree {slug!r}", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    print(
        f"camp remove: removed worktree {slug!r} ({', '.join(removed)})",
        file=sys.stderr,
    )

    # The caller's shell is now sitting in a deleted directory — hand it the
    # first member's repo_root as the ONLY stdout line so the shellenv wrapper
    # can cd there. Without the wrapper active (no CAMP_SHELL_INTEGRATION
    # marker) the printed path is inert; nudge once, same as `camp new`.
    if cwd_inside_ws and group["members"]:
        if "CAMP_SHELL_INTEGRATION" not in os.environ:
            print(
                '  tip: run eval "$(trailhead shellenv)" so `camp remove` returns '
                "you to the group repo automatically",
                file=sys.stderr,
            )
        print(str(group["members"][0]["repo_root"]))


def _cmd_rebase_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
    dry_run: bool,
) -> None:
    """camp rebase [--onto <branch>] [--name <slug>]"""
    import subprocess
    from ..spine import _consume_flag_value, _die
    from ..group.manifest import manifest_path_for, read_central_manifest

    filtered = list(args)
    onto = _consume_flag_value(filtered, "--onto") or "origin/main"
    slug = _slug_from_args_or_cwd(filtered, group, verb="rebase", env=env)

    group_name = group["group"]["name"]
    mpath = manifest_path_for(group_name, slug, env=env)

    try:
        mdata = read_central_manifest(mpath)
    except Exception as e:
        _die(f"camp rebase: {e}")

    if dry_run:
        print(f"[dry-run] would rebase worktree {slug!r} onto {onto}", file=sys.stderr)
        return

    errors = []
    for m in mdata.get("members", []):
        wt_path = Path(m["worktree_path"])
        if not wt_path.is_dir():
            continue
        r = subprocess.run(
            ["git", "-C", str(wt_path), "rebase", onto],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            errors.append(f"{m['name']}: {r.stderr.strip()}")

    if errors:
        for e in errors:
            print(f"camp rebase: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"camp rebase: {slug!r} rebased onto {onto}")
