"""The group command group: ``group`` (author/wire a config), ``groups`` (list
configs), and ``new`` (create a workspace).

``group`` has three modes (wire hooks for an existing config, author a config
from ``--member`` flags then wire, or write a ``--scaffold`` stub); ``groups``
is a read-only listing of every configured group; ``new`` seeds a workspace
directory + manifest and spawns the detached background provisioner. All three
are dispatched groupless, before any group resolves from cwd — everything that
acts on an already-created workspace lives in ``workspace`` / ``lifecycle``.
"""
from __future__ import annotations

import json
import os
import sys

from .dispatch import _BIN_DIR
from .parser import CampParser, group_verb_parser


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


#: The branch name camp derives for a workspace when the operator names no
#: pattern of their own. Declared here rather than inline as the parser's
#: default so `_GROUP_HELP` above and the parser quote the same string.
_DEFAULT_BRANCH_PATTERN = "worktree-{slug}"


def _build_group_parser() -> CampParser:
    """The `camp group` parser — the declared shape of every flag the verb takes.

    The group name is declared ``nargs="?"`` rather than required so that its
    absence refuses in camp's words ("a group name is required") instead of
    argparse's, which names the destination and reads like a stack trace. The
    check for it lives in `_parse_init_args` immediately below.
    """
    parser = CampParser(verb="group")
    # `default=None` rather than `default=[]`: before 3.13, an append action
    # mutates the default object it is given, so a list literal accumulates
    # members across every parse a single parser performs. camp supports 3.11.
    parser.add_argument("--member", action="append", metavar="NAME=PATH", default=None)
    parser.add_argument("--branch-pattern", default=_DEFAULT_BRANCH_PATTERN)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--scaffold", action="store_true")
    parser.add_argument("group_name", nargs="?")
    return parser


def _parse_init_args(args: list[str]) -> dict:
    """Parse `camp group` args into {group_name, members, branch_pattern, force,
    allow_missing, scaffold}. Exits non-zero on malformed input.

    Member specs are validated after the parse, not during it, so that a
    malformed ``NAME=PATH`` reports its own reason rather than argparse's
    generic invalid-value wording.
    """
    parser = _build_group_parser()
    parsed = parser.parse_args(args)

    if parsed.group_name is None:
        parser.die("a group name is required")

    return {
        "group_name": parsed.group_name,
        "members": [_parse_member(parser, spec) for spec in parsed.member or []],
        "branch_pattern": parsed.branch_pattern,
        "force": parsed.force,
        "allow_missing": parsed.allow_missing,
        "scaffold": parsed.scaffold,
    }


def _parse_member(parser: CampParser, raw: str) -> dict[str, str]:
    """Parse a NAME=PATH member spec, splitting on the FIRST '=' only.

    Rejects empty NAME or empty PATH. Refuses through *parser*, so a member spec
    the parser accepted but this validation rejects reads in the same voice as
    every other `camp group` refusal rather than reproducing the prefix here.
    """
    if "=" not in raw:
        parser.die(f"malformed --member {raw!r} — expected NAME=PATH")
    name, path = raw.split("=", 1)
    if not name:
        parser.die(f"malformed --member {raw!r} — member NAME must not be empty")
    if not path:
        parser.die(f"malformed --member {raw!r} — member PATH must not be empty")
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
        config_path.write_text(group_scaffold.build_stub_toml(group_name), encoding="utf-8")
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


