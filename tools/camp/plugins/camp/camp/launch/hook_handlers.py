"""Hook handlers for camp: session-bootstrap and worktree-cleanup.

  SessionStart   → camp session-bootstrap   (wired by hooks_writer into member
                   .claude/settings.json)
  worktree-cleanup                            (retained + invocable, but NOT
                   auto-wired: the WorktreeRemove wiring was dropped — camp owns
                   teardown via `camp rm`. Kept for direct invocation / vanilla use.)

session-bootstrap is silent-exit-0 in all no-op cases (cold start, not a member,
malformed config, slug=None) because it fires at EVERY SessionStart in EVERY repo
— including ones that never ran camp init. All no-op cases exit 0 with empty
stderr so they don't pollute session start for the common case of a non-camp repo.

session-bootstrap calls reconcile_worktree synchronously, which bounds each
member's provision-phase TASKS by a tight boot budget
(provision.reconcile.BOOT_TASK_BUDGET_SECONDS) — git fetch and `worktree add`
keep their own, larger timeouts and are outside that budget. A task that
exceeds it comes back state="over-budget" rather than raising, so this handler
still exits 0; reconcile_worktree prints one actionable stderr line the first
time a task goes over budget (naming the task and the provision-phase
contract it violates) and never again for that task, since a task already
recorded over-budget is skip-worthy on every later reconcile_worktree call —
"once" is backed by that persisted manifest state, not anything this process
remembers. `camp setup` still retries an over-budget task.

After reconcile, session-bootstrap emits the SessionStart capability report
(see capability_report below) naming what the agent currently cannot do —
never an all-clear, never a task state, never a task's raw stdout/stderr.
`.mcp.json` is read by the harness before this hook ever fires (no reload
path exists), so this report can never repair MCP config for the session it
runs in — it can only tell the agent what is currently unavailable so it
adapts, and (via the notice the inject drain delivers later) tell it again
once that changes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ceiling on the capability report's length — it lands in every session's
# context at every start, so its length is a permanent tax. A concrete
# constant so a future edit that grows the report has something to fail
# against, rather than a floating "keep it short" intention.
CAPABILITY_REPORT_MAX_CHARS = 1000


def _load_groups_silently() -> list | None:
    """Load all group configs from CAMP_CONFIG_DIR / trailhead.paths.config_dir("camp").

    Returns None (silently) in all cases that should be a no-op:
      - groups config dir absent entirely (cold start)
      - malformed config

    Returns an empty list if the config dir exists but has no .toml files.
    """
    try:
        import trailhead.paths as _paths
        from ..group.config import load_all_groups, GroupConfigError, GroupConfigNotFound
    except ImportError:
        # trailhead not importable — cold start / bare clone.  Silent no-op.
        return None

    try:
        config_dir = _paths.config_dir("camp") / "groups"
    except Exception:
        return None

    if not config_dir.is_dir():
        # Cold start: groups config dir absent entirely → silent no-op.
        return None

    try:
        return load_all_groups(config_dir)
    except (GroupConfigError, GroupConfigNotFound, Exception):
        # Malformed config → silent no-op.
        return None


def _resolve_group_slug_silently(cwd: Path, group_configs: list) -> tuple[dict | None, str | None]:
    """Resolve (group, slug) from cwd; return (None, None) for all silent-no-op cases.

    Silent no-op cases:
      - cwd not a member of any group
      - slug=None (cwd is a repo root, not a worktree)
      - resolution error of any kind
    """
    try:
        from ..group.resolve import resolve_from_cwd
    except ImportError:
        return None, None

    try:
        group_name, slug = resolve_from_cwd(cwd, group_configs)
    except Exception:
        # cwd not in any group, overlap error, etc. → silent no-op.
        return None, None

    if slug is None:
        # cwd is a repo root (fleet-view), nothing to reconcile.
        return None, None

    # Find the full group config
    group = next(
        (c for c in group_configs if c["group"]["name"] == group_name),
        None,
    )
    return group, slug


def _member_capability_lines(
    name: str, member_config: dict, report_member: dict, slug: str
) -> list[str]:
    """Capability-consequence lines for one member's outstanding/failed
    work-enabling tasks — stated as what the agent cannot do and what to do
    instead, never as the raw task state, and never carrying the task's raw
    stdout/stderr (which may hold credentials on failure) — only where to
    read it.

    An outstanding task with a config-declared `capability` string uses that
    text verbatim in place of the generic line — the config author states the
    concrete consequence ("the code-review-graph MCP server has no graph yet
    — prefer Grep/Glob until told otherwise") rather than the agent having to
    infer it from a task name. A task with no `capability` declared falls
    back to the generic state-based line.
    """
    from ..group.config import tasks_in_phase
    from ..provision.activation import ACTIVATE_PHASE

    states = report_member.get("tasks") or {}
    failed: list[dict] = []
    outstanding: list[dict] = []
    for task in tasks_in_phase(member_config, ACTIVATE_PHASE):
        state = (states.get(task["name"]) or {}).get("state")
        if state == "failed":
            failed.append(task)
        elif state != "ok":
            outstanding.append(task)

    lines: list[str] = []
    for task in failed:
        # Deliberately does not point at `camp status --name <slug> --json`:
        # that surface carries the task's unredacted stderr_excerpt, which is
        # known to include credentials on failure (e.g. a private-registry
        # auth failure during `npm ci`). `camp status --name <slug>` (no
        # --json) still names which task failed, without that raw content.
        lines.append(
            f"{name}: the work-enabling task '{task['name']}' failed — treat anything that "
            f"depends on it as broken setup, not a bug in your change. Its output isn't "
            f"available in this context; ask the operator to check `camp status "
            f"--name {slug}` or re-run `camp setup` to retry before assuming a code issue."
        )
    for task in outstanding:
        capability = task.get("capability")
        if capability:
            lines.append(f"{name}: {capability}")
        else:
            # Deliberately does not point at `camp status --name <slug> --json`:
            # see the identical note above the failed-task line — that surface
            # carries the task's unredacted stderr_excerpt.
            lines.append(
                f"{name}: the work-enabling task '{task['name']}' has not finished yet — commands "
                f"or tools that depend on it may fail or behave as unset until it completes; "
                f"its output isn't available in this context; ask the operator to check `camp "
                f"status --name {slug}` or re-run `camp setup` before treating that as a code "
                f"problem."
            )
    return lines


def _summarized_capability_report(count: int, slug: str) -> str:
    """One-line summary used when the full per-task report would exceed
    CAPABILITY_REPORT_MAX_CHARS. Whole, complete sentences only — an agent
    must never receive a half-statement about what it cannot do.
    """
    return (
        f"{count} work-enabling tasks are outstanding or failed across this workspace — their "
        f"output isn't available in this context; ask the operator to check `camp status "
        f"--name {slug}` or re-run `camp setup` before assuming a code issue."
    )


def capability_report(group: dict, slug: str, *, env: dict[str, str] | None = None) -> str:
    """Build the SessionStart capability-report text for (group, slug), or ""
    when every member's work-enabling tasks are done (or none are declared).

    Reads the manifest FRESH on every call via provision_status_code — never
    cached — so the report reflects live state at read time: a session that
    reads this twice across a settle sees the change. A stale report is worse
    than none, in both directions (told-missing-but-arrived avoids working
    tools; told-present-but-missing misreads an install failure as a code bug).

    States capability consequences ("commands depending on it may fail until
    it finishes"), never a raw task state, and never a task's raw
    stdout/stderr — only where to read it. Bounded to
    CAPABILITY_REPORT_MAX_CHARS; an overflow degrades to one summarizing
    sentence rather than truncating a per-task statement mid-way. Never
    raises — any internal failure returns "" so a broken report can't crash
    session start.
    """
    try:
        from ..provision.lifecycle import provision_status_code

        _, status_report = provision_status_code(group, slug, env=env)

        member_configs = {m["name"]: m for m in group.get("members", [])}
        lines: list[str] = []
        for report_member in status_report.get("members", []):
            member_name = report_member["name"]
            lines.extend(
                _member_capability_lines(
                    member_name, member_configs.get(member_name, {}), report_member, slug
                )
            )

        if not lines:
            return ""

        report = "\n".join(lines)
        if len(report) > CAPABILITY_REPORT_MAX_CHARS:
            report = _summarized_capability_report(len(lines), slug)
        return report
    except Exception:
        return ""


def _emit_capability_report(group: dict, slug: str) -> None:
    """Print the SessionStart additionalContext payload for the capability
    report, if any. Silent when the report is empty — matches the inject
    drain's silent-empty-queue behaviour. Never raises: a failure while
    formatting/writing the payload is swallowed rather than crashing session
    start.
    """
    try:
        report = capability_report(group, slug)
        if not report:
            return
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": report,
                    }
                }
            )
        )
    except Exception:
        pass


def cmd_session_bootstrap() -> None:
    """camp session-bootstrap: idempotent reconcile of the current worktree.

    Called by the SessionStart hook in each member's .claude/settings.json.
    Silent exit-0 in all no-op cases.
    """
    cwd = Path.cwd()

    group_configs = _load_groups_silently()
    if group_configs is None:
        sys.exit(0)

    # Config dir present but empty → no-op.
    if not group_configs:
        sys.exit(0)

    group, slug = _resolve_group_slug_silently(cwd, group_configs)
    if group is None:
        sys.exit(0)

    # Reconcile (idempotent create-or-complete).
    try:
        from ..provision.reconcile import reconcile_worktree

        reconcile_worktree(group, slug)
    except Exception as e:
        # Genuine failure in a valid member worktree — warn once, don't crash.
        sys.stderr.write(
            f"camp: reconcile failed for {slug!r} — run `camp {slug}` to retry ({e})\n"
        )
        sys.exit(0)

    _emit_capability_report(group, slug)

    sys.exit(0)


def cmd_worktree_cleanup(*, force: bool = False) -> None:
    """camp worktree-cleanup: remove member worktrees + central manifest.

    Retained + directly invocable, but NOT auto-wired into any hook (the
    WorktreeRemove wiring was dropped — `camp rm` is the wired teardown path).
    Silent exit-0 when cwd is not a member of any known group (common case for
    non-camp repos).

    Raises SystemExit(1) with a message when:
      - A dirty worktree blocks removal (and force=False).
    """
    cwd = Path.cwd()

    group_configs = _load_groups_silently()
    if group_configs is None:
        sys.exit(0)

    if not group_configs:
        sys.exit(0)

    group, slug = _resolve_group_slug_silently(cwd, group_configs)
    if group is None:
        sys.exit(0)

    try:
        from ..provision.reconcile import reconcile_break, ReconcileError
        from ..group.manifest import ManifestError
    except ImportError:
        sys.exit(0)

    try:
        result = reconcile_break(group, slug, force=force)
    except ReconcileError as e:
        # Dirty worktree (or other named error) → exit 1 with message.
        sys.stderr.write(f"{e}\n")
        sys.exit(1)
    except ManifestError as e:
        sys.stderr.write(f"{e}\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"camp worktree-cleanup: {e}\n")
        sys.exit(1)

    removed = result.get("removed", [])
    if removed:
        print(f"camp: removed worktrees for slug {slug!r} ({', '.join(removed)})")

    sys.exit(0)
