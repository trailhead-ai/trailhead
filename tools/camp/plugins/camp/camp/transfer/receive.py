"""`camp transfer-receive` — the peer side of a workspace move.

Two phases, each dispatched as its own subcommand and run on whatever host
this process executes on: :func:`begin` and :func:`finish`. Both continue
`camp.transfer.probe`'s stated posture — no field the caller supplies is ever
used to construct a local path; every path this module writes to is resolved
from this host's OWN group config, keyed only by the `--group`/`--slug` the
caller names. The sender's declared name (`--owner`) is data stamped into the
manifest, never a path component.

**begin** decides whether a transfer may start, and if so prepares a place
for it. A workspace of the given slug already present here, and recorded as
owned by anyone other than the sender, refuses outright — this host will not
let one peer overwrite another peer's arrived content. A workspace already
present and either owned by the sender itself or carrying no recorded owner
at all requires `--overwrite` to proceed, since removing it is destructive;
a genuinely free slug needs no such gate. The overwrite path removes the
prior attempt through camp's own teardown
(`camp.provision.reconcile.reconcile_break`) before seeding a fresh manifest,
so a re-run after a partial failure never leaves the previous attempt's
content behind. The seeded manifest's `owner` is always the sender's
declared name — this host never stamps its own.

`begin` answers each member's basis commit: the commit this host's local
clone already resolves for that member's declared base ref, or `null` when
it does not resolve here at all. A later phase negatives its content
transfer against exactly these commits.

**finish** writes the workspace docs/hooks and spawns camp's existing
detached provisioner (`camp.provision.provision.bring_up_workspace`), which
is what regenerates the platform-specific state a transfer does not copy. It
does not wait for provisioning to complete — "arrived" and "ready to work
in" are deliberately different moments.

**The marker.** Every phase, once it actually runs (never on a refusal — a
refused phase never touches the workspace it refused), appends an entry to a
small JSON log inside the arriving workspace naming which phase reached this
host and how it ended. This is the minimum that keeps an interrupted
transfer legible from the machine the operator moved TO, when they can no
longer see the sending terminal. Read it back with
:func:`read_transfer_marker`, never by parsing the file directly — its shape
is not a contract other code binds to.

**The untrusted boundary.** `--owner` arrives from whatever process invoked
this verb — typically a remote peer over ssh, an untrusted party from this
host's point of view. It is refused, before any group lookup or disk read,
if it exceeds :data:`MAX_OWNER_NAME_BYTES` or fails the same charset a
host's own declared name must pass (`camp.host.config`'s `self_name`
grammar) — the same defend-before-parsing posture `camp.transfer.probe`
applies to the wire response it reads.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "RECEIVE_CONTRACT_VERSION",
    "MAX_OWNER_NAME_BYTES",
    "MarkerEntry",
    "ReceiveRefused",
    "GroupNotConfigured",
    "MalformedOwnerName",
    "OwnershipConflict",
    "OverwriteRequired",
    "begin",
    "finish",
    "read_transfer_marker",
]

#: Bumped whenever the JSON this module answers changes in a way an old
#: parser on the sending side cannot safely read.
RECEIVE_CONTRACT_VERSION = 1

#: `--owner` is refused outright above this many UTF-8 bytes, before a single
#: further step (group lookup, locking, disk read) runs. Mirrors
#: `camp.transfer.probe.MAX_PROBE_RESPONSE_BYTES`'s defend-before-parsing
#: posture, applied to the one field here that arrives from an untrusted peer
#: and is persisted to local state.
MAX_OWNER_NAME_BYTES = 256

# Same grammar `camp.host.config` requires of a declared self_name — an
# owner is, by construction, some host's self_name as declared on its own
# machine, so the charset it must pass here is that same one.
_VALID_OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\Z")

_MARKER_FILENAME = ".transfer-marker.json"

_DEFAULT_BASE = "origin/main"


class ReceiveRefused(Exception):
    """Raised when `begin` or `finish` refuses to act, by name.

    The caller never proceeds past this exception into a write — every
    refusal below is raised strictly before the write it would have guarded.
    """


class MalformedOwnerName(ReceiveRefused):
    """`--owner` is oversized or fails the declared-name charset."""


class GroupNotConfigured(ReceiveRefused):
    """The named group is not configured on this host."""

    def __init__(self, group_name: str) -> None:
        super().__init__(f"group {group_name!r} is not configured on this host")
        self.group_name = group_name


class OwnershipConflict(ReceiveRefused):
    """A workspace of this slug is already recorded as owned by a third host."""

    def __init__(self, slug: str, owner: str, sender: str) -> None:
        super().__init__(
            f"slug {slug!r} already exists here, owned by {owner!r} — refusing "
            f"to let sender {sender!r} overwrite another host's workspace"
        )
        self.owner = owner
        self.sender = sender


class OverwriteRequired(ReceiveRefused):
    """A workspace of this slug already exists and needs `--overwrite`."""

    def __init__(self, slug: str, sender: str) -> None:
        super().__init__(
            f"slug {slug!r} already exists here — pass --overwrite to remove "
            f"it and re-seed for sender {sender!r}, or choose a different slug"
        )
        self.sender = sender


@dataclass(frozen=True)
class MarkerEntry:
    """One line of a transfer's durable per-transfer marker."""

    phase: str
    outcome: str
    at: str


def _validate_owner(owner: str) -> None:
    encoded = owner.encode("utf-8", errors="surrogateescape")
    if len(encoded) > MAX_OWNER_NAME_BYTES:
        raise MalformedOwnerName(
            f"--owner is {len(encoded)} bytes, exceeding the "
            f"{MAX_OWNER_NAME_BYTES}-byte size bound — refused before it is used"
        )
    if not owner or not _VALID_OWNER_RE.match(owner):
        raise MalformedOwnerName(
            f"--owner {owner!r} must start with a lowercase letter or digit "
            "and contain only lowercase letters, digits, and hyphens "
            "(^[a-z0-9][a-z0-9-]*$)"
        )


