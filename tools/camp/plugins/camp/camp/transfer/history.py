"""`camp transfer-receive history` — one member's committed history, crossing
directly from sender to peer.

**Sender side** (:func:`send_history`): for one member, `git bundle create -`
of the workspace branch, negatived against the basis commit `begin` reported
the peer already holds (or full history when the peer reported `null`),
streamed via `camp.host.transport.stream_camp` into
`camp transfer-receive history` on the peer. The bundle is built with a git
subprocess this module owns (:func:`build_bundle_argv`) — a caller-owned
producer, exactly the shape `stream_camp` requires. Never a bare revision as
the ref to bundle: `git bundle create` accepts a REF NAME, and a bare rev
(`main~1`) is silently accepted here but fails on the receiving end with a
misleading "early EOF" — so *ref* below is always a branch name, never
resolved to a sha first.

No git remote is ever contacted on this path — `git bundle create` reads only
this host's local object store, and the transport is `stream_camp`'s direct
ssh pipe, never a fetch or push through the group's shared remote. That is
the reason this channel exists at all: a commit made on the sender's branch
and never pushed anywhere still crosses.

**Peer side** lives in `camp.transfer.receive.history` — this module carries
only the sending half, continuing `camp.transfer.receive`'s module docstring
posture: no field the peer's own config didn't resolve is ever used to build
a local path, and the peer answer is the sole input to a later `send_history`
call's `basis_commit`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from ..host.config import Host
from ..host.transport import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_SERVER_ALIVE_COUNT_MAX,
    DEFAULT_SERVER_ALIVE_INTERVAL_SECONDS,
    ProducerSpawner,
    StreamSpawner,
    TransportOutcome,
    default_producer_spawn,
    default_stream_spawner,
    stream_camp,
)

__all__ = [
    "ProducerSpawner",
    "default_producer_spawn",
    "InvalidBasisCommit",
    "build_bundle_argv",
    "send_history",
]

#: A git object id is hex, and shorter than a full sha1 (7-40) or sha256
#: (4-64) is still a legitimate abbreviation git itself would accept —
#: this only screens out something that cannot possibly be an object id
#: (an option-injection attempt, free text) before it reaches git's argv.
_BASIS_COMMIT_RE = re.compile(r"^[0-9a-f]{4,64}\Z")


class InvalidBasisCommit(ValueError):
    """*basis_commit* is neither `None` nor a plausible git object-id shape.

    Raised before the value ever reaches `git bundle create`'s own argv
    parsing, whose failure on a malformed value reads as a broken option
    rather than a bad basis commit.
    """

    def __init__(self, basis_commit: str) -> None:
        super().__init__(
            f"basis_commit {basis_commit!r} is not a plausible git object id "
            "(expected 4-64 lowercase hex characters) — refused before it "
            "reached git"
        )
        self.basis_commit = basis_commit


def _sender_holds_commit(repo_root: Path, basis_commit: str) -> bool:
    """True when *repo_root*'s own object store already resolves
    *basis_commit* to a commit — the prerequisite `git bundle create --not`
    needs to actually shrink the bundle rather than fail outright."""
    from ..gitutil import _git

    result = _git(repo_root, "cat-file", "-e", f"{basis_commit}^{{commit}}")
    return result.returncode == 0


def build_bundle_argv(repo_root: Path, ref: str, *, basis_commit: str | None) -> list[str]:
    """The exact `git bundle create -` argv for one member's *ref*.

    Negatived against *basis_commit* when given AND actually held by this
    host's own object store at *repo_root* (the peer already holds it —
    `begin`'s non-null answer for this member — but "the peer already
    holds it" is a claim about the PEER, not this host, and the two
    machines an operator alternates between routinely diverge in either
    direction). Full history otherwise — negativing is an optimization,
    never a correctness requirement, so a basis commit this host cannot
    resolve is silently dropped rather than hard-failing the bundle git
    itself cannot build. *ref* must be a branch name, never a bare
    revision — see the module docstring's gotcha.

    Raises:
        InvalidBasisCommit: *basis_commit* is not `None` and not a
            plausible git object-id shape.
    """
    argv = ["git", "-C", str(repo_root), "bundle", "create", "-", ref]
    if basis_commit:
        if not _BASIS_COMMIT_RE.match(basis_commit):
            raise InvalidBasisCommit(basis_commit)
        if _sender_holds_commit(repo_root, basis_commit):
            argv += ["--not", basis_commit]
    return argv


def send_history(
    host: Host,
    *,
    group: str,
    slug: str,
    member: str,
    repo_root: Path,
    ref: str,
    basis_commit: str | None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    server_alive_interval: float = DEFAULT_SERVER_ALIVE_INTERVAL_SECONDS,
    server_alive_count_max: int = DEFAULT_SERVER_ALIVE_COUNT_MAX,
    extra_ssh_options: Sequence[str] = (),
    spawn: StreamSpawner = default_stream_spawner,
    producer_spawn: ProducerSpawner = default_producer_spawn,
) -> TransportOutcome:
    """Stream one member's *ref*, bundled from *repo_root*, into
    `camp transfer-receive history` on *host*.

    Returns whatever `stream_camp` classifies the invocation as — a failed
    `git bundle create` (the producer) surfaces as `ProducerFailed`, never as
    a successful transfer of a truncated or empty stream.
    """
    remote_argv = [
        "transfer-receive",
        "history",
        "--group",
        group,
        "--slug",
        slug,
        "--member",
        member,
    ]
    producer = producer_spawn(build_bundle_argv(repo_root, ref, basis_commit=basis_commit))
    return stream_camp(
        host,
        remote_argv,
        producer,
        connect_timeout=connect_timeout,
        server_alive_interval=server_alive_interval,
        server_alive_count_max=server_alive_count_max,
        extra_ssh_options=extra_ssh_options,
        spawn=spawn,
    )
