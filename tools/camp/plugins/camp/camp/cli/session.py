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

The RESUME flavor adds one more CLI job: turning an operator's session reference
into exactly one addressable session, or refusing. The resolution itself is pure
and lives in ``camp.launch.recovery``; everything the operator SEES about it —
the candidate rows, the exit codes, the wording of each refusal — is here,
because a question answered on a terminal cannot also be answered identically
from a test or a listing.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

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


def _consume_json_flag(args: list[str]) -> bool:
    """Remove every ``--json`` from *args* in place, reporting whether one was there.

    Removal matters as much as detection: the remaining args are what slug
    resolution reads positionally, and a leftover ``--json`` sitting at args[0]
    would be resolved as a slug.
    """
    present = "--json" in args
    while "--json" in args:
        args.remove("--json")
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
):
    """Spawn a session — by workspace *slug* or at a named *root* — and confirm it.

    The addressing arguments are the engine's own, forwarded whole: exactly one of
    *slug* or the (*root*, *name_component*, *trust_scope*) triple, which the
    engine enforces. *resume_session_id* rides on either, re-entering a session
    the harness already holds. Everything after the spawn is identical for all
    three flavors, so they report the same three stderr lines and return the same
    :class:`LaunchedSession`.

    Raises :class:`LaunchError` on refusal — including a spawn that never
    confirmed, which the engine has already killed.
    """
    from ..bookmark import harness_for
    from ..launch.session import confirm_session, launch_session

    launched = launch_session(
        group,
        slug,
        env=env,
        root=root,
        name_component=name_component,
        trust_scope=trust_scope,
        resume_session_id=resume_session_id,
    )
    print(
        f"camp launch: launched session {launched.session_id} in {launched.launch_dir}\n"
        f"  attach: tmux attach -t {launched.tmux_name}",
        file=sys.stderr,
    )
    confirm_session(harness_for(group), launched, env=env)
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


def _candidate_payload(candidate) -> dict:
    """One resolver candidate as JSON-ready data.

    This key set is the candidate ROW SHAPE, shared by every surface that lists
    candidates, so a consumer that learned it from one listing reads the other
    unchanged. ``root`` and ``age_seconds`` are ``null`` rather than absent when
    the harness could not tell camp them: a missing key and a known-absent value
    are different facts, and only the second is answerable.
    """
    return {
        "session_id": candidate.session_id,
        "tmux_name": candidate.derived_name,
        "root": str(candidate.root) if candidate.root is not None else None,
        "age_seconds": candidate.age_seconds,
        "root_missing": candidate.root_missing,
        "unreadable": candidate.unreadable,
    }


def _candidate_line(candidate) -> str:
    """One candidate as an operator-facing row: name, id, where, how old.

    The three things that distinguish two candidates the same ref matched — a
    directory, its state, and an age — are all here, because the operator picks
    between them by reading this line and nothing else.
    """
    if candidate.root is None:
        where = "directory unknown"
    elif candidate.root_missing:
        where = f"{candidate.root} (gone)"
    else:
        where = str(candidate.root)
    age = f"{int(candidate.age_seconds)}s" if candidate.age_seconds is not None else "live"
    return f"{candidate.derived_name}  {candidate.session_id}  {where}  {age}"


