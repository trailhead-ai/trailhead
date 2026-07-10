"""Lazy member activation for camp.

camp activate <member>:
  (a) Ensures the member is ready — else raises MemberNotReadyError with a
      legible message: 'still provisioning' hint for pending, failure reason
      for failed.
  (b) Runs the member's activate-phase tasks IDEMPOTENTLY — tracks an
      'activated' field in the manifest; re-activate is a cheap no-op (tasks not
      re-run), doc re-printed. A required activate-task failure aborts activation
      and leaves the member NOT activated; an optional-task failure warns on
      stderr and activation proceeds. Task outcomes are recorded in the manifest's
      per-member `tasks` map (uniformly with provision-phase tasks) so
      `camp status` surfaces them regardless of phase.
  (c) Prints the member's CLAUDE.md to stdout so the calling agent ingests it.

Tasks run shell=False (list-mode, bootstrap-trust posture).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Activate-phase tasks run wherever the retired dep-install activation hooks ran.
ACTIVATE_PHASE = "activate"


class MemberNotReadyError(Exception):
    """Raised when camp activate is called on a member that is not ready."""


def _find_member_entry(data: dict[str, Any], member_name: str) -> dict[str, Any] | None:
    for m in data.get("members", []):
        if m.get("name") == member_name:
            return m
    return None


def _find_member_config(group: dict[str, Any], member_name: str) -> dict[str, Any] | None:
    for m in group.get("members", []):
        if m.get("name") == member_name:
            return m
    return None


def _mark_activated(
    mpath: Path, member_name: str, *, tasks: dict[str, Any] | None = None
) -> None:
    """Atomically set activated=True (and merge task states) for the named member.

    When `tasks` is given it is a per-task state map ({name: {"state": ...}})
    merged into the member's existing `tasks` map in the same locked write, so
    activate-phase task outcomes are recorded uniformly with provision-phase
    outcomes (both feed `camp status`).
    """
    from ..group.manifest import read_central_manifest, write_central_manifest, reconcile_lock

    with reconcile_lock(mpath.parent):
        data = read_central_manifest(mpath)
        for m in data.get("members", []):
            if m.get("name") == member_name:
                m["activated"] = True
                if tasks:
                    merged = m.get("tasks", {})
                    merged.update(tasks)
                    m["tasks"] = merged
                break
        write_central_manifest(mpath, data)


def _run_activate_tasks(
    member_config: dict[str, Any] | None,
    wt_path: Path,
    slug: str,
    member_name: str,
) -> dict[str, Any]:
    """Run the member's activate-phase tasks; return their manifest tasks map.

    Reuses the same config→runner→manifest adapters the provision path uses, so
    the persisted task-state shape and the optional-failure stderr warning are
    identical across both phases. A required-task failure raises TaskError
    (activation aborts before the member is marked activated); an optional-task
    failure is warned on stderr and tolerated. Returns the per-task state map to
    merge into the manifest.
    """
    if not member_config:
        return {}

    from .reconcile import (
        _adapt_task_steps,
        _build_task_context,
        _tasks_map_from_results,
        _warn_optional_task_failures,
    )
    from .tasks import run_member_tasks

    context = _build_task_context(
        repo_root=member_config["repo_root"],
        worktree=wt_path,
        slug=slug,
        member_name=member_name,
    )
    # The `activated` flag (not per-task run-once state) is the activate-path
    # idempotency gate, so every activate-phase task runs together on first
    # activation — pass an empty `completed` map so none are skipped here.
    results = run_member_tasks(
        _adapt_task_steps(member_config.get("tasks") or []),
        ACTIVATE_PHASE,
        context,
        {},
    )
    _warn_optional_task_failures(results, member_name)
    return _tasks_map_from_results(results)


def _member_doc_content(member_name: str, wt_path: Path) -> str:
    """Return the member's CLAUDE.md content, or a fallback notice."""
    claude_md = wt_path / "CLAUDE.md"
    if claude_md.is_file():
        return claude_md.read_text()
    return (
        f"# {member_name}\n\n"
        f"Member worktree activated: {wt_path}\n"
        f"(No CLAUDE.md found — consider adding one for session context.)\n"
    )


