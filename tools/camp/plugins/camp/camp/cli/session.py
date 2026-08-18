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


def launch_and_confirm(group: dict, slug: str, *, env: dict[str, str] | None = None):
    """Spawn a session for (*group*, *slug*) and confirm it registered.

    Returns the :class:`LaunchedSession`. Raises :class:`LaunchError` on refusal —
    including a spawn that never confirmed, which the engine has already killed.
    """
    from ..bookmark import harness_for
    from ..launch.session import confirm_session, launch_session

    launched = launch_session(group, slug, env=env)
    print(
        f"camp launch: launched session {launched.session_id} in {launched.launch_dir}\n"
        f"  attach: tmux attach -t {launched.tmux_name}",
        file=sys.stderr,
    )
    confirm_session(harness_for(group), launched, env=env)
    print(f"camp launch: confirmed session {launched.session_id}", file=sys.stderr)
    return launched


def launch_for_new(group: dict, slug: str, *, env: dict[str, str] | None = None) -> str | None:
    """`camp new --launch`'s launch step: the session id, or None on refusal.

    Returning None rather than exiting is the whole point: `camp new` already
    created the workspace, and that success is what its exit code and its stdout
    path report. A failed launch is reported on stderr in exactly the shape
    `camp launch` uses, and leaves the caller with a usable workspace.
    """
    from ..launch.session import LaunchError

    try:
        return launch_and_confirm(group, slug, env=env).session_id
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


def _cmd_launch_group_cli(
    args: list[str],
    group: dict,
    env: dict[str, str] | None,
) -> None:
    """camp launch <slug> [--json] — start a detached harness session.

    Output contract, mirroring `camp pwd`: stdout carries ONLY the session id —
    exactly one line — so a caller can capture it with `$(camp launch …)`. The
    workspace, the tmux attach handle, and the confirmation all go to stderr. On
    any refusal stdout is EMPTY and the exit code is non-zero.
    """
    from ..launch.session import LaunchError
    from ..spine import _consume_flag_value, _die
    from .dispatch import _slug_from_args_or_cwd

    rest = list(args)
    _consume_flag_value(rest, "--group")  # already resolved upstream; drop it
    as_json = _consume_json_flag(rest)

    slug = _slug_from_args_or_cwd(
        rest, group, verb="launch", consume_positional=True, env=env
    )

    try:
        launched = launch_and_confirm(group, slug, env=env)
    except LaunchError as exc:
        _die(_refusal(exc))
        return

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
