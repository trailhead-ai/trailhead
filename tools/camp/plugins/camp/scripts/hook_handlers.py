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
"""

from __future__ import annotations

import sys
from pathlib import Path


def _load_groups_silently() -> list | None:
    """Load all group configs from CAMP_CONFIG_DIR / trailhead.paths.config_dir("camp").

    Returns None (silently) in all cases that should be a no-op:
      - groups config dir absent entirely (cold start)
      - malformed config

    Returns an empty list if the config dir exists but has no .toml files.
    """
    try:
        import trailhead.paths as _paths
        from group_config import load_all_groups, GroupConfigError, GroupConfigNotFound
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
        from group_resolve import resolve_from_cwd
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
        from reconcile import reconcile_worktree

        reconcile_worktree(group, slug)
    except Exception as e:
        # Genuine failure in a valid member worktree — warn once, don't crash.
        sys.stderr.write(
            f"camp: reconcile failed for {slug!r} — run `camp {slug}` to retry ({e})\n"
        )
        sys.exit(0)

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
        from reconcile import reconcile_break, ReconcileError
        from manifest import ManifestError
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