def _surface_member_doc(
    member_name: str,
    wt_path: Path,
    workspace_dir: Path,
    inject: str,
) -> None:
    """Surface the member doc via the resolved inject strategy.

    "stdout"      → print the full doc to stdout (universal floor, unchanged).
    "claude-hook" → only if the PostToolUse `inject --drain` hook is actually
                    installed in <workspace>/.claude/settings.json: enqueue the full
                    doc to the workspace inject queue and print a concise stdout
                    confirmation (the full doc loads on the next turn via the camp
                    PostToolUse hook — NOT dumped to stdout here). If the drain hook
                    is ABSENT, fall back to the stdout floor so the content still
                    reaches the agent (no false "will load via hook" claim).
    """
    doc = _member_doc_content(member_name, wt_path)

    if inject == "claude-hook":
        from ..harness.hooks_writer import has_inject_drain_hook

        if has_inject_drain_hook(workspace_dir):
            from ..harness.inject import enqueue_doc

            enqueue_doc(workspace_dir, doc)
            print(
                f"Entered `{member_name}`; its CLAUDE.md will load into context on the "
                f"next turn via the camp PostToolUse hook."
            )
            return
        print(
            f"camp: claude-hook inject strategy selected but no PostToolUse "
            f"`inject --drain` hook is installed in the workspace — printing "
            f"`{member_name}` CLAUDE.md to stdout instead.",
            file=sys.stderr,
        )

    print(doc, end="")


def activate_member(
    group: dict[str, Any],
    slug: str,
    member_name: str,
    *,
    env: dict[str, str] | None = None,
    profile: Any | None = None,
) -> None:
    """Activate a member for the current session.

    (a) Checks provision_state: raises MemberNotReadyError for pending or failed.
    (b) Runs activate-phase tasks idempotently (skipped if already activated).
    (c) Prints the member's CLAUDE.md to stdout.

    Args:
        group:       Parsed group config dict.
        slug:        The workspace slug.
        member_name: The member name to activate.
        env:         Optional env override for state-dir resolution (hermetic tests).

    Raises:
        MemberNotReadyError: If the member is not in the 'ready' state.
        TaskError: If a required activate-phase task fails (member stays
            not-activated).
        ValueError: If the member name is not found in the manifest.
    """
    from ..group.manifest import manifest_path_for, read_central_manifest

    if profile is None:
        from ..harness.profile import resolve_harness_profile

        profile = resolve_harness_profile(group)

    mpath = manifest_path_for(group["group"]["name"], slug, env=env)
    data = read_central_manifest(mpath)

    entry = _find_member_entry(data, member_name)
    if entry is None:
        raise ValueError(
            f"camp activate: member {member_name!r} not found in manifest for slug {slug!r}"
        )

    state = entry.get("provision_state", "pending")
    if state == "pending":
        raise MemberNotReadyError(
            f"camp activate: member {member_name!r} is still provisioning — "
            f"run `camp status` to check progress or `camp setup` to retry."
        )
    if state == "failed":
        reason = entry.get("reason", "(no reason recorded)")
        raise MemberNotReadyError(
            f"camp activate: member {member_name!r} provisioning failed: {reason}\n"
            f"  Run `camp setup` to retry provisioning."
        )

    wt_path = Path(entry["worktree_path"])
    member_config = _find_member_config(group, member_name)

    # Idempotency: only run activate-phase tasks on the first activation. The
    # `activated` flag is the gate (not per-task run-once state), so all
    # activate-phase tasks run together on first activation and never again.
    #
    # Inherited TOCTOU: this `activated` read is unlocked while _mark_activated's
    # write below takes the reconcile lock, so two concurrent first activations
    # could both run the tasks. Pre-existing (the single-hook path had the same
    # window); left as-is here rather than widened — activate tasks must be
    # convergent under a rare double-run.
    already_activated = entry.get("activated", False)
    if not already_activated:
        # A required-task failure raises TaskError here BEFORE the member is
        # marked activated, so activation aborts and re-activate retries.
        tasks_map = _run_activate_tasks(member_config, wt_path, slug, member_name)
        _mark_activated(mpath, member_name, tasks=tasks_map)

    # The workspace dir is the parent of the member worktree
    # (<workspace>/<member>) — the inject queue lives at <workspace>/.camp/.
    workspace_dir = wt_path.parent
    _surface_member_doc(member_name, wt_path, workspace_dir, profile.inject)
