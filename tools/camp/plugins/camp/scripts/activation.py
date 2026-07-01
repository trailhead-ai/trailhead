"""Lazy member activation for camp.

camp enter <member>:
  (a) Ensures the member is ready — else raises MemberNotReadyError with a
      legible message: 'still provisioning' hint for pending, failure reason
      for failed.
  (b) Fires activation hooks IDEMPOTENTLY — tracks an 'activated' field in the
      manifest; re-enter is a cheap no-op (hooks not re-run), doc re-printed.
  (c) Prints the member's CLAUDE.md to stdout so the calling agent ingests it.

Hooks run shell=False (list-mode, bootstrap-trust posture).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


class MemberNotReadyError(Exception):
    """Raised when camp enter is called on a member that is not ready."""


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


def _mark_activated(mpath: Path, member_name: str) -> None:
    """Atomically set activated=True for the named member in the manifest."""
    from camp.group.manifest import read_central_manifest, write_central_manifest, reconcile_lock

    with reconcile_lock(mpath.parent):
        data = read_central_manifest(mpath)
        for m in data.get("members", []):
            if m.get("name") == member_name:
                m["activated"] = True
                break
        write_central_manifest(mpath, data)


def _run_hooks(member_config: dict[str, Any], wt_path: Path) -> None:
    """Fire each activation hook in order, shell=False."""
    hooks = member_config.get("hooks") or []
    for hook in hooks:
        cmd = hook["cmd"]
        subprocess.run(cmd, cwd=str(wt_path), check=True)


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
        from hooks_writer import has_inject_drain_hook

        if has_inject_drain_hook(workspace_dir):
            from inject import enqueue_doc

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


def enter_member(
    group: dict[str, Any],
    slug: str,
    member_name: str,
    *,
    env: dict[str, str] | None = None,
    profile: Any | None = None,
) -> None:
    """Activate a member for the current session.

    (a) Checks provision_state: raises MemberNotReadyError for pending or failed.
    (b) Fires activation hooks idempotently (skipped if already activated).
    (c) Prints the member's CLAUDE.md to stdout.

    Args:
        group:       Parsed group config dict.
        slug:        The workspace slug.
        member_name: The member name to activate.
        env:         Optional env override for state-dir resolution (hermetic tests).

    Raises:
        MemberNotReadyError: If the member is not in the 'ready' state.
        ValueError: If the member name is not found in the manifest.
    """
    from camp.group.manifest import manifest_path_for, read_central_manifest

    if profile is None:
        from harness_profile import resolve_harness_profile

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

    # Idempotency: only fire hooks on first activation.
    already_activated = entry.get("activated", False)
    if not already_activated:
        if member_config:
            _run_hooks(member_config, wt_path)
        _mark_activated(mpath, member_name)

    # The workspace dir is the parent of the member worktree
    # (<workspace>/<member>) — the inject queue lives at <workspace>/.camp/.
    workspace_dir = wt_path.parent
    _surface_member_doc(member_name, wt_path, workspace_dir, profile.inject)
