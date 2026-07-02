"""The group command group: ``group`` (author/wire a config) and ``new`` (create a workspace).

``group`` has three modes (wire hooks for an existing config, author a config
from ``--member`` flags then wire, or write a ``--scaffold`` stub); ``new`` seeds
a workspace directory + manifest and spawns the detached background provisioner.
Both are the *creation* verbs — everything that acts on an already-created
workspace lives in ``workspace`` / ``lifecycle``.
"""
from __future__ import annotations

import os
import sys

from .dispatch import _BIN_DIR


_GROUP_HELP = """\
usage: camp group <name> [options]

Three modes (every non-default mode requires an explicit flag):

  camp group <name>
      Wire SessionStart hook entries into each member's
      .claude/settings.json for an already-configured group.

  camp group <name> --member NAME=PATH [--member NAME=PATH ...] [options]
      Author a group config TOML from flags, then wire hooks.
      Refuses to overwrite an existing config unless --force.

  camp group <name> --scaffold
      Write a commented stub config you edit by hand, then re-run with
      --member. Does not wire hooks.

Options:
  --member NAME=PATH   Repeatable. Add a member (split on the first '=').
  --branch-pattern P   Branch pattern for authored config (default
                       'worktree-{slug}').
  --force              Overwrite an existing config file.
  --allow-missing      Skip the repo_root existence/git check when authoring.
  --scaffold           Write a commented stub config and stop (no hooks).
"""


def _parse_init_args(args: list[str]) -> dict:
    """Parse `camp init` args into {group_name, members, branch_pattern, force,
    allow_missing, scaffold}. Exits non-zero on malformed input."""
    members: list[dict[str, str]] = []
    branch_pattern = "worktree-{slug}"
    force = False
    allow_missing = False
    scaffold = False
    group_name: str | None = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--member":
            if i + 1 >= len(args):
                print("camp group: --member requires a NAME=PATH value", file=sys.stderr)
                sys.exit(1)
            members.append(_parse_member(args[i + 1]))
            i += 2
        elif arg.startswith("--member="):
            members.append(_parse_member(arg[len("--member="):]))
            i += 1
        elif arg == "--branch-pattern":
            if i + 1 >= len(args):
                print("camp group: --branch-pattern requires a value", file=sys.stderr)
                sys.exit(1)
            branch_pattern = args[i + 1]
            i += 2
        elif arg.startswith("--branch-pattern="):
            branch_pattern = arg[len("--branch-pattern="):]
            i += 1
        elif arg == "--force":
            force = True
            i += 1
        elif arg == "--allow-missing":
            allow_missing = True
            i += 1
        elif arg == "--scaffold":
            scaffold = True
            i += 1
        elif arg.startswith("-"):
            print(f"camp group: unknown flag {arg!r}", file=sys.stderr)
            sys.exit(1)
        else:
            if group_name is not None:
                print(f"camp group: unexpected argument {arg!r}", file=sys.stderr)
                sys.exit(1)
            group_name = arg
            i += 1

    if group_name is None:
        print("camp group: a group name is required", file=sys.stderr)
        sys.exit(1)

    return {
        "group_name": group_name,
        "members": members,
        "branch_pattern": branch_pattern,
        "force": force,
        "allow_missing": allow_missing,
        "scaffold": scaffold,
    }


def _parse_member(raw: str) -> dict[str, str]:
    """Parse a NAME=PATH member spec, splitting on the FIRST '=' only.

    Rejects empty NAME or empty PATH. Exits non-zero with a legible error.
    """
    if "=" not in raw:
        print(
            f"camp group: malformed --member {raw!r} — expected NAME=PATH",
            file=sys.stderr,
        )
        sys.exit(1)
    name, path = raw.split("=", 1)
    if not name:
        print(
            f"camp group: malformed --member {raw!r} — member NAME must not be empty",
            file=sys.stderr,
        )
        sys.exit(1)
    if not path:
        print(
            f"camp group: malformed --member {raw!r} — member PATH must not be empty",
            file=sys.stderr,
        )
        sys.exit(1)
    return {"name": name, "repo_root": path}


