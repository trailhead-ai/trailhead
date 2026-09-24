"""Launch-environment resolution shared across camp.

`resolve_launch_environment` is the one resolution of a group's declared
account, the environment a pane binds it under, and the scrub that strips the
ambient environment first — shared by workspace bring-up's trust pre-seed,
the workspace session's own environment (`resolve_session_environment`), and
resurrection's resume stubs. A second,
independent read of the same declaration is two answers that can disagree,
and the disagreement is invisible until a workspace is trusted in one
account's config file and started under another's.

Refusal posture. Every resolution entry point raises :class:`LaunchError` on a harness
seam that cannot answer what was asked of it — a harness camp cannot name, or
one that cannot resolve an account binding it was explicitly given.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


class LaunchError(Exception):
    """A launch-environment resolution camp refused."""


def _unsupported_harness(harness, profile, what: str) -> LaunchError:
    """The refusal for a launch seam that answered ``None``.

    Two seams on this path can answer ``None`` to mean the same thing — this
    harness does not bind an account or resolve a scrub — and one wording
    for both is what keeps them from reading as two different problems to an
    operator.

    Worded without "launch" on purpose: this resolver is also reached from
    workspace-session creation (`resolve_session_environment`), which has no
    launch verb of its own to name.
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


@dataclass(frozen=True)
class SessionEnvironment:
    """The environment statements a workspace's tmux session carries, so
    every pane it starts — the first one, and any the operator opens by
    hand — begins on the group's account with the harness's scrub applied.

    ``removals`` are names the session hides from every pane it starts
    (``tmux set-environment -r``), even when the tmux server's own global
    environment carries them. ``assignments`` are ``(name, value)`` pairs
    it sets. A name is never in both: an assignment already replaces
    whatever the pane would otherwise inherit.
    """

    removals: tuple[str, ...] = ()
    assignments: tuple[tuple[str, str], ...] = ()


def resolve_session_environment(
    harness, group: dict | None, env: dict[str, str] | None = None
) -> SessionEnvironment:
    """The :class:`SessionEnvironment` a workspace session for *group*
    carries — resolved through :func:`resolve_launch_environment`, the same
    resolution every other account-binding surface reads, so the account a
    hand-opened pane lands on and the account the workspace was trusted in
    cannot disagree.

    Why the session and not each command: a tmux pane inherits the tmux
    SERVER's global environment, fixed by whichever process started that
    server. A server started from inside a harness session carries that
    session's markers and account into every pane opened on it. Stating the
    scrub and the binding on the workspace session itself overrides that
    for every pane the session starts, whoever started the server.

    *group* ``None`` (a caller that could not resolve one) states nothing.
    *harness* ``None`` states nothing when the group declares no account,
    and raises :class:`LaunchError` when it does: an unrecognized harness
    must never silently ignore a declared account.
    """
    if group is None:
        return SessionEnvironment()
    if harness is None:
        account = (group.get("launch") or {}).get("account")
        if account is not None:
            raise LaunchError(
                "camp: cannot bind an account — no harness is configured for "
                f"this group, so camp cannot bind the declared account {account}"
            )
        return SessionEnvironment()

    from .profile import resolve_harness_profile

    profile = resolve_harness_profile(group)
    _account, binding, scrub, _launch_env = resolve_launch_environment(
        harness, profile, group, env
    )
    return SessionEnvironment(
        removals=tuple(name for name in scrub if name not in binding),
        assignments=tuple(binding.items()),
    )
