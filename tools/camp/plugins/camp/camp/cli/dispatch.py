"""camp CLI top-level dispatch — the hand-rolled verb router.

Two orthogonal concerns, kept distinct:
  (a) The thin ``cli/camp`` shim puts ``plugins/camp/`` on ``sys.path`` (so the
      ``camp`` package and the plugin-root-level ``_bootstrap`` module resolve)
      then calls ``main()`` here.
  (b) ``main`` bootstraps ``trailhead.paths`` via ``_bootstrap`` before any
      command code that needs it runs — EXCEPT on the hidden inject route,
      which never touches the heavy spine module and, when an explicit
      ``--workspace`` is given, never touches trailhead.paths either. Without
      ``--workspace`` the inject route still needs trailhead.paths (to derive
      the queue's central-state-dir location), so it calls the same cheap
      ``_bootstrap`` walk itself, lazily, inside ``cli/inject.py``.

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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..host.config import Host

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

#: The two spellings of the widen-to-every-group option, and the only verbs it
#: has any meaning for. Held here, once, so `read_all_groups_option` (the
#: reader `main()` consults for every verb) and its applicability check can
#: never drift against each other.
ALL_GROUPS_FLAGS = ("--all-groups", "-g")
_ALL_GROUPS_VERBS = frozenset({"list", "sessions"})

#: The two spellings of the widen-to-every-machine option, and the only
#: verbs it has any meaning for. Held here, once, alongside
#: `ALL_GROUPS_FLAGS` above, so the two independent axes (groups, machines)
#: can never drift against each other on spelling or applicability.
#:
#: "attach" is here alongside "list"/"sessions", but it is dispatched
#: through its own `_dispatch_attach_all_hosts` — never through
#: `_dispatch_all_hosts_command`, whose resolved-group, row-merging shape
#: does not apply to a ref-addressed, groupless verb (see
#: `docs/design/attaching-reaches-a-running-session-on-any-machine.md`,
#: "Why -a probes rather than reading the merged listing").
ALL_HOSTS_FLAGS = ("--all-hosts", "-a")
_ALL_HOSTS_VERBS = frozenset({"list", "sessions", "attach"})

#: Verbs whose trailing argv is an opaque payload forwarded to something else
#: — never camp's own flags. `camp foreach <cmd…>` forwards everything after
#: its own `--name`/`--fail-fast`/`--json` to the wrapped command verbatim, so
#: a `-g` or `--all-groups` in THAT command's own argv (e.g. `git log -g`)
#: must never be scanned for camp's widen-to-every-group option at all.
_OPAQUE_PAYLOAD_VERBS = frozenset({"foreach"})

#: The characters `split_bundled_short_flags` is willing to expand a
#: bundled single-dash token into — camp's own short flags, and only these
#: two. Held here, once, so the splitter and the two readers above
#: (`ALL_GROUPS_FLAGS`'s `-g`, `ALL_HOSTS_FLAGS`'s `-a`) cannot drift apart
#: on what counts as one of camp's own short options.
_BUNDLED_SHORT_FLAG_CHARS = frozenset({"a", "g"})


def split_bundled_short_flags(args: list[str]) -> list[str]:
    """Split a bundled single-dash token (``-ag``, ``-ga``, ``-aa``, …) into
    its individual short flags, ONE token per character, order-preserving.

    Deliberately conservative: a token is split ONLY when EVERY character
    after the leading dash is one of camp's own short flags (`a`, `g`) —
    `_BUNDLED_SHORT_FLAG_CHARS`. Anything else (a verb's own short option, a
    flag value, a long `--flag`, a bare `-`) passes through completely
    unchanged, so no verb's own argument can ever be corrupted by this scan.
    An already-bare `-a` or `-g` is untouched too (nothing to split).
    """
    out: list[str] = []
    for arg in args:
        if (
            len(arg) > 2
            and arg[0] == "-"
            and arg[1] != "-"
            and all(c in _BUNDLED_SHORT_FLAG_CHARS for c in arg[1:])
        ):
            out.extend(f"-{c}" for c in arg[1:])
        else:
            out.append(arg)
    return out


def _read_widening_option(
    args: list[str], flags: tuple[str, ...]
) -> tuple[list[str], bool]:
    """Consume every token in *args* spelling one of *flags*, order-preserving.

    Returns ``(remaining, present)``. The one scan behind both widening
    options below, so the two axes cannot drift on how their option is
    consumed — only on which spellings name it.
    """
    remaining: list[str] = []
    present = False
    for arg in args:
        if arg in flags:
            present = True
        else:
            remaining.append(arg)
    return remaining, present


def read_all_groups_option(args: list[str]) -> tuple[list[str], bool]:
    """Consume every ``--all-groups``/``-g`` from *args*, order-preserving.

    Returns ``(remaining, present)``. This is the ONE reader both `camp list`
    and `camp sessions` are widened through — `main()` calls it exactly once,
    on the raw argv, before a verb is classified or a group is resolved, so
    the two entry points cannot drift on what spells the option or where its
    applicability is decided.
    """
    return _read_widening_option(args, ALL_GROUPS_FLAGS)


def read_all_hosts_option(args: list[str]) -> tuple[list[str], bool]:
    """Consume every ``--all-hosts``/``-a`` from *args*, order-preserving.

    Returns ``(remaining, present)`` — the same shape as
    `read_all_groups_option`. `main()` calls it exactly once, on the raw
    argv (after `split_bundled_short_flags` has already separated a bundled
    ``-ag`` into its own ``-a``/``-g`` tokens), before a verb is classified
    or a group is resolved.
    """
    return _read_widening_option(args, ALL_HOSTS_FLAGS)


def _flag_present(args: list[str], flag: str) -> bool:
    """True when *flag* appears in *args* in either spelling (``--x`` /
    ``--x=value``).

    A presence scan only — nothing is consumed, because `main()`'s collision
    refusals must SEE a flag in an argv the refused command never gets to
    run. One scanner for every such check, so `--group`'s collision test and
    `--host`'s cannot drift apart on what counts as "present".
    """
    return any(a == flag or a.startswith(f"{flag}=") for a in args)


#: The one spelling of the resolve-a-single-declared-remote-host option, and
#: the only verbs it has any meaning for. Held here, once, so
#: `read_host_option` (the reader `main()` consults for every verb) and its
#: applicability check can never drift against each other — the same shape as
#: `ALL_GROUPS_FLAGS` / `_ALL_GROUPS_VERBS` above.
#:
#: "attach" carries its reference across untouched rather than going through
#: the JSON relay transport `_dispatch_host_command` builds for "list"/
#: "sessions" — see that function's own docstring.
#:
#: "launch" and "kill" are the two STATE-CHANGING members — see
#: `_STATE_CHANGING_HOST_VERBS` below. "kill" is nonetheless groupless (see
#: `_GROUP_REQUIRED_HOST_VERBS`'s own comment for why the two concerns no
#: longer share one set).
HOST_FLAG = "--host"
_HOST_VERBS = frozenset({"list", "sessions", "attach", "launch", "kill"})

#: The subset of `_HOST_VERBS` that changes state on the named machine,
#: rather than merely reading from it. Held here, once, so the
#: `--all-hosts`/`-a` refusal below stays in lockstep with this set: a
#: member gets its own wording — the option is refused because the verb
#: changes state, not because it merely "has no meaning" — where every
#: other `_HOST_VERBS` member gets the generic refusal.
#:
#: This set drives ONLY the --all-hosts wording now. It used to also decide
#: the --group requirement below, but "kill" joining it broke that
#: coincidence: a stop is groupless (the reference names the session, and
#: the far side resolves it against its own pool exactly as it would
#: locally), so "kill" belongs here for the all-hosts refusal but must NOT
#: pick up "launch"'s --group requirement — see `_GROUP_REQUIRED_HOST_VERBS`
#: below, which holds only "launch".
_STATE_CHANGING_HOST_VERBS = frozenset({"launch", "kill"})

#: The subset of `_HOST_VERBS` for which `--host` requires an explicit
#: `--group <name>` rather than colliding with one the way every other
#: `_HOST_VERBS` member does. A state-changing verb that also needs to know
#: WHICH group to act on remotely belongs here: `--group` is the value it
#: forwards, never a value this side infers from its own cwd
#: (`docs/design/a-session-starts-on-a-named-machine.md`, "The group is
#: named, never inferred"). "kill" is deliberately excluded even though it
#: is state-changing — a stop names no group at all, so `--host` + `--group`
#: together on "kill" takes the same collision refusal `list`/`sessions`/
#: `attach` take, per `docs/design/stopping-a-session-on-a-named-machine.md`,
#: "The reference names the session; the group is not asked for".
_GROUP_REQUIRED_HOST_VERBS = frozenset({"launch"})


class _HostFlagMissingValue(Exception):
    """Raised by `read_host_option` when ``--host`` is the final token in
    *args*, with no following value and no ``=value`` — a name camp cannot
    resolve, so it must refuse rather than silently drop the flag or swallow
    the next argument as its value."""


def read_host_option(args: list[str]) -> tuple[list[str], str | None]:
    """Consume ``--host <name>``/``--host=value`` from *args*.

    Delegates the actual consumption to `_consume_flag_value` (spine.py),
    which already supports both spellings — but that helper takes whatever
    token follows ``--host`` as its value, whatever it is. A host name never
    begins with ``-``, so before delegating, this scans for the first
    ``--host`` occurrence and refuses up front when the next token is
    another flag (or absent) — otherwise a flag like ``--group`` or
    ``--all-groups`` gets silently swallowed as the host name, and the very
    refusal that flag should have triggered downstream never fires. Nothing
    is consumed from *args* before that refusal: it raises against a plain
    scan, never against the mutated copy `_consume_flag_value` would have
    produced. That same scan is also what refuses a trailing ``--host`` with
    nothing after it at all. ``--host=`` (an empty value after the equals
    sign) refuses after delegating, since an empty host name is never a
    resolvable one either.
    Returns ``(remaining, host_name)``; `host_name` is `None` when `--host`
    is absent. Raises `_HostFlagMissingValue` when the value is missing.
    """
    from ..spine import _consume_flag_value

    remaining = list(args)
    for i, arg in enumerate(remaining):
        if arg == HOST_FLAG:
            if i + 1 >= len(remaining) or remaining[i + 1].startswith("-"):
                raise _HostFlagMissingValue()
            break
        if arg.startswith(f"{HOST_FLAG}="):
            break

    host_name = _consume_flag_value(remaining, HOST_FLAG)
    if host_name == "":
        raise _HostFlagMissingValue()
    return remaining, host_name


def _dispatch_host_command(
    verb: str, host: "Host", host_name: str, rest: list[str]
) -> None:
    """Hand a resolved remote `Host` off to its verb handler.

    Reached ONLY after `--host` has resolved to a declared host and every
    refusal above has passed — `main()`'s `--host` block is this function's
    sole caller, and it refuses any verb outside `_HOST_VERBS` before
    reaching here, so *verb* is always one of the five below.

    "list" and "sessions" are wired to the SSH transport
    (`camp.host.transport.run_camp`, via `camp.host.relay.relay_all_groups`).
    "attach" is not: it carries the reference across untouched and hands this
    process to an interactive `ssh -t` (`camp.host.handoff`) rather than
    relaying a JSON answer — see `cli/session.py`'s `_cmd_attach_host_cli`.

    "launch" is accepted by `_HOST_VERBS` — and reaches here with `--group`
    already required and present, per the `_GROUP_REQUIRED_HOST_VERBS`
    handling above. Unlike "list"/"sessions" it is wired through
    `camp.host.relay.answer_object_for_host` (the single-object relay shape),
    not `relay_all_groups`: a launch answers with one session or nothing at
    all, and its rendering carries its own certainty-aware exit-code and
    stderr-ordering policy the generic rows relay does not provide — see
    `cli/session.py`'s `_cmd_launch_host_cli`.

    "kill" reaches here with NO --group required or forwarded — it is
    state-changing (`_STATE_CHANGING_HOST_VERBS`) but not group-required
    (`_GROUP_REQUIRED_HOST_VERBS`): the reference alone names the session,
    exactly as with "attach". It is wired through
    `camp.host.relay.answer_payload_for_host` (the payload relay shape that
    accepts either a single object or candidate rows) — see `cli/session.py`'s
    `_cmd_kill_host_cli`.
    """
    if verb == "list":
        from .workspace import _cmd_ls_host_cli

        _cmd_ls_host_cli(rest, host, host_name)
    elif verb == "sessions":
        from .session import _cmd_sessions_host_cli

        _cmd_sessions_host_cli(rest, host, host_name)
    elif verb == "launch":
        from .session import _cmd_launch_host_cli

        _cmd_launch_host_cli(rest, host, host_name)
    elif verb == "kill":
        from .session import _cmd_kill_host_cli

        _cmd_kill_host_cli(rest, host, host_name)
    else:
        assert verb == "attach"
        from .session import _cmd_attach_host_cli

        _cmd_attach_host_cli(rest, host, host_name)


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


def _is_ref_addressed_launch(verb: str, rest: list[str]) -> bool:
    """Is this a `camp launch --resume <ref>` — the one groupless launch flavor?

    Classified from the raw argv, before any group is resolved, because the whole
    point of the flavor is that it resolves without one. Reads the flag name from
    the handler that parses it, so the router and the parser cannot disagree about
    what spells a resume.
    """
    from .session import RESUME_FLAG

    canonical, kind = _resolve_verb(verb)
    if canonical != "launch" or kind != "live":
        return False
    return any(arg == RESUME_FLAG or arg.startswith(f"{RESUME_FLAG}=") for arg in rest)


def main() -> None:
    global _TRAILHEAD_PATHS_OK
    argv = sys.argv[1:]

    # The hidden `camp inject --drain` PostToolUse hook fires on EVERY Bash tool
    # call. It never touches the heavy spine module, so keep IT near-free at
    # least: detect the inject route BEFORE the cold-subprocess
    # ensure_trailhead_importable() walk here and skip it — cli/inject.py calls
    # that walk itself, lazily, only when it actually needs trailhead.paths
    # (i.e. no explicit --workspace). The bootstrap still runs unconditionally
    # for every OTHER command (behavior unchanged).
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

    if argv and argv[0] in ("--which", "which"):
        from .status import _cmd_which
        _cmd_which()
        return

    # Strip --dry-run for command dispatch (spine re-checks it)
    dry_run = "--dry-run" in argv or bool(os.environ.get("CAMP_DRY_RUN"))

    first = argv[0] if argv else None

    # Split a bundled short-flag token (`-ag`, `-ga`, …) into its individual
    # flags BEFORE anything downstream reads argv — the one place this must
    # happen so every later reader (`read_all_groups_option`,
    # `read_all_hosts_option`, `_flag_present`, `_resolve_group_for_command`,
    # the group-aware dispatch) sees the same expanded tokens regardless of
    # how the operator spelled it. `foreach`'s opaque payload is excluded —
    # its own argv is forwarded verbatim to the wrapped command and must
    # never be rewritten.
    if first is not None and first not in _OPAQUE_PAYLOAD_VERBS:
        argv = [first, *split_bundled_short_flags(argv[1:])]

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
        if _flag_present(argv[1:], HOST_FLAG):
            print(f"camp {first}: {HOST_FLAG} has no meaning here", file=sys.stderr)
            sys.exit(1)
        from .group import _cmd_group_cli
        _cmd_group_cli(argv[1:])
        return

    # 'groups' is a read-only listing, dispatched here — before group
    # resolution is even attempted — so it never requires a resolved group
    # and never fails outright on a sibling group's malformed config (it
    # degrades that one entry instead; see _cmd_groups_cli). Both this
    # branch and 'group' above return BEFORE the --host reader below is
    # ever reached, so --host must be refused explicitly here — otherwise
    # `camp groups --host <name>` silently answers LOCALLY at exit 0
    # instead of refusing (the same silent-drop class already fixed once
    # for --all-groups + --group).
    if first == "groups":
        if _flag_present(argv[1:], HOST_FLAG):
            print(f"camp {first}: {HOST_FLAG} has no meaning here", file=sys.stderr)
            sys.exit(1)
        from .group import _cmd_groups_cli
        _cmd_groups_cli(argv[1:])
        return

    # 'transfer-probe' answers what THIS host is, for the transfer preflight's
    # sending side — dispatched here, before group resolution, for the same
    # reason 'groups' is: "the named group is not configured here" is a valid,
    # distinct answer this verb must produce itself, never a dispatch-time
    # refusal that never gets the chance to say so. --host has no meaning
    # here either — this verb never asks a third host about itself.
    if first == "transfer-probe":
        if _flag_present(argv[1:], HOST_FLAG):
            print(f"camp {first}: {HOST_FLAG} has no meaning here", file=sys.stderr)
            sys.exit(1)
        from .transfer import _cmd_transfer_probe_cli
        _cmd_transfer_probe_cli(argv[1:])
        return

    # 'transfer-receive' is the peer side of a workspace move (begin/finish),
    # dispatched here before group resolution for the same reason
    # 'transfer-probe' is: "the named group is not configured here" is a
    # valid, distinct answer this verb must produce itself. --host has no
    # meaning here either — this verb never asks a third host about itself.
    if first == "transfer-receive":
        if _flag_present(argv[1:], HOST_FLAG):
            print(f"camp {first}: {HOST_FLAG} has no meaning here", file=sys.stderr)
            sys.exit(1)
        from .transfer import _cmd_transfer_receive_cli
        _cmd_transfer_receive_cli(argv[1:])
        return

    if first == "init":
        from ..spine import cmd_legacy_redirect
        cmd_legacy_redirect("init", "group")
        return

    # ---------------------------------------------------------------------------
    # --all-groups / -g — read ONCE here, before a single group is ever
    # resolved, before any group config is loaded, and before any session or
    # workspace is enumerated. A refusal below therefore costs nothing: not
    # one file has been read yet and no harness has been asked anything.
    # ---------------------------------------------------------------------------
    scan_rest = argv[1:] if first and first not in _OPAQUE_PAYLOAD_VERBS else []
    scan_rest, all_groups = read_all_groups_option(scan_rest)
    scan_rest, all_hosts = read_all_hosts_option(scan_rest)

    # ---------------------------------------------------------------------------
    # --all-hosts / -a — read at the same early point as --all-groups, and
    # BEFORE the --all-groups-only branch below, since `-ag`/`-ga` (both
    # present) is answered by the all-hosts path too — it widens both axes
    # rather than being a third flag. When --all-hosts is absent this block
    # is a no-op and --all-groups behaves exactly as it did before this
    # option existed.
    # ---------------------------------------------------------------------------
    if all_hosts:
        canonical, _kind = _resolve_verb(first) if first else (first, "live")
        if canonical not in _ALL_HOSTS_VERBS:
            # A state-changing verb (launch) is refused for its OWN stated
            # reason — the verb changes state, so it acts on one named
            # machine — never the generic "has no meaning here" a verb gets
            # when an option simply does not apply to it. The generic
            # wording would read as an oversight; this is a decision (design
            # doc: "Launching is never a broadcast"). The refusal names the
            # single-host form so the operator's real intent — do this over
            # there — is one option away.
            if canonical in _STATE_CHANGING_HOST_VERBS:
                print(
                    f"camp {canonical}: --all-hosts changes state on every "
                    f"declared machine at once — {canonical} acts on the one "
                    f"machine you name with {HOST_FLAG} <name>",
                    file=sys.stderr,
                )
            else:
                print(f"camp {first}: --all-hosts has no meaning here", file=sys.stderr)
            sys.exit(1)
        # --host names one declared machine; --all-hosts names every declared
        # machine plus this one. Refused like the --all-groups/--host
        # collision above rather than accepted as redundant.
        if _flag_present(scan_rest, HOST_FLAG):
            print(
                f"camp {canonical}: --all-hosts and {HOST_FLAG} name every "
                "machine and one machine at once — pass one or the other",
                file=sys.stderr,
            )
            sys.exit(1)
        # attach is groupless — it has no group axis for --all-groups/-g to
        # widen — and is dispatched through its own probe-then-refuse
        # function rather than the row-merging `_dispatch_all_hosts_command`
        # every other `_ALL_HOSTS_VERBS` member uses (see that flag's own
        # comment above ALL_HOSTS_FLAGS).
        if canonical == "attach":
            if all_groups:
                print(
                    "camp attach: --all-groups has no meaning here — attach "
                    "addresses a session by reference, not a group",
                    file=sys.stderr,
                )
                sys.exit(1)
            _dispatch_attach_all_hosts(scan_rest)
            return
        _dispatch_all_hosts_command(
            canonical, scan_rest, all_groups=all_groups, argv=argv
        )
        return

    if all_groups:
        canonical, _kind = _resolve_verb(first) if first else (first, "live")
        if canonical not in _ALL_GROUPS_VERBS:
            print(f"camp {first}: --all-groups has no meaning here", file=sys.stderr)
            sys.exit(1)
        if _flag_present(scan_rest, "--group"):
            print(
                f"camp {canonical}: --all-groups and --group name every group and "
                "one group at once — pass one or the other",
                file=sys.stderr,
            )
            sys.exit(1)
        # --host names "every group on a named remote host"; --all-groups
        # names "every group on this machine". Different scopes, so refused
        # like the --group collision above rather than accepted as
        # redundant — detected through the same `_flag_present` scanner the
        # --group check uses, so a valueless `--host` (caught properly by
        # read_host_option
        # when reached on its own) is still caught here rather than silently
        # dispatching --all-groups's local answer. This check must live
        # INSIDE the all_groups branch: --all-groups is consumed and
        # dispatched before read_host_option ever runs below, so without it
        # --host is silently dropped.
        if _flag_present(scan_rest, HOST_FLAG):
            print(
                f"camp {canonical}: --all-groups and {HOST_FLAG} name every group "
                "on this machine and every group on a named remote host at once "
                "— pass one or the other",
                file=sys.stderr,
            )
            sys.exit(1)
        _dispatch_all_groups_command(canonical, scan_rest)
        return

    # ---------------------------------------------------------------------------
    # --host <name> — read at the same early point as --all-groups above:
    # before any group config loads, any group resolves, or the hosts file is
    # even opened for a verb this option has no meaning for. A refusal below
    # therefore also costs nothing.
    #
    # Applicability is checked BEFORE the value is read: a verb --host has no
    # meaning for is refused with "has no meaning here" even when --host is
    # also missing its value — a value check that ran first would instead
    # report "requires a value" on a verb where no value would ever be
    # accepted anyway.
    # ---------------------------------------------------------------------------
    if _flag_present(scan_rest, HOST_FLAG):
        canonical, _kind = _resolve_verb(first) if first else (first, "live")
        if canonical not in _HOST_VERBS:
            print(f"camp {first}: {HOST_FLAG} has no meaning here", file=sys.stderr)
            sys.exit(1)

    try:
        scan_rest, host_name = read_host_option(scan_rest)
    except _HostFlagMissingValue:
        print(f"camp {first}: {HOST_FLAG} requires a value", file=sys.stderr)
        sys.exit(1)
    if host_name is not None:
        canonical, _kind = _resolve_verb(first) if first else (first, "live")
        if canonical not in _HOST_VERBS:
            print(f"camp {first}: {HOST_FLAG} has no meaning here", file=sys.stderr)
            sys.exit(1)
        if canonical in _GROUP_REQUIRED_HOST_VERBS:
            # A state-changing verb sends --group across untouched — the far
            # side resolves it there, exactly as a local invocation would.
            # No group resolves on the far side from a non-interactive ssh
            # cwd (it lands in a home directory belonging to no workspace),
            # so unlike the read verbs above, --group is REQUIRED here
            # rather than refused: camp never fills the gap from its own
            # working directory, because a local directory deciding what
            # runs on another machine is the exact substitution the host
            # axis exists to prevent (design doc: "The group is named,
            # never inferred"). Checked here, before hosts.toml is even
            # read, so the refusal costs nothing and no connection is ever
            # attempted.
            if not _flag_present(scan_rest, "--group"):
                print(
                    f"camp {canonical}: {HOST_FLAG} requires an explicit "
                    "--group <name> — the far side resolves no group from "
                    "its own working directory, so this machine's cwd must "
                    "never decide what runs on another one",
                    file=sys.stderr,
                )
                sys.exit(1)
        elif _flag_present(scan_rest, "--group"):
            print(
                f"camp {canonical}: {HOST_FLAG} and --group name one remote "
                "host and one local group at once — pass one or the other",
                file=sys.stderr,
            )
            sys.exit(1)

        from ..host.config import load_hosts, HostConfigError

        try:
            hosts = load_hosts()
        except HostConfigError as e:
            print(f"camp {canonical}: {e}", file=sys.stderr)
            sys.exit(1)

        host = hosts.get(host_name)
        if host is None:
            if hosts:
                declared = ", ".join(sorted(hosts))
                print(
                    f"camp {canonical}: no host named {host_name!r} is "
                    f"declared — declared hosts are: {declared}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"camp {canonical}: no host named {host_name!r} is "
                    "declared — no hosts are declared in "
                    "~/.config/camp/hosts.toml",
                    file=sys.stderr,
                )
            sys.exit(1)

        _dispatch_host_command(canonical, host, host_name, scan_rest)
        return

    # ---------------------------------------------------------------------------
    # Group-aware command routing
    # ---------------------------------------------------------------------------
    _SKIP_GROUP_RESOLVE = frozenset({
        "help", "--help", "-h", "doctor",
        "foreach", "path", "which",
        "--version", "version",
        # Fully groupless, and for a stronger reason than convenience: a stop
        # is what an operator reaches for when something is already wrong, so
        # resolving a group first would let a sibling group's malformed config
        # abort the verb that reclaims the memory.
        "kill",
        # Same reasoning as "kill" — a reference names the session, and a
        # sibling group's malformed config must never block an attach.
        "attach",
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
        # `camp launch --resume <ref>` is ref-addressed: the reference names the
        # session, and the session's own recorded root names the group. A resume into a camp workspace must therefore
        # answer from a plain shell outside every group directory, so it is
        # dispatched here rather than falling through to the needs-group refusal
        # — which would demand a flag the workspace flavor is defined not to need.
        # The handler still requires an explicit --group for any root that is NOT
        # a workspace; that boundary is its call to make, not this router's.
        if _is_ref_addressed_launch(first, argv[1:]):
            from .session import _cmd_launch_group_cli

            _cmd_launch_group_cli(argv[1:], None, None)
            return

    # Delegate everything else to the spine dispatcher (fallback / non-group cmds).
    # Imported lazily so the early-returning inject route never pays the
    # spine module-load cost.
    from ..spine import main as _spine_main
    _spine_main()


def _dispatch_all_groups_command(verb: str, rest: list[str]) -> None:
    """Answer `list` or `sessions` for every configured group in one invocation.

    Reached ONLY from `main()`'s early `--all-groups`/`-g` handling, before any
    single group is resolved — the whole point of the option is an answer with
    no one group to resolve against, and refusing a named group alongside it
    (in `main()`, before this function ever runs) is what keeps that true.
    `verb` is already the canonicalized verb name.
    """
    from ..group.config import GroupConfigError
    from .session import _cmd_sessions_group_cli
    from .workspace import _cmd_ls_all_groups_cli

    try:
        if verb == "list":
            _cmd_ls_all_groups_cli(rest, env=None)
        else:
            _cmd_sessions_group_cli(rest, None, None, all_groups=True)
    except GroupConfigError as e:
        print(f"camp: config error: {e}", file=sys.stderr)
        sys.exit(1)


def _dispatch_all_hosts_command(
    verb: str, rest: list[str], *, all_groups: bool, argv: list[str]
) -> None:
    """Answer `list` or `sessions` for the resolved group — or, under
    `-ag`/`-ga`, every group — on every declared machine plus this one,
    merged into one ordered answer.

    Reached ONLY from `main()`'s early `--all-hosts`/`-a` handling, before
    any single group is resolved for the machine axis itself — the group
    axis (`all_groups`) stays exactly where the `--all-groups` handling
    already puts it; this function only widens the machine axis and, when
    `all_groups` is False, resolves the one group its narrowing filter
    needs via the SAME resolver every plain (non-widened) invocation uses.

    `-a`'s narrowing to a resolved group is applied locally, as a filter on
    the merged rows (:func:`camp.host.merge.merge_all_hosts_answer`'s
    `group=`) — never as a group name crossing the wire. Every remote
    invocation is always the all-groups + `--json` form, matching what
    `--host` already sends.
    """
    from ..host.config import HostConfigError, load_hosts, self_host_name
    from ..host.merge import answer_all_hosts_concurrently, merge_all_hosts_answer

    if all_groups and _flag_present(rest, "--group"):
        print(
            f"camp {verb}: --all-groups and --group name every group and "
            "one group at once — pass one or the other",
            file=sys.stderr,
        )
        sys.exit(1)

    if verb == "sessions":
        from .session import refuse_sessions_local_only_options

        refuse_sessions_local_only_options(rest, widening_flag="--all-hosts")

    as_json = _flag_present(rest, "--json")

    group: dict | None = None
    narrow_group: str | None = None
    if not all_groups:
        try:
            group, _group_env = _resolve_group_for_command(argv)
        except Exception as exc:
            print(f"camp: config error: {exc}", file=sys.stderr)
            sys.exit(1)
        if group is None:
            print(
                f"camp {verb}: --all-hosts needs a group to widen — pass "
                "-ag for every group on every machine, or --group <name> "
                "to name one",
                file=sys.stderr,
            )
            sys.exit(1)
        narrow_group = group["group"]["name"]

    if verb == "list":
        from .workspace import local_list_answer as local_answer_fn
        from .workspace import render_list_row_human as render_row
    else:
        from .session import local_sessions_answer as local_answer_fn
        from .session import render_session_row_human as render_row

    def _local_answer() -> tuple[list[dict], list[str], int]:
        return local_answer_fn(group, all_groups=all_groups)

    try:
        hosts = load_hosts()
        hosts_error: str | None = None
    except HostConfigError as exc:
        hosts = {}
        hosts_error = str(exc)

    (local_rows, local_notices, local_exit_code), host_answers = (
        answer_all_hosts_concurrently(
            _local_answer,
            list(hosts.items()),
            verb=verb,
            remote_argv=[verb, "--all-groups", "--json"],
        )
    )

    # self_host_name() re-reads the same hosts.toml load_hosts() already
    # read above. When that read already failed (hosts_error is set), the
    # declared-hosts read is treated as ONE operation: self_name is None
    # and the failure is already carried as hosts_error — a second raise
    # here must never bypass the recovery merge_all_hosts_answer renders
    # for exactly this state. self_host_name() is only called when the
    # file is known to parse, so a raise here names a genuine self_name-
    # specific problem (e.g. a malformed self_name value), not a failure
    # to read the declarations.
    if hosts_error is not None:
        self_name: str | None = None
    else:
        try:
            self_name = self_host_name()
        except HostConfigError as exc:
            print(f"camp {verb}: {exc}", file=sys.stderr)
            sys.exit(1)

    rows, notices, exit_code = merge_all_hosts_answer(
        local_rows,
        local_notices,
        local_exit_code,
        self_name=self_name,
        host_answers=host_answers,
        hosts_error=hosts_error,
        group=narrow_group,
    )

    for notice in notices:
        print(notice, file=sys.stderr)

    if as_json:
        import json as _json

        print(_json.dumps(rows))
    else:
        _render_all_hosts_human(self_name, hosts, hosts_error, rows, render_row, verb)

    sys.exit(exit_code)


def _attach_probe_object(outcome) -> dict | None:
    """One host's answer to an attach probe, decoded — or `None` if the host
    did not answer the question at all.

    `None` covers every way an answer can fail to be one: the transport never
    reached a running camp, the far side exited non-zero, its stdout was not
    JSON, or it was JSON that is not an object. Both probe sub-modes
    (`--resolve --json` and `--list --json`) ask their own question of the
    decoded object; this decides only whether there is an object to ask.
    """
    import json as _json

    from ..host.transport import Answered

    if not isinstance(outcome, Answered) or outcome.exit_code != 0:
        return None
    try:
        data = _json.loads(outcome.stdout)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _attach_resolve_answer(outcome) -> tuple[bool, str | None, str | None] | None:
    """Classify one host's `<camp_bin> attach <ref> --resolve --json` answer.

    Returns `(matched, session_id, state)`:

    - `matched` is the far side's own `ok` field — it resolved, or it
      definitely did not.
    - `session_id` is the session the far side named, when it named one (a
      `Resolved` match, or a `NotRunning` state — both carry `session_id` in
      `_attach_resolve_payload`). `None` otherwise.
    - `state` is the far side's own `state` field (`"not_running"`,
      `"ambiguous"`, or `"no_match"`), present only when `matched` is False.

    `None` overall means the host did not answer a resolvable question at
    all (unreachable, timed out, refused credentials, answered with
    something that is not the JSON this probe expects) — the "declared host
    did not answer" state the design doc requires `-a` to refuse on rather
    than silently treat as "didn't match".
    """
    data = _attach_probe_object(outcome)
    if data is None or "ok" not in data:
        return None
    return bool(data["ok"]), data.get("session_id"), data.get("state")


def _attach_list_answer(outcome) -> list[dict] | None:
    """Classify one host's `<camp_bin> attach --list --json` answer — the
    bare cross-host picker's sibling of `_attach_resolve_answer` above.
    `None` on anything short of a well-formed row list; a host this fails
    for is dropped from the picker with a stderr notice rather than
    aborting the whole widened picker (see `_dispatch_attach_all_hosts`).
    """
    data = _attach_probe_object(outcome)
    if data is None or not data.get("ok"):
        return None
    rows = data.get("rows")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return None
    return rows


def _reject_self_named_host(hosts: dict, self_name: str | None) -> None:
    """Refuse when a declared host shares this machine's own `self_name`.

    Nothing in `host/config.py` rejects that collision at declaration time —
    `self_host_name`'s own docstring says detecting it "belongs to the first
    code that has both names in hand", which is here: `_dispatch_attach_all_hosts`
    is the one place that loads `hosts.toml` AND resolves `self_name` for the
    same invocation. Left unguarded, a row for that host reaches the local
    branch of a handoff decision (`row.machine == self_name`) carrying a
    `_RemoteAttachCandidate`, which has no `derived_name` — an `AttributeError`
    instead of a camp refusal.
    """
    from ..spine import _die

    if self_name is not None and self_name in hosts:
        _die(
            f"camp attach: hosts.toml declares {self_name!r} as a remote host — "
            "the same name this machine's own self_name uses; rename one so "
            "camp attach can tell them apart"
        )


class _RemoteAttachCandidate:
    """A remote picker row's identity — just enough for a handoff.

    Never a `camp.launch.recovery.SessionCandidate`: that dataclass carries
    fields (`root`, `age_seconds`, `root_missing`, `unreadable`) this side has
    no way to learn about a session on another machine, and nothing downstream
    reads them for a row this module never treats as local. A picked remote
    row is always handed off via `camp.host.handoff.remote_argv(host,
    session_id)` — the far side resolves and re-derives everything else
    itself, the same "carry the reference across untouched" posture `--host`
    uses. `session_id` is the only field a handoff needs; the wire payload's
    `derived_name` is never read here.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


