"""The ``attach`` verb (re-enter a workspace), plus the harness/store addressing
helpers `camp remove`, `camp transfer` and `camp doctor` share.

`camp attach` resolves a slug against exactly one group — `--group` if given,
else the same `resolve_from_cwd` every other group-resolved verb uses — and
hands the terminal to that workspace's door: create, connect, or resurrect. A
slug that does not resolve is a refusal in the door's own words; a group that
does not resolve is the standard needs-group refusal. `--host <name>` forwards
the argv untouched to that machine, where it is that machine's own door.

`_addressable_harnesses` is the pool of (harness, credential store) pairs camp
can ask about sessions at all — every configured group's harness, not only the
one the invocation resolved, because a store camp cannot bind is a session it
cannot see. `camp remove`'s teardown guard, `camp transfer`'s conversation
listing and `camp doctor` all read this pool; `_parsable_groups` is the
malformed-config-tolerant loader that feeds it.

`trigger_activate_phase_work` is `camp new --activate`'s non-interactive
trigger for every member's activate-phase work, unrelated to attach or the
harness pool above beyond sharing this module.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn
from .parser import CampParser

if TYPE_CHECKING:
    from ..attach.door_target import ResolvedWorkspace
    from ..host.config import Host

#: Bounds for `camp new --activate`'s boot-readiness wait. Provisioning clones
#: and sets up every member repo, so the ceiling is generous; the floor is that
#: this wait is BOUNDED at all — a killed provisioner leaves the manifest `pending`
#: forever with no liveness signal, so an unbounded wait would hang the caller.
_PROVISION_POLL_INTERVAL_SECONDS = 1.0
_PROVISION_POLL_TIMEOUT_SECONDS = 900.0


def trigger_activate_phase_work(
    group: dict, slug: str, *, env: dict[str, str] | None = None, wait: bool = True
) -> None:
    """`camp new --activate`'s trigger step: hand every member's activate-phase
    work to the detached provisioner — the non-blocking part is that this never
    waits for that work itself (the possibly-expensive `npm ci` or graph
    build), matching "triggers ... and returns without waiting for it". This is
    the non-interactive path to the same work `camp activate <member>` triggers
    interactively — the way a consumer that never calls `camp activate`
    (any automation that puts an agent straight
    into a worktree) gets its work-enabling tasks run.

    An activate-phase task runs inside the member's worktree, which does not
    exist until the member reaches boot-readiness — so by default (wait=True)
    this first waits, bounded, for boot-readiness before spawning anything; a
    workspace that never reaches boot-readiness triggers nothing. Blocking on
    boot-readiness is acceptable because cheapness is a requirement of that
    phase — only the activate-phase work itself never blocks. wait=False
    (`--no-wait`) skips even that: it spawns immediately, racing the
    still-running provisioner, an accepted risk on that flag.

    A member declaring no activate-phase task is skipped entirely — no
    subprocess is spawned for it — so a group with no activate-phase tasks
    anywhere is a clean no-op.
    """
    from ..group.config import tasks_in_phase
    from ..group.manifest import ManifestError
    from ..provision.activation import ACTIVATE_PHASE, _spawn_background_activation
    from ..provision.lifecycle import wait_for_provisioning_ready

    if wait:
        try:
            outcome, _report = wait_for_provisioning_ready(
                group,
                slug,
                env=env,
                interval=_PROVISION_POLL_INTERVAL_SECONDS,
                timeout=_PROVISION_POLL_TIMEOUT_SECONDS,
                sleep=time.sleep,
            )
        except ManifestError:
            return
        if outcome != "ready":
            print(
                f"camp new --activate: gave up waiting for {slug!r} to reach "
                f"boot-readiness (outcome={outcome!r}); no activate-phase work "
                "was triggered — re-run `camp activate <member>` once the "
                "workspace is ready",
                file=sys.stderr,
            )
            return

    for member in group["members"]:
        if not tasks_in_phase(member, ACTIVATE_PHASE):
            continue
        _spawn_background_activation(group, slug, member["name"], env=env)


def _harness_display_name(harness) -> str:
    """The name to put in a refusal about *harness*."""
    underlying = getattr(harness, "harness", harness)
    return harness.name or type(underlying).__name__


def _addressable_harnesses(
    groups, *, env: dict[str, str] | None = None, on_drop=None
) -> list:
    """Every (harness, credential store) camp can ask about sessions.

    Naming a group, or standing in one, does not change which sessions exist, so
    the pool spans every configured group's harness rather than whichever one the
    invocation happened to resolve.

    Keyed by (harness display name, declared account) — NOT by harness name
    alone. Two groups sharing a harness but declaring different accounts are
    two entries: each names a different credential store, and collapsing them
    was the exact bug that let a reference resolve against whichever store
    happened to win the collapse while a matching session sat unreferenced in
    the other. Two groups sharing a harness AND declaring the same account (or
    both declaring none) are one entry — the same store must never be read
    twice into the pool.

    Each entry is a :class:`~camp.launch.profile.HarnessStore`: every ordinary
    ``Harness`` method still works on it (it proxies through), plus ``.account``
    (the declaring group's ``[launch] account``, exactly as written, or
    ``None``) and ``.env`` (the environment this store's queries must run
    under). camp names no credential location of its own to build either —
    both come from :func:`camp.launch.profile.harness_store_for`, which asks
    the harness.

    A group whose harness camp cannot name contributes nothing rather than
    failing the lookup: one bad group must not make every other group's
    sessions unaddressable. A group whose harness DID resolve but whose
    declared account the harness refuses to bind is different — that store
    was a real candidate a moment ago, so dropping it is never silent: see
    *on_drop*.

    The default (no-account) store is ALWAYS a member of the pool, regardless
    of what any configured group declares — including when every configured
    group declares its own account. A session running under the default
    store must stay addressable no matter how the rest of the machine's
    groups are configured; the dedupe above already keys the pool by
    (harness, account), so a group that itself declares no account still
    contributes the default store exactly once.

    *on_drop*, when given, is called with ``(group_config, error)`` for every
    group whose store :func:`~camp.launch.profile.harness_store_for` raised
    :class:`~camp.launch.profile.StoreBindingError` for, INSTEAD OF this
    function's own default notice — the fail-closed ``camp remove`` guard
    passes one that refuses outright rather than degrading, because a store
    camp cannot bind is a session it cannot see, and a removal guard must
    never read that as "nothing to block on".
    """
    from ..launch.profile import harness_store_for, StoreBindingError

    resolved_env = dict(env) if env is not None else dict(os.environ)
    found: dict[tuple[str, tuple[tuple[str, str], ...]], object] = {}
    for config in groups or []:
        try:
            store = harness_store_for(config, env=resolved_env)
        except StoreBindingError as e:
            if on_drop is not None:
                on_drop(config, e)
            else:
                name = (config.get("group") or {}).get("name", "?")
                print(
                    f"camp: could not address group {name!r}'s credential "
                    f"store — {e}",
                    file=sys.stderr,
                )
            continue
        if store is None:
            continue
        key = (_harness_display_name(store), _store_binding_key(store))
        found.setdefault(key, store)

    try:
        default_store = harness_store_for({}, env=resolved_env)
    except StoreBindingError:
        # No declaring group to name for the anonymous default probe — a
        # bind failure here degrades exactly like the harness-unnameable
        # case it sits alongside.
        default_store = None
    if default_store is not None:
        key = (_harness_display_name(default_store), _store_binding_key(default_store))
        found.setdefault(key, default_store)

    return list(found.values())


def _store_binding_key(store) -> tuple[tuple[str, str], ...]:
    """The dedupe key for *store*: its BOUND environment, not the raw declared
    account string.

    Two groups can declare the same credential-store directory under two
    different spellings (``/acct/w`` vs. ``/acct/w/``, or ``~/x`` vs. its
    expansion) — textually different, but the harness binds both to the same
    resolved store, so ``store.env`` (the environment queries against this
    store actually run under, per :func:`~camp.launch.profile.harness_store_for`)
    is byte-identical between them. Keying on that resolved binding rather than
    on ``store.account`` collapses such spellings to one pool entry, so the
    same store is never read twice and its sessions never listed twice.
    ``store.account`` itself is untouched by this — it still carries the
    declaring group's string verbatim.
    """
    return tuple(sorted(store.env.items()))


def _parsable_groups() -> list[dict]:
    """Every group config camp can PARSE — a malformed sibling contributes nothing.

    One unreadable toml elsewhere in the config directory must not make every
    other group unaddressable. That is precisely the situation a stop is reached
    for from a phone: something is already broken, and the verb that reclaims
    memory has to still answer.

    Deliberately not the loader every group-resolved verb uses. Those verbs act
    ON a group and must refuse rather than act against a config camp misread;
    this pool only supplies the name rule with the containers it knows about, and
    a missing container costs a nicer derived name, never correctness.
    """
    from ..group.config import load_group
    from .common import _groups_dir

    directory = _groups_dir()
    if not directory.is_dir():
        return []
    configs: list[dict] = []
    for path in sorted(directory.glob("*.toml")):
        try:
            configs.append(load_group(path))
        except Exception:  # noqa: BLE001 — one broken config never hides the rest
            continue
    return configs


# ---------------------------------------------------------------------------
# camp attach's door — creates, connects, or resurrects the workspace
# _resolve_group_for_attach resolved a slug against, then hands the terminal
# over. Shared by _cmd_attach_cli below and camp new's own door dispatch
# (cli/group.py's _door_dispatch_for_new).
# ---------------------------------------------------------------------------


def _resolve_group_for_attach(
    groups: list[dict], group_override: str | None, *, env: dict[str, str]
) -> dict | None:
    """The one group a slug is resolved against, for `camp attach`'s new
    workspace precedence — `--group` if given, else the same
    `resolve_from_cwd` every other group-resolved verb uses
    (`cli/dispatch.py`'s `_resolve_group_for_command`).

    Returns `None` on ANY failure — an unknown `--group`, a cwd that
    resolves to no group, a cwd matching more than one, or a group config
    missing a key `resolve_from_cwd` needs — never raises. A sibling
    group's malformed config, or simply running `camp attach` from
    somewhere no group claims, must never raise out of an attach; the caller
    refuses with the standard needs-group line instead, the same tolerance
    `_parsable_groups` already applies to this same `groups` list.
    """
    from ..group.resolve import resolve_from_cwd, resolve_group_override

    try:
        if group_override:
            return resolve_group_override(group_override, groups)
        group_name, _slug = resolve_from_cwd(Path.cwd(), groups, env=env)
        return next((g for g in groups if g["group"]["name"] == group_name), None)
    except Exception:  # noqa: BLE001 — see docstring: never blocks an attach
        return None


def _refuse_door(outcome, reason: str, *, as_json: bool) -> NoReturn:
    """One refusal for `_open_workspace_door`: `camp attach: <reason>` on
    stderr under the plain form, or `{"ok": false, "outcome": <word or
    null>, "reason": <reason>}` on stdout under `--json` — the same
    `ok`-flagged shape every other `camp attach` JSON answer already
    carries. `outcome` is the machine-readable word for the two
    tmux-boundary refusals (`RefusedCreateFailed` vs `RefusedCreateRefused`
    render distinct words, so a `--json` consumer can tell a policy
    refusal from a transient tmux failure without parsing `reason`);
    `None` when *outcome* defines none. *reason* is escaped through
    `printable_path`, composed whole rather than field by field, because
    it can carry tmux's own stderr verbatim. `camp.launch.door`'s own
    docstring is explicit that a refusal's message is composed by the code
    that constructs it, never read back out of that module.
    """
    from ..launch.door import exit_status, refusal_outcome_word
    from ..launch.recovery import printable_path
    from ..spine import _die

    reason = printable_path(reason)
    if as_json:
        print(json.dumps({"ok": False, "outcome": refusal_outcome_word(outcome), "reason": reason}))
        sys.exit(exit_status(outcome))
    _die(f"camp attach: {reason}")


def _print_reconcile_outcome(reconcile_outcome) -> None:
    """Print the connect arm's reconciliation to stderr, one line per
    change, before the door's own outcome line — or, when reconciliation
    could not run at all, the one line naming why.

    `None` means no reconciliation was attempted (the create arm has no
    session to read a record against yet) and prints nothing. Every other
    outcome renders through `window_reconcile.render_reconcile_lines`, the
    one rendering `camp stop` prints through too.
    """
    from ..launch.window_reconcile import render_reconcile_lines

    if reconcile_outcome is None:
        return
    for line in render_reconcile_lines(reconcile_outcome):
        print(line, file=sys.stderr)


def _print_resurrection_lines(resurrection) -> None:
    """Print resurrection's own drop/failure/restamp-failure lines to
    stderr, before the door's own outcome line — the resurrection analog of
    `_print_reconcile_outcome`."""
    from ..launch.resurrect import render_resurrection_lines

    for line in render_resurrection_lines(resurrection):
        print(line, file=sys.stderr)


def _door_success_outcome(
    probe,
    *,
    slug: str,
    group: str,
    workspace_path,
    interactive: bool,
):
    """Print *probe*'s own stderr lines and build the door outcome it
    reports — the success fold both doors share (`camp attach`'s
    `_open_workspace_door` and `camp new`'s
    `cli/group.py:_door_dispatch_for_new`), so the two verbs can never
    disagree on what a created, connected, or resurrected session says.

    A `RESURRECTED` probe prints resurrection's drop/failure lines and
    counts its windows into a :class:`~camp.launch.door.Resurrected`; every
    other success prints the connect arm's reconciliation (nothing at all
    on the create arm, which has no record to read yet) and builds
    :class:`~camp.launch.door.Created` or
    :class:`~camp.launch.door.Connected`. What each caller does with the
    returned outcome — which stream it renders on, and whether it exits on
    its status — stays at the call site.
    """
    from ..launch.door import Connected, Created, Resurrected
    from ..launch.workspace_session import DoorState

    if probe.state is DoorState.RESURRECTED:
        _print_resurrection_lines(probe.resurrection)
        return Resurrected(
            slug=slug,
            group=group,
            tmux_session=probe.session_name,
            workspace_path=workspace_path,
            attached=interactive,
            restored=len(probe.resurrection.restored),
            failed=len(probe.resurrection.failed),
            dropped=len(probe.resurrection.dropped),
        )

    _print_reconcile_outcome(probe.reconcile_outcome)
    outcome_cls = Created if probe.state is DoorState.CREATED else Connected
    return outcome_cls(
        slug=slug,
        group=group,
        tmux_session=probe.session_name,
        workspace_path=workspace_path,
        attached=interactive,
    )


def _open_workspace_door(
    target: "ResolvedWorkspace",
    *,
    tmux,
    resolved_env: dict[str, str],
    as_json: bool,
    interactive: bool,
    harness=None,
    group: dict | None = None,
) -> None:
    """Create, connect, or resurrect the workspace `target` resolved to,
    then hand the terminal over — `camp attach`'s door.

    The tmux boundary is
    `launch.workspace_session.create_or_connect_workspace_session` (the
    probe, the create, the race re-probe, and — once the workspace has a
    window record with entries — the resurrection dispatch, shared with
    `camp new`'s door at `cli/group.py:_door_dispatch_for_new`); the
    handover is `host.handoff.hand_over_to_session` (the exec and
    `switch-client` arms). What is `camp attach`'s alone, and stays here, is
    that every failure state is a refusal — the session is all this verb
    has, so an unanswered tmux, a failed create, a create refused by
    policy, or an unreadable window record each end in `camp attach: …` and
    a non-zero exit, where `camp new` reports a workspace-only success.
    `harness` is forwarded to the resurrection planner unchanged (`None`
    when the caller could not resolve one for the group) — it decides only
    what a resurrected window's stub prints. `group` is forwarded alongside
    it, so a resurrected conversation's resume line binds the same account
    a live window would.

    Success prints the outcome line on stdout (or the `--json` object),
    then hands over. `attached` in that report is true on both handover
    arms and false whenever `interactive` is false — without a terminal the
    session is still created, connected, or resurrected, but nothing is
    handed over and neither handover seam is touched. Every success is
    folded by `_door_success_outcome`, shared with `camp new`, which prints
    a `RESURRECTED` probe's drop/failure lines to stderr first, exactly the
    way `CONNECTED`'s reconciliation does; a partial resurrection's exit
    status (2, via `exit_status`) applies only on the non-interactive path —
    an interactive attach still hands the terminal over regardless.
    """
    from ..host.handoff import hand_over_to_session
    from ..launch.door import (
        RefusedCreateFailed,
        RefusedCreateRefused,
        RefusedRecordUnreadable,
        RefusedTmuxUnanswered,
        exit_status,
        render_human,
        render_json,
    )
    from ..launch.workspace_session import DoorState, create_or_connect_workspace_session

    probe = create_or_connect_workspace_session(
        target.group, target.slug, target.path, env=resolved_env, tmux=tmux, harness=harness,
        group=group,
    )

    if probe.state is DoorState.TMUX_UNANSWERED:
        _refuse_door(RefusedTmuxUnanswered(), probe.reason, as_json=as_json)
        return
    if probe.state is DoorState.CREATE_FAILED:
        _refuse_door(RefusedCreateFailed(), probe.reason, as_json=as_json)
        return
    if probe.state is DoorState.CREATE_REFUSED:
        _refuse_door(RefusedCreateRefused(), probe.reason, as_json=as_json)
        return
    if probe.state is DoorState.RECORD_UNREADABLE:
        _refuse_door(RefusedRecordUnreadable(), probe.reason, as_json=as_json)
        return

    outcome = _door_success_outcome(
        probe,
        slug=target.slug,
        group=target.group,
        workspace_path=target.path,
        interactive=interactive,
    )

    if as_json:
        print(json.dumps(render_json(outcome)))
    else:
        print(render_human(outcome))

    if not interactive:
        sys.exit(exit_status(outcome))

    hand_over_to_session(tmux, probe.session_name, env=resolved_env)


# ---------------------------------------------------------------------------
# camp attach — hand the operator's terminal to a workspace's session
#
# `<slug> --host <name>` (`_cmd_attach_host_cli`) is dispatched before
# `camp.spine`'s fallback is ever reached — see that function's own
# docstring. `-a`/`--all-hosts` in any form is refused directly by
# `camp.cli.dispatch.main`'s `--all-hosts` handling, before a group is ever
# resolved or this function is ever called.
# ---------------------------------------------------------------------------


def _cmd_attach_cli(args: list[str], env: dict[str, str] | None = None) -> None:
    """camp attach [<slug>] [--group <name>].

    *<slug>* resolves against one group's own workspaces — `--group` if
    given, else the group `resolve_from_cwd` finds for the working
    directory (`_resolve_group_for_attach`, the same resolution `camp stop`
    uses). No resolvable group refuses with the needs-group line every
    other group-resolved verb prints, whether or not a slug was given. A
    slug naming no workspace in the resolved group refuses in the door's
    own words (`attach.door_target.refusal_message`'s `NotAWorkspace`
    branch); a slug naming one opens the door
    (`_open_workspace_door`). With no slug and a terminal, the door's own
    numbered picker over the group's workspaces runs instead
    (`attach.door_target.resolve_attach_target`'s bare form).

    `--resolve` and `--list` are the two machine-readable probes the
    retired cross-host picker (`camp attach -a`) used to issue against
    every declared machine — refused here, before any group is resolved or
    any workspace listing read, with the same retired-flag line
    `cli/dispatch.py`'s `-a` handling prints for the flag itself.
    """
    from ..spine import _die
    from ..workspace.verb_taxonomy import needs_group_message

    parser = CampParser(verb="attach")
    parser.add_argument("--group")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--resolve", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("refs", nargs="*")
    parsed = parser.parse_args(args)

    if parsed.resolve or parsed.list:
        # Refused ahead of every other check, including argument-shape
        # validation below: neither probe is a spelling of an ordinary
        # attach, and no group needs resolving, no workspace listing needs
        # reading, and no tmux seam needs touching to answer it.
        _die(
            "camp attach: -a is retired — find the workspace with "
            "'camp list -ag', then 'camp attach <slug> --host <name>'"
        )

    as_json = parsed.json
    rest = parsed.refs
    if len(rest) > 1:
        _die(
            f"camp attach: one workspace slug, not {len(rest)} — an attach "
            "addresses exactly one workspace"
        )
    ref = rest[0] if rest else None
    if ref is not None and not ref.strip():
        _die("camp attach: requires a workspace slug")
    if ref is not None and ref.startswith("-"):
        _die(
            f"camp attach: {ref!r} looks like a flag, not a workspace slug — "
            "a slug may not start with a dash"
        )

    resolved_env = dict(env) if env is not None else dict(os.environ)
    groups = _parsable_groups()
    target_group = _resolve_group_for_attach(groups, parsed.group, env=resolved_env)
    if target_group is None:
        _die(needs_group_message("attach"))

    from ..attach.door_target import (
        ResolvedWorkspace,
        WorkspaceCandidate,
        refusal_message,
        resolve_attach_target,
    )
    from ..launch.inventory import format_state
    from ..launch.profile import harness_for
    from ..launch.stop import Tmux
    from ..provision.lifecycle import cmd_ls_group

    group_name = target_group["group"]["name"]
    tmux = Tmux()

    def _group_workspaces(
        group=target_group, tmux=tmux, env=resolved_env
    ) -> list["WorkspaceCandidate"]:
        listing = cmd_ls_group(group, env=env, tmux=tmux)
        return [
            WorkspaceCandidate(
                slug=e["slug"],
                path=Path(e["workspace_path"]),
                state_text=format_state(e.get("state"), e.get("window_count")),
            )
            for e in listing.entries
        ]

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    target = resolve_attach_target(
        ref,
        group_name=group_name,
        workspaces=_group_workspaces,
        isatty=interactive,
        stdin=sys.stdin,
        stdout=sys.stdout,
    )

    if isinstance(target, ResolvedWorkspace):
        _open_workspace_door(
            target,
            tmux=tmux,
            resolved_env=resolved_env,
            as_json=as_json,
            interactive=interactive,
            harness=harness_for(target_group),
            group=target_group,
        )
        return

    # Every remaining outcome — NotAWorkspace, the two door refusals, or a
    # PoolUnreadable EOF mid-picker — composes through refusal_message,
    # which raises for anything it does not recognise rather than let an
    # unhandled outcome print nothing and exit 0.
    _die(refusal_message(target, group_name=group_name))


def _cmd_attach_host_cli(
    args: list[str],
    host: "Host",
    host_name: str,
    env: dict[str, str] | None = None,
    *,
    connect_timeout: float | None = None,
) -> None:
    """camp attach <slug> --host <name> [--group <g>] — carry the slug, and
    the group it resolves to, across to the far side's own door.

    The far side has no group axis of its own to resolve *ref* against — a
    bare `camp attach <ref>` run over `ssh` sees `$HOME`, not the
    operator's own cwd, so no group would ever resolve there. This
    resolves the group LOCALLY instead, exactly the way a local attach
    does (`_resolve_group_for_attach`: `--group` if given, else the group
    `resolve_from_cwd` finds for the working directory this was invoked
    from), then forwards the resolved name so the far side's own `camp
    attach <ref> --group <g>` decides and refuses in its own words
    (`docs/design/attaching-reaches-a-running-session-on-any-machine.md`,
    "Resolution is not the same question on each axis"). No group
    resolving locally is refused locally, with the same needs-group line
    every other group-resolved verb prints — no machine is contacted. The
    nested-multiplexer warning is still decided from THIS machine's own
    environment before the handoff — it is a property of the local
    terminal, not of the target — per that same design doc's "The
    key-prefix conflict warning is decided locally".

    ``connect_timeout`` is the operator's resolved value
    (`camp.host.config.connect_timeout_seconds()`, read once by `main()`'s
    `--host` handling), threaded to the interactive ssh handoff exactly as
    it is to every other `--host` verb — `None` falls back to the
    transport's own documented default, for a caller with no resolved value
    in hand.
    """
    from ..attach.prefix_warning import warn_if_nested
    from ..group.resolve import (
        validate_group_name,
        resolve_group_override,
        GroupConfinementError,
        GroupResolutionError,
    )
    from ..host.handoff import handoff, remote_argv
    from ..host.transport import DEFAULT_CONNECT_TIMEOUT_SECONDS
    from ..spine import _die
    from ..workspace.verb_taxonomy import needs_group_message

    if connect_timeout is None:
        connect_timeout = DEFAULT_CONNECT_TIMEOUT_SECONDS

    parser = CampParser(verb="attach")
    parser.add_argument("--group")
    parser.add_argument("refs", nargs="*")
    parsed = parser.parse_args(args)
    rest = parsed.refs
    if len(rest) != 1:
        _die(
            f"camp attach: --host requires exactly one workspace slug, got "
            f"{len(rest)}"
        )
    ref = rest[0]
    if not ref.strip() or ref.startswith("-"):
        _die(f"camp attach: {ref!r} is not a valid workspace slug")

    resolved_env = dict(env) if env is not None else dict(os.environ)
    groups = _parsable_groups()
    # An explicit `--group` naming a group this machine has no config for is
    # a different failure than no group resolving from cwd at all —
    # `_resolve_group_for_attach` folds both into the same `None`
    # (its docstring: "Returns None on ANY failure"), which would otherwise
    # tell the operator to pass a group they already passed. Calling
    # `resolve_group_override` directly here, ahead of that fold, surfaces
    # the distinct answer instead.
    if parsed.group:
        try:
            target_group = resolve_group_override(parsed.group, groups)
        except GroupResolutionError:
            known = [g["group"]["name"] for g in groups]
            _die(
                f"camp attach: group {parsed.group!r} is not configured on "
                f"this machine (known: {', '.join(known) or 'none'})"
            )
    else:
        target_group = _resolve_group_for_attach(groups, None, env=resolved_env)
        if target_group is None:
            _die(needs_group_message("attach"))
    group_name = target_group["group"]["name"]
    try:
        validate_group_name(group_name)
    except GroupConfinementError as exc:
        _die(str(exc))

    warn_if_nested(resolved_env)
    handoff(remote_argv(host, ref, group=group_name, connect_timeout=connect_timeout))