def _find_group(groups: list[dict[str, Any]], group_name: str) -> dict[str, Any] | None:
    return next((g for g in groups if g["group"]["name"] == group_name), None)


def _basis_commit(repo_root: Path, base: str) -> str | None:
    from ..gitutil import _git_out

    sha = _git_out(repo_root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    return sha or None


def append_marker(ws_dir: Path, *, phase: str, outcome: str) -> None:
    """Append one entry to the workspace's durable transfer marker.

    Creates *ws_dir* if it does not already exist. Called only immediately
    after the write it records has already succeeded — never before, and
    never for a phase that refused.
    """
    ws_dir.mkdir(parents=True, exist_ok=True)
    path = ws_dir / _MARKER_FILENAME

    existing: list[dict[str, Any]] = []
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = raw
        except (OSError, json.JSONDecodeError):
            existing = []

    existing.append(
        {
            "phase": phase,
            "outcome": outcome,
            "at": datetime.now(timezone.utc).isoformat(),
        }
    )

    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_transfer_marker(ws_dir: Path) -> tuple[MarkerEntry, ...]:
    """Read back the workspace's durable transfer marker, oldest entry first.

    Returns an empty tuple for a workspace that carries no marker, or one
    whose marker cannot be parsed — the marker is diagnostic, not load-
    bearing, so a damaged or absent one is never raised as an error.
    """
    path = ws_dir / _MARKER_FILENAME
    if not path.is_file():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(raw, list):
        return ()

    entries: list[MarkerEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        phase = item.get("phase")
        outcome = item.get("outcome")
        at = item.get("at")
        if isinstance(phase, str) and isinstance(outcome, str) and isinstance(at, str):
            entries.append(MarkerEntry(phase=phase, outcome=outcome, at=at))
    return tuple(entries)


def begin(
    *,
    groups: list[dict[str, Any]],
    group_name: str,
    slug: str,
    sender: str,
    overwrite: bool,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Prepare this host to receive *slug*'s content from *sender*.

    See the module docstring for the refuse/teardown/reseed rule. Returns
    the JSON-serializable answer: this transfer's contract version, and per
    member the basis commit this host already holds for that member's
    declared base ref (or `null`).

    Raises:
        MalformedOwnerName: *sender* is oversized or malformed — checked
            before anything else runs.
        GroupNotConfigured: *group_name* is not configured on this host.
        OwnershipConflict: a workspace of *slug* is recorded as owned by a
            host other than *sender*.
        OverwriteRequired: a workspace of *slug* already exists and
            *overwrite* is False.
    """
    _validate_owner(sender)

    group = _find_group(groups, group_name)
    if group is None:
        raise GroupNotConfigured(group_name)

    from ..group.manifest import (
        ManifestError,
        manifest_path_for,
        owner_of,
        read_central_manifest,
        workspace_dir,
    )
    from ..provision.provision import seed_pending_workspace
    from ..provision.reconcile import _slug_lock, reconcile_break

    mpath = manifest_path_for(group_name, slug, env=env)
    ws_dir = workspace_dir(group_name, slug, env=env)

    # The check and the decision it produces are made under the slug lock, so
    # two overlapping `begin` calls for the same slug cannot both read the
    # same pre-teardown state and decide to act on it. The lock is released
    # before calling seed_pending_workspace / reconcile_break below — both
    # acquire this SAME lock internally to serialize their own writes, and
    # threading.Lock is not reentrant, so holding it across that call would
    # deadlock this thread against itself.
    lock = _slug_lock(f"{group_name}/{slug}")
    with lock:
        exists = mpath.is_file()
        needs_teardown = False
        if exists:
            try:
                existing_owner = owner_of(read_central_manifest(mpath))
            except ManifestError:
                existing_owner = None

            if existing_owner is not None and existing_owner != sender:
                raise OwnershipConflict(slug, existing_owner, sender)

            if not overwrite:
                raise OverwriteRequired(slug, sender)

            needs_teardown = True

    if needs_teardown:
        reconcile_break(group, slug, env=env, force=True)

    seed_pending_workspace(group, slug, env=env, owner=sender)

    members_answer = []
    for member in group["members"]:
        repo_root = Path(member["repo_root"])
        base = member.get("base") or _DEFAULT_BASE
        members_answer.append(
            {"name": member["name"], "basis_commit": _basis_commit(repo_root, base)}
        )

    append_marker(ws_dir, phase="begin", outcome="ok")

    return {
        "contract_version": RECEIVE_CONTRACT_VERSION,
        "members": members_answer,
    }


def finish(
    *,
    groups: list[dict[str, Any]],
    group_name: str,
    slug: str,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Bring the transferred workspace up: docs, hooks, detached provisioner.

    Does not wait for provisioning — `bring_up_workspace` spawns and returns.

    Raises:
        GroupNotConfigured: *group_name* is not configured on this host.
    """
    group = _find_group(groups, group_name)
    if group is None:
        raise GroupNotConfigured(group_name)

    from ..group.manifest import workspace_dir
    from ..provision.provision import bring_up_workspace

    ws_dir = workspace_dir(group_name, slug, env=env)
    mpath = bring_up_workspace(group, slug, env=env)

    append_marker(ws_dir, phase="finish", outcome="ok")

    return {
        "contract_version": RECEIVE_CONTRACT_VERSION,
        "manifest_path": str(mpath),
    }
