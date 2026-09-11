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

**history** receives one member's committed history, sent by
`camp.transfer.history.send_history` as a `git bundle` on stdin, and lands
it directly — no git remote is ever read or written on this path. The
member's `repo_root` is resolved from THIS host's own group config, keyed
only by `--member`; the bundle bytes carry no path. `git bundle unbundle -`
is fed the bytes verbatim; a bundle whose prerequisite commits this host
does not hold fails the phase by name (`BundleUnbundleFailed`, carrying
git's own stderr) before anything is written — `unbundle` creates no refs on
either success or failure, so there is no ref to roll back. On success the
member's slug branch (the same `branch_pattern`-derived name
`camp.provision.reconcile` uses) is force-updated with `git update-ref` to
the bundle's reported tip — unconditional, so re-running `history` against a
branch this host already has moves it rather than refusing — and the
member's worktree is then materialized through
`camp.provision.reconcile._add_worktree_for_member`, which reuses that
already-present local branch (`_branch_exists_locally`) rather than
branching a fresh one off `base`.

**worktree** receives one member's working-tree content, sent by
`camp.transfer.worktree.send_worktree` as a stdlib `tarfile` stream on
stdin, and extracts it directly over the member's worktree — resolved from
THIS host's own group config, keyed only by `--member`, exactly like
`history`. The extraction itself (`camp.transfer.worktree.extract_archive`)
refuses any archive member whose path or link target would land outside
that worktree before writing it; a refusal here surfaces as
`ArchiveMemberRefused`, naming the member and the reason. Never buffers the
whole stream — `sys.stdin.buffer` is handed to the extractor directly, and
extraction reads it one archive member at a time.

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
import subprocess
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
    "MemberNotConfigured",
    "BundleUnbundleFailed",
    "BundleRefUnresolved",
    "ArchiveMemberRefused",
    "begin",
    "finish",
    "history",
    "worktree",
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


class MemberNotConfigured(ReceiveRefused):
    """*member* is not declared in the named group's config on this host."""

    def __init__(self, group_name: str, member: str) -> None:
        super().__init__(
            f"member {member!r} is not declared in group {group_name!r} on this host"
        )
        self.group_name = group_name
        self.member = member


class BundleUnbundleFailed(ReceiveRefused):
    """`git bundle unbundle` refused the incoming bundle — its own stderr,
    carried through unchanged. Raised before any ref is touched."""

    def __init__(self, member: str, stderr: str) -> None:
        super().__init__(
            f"member {member!r}: git bundle unbundle refused the incoming "
            f"bundle: {stderr.strip()}"
        )
        self.member = member
        self.stderr = stderr


class BundleRefUnresolved(ReceiveRefused):
    """The bundle unbundled cleanly but reported no tip for the expected ref."""

    def __init__(self, member: str, ref: str) -> None:
        super().__init__(
            f"member {member!r}: the bundle carried no tip for expected ref "
            f"{ref!r} — refusing to update it from an unresolved commit"
        )
        self.member = member
        self.ref = ref


class ArchiveMemberRefused(ReceiveRefused):
    """`worktree`'s incoming archive carried a member whose path or link
    target would land outside the member's worktree — refused before that
    member was written; see `camp.transfer.worktree.ArchiveMemberEscaped`,
    which this wraps with the phase's usual by-name-refusal shape."""

    def __init__(self, member: str, detail: str) -> None:
        super().__init__(f"member {member!r}: {detail}")
        self.member = member


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


def _find_member(group: dict[str, Any], member: str) -> dict[str, Any] | None:
    return next((m for m in group["members"] if m["name"] == member), None)