def _dispatch_attach_all_hosts(rest: list[str]) -> None:
    """`camp attach -a` and `camp attach <ref> -a` — probe every declared
    machine rather than merge rows.

    Reached ONLY from `main()`'s `--all-hosts`/`-a` handling, for the
    `"attach"` verb specifically — never through `_dispatch_all_hosts_command`
    above, whose resolved-group, row-merging shape is for `list`/`sessions`
    and does not apply to a ref-addressed, groupless verb (see
    `docs/design/attaching-reaches-a-running-session-on-any-machine.md`,
    "Why -a probes rather than reading the merged listing").

    Two shapes, by whether *rest* carries a reference:

    - `<ref> -a`: resolves *ref* locally AND asks every declared host to
      resolve it too, concurrently (`<camp_bin> attach <ref> --resolve
      --json`, classified by `_attach_resolve_answer`). A host that does not
      answer refuses the whole probe — the match count is unknown, and
      proceeding on the machines that did answer could attach to the wrong
      session or report no match when one exists. Otherwise: zero matches
      refuses naming every machine asked; exactly one hands off to that
      machine (locally, or via the untouched-reference `--host` pass-through
      `camp.host.handoff.remote_argv` builds); more than one refuses naming
      every match.
    - `-a` bare: widens the numbered picker across every declared machine,
      by the same probe-and-classify shape applied to `<camp_bin> attach
      --list --json` (`_attach_list_answer`) instead — this is a listing,
      not a resolution, so a silent host is dropped with a notice rather
      than refusing the whole picker (mirrors `camp sessions -a`'s own
      per-machine degradation, not the reference form's stricter posture).
    """
    import concurrent.futures

    from ..attach.picker import (
        NothingToOffer,
        Picked,
        PoolReady,
        PoolUnreadable,
        Row,
        local_pool,
        pick_session,
    )
    from ..attach.prefix_warning import warn_if_nested
    from ..attach.resolve import Ambiguous, NotRunning, Resolved, resolve_attach_ref
    from ..host import transport as _transport
    from ..host.config import HostConfigError, load_hosts
    from ..host.handoff import handoff, local_argv, remote_argv
    from ..spine import _die
    from .session import _AMBIGUOUS_EXIT_CODE, _attach_session_context, _print_candidates

    if len(rest) > 1:
        _die(
            f"camp attach: one session reference, not {len(rest)} — an attach "
            "addresses exactly one session"
        )
    ref = rest[0] if rest else None
    if ref is not None and (not ref.strip() or ref.startswith("-")):
        _die(f"camp attach: {ref!r} is not a valid session reference")

    try:
        hosts = load_hosts()
    except HostConfigError as exc:
        print(f"camp attach: {exc}", file=sys.stderr)
        sys.exit(1)
    host_items = list(hosts.items())

    resolved_env = dict(os.environ)

    if ref is not None:
        groups, transcripts, live, harness, tmux, self_name, _accounts = _attach_session_context(
            resolved_env
        )
        _reject_self_named_host(hosts, self_name)
        local_resolution = resolve_attach_ref(
            ref,
            harness=harness,
            tmux=tmux,
            transcripts=transcripts,
            live_records=live,
            groups=groups,
            env=resolved_env,
        )

        def _probe(item: tuple[str, "Host"]):
            name, host = item
            try:
                outcome = _transport.run_camp(host, ["attach", ref, "--resolve", "--json"])
            except Exception:
                # A bug in this code, or a missing local `ssh`, must never
                # render as a host that failed to answer via a traceback out
                # of `pool.map` — see `host/merge.py`'s `_answer_one_host`.
                return name, None
            return name, _attach_resolve_answer(outcome)

        probed: list[tuple[str, tuple[bool, str | None, str | None] | None]] = []
        if host_items:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(host_items)
            ) as pool:
                probed = list(pool.map(_probe, host_items))

        silent = [name for name, answer in probed if answer is None]
        if silent:
            named = ", ".join(sorted(silent))
            _die(
                f"camp attach: {named} did not answer while resolving {ref!r} — "
                "the match count is unknown, so camp will not guess; re-run "
                f"with --host <name> to attach without waiting on "
                f"{'it' if len(silent) == 1 else 'them'}"
            )

        self_label = self_name or "this machine"

        # Every machine holding a session the ref addresses counts as a
        # match — including one that is ambiguous or stopped there. A local
        # `Ambiguous`/`NotRunning` is itself evidence this machine matched;
        # folding it into "no local match" would let a same-ref match
        # elsewhere silently win the handoff even though this machine also
        # holds (an ambiguous, or a stopped) session the ref names — exactly
        # the wrong-guess risk `## State — the named session matches on more
        # than one machine` exists to refuse. A remote `not_running` is the
        # same fact on the far side, and is deliberately handed off rather
        # than resolved here: the far side's own `camp attach <ref>` refuses
        # in its own words when it is the sole match (mirrors `--host`).
        matches: list[tuple[str, str]] = []
        if isinstance(local_resolution, Resolved):
            matches.append((self_label, local_resolution.candidate.session_id))
        elif isinstance(local_resolution, Ambiguous):
            matches.append(
                (self_label, ", ".join(c.session_id for c in local_resolution.candidates))
            )
        elif isinstance(local_resolution, NotRunning):
            matches.append((self_label, local_resolution.candidate.session_id))
        for name, answer in probed:
            if answer is None:
                continue
            ok, session_id, state = answer
            if ok or state == "not_running":
                matches.append((name, session_id or "matched"))

        if not matches:
            asked = ", ".join([self_label] + [n for n, _ in host_items])
            _die(f"camp attach: no session on any declared machine matches {ref!r} (asked: {asked})")

        if len(matches) > 1:
            named = "; ".join(f"{machine} ({session})" for machine, session in matches)
            _die(
                f"camp attach: {ref!r} matches on more than one machine "
                f"({named}) — re-run with --host <name> naming "
                "the one you mean",
                code=_AMBIGUOUS_EXIT_CODE,
            )

        winner = matches[0][0]
        if winner == self_label:
            if isinstance(local_resolution, Ambiguous):
                _print_candidates(local_resolution.candidates, as_json=False)
                _die(
                    f"camp attach: {ref!r} matches {len(local_resolution.candidates)} "
                    "sessions on this machine — re-run with a longer prefix naming "
                    "exactly one",
                    code=_AMBIGUOUS_EXIT_CODE,
                )
            if isinstance(local_resolution, NotRunning):
                _die(
                    f"camp attach: session {local_resolution.candidate.session_id} is "
                    f"not running — bring it back with `camp launch --resume {ref}`"
                )
            assert isinstance(local_resolution, Resolved)
            warn_if_nested(resolved_env)
            handoff(local_argv(local_resolution.candidate.derived_name))
            return

        warn_if_nested(resolved_env)
        handoff(remote_argv(hosts[winner], ref))
        return

    # Bare cross-host picker.
    groups, transcripts, live, harness, tmux, self_name, _accounts = _attach_session_context(
        resolved_env
    )
    _reject_self_named_host(hosts, self_name)
    local_result = local_pool(
        harness=harness,
        tmux=tmux,
        transcripts=transcripts,
        live_records=live,
        groups=groups,
        env=resolved_env,
        machine=self_name,
    )
    if isinstance(local_result, PoolUnreadable):
        _die(f"camp attach: {local_result.reason}")

    def _probe_list(item: tuple[str, "Host"]):
        name, host = item
        try:
            outcome = _transport.run_camp(host, ["attach", "--list", "--json"])
        except Exception as exc:
            # Same posture as the ref-form probe above: a raise out of this
            # code must render as a host that did not answer, not a
            # traceback out of `pool.map`.
            outcome = _transport.Unreachable(reason=str(exc))
        return name, host, outcome

    probed_list: list[tuple[str, "Host", object]] = []
    if host_items:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(host_items)
        ) as pool:
            probed_list = list(pool.map(_probe_list, host_items))

    rows = list(local_result.rows)
    remote_hosts: dict[str, "Host"] = {}
    for name, host, outcome in probed_list:
        parsed = _attach_list_answer(outcome)
        if parsed is None:
            print(
                f"camp attach: {name} did not answer — omitted from the picker",
                file=sys.stderr,
            )
            continue
        remote_hosts[name] = host
        for row in parsed:
            try:
                session_id = row["session_id"]
            except KeyError as e:
                print(
                    f"camp attach: {name} sent a row missing {e.args[0]!r} — "
                    "skipping",
                    file=sys.stderr,
                )
                continue
            rows.append(
                Row(
                    machine=name,
                    group=row.get("group"),
                    slug=row.get("slug", ""),
                    candidate=_RemoteAttachCandidate(session_id),
                )
            )

    result = pick_session(
        PoolReady(rows=tuple(rows)),
        stdin=sys.stdin,
        stdout=sys.stdout,
        isatty=sys.stdin.isatty() and sys.stdout.isatty(),
    )
    if isinstance(result, PoolUnreadable):
        _die(f"camp attach: {result.reason}")
    if isinstance(result, NothingToOffer):
        omitted = sorted(name for name, _ in host_items if name not in remote_hosts)
        if omitted:
            _die(
                "camp attach: no running session found on this machine or any "
                f"declared machine that answered — {', '.join(omitted)} did not "
                "answer and could not be checked"
            )
        _die("camp attach: no running session found on this machine or any declared machine")
    assert isinstance(result, Picked)
    warn_if_nested(resolved_env)
    if result.row.machine == self_name:
        handoff(local_argv(result.row.candidate.derived_name))
    else:
        handoff(remote_argv(remote_hosts[result.row.machine], result.row.candidate.session_id))


