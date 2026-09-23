"""Session enumeration and launch-environment resolution shared across camp.

`enumerate_records` is the one place camp asks a harness which sessions are
live and parses the answer — `camp sessions`, and the ref-addressed lookup
`kill`/`attach` share, all read live sessions through here, so every surface
asks the same question the same way.

`resolve_launch_environment` is the one resolution of a group's declared
account, the environment a pane binds it under, and the scrub that strips the
ambient environment first — shared by workspace bring-up's trust pre-seed and
the window-composition path that actually starts a conversation. A second,
independent read of the same declaration is two answers that can disagree,
and the disagreement is invisible until a workspace is trusted in one
account's config file and started under another's.

Refusal posture. Both entry points raise :class:`LaunchError` on a harness
seam that cannot answer what was asked of it — a harness camp cannot name, or
one that cannot resolve an account binding it was explicitly given.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


#: Seconds to wait on the enumeration probe/subprocess. Advisory only, so it
#: must never be able to hold anything up indefinitely.
_ENUMERATE_TIMEOUT_SECONDS = 10


class LaunchError(Exception):
    """A launch-environment resolution camp refused."""


def enumerate_records(
    harness,
    workspace: Path | None,
    env: dict[str, str],
    *,
    cwd: Path | None = None,
):
    """Ask *harness* which sessions are live under *workspace*, and parse the answer.

    The single enumeration mechanic in camp — `camp sessions`, and the
    ref-addressed lookup `kill`/`attach` share, all read live sessions
    through here, so every surface asks the same question the same way.

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


def _unsupported_harness(harness, profile, what: str) -> LaunchError:
    """The refusal for a launch seam that answered ``None``.

    Two seams on this path can answer ``None`` to mean the same thing — this
    harness does not bind an account or resolve a scrub — and one wording
    for both is what keeps them from reading as two different problems to an
    operator.

    Worded without "launch" on purpose: this resolver is also reached from
    the window-creation key's refusal path
    (`cli/window_dispatch.py:dispatch_window`, via `compose_window`), which
    has no launch verb of its own to name.
    """
    return LaunchError(
        f"camp: cannot bind an account — harness {harness.name or profile.binary!r} "
        f"(configured binary {profile.binary!r}) {what}"
    )


def _resolve_account_binding(harness, profile, group: dict, env: dict[str, str]):
    """The group's declared account and the environment the harness binds it to.

    Camp reads no key of the returned mapping: only the harness knows which
    variables express an account, and a declared value is opaque here — every
    check on it belongs to the harness that will honor it.

    A harness that refuses a DECLARED account refuses the launch: camp was given
    an instruction it cannot honor, and starting the session anyway would put it
    on an account contradicting the group's own statement of intent.

    A harness that refuses to resolve its DEFAULT is a different case, and is
    warned about rather than refused. The refusal there is about a contradiction
    already present in *env* — one camp neither created nor was asked to take a
    side in — and every launch in such an environment would otherwise be blocked
    on a condition no group declaration can clear. Camp says so loudly and
    launches with no assignment of its own, which is the state that environment
    was already in.

    An EMPTY mapping is a legitimate answer, not a failure to answer: a harness
    whose default has no value that expresses it states the default as the
    variable's absence and puts the name in its scrub instead. Camp treats the
    two the same way — scrub, then assign — and reads no key of either.
    """
    from trailhead.harness import HarnessError

    account = (group.get("launch") or {}).get("account")
    try:
        binding = harness.session_launch_env_set(account, env=env)
    except HarnessError as exc:
        if account is not None:
            raise LaunchError(
                f"camp: cannot bind an account — harness "
                f"{harness.name or profile.binary!r} will not bind this session to "
                f"the declared account {account}: {exc}"
            ) from exc
        print(
            f"camp: no account declared and harness "
            f"{harness.name or profile.binary!r} will not resolve a default here: "
            f"{exc} — continuing with NO account binding",
            file=sys.stderr,
        )
        return None, {}
    if binding is None:
        raise _unsupported_harness(harness, profile, "supports no session binding at all")
    return account, dict(binding)


def resolve_launch_environment(
    harness, profile, group: dict, env: dict[str, str] | None = None
) -> tuple[str | None, dict[str, str], tuple[str, ...], dict[str, str]]:
    """The account, the binding, the scrub, and the environment a pane will carry.

    The ONE resolution, shared by every surface that must agree about which
    account a group's session runs on: workspace bring-up, whose trust
    pre-seed writes into that account's config file, and window composition,
    which starts the conversation itself. A second, independent read is two
    answers that can disagree, and the disagreement is invisible until a
    workspace is trusted in one account's file and started under another's.

    The returned environment is the PANE's, not camp's: the scrub is applied
    before the assignments, in that order, because a harness may state a default
    as a name's ABSENCE and a declared account as an assignment of that same
    name. Merging the binding over the raw *env* would leave an ambient value in
    place exactly when the harness meant it removed.

    *env* defaults to the process environment, because this is a public entry
    point and its callers reach it with the same optional env their own
    signatures carry. Defaulting here rather than at each call site is what
    keeps a caller that forwards its own ``None`` from turning into an
    attribute error deep inside a best-effort step that swallows it — a
    silently skipped trust seed rather than a refusal.
    """
    env = dict(env if env is not None else os.environ)
    account, binding = _resolve_account_binding(harness, profile, group, env)
    scrub = harness.session_launch_env_unset()
    if scrub is None:
        raise _unsupported_harness(harness, profile, "supports no session binding at all")
    scrub_set = set(scrub)
    launch_env = {k: v for k, v in env.items() if k not in scrub_set}
    launch_env.update(binding)
    return account, binding, tuple(scrub), launch_env