def _cmd_groups_cli(args: list[str]) -> None:
    """camp groups [--json] — list every configured group: name + member names.

    Read-only and fully groupless: reads directly from the group config
    directory, the same source `load_group` reads, and never resolves a group
    from cwd or accepts `--group`. Runs from any directory, including outside
    every configured group's member repos.

    No groups configured (missing or empty groups dir) exits 0 with an empty
    list — `[]` in --json mode, a plain "no groups configured" line in human
    mode — never an error.

    A group config file that fails to load degrades that one entry with a
    stderr notice and is dropped from the listing, rather than failing the
    whole command (matching the best-effort degrade idiom used elsewhere in
    camp, e.g. provision.py's pretrust step) — one bad file must not hide every
    other configured group. Unparseable TOML is only the expected case: the
    glob also matches a directory wearing a `.toml` name, and reading the file
    can fail on non-UTF-8 bytes or on permissions, none of which may reach the
    operator as a traceback.

    That degrade covers load failures ONLY — a malformed, unreadable, or absent
    file. Anything else escaping the loader is a defect in the loader, and it
    propagates: reporting it as a bad config file would drop a healthy group
    from the listing and still exit 0, and this is the only group-enumeration
    surface camp offers, so a silently short listing reads as "that group does
    not exist".
    """
    from ..group.config import GroupConfigError, GroupConfigNotFound, load_group
    from .common import _groups_dir

    # Groupless by contract — `--group` is deliberately NOT declared here, so
    # passing it refuses rather than being silently ignored.
    parser = CampParser(verb="groups")
    parser.add_argument("--json", action="store_true")
    as_json = parser.parse_args(args).json

    entries: list[dict] = []
    groups_dir = _groups_dir()
    if groups_dir.is_dir():
        for toml_file in sorted(groups_dir.glob("*.toml")):
            try:
                config = load_group(toml_file)
            # The load failures, spelled out: a malformed file, a path that is
            # not a file at all (a directory wearing a `.toml` name), non-UTF-8
            # bytes (a UnicodeDecodeError, not an OSError), and an unreadable
            # file.
            except (
                GroupConfigError,
                GroupConfigNotFound,
                UnicodeDecodeError,
                OSError,
            ) as e:
                # First line only: a config error's message can carry a
                # multi-line first-run hint, which is noise inside a listing.
                # And only some of these name the file — the config errors
                # embed the path, a decode error from non-UTF-8 bytes names
                # nothing at all — so the notice supplies it when it is absent.
                message = str(e).strip()
                detail = message.splitlines()[0] if message else e.__class__.__name__
                if str(toml_file) not in detail:
                    detail = f"{toml_file}: {detail}"
                print(f"camp groups: {detail} — skipping", file=sys.stderr)
                continue
            entries.append(
                {
                    "name": config["group"]["name"],
                    "members": [m["name"] for m in config["members"]],
                }
            )
    entries.sort(key=lambda entry: entry["name"])

    if as_json:
        print(json.dumps(entries))
        return

    if not entries:
        print("no groups configured")
        return

    for entry in entries:
        print(f"{entry['name']}: {', '.join(entry['members'])}")


