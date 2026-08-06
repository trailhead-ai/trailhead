"""camp CLI top-level dispatch — the hand-rolled verb router.

Two orthogonal concerns, kept distinct:
  (a) The thin ``cli/camp`` shim puts ``plugins/camp/`` on ``sys.path`` (so the
      ``camp`` package and the plugin-root-level ``_bootstrap`` module resolve)
      then calls ``main()`` here.
  (b) ``main`` bootstraps ``trailhead.paths`` via ``_bootstrap`` before any
      command code that needs it runs — EXCEPT on the hidden inject route, which
      resolves the workspace from cwd and never touches trailhead.paths, so it
      stays near-free (no cold-subprocess walk, no spine module-load).

Group-aware command routing: ``main`` loads the group config from cwd (or a
``--group`` override) and routes lifecycle commands through the central manifest
+ reconcile functions; everything else falls through to the spine dispatcher.
The verb dispatch tables live in ``camp.workspace.verb_taxonomy`` (imported here,
a tiny pure-data module) so both entry points share one alias/disabled/legacy
resolution order.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Single source of truth for the verb dispatch tables. verb_taxonomy is a
# tiny pure-data module (no regex/subprocess/spine), so importing it at module
# load keeps the inject route light while letting the router share the tables.
from ..workspace.verb_taxonomy import (
    LEGACY_REDIRECTS as _LEGACY_REDIRECTS,
    bare_slug_message as _bare_slug_message,
    resolve_verb as _resolve_verb,
)

# This module lives at plugins/camp/camp/cli/dispatch.py; parents[2] is the
# plugin root (plugins/camp/), the same dir the shim inserts on sys.path. The
# binary the wrapper execs is <plugin_root>/cli/camp — _SELF resolves there so
# `camp --which` / `camp --version` still name the real binary post-split.
_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_SELF = _PLUGIN_ROOT / "cli" / "camp"
_BIN_DIR = _PLUGIN_ROOT / "bin"
_VERSION = "0.1.0"

# Set by main() once the inject route is classified: True on every non-inject
# command (bootstrap ran, trailhead.paths importable), False on the inject route
# (bootstrap skipped). _resolve_group_for_command consults it.
_TRAILHEAD_PATHS_OK = False


def _not_on_path_warning() -> None:
    """Print a one-time warning if this tool's bin/ dir is not on $PATH."""
    bin_dir_str = str(_BIN_DIR)
    path_dirs = os.environ.get("PATH", "").split(":")
    if not any(Path(p).resolve() == _BIN_DIR.resolve() for p in path_dirs if p):
        print(
            f"camp: note — {bin_dir_str} is not on $PATH.\n"
            f"  Add it: fish_add_path {bin_dir_str}",
            file=sys.stderr,
        )


def _resolve_group_for_command(argv: list[str]) -> tuple[dict | None, dict[str, str] | None]:
    """Attempt to load the group config for the current cwd or --group flag.

    Returns (group_config_dict, env) or (None, None) if not resolvable.

    Raises GroupConfigError if a config file is present but malformed — this is a
    hard failure that must surface to the user, not a silent fall-through to spine.
    A GroupResolutionError or missing config (no group resolves from cwd) returns
    (None, None) and lets spine handle the command.
    """
    if not _TRAILHEAD_PATHS_OK:
        return None, None

    try:
        from ..group.config import load_all_groups, GroupConfigError
        from ..group.resolve import (
            resolve_from_cwd,
            resolve_group_override,
            GroupConfinementError,
            GroupResolutionError,
        )
        from .common import _groups_dir
    except ImportError:
        return None, None

    # Check for --group flag
    group_override: str | None = None
    for i, arg in enumerate(argv):
        if arg == "--group" and i + 1 < len(argv):
            group_override = argv[i + 1]
            break
        if arg.startswith("--group="):
            group_override = arg[len("--group="):]
            break

    config_dir = _groups_dir()

    try:
        configs = load_all_groups(config_dir)
        if not configs:
            return None, None

        if group_override:
            group = resolve_group_override(group_override, configs)
        else:
            group_name, _ = resolve_from_cwd(Path.cwd(), configs)
            group = next(
                (c for c in configs if c["group"]["name"] == group_name), None
            )
            if group is None:
                return None, None

        return group, None  # env=None → use os.environ (resolver's default)
    except (GroupConfigError, GroupConfinementError):
        # Config exists but is malformed (bad TOML shape, or a group name that fails
        # the path-confinement charset check) — re-raise so the caller can surface it.
        raise
    except GroupResolutionError:
        # No group resolves from cwd / --group — fall through to spine.
        return None, None


