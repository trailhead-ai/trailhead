"""The session command group: ``launch`` (start one) and ``sessions`` (list them).

Both are group-resolved like every other workspace verb. The engine lives in
``camp.launch.session``; this module owns only the CLI's three jobs — flag
parsing, the stdout/stderr split, and turning a :class:`LaunchError` into camp's
one-line refusal.

Two deliberately different postures:

``launch`` is an ACTION, so every failure is a refusal — one ``camp launch: …``
stderr line, empty stdout, non-zero exit. That includes a launch that spawned but
never registered: an unconfirmable session is not a success with a caveat.

``sessions`` is a QUESTION, so every failure DEGRADES — a stderr notice, an empty
list on stdout, exit 0. A caller asking what is running can act on "nothing" and
on "I could not tell" the same way (there is nothing to attach to either way), and
exiting non-zero for the second would make a read-only query a scripting hazard.
The two are still distinguishable: the degraded answer carries a notice naming
what could not be determined, and an honestly-empty one is silent.

``camp new --launch`` reuses this module rather than re-deriving the flow, so a
launch means the same thing and refuses the same way at both entry points.

``--prompt`` hands the launched session an initial prompt through the harness
seam. Security note: camp cannot distinguish an injected agent invoking this
flag from a legitimate operator doing the same — both are the same process
with the same privileges, so no check camp performs on itself can authorize
the call. What camp guarantees instead is that the call can never happen
quietly: a prompt-carrying launch writes an audit line to stderr naming the
prompt verbatim and the workspace it is launching into, so it is visible in
the terminal and in the session transcript. The gate that actually enforces is
the harness's own permission prompt — which is why ``camp launch`` must never
be added to any agent's Bash auto-allow list (see ``camp help``): a direct
invocation then always surfaces this text for review before anything runs.

The RESUME flavor adds one more CLI job: turning an operator's session reference
into exactly one addressable session, or refusing. The resolution itself is pure
and lives in ``camp.launch.recovery``; everything the operator SEES about it —
the candidate rows, the exit codes, the wording of each refusal — is here,
because a question answered on a terminal cannot also be answered identically
from a test or a listing.

``sessions --recoverable`` is the discovery half of that flavor, and the same
division applies: the subtraction that produces the dead sessions is pure and
lives beside the resolver, while the cap, the row rendering, the empty-state
line and the harness-unsupported refusal are here. Its one hard rule is that
BOTH halves of the subtraction are scoped by the same argument — the transcript
enumeration and the live enumeration alike — because scoping only one of them
reports running sessions as recoverable.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import NoReturn

#: Bounds for `camp new --launch`'s provisioning wait. Provisioning clones and
#: sets up every member repo, so the ceiling is generous; the floor is that this
#: wait is BOUNDED at all — a killed provisioner leaves the manifest `pending`
#: forever with no liveness signal, so an unbounded wait would hang the caller.
_PROVISION_POLL_INTERVAL_SECONDS = 1.0
_PROVISION_POLL_TIMEOUT_SECONDS = 900.0

#: The resume flavor's flag, named once. Held as a constant rather than spelled
#: at each of its four reading sites so the router that decides a groupless
#: invocation and the handler that parses it can never disagree about it.
RESUME_FLAG = "--resume"

#: Exit code for an ambiguous session reference. Deliberately NOT 1: an
#: ambiguous ref is information — camp found the sessions and is showing them —
#: and a consumer that reads every non-zero exit as breakage would report a
#: solvable, one-more-character problem as a failure.
_AMBIGUOUS_EXIT_CODE = 2

#: How many recoverable rows `camp sessions --recoverable` prints before it
#: starts saying "and N more". A real transcript store is mostly sessions that
#: have nothing to do with camp, so an uncapped global listing is unreadable on
#: the phone this listing exists to be read from. The total is always printed
#: alongside, and `--limit <n>` / `--all` widen it.
_RECOVERABLE_DEFAULT_LIMIT = 20


def _refusal(exc: Exception) -> str:
    """Re-prefix an engine refusal as a `camp launch:` line.

    The engine raises pre-formatted `camp: …` messages because it is shared; the
    CLI is the layer that knows which verb the user typed, so the verb name is
    attached here rather than being baked into the engine's wording.
    """
    message = str(exc)
    prefix = "camp: "
    if message.startswith(prefix):
        message = message[len(prefix) :]
    return f"camp launch: {message}"


def _consume_flag(args: list[str], flag: str) -> bool:
    """Remove every *flag* from *args* in place, reporting whether one was there.

    Removal matters as much as detection: the remaining args are what slug
    resolution reads positionally, and a leftover flag sitting at args[0] would
    be resolved as a slug and die naming the wrong problem.
    """
    present = flag in args
    while flag in args:
        args.remove(flag)
    return present


def launch_and_confirm(
    group: dict,
    slug: str | None = None,
    *,
    env: dict[str, str] | None = None,
    root: Path | None = None,
    name_component: str | None = None,
    trust_scope: Path | None = None,
    resume_session_id: str | None = None,
    camp_managed_root: bool = False,
    initial_prompt: str | None = None,
):
    """Spawn a session — by workspace *slug* or at a named *root* — and confirm it.

    The addressing arguments are the engine's own, forwarded whole: exactly one of
    *slug* or the (*root*, *name_component*, *trust_scope*) triple, which the
    engine enforces. *camp_managed_root* and *resume_session_id* ride on that
    triple and on either flavor respectively. Everything after the spawn is
    identical for all three flavors, so they report the same three stderr lines
    and return the same :class:`LaunchedSession`.

    *initial_prompt*, given, is handed to the engine whole and additionally
    named on stderr, verbatim, alongside the workspace it is launching into —
    the audit line that makes a prompt-carrying launch impossible to happen
    quietly (see the module docstring's security note). An unprompted launch
    prints no such line.

    Raises :class:`LaunchError` on refusal — including a spawn that never
    confirmed, which the engine has already killed, and including the engine's
    own refusal when a harness accepted `initial_prompt` and dropped it anyway.
    """
    from ..launch.profile import harness_for
    from ..launch.session import confirm_session, launch_session

    launched = launch_session(
        group,
        slug,
        env=env,
        root=root,
        name_component=name_component,
        trust_scope=trust_scope,
        resume_session_id=resume_session_id,
        camp_managed_root=camp_managed_root,
        initial_prompt=initial_prompt,
    )
    if initial_prompt is not None:
        # camp cannot tell an injected agent from a legitimate one — both are
        # the same process with the same privileges — so this cannot AUTHORIZE
        # anything. What it guarantees is that the launch leaves a record
        # naming exactly what was handed to the peer: visible here, in the
        # session transcript, and in the pane's own start command. The gate
        # that actually enforces is the harness's own permission prompt, which
        # is why `camp launch` must never be added to an agent's auto-allow
        # list (see `camp help`) — a direct call surfaces this text for review
        # before anything runs.
        print(
            f"camp launch: handing session {launched.session_id} in "
            f"{launched.launch_dir} the prompt: {initial_prompt}",
            file=sys.stderr,
        )
    print(
        f"camp launch: launched session {launched.session_id} in {launched.launch_dir}\n"
        f"  attach: tmux attach -t {launched.tmux_name}",
        file=sys.stderr,
    )
    # The pane's own environment, not this process's: the confirmation reports
    # which config file the session reads, and the ambient one the CLI was
    # invoked with is exactly what the launch scrubbed.
    confirm_session(harness_for(group), launched, env=launched.pane_env)
    print(f"camp launch: confirmed session {launched.session_id}", file=sys.stderr)
    return launched


def launch_for_new(group: dict, slug: str, *, env: dict[str, str] | None = None):
    """`camp new --launch`'s launch step: the LaunchedSession, or None on refusal.

    Returning the whole :class:`LaunchedSession` — not just its session id — is
    what lets `camp new --launch --json` report `tmux_name` alongside
    `session_id` without reconstructing `camp-<slug>-<uuid8>` at the print site;
    the caller carries the exact name the launch engine chose.

    Returning None rather than exiting is the whole point: `camp new` already
    created the workspace, and that success is what its exit code and its stdout
    path report. A failed launch is reported on stderr in exactly the shape
    `camp launch` uses, and leaves the caller with a usable workspace.
    """
    from ..launch.session import LaunchError

    try:
        return launch_and_confirm(group, slug, env=env)
    except LaunchError as exc:
        print(_refusal(exc), file=sys.stderr)
        return None


def wait_for_provisioning(group: dict, slug: str, *, env: dict[str, str] | None = None) -> bool:
    """Block until *slug* is provisioned; False when the launch must be refused.

    A workspace whose members are still being cloned is not a workspace a harness
    can usefully be launched into, so `camp new --launch` waits by default. A
    failed or timed-out provisioning refuses the launch rather than racing it —
    the timeout report already names `camp status <slug>` as where the real state
    is, so the refusal repeats it verbatim. A missing or corrupt manifest
    (:class:`ManifestError`) is the same refusal shape, not a traceback — the
    provisioner never got far enough to leave a readable state.
    """
    from ..group.manifest import ManifestError
    from ..provision.lifecycle import wait_for_provisioning_ready

    print(
        f"camp new: waiting for provisioning of {slug!r} to finish before launching",
        file=sys.stderr,
    )
    try:
        outcome, report = wait_for_provisioning_ready(
            group,
            slug,
            env=env,
            interval=_PROVISION_POLL_INTERVAL_SECONDS,
            timeout=_PROVISION_POLL_TIMEOUT_SECONDS,
            sleep=time.sleep,
        )
    except ManifestError as exc:
        print(f"camp launch: refusing to launch — {exc}", file=sys.stderr)
        return False
    if outcome == "ready":
        return True
    detail = report.get("message") or f"provisioning of workspace {slug!r} failed"
    print(f"camp launch: refusing to launch — {detail}", file=sys.stderr)
    return False


def trigger_activate_phase_work(
    group: dict, slug: str, *, env: dict[str, str] | None = None, wait: bool = True
) -> None:
    """`camp new --activate`'s trigger step: hand every member's activate-phase
    work to the detached provisioner — the non-blocking part is that this never
    waits for that work itself (the possibly-expensive `npm ci` or graph
    build), matching "triggers ... and returns without waiting for it". This is
    the non-interactive path to the same work `camp activate <member>` triggers
    interactively — the way a consumer that never calls `camp activate`
    (ranger's execute drain, any other automation that puts an agent straight
    into a worktree) gets its work-enabling tasks run.

    An activate-phase task runs inside the member's worktree, which does not
    exist until the member reaches boot-readiness — so by default (wait=True)
    this first waits, bounded, for boot-readiness (the identical poll
    `wait_for_provisioning` uses) before spawning anything; a workspace that
    never reaches boot-readiness triggers nothing, same as `--launch` refusing
    rather than racing it. Blocking on boot-readiness is acceptable because
    cheapness is a requirement of that phase — only the activate-phase work
    itself never blocks. wait=False (`--no-wait`) skips even that: it spawns
    immediately, racing the still-running provisioner exactly as
    `--launch --no-wait` races the harness launch, the same accepted risk on
    the same flag.

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


def _candidate_payload(candidate) -> dict:
    """One resolver candidate as JSON-ready data.

    This key set is the candidate ROW SHAPE, shared by every surface that lists
    candidates, so a consumer that learned it from one listing reads the other
    unchanged. ``root`` and ``age_seconds`` are ``null`` rather than absent when
    the harness could not tell camp them: a missing key and a known-absent value
    are different facts, and only the second is answerable.

    ``age_seconds`` is a whole number of seconds. The sub-second component is an
    artifact of when the listing happened to run, not a fact about the session,
    and emitting it would make two rows written in the same instant compare
    unequal for every consumer that reads this field.
    """
    return {
        "session_id": candidate.session_id,
        "tmux_name": candidate.derived_name,
        "root": str(candidate.root) if candidate.root is not None else None,
        "age_seconds": (
            int(candidate.age_seconds) if candidate.age_seconds is not None else None
        ),
        "root_missing": candidate.root_missing,
        "unreadable": candidate.unreadable,
    }


def _format_age(seconds: float | None) -> str:
    """A candidate's age as one compact, coarse duration — ``2d``, ``4h``, ``9m``.

    Coarse on purpose. These rows are read on a phone to answer "which of these
    is the one I was in", and a single unit at the largest scale that still has
    a whole number answers that in fewer characters than a precise duration
    would. ``None`` means there is no transcript to be aged — a session known
    only from the live enumeration — and reads as ``live``.
    """
    if seconds is None:
        return "live"
    total = max(int(seconds), 0)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h"
    return f"{total // 86400}d"


def _candidate_line(candidate) -> str:
    """One candidate as an operator-facing row: name, id, where, how old.

    The three things that distinguish two candidates — a directory, its state,
    and an age — are all here, because the operator picks between them by
    reading this line and nothing else. One row shape serves both listings that
    print candidates, so a consumer that learned it from the recoverable listing
    reads an ambiguity listing unchanged.

    A candidate with no extractable root says so rather than naming a location:
    "somewhere camp cannot name" and "a directory that was torn down" are
    different facts, and only the second has a path to print.
    """
    from ..launch.recovery import printable_path

    if candidate.root is None:
        where = "directory unknown"
    elif candidate.root_missing:
        where = f"{printable_path(candidate.root)} (gone)"
    else:
        where = printable_path(candidate.root)
    return (
        f"{candidate.derived_name}  {candidate.session_id}  {where}  "
        f"{_format_age(candidate.age_seconds)}"
    )


def _print_candidates(candidates, *, as_json: bool) -> None:
    """Print candidate rows on STDOUT — the one print site both listings use.

    On stdout even when the exit code is non-zero: the rows ARE the answer to
    what was asked, and a caller capturing stdout must get them. The one-line
    explanation of why camp stopped — or the notice naming what was capped —
    goes to stderr alongside, keeping the split every other camp verb uses.

    Shared by the ambiguity listing and the recoverable listing so the two emit
    the identical bytes for the identical candidate: a consumer that learned the
    shape from one reads the other unchanged, which two print sites cannot
    guarantee.
    """
    if as_json:
        print(json.dumps([_candidate_payload(candidate) for candidate in candidates]))
        return
    for candidate in candidates:
        print(_candidate_line(candidate))


def _retention_hint(harness, env: dict[str, str]) -> str:
    """Why an empty transcript pool is probably empty, in the harness's own terms.

    Reached only when the harness reports NO sessions whatsoever, where retention
    cleanup is the overwhelmingly likely explanation. Saying this for a pool that
    merely failed to match would send an operator hunting for a session that is
    sitting right there under a different reference.
    """
    try:
        days = harness.session_retention_days(env=env)
    except Exception:  # noqa: BLE001 — a hint is never worth a traceback
        days = None
    if days is None:
        return (
            "a transcript that has aged out of the harness's retention window is "
            "no longer addressable"
        )
    return (
        f"transcripts are removed after {days} days, so this one has most likely "
        "aged out of that retention window"
    )


def _harness_display_name(harness) -> str:
    """The name to put in a refusal about *harness*."""
    return harness.name or type(harness).__name__


def _addressable_harnesses(groups) -> list:
    """Every harness camp can ask about sessions — one entry per distinct harness.

    A reference addresses a SESSION, not a group. Naming a group, or standing in
    one, does not change which sessions exist, so the pool spans every configured
    group's harness rather than whichever one the invocation happened to resolve
    — the same reason a resume needs no ``--group`` in the first place.
    Deduplicated by name, because groups routinely share a harness and one store
    must never be read twice into the same pool.

    A group whose harness camp cannot name contributes nothing rather than
    failing the lookup: one unrecognized group must not make every other group's
    sessions unaddressable. With no groups configured at all, camp's default
    harness profile is the one thing left to ask.
    """
    from ..launch.profile import harness_for

    found: dict[str, object] = {}
    for config in groups or [{}]:
        harness = harness_for(config)
        if harness is None:
            continue
        found.setdefault(_harness_display_name(harness), harness)
    return list(found.values())


def _parsable_groups() -> list[dict]:
    """Every group config camp can PARSE — a malformed sibling contributes nothing.

    A session reference names a session, not a group, so one unreadable toml
    elsewhere in the config directory must not make every session unaddressable.
    That is precisely the situation a stop is reached for from a phone: something
    is already broken, and the verb that reclaims memory has to still answer.

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


def _session_pool(
    groups,
    *,
    verb: str,
    env: dict[str, str],
    live_required: bool = False,
) -> tuple[list, list, list]:
    """The addressable pool: (transcripts, live records, harnesses that answered).

    Every addressable harness's on-disk transcripts UNION its live sessions. A
    harness with no transcript concept — or one whose store camp cannot read at
    all, which the seam contract forbids but a third-party harness may still do
    — contributes no transcripts, and if NONE of them has one the reference is
    unanswerable and camp refuses naming them: an unanswerable seam is a
    refusal, never a permissive default, and never a traceback.

    The live probe is the opposite posture BY DEFAULT — it only ever ADDS
    candidates, so a probe that fails narrows what camp can offer without being
    able to make camp address the wrong thing.

    *live_required* flips that, and a DESTRUCTIVE caller must set it. A failed
    probe does not say "nothing is live", it says nothing at all, and a caller
    whose decision turns on liveness would read the silence as "not live": the
    stop path's already-down oracle would then report a running session, still
    holding its memory, as reclaimed. On that path an unanswerable probe is a
    refusal, the same posture the teardown guard takes for the same reason.

    *verb* is the name to put in either refusal, because both are read verbatim
    off a relayed stderr line and have to name the command the operator typed.
    """
    from ..launch.session import enumerate_records
    from ..spine import _die

    harnesses = _addressable_harnesses(groups)
    if not harnesses:
        _die(
            f"camp {verb}: camp cannot name a harness for any configured group, so "
            "it cannot look up the session this reference addresses"
        )

    transcripts: list = []
    live: list = []
    answered: list = []
    for harness in harnesses:
        try:
            records = enumerate_records(harness, None, env)
        except Exception as exc:  # noqa: BLE001 — posture below, never a traceback
            records = None
            detail = str(exc)
        else:
            detail = "the enumeration could not be answered"
        if records is None and live_required:
            _die(
                f"camp {verb}: camp could not ask harness "
                f"{_harness_display_name(harness)} which of its sessions are "
                f"live ({detail}), so it cannot tell whether this session is "
                "already down or still holding its memory — re-run once the "
                "harness answers"
            )
        live.extend(records or [])
        try:
            rows = harness.session_transcripts(env=env)
        except Exception:  # noqa: BLE001 — a harness camp cannot read contributes nothing
            rows = None
        if rows is not None:
            answered.append(harness)
            transcripts.extend(rows)

    if not answered:
        names = ", ".join(_harness_display_name(harness) for harness in harnesses)
        _die(
            f"camp {verb}: harness {names} keeps no session transcripts camp can "
            "read, so its sessions cannot be addressed by reference"
        )
    return transcripts, live, answered


def _die_unresolved(
    outcome,
    ref: str,
    *,
    verb: str,
    harness,
    env: dict[str, str],
    as_json: bool,
) -> NoReturn:
    """Refuse a *ref* that did not address exactly one session, in *verb*'s terms.

    Three outcomes end in a refusal, and the wording of each is the whole point:

    * MORE THAN ONE match prints the candidates and exits
      :data:`_AMBIGUOUS_EXIT_CODE`, never guessing.
    * NO match against a populated pool is a ref problem, and points at the
      listing that shows what the refs are.
    * NO match against an EMPTY pool is not a ref problem at all, and says so —
      naming the harness's retention window instead of implying the operator
      mistyped something.

    Every ref-addressed verb refuses through here, so an operator who mistypes
    the same reference at two of them is told the same thing and only the
    command name differs. *verb* is that name, and *harness* is the one whose
    retention window explains an empty pool.
    """
    from ..launch.recovery import Ambiguous, NoMatch
    from ..spine import _die

    if isinstance(outcome, Ambiguous):
        _print_candidates(outcome.candidates, as_json=as_json)
        _die(
            f"camp {verb}: {ref!r} matches {len(outcome.candidates)} sessions "
            "(listed above) — re-run with a longer prefix naming exactly one",
            code=_AMBIGUOUS_EXIT_CODE,
        )

    if isinstance(outcome, NoMatch) and outcome.pool_size:
        _die(
            f"camp {verb}: no candidate matched `{ref}`; run "
            "`camp sessions --recoverable` to see what camp can address"
        )
    _die(
        f"camp {verb}: harness {_harness_display_name(harness)} reports no "
        f"sessions at all — {_retention_hint(harness, env)}"
    )


def _resolve_session_reference(ref: str, *, env: dict[str, str], as_json: bool):
    """Resolve *ref* to one addressable session; return it with the group configs.

    Both halves come back because the caller needs both, and loading the configs
    a second time would let the name rule's two applications drift apart.

    The pool is :func:`_session_pool`'s, and a ref that does not address exactly
    one session refuses through :func:`_die_unresolved`, so resume and stop
    answer a mistyped reference identically.
    """
    from ..group.config import load_all_groups
    from ..launch.recovery import Resolved, resolve_session_ref
    from .common import _groups_dir

    groups = load_all_groups(_groups_dir())
    transcripts, live, answered = _session_pool(groups, verb="launch", env=env)

    outcome = resolve_session_ref(
        ref, transcripts=transcripts, live_records=live, groups=groups, env=env
    )

    if isinstance(outcome, Resolved):
        return outcome.candidate, groups

    _die_unresolved(
        outcome, ref, verb="launch", harness=answered[0], env=env, as_json=as_json
    )


def _workspace_owner(root: Path, groups, *, env: dict[str, str]) -> dict | None:
    """The group whose workspace holds *root*, or ``None`` for anywhere else.

    The one question the resume flavor asks beyond the name rule. A session rooted
    in a camp workspace belongs to the group camp provisioned that workspace for —
    not to whichever group the operator happens to be standing in — so the answer
    is read off the path, and a resume needs no ``--group`` to find it.

    Asked one group at a time through :func:`is_workspace_root`, the boolean half
    of the very rule that names the session, so the two can never disagree about
    what counts as a workspace. ``None`` means *root* is not a camp workspace at
    all, which is exactly the case the eligibility gate exists to fence.
    """
    from ..launch.recovery import is_workspace_root

    for config in groups:
        if is_workspace_root(root, [config], env=env):
            return config
    return None


def _report_launched(launched, *, as_json: bool, extra: dict | None = None) -> None:
    """The success report, identical for every launch flavor.

    *extra* is merged into the JSON object for a flavor that has something more
    to say about the launch it just made. It is deliberately absent from an
    ordinary launch rather than present-and-null: the key set a caller already
    parses stays exactly what it was, and a key that appears at all is a fact
    worth reading. Only the resume flavor uses it today.
    """
    if as_json:
        payload = {
            "workspace": str(launched.launch_dir),
            "session_id": launched.session_id,
            "tmux_name": launched.tmux_name,
            "account": launched.account,
            "account_binding": dict(launched.account_binding),
        }
        payload.update(extra or {})
        print(json.dumps(payload))
        return
    print(launched.session_id)


def _history_restored(config, session_id: str, root: Path, env: dict[str, str]) -> bool:
    """Will the resume about to run bring the conversation back with it?

    False is the outcome a resume must never report as an ordinary success:
    past the harness's retention window — or for a transcript that was never
    resumable — the session comes back EMPTY and exits 0 doing it, which is
    indistinguishable from a restored one and is exactly the silent degradation
    a stop-and-resume cycle exists to prevent.

    Read BEFORE the spawn, off the only thing that can answer it: the transcript
    the harness would replay. An absent transcript and a zero-length one are the
    same answer here — there is nothing to replay either way.

    True whenever camp cannot tell. A harness camp cannot name, or a store it
    cannot stat, knows nothing about retention either, and warning on every
    resume it cannot answer for would train the operator to ignore the one
    warning that matters.
    """
    from ..launch.profile import harness_for

    harness = harness_for(config)
    if harness is None:
        return True
    try:
        path = harness.session_transcript_path(session_id, root, env=env)
    except Exception:  # noqa: BLE001 — an advisory signal is never worth a traceback
        return True
    if path is None:
        return False
    try:
        return path.stat().st_size > 0
    except OSError:
        return True


def _launch_resume(
    ref: str,
    *,
    group: dict | None,
    explicit_group: str | None,
    env: dict[str, str] | None,
    as_json: bool,
) -> None:
    """Re-enter the session *ref* addresses, or refuse before anything spawns.

    Every gate below runs ahead of the engine, in the order an operator can act
    on, and each names a DIFFERENT situation. Two of them are easy to collapse
    and must not be: a session camp cannot locate at all has no directory to
    name, while a session whose directory was torn down has one — and the second
    tells the operator where their work went while the first cannot. Neither
    message may carry an internal absence marker; they are read verbatim off a
    relayed stderr line, often on a phone.

    A resume restores the CONVERSATION. Nothing here claims the work in flight
    when the session died comes back with it.
    """
    from ..launch.recovery import derive_name_component, printable_path
    from ..launch.session import LaunchError, already_running_error
    from ..spine import _die

    resolved_env = dict(env) if env is not None else dict(os.environ)
    candidate, groups = _resolve_session_reference(
        ref, env=resolved_env, as_json=as_json
    )

    if candidate.live:
        _die(_refusal(already_running_error(candidate.session_id, candidate.derived_name)))

    if candidate.unreadable:
        _die(
            f"camp launch: camp cannot tell which directory session "
            f"{candidate.session_id} was started in, so there is nowhere to bring "
            "it back up and it cannot be resumed"
        )

    root = Path(candidate.root).resolve()
    if candidate.root_missing:
        _die(
            f"camp launch: session {candidate.session_id} was started in "
            f"{printable_path(root)}, "
            "which no longer exists — camp will not recreate a torn-down directory "
            "to resume into it"
        )

    component = derive_name_component(root, groups, env=resolved_env)
    owner = _workspace_owner(root, groups, env=resolved_env)

    if owner is None:
        # Anywhere but a camp workspace, the allowlist is the containment
        # boundary — so the group supplying it is named explicitly, exactly as
        # `--dir` requires, and never inferred from where camp was invoked.
        if not explicit_group:
            _die(
                f"camp launch: session {candidate.session_id} was started in "
                f"{printable_path(root)}, "
                "which is not a camp workspace — re-run with an explicit --group "
                "<name> whose [launch] roots allowlist covers it"
            )
        if group is None:
            _die(f"camp launch: no camp group named {explicit_group!r} is configured")

    config = group if owner is None else owner
    restored = _history_restored(config, candidate.session_id, root, resolved_env)

    try:
        # The recorded root IS the launch directory, for a workspace session as
        # much as for any other: a harness routinely starts BELOW the workspace
        # root, and re-deriving the directory from the group's configuration would
        # bring the session back up somewhere it never ran while still reporting
        # success. Only two things differ between the branches — which group
        # supplies the harness profile, and whether the eligibility gate has
        # anything to fence, since camp built the workspace itself.
        launched = launch_and_confirm(
            config,
            env=env,
            root=root,
            name_component=component,
            trust_scope=root,
            resume_session_id=candidate.session_id,
            camp_managed_root=owner is not None,
        )
    except LaunchError as exc:
        _die(_refusal(exc))
        return

    if not restored:
        print(
            f"camp launch: session {candidate.session_id} came back with NO PRIOR "
            "HISTORY — its transcript is gone, so this is a fresh, empty session "
            "under the old reference and the conversation did not come back",
            file=sys.stderr,
        )
    _report_launched(
        launched, as_json=as_json, extra=None if restored else {"history_restored": False}
    )


def _cmd_launch_group_cli(
    args: list[str],
    group: dict | None,
    env: dict[str, str] | None,
) -> None:
    """camp launch <slug> | --dir <path> --group <name> | --resume <ref>, [--json].

    Three addressing forms, one engine. A slug launches into the workspace camp
    provisioned for it; `--dir` launches at a directory the operator names, fenced
    by the group's `[launch] roots` allowlist; `--resume` re-enters a session the
    harness already holds, rooted where that session recorded it started. All
    three are mutually exclusive — a launch is rooted at a directory, at a
    workspace, or re-enters an existing session, never two of the three.

    `--dir` REQUIRES an explicit `--group`, and so does a `--resume` whose root is
    NOT a camp workspace. The allowlist is the containment boundary for both, so
    which group supplies it must never depend on the directory camp happened to be
    invoked from — a boundary that moves with the caller is not a boundary. This
    is why `--group` is read for its value here rather than merely dropped: the
    value IS the signal that the operator named the group. A resume into a camp
    workspace is the exception that proves it: camp built that directory itself,
    reads the owning group off the path, and needs no flag at all.

    *group* is therefore optional. A workspace resume must answer from a plain
    shell outside every group directory — the ref names everything camp needs —
    so the router hands this handler `None` on that path rather than refusing
    upstream for want of a group nobody had to name.

    Every flag is consumed BEFORE slug resolution. An unconsumed one would be
    forwarded as a positional and die as a flag-shaped slug, which reports the
    wrong problem.

    Output contract, mirroring `camp pwd`: stdout carries ONLY the session id —
    exactly one line — so a caller can capture it with `$(camp launch …)`. The
    workspace, the tmux attach handle, and the confirmation all go to stderr. On
    any refusal stdout is EMPTY and the exit code is non-zero, with one deliberate
    exception: an ambiguous `--resume` ref prints its candidate rows to stdout and
    exits `2`, because there the rows are the answer.
    """
    from ..group.config import load_all_groups
    from ..launch.recovery import derive_name_component
    from ..launch.session import LaunchError
    from ..spine import _consume_flag_value, _die
    from .common import _groups_dir
    from .dispatch import _slug_from_args_or_cwd

    rest = list(args)
    explicit_group = _consume_flag_value(rest, "--group")
    resume_ref = _consume_flag_value(rest, RESUME_FLAG)
    directory = _consume_flag_value(rest, "--dir")
    as_json = _consume_flag(rest, "--json")
    # Consumed unconditionally, like every other flag here, and BEFORE the
    # positional slug is read — an unconsumed `--prompt` would otherwise be
    # read as the slug and die reporting the wrong problem. Taken as free text:
    # a value beginning with `-` is still the prompt, never a camp flag,
    # because `_consume_flag_value` grabs whatever token follows `--prompt`
    # regardless of its shape.
    prompt = _consume_flag_value(rest, "--prompt")

    if directory is None and "--dir" in rest:
        # `--dir` with nothing after it: consumed by neither branch above.
        _die("camp launch: --dir requires a directory path")
    if resume_ref is None and RESUME_FLAG in rest:
        _die("camp launch: --resume requires a session reference")
    if prompt is None and "--prompt" in rest:
        _die("camp launch: --prompt requires a value")
    if prompt is not None and not prompt.strip():
        _die("camp launch: --prompt requires a non-empty value")

    if directory is not None and resume_ref is not None:
        _die(
            "camp launch: --dir and --resume are mutually exclusive — a launch "
            "is rooted at a named directory or re-enters an existing session, "
            "never both"
        )

    if resume_ref is not None and prompt is not None:
        _die(
            "camp launch: --resume and --prompt are mutually exclusive — a "
            "prompt becomes a session's first turn, and a resumed session is "
            "already past its first turn"
        )

    if resume_ref is not None:
        if rest:
            _die(
                "camp launch: --resume and a workspace slug are mutually exclusive "
                "— a launch re-enters an existing session or starts a new one in a "
                "workspace, never both"
            )
        if not resume_ref.strip():
            _die("camp launch: --resume requires a session reference")
        if resume_ref.startswith("-"):
            # Same reason a slug may not be flag-shaped: this is what an
            # unconsumed flag directly after `--resume` looks like, and reporting
            # it as an unmatched reference would name the wrong problem.
            _die(
                f"camp launch: --resume: {resume_ref!r} looks like a flag, not a "
                "session reference — a reference may not start with a dash"
            )
        _launch_resume(
            resume_ref,
            group=group,
            explicit_group=explicit_group,
            env=env,
            as_json=as_json,
        )
        return

    slug: str | None = None
    root: Path | None = None
    name_component: str | None = None
    trust_scope: Path | None = None

    if directory is not None:
        if rest:
            _die(
                "camp launch: --dir and a workspace slug are mutually exclusive — a "
                "launch is rooted at a named directory or at a workspace, never both"
            )
        if not directory.strip():
            _die("camp launch: --dir requires a directory path")
        if not explicit_group:
            _die(
                "camp launch: --dir requires an explicit --group <name> — the "
                "group's [launch] roots allowlist is what fences a directory-rooted "
                "launch, so it must never depend on the directory camp was invoked "
                "from"
            )
        # `~` expands here for the same reason it does in `camp sessions --dir`:
        # a quoted `--dir '~/code'` reaches camp unexpanded, and resolving it
        # against the current directory would refuse while naming a path that
        # exists nowhere.
        root = Path(directory).expanduser()
        # The name component comes from the one name rule every flavor derives
        # through, over the RESOLVED path — so `--dir .` and a trailing slash name
        # the directory the session actually runs in, and a directory inside a camp
        # workspace is named by its slug exactly as a later `--resume` of that same
        # session reconstructs it. Two names for one session would mean the tmux
        # duplicate-name claim could never fire for it, and that claim is the
        # race-proof backstop. The trust scope is that same directory: a named root
        # is its own confinement, which is exactly why the eligibility gate — not
        # the trust pre-seed — is the boundary here.
        name_component = derive_name_component(
            root,
            load_all_groups(_groups_dir()),
            env=dict(env) if env is not None else dict(os.environ),
        )
        trust_scope = root
    else:
        rest_before_slug = list(rest)
        slug = _slug_from_args_or_cwd(
            rest, group, verb="launch", consume_positional=True, env=env
        )
        if rest == rest_before_slug and rest:
            # `_slug_from_args_or_cwd` took args[0] as the positional slug but
            # never removes it from `rest` (it only mutates `rest` in place
            # when `--name` was consumed instead) — drop it here so the
            # leftover check below never mistakes the slug for an unconsumed
            # flag.
            rest = rest[1:]
        unrecognized = next((tok for tok in rest if tok.startswith("-")), None)
        if unrecognized is not None:
            _die(f"camp launch: unrecognized flag: {unrecognized!r}")

    try:
        launched = launch_and_confirm(
            group,
            slug,
            env=env,
            root=root,
            name_component=name_component,
            trust_scope=trust_scope,
            initial_prompt=prompt,
        )
    except LaunchError as exc:
        _die(_refusal(exc))
        return

    _report_launched(launched, as_json=as_json)


def _session_payload(record) -> dict:
    """One :class:`SessionRecord` as JSON-ready data — normalized fields only.

    The seam already drops harness-native fields beyond the normalized set; this
    keeps camp from re-widening the surface it just narrowed.
    """
    return {
        "session_id": record.session_id,
        "cwd": str(record.cwd),
        "kind": record.kind,
        "controllable": record.controllable,
        "name": record.name,
        "pid": record.pid,
        "started_at": record.started_at.isoformat() if record.started_at else None,
    }


def _enumerate_sessions(group: dict, workspace: Path | None, env: dict[str, str] | None):
    """Return the live session records, or None when they cannot be determined.

    None is the honest "I could not tell" — a harness camp cannot name, a harness
    with no enumeration concept, a missing binary, a non-zero exit, or output the
    seam refuses to decode. It is deliberately distinct from `[]`, which is the
    equally honest "nothing is running": the caller prints a notice for the first
    and stays silent for the second.
    """
    from ..launch.profile import harness_for
    from ..launch.session import enumerate_records

    harness = harness_for(group)
    if harness is None:
        return None
    try:
        return enumerate_records(
            harness, workspace, dict(env) if env is not None else dict(os.environ)
        )
    except Exception:  # noqa: BLE001 — every failure of a read-only query degrades
        return None


def _list_recoverable(
    scope: Path | None,
    *,
    env: dict[str, str] | None,
    as_json: bool,
    limit: int | None,
    where: str,
) -> None:
    """Print the DEAD sessions in *scope* — enumerated transcripts minus the live set.

    BOTH HALVES OF THE SUBTRACTION ARE SCOPED BY THE SAME ARGUMENT. *scope* goes
    to the transcript enumeration and to the live enumeration unchanged, and the
    seam defines both as "cwd equal to or under this path, on resolved paths".
    Scoping one half and not the other would report live sessions as recoverable
    — the one answer this listing must never give.

    The pool spans every harness camp can name, not just the invoking group's:
    what is recoverable is a property of the sessions that exist, and standing
    in one group does not make another group's dead sessions disappear.

    Three outcomes, deliberately distinct on the operator's terminal:

    * NO harness keeps transcripts camp can read → a REFUSAL naming them. This is
      the one non-degrading path on a question verb, because the answer is "camp
      cannot do this here", not "nothing is recoverable", and an operator who
      reads the second for the first stops looking for their session.
    * The live set is UNDETERMINABLE → the live listing's own notice and an empty
      list, exit 0. The unsubtracted pool is never printed: every row in it might
      be a session running right now.
    * Nothing is recoverable → an explicit line saying so, worded so it cannot be
      mistaken for the refusal above.

    Rows go to stdout and every notice to stderr, so a caller parsing stdout gets
    rows and nothing else — including the empty JSON list, which is an answer.
    """
    from ..group.config import load_all_groups
    from ..launch.recovery import recoverable_candidates
    from ..launch.session import enumerate_records
    from ..spine import _die
    from .common import _groups_dir

    resolved_env = dict(env) if env is not None else dict(os.environ)
    groups = load_all_groups(_groups_dir())
    harnesses = _addressable_harnesses(groups)
    if not harnesses:
        _die(
            "camp sessions: camp cannot name a harness for any configured group, "
            "so it cannot tell which sessions are recoverable"
        )

    transcripts: list = []
    live: list = []
    answered: list = []
    live_known = True
    for harness in harnesses:
        try:
            rows = harness.session_transcripts(scope, env=resolved_env)
        except Exception:  # noqa: BLE001 — a harness camp cannot read contributes nothing
            rows = None
        if rows is None:
            continue
        answered.append(harness)
        transcripts.extend(rows)
        try:
            records = enumerate_records(harness, scope, resolved_env)
        except Exception:  # noqa: BLE001 — an unanswerable probe is undeterminable, not empty
            records = None
        if records is None:
            live_known = False
        else:
            live.extend(records)

    if not answered:
        names = ", ".join(_harness_display_name(harness) for harness in harnesses)
        _die(
            f"camp sessions: harness {names} keeps no session transcripts camp can "
            "read, so camp cannot tell which of its sessions are recoverable"
        )

    candidates: tuple = ()
    if live_known:
        candidates = recoverable_candidates(
            transcripts=transcripts,
            live_records=live,
            groups=groups,
            env=resolved_env,
        )

    shown = candidates if limit is None else candidates[:limit]
    _print_candidates(shown, as_json=as_json)

    if not live_known:
        print(
            f"camp sessions: could not determine the live sessions{where} — "
            "reporting none",
            file=sys.stderr,
        )
    elif not candidates:
        print(f"camp sessions: no recoverable sessions{where}", file=sys.stderr)
    elif len(shown) < len(candidates):
        print(
            f"camp sessions: showing the {len(shown)} newest of {len(candidates)} "
            f"recoverable sessions{where} — re-run with --limit <n> or --all "
            "for the rest",
            file=sys.stderr,
        )


def _cmd_sessions_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
) -> None:
    """camp sessions [<slug>] [--dir <path>] [--recoverable [--limit <n>|--all]] [--json].

    Two listings behind one verb, over the same scope. The LIVE listing answers
    what is running; `--recoverable` answers what is dead and could be brought
    back. Scope is the workspace when a slug is given or resolves from cwd, the
    directory named by `--dir`, and everything otherwise — and it reaches both
    listings by the same argument, so the subtraction that produces the
    recoverable rows covers the same set on both sides.

    `--dir` is NOT eligibility-gated. The allowlist fences launching, not
    looking, and a listing that refused to describe a directory would tell the
    operator nothing they could not learn by looking at it. The path need not
    exist either: a torn-down root is precisely the scope a recovery listing is
    asked about.

    Always exits 0 with ONE exception each side of the split. This is a question,
    so "I could not tell" degrades to a stderr notice plus an empty list rather
    than a failure a script has to special-case — but malformed input (a `--limit`
    that cannot mean anything) and a harness that keeps no transcripts at all are
    refusals, because neither has an empty listing as its honest answer.
    """
    from ..group.manifest import workspace_dir
    from ..spine import _consume_flag_value, _die
    from .dispatch import _slug_from_args_or_cwd

    rest = list(args)
    _consume_flag_value(rest, "--group")  # already resolved upstream; drop it
    as_json = _consume_flag(rest, "--json")
    recoverable = _consume_flag(rest, "--recoverable")
    show_all = _consume_flag(rest, "--all")
    directory = _consume_flag_value(rest, "--dir")
    limit_raw = _consume_flag_value(rest, "--limit")

    if directory is None and "--dir" in rest:
        _die("camp sessions: --dir requires a directory path")
    if limit_raw is None and "--limit" in rest:
        _die("camp sessions: --limit requires a count")

    if not recoverable and (show_all or limit_raw is not None):
        _die(
            "camp sessions: --limit and --all widen the --recoverable listing; "
            "the live listing is not capped, so there is nothing for them to widen"
        )
    if show_all and limit_raw is not None:
        _die(
            "camp sessions: --limit and --all are mutually exclusive — a listing "
            "is capped at a count or not capped at all, never both"
        )

    limit = _RECOVERABLE_DEFAULT_LIMIT
    if limit_raw is not None:
        try:
            limit = int(limit_raw)
        except ValueError:
            _die(f"camp sessions: --limit expects a whole number, not {limit_raw!r}")
        if limit < 1:
            _die(f"camp sessions: --limit expects a count of at least 1, not {limit}")

    slug: str | None = None
    scope: Path | None = None
    if directory is not None:
        if not directory.strip():
            _die("camp sessions: --dir requires a directory path")
        if rest:
            _die(
                "camp sessions: --dir and a workspace slug are mutually exclusive "
                "— a listing is scoped to a named directory or to a workspace, "
                "never both"
            )
        # Non-strict: a directory that no longer exists is a scope worth asking
        # about, and is the whole reason the recoverable listing marks rows
        # root-missing rather than hiding them.
        scope = Path(directory).expanduser().resolve()
    else:
        slug = _slug_from_args_or_cwd(
            rest, group, verb="sessions", consume_positional=True, allow_none=True, env=env
        )
        if slug:
            scope = workspace_dir(group["group"]["name"], slug, env=env)
            try:
                # Mirror the launch engine's resolution (`_resolve_launch_dir`): a
                # symlinked workspace dir must scope enumeration by the same
                # resolved path a just-launched session registered under, or a
                # slug-scoped query never finds it.
                scope = scope.resolve(strict=True)
            except OSError:
                pass

    if recoverable:
        if slug:
            where = f" in workspace {slug!r}"
        elif directory is not None:
            where = f" under {scope}"
        else:
            where = ""
        _list_recoverable(
            scope,
            env=env,
            as_json=as_json,
            limit=None if show_all else limit,
            where=where,
        )
        return

    records = _enumerate_sessions(group, scope, env)
    if records is None:
        if slug:
            described = f"workspace {slug!r}"
        elif directory is not None:
            described = f"directory {str(scope)!r}"
        else:
            described = f"group {group['group']['name']!r}"
        print(
            f"camp sessions: could not determine the live sessions for {described} — "
            "reporting none",
            file=sys.stderr,
        )
        records = []

    if as_json:
        print(json.dumps([_session_payload(record) for record in records]))
        return
    from ..launch.recovery import printable_path

    for record in records:
        label = f" ({record.name})" if record.name else ""
        print(f"{record.session_id}  {record.kind}  {printable_path(record.cwd)}{label}")


# ---------------------------------------------------------------------------
# camp kill — stop one addressed session
# ---------------------------------------------------------------------------

def _report_stop(candidate, *, outcome: str, as_json: bool) -> None:
    """The success report for a stop — one dict literal, one row shape.

    ``outcome`` is what tells the two SUCCESSES apart. Both exit 0, so an exit
    code cannot carry the difference, and a caller that has to know whether it
    reclaimed anything reads it here rather than parsing prose off stderr.
    """
    if as_json:
        print(
            json.dumps(
                {
                    "session_id": candidate.session_id,
                    "tmux_name": candidate.derived_name,
                    "outcome": outcome,
                }
            )
        )
        return
    print(candidate.session_id)


def _stop_refusal(candidate, reason: str) -> str:
    """The one `camp kill: …` line for a refusal, chosen by *reason*.

    Every reason gets its own sentence because the operator's next move differs
    for each, and a shared "camp will not stop this" would leave them with
    nothing to act on. The two that are easiest to collapse are deliberately
    apart: a session that is live while owning no tmux session has nothing for
    camp to signal at all, while a foreign pane holding the name has something
    running that camp did not start — the first is a session to investigate,
    the second is a name to investigate.
    """
    from ..launch.stop import (
        REFUSED_ANCHOR,
        REFUSED_LIVE_WITHOUT_SESSION,
        REFUSED_NOT_CAMP_LAUNCHED,
        REFUSED_SELF,
        REFUSED_TMUX_UNANSWERED,
    )

    session_id = candidate.session_id
    name = candidate.derived_name
    if reason == REFUSED_ANCHOR:
        return (
            f"camp kill: session {session_id} is the concierge anchor — stopping it "
            "would take away the entry point every other session is started from"
        )
    if reason == REFUSED_SELF:
        return (
            f"camp kill: session {session_id} is the session camp is running in — a "
            "session cannot stop itself; run this from another session"
        )
    if reason == REFUSED_LIVE_WITHOUT_SESSION:
        return (
            f"camp kill: session {session_id} is still running but owns no tmux "
            f"session named {name}, so there is nothing here for camp to signal — "
            "its memory cannot be reclaimed by stopping a session that is not there"
        )
    if reason == REFUSED_NOT_CAMP_LAUNCHED:
        return (
            f"camp kill: the tmux session {name} is held by a pane camp did not "
            "launch, so camp will not signal it — a name match is not proof of "
            "ownership"
        )
    if reason == REFUSED_TMUX_UNANSWERED:
        return (
            f"camp kill: tmux did not answer, so camp cannot tell whether session "
            f"{session_id} was stopped — assume its memory was not reclaimed and "
            "re-run once tmux responds"
        )
    return f"camp kill: camp will not stop session {session_id}"


def _cmd_kill_cli(args: list[str], env: dict[str, str] | None = None) -> None:
    """camp kill <ref> [--json].

    Stop ONE session and reclaim its memory, leaving its workspace, worktree,
    and working tree completely untouched — nothing is removed, cleaned, or
    marked, and camp persists nothing.

    Fully groupless, like `camp launch --resume`: the reference names the
    session and the session names everything else, so this answers from a plain
    shell outside every group directory. It is also the verb an operator reaches
    for when something is already broken, so the group configs are read
    tolerantly rather than aborting the verb: a group camp cannot parse is
    skipped, and its workspaces lose the slug component of their derived name.
    That is a real cost, not a free one — a session whose name camp can no
    longer derive is a session this verb can no longer address — but it is
    borne by the unparsable group alone, and the alternative is a sibling
    group's broken toml taking down the surface that reclaims memory.

    All of the decision-making — resolution, the ownership check, the anchor and
    self gates, the already-down oracle, and the re-poll for absence — lives in
    `camp.launch.stop`. This handler owns the CLI's four jobs: parsing, the
    stdout/stderr split, the exit code, and the wording.

    Posture: kill is an ACTION, matching `camp launch`. Every failure is exactly
    one `camp kill: …` line on stderr with empty stdout and a non-zero exit —
    INCLUDING a session still present after the kill, which is a failure and not
    a success with a caveat: the memory was not reclaimed. The single deliberate
    exception is an ambiguous ref, which prints its candidates on stdout and
    exits 2, because there the rows are the answer.
    """
    from ..launch.recovery import Ambiguous, NoMatch
    from ..launch.stop import AlreadyDown, Refused, StillPresent, stop_session
    from ..spine import _consume_flag_value, _die

    rest = list(args)
    _consume_flag_value(rest, "--group")  # a ref names the session; no group needed
    as_json = _consume_flag(rest, "--json")

    if not rest:
        _die(
            "camp kill: requires a session reference — an unambiguous prefix of a "
            "session's name or id, as `camp sessions` and `camp launch --resume` "
            "use"
        )
    if len(rest) > 1:
        _die(
            f"camp kill: one session reference, not {len(rest)} — a stop addresses "
            "exactly one session"
        )
    ref = rest[0]
    if not ref.strip():
        _die("camp kill: requires a session reference")
    if ref.startswith("-"):
        _die(
            f"camp kill: {ref!r} looks like a flag, not a session reference — a "
            "reference may not start with a dash"
        )

    resolved_env = dict(env) if env is not None else dict(os.environ)
    groups = _parsable_groups()
    transcripts, live, answered = _session_pool(
        groups, verb="kill", env=resolved_env, live_required=True
    )

    outcome = stop_session(
        ref,
        # The ownership check asks a harness which pane commands IT composes, so
        # it needs one harness rather than the pool. Groups routinely share a
        # harness and `_addressable_harnesses` already deduplicates, so this is
        # the only one on all but a mixed-harness machine — where a foreign
        # harness's session is REFUSED rather than mis-signalled, which is the
        # direction this check is supposed to fail in.
        harness=answered[0],
        transcripts=transcripts,
        live_records=live,
        groups=groups,
        env=resolved_env,
    )

    if isinstance(outcome, (Ambiguous, NoMatch)):
        _die_unresolved(
            outcome,
            ref,
            verb="kill",
            harness=answered[0],
            env=resolved_env,
            as_json=as_json,
        )

    candidate = outcome.candidate

    if isinstance(outcome, Refused):
        _die(_stop_refusal(candidate, outcome.reason))

    if isinstance(outcome, StillPresent):
        _die(
            f"camp kill: session {candidate.session_id} is still running as "
            f"{candidate.derived_name} after the stop — its memory was not reclaimed"
        )

    if isinstance(outcome, AlreadyDown):
        print(
            f"camp kill: session {candidate.session_id} ({candidate.derived_name}) "
            "was already down — nothing to stop",
            file=sys.stderr,
        )
        _report_stop(candidate, outcome="already-down", as_json=as_json)
        return

    # The reference does not change across a stop: the harness preserves the
    # session id through a resume, so the transcript, the derived name, and the
    # ref an operator holds are all stable over arbitrarily many cycles. Saying
    # so here is what makes a stop read as recoverable rather than final.
    print(
        f"camp kill: stopped session {candidate.session_id} "
        f"({candidate.derived_name}) — its memory is reclaimed; "
        f"`camp launch --resume {candidate.session_id}` brings it back under "
        "this same reference",
        file=sys.stderr,
    )
    _report_stop(candidate, outcome="stopped", as_json=as_json)
