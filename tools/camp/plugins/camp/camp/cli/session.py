"""The session command group: ``launch`` (start one) and ``sessions`` (list them).

Both are group-resolved like every other workspace verb. The engine lives in
``camp.launch.session``; this module owns only the CLI's three jobs — flag
parsing, the stdout/stderr split, and turning a :class:`LaunchError` into camp's
one-line refusal.

Two deliberately different postures:

``launch`` is an ACTION, so every failure is a refusal — one ``camp launch: …``
stderr line, empty stdout, non-zero exit. That includes a launch that spawned but
never registered: an unconfirmable session is not a success with a caveat.

``sessions`` is a QUESTION, so most failures DEGRADE — a stderr notice, an empty
list on stdout, exit 0. A caller asking what is running can act on "nothing" and
on "I could not tell" the same way (there is nothing to attach to either way), and
exiting non-zero for the second would make a read-only query a scripting hazard.
An honestly-empty answer stays silent.

The live listing asks every (harness, credential store) candidate in the pool —
never only the group the invocation happened to resolve — and merges what comes
back (see ``_enumerate_live_sessions_pool``). A store that cannot be read
degrades to a stderr notice naming the ACCOUNT it could not reach while the
stores that answered still contribute their rows, exit 0 — the read-only,
partial-information case above. But when EVERY addressable store fails, "nothing
to attach to" is no longer true: something might well be running under a store
camp simply could not ask, so that case is a REFUSAL — non-zero exit, no rows,
a stated reason — never an empty answer indistinguishable from "nothing is
running". The `--json` form carries the same partial/total distinction IN BAND:
every row (session or not) carries `"ok"`, so a parser sorts a mixed answer
without touching stderr, and a total failure prints no array at all rather than
an empty one a parser could read as complete.

``camp new --launch`` reuses this module rather than re-deriving the flow, so a
launch means the same thing and refuses the same way at both entry points.

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
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from ..host.config import Host

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
):
    """Spawn a session — by workspace *slug* or at a named *root* — and confirm it.

    The addressing arguments are the engine's own, forwarded whole: exactly one of
    *slug* or the (*root*, *name_component*, *trust_scope*) triple, which the
    engine enforces. *camp_managed_root* and *resume_session_id* ride on that
    triple and on either flavor respectively. Everything after the spawn is
    identical for all three flavors, so they report the same three stderr lines
    and return the same :class:`LaunchedSession`.

    Raises :class:`LaunchError` on refusal — including a spawn that never
    confirmed, which the engine has already killed.
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
    (any automation that puts an agent straight
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


def _candidate_row_line(row: dict) -> str:
    """A relayed `--host` candidate row, human-rendered — the JSON-shaped
    counterpart to :func:`_candidate_line`, from a `_candidate_payload`-shaped
    dict (``session_id``, ``tmux_name``, ``root``, ``age_seconds``,
    ``root_missing``) rather than a `Candidate` object, because a relayed
    ambiguous answer's rows never carry the local resolver's own type.

    A remote camp of a different version can omit a key this depends on;
    that is a `KeyError` a caller degrades per row, exactly as
    `render_session_row_human` does for a `sessions --host` row.

    Every interpolated field — not just `root` — goes through
    `printable_path`, which despite the name works on any relayed string:
    `tmux_name` and `session_id` are as untrusted as `root` is, and control
    sequences that `_strip_control_sequences` deliberately preserves
    (newline, tab) can otherwise render one relayed row as two, forging a
    candidate the far side never sent.
    """
    from ..launch.recovery import printable_path

    root = row.get("root")
    if root is None:
        where = "directory unknown"
    elif row.get("root_missing"):
        where = f"{printable_path(root)} (gone)"
    else:
        where = printable_path(root)
    return (
        f"{printable_path(row['tmux_name'])}  {printable_path(row['session_id'])}  {where}  "
        f"{_format_age(row.get('age_seconds'))}"
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
    underlying = getattr(harness, "harness", harness)
    return harness.name or type(underlying).__name__


def _account_label(account: str | None) -> str:
    """How to name *account* in operator-facing text — never a bare ``None``."""
    return f"account {account!r}" if account is not None else "the default account"


def _addressable_harnesses(
    groups, *, env: dict[str, str] | None = None, on_drop=None
) -> list:
    """Every (harness, credential store) camp can ask about sessions.

    A reference addresses a SESSION, not a group. Naming a group, or standing in
    one, does not change which sessions exist, so the pool spans every configured
    group's harness rather than whichever one the invocation happened to resolve
    — the same reason a resume needs no ``--group`` in the first place.

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
) -> tuple[list, list, list, dict[str, str | None]]:
    """The addressable pool: (transcripts, live records, stores that answered,
    session id -> declaring account).

    Every addressable STORE's on-disk transcripts UNION its live sessions, each
    queried under its OWN environment — a store whose declared account differs
    from another's must be read from its own credential directory, never from
    whichever store the pool happened to query first, or a session sitting in
    the unqueried store is invisible to every reference that would otherwise
    match it. A store with no transcript concept — or one camp cannot read at
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

    The returned mapping is session id -> the account that store declared (or
    ``None``), first-write-wins across stores. It exists so a refusal that
    turns out ambiguous ACROSS stores can name the account each match came
    from, without :class:`~camp.launch.recovery.SessionCandidate` — which is
    shared by every session surface, not just the ref-addressed ones — having
    to carry a field only this refusal reads.
    """
    from ..launch.session import enumerate_records
    from ..spine import _die

    harnesses = _addressable_harnesses(groups, env=env)
    if not harnesses:
        _die(
            f"camp {verb}: camp cannot name a harness for any configured group, so "
            "it cannot look up the session this reference addresses"
        )

    transcripts: list = []
    live: list = []
    answered: list = []
    accounts: dict[str, str | None] = {}
    for harness in harnesses:
        store_env = getattr(harness, "env", env)
        try:
            records = enumerate_records(harness, None, store_env)
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
        for record in records or ():
            accounts.setdefault(record.session_id, getattr(harness, "account", None))
        live.extend(records or [])
        try:
            rows = harness.session_transcripts(env=store_env)
        except Exception:  # noqa: BLE001 — a harness camp cannot read contributes nothing
            rows = None
        if rows is not None:
            answered.append(harness)
            for row in rows:
                accounts.setdefault(row.session_id, getattr(harness, "account", None))
            transcripts.extend(rows)

    if not answered:
        names = ", ".join(_harness_display_name(harness) for harness in harnesses)
        _die(
            f"camp {verb}: harness {names} keeps no session transcripts camp can "
            "read, so its sessions cannot be addressed by reference"
        )
    return transcripts, live, answered, accounts


def _die_unresolved(
    outcome,
    ref: str,
    *,
    verb: str,
    harness,
    env: dict[str, str],
    as_json: bool,
    accounts: dict[str, str | None] | None = None,
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

    An ambiguous match spanning more than one credential store is not a second
    refusal shape — it is this same one, with *accounts* (session id -> the
    account that store declared) letting the message name which store each
    match came from, so an operator is never left guessing which of two
    same-named sessions on different accounts a longer prefix would even
    disambiguate. That detail is added ONLY when the matches actually span more
    than one account — an ambiguity inside a single store names nothing new.
    """
    from ..launch.recovery import Ambiguous, NoMatch
    from ..spine import _die

    if isinstance(outcome, Ambiguous):
        _print_candidates(outcome.candidates, as_json=as_json)
        by_account = ""
        if accounts:
            matched_accounts = {accounts.get(c.session_id) for c in outcome.candidates}
            if len(matched_accounts) > 1:
                by_account = " — " + "; ".join(
                    f"{candidate.derived_name} in "
                    f"{_account_label(accounts.get(candidate.session_id))}"
                    for candidate in outcome.candidates
                )
        _die(
            f"camp {verb}: {ref!r} matches {len(outcome.candidates)} sessions "
            f"(listed above){by_account} — re-run with a longer prefix naming "
            "exactly one",
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
    transcripts, live, answered, accounts = _session_pool(groups, verb="launch", env=env)

    outcome = resolve_session_ref(
        ref, transcripts=transcripts, live_records=live, groups=groups, env=env
    )

    if isinstance(outcome, Resolved):
        return outcome.candidate, groups

    _die_unresolved(
        outcome,
        ref,
        verb="launch",
        harness=answered[0],
        env=env,
        as_json=as_json,
        accounts=accounts,
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
    from ..launch.recovery import derive_name_component, is_workspace_root
    from ..launch.session import LaunchError
    from ..spine import _consume_flag_value, _die
    from .common import _groups_dir
    from .dispatch import _slug_from_args_or_cwd

    rest = list(args)
    explicit_group = _consume_flag_value(rest, "--group")
    resume_ref = _consume_flag_value(rest, RESUME_FLAG)
    directory = _consume_flag_value(rest, "--dir")
    as_json = _consume_flag(rest, "--json")

    if directory is None and "--dir" in rest:
        # `--dir` with nothing after it: consumed by neither branch above.
        _die("camp launch: --dir requires a directory path")
    if resume_ref is None and RESUME_FLAG in rest:
        _die("camp launch: --resume requires a session reference")

    if directory is not None and resume_ref is not None:
        _die(
            "camp launch: --dir and --resume are mutually exclusive — a launch "
            "is rooted at a named directory or re-enters an existing session, "
            "never both"
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
    camp_managed_root = False

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
        # The same claim the resume path makes for a workspace-rooted session:
        # a directory inside a group's OWN workspace tree was chosen by camp, not
        # by the operator, so the allowlist — which asks who chose it — has
        # nothing left to answer. It is a claim, not a grant: the engine re-checks
        # it against the name rule and falls back to the allowlist when it does
        # not hold, and the credential rule runs on both branches regardless.
        #
        # Without this, rooting a worker at the member repo it owns would mean
        # widening `[launch] roots` to cover camp's own state directory, which
        # would also open every other workspace on the machine.
        camp_managed_root = is_workspace_root(
            root,
            [group] if group is not None else [],
            env=dict(env) if env is not None else dict(os.environ),
        )
    else:
        slug = _slug_from_args_or_cwd(
            rest, group, verb="launch", consume_positional=True, env=env
        )

    try:
        launched = launch_and_confirm(
            group,
            slug,
            env=env,
            root=root,
            name_component=name_component,
            trust_scope=trust_scope,
            camp_managed_root=camp_managed_root,
        )
    except LaunchError as exc:
        _die(_refusal(exc))
        return

    _report_launched(launched, as_json=as_json)


def _attribute_session(cwd: Path, groups: list[dict], *, env: dict[str, str]) -> dict:
    """Resolve the group and declared account *cwd* belongs to.

    Uses :func:`camp.group.resolve.resolve_from_cwd` — the SAME resolver the
    dispatcher applies to the invoking process's own cwd
    (``cli/dispatch.py``'s ``_resolve_group_for_command``) — so a session
    rooted in a member repository checkout attributes exactly like one rooted
    in a workspace, and the group name returned here is spelled exactly as
    ``--group`` accepts it and ``resolve_from_cwd`` returns it: a later filter
    over these rows compares against this value directly, with no
    normalization step of its own.

    A *cwd* no configured group's resolver recognizes raises
    ``GroupResolutionError`` from ``resolve_from_cwd`` — an ordinary, expected
    outcome here, degraded to a null group and a null account rather than
    propagated or dropping the row: enumeration already found and reported
    this session, so a row naming no home is a real answer, not a failure.

    ``account`` is the resolved group's ``[launch] account`` exactly as
    declared — carried verbatim, like :class:`~camp.launch.profile.HarnessStore`'s
    own ``account`` field, never expanded, normalized, or resolved — and
    ``None`` wherever the group declares none.
    """
    from ..group.resolve import GroupResolutionError, resolve_from_cwd

    try:
        group_name, _slug = resolve_from_cwd(cwd, groups, env=env)
    except GroupResolutionError:
        return {"group": None, "account": None}

    group = next((cfg for cfg in groups if cfg["group"]["name"] == group_name), None)
    account = (group.get("launch") or {}).get("account") if group is not None else None
    return {"group": group_name, "account": account}


def _sessions_for_group(
    attributed: list[tuple], group_name: str
) -> list[tuple]:
    """Narrow an attributed ``(record, attribution)`` set to one group's rows.

    *attributed* is the whole cross-store, cross-group answer — every store's
    records, each already paired with :func:`_attribute_session`'s result — so
    this is a pure filter over an answer that already exists, never a second
    enumeration. A row whose attribution has no group (an unresolvable *cwd*,
    or a store-failure row this function never receives) never matches any
    name and is dropped, never kept as "unattributed but maybe relevant".

    This is the ONE seam a caller narrows the live answer through. Asking for
    every group at once is a DIFFERENT caller of the same *attributed* set —
    it bypasses this function rather than this function growing a condition
    to widen through.
    """
    return [
        (record, attribution)
        for record, attribution in attributed
        if attribution["group"] == group_name
    ]


def _session_payload(record, *, group: str | None, account: str | None) -> dict:
    """One :class:`SessionRecord` as JSON-ready data — normalized fields only.

    The seam already drops harness-native fields beyond the normalized set; this
    keeps camp from re-widening the surface it just narrowed.

    ``ok`` is ``True`` on every row this function builds. It exists so a
    machine-readable consumer can tell a session row from a partial-failure
    row (see :func:`_store_failure_payload`) with ONE field test, on every row
    in the list, rather than by the row's shape or the absence of a key.

    ``group`` and ``account`` come from :func:`_attribute_session`, resolved
    from ``record.cwd`` — not from which credential store's enumeration
    produced this record, which can differ from the group the session's
    working directory actually belongs to.
    """
    return {
        "ok": True,
        "session_id": record.session_id,
        "cwd": str(record.cwd),
        "kind": record.kind,
        "controllable": record.controllable,
        "name": record.name,
        "pid": record.pid,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "group": group,
        "account": account,
    }


def _store_failure_payload(failure: dict) -> dict:
    """One unreadable-store entry as JSON-ready data.

    ``ok`` is ``False`` — the same field :func:`_session_payload` sets ``True``
    on every session row, so a consumer tests one field to sort a mixed list
    into rows it can use and rows it cannot. The row carries only what a
    failed enumeration actually knows: which account it was asking about, and
    why the answer never came back. It invents no session attribution — no
    ``cwd``, no ``pid`` — because none was ever read.
    """
    return {"ok": False, "account": failure["account"], "reason": failure["reason"]}


def _group_config_failure_payload(detail: str) -> dict:
    """One unparsable-group-config entry as JSON-ready data.

    ``ok`` is ``False`` — the same discriminator :func:`_store_failure_payload`
    carries for a store that failed to answer, one level down. *detail* is
    the same ``camp <verb>: <detail> — skipping`` text already printed to
    stderr by :func:`~camp.provision.lifecycle.answerable_groups_or_refuse`
    (naming the config file, since a config that failed to parse has no
    reliable group name to attribute instead). No ``account`` — this group
    never got far enough to declare one.
    """
    return {"ok": False, "group": None, "reason": detail}


def _enumerate_live_sessions_pool(
    scope: Path | None,
    *,
    env: dict[str, str] | None,
    groups: list[dict],
) -> tuple[list, list[dict], int]:
    """Enumerate live sessions once per (harness, credential store) candidate.

    A reference addresses a SESSION, not a group, and a session can be running
    under any account any configured group declares — not only the account the
    invoking group happens to bind. So this asks every candidate in
    :func:`_addressable_harnesses`'s pool, scoped by *scope*, EACH UNDER ITS
    OWN store's environment (``store.env``, never the caller's ambient one),
    and merges what comes back. This is what makes a session launched under a
    non-default credential store visible from a shell bound to the default
    one.

    Returns ``(records, failures, stores_total)``:

    * ``records`` — every live :class:`~trailhead.harness.base.SessionRecord`
      across every store that answered, in pool order.
    * ``failures`` — one ``{"account": ..., "reason": ...}`` entry per store
      whose enumeration could not be completed: an exception, a non-zero
      exit, a missing enumeration concept, or the per-call timeout expiring
      (:data:`camp.launch.session._ENUMERATE_TIMEOUT_SECONDS` bounds each
      store in turn, so a hanging store degrades exactly like a failing one
      rather than blocking the others). Never a silent omission — a store
      that could not be read always contributes exactly one entry here.
    * ``stores_total`` — how many candidates were queried. ``0`` means the
      pool itself was empty (no configured group's harness could be named at
      all); the caller's degrade for THAT case is unchanged from before this
      merge existed. A caller distinguishes "every store failed" from "no
      store was addressable" by comparing ``len(failures)`` to
      ``stores_total``, not by ``stores_total`` alone.

    Each store is asked exactly once — the pool is already deduplicated by
    (harness, account) in :func:`_addressable_harnesses`, and this walks it
    once, so no store is ever enumerated twice.

    *groups* is supplied by the caller rather than loaded here, and is the SAME
    list the caller attributes the returned records against — one load per
    invocation, so the pool and the attribution can never be built from two
    different readings of the config directory. It also lets a caller pass a
    list that has already degraded (the `--all-groups` seam skips an unparsable
    sibling BY NAME before calling), which a load performed in here would have
    no way to report.
    """
    from ..launch.session import enumerate_records

    resolved_env = dict(env) if env is not None else dict(os.environ)
    stores = _addressable_harnesses(groups, env=resolved_env)

    records: list = []
    failures: list[dict] = []
    for store in stores:
        try:
            rows = enumerate_records(store, scope, store.env)
        except Exception:  # noqa: BLE001 — a store camp cannot read degrades, not fails
            rows = None
        if rows is None:
            failures.append(
                {
                    "account": store.account,
                    "reason": "sessions could not be enumerated for this credential store",
                }
            )
            continue
        records.extend(rows)
    return records, failures, len(stores)


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
    harnesses = _addressable_harnesses(groups, env=resolved_env)
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
        store_env = getattr(harness, "env", resolved_env)
        try:
            rows = harness.session_transcripts(scope, env=store_env)
        except Exception:  # noqa: BLE001 — a harness camp cannot read contributes nothing
            rows = None
        if rows is None:
            continue
        answered.append(harness)
        transcripts.extend(rows)
        try:
            records = enumerate_records(harness, scope, store_env)
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


def refuse_sessions_local_only_options(rest: list[str], *, widening_flag: str) -> None:
    """Refuse the five `camp sessions` options that narrow or reshape the
    LOCAL question — meaningless once *widening_flag* (``--host`` or
    ``--all-hosts``) has widened the machine axis to a remote or merged
    answer: ``--recoverable``, ``--all``, ``--dir``, ``--limit``, and a
    positional workspace slug.

    Shared by `_cmd_sessions_host_cli` (``--host``) and the `-a`/
    ``--all-hosts`` wiring in `cli/dispatch.py`, so both widening forms
    refuse alike rather than one silently dropping what the other refuses.
    """
    from ..spine import _die

    if "--recoverable" in rest:
        _die(
            f"camp sessions: --recoverable has no meaning with {widening_flag} "
            "— a widened machine is always asked for its own live sessions"
        )
    if "--all" in rest:
        _die(
            "camp sessions: --all only widens --recoverable, which has no "
            f"meaning with {widening_flag}"
        )
    if "--dir" in rest or any(a.startswith("--dir=") for a in rest):
        _die(
            f"camp sessions: --dir has no meaning with {widening_flag} — a "
            "widened machine answers for every one of its own groups, not a "
            "local directory"
        )
    if "--limit" in rest or any(a.startswith("--limit=") for a in rest):
        _die(
            "camp sessions: --limit only widens --recoverable, which has no "
            f"meaning with {widening_flag}"
        )

    # `--json` and (on the `-a`/`--all-hosts` path only — `--host` never
    # sees one, refused together with `--group` upstream) a `--group
    # <name>` pair are the widened form's own recognized options, not a
    # leftover positional. Stripped before the catch-all below so neither
    # is mistaken for a workspace slug.
    positional = []
    skip_next = False
    for arg in rest:
        if skip_next:
            skip_next = False
            continue
        if arg == "--group":
            skip_next = True
            continue
        if arg == "--json" or arg.startswith("--group="):
            continue
        positional.append(arg)

    if positional:
        _die(
            f"camp sessions: {widening_flag} widens the machine axis — a "
            f"workspace slug ({positional[0]!r}) has no meaning alongside it"
        )


def _cmd_sessions_host_cli(
    args: list[str], host: "Host", host_name: str, *, connect_timeout: float | None = None
) -> None:
    """camp sessions --host <name> [--json] — every group's live sessions on
    one declared remote machine, relayed through the SSH transport.

    ``connect_timeout`` is the operator's resolved value
    (`camp.host.config.connect_timeout_seconds()`, read once by `main()`'s
    ``--host`` handling and passed down); ``None`` (a direct call with no
    caller-supplied value) falls back to the transport's own documented
    default.

    Reached ONLY from ``cli/dispatch.py``'s ``--host`` handling in
    ``_dispatch_host_command``, after the name has resolved to a declared
    `Host` — the same shape ``_cmd_ls_host_cli`` is reached in for `list`.
    The far side is ALWAYS invoked with the all-groups + ``--json`` form, the
    same shape `_cmd_ls_host_cli` uses, so a remote answer always spans that
    machine's groups.

    Every other existing `sessions` option (``--group`` is already refused
    together with ``--host`` upstream in `main()`; ``--recoverable``,
    ``--all``, ``--dir``, ``--limit``, and a positional workspace slug are
    refused HERE) narrows or reshapes the LOCAL question in a way that has no
    meaning for "every group on that host" — narrowing to one directory, one
    workspace, or the recoverable/dead listing all assume a single machine's
    own state. Refusing rather than silently dropping them mirrors the
    ``--all-groups``/``--group`` and ``--host``/``--group`` refusals already
    beside this one.

    Delegates everything downstream of "what argv to send" and "how to print
    an ok row" to :func:`camp.host.relay.relay_all_groups` — the shared seam
    every `--host` verb dispatches through. `_render_human_rows` below
    renders each row through :func:`render_session_row_human`, the shared
    per-row renderer, and never through the local `--all-groups` listing:
    that one sorts its rows by group (see `_cmd_sessions_group_cli`'s
    `all_groups` branch), and re-sorting is exactly what the design requires
    a relayed answer never do.
    """
    from ..host.relay import relay_all_groups
    from ..host.transport import DEFAULT_CONNECT_TIMEOUT_SECONDS

    if connect_timeout is None:
        connect_timeout = DEFAULT_CONNECT_TIMEOUT_SECONDS

    rest = list(args)
    as_json = _consume_flag(rest, "--json")

    refuse_sessions_local_only_options(rest, widening_flag="--host")

    def _render_human_rows(rows: list[dict]) -> None:
        for row in rows:
            if not row.get("ok"):
                continue
            # Version skew across the operator's two machines is the
            # expected steady state for this feature, not an edge case — a
            # remote camp of a different version can answer with a row that
            # omits a key this rendering depends on. Degrade that ONE row
            # rather than let it take the whole answer down; the well-formed
            # rows around it still print.
            try:
                rendered = render_session_row_human(row)
            except KeyError as e:
                print(
                    f"camp sessions: host {host_name!r} sent a session row "
                    f"missing {e.args[0]!r} — skipping",
                    file=sys.stderr,
                )
                continue
            print(rendered)

    relay_all_groups(
        "sessions",
        host,
        host_name,
        ["sessions", "--all-groups", "--json"],
        as_json=as_json,
        render_human_rows=_render_human_rows,
        connect_timeout=connect_timeout,
    )


#: Exit code for `camp launch --host` when certainty is unknown
#: (`Certainty.UNKNOWN`) — either the connection completed and the
#: invocation then exceeded its bound without answering, or a local
#: producer feeding a streamed invocation failed after the remote already
#: ran. Distinct from 0 (success) and from every certain-failure exit — the
#: fixed `1` the five locally-classified transport failures reachable here
#: share, or the far side's own exit code when the transport happens to
#: propagate it — so a scripted caller can branch on "check before
#: retrying" without parsing stderr
#: (docs/design/a-session-starts-on-a-named-machine.md, "Added by council
#: review (Critical)").
_LAUNCH_HOST_UNKNOWN_EXIT_CODE = 3


def _cmd_launch_host_cli(
    args: list[str], host: "Host", host_name: str, *, connect_timeout: float | None = None
) -> None:
    """camp launch <slug> --host <name> --group <group> [--json].

    Reached ONLY from `cli/dispatch.py`'s `--host` handling in
    `_dispatch_host_command`, after `--group` has already been required and
    validated present — a state-changing verb never infers it from this
    machine's cwd (see `HOST_FLAG`'s own comment in dispatch.py).

    ``connect_timeout`` is the operator's resolved value
    (`camp.host.config.connect_timeout_seconds()`, read once by `main()`'s
    ``--host`` handling and passed down); ``None`` (a direct call with no
    caller-supplied value) falls back to the transport's own documented
    default.

    Relays through `camp.host.relay.answer_object_for_host` — the single-
    object counterpart to the rows relay `_cmd_sessions_host_cli` and
    `_cmd_ls_host_cli` use — rather than `relay_all_groups`: a launch answers
    with one session or nothing at all, and the rendering below needs its
    own exit-code and stderr-ordering policy the generic rows relay does not
    provide.

    `Certainty.HAPPENED` is necessary but not sufficient here: this peer's
    SSH transport does not propagate the remote command's own exit status
    (see `camp.host.transport`'s module docstring), so a far side that
    refused, crashed, or could not resolve camp still classifies as
    `Answered`/HAPPENED. `answer_object_for_host` already guards this —
    `answer.answer` is `None` whenever the far side's stdout did not decode
    as a JSON object — so THIS is what decides success, never the certainty
    or the raw exit code alone.
    """
    from ..host.relay import Certainty, answer_object_for_host
    from ..host.transport import DEFAULT_CONNECT_TIMEOUT_SECONDS
    from ..spine import _consume_flag_value, _die

    if connect_timeout is None:
        connect_timeout = DEFAULT_CONNECT_TIMEOUT_SECONDS

    rest = list(args)
    as_json = _consume_flag(rest, "--json")
    group = _consume_flag_value(rest, "--group")
    if group is None:
        # Unreachable in practice — dispatch.py's --host handling already
        # requires --group for a state-changing verb before this function is
        # ever reached — but a CLI entry point never trusts a caller-
        # enforced invariant it can cheaply re-check itself.
        _die("camp launch: --host requires an explicit --group")
    if len(rest) != 1:
        _die(
            f"camp launch: --host requires exactly one workspace slug, got "
            f"{len(rest)}"
        )
    slug = rest[0]

    answer = answer_object_for_host(
        "launch",
        host,
        host_name,
        ["launch", slug, "--group", group, "--json"],
        connect_timeout=connect_timeout,
    )

    if answer.answer is not None:
        _report_launch_host_success(answer.answer, host_name=host_name, as_json=as_json)
        sys.exit(0)

    if answer.certainty == Certainty.UNKNOWN:
        # The instruction to check comes FIRST, before any explanation, and
        # reads as an instruction naming the command to run — an operator
        # who reads only this line must still do the right thing (design
        # doc, "State — the connection drops after the launch was sent").
        command = f"camp sessions --host {host_name} --group {group}"
        print(
            f"camp launch: check before retrying — run: {command}",
            file=sys.stderr,
        )
        # UNKNOWN now covers two shapes (`Certainty.UNKNOWN`'s docstring at
        # relay.py's Certainty enum) — a connection that stopped answering,
        # or a local producer that failed after the remote already ran. This
        # line names neither mechanism; `answer.notices` (printed next)
        # already carries the outcome-specific sentence for whichever one it
        # was.
        print(
            f"camp launch: camp does not know whether a session was started "
            f"on host {host_name!r} — the outcome could not be confirmed",
            file=sys.stderr,
        )
        for notice in answer.notices:
            print(notice, file=sys.stderr)
        if as_json:
            print(json.dumps({
                "ok": False,
                "host": host_name,
                "certainty": answer.certainty.value,
                "reason": "outcome could not be confirmed",
            }))
        sys.exit(_LAUNCH_HOST_UNKNOWN_EXIT_CODE)

    # A certain failure: either one of the five locally-classified transport
    # states reachable here (host unreachable, no pinned key, ...) or a
    # far-side refusal relayed in its own words. Either way nothing was
    # started, so the same
    # plain sentence closes the report for both — the far side's own words
    # (when there are any) are relayed exactly as `answer.notices` already
    # carries them, unwrapped, before it.
    # Camp's own sentence leads, and the far side's words follow it.
    #
    # The order is load-bearing, not cosmetic. The first stderr line is what
    # carries the certain/uncertain distinction to an operator who skims, and
    # a line the far side authored cannot carry it: a declared host is trusted
    # to run commands, not to write camp's most consequential sentence. Left
    # in front, a refusal crafted to read like the check-before-retry
    # instruction would send the operator hunting for a session that was never
    # started — the exact confusion that wording exists to prevent.
    #
    # The refusal itself is still relayed in the far side's own words,
    # unwrapped; only the leading position is camp's.
    print(f"camp launch: no session was started on host {host_name!r}", file=sys.stderr)
    for notice in answer.notices:
        print(notice, file=sys.stderr)

    # The far side's own status is passed through where it says something,
    # but the uncertain code is RESERVED: a remote camp that happens to exit
    # with that number would otherwise impersonate camp's own "I do not know",
    # and the one signal a scripted caller can branch on without parsing prose
    # would stop meaning what it says. A refusal is certain — nothing was
    # started and there is nothing to check — so it collapses to the shared
    # certain-failure code instead.
    exit_code = answer.exit_code
    if exit_code == 0 or exit_code == _LAUNCH_HOST_UNKNOWN_EXIT_CODE:
        exit_code = 1
    if as_json:
        reason = answer.notices[-1] if answer.notices else "no session was started"
        print(json.dumps({
            "ok": False,
            "host": host_name,
            "certainty": answer.certainty.value,
            "reason": reason,
        }))
    sys.exit(exit_code)


def _report_launch_host_success(
    answer: dict, *, host_name: str, as_json: bool
) -> None:
    """The success report for `camp launch --host` — the same stdout/stderr
    split as a local launch (`_report_launched`): stdout carries ONLY the
    session id, so `$(camp launch --host ...)` captures the same value
    whichever machine ran it, and everything else — the machine, the
    workspace, the attach handle — goes to stderr.

    The attach hint names `camp attach --host`, not the local `tmux attach`
    a same-machine launch prints: the session lives on `host_name`, not
    here, and the design doc pairs this launch with the attach verb that
    already reaches a named machine's session.
    """
    session_id = answer["session_id"]
    workspace = answer["workspace"]
    print(
        f"camp launch: launched session {session_id} on host {host_name!r} "
        f"in {workspace}\n  attach: camp attach {session_id} --host {host_name}",
        file=sys.stderr,
    )
    if as_json:
        payload = dict(answer)
        payload["host"] = host_name
        payload["certainty"] = "happened"
        print(json.dumps(payload))
        return
    print(session_id)


def _cmd_sessions_group_cli(
    args: list[str],
    group: dict | None,
    env: dict[str, str] | None,
    *,
    all_groups: bool = False,
) -> None:
    """camp sessions [<slug>] [--dir <path>] [--recoverable [--limit <n>|--all]] [--json].

    `all_groups=True` is the `--all-groups`/`-g` seam: reached only from
    ``cli/dispatch.py``'s early handling of that option, with `group=None`
    (there is no single resolved group to narrow by — `--all-groups` and
    `--group` are refused together before this function is ever called). A
    `--dir` scope still works unchanged (it never depended on `group`); with
    no `--dir` and no positional slug it skips the cwd-relative slug
    resolution `group` would otherwise be needed for and leaves `scope` (and
    therefore the narrowing below) at `None` — the whole cross-store pool.

    Under `all_groups=True`, group configs are loaded once through
    :func:`~camp.provision.lifecycle.answerable_groups_or_refuse`, which degrades a
    config camp cannot parse instead of failing the whole answer: one broken
    sibling is skipped BY NAME on stderr while every other group still
    answers, exit 0. Every group unparsable is a refusal (nonzero exit, a
    stated reason) — never an answer that reads as "nothing configured". No
    groups configured at all states that on stderr and answers with an empty
    list, exit 0; it never falls through to the legacy standalone-worktree
    source (that fallback belongs to `spine.main`'s no-group `cmd_ls`, which
    `--all-groups` never reaches).

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

    Always exits 0 with ONE exception each side of the split (plus, under
    `all_groups=True`, every configured group failing to parse — see above).
    This is a question, so "I could not tell" degrades to a stderr notice plus
    an empty list rather than a failure a script has to special-case — but
    malformed input (a `--limit` that cannot mean anything) and a harness that
    keeps no transcripts at all are refusals, because neither has an empty
    listing as its honest answer.

    The LIVE listing's pool spans every configured group's store (see
    :func:`_enumerate_live_sessions_pool`), so a BARE group-named query — no
    `--dir`, no resolved workspace slug, i.e. `scope is None` — narrows that
    cross-group answer to *group*'s own rows via :func:`_sessions_for_group`,
    applied to every store's records ALREADY attributed by
    :func:`_attribute_session`. A `--dir` or slug scope has already narrowed
    the pool by PATH before this point (`_enumerate_live_sessions_pool` is
    itself called with that scope), so this filter is not applied again on
    top of it: a `--dir` scope answers about a directory, not a group, and is
    not eligibility-gated by group membership either (see above) — narrowing
    it further by group name would silently drop a session `--dir` was asked
    to describe. A store-failure row carries no `cwd` and so no group; it is
    never filtered by group name and is always included, because a group-named
    answer must still tell the operator a store failed rather than silently
    reading as complete.
    """
    from ..group.manifest import workspace_dir
    from ..spine import _consume_flag_value, _die
    from .dispatch import _slug_from_args_or_cwd

    rest = list(args)
    # Already resolved upstream to select `group`, so consuming it here is
    # just removing it from `rest` — the resolved name (`group["group"]["name"]`)
    # is what the live listing filters by below, not this raw flag value.
    _consume_flag_value(rest, "--group")
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
    elif all_groups:
        # A leftover positional here is a workspace slug --all-groups never
        # consumes (unlike the narrow path just below) — the same
        # narrow-vs-widen contradiction `cli/dispatch.py` refuses for
        # `--group` alongside `--all-groups`, refused here before a group is
        # loaded or a store is read (both happen further down this function).
        if rest:
            _die(
                "camp sessions: --all-groups and a workspace slug name every "
                "group and one workspace at once — pass one or the other"
            )
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

    # NOT read on the `recoverable` path above (it returns before this
    # point): `_list_recoverable` answers from its own strict
    # `load_all_groups` rather than this degraded loader, so running this
    # here for `--recoverable` would print a "— skipping" notice promising
    # every other group still answers, immediately followed by
    # `_list_recoverable` refusing outright on that very same broken
    # config — a promise and its own contradiction in the same invocation.
    def _described() -> str:
        if slug:
            return f"workspace {slug!r}"
        if directory is not None:
            return f"directory {str(scope)!r}"
        if all_groups:
            return "every configured group"
        return f"group {group['group']['name']!r}"

    rows, notices, exit_code = _sessions_live_answer(
        scope, env=env, all_groups=all_groups, group=group, described=_described()
    )
    for notice in notices:
        print(notice, file=sys.stderr)
    if exit_code != 0:
        sys.exit(exit_code)

    if as_json:
        print(json.dumps(rows))
        return

    for row in rows:
        if not row.get("ok", True):
            continue
        print(render_session_row_human(row))


def render_session_row_human(row: dict) -> str:
    """One answered `camp sessions` row, human-rendered — the
    ``session_id  kind  cwd (name)`` line, from a JSON-shaped row rather
    than a `SessionRecord`.

    The one place a local, relayed, or merged `camp sessions` row is turned
    into its human line: `_cmd_sessions_group_cli`, `_cmd_sessions_host_cli`'s
    `--host` callback, and the `-a`/`--all-hosts` merged renderer in
    `cli/dispatch.py` all print through it. Raises `KeyError` on a row
    missing a key it needs, so a caller can degrade that one row.
    """
    from ..launch.recovery import printable_path

    label = f" ({printable_path(row['name'])})" if row.get("name") else ""
    return (
        f"{printable_path(row['session_id'])}  {printable_path(row['kind'])}  "
        f"{printable_path(row['cwd'])}{label}"
    )


def local_sessions_answer(
    group: dict | None, *, all_groups: bool
) -> tuple[list[dict], list[str], int]:
    """The value-returning local answer for `camp sessions`, reused by the
    `-a`/`--all-hosts` wiring in `cli/dispatch.py`. `_sessions_live_answer`
    already never prints or exits, so this is a thin wrapper supplying the
    `scope=None` (the whole cross-store pool, exactly the `--all-groups`
    scope already uses) and the `described` text `_cmd_sessions_group_cli`
    would have computed itself for the same inputs.
    """
    described = (
        "every configured group"
        if all_groups
        else f"group {group['group']['name']!r}"
    )
    return _sessions_live_answer(
        None, env=None, all_groups=all_groups, group=group, described=described
    )


def _sessions_live_answer(
    scope: Path | None,
    *,
    env: dict[str, str] | None,
    all_groups: bool,
    group: dict | None,
    described: str,
) -> tuple[list[dict], list[str], int]:
    """The value-returning half of `camp sessions`' LIVE listing (never the
    `--recoverable` listing, which stays its own answer through
    `_list_recoverable`).

    Enumerates every store's live sessions and returns ``(rows, notices,
    exit_code)`` — never prints, never calls ``sys.exit``.
    `_cmd_sessions_group_cli` is the renderer for BOTH the single-group and
    the `--all-groups` entry points: it calls this, prints each notice to
    stderr in order, prints the rows (`--json` or human), and exits with the
    returned code.

    *rows* is exactly the JSON-shaped payload `--json` already prints today
    — one `_session_payload` dict per live session, then one
    `_store_failure_payload` per credential store that failed to answer,
    then one `_group_config_failure_payload` per unparsable sibling under
    `--all-groups`, in that order.

    *notices* carries every stderr line the printed form emits for this
    listing, in the same order, INCLUDING `_addressable_harnesses`'s own
    per-group credential-store-binding notice. That helper still prints it
    directly by default (its `on_drop=None` posture stays unchanged — its
    other callers, and the shipped unit test double standing in for
    `_enumerate_live_sessions_pool`, both depend on that signature staying
    as-is), so this function captures it with a temporary `sys.stderr`
    redirect around the one call that can reach it, rather than threading a
    new `on_drop` parameter through `_enumerate_live_sessions_pool`.

    *exit_code* is 0 on every path except two refusals, each returned as a
    ``1`` rather than raised: every credential store failed, and (under
    `all_groups=True`) every configured group's TOML failed to parse. The
    group-config refusal is reached through
    :func:`~camp.provision.lifecycle.load_answerable_groups` — the
    value-returning sibling of
    :func:`~camp.provision.lifecycle.answerable_groups_or_refuse`, never that
    helper — with its two notices turned into returned data here, so this
    function never exits the process on that path either.

    *described* is the caller's already-computed `_described()` text — this
    function has no `slug`/`directory` of its own, only the *scope* they
    already resolved to.
    """
    import io
    from contextlib import redirect_stderr

    notices: list[str] = []

    all_groups_configs: list[dict] | None = None
    all_groups_unparsable: list[str] = []
    all_groups_no_groups_configured = False
    if all_groups:
        from ..provision.lifecycle import load_answerable_groups
        from .common import _groups_dir

        all_groups_configs, all_groups_unparsable = load_answerable_groups(_groups_dir())
        for detail in all_groups_unparsable:
            notices.append(f"camp sessions: {detail} — skipping")
        if not all_groups_configs and all_groups_unparsable:
            notices.append(
                "camp sessions: could not answer for any configured group — "
                "every group config failed to parse; fix a config above and re-run"
            )
            return [], notices, 1
        all_groups_no_groups_configured = not all_groups_configs

    # ONE reading of the config directory per invocation, shared by the
    # enumeration pool below and by the attribution that pairs each returned
    # record with a group: two loads could disagree about which groups exist,
    # and a row would then be attributed against a different set than the pool
    # that produced it.
    if all_groups:
        session_groups = all_groups_configs or []
    else:
        from ..group.config import load_all_groups
        from .common import _groups_dir

        session_groups = load_all_groups(_groups_dir())

    already_notified_unanswerable = False
    if all_groups_no_groups_configured:
        notices.append("camp sessions: no groups configured — nothing to answer for")
        records, failures = [], []
    else:
        capture = io.StringIO()
        with redirect_stderr(capture):
            records, failures, stores_total = _enumerate_live_sessions_pool(
                scope, env=env, groups=session_groups
            )
        notices.extend(line for line in capture.getvalue().splitlines() if line)

        if stores_total == 0:
            notices.append(
                f"camp sessions: could not determine the live sessions for {described} — "
                "reporting none"
            )
            records = []
            failures = []
            already_notified_unanswerable = True
        elif failures and len(failures) == stores_total:
            accounts = ", ".join(_account_label(failure["account"]) for failure in failures)
            notices.append(
                f"camp sessions: could not enumerate live sessions for {described} — "
                f"every credential store failed ({accounts}) — check each store's "
                "credentials and re-run"
            )
            return [], notices, 1
        else:
            for failure in failures:
                notices.append(
                    "camp sessions: could not enumerate sessions for "
                    f"{_account_label(failure['account'])}"
                )

    resolved_env = dict(env) if env is not None else dict(os.environ)
    attributed = [
        (record, _attribute_session(record.cwd, session_groups, env=resolved_env))
        for record in records
    ]
    if scope is None:
        if all_groups:
            # Widened rather than narrowed: every store's rows, ordered by
            # group so the merged answer is stable across invocations. A
            # stable sort keeps each group's OWN rows in the enumeration
            # order _enumerate_live_sessions_pool already produced them in.
            attributed = sorted(attributed, key=lambda pair: pair[1]["group"] or "")
        else:
            attributed = _sessions_for_group(attributed, group["group"]["name"])
            if not already_notified_unanswerable:
                from ..launch.profile import StoreBindingError, harness_store_for

                try:
                    group_store_unaddressable = (
                        harness_store_for(group, env=resolved_env) is None
                    )
                except StoreBindingError:
                    group_store_unaddressable = True

                if group_store_unaddressable:
                    # This group's own credential store never entered the
                    # pool at all — distinct from the pool answering with
                    # zero rows for it, which is a legitimate empty listing.
                    # An operator reading silence here as "nothing running"
                    # is exactly the confident-wrong-answer this listing
                    # exists to avoid.
                    notices.append(
                        f"camp sessions: could not determine the live sessions for "
                        f"{described} — reporting none"
                    )

    rows = [_session_payload(record, **attribution) for record, attribution in attributed]
    rows += [_store_failure_payload(failure) for failure in failures]
    rows += [_group_config_failure_payload(detail) for detail in all_groups_unparsable]
    return rows, notices, 0


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
    transcripts, live, answered, accounts = _session_pool(
        groups, verb="kill", env=resolved_env, live_required=True
    )

    outcome = stop_session(
        ref,
        # The ownership check asks a harness which pane commands IT composes, so
        # it needs one harness rather than the pool. Groups sharing a harness
        # but declaring different accounts are now separate pool entries, so
        # this picks the first store that answered — the composed commands are
        # a property of the harness TYPE, not of which account it is bound to,
        # so any answering store's shapes are the right ones to compare against
        # on all but a mixed-harness machine, where a foreign harness's session
        # is REFUSED rather than mis-signalled, the direction this check is
        # supposed to fail in.
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
            accounts=accounts,
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


#: Exit code for `camp kill --host` when the connection completed and the
#: invocation then exceeded its bound without answering (`Certainty.UNKNOWN`).
#: Distinct from 0 (success), from `_AMBIGUOUS_EXIT_CODE` (the reference
#: matched more than one session), and from every certain-failure exit — the
#: fixed `1` the six locally-classified transport failures share, or the far
#: side's own exit code when the transport happens to propagate it — so a
#: scripted caller can branch on "check before retrying" without parsing
#: stderr (docs/design/stopping-a-session-on-a-named-machine.md, "Two
#: reserved exit codes, not one"). Both this and `_AMBIGUOUS_EXIT_CODE` are
#: RESERVED against pass-through: a remote status landing on either
#: collapses to 1 rather than being relayed unchanged.
_KILL_HOST_UNKNOWN_EXIT_CODE = 3


def _cmd_kill_host_cli(
    args: list[str], host: "Host", host_name: str, *, connect_timeout: float | None = None
) -> None:
    """camp kill <ref> --host <name> [--json].

    Reached ONLY from `cli/dispatch.py`'s `--host` handling for `kill`.
    Fully groupless, exactly like the local `_cmd_kill_cli`: the reference
    names the session and the session names everything else, so `--host`
    carries the reference and nothing else.

    ``connect_timeout`` is the operator's resolved value
    (`camp.host.config.connect_timeout_seconds()`, read once by `main()`'s
    ``--host`` handling and passed down); ``None`` (a direct call with no
    caller-supplied value) falls back to the transport's own documented
    default.

    Relays through `camp.host.relay.answer_payload_for_host`, the payload
    reader that accepts either shape a stop can answer with: one object
    (stopped, or already down), or the candidate rows an ambiguous
    reference produces — the same two shapes `camp kill` answers with
    locally, now carried over the wire.

    `Certainty.HAPPENED` is necessary but not sufficient here: this peer's
    SSH transport does not propagate the remote command's own exit status
    (see `camp.host.transport`'s module docstring), so a far side that
    refused, crashed, or could not resolve camp still classifies as
    `Answered`/HAPPENED. `answer.obj` / `answer.rows` being `None` is what
    decides "nothing relayable came back" — never the certainty or the raw
    exit code alone. Exit status is likewise decided from the payload's own
    shape (`obj` vs `rows` vs neither) and never from `answer.exit_code`,
    which the far side controls and does not even reliably reach this side.
    """
    from ..host.relay import Certainty, answer_payload_for_host
    from ..host.transport import DEFAULT_CONNECT_TIMEOUT_SECONDS
    from ..spine import _die

    if connect_timeout is None:
        connect_timeout = DEFAULT_CONNECT_TIMEOUT_SECONDS

    rest = list(args)
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

    answer = answer_payload_for_host(
        "kill", host, host_name, ["kill", ref, "--json"], connect_timeout=connect_timeout,
    )

    obj = answer.obj
    # `obj is not None` alone is not sufficient: a differently-versioned
    # remote, or an error object like `{"ok": false, "reason": "..."}`, is
    # a JSON object too. Only an object carrying the fields the success
    # report actually needs — a session id, and an outcome camp recognises
    # — is a stop's success answer; anything else falls through to the same
    # certain-failure path an unparsable answer takes, exactly as the
    # empty-rows case does above.
    if (
        obj is not None
        and obj.get("session_id") is not None
        and obj.get("outcome") in ("stopped", "already-down")
    ):
        from ..launch.recovery import printable_path

        session_id = obj.get("session_id")
        tmux_name = obj.get("tmux_name")
        outcome = obj.get("outcome")
        printable_session_id = printable_path(session_id)
        printable_tmux_name = printable_path(tmux_name)
        if outcome == "already-down":
            print(
                f"camp kill: session {printable_session_id} ({printable_tmux_name}) "
                f"on host {host_name!r} was already down — nothing to stop",
                file=sys.stderr,
            )
        else:
            print(
                f"camp kill: stopped session {printable_session_id} "
                f"({printable_tmux_name}) on host {host_name!r} — its memory is "
                f"reclaimed; `camp launch --resume {printable_session_id} --host "
                f"{host_name}` brings it back under this same reference",
                file=sys.stderr,
            )
        for notice in answer.notices:
            print(notice, file=sys.stderr)
        if as_json:
            payload = dict(answer.obj)
            payload["host"] = host_name
            print(json.dumps(payload))
        else:
            print(printable_session_id)
        sys.exit(0)

    if answer.rows:
        # The rows ARE the answer to what was asked, so — mirroring
        # `_print_candidates` — they print on stdout before camp's own line,
        # never suppressed by a non-zero exit. Human mode gets the same
        # per-candidate rendering `_print_candidates` gives a local
        # ambiguity; `--json` relays the far side's own row shape verbatim.
        if as_json:
            print(json.dumps(answer.rows))
        else:
            for row in answer.rows:
                try:
                    rendered = _candidate_row_line(row)
                except KeyError as e:
                    print(
                        f"camp kill: host {host_name!r} sent a candidate row "
                        f"missing {e.args[0]!r} — skipping",
                        file=sys.stderr,
                    )
                    continue
                print(rendered)
        print(
            f"camp kill: {ref!r} matched more than one session on host "
            f"{host_name!r} — re-run with a longer prefix naming exactly one",
            file=sys.stderr,
        )
        for notice in answer.notices:
            print(notice, file=sys.stderr)
        sys.exit(_AMBIGUOUS_EXIT_CODE)

    if answer.certainty == Certainty.UNKNOWN:
        # The instruction to check comes FIRST, before any explanation, and
        # reads as an instruction naming the command to run. The fallback —
        # reach the machine directly — follows it and precedes the
        # explanation: the machine the check would ask is, by construction,
        # the one that just stopped answering, so a report naming only the
        # check hands an operator a loop (design doc, "A retry is the
        # dangerous move" / "State — the connection drops after the stop
        # was sent").
        command = f"camp sessions --host {host_name}"
        print(
            f"camp kill: check before retrying — run: {command}",
            file=sys.stderr,
        )
        print(
            f"camp kill: if that check cannot answer either, reach host "
            f"{host_name!r} directly and look",
            file=sys.stderr,
        )
        print(
            f"camp kill: camp does not know whether session {ref!r} was stopped "
            f"on host {host_name!r} — the connection stopped answering before "
            "the far side reported back",
            file=sys.stderr,
        )
        for notice in answer.notices:
            print(notice, file=sys.stderr)
        if as_json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "host": host_name,
                        "certainty": answer.certainty.value,
                        "reason": "connection stopped answering before the far "
                        "side reported back",
                    }
                )
            )
        sys.exit(_KILL_HOST_UNKNOWN_EXIT_CODE)

    # A certain failure: either one of the six locally-classified transport
    # states (host unreachable, no pinned key, ...), a far-side refusal
    # relayed in its own words, or an answer camp could not parse. Either
    # way nothing was stopped, and there is nothing to go check — camp's own
    # sentence leads, unconditionally before the far side's relayed words.
    print(f"camp kill: no session was stopped on host {host_name!r}", file=sys.stderr)
    for notice in answer.notices:
        print(notice, file=sys.stderr)

    if as_json:
        reason = answer.notices[-1] if answer.notices else "no session was stopped"
        print(
            json.dumps(
                {
                    "ok": False,
                    "host": host_name,
                    "certainty": answer.certainty.value,
                    "reason": reason,
                }
            )
        )

    # Every certain failure is 1, full stop — never a pass-through of the
    # remote's own exit code. The status is decided here, from the payload
    # camp actually parsed: unreachable, unpinned or changed key, refused
    # credentials, camp not resolvable there, a far-side refusal in its own
    # words, or an answer camp could not parse are all the same outcome to a
    # scripted caller. Passing an arbitrary remote code through would also
    # let a remote camp that happens to exit 2 or 3 for its own reasons
    # impersonate camp's own reserved "ambiguous" or "unknown" signal.
    sys.exit(1)


# ---------------------------------------------------------------------------
# camp attach — hand the operator's terminal to a running session
#
# Fully groupless, exactly like `camp kill`: the reference names the session,
# so the local forms below never resolve a group. Two forms never reach these
# functions at all — `<ref> --host <name>` (`_cmd_attach_host_cli`) and
# `-a` in either shape (`camp.cli.dispatch._dispatch_attach_all_hosts`) — both
# are dispatched before `camp.spine`'s fallback is ever reached, per
# `docs/design/attaching-reaches-a-running-session-on-any-machine.md`.
# ---------------------------------------------------------------------------


def _attach_session_context(
    env: dict[str, str],
) -> tuple[list[dict], list, list, "object", "object", str | None, dict[str, str | None]]:
    """The addressable pool, the ownership seam, this machine's own name, and
    the accounts each candidate came from — shared setup for every LOCAL
    `camp attach` form (bare, `<ref>`, `--resolve --json`, `--list --json`).
    Never used by the `--host` pass-through, which resolves nothing locally
    by design.
    """
    from ..host.config import HostConfigError, self_host_name
    from ..launch.stop import Tmux
    from ..spine import _die

    groups = _parsable_groups()
    # `self_host_name` is a local hosts.toml read with no session pool
    # involvement, so a malformed `self_name` must refuse here, before the
    # pool ever asks a harness to enumerate live sessions — otherwise a
    # config mistake surfaces as a harness-probe failure instead of camp's
    # own, more actionable refusal naming `self_name`.
    try:
        machine = self_host_name(env)
    except HostConfigError as exc:
        _die(f"camp attach: {exc}")
    transcripts, live, answered, accounts = _session_pool(
        groups, verb="attach", env=env, live_required=True
    )
    harness = answered[0]
    tmux = Tmux()
    return groups, transcripts, live, harness, tmux, machine, accounts


def _attach_resolve_payload(resolution) -> dict:
    """`resolve_attach_ref`'s answer as JSON — the wire shape `--resolve --json`
    prints and `camp attach -a` (ref form) parses back. Not a public API: its
    sole consumer is camp's own `-a` probe, per the design doc's own framing
    of `--resolve` as "reachable only through a flag that did not exist
    before … its only consumer is `camp attach -a` itself"."""
    from ..attach.resolve import Ambiguous, NoMatch, NotRunning, Resolved

    if isinstance(resolution, Resolved):
        candidate = resolution.candidate
        return {
            "ok": True,
            "session_id": candidate.session_id,
            "derived_name": candidate.derived_name,
        }
    if isinstance(resolution, NotRunning):
        return {"ok": False, "state": "not_running", "session_id": resolution.candidate.session_id}
    if isinstance(resolution, Ambiguous):
        return {
            "ok": False,
            "state": "ambiguous",
            "candidates": [_candidate_payload(c) for c in resolution.candidates],
        }
    assert isinstance(resolution, NoMatch)
    return {"ok": False, "state": "no_match"}


def _attach_pool_payload(pool) -> dict:
    """A local picker pool as JSON — the wire shape `--list --json` prints and
    the cross-host bare picker (`camp attach -a`, no reference) parses back.
    Also internal-only, the bare-picker sibling of `_attach_resolve_payload`.
    """
    from ..attach.picker import PoolReady

    if isinstance(pool, PoolReady):
        return {
            "ok": True,
            "rows": [
                {
                    "session_id": row.candidate.session_id,
                    "derived_name": row.candidate.derived_name,
                    "group": row.group,
                    "slug": row.slug,
                }
                for row in pool.rows
            ],
        }
    return {"ok": False, "reason": pool.reason}


def _cmd_attach_cli(args: list[str], env: dict[str, str] | None = None) -> None:
    """camp attach [<ref>] [--resolve --json] [--list --json].

    The three forms this function itself answers — the numbered picker with
    no reference, `camp attach <ref>`, and the machine-readable `<ref>
    --resolve --json` probe sub-mode — all resolve against THIS machine's own
    pool. `--host` and `-a` are intercepted earlier, in `cli/dispatch.py`,
    and never reach this function (see the module-section comment above).

    Posture matches `camp kill`: every refusal is one `camp attach: …` line on
    stderr, empty stdout, non-zero exit — except an ambiguous reference, which
    prints its candidates on stdout and exits 2, the same convention
    `_die_unresolved` documents for every ref-addressed verb.
    """
    from ..attach.picker import NothingToOffer, Picked, PoolUnreadable, local_pool, pick_session
    from ..attach.prefix_warning import warn_if_nested
    from ..attach.resolve import Ambiguous, NoMatch, NotRunning, Resolved, resolve_attach_ref
    from ..host.handoff import handoff, local_argv
    from ..spine import _consume_flag_value, _die

    rest = list(args)
    _consume_flag_value(rest, "--group")  # a ref names the session; no group needed
    # `--json` is accepted on the plain `<ref>` form too (no `--resolve`/
    # `--list`) — deliberately, not an oversight. It changes nothing about
    # the success path (a handoff has no JSON shape to offer), and its only
    # effect there is on `_die_unresolved`'s ambiguity listing, which already
    # supports both a human and a machine-readable rendering for every
    # ref-addressed verb. Refusing it here would make attach the one verb
    # that treats a harmless, already-supported flag as an error.
    as_json = _consume_flag(rest, "--json")
    resolve_only = _consume_flag(rest, "--resolve")
    list_only = _consume_flag(rest, "--list")

    if len(rest) > 1:
        _die(
            f"camp attach: one session reference, not {len(rest)} — an attach "
            "addresses exactly one session"
        )
    ref = rest[0] if rest else None
    if ref is not None and not ref.strip():
        _die("camp attach: requires a session reference")
    if ref is not None and ref.startswith("-"):
        _die(
            f"camp attach: {ref!r} looks like a flag, not a session reference — "
            "a reference may not start with a dash"
        )
    if resolve_only and ref is None:
        _die("camp attach: --resolve requires a session reference")
    if list_only and ref is not None:
        _die("camp attach: --list takes no session reference")
    if (resolve_only or list_only) and not as_json:
        _die("camp attach: --resolve and --list are machine-readable only — pass --json")

    resolved_env = dict(env) if env is not None else dict(os.environ)
    groups, transcripts, live, harness, tmux, machine, accounts = _attach_session_context(
        resolved_env
    )

    if list_only or ref is None:
        # The two reference-less forms read the identical pool: `--list --json`
        # dumps it for the cross-host picker to merge, the bare form presents
        # it. Read once, here, rather than twice below.
        pool = local_pool(
            harness=harness,
            tmux=tmux,
            transcripts=transcripts,
            live_records=live,
            groups=groups,
            env=resolved_env,
            machine=machine,
        )
        if list_only:
            print(json.dumps(_attach_pool_payload(pool)))
            sys.exit(0)

        result = pick_session(
            pool,
            stdin=sys.stdin,
            stdout=sys.stdout,
            isatty=sys.stdin.isatty() and sys.stdout.isatty(),
        )
        if isinstance(result, PoolUnreadable):
            _die(f"camp attach: {result.reason}")
        if isinstance(result, NothingToOffer):
            _die("camp attach: no running session found on this machine")
        assert isinstance(result, Picked)
        warn_if_nested(resolved_env)
        handoff(local_argv(result.row.candidate.derived_name))
        return

    resolution = resolve_attach_ref(
        ref,
        harness=harness,
        tmux=tmux,
        transcripts=transcripts,
        live_records=live,
        groups=groups,
        env=resolved_env,
    )

    if resolve_only:
        print(json.dumps(_attach_resolve_payload(resolution)))
        sys.exit(0)

    if isinstance(resolution, NotRunning):
        _die(
            f"camp attach: session {resolution.candidate.session_id} is not "
            f"running — bring it back with `camp launch --resume {ref}`"
        )
    if isinstance(resolution, Ambiguous):
        from ..launch.recovery import Ambiguous as _RecoveryAmbiguous

        # attach's own Ambiguous (`camp.attach.resolve`) is never the SAME
        # class `_die_unresolved` checks (`camp.launch.recovery`'s) —
        # translated here so every ref-addressed verb refuses through that one
        # shared helper and an operator gets the identical wording (including
        # which account a cross-store ambiguity matched in) regardless of
        # which verb they typed.
        _die_unresolved(
            _RecoveryAmbiguous(candidates=resolution.candidates),
            ref,
            verb="attach",
            harness=harness,
            env=resolved_env,
            as_json=as_json,
            accounts=accounts,
        )
    if isinstance(resolution, NoMatch):
        # NOT routed through `_die_unresolved`: that helper's populated-pool
        # wording points at `camp sessions --recoverable` — a listing of
        # exactly the stopped sessions attach refuses to touch — which is
        # the wrong next step for a verb that only ever offers live,
        # camp-owned sessions. Attach names the pool IT searched instead
        # (`## State — the named session was not found`), while keeping the
        # empty-vs-populated-pool split `_die_unresolved` itself draws.
        if resolution.pool_size:
            _die(f"camp attach: no session on this machine matches {ref!r}")
        _die(
            f"camp attach: harness {_harness_display_name(harness)} reports no "
            f"sessions at all — {_retention_hint(harness, resolved_env)}"
        )
    assert isinstance(resolution, Resolved)
    warn_if_nested(resolved_env)
    handoff(local_argv(resolution.candidate.derived_name))


def _cmd_attach_host_cli(
    args: list[str],
    host: "Host",
    host_name: str,
    env: dict[str, str] | None = None,
    *,
    connect_timeout: float | None = None,
) -> None:
    """camp attach <ref> --host <name> — carry the reference across untouched.

    Resolves nothing locally: the far side's own `camp attach <ref>` decides
    and refuses in its own words (`docs/design/attaching-reaches-a-running-
    session-on-any-machine.md`, "Resolution is not the same question on each
    axis"). The nested-multiplexer warning is still decided from THIS
    machine's own environment before the handoff — it is a property of the
    local terminal, not of the target — per that same design doc's "The
    key-prefix conflict warning is decided locally".

    ``connect_timeout`` is the operator's resolved value
    (`camp.host.config.connect_timeout_seconds()`, read once by `main()`'s
    `--host` handling), threaded to the interactive ssh handoff exactly as
    it is to every other `--host` verb — `None` falls back to the
    transport's own documented default, for a caller with no resolved value
    in hand.
    """
    from ..attach.prefix_warning import warn_if_nested
    from ..host.handoff import handoff, remote_argv
    from ..host.transport import DEFAULT_CONNECT_TIMEOUT_SECONDS
    from ..spine import _die

    if connect_timeout is None:
        connect_timeout = DEFAULT_CONNECT_TIMEOUT_SECONDS

    rest = list(args)
    if len(rest) != 1:
        _die(
            f"camp attach: --host requires exactly one session reference, got "
            f"{len(rest)}"
        )
    ref = rest[0]
    if not ref.strip() or ref.startswith("-"):
        _die(f"camp attach: {ref!r} is not a valid session reference")

    resolved_env = dict(env) if env is not None else dict(os.environ)
    warn_if_nested(resolved_env)
    handoff(remote_argv(host, ref, connect_timeout=connect_timeout))