def _cmd_new_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
    dry_run: bool,
) -> None:
    """camp new <slug> [--no-attach] [--no-session] [--launch] [--activate]
    [--json] — create or re-enter a workspace, then go through the same
    door `camp attach` opens onto its tmux session.

    NEW slug: bring_up_workspace — synchronous seed (workspace dir + manifest with
    each member pending) + a DETACHED provisioner (camp setup --background) that runs
    the actual git work asynchronously. EXISTING workspace: re-enter without
    re-provisioning or clobbering the manifest.

    Output contract: the workspace ABSOLUTE PATH is the ONLY thing on stdout —
    exactly one line, no trailing whitespace. The created/entered confirmation
    and the door's own outcome line go to stderr. Exit 0 on success; on
    seed/provision failure exit nonzero with a stderr message and EMPTY stdout.

    No session lock and no synchronous activation: provisioning is async (check it
    with `camp status <slug>`) and activation is deferred — the workspace activates
    when ready / via `camp activate <slug>`. That next-step guidance is part of the
    stderr confirmation so the user is not stranded.

    Creating the workspace's tmux session and handing the terminal over is now
    unconditional — `_door_dispatch_for_new` below reuses the same seams and
    outcome type `camp attach`'s own door dispatch
    (`cli/session.py:_open_workspace_door`) is built from
    (`launch.workspace_session.create_workspace_session`,
    `launch.door.Created`/`Connected`/`render_human`/`render_json`,
    `host.handoff.door_argv`/`handoff`, `attach.prefix_warning.inside_multiplexer`)
    but reports on `camp new`'s own stream contract instead of reusing that
    function's print calls directly: the outcome line belongs on stderr here,
    where the door prints it to stdout for `camp attach`.

    `--no-attach` creates the workspace and its session but never touches the
    exec or `switch-client` seam — `attached` is reported `false`.

    `--no-session` is the escape hatch back to the pre-flip surface in full:
    it skips the door entirely (no tmux session, no `--json` object built from
    it) and reproduces exactly what `camp new` did before this task, including
    `--launch` spawning a detached harness session through the old launch
    engine and `--json` requiring `--launch` to mean anything.

    `--launch` is accepted and does nothing new outside `--no-session` — it
    prints one notice on stderr and takes the same door path the bare form
    already takes, so camp's own concierge skill and other existing callers
    keep working. `--json` no longer requires `--launch`: the door's object is
    the machine answer for the command itself.

    `--activate` triggers every member's activate-phase work at creation time —
    the non-interactive path to what `camp activate <member>` triggers
    interactively. It waits (bounded) for boot-readiness first — an
    activate-phase task runs inside a member's worktree, which does not
    exist before boot-readiness — but it never waits for the activate-phase
    work itself, which is what makes it non-blocking: `--activate`'s own wait
    is capped at the same cheap boot budget, never at the potentially-expensive
    work `camp activate` would otherwise trigger. It never changes the exit
    code or the stdout path. A member declaring no activate-phase task is a
    clean no-op. Without `--activate`, `camp new` triggers no activate-phase
    work at all — only provision-phase tasks run at creation, which is what
    keeps an expensive activate-phase task (e.g. a knowledge-graph build) from
    firing for every member of every new workspace. `--no-wait` (read only by
    `--activate` now — the door waits on nothing) skips its wait and says so.
    """
    from ..spine import _resolve_slug, _die
    from ..provision.provision import bring_up_workspace
    from ..group.manifest import workspace_dir, manifest_path_for

    parser = group_verb_parser("new", dry_run=True)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-attach", action="store_true")
    parser.add_argument("--no-session", action="store_true")
    parser.add_argument("slug", nargs="?")
    parsed = parser.parse_args(args)

    launch = parsed.launch
    no_wait = parsed.no_wait
    activate = parsed.activate
    as_json = parsed.json
    no_attach = parsed.no_attach
    no_session = parsed.no_session

    if parsed.slug is None:
        print("camp new: a slug is required\n  usage: camp new <slug>", file=sys.stderr)
        sys.exit(1)

    slug = _resolve_slug(parsed.slug, context="new")

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

    if no_session:
        # The escape hatch: reproduce exactly what `camp new` did before this
        # task, including `--launch`'s old detached-harness behaviour and
        # `--json`'s old dependency on it.
        launched_session = None
        if launch:
            from .session import launch_for_new, wait_for_provisioning

            if no_wait:
                print(
                    f"camp new: --no-wait — launching without waiting for provisioning; "
                    f"a later provisioning failure surfaces in `camp status {slug}`",
                    file=sys.stderr,
                )
                ready = True
            else:
                ready = wait_for_provisioning(group, slug, env=env)
            if ready:
                launched_session = launch_for_new(group, slug, env=env)

        if activate:
            from .session import trigger_activate_phase_work

            trigger_activate_phase_work(group, slug, env=env, wait=not no_wait)

        if as_json:
            if not launch:
                _die("camp new: --json requires --launch")
            print(
                json.dumps(
                    {
                        "workspace": str(ws_dir),
                        "session_id": launched_session.session_id if launched_session else None,
                        "tmux_name": launched_session.tmux_name if launched_session else None,
                        "account": launched_session.account if launched_session else None,
                        "account_binding": (
                            dict(launched_session.account_binding) if launched_session else None
                        ),
                    }
                )
            )
            return

        # The workspace abs path is the ONLY thing on stdout: exactly one
        # line, no trailing whitespace (print() appends the single newline).
        print(str(ws_dir))
        return

    if launch:
        print(
            "camp new: --launch is accepted but no longer needed — "
            "creating and attaching is now the default",
            file=sys.stderr,
        )

    if activate:
        from .session import trigger_activate_phase_work

        trigger_activate_phase_work(group, slug, env=env, wait=not no_wait)

    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not no_attach
    _door_dispatch_for_new(
        group_name=group_name,
        slug=slug,
        ws_dir=ws_dir,
        env=env,
        as_json=as_json,
        interactive=interactive,
    )


