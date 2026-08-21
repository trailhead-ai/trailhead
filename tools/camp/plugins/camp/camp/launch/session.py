"""camp's detached session launch engine — the one place camp execs a harness.

`launch_session` spawns a detached, tmux-hosted harness session and hands back
the handles for it. A launch is addressed one of two ways and the caller picks
exactly one: by workspace SLUG, where camp resolves the directory, the tmux name
component and the trust scope itself from a manifest it wrote; or by an explicit
ROOT, where the caller supplies all three. Both reach the same spawn — there is
no second engine — and a `resume_session_id` rides on either, re-entering a
session the harness already holds instead of starting a fresh one.

ONE resolved directory. Substituted from `HarnessProfile.resolved_cwd(...)` for a
slug, or handed in as a root, the directory is resolved once here and then serves
as the launch cwd, the trust target, and the enumeration scope. Three names for
the same directory is how a session gets launched somewhere it is never found
again, so they are computed once and never re-derived downstream.

Containment. What fences a launch is WHO CHOSE the directory, not how it was
addressed. A directory camp computed itself is fenced by construction — a slug's
workspace, and equally a root inside that workspace, which is where a resumed
session's recorded cwd lands. A directory the OPERATOR named has no such fence,
so the eligibility gate supplies one, and it must answer BEFORE the trust
pre-seed: the pre-seed's own confinement check compares the launch directory
against the declared trust scope, which for an explicitly rooted launch IS that
same directory, so it can never be the boundary there. Sameness, not containment,
is this module's guarantee — a group that opts out of the pre-seed launches
wherever its `[harness] cwd` template resolves to.

The seam boundary. camp core spells exactly two things: `tmux` and `env -u`.
Every harness literal — the binary, its flags, the names of the variables to
scrub — comes from the trailhead harness seam (`harness_for` → `session_launch` /
`session_resume` / `session_launch_env_unset` / `session_enumerate`) and is placed
into argv whole. `tools/camp/tests/test_seam_removal.py` enforces this.

Refusal posture. A refusal raises :class:`LaunchError` and guarantees no process
was started: an unresolvable or ineligible launch directory, a harness camp cannot
name or that cannot start or re-enter sessions, a missing `tmux`, a trust pre-seed
that reported failure, or a tmux that refused the spawn. The CLI layer turns that
into camp's one-line stderr refusal.

Sessions already live in the launch directory are the deliberate NON-refusal for a
fresh launch — reported on stderr, and the launch proceeds. For a re-entry they are
the opposite: running a session that is already running would double it. Two
independent things can notice — the pre-spawn lookup, and tmux refusing a session
name that is already taken — and they raise the identical message, because which
one noticed is camp's business and not the operator's. The name claim is the
guarantee: it happens AT the spawn, so nothing can race between the look and the
launch.

Calling with neither addressing form, with both, or with an incomplete root triple
is a programming error in the caller — :class:`ValueError`, raised before any work.

This module ends at a successful detached spawn. Confirming the session actually
registered is a separate concern with its own bounded wait — :func:`confirm_session`
below.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..bookmark import harness_for
from ..group.manifest import workspace_dir
from .claude_trust import pretrust_workspace
from .recovery import sanitize_name_component
from .profile import resolve_harness_profile

#: Seconds to wait on the pre-spawn enumeration probe. It is advisory output
#: only, so it must never be able to hold up a launch.
_ENUMERATE_TIMEOUT_SECONDS = 10

#: Bound on the tmux spawn itself. `tmux new-session -d` returns as soon as the
#: session is created (~10ms warm, ~22ms when it must start the server first) —
#: camp waits for it because tmux's exit status IS the session-name claim, and
#: that claim is what makes a doubled session impossible.
_SPAWN_TIMEOUT_SECONDS = 30

#: The fragment tmux's stderr carries when the requested session name is taken.
#: Matching it is what separates "that session is already running" from every
#: other reason a spawn can fail; a misdiagnosed failure is worse than a generic
#: one, so any other non-zero exit is reported as itself.
_DUPLICATE_SESSION_MARKER = "duplicate session"

#: Poll interval and bound for :func:`confirm_session`, pinned from measured
#: cold `claude --remote-control` start-to-enumeration latency (min 1.35s /
#: median 1.39s / max 2.22s across n=8 live launches) — provisional, subject to
#: revision if that measurement drifts.
_CONFIRM_POLL_INTERVAL_SECONDS = 0.5
_CONFIRM_POLL_TIMEOUT_SECONDS = 10.0

#: Bound on the cleanup `tmux kill-session` call in :func:`confirm_session`. The
#: failure was already reported by the time this runs, so a wedged or vanished
#: tmux must not be able to hang — or crash — the refusal that follows it.
_KILL_SESSION_TIMEOUT_SECONDS = 10


class LaunchError(Exception):
    """A launch camp refused. Guarantees no process was started."""


@dataclass(frozen=True)
class LaunchedSession:
    """A successfully spawned detached session.

    `session_id` is the id the session runs under — minted by camp for a fresh
    launch, or the re-entered session's own id — and is the handle for
    programmatic follow-up. `tmux_name` is the operator's attach handle; they are
    deliberately different strings and both are reported.
    """

    session_id: str
    tmux_name: str
    launch_dir: Path


def _resolve_launch_dir(profile, slug: str, ws_dir: Path) -> Path:
    """The ONE directory: substituted from the profile, then fully resolved.

    `strict=True` because every downstream use — the tmux `-c` operand, the trust
    write, the enumeration scope — is meaningless against a path that does not
    exist, and a non-strict resolve would hand all three a plausible-looking lie.
    """
    candidate = profile.resolved_cwd(slug=slug, workspace=ws_dir)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise LaunchError(
            f"camp: cannot launch — launch directory {candidate} is unresolvable: {exc}"
        ) from exc
    if not resolved.is_dir():
        raise LaunchError(
            f"camp: cannot launch — launch directory {resolved} is not a directory"
        )
    return resolved


def _resolve_named_root(
    root: Path, group: dict, env: dict[str, str], *, camp_managed: bool
) -> Path:
    """Gate and resolve an explicitly-rooted launch.

    Eligibility answers first and existence second, so a directory the allowlist
    rejects is refused on that ground whether or not it exists. The existence
    check is not a formality: `tmux new-session -c` silently falls back to the
    invoking environment's home directory rather than failing, so an absent root
    would otherwise root the session somewhere nobody named and nothing fenced.

    *camp_managed* is the caller's claim that *root* sits inside a workspace camp
    itself provisioned for *group* — the same fence the slug flavor is launched
    behind, which is why the ALLOWLIST has nothing to add there. The claim is
    checked, not trusted: it waives that allowlist only when the name rule agrees
    the directory lies inside that group's own workspace tree, so it cannot
    launder an arbitrary operator-named directory past the gate.

    What it never waives is the credential rule, which runs on both branches.
    The allowlist asks who chose this directory; the credential rule asks what is
    IN it, and that answer does not change with the asker. Keeping it outside the
    branch is what keeps "no group configuration can permit it" true of every
    launch rather than of most of them.
    """
    # Imported here rather than at module scope: the gate raises LaunchError and
    # therefore imports it from this module.
    from .eligibility import assert_launch_eligible, assert_not_a_credential_store
    from .recovery import is_workspace_root

    if camp_managed and is_workspace_root(root, [group], env=env):
        resolved = Path(root).resolve()
        assert_not_a_credential_store(resolved, env=env)
    else:
        resolved = assert_launch_eligible(root, group=group, env=env)
    if not resolved.is_dir():
        raise LaunchError(
            f"camp: cannot launch — launch directory {resolved} is not a directory"
        )
    return resolved


def _assert_trust(profile, launch_dir: Path, ws_dir: Path, env: dict[str, str]) -> None:
    """Gate the launch on the harness trust pre-seed.

    A harness that stalls on an unanswered trust prompt looks alive and is not,
    so a pre-seed that reports failure is a refusal, not a warning. The opt-out is
    the operator's own call — it warns and proceeds, because the operator who
    disabled it is the one who will answer the prompt.

    An exception from ``pretrust_workspace`` itself (e.g. its atomic write
    failing mid ``os.replace``) is folded into the same refusal rather than left
    to propagate raw — every caller of this engine relies on ``LaunchError``
    being the only failure mode of a launch attempt.
    """
    if not profile.should_pretrust():
        print(
            f"camp: trust pre-seed disabled for {launch_dir} — the session may stall "
            "at an unanswered trust prompt and still report as live",
            file=sys.stderr,
        )
        return
    try:
        trusted = pretrust_workspace(launch_dir, workspace_root=ws_dir, env=env)
    except Exception as exc:  # noqa: BLE001 — engine-wide contract: refuse, never traceback
        raise LaunchError(
            f"camp: refusing to launch — could not pre-seed harness trust for "
            f"workspace {launch_dir}: {exc}"
        ) from exc
    if not trusted:
        raise LaunchError(
            f"camp: refusing to launch — could not pre-seed harness trust for "
            f"workspace {launch_dir}"
        )


def enumerate_records(
    harness,
    workspace: Path | None,
    env: dict[str, str],
    *,
    cwd: Path | None = None,
):
    """Ask *harness* which sessions are live under *workspace*, and parse the answer.

    The single enumeration mechanic in camp: the pre-spawn advisory probe, the
    confirmation poll, and `camp sessions` all read live sessions through here, so
    all three ask the same question the same way.

    Returns the parsed records, or ``None`` when no answer could be obtained — the
    harness has no enumeration concept, or the enumeration command failed. ``None``
    is deliberately distinct from ``[]`` ("nothing is running"), which is an answer.

    Exceptions propagate. What to DO about an unanswerable enumeration — degrade to
    silence, tolerate it and keep polling, or degrade to a notice — is the caller's
    posture, not this function's.
    """
    argv = harness.session_enumerate(workspace)
    if not argv:
        return None
    completed = subprocess.run(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=_ENUMERATE_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        return None
    return harness.parse_session_list(completed.stdout)


def _report_live_sessions(harness, launch_dir: Path, env: dict[str, str]) -> None:
    """Best-effort notice about sessions already rooted under *launch_dir*.

    Advisory only. Every failure — no enumeration concept, a missing binary, a
    non-zero exit, undecodable output — degrades to silence: a launch must not
    hinge on camp's ability to describe what is already running.
    """
    try:
        records = enumerate_records(harness, launch_dir, env, cwd=launch_dir)
    except Exception:  # noqa: BLE001 — advisory probe, never blocks a launch
        return
    if records:
        print(
            f"camp: {len(records)} session(s) already live in {launch_dir}; "
            "launching another",
            file=sys.stderr,
        )


def already_running_error(session_id: str, tmux_name: str) -> LaunchError:
    """The one wording for "that session is already running".

    Every detector raises it — the pre-spawn lookup below, tmux refusing the
    session name at the spawn, and the CLI's own pre-resolution liveness gate —
    so the operator reads one message about one condition, and reads it as
    "already running" rather than as a failure. Public precisely because the
    third of those lives outside this module: a second wording for the same
    condition is a second condition as far as anyone reading the terminal is
    concerned.
    """
    return LaunchError(
        f"camp: refusing to launch — session {session_id} is already running as "
        f"tmux session {tmux_name}; attach it with `tmux attach -t {tmux_name}`"
    )


def _refuse_if_already_live(
    harness, session_id: str, tmux_name: str, launch_dir: Path, env: dict[str, str]
) -> None:
    """Refuse re-entry of a session still live under *launch_dir*.

    The mirror image of :func:`_report_live_sessions`: a live neighbour is a
    notice when starting a new session and a refusal when re-entering an existing
    one. Every failure degrades to silence here for the same reason it does
    there — this lookup produces the legible refusal, it is not the guarantee.
    The guarantee is tmux's claim on the session name, which cannot be raced
    because it happens at the spawn itself.
    """
    try:
        records = enumerate_records(harness, launch_dir, env, cwd=launch_dir)
    except Exception:  # noqa: BLE001 — same posture as the advisory probe
        return
    if any(record.session_id == session_id for record in records or ()):
        raise already_running_error(session_id, tmux_name)


def launch_session(
    group: dict,
    slug: str | None = None,
    *,
    env: dict[str, str] | None = None,
    root: Path | None = None,
    name_component: str | None = None,
    trust_scope: Path | None = None,
    resume_session_id: str | None = None,
    camp_managed_root: bool = False,
) -> LaunchedSession:
    """Spawn a detached, tmux-hosted harness session; return its handles.

    Addressed either by *slug* — the workspace flavor, where camp derives the
    launch directory, the tmux name component and the trust scope from the group
    manifest — or by an explicit *root* with its own *name_component* and
    *trust_scope*. Exactly one of the two, or :class:`ValueError` before any work.

    *camp_managed_root* rides on the *root* flavor and says the directory came
    from camp's own workspace layout rather than from an operator naming it, which
    is what the eligibility gate exists to fence. It is verified against the name
    rule before it can open that gate (see :func:`_resolve_named_root`).

    *resume_session_id* rides on either flavor: given, the session runs under that
    id and the pane runs the harness's re-entry argv instead of a fresh-session
    one, so the session reclaims the very tmux name its first launch used.

    Returns the session id and the tmux name to attach with. Raises
    :class:`LaunchError` — with no process started — on any refusal path.
    """
    if (slug is None) == (root is None):
        raise ValueError(
            "launch_session takes exactly one of slug or root — never both, "
            "never neither"
        )
    if root is not None and (name_component is None or trust_scope is None):
        raise ValueError(
            "a launch rooted at a named directory requires both name_component "
            "and trust_scope"
        )

    env = dict(env if env is not None else os.environ)
    profile = resolve_harness_profile(group)
    if root is None:
        trust_root = workspace_dir(group["group"]["name"], slug, env=env)
        launch_dir = _resolve_launch_dir(profile, slug, trust_root)
        name_component = slug
    else:
        launch_dir = _resolve_named_root(root, group, env, camp_managed=camp_managed_root)
        trust_root = trust_scope

    harness = harness_for(group)
    if harness is None:
        raise LaunchError(
            f"camp: refusing to launch — no harness named {profile.binary!r} is "
            "registered"
        )

    if resume_session_id is None:
        session_id = str(uuid.uuid4())
        harness_argv = harness.session_launch(launch_dir, session_id)
        unsupported = "cannot launch sessions"
    else:
        session_id = resume_session_id
        harness_argv = harness.session_resume(session_id)
        unsupported = "cannot re-enter sessions"
    if harness_argv is None:
        raise LaunchError(
            f"camp: refusing to launch — harness {harness.name or profile.binary!r} "
            f"(configured binary {profile.binary!r}) {unsupported}"
        )

    if shutil.which("tmux") is None:
        raise LaunchError("camp: refusing to launch — tmux is not on PATH")

    _assert_trust(profile, launch_dir, trust_root, env)

    scrub = harness.session_launch_env_unset()
    if scrub is None:
        raise LaunchError(
            f"camp: refusing to launch — harness {harness.name or profile.binary!r} "
            f"(configured binary {profile.binary!r}) cannot launch sessions"
        )

    # The scrub rides INSIDE the pane command, as `env -u` operands sitting
    # between tmux's own options and the harness argv. Scrubbing camp's own
    # spawn environment alone is not enough and silently does nothing in the common
    # case: when a tmux SERVER is already running, `tmux new-session` is a client
    # request and the new pane inherits the SERVER's environment, not this
    # process's. Only the pane-level `env -u` holds in both cases — fresh server
    # and pre-existing one alike. Both scrubs are applied; neither is redundant.
    # Folded at the single point every flavor's name is composed, so the handle
    # camp prints is one tmux will accept back. A caller-supplied component is
    # a directory basename, which routinely carries a dot.
    tmux_name = (
        f"camp-{sanitize_name_component(name_component)}-"
        f"{sanitize_name_component(session_id[:8])}"
    )
    argv = ["tmux", "new-session", "-d", "-s", tmux_name, "-c", str(launch_dir), "env"]
    for name in scrub:
        argv += ["-u", name]
    argv += list(harness_argv)

    if resume_session_id is None:
        _report_live_sessions(harness, launch_dir, env)
    else:
        _refuse_if_already_live(harness, session_id, tmux_name, launch_dir, env)

    scrub_set = set(scrub)
    spawn_env = {k: v for k, v in env.items() if k not in scrub_set}

    def _kill_session_quietly(name: str) -> None:
        """Best-effort reclaim of a session name after an indeterminate spawn.

        Every outcome is acceptable and none changes the refusal that follows:
        the session may never have existed, in which case tmux simply reports
        no such session. The point is only that camp does not leave a session
        running under a name it is about to tell the operator it could not
        claim.
        """
        try:
            subprocess.run(
                ["tmux", "kill-session", "-t", name],
                capture_output=True,
                text=True,
                timeout=_KILL_SESSION_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    try:
        spawned = subprocess.run(
            argv,
            cwd=str(launch_dir),
            env=spawn_env,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_SPAWN_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # A timeout means the read hung, not that nothing happened: tmux may
        # already have claimed the name server-side. Reporting failure while
        # that session runs is the worst of both — the operator is told the
        # launch failed, and their next attempt refuses as "already running"
        # for a session they never got. Reclaim the name before giving up.
        if isinstance(exc, subprocess.TimeoutExpired):
            _kill_session_quietly(tmux_name)
        raise LaunchError(
            f"camp: refusing to launch — tmux could not start session "
            f"{tmux_name}: {exc}"
        ) from exc
    if spawned.returncode != 0:
        stderr = (spawned.stderr or "").strip()
        if _DUPLICATE_SESSION_MARKER in stderr:
            raise already_running_error(session_id, tmux_name)
        detail = stderr or f"exit status {spawned.returncode}"
        raise LaunchError(
            f"camp: refusing to launch — tmux could not start session "
            f"{tmux_name}: {detail}"
        )

    return LaunchedSession(session_id=session_id, tmux_name=tmux_name, launch_dir=launch_dir)


def _poll_enumerated(
    harness, session_id: str, launch_dir: Path, env: dict[str, str]
) -> bool:
    """One enumeration attempt: True iff *session_id* is exactly among the live ids.

    Membership is exact-string on ``SessionRecord.session_id`` — never prefix or
    name matching, which could mistake an unrelated session for camp's own. An
    unanswerable enumeration is simply "not yet found": the caller keeps polling.
    """
    records = enumerate_records(harness, launch_dir, env, cwd=launch_dir)
    return any(record.session_id == session_id for record in records or ())


def confirm_session(
    harness,
    launched: LaunchedSession,
    *,
    env: dict[str, str] | None = None,
    interval: float = _CONFIRM_POLL_INTERVAL_SECONDS,
    timeout: float = _CONFIRM_POLL_TIMEOUT_SECONDS,
    sleep=time.sleep,
    clock=time.monotonic,
) -> None:
    """Confirm *launched* registered with the harness, or kill it and refuse.

    Polls ``harness.session_enumerate(launched.launch_dir)`` at *interval* up to
    *timeout*, testing exact-string membership of ``launched.session_id``. A
    :class:`HarnessError` mid-poll (e.g. an unsafe-argv guard tripping), any
    :class:`OSError` (a missing harness binary as :class:`FileNotFoundError` — camp
    only which-checks tmux, not the harness — or a launch dir yanked out from under
    a running enumeration as :class:`PermissionError`/:class:`NotADirectoryError`),
    or a hung enumeration subprocess (:class:`subprocess.TimeoutExpired`) is a
    failed POLL, not a failed launch — polling continues to timeout expiry, since
    the harness itself may simply not be up yet. This mirrors the other two
    ``enumerate_records`` callers in this module, which both catch broadly for the
    same reason: a read-only query never escapes as a raw traceback.

    Confirmed → returns normally: the process exists and is enumerable, no claim
    of usability beyond that.

    Timed out → kills the tmux session by the exact name camp chose and raises
    :class:`LaunchError`. A session absent from enumeration is, empirically, most
    often one stalled at an unaccepted trust prompt — even a successful pretrust
    still leaves some launches stalled there — so the failure message names that
    as the probable cause. A failing kill is reported on stderr naming the tmux
    session; it is never left silent, even though the launch is refused either
    way.
    """
    from trailhead.harness import HarnessError

    env = dict(env if env is not None else os.environ)
    start = clock()
    poll_count = 0
    while True:
        poll_count += 1
        try:
            if _poll_enumerated(harness, launched.session_id, launched.launch_dir, env):
                return
        except (HarnessError, OSError, subprocess.TimeoutExpired):
            pass
        elapsed = clock() - start
        if elapsed >= timeout:
            break
        sleep(interval)

    elapsed = clock() - start
    print(
        f"camp: confirmation of session {launched.session_id} timed out after "
        f"{poll_count} poll(s) over {elapsed:.2f}s; killing tmux session "
        f"{launched.tmux_name}",
        file=sys.stderr,
    )
    try:
        kill = subprocess.run(
            ["tmux", "kill-session", "-t", launched.tmux_name],
            capture_output=True,
            text=True,
            timeout=_KILL_SESSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(
            f"camp: failed to kill tmux session {launched.tmux_name}: {exc}",
            file=sys.stderr,
        )
        raise LaunchError(
            f"camp: launch of session {launched.session_id} could not be confirmed — "
            f"likely stalled at an unaccepted trust prompt in {launched.launch_dir}"
        ) from exc
    if kill.returncode != 0:
        print(
            f"camp: failed to kill tmux session {launched.tmux_name}: "
            f"{(kill.stderr or '').strip() or kill.returncode}",
            file=sys.stderr,
        )
    raise LaunchError(
        f"camp: launch of session {launched.session_id} could not be confirmed — "
        f"likely stalled at an unaccepted trust prompt in {launched.launch_dir}"
    )
