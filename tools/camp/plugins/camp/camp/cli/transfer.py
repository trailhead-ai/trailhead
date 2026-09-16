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
member history and worktree, then claim, then finish. Any check that is not PASSED
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
  9  EXIT_PHASE_FAILED        a move phase (begin/history/worktree/conversations/
                               claim) failed BEFORE `claim` answered — ownership
                               never moved, nothing after the failing phase ran,
                               and re-running the transfer (with --overwrite) is
                               safe
 10  EXIT_PHASE_FAILED_POST_COMMIT  `claim` has answered — ownership has already
                               moved to the peer — and either `finish` then
                               failed, or `claim`'s own outcome could not be
                               read directly but a re-probe of the peer
                               confirmed it landed anyway, in which case
                               `finish` never even ran (see
                               `camp.transfer.move.PhaseFailed`). A re-run is
                               NOT safe here: the peer already owns the
                               workspace and would refuse a re-run's opening
                               `begin`. The conversations that already
                               crossed are still released from this host;
                               this host's own manifest is deliberately left
                               stale, and the printed remedy names the peer
                               and directs the operator to continue the work
                               there
 11  EXIT_RELEASE_INCOMPLETE  `claim` has answered — ownership has already moved
                               to the peer — but completing the local handover
                               on this host did not go entirely cleanly: either
                               at least one crossed conversation's release came
                               back FAILED (this host MAY still hold a
                               resumable copy of it — a FAILED release whose
                               transcript already relocated, with only its
                               durable marker unrecorded, does not, and the
                               printed summary says which), or an unexpected
                               error interrupted archiving and/or flipping
                               this host's own record. Distinct from
                               `EXIT_WOULD_TRANSFER` precisely so a caller
                               reading the exit code alone can tell a fully
                               clean transfer from one that needs checking by
                               hand.
 12  EXIT_PHASE_INDETERMINATE `claim` failed in a way `move_workspace` could
                               not resolve even after re-probing the peer
                               (`camp.transfer.move.PhaseFailed.indeterminate`)
                               — whether ownership moved is genuinely
                               unknown. Neither the pre-commit remedy
                               (`EXIT_PHASE_FAILED`'s "re-running is safe")
                               nor the post-commit one is asserted; the
                               operator must check the peer by hand
                               (`camp transfer-probe`) before doing anything.

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
in the process exit code. `EXIT_OVERWRITE_REQUIRED`, `EXIT_PHASE_FAILED`, and
`EXIT_PHASE_FAILED_POST_COMMIT` are never produced by the preflight itself —
they come from `camp.transfer.move.move_workspace`, reached only once every
check has PASSED. The last two are told apart by whether the raised
`PhaseFailed` carries a `claimed_owner` — see that class's own docstring.
"""

from __future__ import annotations

import json
import sys

from .parser import CampParser, group_verb_parser


def _cmd_transfer_probe_cli(args: list[str]) -> None:
    from ..group.config import GroupConfigError, load_all_groups
    from ..host.config import HostConfigError, self_host_name
    from ..transfer.probe import build_probe_answer
    from .common import _groups_dir

    parser = CampParser(verb="transfer-probe")
    parser.add_argument("--group")
    parser.add_argument("--slug")
    parsed = parser.parse_args(args)

    # Both are checked here rather than declared ``required=True`` so the
    # refusal names the flag in camp's words, and so an invocation missing both
    # reports `--group` first — the order a caller fixes them in.
    group_name = parsed.group
    if not group_name:
        parser.die("--group is required")

    slug = parsed.slug
    if not slug:
        parser.die("--slug is required")

    try:
        self_name = self_host_name()
    except HostConfigError as e:
        parser.die(str(e))

    try:
        groups = load_all_groups(_groups_dir())
    except GroupConfigError as e:
        parser.die(str(e))

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
_PHASES = ("begin", "claim", "conversations", "finish", "history", "worktree")


def _build_receive_parser(phase: str) -> CampParser:
    """The parser for one `camp transfer-receive` phase.

    Built per phase rather than as one parser over the union of every phase's
    flags, so that a flag belonging to another phase (``--member`` on
    ``begin``) is refused rather than silently accepted and dropped. ``--group``
    and ``--slug`` are common to every phase; the rest are the phase's own.

    Nothing is declared ``required``: each phase checks its own flags after the
    parse so the refusal can name the phase it is required FOR, which argparse's
    generic wording cannot.
    """
    parser = CampParser(verb="transfer-receive")
    parser.add_argument("--group")
    parser.add_argument("--slug")
    if phase in ("begin", "claim"):
        parser.add_argument("--owner")
    if phase == "begin":
        parser.add_argument("--overwrite", action="store_true")
    if phase == "conversations":
        parser.add_argument("--session-id")
        parser.add_argument("--subpath")
    if phase in ("history", "worktree"):
        parser.add_argument("--member")
    return parser


def _cmd_transfer_receive_cli(args: list[str]) -> None:
    """camp transfer-receive begin|finish|history|worktree --group <g> --slug <s> [...]

    Dispatched here exactly like `camp transfer-probe` — every local read and
    write is this function's (and `camp.transfer.receive`'s) to make; no path
    is ever built from a caller-supplied field. See `camp.transfer.receive`'s
    module docstring for each phase's contract.
    """
    from ..group.config import GroupConfigError, load_all_groups
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
    parser = _build_receive_parser(phase)
    parsed = parser.parse_args(args[1:])

    group_name = parsed.group
    if not group_name:
        parser.die("--group is required")

    slug = parsed.slug
    if not slug:
        parser.die("--slug is required")

    try:
        groups = load_all_groups(_groups_dir())
    except GroupConfigError as e:
        parser.die(str(e))

    # Each branch below gathers only the arguments its own phase adds; the
    # call/refusal/print tail is shared, so every phase answers on stdout and
    # refuses on stderr in exactly one way. `phase` is looked up on
    # `receive_mod` by name, which is safe precisely because it was already
    # checked against the closed `_PHASES` set above — a caller cannot reach
    # any other attribute of that module through it.
    phase_kwargs: dict = {}
    if phase == "begin":
        if not parsed.owner:
            parser.die("--owner is required for begin")
        phase_kwargs = {"sender": parsed.owner, "overwrite": parsed.overwrite}
    elif phase == "claim":
        if not parsed.owner:
            parser.die("--owner is required for claim")
        phase_kwargs = {"sender": parsed.owner}
    elif phase == "conversations":
        if not parsed.session_id:
            parser.die("--session-id is required for conversations")
        if not parsed.subpath:
            parser.die("--subpath is required for conversations")
        phase_kwargs = {
            "session_id": parsed.session_id,
            "subpath": parsed.subpath,
            "archive_stream": sys.stdin.buffer,
        }
    elif phase in ("history", "worktree"):
        if not parsed.member:
            parser.die(f"--member is required for {phase}")
        phase_kwargs = {"member": parsed.member}
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
EXIT_PHASE_FAILED_POST_COMMIT = 10
EXIT_RELEASE_INCOMPLETE = 11
EXIT_PHASE_INDETERMINATE = 12

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
    move_result, *, slug: str, peer_name: str, group_name: str, release_results
) -> None:
    """The report printed once `move_workspace` returns successfully.

    Distinguishes "arrived" from "ready to work in" — regeneration is
    spawned, not awaited (see `camp.transfer.receive`'s `finish`), and this
    function deliberately does not poll or block on it; it states that the
    peer may still be provisioning and names both the check and the retry —
    states plainly that ownership moved to the peer, naming the exact owner
    name the peer's `claim` phase answered
    (`move_result.claimed_owner`) rather than this function's own `peer_name`
    alias for it, and names the interim risk that nothing scans what crossed
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

    *release_results* is `camp.transfer.release.release_conversations`'s own
    return value — one `ConversationRelease` per crossed conversation,
    reported here individually rather than as a single aggregate line, so an
    operator can tell exactly which conversations are now peer-only and
    which are still sitting resumable on this host because their release
    failed. A FAILED release's own `archive_path` decides which of those two
    it is: `None` means the relocation itself never happened (still
    resumable here), while a populated path means the transcript already
    moved and only the durable marker append failed — that one is reported
    as moved, never as resumable, since it no longer is.
    """
    print(f"camp transfer: {slug!r} arrived on {peer_name!r}")
    print(f"  ownership moved to {move_result.claimed_owner!r}")
    print(
        f"  the peer may still be provisioning — regeneration of each "
        f"member's excluded state was spawned there, not awaited; check its "
        f"progress with `camp status --name {slug} --group {group_name}` "
        "and retry any failed or pending member with `camp setup`"
    )
    print(
        "  nothing scanned what crossed for credential-shaped content — "
        "untracked files routinely carry them and the peer now holds a "
        "cleartext copy; review it yourself"
    )
    if not move_result.conversations:
        print("  no conversations are rooted in this workspace")
    else:
        _render_conversation_releases(move_result.conversations, release_results)


def _render_conversation_releases(conversations, release_results, *, file=None) -> None:
    """Print each crossed conversation's id, resume command, and release
    outcome — the per-conversation report `_render_move_completion`'s
    success path and the post-commit `finish`-failure branch both need,
    factored out so the two can never drift apart in what they report. See
    `_render_move_completion`'s own docstring for how a FAILED outcome's
    `archive_path` decides which of the two FAILED messages below applies.
    """
    from ..launch.recovery import printable_path
    from ..transfer.release import ReleaseOutcome

    release_by_id = {r.session_id: r for r in release_results}
    for conversation in conversations:
        subpath = printable_path(conversation.subpath)
        print(f"    {conversation.session_id} @ {subpath}", file=file)
        print(f"      resume with: camp launch --resume {conversation.session_id}", file=file)
        released = release_by_id.get(conversation.session_id)
        if released is None:
            continue
        if released.outcome is ReleaseOutcome.FAILED and released.archive_path is None:
            print(
                f"      this host still holds a resumable copy — release "
                f"failed: {released.detail}",
                file=file,
            )
        elif released.outcome is ReleaseOutcome.FAILED:
            print(
                f"      moved to {released.archive_path} but the durable "
                f"marker could not be recorded — {released.detail}",
                file=file,
            )
        else:
            print(f"      released from this host — archived at {released.archive_path}", file=file)


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
    from ..spine import _die
    from ..transfer.preflight import MemberDeclaration, Verdict, compose_preflight
    from ..transfer.probe import InvalidSlugForTransport, probe_peer
    from .dispatch import _slug_from_name_or_cwd
    from .session import _parsable_groups

    parser = group_verb_parser("transfer", dry_run=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--to", metavar="PEER")
    parser.add_argument("--name", metavar="SLUG")
    parser.add_argument("slug", nargs="?")
    parsed = parser.parse_args(args)

    as_json = parsed.json
    overwrite = parsed.overwrite
    peer_name = parsed.to

    if not peer_name:
        _die("camp transfer: --to <peer> is required", code=EXIT_ERROR)

    slug = _slug_from_name_or_cwd(
        group, verb="transfer", name=parsed.name, positional=parsed.slug, env=env
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

    def _release_crossed(crossed: tuple) -> tuple:
        """Archive this host's own copies of the conversations that crossed.

        Both places a completed `claim` leaves conversations sitting
        resumable on this host reach the release through here — the
        successful move, and the post-commit `finish` failure — so the two
        can never drift apart in what they archive or how they locate it.
        `locate_transcript` is built only when there is something to locate:
        `release_conversations` never consults it for an empty pool, and
        building it walks every harness store this host can address.
        """
        from ..group.manifest import workspace_dir
        from ..transfer.release import release_conversations

        return release_conversations(
            group=group_name,
            slug=slug,
            workspace_root=workspace_dir(group_name, slug, env=resolved_env),
            conversations=crossed,
            locate_transcript=(
                _locate_transcript(session_groups, resolved_env)
                if crossed
                else (lambda session_id, root: None)
            ),
            env=resolved_env,
        )

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
        if e.indeterminate:
            # Neither the pre-commit remedy below (re-running is safe) nor
            # the post-commit one above (continue on the peer) is asserted
            # here — `move_workspace` itself could not establish, even after
            # re-probing the peer, whether `claim` actually landed. Nothing
            # is released and this host's own record is left untouched;
            # guessing either remedy would be worse than refusing to guess.
            print(
                f"camp transfer: phase {e.phase!r} failed — {e.detail}. "
                "Whether ownership moved to the peer could not be "
                "established, even after re-probing it: this is neither "
                "the pre-commit 're-running is safe' case nor the "
                "post-commit 'continue on the peer' case. Do not assume "
                "either — check the peer directly with `camp "
                f"transfer-probe --group {group_name} --slug {slug}` before "
                "doing anything else.",
                file=sys.stderr,
            )
            sys.exit(EXIT_PHASE_INDETERMINATE)
        if e.claimed_owner is not None:
            # `claim` already answered before `finish` failed — ownership
            # has already moved to the peer (see `PhaseFailed`'s own
            # docstring). The conversations that already crossed are still
            # released, exactly as a successful move would release them, but
            # `flip_sender_ownership` never runs: this host's own record
            # stays stale on purpose, since a wrong flip here would claim a
            # handover this host cannot confirm actually finished on the
            # peer.
            post_commit_release_results = _release_crossed(e.conversations)
            if e.phase == "finish":
                bring_up_note = (
                    "The phase that failed is exactly the one that performs "
                    "bring-up, so the peer's workspace has had no setup at "
                    "all yet"
                )
            else:
                # `claim` resolved as landed only via a re-probe (its own
                # outcome could not be read directly) — `finish`, the phase
                # that performs bring-up, never even ran on the peer.
                bring_up_note = (
                    f"The phase that failed ({e.phase!r}) never reaches "
                    "bring-up itself, and `finish` — the phase that does — "
                    "never ran either, so the peer's workspace has had no "
                    "setup at all yet"
                )
            print(
                f"camp transfer: phase {e.phase!r} failed after ownership had "
                f"already moved to {e.claimed_owner!r} — {e.detail}. This is "
                f"not a re-runnable failure: {e.claimed_owner!r} now owns "
                f"the workspace and holds its content; this host's own "
                f"record is stale. Continue the work on {e.claimed_owner!r} "
                "rather than retrying here — the two hosts' records now "
                f"disagree, and running `camp transfer-probe --group "
                f"{group_name} --slug {slug}` ON THE PEER ({e.claimed_owner!r}) "
                "against this host is how you confirm that; run here it "
                "would only report that this host's own record is stale, "
                f"which you already know. {bring_up_note} — check its "
                f"progress there with `camp status --name {slug} --group "
                f"{group_name}` and retry any failed or pending member with "
                "`camp setup`.",
                file=sys.stderr,
            )
            if e.conversations:
                _render_conversation_releases(
                    e.conversations, post_commit_release_results, file=sys.stderr
                )
            sys.exit(EXIT_PHASE_FAILED_POST_COMMIT)
        print(
            f"camp transfer: phase {e.phase!r} failed — {e.detail}. This is a "
            "failure, not a refusal: everything up to this phase already "
            "crossed, and re-running the transfer is safe — pass --overwrite, "
            "since the first begin already seeded the manifest.",
            file=sys.stderr,
        )
        sys.exit(EXIT_PHASE_FAILED)

    from ..transfer.release import flip_sender_ownership

    try:
        release_results = _release_crossed(move_result.conversations)

        # This host's own last write of the whole verb — after release_conversations
        # has archived and marked every crossed conversation, never before. See
        # camp.transfer.release's module docstring for why the ordering is load-bearing.
        flip_sender_ownership(
            group=group_name, slug=slug, owner=move_result.claimed_owner, env=resolved_env
        )
    except Exception as e:
        # `claim` has already answered by this point — ownership has already
        # moved to `move_result.claimed_owner` regardless of what happens
        # next. An unexpected error completing the local archive-and-flip
        # tail must never escape as a raw traceback and exit 1 — that code
        # is reserved for a local/config error before anything has crossed —
        # so this is routed to the same honest, documented outcome an
        # incomplete release already gets.
        print(
            f"camp transfer: ownership already moved to "
            f"{move_result.claimed_owner!r} — but completing the local "
            f"handover on this host (archiving crossed conversations and/or "
            f"flipping this host's own record) failed unexpectedly: {e}. "
            "This host's own manifest and its archived conversations may be "
            "in an inconsistent state; check them by hand rather than "
            "retrying the transfer.",
            file=sys.stderr,
        )
        sys.exit(EXIT_RELEASE_INCOMPLETE)

    _render_move_completion(
        move_result,
        slug=slug,
        peer_name=peer_name,
        group_name=group_name,
        release_results=release_results,
    )

    from ..transfer.release import ReleaseOutcome

    failed_releases = [r for r in release_results if r.outcome is ReleaseOutcome.FAILED]
    if failed_releases:
        if any(r.archive_path is None for r in failed_releases):
            # At least one FAILED release never relocated its transcript at
            # all — this host genuinely may still hold a resumable copy.
            print(
                "camp transfer: ownership moved cleanly, but at least one "
                "conversation's release failed — see the FAILED line(s) "
                "above for which one(s) still need cleaning up on this "
                "host.",
                file=sys.stderr,
            )
        else:
            # Every FAILED release here is the marker-only shape: the
            # transcript already left this host and only the durable
            # release marker could not be recorded — nothing is sitting
            # here to clean up.
            print(
                "camp transfer: ownership moved cleanly, and every crossed "
                "conversation's transcript has already left this host, but "
                "at least one durable release marker could not be recorded "
                "— see the FAILED line(s) above.",
                file=sys.stderr,
            )
        sys.exit(EXIT_RELEASE_INCOMPLETE)
    sys.exit(EXIT_WOULD_TRANSFER)
