"""`camp transfer-probe` and `camp transfer` — the transfer preflight's two ends.

`camp transfer-probe` is documented in its own handler below. This module also
carries `camp transfer <slug> --to <peer> [--dry-run] [--overwrite] [--json]`
— the operator-facing verb, dispatched from `cli/dispatch.py`'s group-aware
router exactly like `camp status`. It is the one place that performs every
local read (self-declared name, the workspace manifest, `hosts.toml`, each
member's declared `excluded` set, the conversation pool) and the one peer read
(`camp.transfer.probe.probe_peer`), then hands the *results* of those reads to
`camp.transfer.preflight.compose_preflight` — which stays pure data-to-data,
per its own module docstring. Every rendering decision and every exit code
below is this module's to make; `compose_preflight` makes none of them.

**The same preflight governs both paths.** `--dry-run` renders the composed
checks and stops. Its absence runs the identical composition and, only on a
clean verdict, drives `camp.transfer.move.move_workspace` — begin, then per
member history and worktree, then finish. Any check that is not PASSED
refuses on either path, before `move_workspace` is ever called, so a member
that never declared an `excluded` set (or any other failing check) moves
nothing whether or not `--dry-run` was given — see
`camp.transfer.preflight`'s check 10 and this module's `_exit_code_for`.

**`--overwrite`** is the one flag specific to the moving path: it is threaded
to `move_workspace`, and from there to `transfer-receive begin`, whose own
refusal it lifts — see `camp.transfer.receive`'s module docstring for what it
guards. Omitted, a workspace already present on the peer and owned by this
host refuses (`EXIT_OVERWRITE_REQUIRED`) rather than being torn down by the
same keystrokes an operator has muscle memory for from the read-only phase.

EXIT CODES — the whole closed set `camp transfer` can return, in the order its
inputs are checked:

  0  EXIT_WOULD_TRANSFER      every check passed — a clean verdict with
                               `--dry-run`, or (without it) the transfer
                               completed
  1  EXIT_ERROR               an unexpected/local error: a missing required
                               flag, a malformed hosts.toml or group config
  3  EXIT_NOT_CLEAN           the verdict is NOT_CLEAN for a reason with no
                               more specific code below (this host declared no
                               name, the peer's name collides with this host's,
                               the peer's group/account/slug/excluded-set
                               checks failed, or conversations could not be
                               enumerated)
  4  EXIT_OWNERSHIP_REFUSED   this host does not own the workspace — the
                               refusal names the owning host and the remedy
  5  EXIT_PEER_UNREACHABLE   the peer could not be reached (any of the seven
                               `camp.host.transport.TransportOutcome` kinds)
  6  EXIT_UNKNOWN_SLUG        no workspace manifest is recorded for this slug
                               on this host
  7  EXIT_UNKNOWN_PEER        the named peer is not declared in hosts.toml
  8  EXIT_OVERWRITE_REQUIRED  the workspace already exists on the peer, owned
                               by this host, and `--overwrite` was not passed
                               — nothing crossed
  9  EXIT_PHASE_FAILED        a move phase (begin/history/worktree/finish)
                               failed after the preflight passed — nothing
                               after the failing phase ran; re-running the
                               transfer is safe

Code 2 is absent from the set deliberately: it is never produced, and it is
held unused rather than reassigned, so a script that checks for it
specifically gets a clear "not that" answer rather than a code that means
something unrelated.

Selected by walking `PreflightResult.checks` in their fixed order (see
`camp.transfer.preflight`'s module docstring) and taking the first check that
is not PASSED — the same order the checks themselves are evaluated and
rendered in, so the exit code always names the FIRST thing an operator would
read as wrong, never a later one that happens to sort first some other way.
`EXIT_NOT_CLEAN` is the deliberate shared code for every check this table does
not call out by name: check 1 (self-declared name) and checks 6-11 (peer name
collision, peer group/account/slug checks, the excluded-set declaration, and
conversation enumeration) do not need their own operator-visible exit code —
their distinguishing detail is carried in the rendered check text and (for
`--json`) in the check's own `status`/`detail`/`transport_outcome` fields, not
in the process exit code. `EXIT_OVERWRITE_REQUIRED` and `EXIT_PHASE_FAILED`
are never produced by the preflight itself — they come from
`camp.transfer.move.move_workspace`, reached only once every check has
PASSED.
"""

from __future__ import annotations

import json
import sys