def _cmd_group_cli(args: list[str]) -> None:
    """camp group <name> — wire hooks, author a config from flags, or write a stub."""
    from ..group.config import load_all_groups, GroupConfigError
    from ..group.resolve import resolve_group_override, GroupResolutionError
    from ..workspace.init import run_init
    from ..group import scaffold as group_scaffold
    from .common import _groups_dir

    if not args or args[0] in ("--help", "-h"):
        print(_GROUP_HELP)
        return

    parsed = _parse_init_args(args)
    group_name = parsed["group_name"]
    config_dir = _groups_dir()
    config_path = config_dir / f"{group_name}.toml"

    # --- Mode (c): --scaffold (and no --member) → write a stub, stop ---
    if parsed["scaffold"] and not parsed["members"]:
        if config_path.exists() and not parsed["force"]:
            print(
                f"camp group: config {config_path!s} already exists — pass --force to overwrite",
                file=sys.stderr,
            )
            sys.exit(1)
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path.write_text(group_scaffold.build_stub_toml(group_name))
        print(f"camp group: wrote stub {config_path!s}")
        print(f"  edit this then re-run `camp group {group_name} --member NAME=PATH ...`")
        return

    # --- Mode (a): --member present → author config (atomic) then wire hooks ---
    if parsed["members"]:
        _author_group(
            group_name,
            parsed["members"],
            parsed["branch_pattern"],
            force=parsed["force"],
            allow_missing=parsed["allow_missing"],
            config_dir=config_dir,
            config_path=config_path,
        )
        # Re-load all configs (now including the freshly written one) + wire hooks.
        try:
            all_configs = load_all_groups(config_dir)
            group = resolve_group_override(group_name, all_configs)
            run_init(group, str(_BIN_DIR / "camp"), group_configs=all_configs)
        except (GroupResolutionError, GroupConfigError) as e:
            print(f"camp group: {e}", file=sys.stderr)
            sys.exit(1)
        print(
            f"camp group: authored + wired group {group_name!r} "
            f"({len(group['members'])} member(s))"
        )
        return

    # --- Mode (b): no new flags → unchanged behavior (resolve + wire hooks) ---
    try:
        all_configs = load_all_groups(config_dir)
    except GroupConfigError as e:
        print(f"camp group: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        group = resolve_group_override(group_name, all_configs)
    except GroupResolutionError as e:
        print(f"camp group: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        run_init(group, str(_BIN_DIR / "camp"), group_configs=all_configs)
    except (GroupResolutionError, GroupConfigError) as e:
        print(f"camp group: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"camp group: hooks wired for group {group_name!r} ({len(group['members'])} member(s))")


def _cmd_new_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
    dry_run: bool,
) -> None:
    """camp new <slug> — create or re-enter a workspace.

    NEW slug: bring_up_workspace — synchronous seed (workspace dir + manifest with
    each member pending) + a DETACHED provisioner (camp setup --background) that runs
    the actual git work asynchronously. EXISTING workspace: re-enter without
    re-provisioning or clobbering the manifest.

    Output contract: the workspace ABSOLUTE PATH is the ONLY thing on stdout —
    exactly one line, no trailing whitespace — so a shell `camp()` wrapper can
    `cd "$(camp new …)"`. The created/entered confirmation goes to stderr. Exit 0 on
    success; on seed/provision failure exit nonzero with a stderr message and EMPTY
    stdout.

    No session lock, no harness launch, no synchronous activation: provisioning is
    async (check it with `camp status <slug>`) and activation is deferred — the
    workspace activates when ready / via `camp activate <slug>`. That next-step
    guidance is part of the stderr confirmation so the user is not stranded.
    """
    from ..spine import _resolve_slug, _consume_flag_value, _die
    from ..provision.provision import bring_up_workspace
    from ..group.manifest import workspace_dir, manifest_path_for

    rest = list(args)
    _consume_flag_value(rest, "--group")  # already resolved upstream; drop it

    if not rest:
        print("camp new: a slug is required\n  usage: camp new <slug>", file=sys.stderr)
        sys.exit(1)

    slug = _resolve_slug(rest[0], context="new")

    group_name = group["group"]["name"]
    if not group["members"]:
        _die(f"camp new: group {group_name!r} has no members")

    ws_dir = workspace_dir(group_name, slug, env=env)

    if dry_run:
        print(
            f"[dry-run] would seed + spawn detached provisioner for {slug!r}",
            file=sys.stderr,
        )
        return

    # Re-enter vs (re)provision keys on MANIFEST presence, not ws_dir.exists().
    # A crash between seed_pending_workspace's ws_dir.mkdir and its manifest
    # write leaves a manifest-less workspace dir; keying on ws_dir.exists() would
    # then re-enter that broken dir forever (success+exit 0 into an empty dir) and
    # `camp remove` would die on its fail-fast manifest read — unrecoverable via the
    # CLI. Keying on the manifest re-seeds such a partial dir instead (bring_up is
    # idempotent: seed_pending_workspace mkdir's exist_ok and merges prior states).
    if manifest_path_for(group_name, slug, env=env).exists():
        # Healthy existing workspace: re-enter without re-provisioning or clobbering.
        headline = f"re-entered workspace {slug!r}"
    else:
        try:
            bring_up_workspace(group, slug, env=env)
        except Exception as e:
            _die(f"camp new: workspace bring-up failed: {e}")
        headline = f"created workspace {slug!r} — provisioning in the background"

    # Shared next-step guidance: the headline differs per branch, the two
    # follow-up lines are identical, so emit them once.
    print(
        f"camp new: {headline}\n"
        f"  check provisioning: camp status {slug}\n"
        f"  activates when ready, or run: camp activate {slug}",
        file=sys.stderr,
    )

    # Nudge the user to install the trailhead shellenv `camp()` wrapper
    # so `camp new` auto-cd's the parent shell. The wrapper exports
    # CAMP_SHELL_INTEGRATION around its `camp new` call — when that marker is set the
    # wrapper is active and will cd for the user, so we stay quiet. When it is absent
    # this is a bare-binary run: the path printed below is the user's only handle.
    if "CAMP_SHELL_INTEGRATION" not in os.environ:
        print(
            '  tip: run eval "$(trailhead shellenv)" so `camp new` cd\'s you in '
            "automatically",
            file=sys.stderr,
        )

    # The workspace abs path is the ONLY thing on stdout: exactly one line, no
    # trailing whitespace (print() appends the single newline).
    print(str(ws_dir))


def _author_group(
    group_name: str,
    members: list[dict[str, str]],
    branch_pattern: str,
    *,
    force: bool,
    allow_missing: bool,
    config_dir,
    config_path,
) -> None:
    """Validate + atomically write a group config TOML. Exits non-zero on failure."""
    from ..group.config import load_all_groups, load_group, GroupConfigError
    from ..group.resolve import GroupConfinementError
    from ..group import scaffold as group_scaffold

    if config_path.exists() and not force:
        print(
            f"camp group: config {config_path!s} already exists — pass --force to overwrite",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load other groups, EXCLUDING the target group by name so a --force redefine
    # does not self-collide against its own prior config in validate_no_overlap.
    try:
        all_configs = load_all_groups(config_dir)
    except GroupConfigError as e:
        print(f"camp group: {e}", file=sys.stderr)
        sys.exit(1)
    other_configs = [c for c in all_configs if c["group"]["name"] != group_name]

    # render_group_toml knows only the core schema, so a --force re-author would
    # otherwise drop a hand-added [[lore_scopes]] binding. Carry the existing
    # group's binding (if any) through so it round-trips instead of being lost.
    existing_group = next(
        (c for c in all_configs if c["group"]["name"] == group_name), None
    )
    existing_lore_scopes = existing_group.get("lore_scopes", []) if existing_group else []

    try:
        group_scaffold.validate_scaffold(
            group_name,
            members,
            other_configs=other_configs,
            allow_missing=allow_missing,
        )
    except (group_scaffold.ScaffoldError, GroupConfinementError) as e:
        print(f"camp group: {e}", file=sys.stderr)
        sys.exit(1)

    rendered = group_scaffold.render_group_toml(
        group_name, members, branch_pattern, lore_scopes=existing_lore_scopes
    )

    # Atomic write: tmp → round-trip gate via load_group → os.replace.
    config_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp_path.write_text(rendered)
    try:
        load_group(tmp_path)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        print(f"camp group: rendered config failed round-trip validation: {e}", file=sys.stderr)
        sys.exit(1)
    os.replace(str(tmp_path), str(config_path))