def _workspace_only_payload(
    *, slug: str, group_name: str, ws_dir, session_error: str
) -> dict:
    """The `--json` object for `camp new`'s workspace-only outcome — the
    workspace exists but its tmux session does not, whether tmux was
    unreachable or the create attempt itself failed. One outcome value
    covers both causes; no consumer needs to branch on which one happened,
    only on `session_error`, which carries tmux's own words wherever tmux
    supplied them. The sole place this key set is assembled — both call
    sites in `_door_dispatch_for_new` build the object here, never ad hoc.
    """
    return {
        "ok": True,
        "outcome": "workspace-only",
        "slug": slug,
        "group": group_name,
        "workspace_path": str(ws_dir),
        "tmux_session": None,
        "attached": False,
        "session_error": session_error,
    }


def _report_workspace_only(
    *,
    as_json: bool,
    slug: str,
    group_name: str,
    ws_dir,
    derived_name: str,
    session_error: str,
) -> None:
    """Report `camp new`'s workspace-only outcome and return — never exits
    and never raises. The workspace is real and usable, so this is success
    with a warning, not a refusal: exactly the workspace path on stdout (or
    the `--json` object replacing it), and a warning naming the unreached
    session on stderr under the plain form.
    """
    if as_json:
        print(
            json.dumps(
                _workspace_only_payload(
                    slug=slug,
                    group_name=group_name,
                    ws_dir=ws_dir,
                    session_error=session_error,
                )
            )
        )
        return

    print(str(ws_dir))
    print(f"camp new: warning — {derived_name} — {session_error}", file=sys.stderr)