def _cmd_transfer_probe_cli(args: list[str]) -> None:
    from ..group.config import GroupConfigError, load_all_groups
    from ..host.config import HostConfigError, self_host_name
    from ..spine import _consume_flag_value
    from ..transfer.probe import build_probe_answer
    from .common import _groups_dir

    group_name = _consume_flag_value(args, "--group")
    if not group_name:
        print("camp transfer-probe: --group is required", file=sys.stderr)
        sys.exit(1)

    slug = _consume_flag_value(args, "--slug")
    if not slug:
        print("camp transfer-probe: --slug is required", file=sys.stderr)
        sys.exit(1)

    try:
        self_name = self_host_name()
    except HostConfigError as e:
        print(f"camp transfer-probe: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        groups = load_all_groups(_groups_dir())
    except GroupConfigError as e:
        print(f"camp transfer-probe: {e}", file=sys.stderr)
        sys.exit(1)

    answer = build_probe_answer(
        group_name=group_name,
        slug=slug,
        groups=groups,
        self_name=self_name,
    )
    print(json.dumps(answer))


# ---------------------------------------------------------------------------
# camp transfer-receive — the peer side of a workspace move
# ---------------------------------------------------------------------------


#: The closed set of phases `camp transfer-receive` admits, in dispatch
#: order. Each name is also the `camp.transfer.receive` entry point it
#: dispatches to, so admitting a new phase is a one-line addition here plus —
#: only if it carries arguments of its own — a branch in the argument
#: gathering below, never a second closed-set site.
_PHASES = ("begin", "conversations", "finish", "history", "worktree")


def _cmd_transfer_receive_cli(args: list[str]) -> None:
    """camp transfer-receive begin|finish|history|worktree --group <g> --slug <s> [...]

    Dispatched here exactly like `camp transfer-probe` — every local read and
    write is this function's (and `camp.transfer.receive`'s) to make; no path
    is ever built from a caller-supplied field. See `camp.transfer.receive`'s
    module docstring for each phase's contract.
    """
    from ..group.config import GroupConfigError, load_all_groups
    from ..spine import _consume_flag_value
    from ..transfer import receive as receive_mod
    from .common import _groups_dir

    if not args or args[0] not in _PHASES:
        got = args[0] if args else None
        phases = "', '".join(_PHASES)
        print(
            f"camp transfer-receive: a phase of '{phases}' is "
            f"required, got {got!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    phase = args[0]
    rest = args[1:]

    group_name = _consume_flag_value(rest, "--group")
    if not group_name:
        print("camp transfer-receive: --group is required", file=sys.stderr)
        sys.exit(1)

    slug = _consume_flag_value(rest, "--slug")
    if not slug:
        print("camp transfer-receive: --slug is required", file=sys.stderr)
        sys.exit(1)

    try:
        groups = load_all_groups(_groups_dir())
    except GroupConfigError as e:
        print(f"camp transfer-receive: {e}", file=sys.stderr)
        sys.exit(1)

    # Each branch below gathers only the arguments its own phase adds; the
    # call/refusal/print tail is shared, so every phase answers on stdout and
    # refuses on stderr in exactly one way. `phase` is looked up on
    # `receive_mod` by name, which is safe precisely because it was already
    # checked against the closed `_PHASES` set above — a caller cannot reach
    # any other attribute of that module through it.
    phase_kwargs: dict = {}
    if phase == "begin":
        owner = _consume_flag_value(rest, "--owner")
        if not owner:
            print("camp transfer-receive: --owner is required for begin", file=sys.stderr)
            sys.exit(1)
        phase_kwargs = {"sender": owner, "overwrite": "--overwrite" in rest}
    elif phase == "conversations":
        session_id = _consume_flag_value(rest, "--session-id")
        if not session_id:
            print(
                "camp transfer-receive: --session-id is required for conversations",
                file=sys.stderr,
            )
            sys.exit(1)
        subpath = _consume_flag_value(rest, "--subpath")
        if not subpath:
            print(
                "camp transfer-receive: --subpath is required for conversations",
                file=sys.stderr,
            )
            sys.exit(1)
        phase_kwargs = {
            "session_id": session_id,
            "subpath": subpath,
            "archive_stream": sys.stdin.buffer,
        }
    elif phase in ("history", "worktree"):
        member = _consume_flag_value(rest, "--member")
        if not member:
            print(
                f"camp transfer-receive: --member is required for {phase}",
                file=sys.stderr,
            )
            sys.exit(1)
        phase_kwargs = {"member": member}
        if phase == "history":
            phase_kwargs["bundle_bytes"] = sys.stdin.buffer.read()
        else:
            phase_kwargs["archive_stream"] = sys.stdin.buffer

    try:
        answer = getattr(receive_mod, phase)(
            groups=groups,
            group_name=group_name,
            slug=slug,
            **phase_kwargs,
        )
    except receive_mod.ReceiveRefused as e:
        print(f"camp transfer-receive: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(answer))


# ---------------------------------------------------------------------------
# camp transfer — the operator-facing dry-run verb
# ---------------------------------------------------------------------------

EXIT_WOULD_TRANSFER = 0
EXIT_ERROR = 1
EXIT_NOT_CLEAN = 3
EXIT_OWNERSHIP_REFUSED = 4
EXIT_PEER_UNREACHABLE = 5
EXIT_UNKNOWN_SLUG = 6
EXIT_UNKNOWN_PEER = 7
EXIT_OVERWRITE_REQUIRED = 8
EXIT_PHASE_FAILED = 9

#: Check name -> exit code, for the checks that get their own. Looked up by
#: `_exit_code_for` while walking `PreflightResult.checks` in order; a check
#: name absent from this table (or present but not the qualifying status)
#: falls through to `EXIT_NOT_CLEAN`. Keyed on the exact `Check.name` strings
#: `camp.transfer.preflight.compose_preflight` produces — those strings are
#: this module's only handle on which of the eleven checks is which, since
#: `Check` itself carries no separate machine-readable id.
_EXIT_BY_CHECK_NAME = {
    "the workspace exists here": EXIT_UNKNOWN_SLUG,
    "this host owns it, or it was never recorded": EXIT_OWNERSHIP_REFUSED,
    "the named peer is declared": EXIT_UNKNOWN_PEER,
}


def _exit_code_for(result) -> int:
    """The one exit code for a composed `PreflightResult`. See the module
    docstring's EXIT CODES table for the full closed set and the reasoning."""
    from ..transfer.preflight import CheckStatus, Verdict

    if result.verdict is Verdict.WOULD_TRANSFER:
        return EXIT_WOULD_TRANSFER

    for check in result.checks:
        if check.status is CheckStatus.PASSED:
            continue
        if check.name == "the peer answers" and check.status is CheckStatus.INDETERMINATE:
            return EXIT_PEER_UNREACHABLE
        return _EXIT_BY_CHECK_NAME.get(check.name, EXIT_NOT_CLEAN)

    return EXIT_NOT_CLEAN  # pragma: no cover - unreachable: NOT_CLEAN implies a non-passed check


def _transport_outcome_payload(outcome) -> dict:
    """JSON-serialize a `TransportOutcome`: its kind name, plus whatever
    fields that subtype carries — never re-derived, carried verbatim from the
    same object `Check.transport_outcome` holds."""
    payload: dict = {"kind": type(outcome).__name__}
    reason = getattr(outcome, "reason", None)
    if reason is not None:
        payload["reason"] = reason
    execution_timeout = getattr(outcome, "execution_timeout", None)
    if execution_timeout is not None:
        payload["execution_timeout"] = execution_timeout
    if hasattr(outcome, "exit_code"):
        payload["exit_code"] = outcome.exit_code
        payload["stderr"] = outcome.stderr
    return payload


def _check_payload(check) -> dict:
    from ..transfer.preflight import CheckStatus

    return {
        "ok": check.status is CheckStatus.PASSED,
        "name": check.name,
        "status": check.status.value,
        "detail": check.detail,
        "transport_outcome": (
            _transport_outcome_payload(check.transport_outcome)
            if check.transport_outcome is not None
            else None
        ),
    }


def _conversation_payload(conversation) -> dict:
    return {
        "session_id": conversation.session_id,
        "subpath": str(conversation.subpath) if conversation.subpath is not None else None,
        "live": conversation.live,
        "unresolved": conversation.unresolved,
    }


def _regenerated_payload(member) -> dict:
    return {"member": member.name, "excluded": list(member.excluded or ())}


def _json_payload(result, *, slug: str, peer_name: str) -> dict:
    from ..transfer.preflight import Verdict

    return {
        "ok": result.verdict is Verdict.WOULD_TRANSFER,
        "verdict": "would_transfer" if result.verdict is Verdict.WOULD_TRANSFER else "not_clean",
        "slug": slug,
        "peer": peer_name,
        "checks": [_check_payload(c) for c in result.checks],
        "conversations": [_conversation_payload(c) for c in result.conversations],
        "regenerated": [_regenerated_payload(m) for m in result.regenerated],
    }


_STATUS_MARKER = {
    "passed": "PASS",
    "failed": "FAIL",
    "indeterminate": "INDETERMINATE",
}


def _render_human(result, *, slug: str, peer_name: str) -> None:
    """Render the composition for a human.

    Two of the strings here originate outside camp — a check detail carrying a
    refused peer's own stderr, and a conversation subpath read out of a
    transcript the harness wrote — so both go through
    `camp.launch.recovery.printable_path`, camp's established escaper for text
    it does not author. Its docstring carries the reason: a bare carriage
    return plus an erase sequence rewrites a line already printed, and the
    operator then reads a verdict camp never gave.
    """
    from ..launch.recovery import printable_path
    from ..transfer.preflight import Verdict

    print(f"camp transfer: {slug!r} -> {peer_name!r}")
    for check in result.checks:
        marker = _STATUS_MARKER[check.status.value]
        print(f"  [{marker}] {check.name}: {printable_path(check.detail)}")

    if not result.conversations:
        print("  no conversations are rooted in this workspace")
    else:
        for conversation in result.conversations:
            if conversation.unresolved:
                print(f"    ? {conversation.session_id} — root could not be resolved")
            else:
                live_tag = " (live)" if conversation.live else ""
                subpath = printable_path(conversation.subpath)
                print(f"    {conversation.session_id} @ {subpath}{live_tag}")

    if not result.regenerated:
        print("  no member declares state that would be regenerated")
    else:
        print("  regenerated on arrival rather than copied:")
        for member in result.regenerated:
            declared = ", ".join(printable_path(e) for e in member.excluded or ())
            print(f"    {member.name}: {declared}")

    verdict_text = "would transfer" if result.verdict is Verdict.WOULD_TRANSFER else "not clean"
    print(f"camp transfer: verdict — {verdict_text}")


def _gather_conversations(*, group_name: str, slug: str, session_groups, resolved_env):
    """Every conversation rooted in this workspace, or `None` when camp could
    not enumerate them — fail-closed, mirroring `camp remove`'s own session
    guard (`camp.cli.lifecycle._cmd_remove_group_cli`), which this reuses
    verbatim: `_addressable_harnesses` for the pool of harnesses camp can ask,
    `teardown_guard.gather_pool` to read it (or raise), then
    `camp.transfer.conversations.workspace_conversations` to scope the pool to
    this workspace. `None` here becomes check 11's own FAILED report in
    `compose_preflight` — never a crash and never a silent empty answer.
    """
    from ..group.manifest import workspace_dir
    from ..launch import teardown_guard
    from ..transfer.conversations import EnumerationUnavailable, workspace_conversations
    from .lifecycle import _refuse_on_dropped_store
    from .session import _addressable_harnesses

    try:
        transcripts, live = teardown_guard.gather_pool(
            _addressable_harnesses(
                session_groups, env=resolved_env, on_drop=_refuse_on_dropped_store
            ),
            env=resolved_env,
        )
        return workspace_conversations(
            workspace_dir(group_name, slug, env=resolved_env),
            transcripts=transcripts,
            live_records=live,
            groups=session_groups,
            env=resolved_env,
        )
    except (teardown_guard.EnumerationUnavailable, EnumerationUnavailable):
        return None


def _locate_transcript(session_groups, resolved_env):
    """A `locate_transcript` callable shaped like
    `Harness.session_transcript_path` for `move.move_workspace`'s
    `conversations` phase: tries every store in `_addressable_harnesses`'
    pool — the same pool `_gather_conversations` reads — in turn, until one
    names a file. Reaches the harness only through
    `HarnessStore.session_transcript_path`, never by naming a projects
    directory itself.
    """
    from .session import _addressable_harnesses

    stores = _addressable_harnesses(session_groups, env=resolved_env)

    def _locate(session_id: str, root):
        for store in stores:
            path = store.session_transcript_path(session_id, root, env=store.env)
            if path is not None:
                return path
        return None

    return _locate


_MISSING_SELF_NAME_CHECK = "this host has declared a name"


def _missing_self_name_remedy(env: dict[str, str]) -> str:
    """The file to create and the constraint on what to put in it.

    Named here, at the CLI layer that already knows the concrete path and
    performs every rendering decision — `camp.host.config.self_host_name`
    stays a plain "declared or not" read with no rendering opinion of its
    own, and `camp.transfer.preflight` stays pure data-to-data with no path
    to resolve. The refusal is the primary discovery path for this file, not
    the README (see the README's own note on this), so it names the file and
    the different-names requirement rather than pointing elsewhere.
    """
    import trailhead.paths as _paths

    path = _paths.config_dir("camp", env=env) / "hosts.toml"
    return (
        f"create {path} with `self_name = \"<name>\"` — choose a name that "
        "differs from every peer's own self_name declared there; two hosts "
        "declaring the same name pass every ownership check on both sides "
        "at once, silently, since the loader never compares names across "
        "machines"
    )


def _augment_missing_self_name_check(result, *, env: dict[str, str]):
    """Rewrite check 1's FAILED detail to name the remedy, leaving every
    other check (and the verdict) untouched. See `_missing_self_name_remedy`."""
    from dataclasses import replace

    from ..transfer.preflight import CheckStatus

    augmented = tuple(
        replace(check, detail=f"{check.detail} — {_missing_self_name_remedy(env)}")
        if check.name == _MISSING_SELF_NAME_CHECK and check.status is CheckStatus.FAILED
        else check
        for check in result.checks
    )
    return replace(result, checks=augmented)


def _render_move_completion(
    move_result, *, slug: str, peer_name: str, group_name: str, self_name: str
) -> None:
    """The report printed once `move_workspace` returns successfully.

    Distinguishes "arrived" from "ready to work in" (regeneration is spawned,
    not awaited — see `camp.transfer.receive`'s `finish`), states plainly
    that ownership did not move, names the consequence of that (work the
    peer accumulates has no way back to the sender, so a later --overwrite
    destroys it), and names the interim risk that nothing scans what crossed
    for credential-shaped content — untracked files routinely carry them and
    the peer now holds a cleartext copy.

    *move_result* is `move.MoveResult`, not the preflight preview: it names
    only conversations the `conversations` phase actually placed on the
    peer. A row the preflight showed as UNRESOLVED never reaches this
    function at all — `move_workspace`'s `conversations` phase raises
    `PhaseFailed` on such a row before `move_workspace` returns, so there is
    no "could not be resolved" case for a *successful* move to render; see
    `camp.transfer.move.ConversationCrossed`.

    Each arrived conversation gets the literal command that resumes it —
    `camp launch --resume <session-id>`, the same reference-addressed resume
    flavor `camp.cli.session._launch_resume` implements — rather than an
    identifier the operator would have to turn into a command themselves.
    """
    from ..launch.recovery import printable_path

    print(f"camp transfer: {slug!r} arrived on {peer_name!r}")
    print(f"  ownership did not move — {self_name!r} still owns {slug!r}")
    print(
        f"  work accumulated on {peer_name!r} after this point has no way "
        f"back to {self_name!r} — a later transfer of {slug!r} with "
        "--overwrite would destroy any uncommitted or untracked work in "
        "that copy"
    )
    print(
        f"  regeneration of each member's excluded state is still running on "
        f"{peer_name!r} — check its progress there with "
        f"`camp status --name {slug} --group {group_name}`"
    )
    print(
        "  nothing scanned what crossed for credential-shaped content — "
        "untracked files routinely carry them and the peer now holds a "
        "cleartext copy; review it yourself"
    )
    if not move_result.conversations:
        print("  no conversations are rooted in this workspace")
    else:
        for conversation in move_result.conversations:
            subpath = printable_path(conversation.subpath)
            print(f"    {conversation.session_id} @ {subpath}")
            print(f"      resume with: camp launch --resume {conversation.session_id}")


def _cmd_transfer_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
    dry_run: bool,
) -> None:
    """camp transfer <slug> --to <peer> [--dry-run] [--overwrite] [--json]

    See the module docstring for the full exit-code table. Every check is
    composed by `camp.transfer.preflight.compose_preflight` from reads this
    function performs; this function owns rendering and the exit code alone.
    On a clean verdict without `--dry-run`, drives
    `camp.transfer.move.move_workspace` and reports what it did.
    """
    import os

    from ..group.manifest import ManifestError, manifest_path_for, owner_of, read_central_manifest
    from ..host.config import HostConfigError, load_hosts, self_host_name
    from ..spine import _consume_flag_value, _die
    from ..transfer.preflight import MemberDeclaration, Verdict, compose_preflight
    from ..transfer.probe import InvalidSlugForTransport, probe_peer
    from .dispatch import _slug_from_args_or_cwd
    from .session import _parsable_groups

    as_json = "--json" in args
    overwrite = "--overwrite" in args
    filtered = [a for a in args if a not in ("--json", "--dry-run", "--overwrite")]
    _consume_flag_value(filtered, "--group")  # already resolved upstream; drop it

    peer_name = _consume_flag_value(filtered, "--to")

    if not peer_name:
        _die("camp transfer: --to <peer> is required", code=EXIT_ERROR)

    slug = _slug_from_args_or_cwd(
        filtered, group, verb="transfer", consume_positional=True, env=env
    )

    group_name = group["group"]["name"]
    resolved_env = dict(env) if env is not None else dict(os.environ)

    try:
        self_name = self_host_name(resolved_env)
    except HostConfigError as e:
        _die(f"camp transfer: {e}", code=EXIT_ERROR)

    self_account = (group.get("launch") or {}).get("account")

    mpath = manifest_path_for(group_name, slug, env=resolved_env)
    workspace_manifest_exists = mpath.is_file()
    owner: str | None = None
    if workspace_manifest_exists:
        try:
            owner = owner_of(read_central_manifest(mpath))
        except ManifestError:
            owner = None

    try:
        hosts = load_hosts()
    except HostConfigError as e:
        _die(f"camp transfer: {e}", code=EXIT_ERROR)

    peer_declared = peer_name in hosts
    probe_result = None
    if peer_declared:
        try:
            probe_result = probe_peer(
                hosts[peer_name], group=group_name, slug=slug, self_name=self_name
            )
        except InvalidSlugForTransport as e:
            _die(f"camp transfer: {e}", code=EXIT_ERROR)

    members = tuple(
        MemberDeclaration(name=m["name"], excluded=m.get("excluded"))
        for m in group["members"]
    )

    session_groups = _parsable_groups()
    conversations = _gather_conversations(
        group_name=group_name, slug=slug, session_groups=session_groups, resolved_env=resolved_env
    )

    result = compose_preflight(
        self_name=self_name,
        self_account=self_account,
        workspace_manifest_exists=workspace_manifest_exists,
        owner=owner,
        peer_name=peer_name,
        peer_declared=peer_declared,
        probe_result=probe_result,
        members=members,
        slug=slug,
        conversations=conversations,
    )
    result = _augment_missing_self_name_check(result, env=resolved_env)

    if dry_run or result.verdict is not Verdict.WOULD_TRANSFER:
        if as_json:
            print(json.dumps(_json_payload(result, slug=slug, peer_name=peer_name)))
        else:
            _render_human(result, slug=slug, peer_name=peer_name)
        sys.exit(_exit_code_for(result))

    from ..transfer.move import OverwriteNeeded, PhaseFailed, move_workspace

    def _on_phase(phase: str) -> None:
        print(f"camp transfer: phase — {phase}")

    try:
        move_result = move_workspace(
            host=hosts[peer_name],
            group=group,
            group_name=group_name,
            slug=slug,
            sender_name=self_name,
            overwrite=overwrite,
            on_phase=_on_phase,
            env=resolved_env,
            conversations=conversations,
            locate_transcript=(
                _locate_transcript(session_groups, resolved_env) if conversations else None
            ),
        )
    except OverwriteNeeded as e:
        print(
            f"camp transfer: refused — {e.detail} — moves nothing; pass "
            "--overwrite to proceed",
            file=sys.stderr,
        )
        sys.exit(EXIT_OVERWRITE_REQUIRED)
    except PhaseFailed as e:
        print(
            f"camp transfer: phase {e.phase!r} failed — {e.detail}. This is a "
            "failure, not a refusal: everything up to this phase already "
            "crossed, and re-running the transfer is safe — pass --overwrite, "
            "since the first begin already seeded the manifest.",
            file=sys.stderr,
        )
        sys.exit(EXIT_PHASE_FAILED)

    _render_move_completion(
        move_result, slug=slug, peer_name=peer_name, group_name=group_name, self_name=self_name
    )
    sys.exit(EXIT_WOULD_TRANSFER)