def _print_candidates(candidates, *, as_json: bool) -> None:
    """Print candidate rows on STDOUT.

    On stdout even when the exit code is non-zero: the rows ARE the answer to
    what was asked, and a caller capturing stdout must get them. The one-line
    explanation of why camp stopped goes to stderr alongside, keeping the split
    every other camp verb uses.
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
    days = harness.session_retention_days(env=env)
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
    from ..bookmark import harness_for

    found: dict[str, object] = {}
    for config in groups or [{}]:
        harness = harness_for(config)
        if harness is None:
            continue
        found.setdefault(_harness_display_name(harness), harness)
    return list(found.values())


def _resolve_session_reference(ref: str, *, env: dict[str, str], as_json: bool):
    """Resolve *ref* to one addressable session; return it with the group configs.

    Both halves come back because the caller needs both, and loading the configs
    a second time would let the name rule's two applications drift apart.

    The pool is every addressable harness's on-disk transcripts UNION its live
    sessions. A harness with no transcript concept contributes no transcripts,
    and if NONE of them has one the reference is unanswerable and camp refuses
    naming them: an unanswerable seam is a refusal, never a permissive default.
    The live probe is the opposite posture — it only ever ADDS candidates, so a
    probe that fails narrows what camp can offer without being able to make camp
    resume the wrong thing.

    Three outcomes end in a refusal, and the wording of each is the whole point:

    * MORE THAN ONE match prints the candidates and exits
      :data:`_AMBIGUOUS_EXIT_CODE`, never guessing.
    * NO match against a populated pool is a ref problem, and points at the
      listing that shows what the refs are.
    * NO match against an EMPTY pool is not a ref problem at all, and says so —
      naming the harness's retention window instead of implying the operator
      mistyped something.
    """
    from ..group.config import load_all_groups
    from ..launch.recovery import Ambiguous, NoMatch, Resolved, resolve_session_ref
    from ..launch.session import enumerate_records
    from ..spine import _die
    from .common import _groups_dir

    groups = load_all_groups(_groups_dir())
    harnesses = _addressable_harnesses(groups)
    if not harnesses:
        _die(
            "camp launch: camp cannot name a harness for any configured group, so "
            "it cannot look up the session this reference addresses"
        )

    transcripts: list = []
    live: list = []
    answered: list = []
    for harness in harnesses:
        try:
            live.extend(enumerate_records(harness, None, env) or [])
        except Exception:  # noqa: BLE001 — a failed live probe narrows the pool, never fails the command
            pass
        rows = harness.session_transcripts(env=env)
        if rows is not None:
            answered.append(harness)
            transcripts.extend(rows)

    if not answered:
        names = ", ".join(_harness_display_name(harness) for harness in harnesses)
        _die(
            f"camp launch: harness {names} keeps no session transcripts camp can "
            "read, so its sessions cannot be addressed by reference"
        )

    outcome = resolve_session_ref(
        ref, transcripts=transcripts, live_records=live, groups=groups, env=env
    )

    if isinstance(outcome, Resolved):
        return outcome.candidate, groups

    if isinstance(outcome, Ambiguous):
        _print_candidates(outcome.candidates, as_json=as_json)
        _die(
            f"camp launch: {ref!r} matches {len(outcome.candidates)} sessions "
            "(listed above) — re-run with a longer prefix naming exactly one",
            code=_AMBIGUOUS_EXIT_CODE,
        )

    if isinstance(outcome, NoMatch) and outcome.pool_size:
        _die(
            f"camp launch: no candidate matched `{ref}`; run "
            "`camp sessions --recoverable` to see what camp can address"
        )
    _die(
        f"camp launch: harness {_harness_display_name(answered[0])} reports no "
        f"sessions at all — {_retention_hint(answered[0], env)}"
    )


def _workspace_owner(root: Path, slug: str, groups, *, env: dict[str, str]) -> dict | None:
    """The group whose workspace *slug* holds *root*, or ``None`` for anywhere else.

    The one question the resume flavor asks beyond the name rule. A session rooted
    in a camp workspace belongs to the group camp provisioned that workspace for —
    not to whichever group the operator happens to be standing in — so the answer
    is read off the path, and a resume needs no ``--group`` to find it.

    Built on :func:`workspace_dir`, camp's single source of a workspace's path,
    rather than on a second reading of the layout, so it cannot drift from the
    name rule that produced *slug*. ``None`` means *root* is not a camp workspace
    at all, which is exactly the case the eligibility gate exists to fence.
    """
    from ..group.manifest import workspace_dir
    from ..group.resolve import GroupConfinementError

    for config in groups:
        name = (config.get("group") or {}).get("name")
        if not name:
            continue
        try:
            workspace = workspace_dir(name, slug, env=env).resolve()
        except (GroupConfinementError, OSError):
            continue
        if root == workspace or workspace in root.parents:
            return config
    return None


def _report_launched(launched, *, as_json: bool) -> None:
    """The success report, identical for every launch flavor."""
    if as_json:
        print(
            json.dumps(
                {
                    "workspace": str(launched.launch_dir),
                    "session_id": launched.session_id,
                    "tmux_name": launched.tmux_name,
                }
            )
        )
        return
    print(launched.session_id)


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
    from ..launch.recovery import derive_name_component
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
            f"camp launch: session {candidate.session_id} was started in {root}, "
            "which no longer exists — camp will not recreate a torn-down directory "
            "to resume into it"
        )

    component = derive_name_component(root, groups, env=resolved_env)
    owner = _workspace_owner(root, component, groups, env=resolved_env)

    if owner is None:
        # Anywhere but a camp workspace, the allowlist is the containment
        # boundary — so the group supplying it is named explicitly, exactly as
        # `--dir` requires, and never inferred from where camp was invoked.
        if explicit_group is None:
            _die(
                f"camp launch: session {candidate.session_id} was started in {root}, "
                "which is not a camp workspace — re-run with an explicit --group "
                "<name> whose [launch] roots allowlist covers it"
            )
        if group is None:
            _die(f"camp launch: no camp group named {explicit_group!r} is configured")

    try:
        if owner is not None:
            # The slug flavor carrying a session id: camp computed this directory
            # from a manifest it wrote, so it is fenced by construction and the
            # eligibility gate has nothing to add.
            launched = launch_and_confirm(
                owner, component, env=env, resume_session_id=candidate.session_id
            )
        else:
            launched = launch_and_confirm(
                group,
                env=env,
                root=root,
                name_component=component,
                trust_scope=root,
                resume_session_id=candidate.session_id,
            )
    except LaunchError as exc:
        _die(_refusal(exc))
        return

    _report_launched(launched, as_json=as_json)


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
    from ..launch.session import LaunchError
    from ..spine import _consume_flag_value, _die
    from .dispatch import _slug_from_args_or_cwd

    rest = list(args)
    explicit_group = _consume_flag_value(rest, "--group")
    resume_ref = _consume_flag_value(rest, RESUME_FLAG)
    directory = _consume_flag_value(rest, "--dir")
    as_json = _consume_json_flag(rest)

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

    if directory is not None:
        if rest:
            _die(
                "camp launch: --dir and a workspace slug are mutually exclusive — a "
                "launch is rooted at a named directory or at a workspace, never both"
            )
        if not directory.strip():
            _die("camp launch: --dir requires a directory path")
        if explicit_group is None:
            _die(
                "camp launch: --dir requires an explicit --group <name> — the "
                "group's [launch] roots allowlist is what fences a directory-rooted "
                "launch, so it must never depend on the directory camp was invoked "
                "from"
            )
        root = Path(directory)
        # The name component comes from the RESOLVED path so that `--dir .` and a
        # trailing slash name the directory the session actually runs in. The trust
        # scope is that same directory: a named root is its own confinement, which
        # is exactly why the eligibility gate — not the trust pre-seed — is the
        # boundary here.
        name_component = root.resolve().name
        trust_scope = root
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
    from ..bookmark import harness_for
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


def _cmd_sessions_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
) -> None:
    """camp sessions [<slug>] [--json] — list the harness sessions camp can see.

    Scope is the workspace when a slug is given or resolves from cwd, and the
    whole harness otherwise (the seam's `workspace=None`). Always exits 0: this is
    a question, and "I could not tell" degrades to a stderr notice plus an empty
    list rather than a failure a script has to special-case.
    """
    from ..group.manifest import workspace_dir
    from ..spine import _consume_flag_value
    from .dispatch import _slug_from_args_or_cwd

    rest = list(args)
    _consume_flag_value(rest, "--group")  # already resolved upstream; drop it
    as_json = _consume_json_flag(rest)

    slug = _slug_from_args_or_cwd(
        rest, group, verb="sessions", consume_positional=True, allow_none=True, env=env
    )
    workspace = None
    if slug:
        workspace = workspace_dir(group["group"]["name"], slug, env=env)
        try:
            # Mirror the launch engine's resolution (`_resolve_launch_dir`): a
            # symlinked workspace dir must scope enumeration by the same
            # resolved path a just-launched session registered under, or a
            # slug-scoped query never finds it.
            workspace = workspace.resolve(strict=True)
        except OSError:
            pass

    records = _enumerate_sessions(group, workspace, env)
    if records is None:
        scope = f"workspace {slug!r}" if slug else f"group {group['group']['name']!r}"
        print(
            f"camp sessions: could not determine the live sessions for {scope} — "
            "reporting none",
            file=sys.stderr,
        )
        records = []

    if as_json:
        print(json.dumps([_session_payload(record) for record in records]))
        return
    for record in records:
        label = f" ({record.name})" if record.name else ""
        print(f"{record.session_id}  {record.kind}  {record.cwd}{label}")
