"""The credential-store floor camp checks before rooting a window at a directory.

A directory a window is rooted at is fenced by construction: it is inside the
workspace or the composition refuses outright. This module supplies the one
other question that fence does not answer — is the directory a credential
store — and it is the *only* place that answers it, for every directory that
gets to root a window, whether camp computed that directory itself or an
operator named it. A second answer somewhere else is a second boundary, and
boundaries that disagree are holes.

**The credential-directory deny list wins regardless of configuration.**
:data:`CREDENTIAL_DENY_ENTRIES` is the FLOOR, fixed in code, and
:func:`credential_deny_entries` is the list actually checked: the floor plus
one entry per account declared under ``[launch] account`` by ANY group camp
knows about. Derivation is STRICTLY ADDITIVE — config can only append, and no
value of any key removes, narrows, shadows, or reorders a floor entry. That is
the sense in which no group config can relax the rule; it is not a default and
not a suggestion, and there is no key that turns an entry off.

The union spans every group, not just the one whose window is being composed.
An account is a credential store no matter which group declared it, and
scoping the derivation to one group would leave a directory rooted in that
group free to reach another group's OAuth store — the case the rule exists
for. Camp cannot enumerate those declarations without reading the group
configs, so a group config it cannot read is a refusal, not a smaller deny
list.

Matching denies a target that is equal to, under, **or an ancestor of** any
entry. The ancestor direction is what makes the rule bite: without it, a
window rooted at ``~`` would launder the entire home directory — and every
credential store inside it — past the gate. It is also the only direction
that can ever fire for the entries naming a FILE (``~/.netrc``, ``~/.npmrc``,
``~/.pypirc``, ``~/.git-credentials``), since a window root is always a
directory. Those entries stay in the list anyway: the enumerated list is the
documentation of what camp considers a credential store, and a shorter list
that happens to be equivalent today is a list that silently stops being
equivalent the first time an entry moves.

Entries are resolved NON-STRICTLY — a credential directory the operator has
not created yet is still denied, so creating it later can never quietly widen
what was already eligible.

``~`` in deny entries expands from the injected environment's HOME, never from
the process's own notion of home, so the boundary is a function of the
environment the window actually runs under.

Failure mode is :class:`~camp.launch.session.LaunchError` and nothing else.
The gate itself is read-only: it resolves paths and answers.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .recovery import printable_path
from .session import LaunchError

#: The floor of credential stores that are never an eligible launch root,
#: regardless of what any group config says. Fixed in code on purpose — editing
#: this list is a change to a security boundary, not an implementation detail.
#: :func:`credential_deny_entries` appends the accounts groups declare; nothing
#: appended can subtract from what is here.
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

    An entry the filesystem refuses to resolve at all — an embedded NUL is the
    reachable case, since the deny list pools the account every group declared —
    is a :class:`LaunchError`, the same refusal an unreadable group config gets.
    Fail CLOSED: an entry camp cannot resolve is an entry it cannot rule out, and
    letting the error escape would take down every directory-rooted launch, for
    every group, with a traceback naming none of them.
    """
    if entry == "~":
        candidate = home
    elif entry.startswith("~/"):
        candidate = home / entry[2:]
    else:
        candidate = Path(entry)
    try:
        return candidate.resolve()
    except (ValueError, OSError) as exc:
        raise LaunchError(
            "camp: cannot launch — camp cannot resolve the credential store "
            f"{entry!r}, so it cannot tell whether this directory is one: {exc}"
        ) from exc


def _declared_account_entries(env: Mapping[str, str] | None) -> tuple[str, ...]:
    """Every ``[launch] account`` declared by any group camp knows about.

    The group configs are located through the SAME environment the rest of the
    boundary reads, so a test — and an operator with a redirected config dir —
    gets the accounts of the camp they are actually running.

    An account that is neither absolute nor ``~``-anchored is skipped rather
    than resolved: a cwd-relative entry would make the boundary move with the
    directory camp happens to be invoked from, and no harness will bind a
    session to one, so it names no reachable store.

    A group config camp cannot read or parse is a refusal: an unreadable
    declaration is an account camp cannot rule out, and answering with a shorter
    deny list would turn a broken config into a widened boundary.

    A groups directory that does not exist is not that case and does not refuse.
    It yields no entries, leaving the hardcoded floor to answer alone — camp
    knows of no groups at all, so no group declares an account and there is
    nothing the shorter list could be missing.
    """
    from ..group.config import load_all_groups

    try:
        import trailhead.paths as _paths

        groups_dir = _paths.config_dir("camp", env=dict(env) if env is not None else None)
        configs = load_all_groups(groups_dir / "groups")
    except Exception as exc:
        raise LaunchError(
            "camp: cannot launch — camp cannot read the group configs, so it "
            "cannot tell which account directories are credential stores: "
            f"{exc}"
        ) from exc

    entries: list[str] = []
    for config in configs:
        account = (config.get("launch") or {}).get("account")
        if not isinstance(account, str):
            continue
        if account == "~" or account.startswith(("~/", "/")):
            entries.append(account)
    return tuple(entries)


def credential_deny_entries(*, env: Mapping[str, str] | None) -> tuple[str, ...]:
    """The deny list this launch is judged against: the floor, then the accounts.

    :data:`CREDENTIAL_DENY_ENTRIES` comes first and comes through whole, in its
    own order, so the additions are visibly additions.
    """
    derived = tuple(
        entry
        for entry in _declared_account_entries(env)
        if entry not in CREDENTIAL_DENY_ENTRIES
    )
    return CREDENTIAL_DENY_ENTRIES + tuple(dict.fromkeys(derived))


def matches_deny_entry(target: Path, entry: Path) -> bool:
    """Is `target` at, under, or above `entry`? Both must already be resolved.

    The third direction is the load-bearing one: a target ABOVE a credential
    store contains it, and launching a harness there hands it the store.
    """
    return target == entry or entry in target.parents or target in entry.parents


def assert_not_a_credential_store(resolved: Path, *, env: Mapping[str, str] | None) -> None:
    """Refuse an already-resolved window root that touches a credential store.

    The list is :func:`credential_deny_entries` — the fixed floor plus every
    account any group declares — so a store belonging to a group other than the
    one whose window is being composed is refused on the same terms as one of
    the floor entries.

    This rule is UNCONDITIONAL: it answers a question about the directory
    itself, which is the same question no matter who chose it or how it was
    reached. Every caller that roots a window anywhere calls this — a branch
    that skips it is a branch where "no group configuration can permit it"
    stops being true.
    """
    home = _home_from_env(env)
    for entry in credential_deny_entries(env=env):
        denied = _expand(entry, home)
        if matches_deny_entry(resolved, denied):
            raise LaunchError(
                f"camp: cannot launch — directory {printable_path(resolved)} is at, "
                f"under, or "
                f"above the credential store {denied}, which camp will never "
                "root a session at. This rule is fixed in camp and no group "
                "configuration can permit it."
            )
