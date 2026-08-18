"""camp's detached session launch engine — the one place camp execs a harness.

`launch_session(group, slug)` spawns a detached, tmux-hosted harness session
rooted at the workspace's resolved launch directory, under a session id camp
mints and therefore already knows.

ONE resolved directory. `HarnessProfile.resolved_cwd(...)` substituted, then
`Path.resolve()`, yields a single path that serves as the launch cwd, the trust
target, and the enumeration scope. Three names for the same directory is how a
session gets launched somewhere it is never found again, so they are computed
once here and never re-derived downstream.

The seam boundary. camp core spells exactly two things: `tmux` and `env -u`.
Every harness literal — the binary, its flags, the names of the variables to
scrub — comes from the trailhead harness seam (`harness_for` →
`session_launch` / `session_launch_env_unset` / `session_enumerate`) and is
placed into argv whole. `tools/camp/tests/test_seam_removal.py` enforces this.

Refusal posture. A refusal raises :class:`LaunchError` and guarantees no process
was started: an unresolvable launch directory, a harness camp cannot name or that
cannot launch sessions, a missing `tmux`, or a trust pre-seed that reported
failure. The CLI layer turns that into camp's one-line stderr refusal. Sessions
already live in the workspace are the deliberate NON-refusal — they are reported
on stderr and the launch proceeds.

This module ends at a successful detached spawn. Confirming the session actually
registered is a separate concern with its own bounded wait.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..bookmark import harness_for
from ..group.manifest import workspace_dir
from .claude_trust import pretrust_workspace
from .profile import resolve_harness_profile

#: Seconds to wait on the pre-spawn enumeration probe. It is advisory output
#: only, so it must never be able to hold up a launch.
_ENUMERATE_TIMEOUT_SECONDS = 10


class LaunchError(Exception):
    """A launch camp refused. Guarantees no process was started."""


@dataclass(frozen=True)
class LaunchedSession:
    """A successfully spawned detached session.

    `session_id` is the id camp minted and handed to the harness — the handle for
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


def _assert_trust(profile, launch_dir: Path, ws_dir: Path, env: dict[str, str]) -> None:
    """Gate the launch on the harness trust pre-seed.

    A harness that stalls on an unanswered trust prompt looks alive and is not,
    so a pre-seed that reports failure is a refusal, not a warning. The opt-out is
    the operator's own call — it warns and proceeds, because the operator who
    disabled it is the one who will answer the prompt.
    """
    if not profile.should_pretrust():
        print(
            f"camp: trust pre-seed disabled for {launch_dir} — the session may stall "
            "at an unanswered trust prompt and still report as live",
            file=sys.stderr,
        )
        return
    if not pretrust_workspace(launch_dir, workspace_root=ws_dir, env=env):
        raise LaunchError(
            f"camp: refusing to launch — could not pre-seed harness trust for "
            f"workspace {launch_dir}"
        )


def _report_live_sessions(harness, launch_dir: Path, env: dict[str, str]) -> None:
    """Best-effort notice about sessions already rooted under *launch_dir*.

    Advisory only. Every failure — no enumeration concept, a missing binary, a
    non-zero exit, undecodable output — degrades to silence: a launch must not
    hinge on camp's ability to describe what is already running.
    """
    try:
        argv = harness.session_enumerate(launch_dir)
        if not argv:
            return
        completed = subprocess.run(
            argv,
            cwd=str(launch_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=_ENUMERATE_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            return
        records = harness.parse_session_list(completed.stdout)
    except Exception:  # noqa: BLE001 — advisory probe, never blocks a launch
        return
    if records:
        print(
            f"camp: {len(records)} session(s) already live in {launch_dir}; "
            "launching another",
            file=sys.stderr,
        )


def launch_session(
    group: dict,
    slug: str,
    *,
    env: dict[str, str] | None = None,
) -> LaunchedSession:
    """Spawn a detached, tmux-hosted harness session for (*group*, *slug*).

    Returns the minted session id and the tmux name to attach with. Raises
    :class:`LaunchError` — with no process started — on any refusal path.
    """
    env = dict(env if env is not None else os.environ)
    profile = resolve_harness_profile(group)
    ws_dir = workspace_dir(group["group"]["name"], slug, env=env)
    launch_dir = _resolve_launch_dir(profile, slug, ws_dir)

    harness = harness_for(group)
    if harness is None:
        raise LaunchError(
            f"camp: refusing to launch — no harness named {profile.binary!r} is "
            "registered"
        )

    session_id = str(uuid.uuid4())
    harness_argv = harness.session_launch(launch_dir, session_id)
    if harness_argv is None:
        raise LaunchError(
            f"camp: refusing to launch — harness {harness.name or profile.binary!r} "
            f"(configured binary {profile.binary!r}) cannot launch sessions"
        )

    if shutil.which("tmux") is None:
        raise LaunchError("camp: refusing to launch — tmux is not on PATH")

    _assert_trust(profile, launch_dir, ws_dir, env)

    scrub = harness.session_launch_env_unset() or []

    # The scrub rides INSIDE the pane command, as `env -u` operands sitting
    # between tmux's own options and the harness argv. Scrubbing camp's Popen
    # environment alone is not enough and silently does nothing in the common
    # case: when a tmux SERVER is already running, `tmux new-session` is a client
    # request and the new pane inherits the SERVER's environment, not this
    # process's. Only the pane-level `env -u` holds in both cases — fresh server
    # and pre-existing one alike. Both scrubs are applied; neither is redundant.
    tmux_name = f"camp-{slug}-{session_id[:8]}"
    argv = ["tmux", "new-session", "-d", "-s", tmux_name, "-c", str(launch_dir), "env"]
    for name in scrub:
        argv += ["-u", name]
    argv += list(harness_argv)

    _report_live_sessions(harness, launch_dir, env)

    scrub_set = set(scrub)
    spawn_env = {k: v for k, v in env.items() if k not in scrub_set}
    subprocess.Popen(
        argv,
        cwd=str(launch_dir),
        env=spawn_env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return LaunchedSession(session_id=session_id, tmux_name=tmux_name, launch_dir=launch_dir)