def _slug_from_args_or_cwd(
    args: list[str],
    group: dict,
    *,
    verb: str,
    consume_positional: bool = False,
    allow_none: bool = False,
    env: dict[str, str] | None = None,
) -> str | None:
    """Resolve a slug from --name, an optional positional, or cwd.

    Consumes `--name <slug>` from args in place. If absent and consume_positional
    is set, takes args[0] as the slug. Otherwise resolves from cwd against the
    ALREADY-RESOLVED group (no reload of all configs). On no resolution, _die with
    a uniform message — unless allow_none, in which case None is returned (the
    caller falls back, e.g. status's fleet view).
    """
    from ..spine import _consume_flag_value, _resolve_slug, _die
    from ..group.resolve import resolve_from_cwd, GroupResolutionError

    name = _consume_flag_value(args, "--name")
    if name is not None:
        return _resolve_slug(name, context="--name")
    if consume_positional and args:
        return _resolve_slug(args[0], context="argument")

    try:
        # Thread env so the cwd slug resolution derives camp_state_dir from the
        # SAME env as the downstream manifest/workspace ops. resolve_from_cwd
        # derives state_dir("camp", env=env) when camp_state_dir is not supplied.
        _, slug = resolve_from_cwd(Path.cwd(), [group], env=env)
    except GroupResolutionError:
        slug = None
    if slug is None and not allow_none:
        _die(
            f"camp {verb}: could not determine slug from cwd — "
            f"pass --name <slug> or run from inside a workspace directory"
        )
    return slug


def main() -> None:
    global _TRAILHEAD_PATHS_OK
    argv = sys.argv[1:]

    # The hidden `camp inject --drain` PostToolUse hook fires on EVERY Bash tool
    # call. It resolves the workspace from cwd and never touches spine or
    # trailhead.paths, so keep it near-free: detect the inject route BEFORE the
    # cold-subprocess ensure_trailhead_importable() walk and skip it. The bootstrap
    # still runs unconditionally for every OTHER command (behavior unchanged).
    inject_route = bool(argv) and argv[0] == "inject"

    # Bootstrap trailhead.paths before any command code runs. _bootstrap walks up
    # from the plugin root to the monorepo root automatically, so this works on a
    # fresh git clone without any pip install. Skipped for the inject route.
    if not inject_route:
        from _bootstrap import ensure_trailhead_importable

        ensure_trailhead_importable()
    _TRAILHEAD_PATHS_OK = not inject_route

    # Print not-on-PATH warning when invoked with no args
    if not argv:
        _not_on_path_warning()

    # Handle meta-flags before dispatch (--version / --which)
    if argv and argv[0] in ("--version", "version"):
        from .status import _cmd_version
        _cmd_version()
        return

    if argv and argv[0] == "--which":
        from .status import _cmd_which
        _cmd_which()
        return

    # Strip --dry-run for command dispatch (spine re-checks it)
    dry_run = "--dry-run" in argv or bool(os.environ.get("CAMP_DRY_RUN"))

    first = argv[0] if argv else None

    # ---------------------------------------------------------------------------
    # Hook handler subcommands (session-bootstrap, worktree-cleanup)
    # These run before group resolution — they handle their own silent no-op logic.
    # ---------------------------------------------------------------------------
    if first == "session-bootstrap":
        from ..launch.hook_handlers import cmd_session_bootstrap
        cmd_session_bootstrap()
        return

    if first == "worktree-cleanup":
        from ..launch.hook_handlers import cmd_worktree_cleanup
        force = "--force" in argv[1:]
        cmd_worktree_cleanup(force=force)
        return

    # Hidden inject hook handler (PostToolUse → drain the inject queue).
    # Runs before group resolution; resilient (drain_queue never crashes a tool call).
    if first == "inject":
        from .inject import _cmd_inject_cli
        _cmd_inject_cli(argv[1:])
        return

    # 'group' is the new name for 'init'; 'init' redirects to 'group'.
    if first == "group":
        from .group import _cmd_group_cli
        _cmd_group_cli(argv[1:])
        return

    if first == "init":
        from ..spine import cmd_legacy_redirect
        cmd_legacy_redirect("init", "group")
        return

    # ---------------------------------------------------------------------------
    # Group-aware command routing
    # ---------------------------------------------------------------------------
    _SKIP_GROUP_RESOLVE = frozenset({
        "help", "--help", "-h", "doctor",
        "foreach", "path", "which",
        "--version", "version",
    })
    if first and first not in _SKIP_GROUP_RESOLVE:
        try:
            group, group_env = _resolve_group_for_command(argv)
        except Exception as _cfg_err:
            # GroupConfigError: config is present but malformed — surface and exit.
            print(f"camp: config error: {_cfg_err}", file=sys.stderr)
            sys.exit(1)
        if group is not None:
            _dispatch_group_command(first, argv[1:], group, group_env, dry_run)
            return

    # Delegate everything else to the spine dispatcher (fallback / non-group cmds).
    # Imported lazily so the early-returning inject route never pays the
    # spine module-load cost.
    from ..spine import main as _spine_main
    _spine_main()