def history(
    *,
    groups: list[dict[str, Any]],
    group_name: str,
    slug: str,
    member: str,
    bundle_bytes: bytes,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Land one member's bundled history, sent by
    `camp.transfer.history.send_history`, directly into this host's clone —
    no git remote is ever contacted. See the module docstring's `history`
    section for the full sequence.

    Raises:
        GroupNotConfigured: *group_name* is not configured on this host.
        MemberNotConfigured: *member* is not declared in that group here.
        BundleUnbundleFailed: `git bundle unbundle` refused the bundle (most
            commonly a missing prerequisite commit) — raised before any ref
            is touched.
        BundleRefUnresolved: the bundle unbundled cleanly but named no tip
            for the branch this host expected.
    """
    group = _find_group(groups, group_name)
    if group is None:
        raise GroupNotConfigured(group_name)

    member_cfg = _find_member(group, member)
    if member_cfg is None:
        raise MemberNotConfigured(group_name, member)

    from ..group.manifest import workspace_dir
    from ..provision.reconcile import DEFAULT_BASE, _add_worktree_for_member, _branch_name, _worktree_path

    repo_root = Path(member_cfg["repo_root"])
    branch_pattern: str = group.get("branch_pattern", "worktree-{slug}")
    branch = _branch_name(slug, branch_pattern)
    ref = f"refs/heads/{branch}"

    result = subprocess.run(
        ["git", "-C", str(repo_root), "bundle", "unbundle", "-"],
        input=bundle_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise BundleUnbundleFailed(member, result.stderr.decode("utf-8", errors="replace"))

    tip_sha: str | None = None
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip() == ref:
            tip_sha = parts[0]
            break
    if tip_sha is None:
        raise BundleRefUnresolved(member, ref)

    update_result = subprocess.run(
        ["git", "-C", str(repo_root), "update-ref", ref, tip_sha],
        capture_output=True,
        check=False,
    )
    if update_result.returncode != 0:
        raise BundleUnbundleFailed(
            member, update_result.stderr.decode("utf-8", errors="replace")
        )

    wt_path = _worktree_path(group_name, slug, member, env=env)
    base = member_cfg.get("base") or DEFAULT_BASE
    _add_worktree_for_member(member_cfg, wt_path, branch, repo_root, base=base, slug=slug)

    ws_dir = workspace_dir(group_name, slug, env=env)
    append_marker(ws_dir, phase="history", outcome="ok")

    return {
        "contract_version": RECEIVE_CONTRACT_VERSION,
        "member": member,
        "branch": branch,
        "commit": tip_sha,
    }


def worktree(
    *,
    groups: list[dict[str, Any]],
    group_name: str,
    slug: str,
    member: str,
    archive_stream: Any,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Extract one member's working-tree archive, sent by
    `camp.transfer.worktree.send_worktree`, directly over this host's own
    worktree for that member. See the module docstring's `worktree` section.

    *archive_stream* is a binary file-like object read incrementally — never
    a `bytes` buffer, unlike `history`'s `bundle_bytes` — so a worktree
    larger than this process's memory budget is never fully buffered.

    Raises:
        GroupNotConfigured: *group_name* is not configured on this host.
        MemberNotConfigured: *member* is not declared in that group here.
        ArchiveMemberRefused: an archive member's path or link target would
            land outside the member's worktree — refused before it is
            written.
    """
    group = _find_group(groups, group_name)
    if group is None:
        raise GroupNotConfigured(group_name)

    member_cfg = _find_member(group, member)
    if member_cfg is None:
        raise MemberNotConfigured(group_name, member)

    from ..group.manifest import workspace_dir
    from ..provision.reconcile import _worktree_path
    from .worktree import ArchiveMemberEscaped, extract_archive

    wt_path = _worktree_path(group_name, slug, member, env=env)

    try:
        extract_archive(archive_stream, wt_path)
    except ArchiveMemberEscaped as e:
        raise ArchiveMemberRefused(member, str(e)) from e

    ws_dir = workspace_dir(group_name, slug, env=env)
    append_marker(ws_dir, phase="worktree", outcome="ok")

    return {
        "contract_version": RECEIVE_CONTRACT_VERSION,
        "member": member,
    }
