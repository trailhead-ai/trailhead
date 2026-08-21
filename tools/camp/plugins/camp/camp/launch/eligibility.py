"""The one gate camp asks before rooting a session at an operator-named directory.

A workspace-rooted launch is fenced by construction: camp computed the directory
itself, from a manifest it wrote — whether it was addressed by slug or by a path
inside that workspace. A launch rooted at a directory the operator names has no
such fence, so this module supplies it — and it is the *only* place that answers
the question, for every launch rooted anywhere camp did not compute itself. A
second answer somewhere else is a second boundary, and boundaries that disagree
are holes.

THE ORDER OF THE THREE CHECKS IS PART OF THE CONTRACT.

1. **Nothing configured is a refusal, not a default.** A group with no
   ``[launch] roots`` has no eligible directory at all. Directory rooting is off
   until an operator turns it on, and the refusal names the missing allowlist so
   turning it on is obvious.

2. **The allowlist.** The target must be equal to, or under, one of the resolved
   ``[launch] roots`` entries. Equal-or-under only — allowlisting ``~/code``
   never allowlists ``~``. Both sides are FULLY RESOLVED before comparison, so a
   symlink cannot smuggle a directory into the allowlist (nor out of it: a
   symlink sitting inside an allowlisted root that points elsewhere is judged by
   where it points).

3. **The credential-directory deny list, checked last and winning regardless of
   configuration.** :data:`CREDENTIAL_DENY_ENTRIES` is fixed in code. It is not a
   default, not a suggestion, and no group config can relax it. It is checked
   AFTER the allowlist precisely so that its refusal can be worded as its own
   rule: an operator who reads "not under the allowlist" reasonably concludes
   they can fix it by editing the allowlist, and for a credential directory that
   conclusion must never be available. The deny refusal therefore names the
   credential rule and says nothing about the allowlist at all.

   Matching denies a target that is equal to, under, **or an ancestor of** any
   entry. The ancestor direction is what makes the rule bite: without it,
   ``roots = ["~"]`` would launder the entire home directory — and every
   credential store inside it — past the gate in one line of config. It is also
   the only direction that can ever fire for the entries naming a FILE
   (``~/.netrc``, ``~/.npmrc``, ``~/.pypirc``, ``~/.git-credentials``), since a
   launch root is always a directory. Those entries stay in the list anyway: the
   enumerated list is the documentation of what camp considers a credential
   store, and a shorter list that happens to be equivalent today is a list that
   silently stops being equivalent the first time an entry moves.

   Entries are resolved NON-STRICTLY — a credential directory the operator has
   not created yet is still denied, so creating it later can never quietly widen
   what was already eligible.

``~`` in both roots entries and deny entries expands from the injected
environment's HOME, never from the process's own notion of home, so the boundary
is a function of the environment the launch will actually run under.

Failure mode is :class:`~camp.launch.session.LaunchError` and nothing else, so
this composes with the launch engine's guarantee that a refusal started no
process. The gate itself is read-only: it resolves paths and answers.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .recovery import printable_path
from .session import LaunchError

#: Credential stores that are never an eligible launch root, regardless of what
#: any group config says. Fixed in code on purpose — extending or trimming this
#: list is a change to a security boundary, not an implementation detail.
CREDENTIAL_DENY_ENTRIES: tuple[str, ...] = (
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.azure",
    "~/.kube",
    "~/.docker",
    "~/.config/gcloud",
    "~/.netrc",
    "~/.config/gh",
    "~/.npmrc",
    "~/.pypirc",
    "~/.git-credentials",
    # The harness's own OAuth store. camp scrubs the live session token out of
    # the pane environment on every launch for exactly this reason; the same
    # material sits on disk here, so rooting a session at it would hand back
    # what the scrub just took away.
    "~/.claude",
    "~/.claude.json",
    "~/Library/Keychains",
    "~/.password-store",
    "~/.local/share/keyrings",
    "~/.config/op",
    "~/.terraform.d",
    "~/.cargo/credentials",
    "~/.gem/credentials",
)


def _home_from_env(env: Mapping[str, str] | None) -> Path:
    """Resolve HOME from the injected env, falling back to the real home.

    The fallback only ever widens the deny list to the invoking user's actual
    credential stores, which is the safe direction; it mirrors the same
    resolution order the trust pre-seed uses.
    """
    if env:
        for key in ("HOME", "USERPROFILE"):
            if key in env:
                return Path(env[key])
    return Path.home()


def _expand(entry: str, home: Path) -> Path:
    """Expand a leading ``~`` against the injected home, then fully resolve.

    Deliberately not :meth:`Path.expanduser`, which consults the process's own
    environment and password database and would make the boundary depend on who
    happens to be running camp rather than on the launch environment.
    """
    if entry == "~":
        candidate = home
    elif entry.startswith("~/"):
        candidate = home / entry[2:]
    else:
        candidate = Path(entry)
    return candidate.resolve()


def matches_deny_entry(target: Path, entry: Path) -> bool:
    """Is `target` at, under, or above `entry`? Both must already be resolved.

    The third direction is the load-bearing one: a target ABOVE a credential
    store contains it, and launching a harness there hands it the store.
    """
    return target == entry or entry in target.parents or target in entry.parents


def assert_launch_eligible(
    target: Path,
    *,
    group: dict[str, Any],
    env: Mapping[str, str] | None,
) -> Path:
    """Return the resolved `target`, or refuse the launch.

    `group` is a loaded group config; `env` is the environment the launch will
    run under, supplying HOME for every ``~`` expansion. Raises
    :class:`~camp.launch.session.LaunchError` on any refusal.
    """
    home = _home_from_env(env)
    resolved = Path(target).resolve()

    group_name = (group.get("group") or {}).get("name", "?")
    roots = (group.get("launch") or {}).get("roots") or []

    if not roots:
        raise LaunchError(
            f"camp: cannot launch — group {group_name!r} configures no "
            "[launch] roots allowlist, so no named directory is eligible; add "
            "[launch] roots = [...] to the group config to enable "
            "directory-rooted launches"
        )

    if not any(
        resolved == root or root in resolved.parents
        for root in (_expand(entry, home) for entry in roots)
    ):
        raise LaunchError(
            f"camp: cannot launch — directory {printable_path(resolved)} is not "
            f"at or under the "
            f"[launch] roots allowlist for group {group_name!r}: "
            f"{', '.join(roots)}"
        )

    assert_not_a_credential_store(resolved, env=env)

    return resolved


def assert_not_a_credential_store(resolved: Path, *, env: Mapping[str, str] | None) -> None:
    """Refuse an already-resolved launch root that touches a credential store.

    Split out of :func:`assert_launch_eligible` because this rule alone is
    UNCONDITIONAL. The allowlist answers a question about a directory the
    operator named, and a directory camp computed itself never had to answer it;
    this rule answers a question about the directory itself, which is the same
    question no matter who chose it. Every caller that roots a session anywhere
    calls this, whether or not it calls the gate above — a branch that skips it
    is a branch where "no group configuration can permit it" stops being true.
    """
    home = _home_from_env(env)
    for entry in CREDENTIAL_DENY_ENTRIES:
        denied = _expand(entry, home)
        if matches_deny_entry(resolved, denied):
            raise LaunchError(
                f"camp: cannot launch — directory {printable_path(resolved)} is at, "
                f"under, or "
                f"above the credential store {denied}, which camp will never "
                "root a session at. This rule is fixed in camp and no group "
                "configuration can permit it."
            )
