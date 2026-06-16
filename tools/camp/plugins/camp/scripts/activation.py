"""Lazy member activation for camp — Slice 5.

camp enter <member>:
  (a) Ensures the member is ready — else raises MemberNotReadyError with a
      legible message: 'still provisioning' hint for pending, failure reason
      for failed.
  (b) Fires activation hooks IDEMPOTENTLY — tracks an 'activated' field in the
      manifest; re-enter is a cheap no-op (hooks not re-run), doc re-printed.
  (c) Prints the member's CLAUDE.md to stdout so the calling agent ingests it.

Hooks run shell=False (list-mode, D-F bootstrap-trust posture).
"""
from __future__ import annotations

import subprocess
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
    from manifest import read_central_manifest, write_central_manifest, reconcile_lock

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


def _print_member_doc(member_name: str, wt_path: Path) -> None:
    """Print the member's CLAUDE.md to stdout, or a fallback notice."""
    claude_md = wt_path / "CLAUDE.md"
    if claude_md.is_file():
        print(claude_md.read_text(), end="")
    else:
        print(
            f"# {member_name}\n\n"
            f"Member worktree activated: {wt_path}\n"
            f"(No CLAUDE.md found — consider adding one for session context.)\n"
        )


def enter_member(
    group: dict[str, Any],
    slug: str,
    member_name: str,
    *,
    env: dict[str, str] | None = None,
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
    from manifest import manifest_path_for, read_central_manifest

    mpath = manifest_path_for(group["group"]["name"], slug, env=env)
    data = read_central_manifest(mpath)

    entry = _find_member_entry(data, member_name)
    if entry is None:
        raise ValueError(
            f"camp enter: member {member_name!r} not found in manifest for slug {slug!r}"
        )

    state = entry.get("provision_state", "pending")
    if state == "pending":
        raise MemberNotReadyError(
            f"camp enter: member {member_name!r} is still provisioning — "
            f"run `camp status` to check progress or `camp setup --retry` to retry."
        )
    if state == "failed":
        reason = entry.get("reason", "(no reason recorded)")
        raise MemberNotReadyError(
            f"camp enter: member {member_name!r} provisioning failed: {reason}\n"
            f"  Run `camp setup --retry` to retry provisioning."
        )

    wt_path = Path(entry["worktree_path"])
    member_config = _find_member_config(group, member_name)

    # Idempotency: only fire hooks on first activation.
    already_activated = entry.get("activated", False)
    if not already_activated:
        if member_config:
            _run_hooks(member_config, wt_path)
        _mark_activated(mpath, member_name)

    _print_member_doc(member_name, wt_path)