def _door_dispatch_for_new(
    *,
    group_name: str,
    slug: str,
    ws_dir,
    env: dict[str, str] | None,
    as_json: bool,
    interactive: bool,
) -> None:
    """Create-or-connect the workspace's tmux session and, when *interactive*,
    hand the terminal over — `camp new`'s own door dispatch.

    Reuses every seam and pure value `camp attach`'s door dispatch
    (`cli/session.py:_open_workspace_door`) is built from —
    `launch.workspace_session.create_workspace_session`, the `Tmux` seam
    (`launch.stop.Tmux`, the same factory attribute attach's own tests
    monkeypatch), `launch.door`'s `Created`/`Connected`/`render_human`/
    `render_json`, and `host.handoff.door_argv`/`handoff` plus
    `attach.prefix_warning.inside_multiplexer` for the handover arm — but
    prints on `camp new`'s own stream contract: the workspace path already
    went to stdout as the caller's only stdout line, so a human-readable
    outcome goes to stderr instead of stdout, and `--json` prints the door's
    object as the ONE thing on stdout, replacing the path line, exactly as
    `camp attach --json` already does.

    Unlike `camp attach`'s own door, neither tmux-unreachable nor a failed
    create is a refusal here: the workspace was already created and is
    usable on disk, so both arms report it through `_report_workspace_only`
    and return, without touching the exec or `switch-client` seam.
    """
    from ..attach.prefix_warning import inside_multiplexer
    from ..host.handoff import door_argv, handoff
    from ..launch.door import Connected, Created, render_human, render_json
    from ..launch.naming import workspace_session_name
    from ..launch.stop import Tmux
    from ..launch.workspace_session import (
        WorkspaceSessionOutcome,
        create_workspace_session,
    )

    resolved_env = dict(env) if env is not None else dict(os.environ)
    tmux = Tmux()
    derived_name = workspace_session_name(group_name, slug)
    present = tmux.has_session(derived_name)

    if present is None:
        _report_workspace_only(
            as_json=as_json,
            slug=slug,
            group_name=group_name,
            ws_dir=ws_dir,
            derived_name=derived_name,
            session_error=(
                "tmux did not answer — run `camp list` to see what camp can still tell"
            ),
        )
        return

    if present:
        outcome_cls = Connected
    else:
        result = create_workspace_session(group_name, slug, ws_dir, env=resolved_env, tmux=tmux)
        if result.outcome is WorkspaceSessionOutcome.CREATED:
            outcome_cls = Created
        elif result.outcome is WorkspaceSessionOutcome.ALREADY_EXISTED:
            outcome_cls = Connected
        elif tmux.has_session(derived_name):
            # Unrecognised create failure — re-probe before refusing, mirroring
            # `_open_workspace_door`'s own locale-robustness against a reworded
            # or localised "duplicate session" stderr line.
            outcome_cls = Connected
        else:
            _report_workspace_only(
                as_json=as_json,
                slug=slug,
                group_name=group_name,
                ws_dir=ws_dir,
                derived_name=derived_name,
                session_error=f"failed to create workspace session — {result.error}",
            )
            return

    outcome = outcome_cls(
        slug=slug,
        group=group_name,
        tmux_session=derived_name,
        workspace_path=ws_dir,
        attached=interactive,
    )

    if as_json:
        print(json.dumps(render_json(outcome)))
    else:
        print(str(ws_dir))
        print(render_human(outcome), file=sys.stderr)

    if not interactive:
        return

    if inside_multiplexer(resolved_env):
        switched = tmux.switch_client(derived_name)
        sys.exit(switched.returncode if switched is not None else 1)

    handoff(door_argv(derived_name))
    # A real exec never returns on success — this line only runs when a
    # test's injected exec seam returns instead of replacing the process.
    sys.exit(0)


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
    import tomllib

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

    # render_group_toml also knows only the core schema for every OTHER
    # top-level table, so a --force re-author would otherwise silently drop
    # any hand-added table it doesn't itself render (e.g. [tasks.*],
    # [harness], [release], [[shared_vaults]]). Read the raw TOML directly
    # (not load_group's normalized shape, which reshapes/loses some of these)
    # and carry through every top-level table render_group_toml doesn't
    # already produce from its own parameters.
    existing_extra_tables: dict = {}
    existing_members_by_name: dict[str, dict] = {}
    if config_path.exists():
        raw_existing = tomllib.loads(config_path.read_text(encoding="utf-8"))
        core_keys = {"group", "members", "branch", "lore_scopes"}
        existing_extra_tables = {k: v for k, v in raw_existing.items() if k not in core_keys}

        # render_group_toml's [[members]] loop only ever knew "name" and
        # "repo_root", so a --force re-author would otherwise silently drop a
        # member's hand-authored/legacy "base", "tasks" reference list,
        # "hooks", and "bootstrap" fields. Read the raw (not load_group's
        # resolved-tasks) member tables and carry those fields through by
        # member name, mirroring the extra_tables carry-through above.
        for raw_member in raw_existing.get("members", []):
            if not isinstance(raw_member, dict):
                continue
            name = raw_member.get("name")
            if not isinstance(name, str):
                continue
            carried = {
                k: v for k, v in raw_member.items() if k in ("base", "tasks", "hooks", "bootstrap")
            }
            if carried:
                existing_members_by_name[name] = carried

    # Merge the carried per-member fields (by name) into the freshly-parsed
    # --member NAME=PATH list. A brand-new member (not present in the prior
    # config) has no entry here, so it keeps render_group_toml's own defaults.
    members = [
        {**m, **existing_members_by_name.get(m["name"], {})} for m in members
    ]

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

    try:
        rendered = group_scaffold.render_group_toml(
            group_name,
            members,
            branch_pattern,
            lore_scopes=existing_lore_scopes,
            extra_tables=existing_extra_tables,
        )
    except group_scaffold.ScaffoldError as e:
        print(f"camp group: {e}", file=sys.stderr)
        sys.exit(1)

    # Atomic write: tmp → round-trip gate via load_group → os.replace.
    config_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp_path.write_text(rendered, encoding="utf-8")
    try:
        load_group(tmp_path)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        print(f"camp group: rendered config failed round-trip validation: {e}", file=sys.stderr)
        sys.exit(1)
    os.replace(str(tmp_path), str(config_path))