def _dispatch_group_command(
    cmd: str,
    rest: list[str],
    group: dict,
    group_env: dict[str, str] | None,
    dry_run: bool,
) -> None:
    """Dispatch a group-aware command."""
    from ..spine import (
        _die,
        cmd_disabled,
        cmd_legacy_redirect,
        RESERVED,
    )
    from .group import _cmd_new_group_cli
    from .lifecycle import (
        _cmd_remove_group_cli,
        _cmd_setup_group_cli,
        _cmd_sync_group_cli,
        _cmd_rebase_group_cli,
    )
    from .workspace import (
        _cmd_activate_group_cli,
        _cmd_pwd_group_cli,
        _cmd_ls_group_cli,
    )
    from .status import _cmd_status_group_cli

    # One resolver classifies alias→disabled→legacy in a single defined order,
    # shared with spine.main, so a token routes identically at both entry points
    # (previously cli/camp checked disabled/legacy BEFORE the alias table and
    # spine checked them AFTER — a future colliding alias would diverge).
    # 'init' is intercepted earlier in main(); 'open'/'break'/'ai'/'enter' are the
    # legacy redirects reachable on the group-aware path.
    cmd, kind = _resolve_verb(cmd)
    if kind == "disabled":
        cmd_disabled(cmd)
        return
    if kind == "legacy":
        cmd_legacy_redirect(cmd, _LEGACY_REDIRECTS[cmd])
        return

    # Canonical verb surface.
    if cmd == "new":
        _cmd_new_group_cli(rest, group, group_env, dry_run)
        return
    if cmd == "remove":
        _cmd_remove_group_cli(rest, group, group_env, dry_run)
        return
    if cmd == "setup":
        _cmd_setup_group_cli(rest, group, group_env, dry_run)
        return
    if cmd == "activate":
        _cmd_activate_group_cli(rest, group, group_env)
        return
    if cmd == "pwd":
        _cmd_pwd_group_cli(rest, group, group_env)
        return
    if cmd == "bookmark":
        from ..bookmark.capture import cmd_bookmark
        from ..bookmark.render import cmd_bookmark_ls, cmd_bookmark_rm
        from ..spine import _consume_flag_value

        if rest and rest[0] == "ls":
            sub_rest = rest[1:]
            _consume_flag_value(sub_rest, "--group")  # already resolved upstream; drop it
            cmd_bookmark_ls(sub_rest, group, group_env)
        elif rest and rest[0] == "rm":
            sub_rest = rest[1:]
            _consume_flag_value(sub_rest, "--group")  # already resolved upstream; drop it
            cmd_bookmark_rm(sub_rest, group, group_env)
        else:
            sub_rest = list(rest)
            _consume_flag_value(sub_rest, "--group")  # already resolved upstream; drop it
            cmd_bookmark(sub_rest, group, group_env)
        return
    if cmd == "resume":
        from ..bookmark.resume import cmd_resume

        cmd_resume(rest, group, group_env)
        return

    # Bare slug removed: any non-RESERVED token that isn't a known verb → error
    # (shared message, defined in verb_taxonomy).
    if cmd not in RESERVED:
        _die(_bare_slug_message(cmd))
        return

    if cmd == "status":
        _cmd_status_group_cli(rest, group, group_env, dry_run)
    elif cmd == "list":
        _cmd_ls_group_cli(rest, group, group_env)
    elif cmd == "sync":
        _cmd_sync_group_cli(rest, group, group_env, dry_run)
    elif cmd == "rebase":
        _cmd_rebase_group_cli(rest, group, group_env, dry_run)
    else:
        # Fall through to spine for non-group commands
        from ..spine import main as _spine_main
        _spine_main()