def _render_all_hosts_human(
    self_name: str | None,
    hosts: dict,
    hosts_error: str | None,
    rows: list[dict],
    render_row,
    verb: str,
) -> None:
    """Print the merged answer grouped by machine — local block first, then
    every declared host in `hosts.toml` declaration order (the same order
    `merge_all_hosts_answer` already merged the rows in). Every machine gets
    its header, even one with nothing beneath it; a machine whose only row
    is a failure (`ok: false`) prints that row's `reason` in place of a
    rendered row.

    Version skew across the operator's declared machines is the expected
    steady state for this feature, not an edge case — a machine running a
    different version can answer with a row that omits a key `render_row`
    indexes directly (a remote row, or a local row from a mismatched local
    answer function). Degrade that ONE row rather than let it take the
    whole merged listing down, the same isolation the `--host` renderers
    (`workspace.py`'s `_cmd_ls_host_cli`, `session.py`'s
    `_cmd_sessions_host_cli`) already hold for their own single-machine
    case — every other row on this machine, and every other machine, still
    renders.
    """
    # Nothing in host/config.py checks a declared host name for uniqueness
    # against self_name (by design — see the module docstring), so the same
    # key can appear twice here. De-duplicate by key, preserving order and
    # keeping the local entry first, so that machine's block — and its
    # rows, matched by key below — is never printed twice.
    machines = [(self_name, self_name if self_name is not None else "this machine")]
    seen_keys = {self_name}
    for host_name in hosts:
        if host_name in seen_keys:
            continue
        seen_keys.add(host_name)
        machines.append((host_name, host_name))

    for key, label in machines:
        print(label)
        for row in rows:
            if row.get("host") != key:
                continue
            if not row.get("ok", True):
                print(f"  {row.get('reason', 'unknown failure')}")
                continue
            try:
                rendered = render_row(row)
            except KeyError as e:
                print(
                    f"camp {verb}: {label} sent a row missing "
                    f"{e.args[0]!r} — skipping",
                    file=sys.stderr,
                )
                continue
            print(f"  {rendered}")

    # hosts.toml itself failed to parse: the declared hosts could never be
    # enumerated, so there is no declared-host header to attribute this row
    # to. When the local machine has its own declared name, it never shares
    # a `host` key with this row (`None`), so it would otherwise never be
    # printed at all — the local machine's own answer would silently read
    # as complete. Skipped only when the local machine is ALSO undeclared
    # (both share the `host: None` key): the row already printed above,
    # under the local block, rather than being printed twice.
    if hosts_error is not None and self_name is not None:
        print("hosts.toml")
        for row in rows:
            if row.get("host") is None and not row.get("ok", True):
                print(f"  {row.get('reason', hosts_error)}")


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
    from .transfer import _cmd_transfer_group_cli

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
    if cmd in ("launch", "sessions"):
        from .session import _cmd_launch_group_cli, _cmd_sessions_group_cli

        handler = _cmd_launch_group_cli if cmd == "launch" else _cmd_sessions_group_cli
        handler(rest, group, group_env)
        return
    if cmd == "transfer":
        _cmd_transfer_group_cli(rest, group, group_env, dry_run)
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
