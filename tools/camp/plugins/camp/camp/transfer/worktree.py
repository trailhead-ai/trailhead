"""`camp transfer-receive worktree` — one member's working-tree content,
crossing directly from sender to peer.

**Sender side** (:func:`send_worktree`): a stdlib `tarfile` stream of the
member's worktree, omitting `.git` (the worktree's git linkage is
host-specific — see `camp.transfer.receive`'s `history` phase, which
materializes it fresh on this host) and every path the member declares in
`excluded`. The producer is THIS module run as a standalone script
(:func:`build_archive_argv` invokes `sys.executable <this file> <worktree>
<excluded...>`) — a caller-owned producer, exactly the shape
`camp.host.transport.stream_camp` requires, mirroring
`camp.transfer.history`'s `git bundle create` producer. Stream mode
(`tarfile.open(mode="w|")`) writes each member as it is added, so the sender
never buffers the whole archive before sending the first byte.

**Peer side** (:func:`extract_archive`, called from
`camp.transfer.receive.worktree`): extracts the incoming stream into the
member's worktree, refusing — before anything from that member is written —
any member whose path or symlink/hardlink target would land outside the
extraction root: an absolute path, a `..` segment, a real symlink already on
disk that routes the resolved path outside, an archived symlink or hardlink
whose target escapes, or a device/special file. This confinement check is
implemented explicitly and unconditionally rather than delegated to
`tarfile.data_filter` — that filter did not exist at all on Python
3.11.0-3.11.3, so a peer on one of those releases would otherwise extract
completely unguarded. `tarfile.data_filter` is consulted as an ADDITIONAL
layer only when the running interpreter provides it, never as the only
check. Extraction reads the stream incrementally (`tarfile.open(mode="r|")`)
one member at a time, so a worktree larger than the process's memory budget
never requires buffering the archive.

No field this module's sender side sends is ever trusted by the peer to name
a path — `extract_archive`'s *dest_root* is resolved by the caller from this
host's own group config, continuing `camp.transfer.probe`'s stated posture.
"""

from __future__ import annotations

import sys
import tarfile
import warnings
from pathlib import Path
from typing import BinaryIO, Sequence

# `build_archive_argv` below runs THIS file as a standalone script
# (`python3 <this file> <worktree> <excluded...>`) — `stream_camp` needs a
# real subprocess to read from, mirroring `camp.transfer.history`'s `git
# bundle create` producer. A script has no package context, so the relative
# imports a few lines down (needed only by the sender/peer functions, not by
# the standalone `write_archive` path `__main__` actually uses) would
# otherwise fail before `__main__` is even reached. Mirrors `cli/camp`'s own
# plugin-root bootstrap; a no-op when this module is imported normally.
if __package__ in (None, ""):
    _plugin_root = Path(__file__).resolve().parent.parent.parent
    if str(_plugin_root) not in sys.path:
        sys.path.insert(0, str(_plugin_root))
    __package__ = "camp.transfer"

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
    "build_archive_argv",
    "write_archive",
    "send_worktree",
    "ArchiveMemberEscaped",
    "extract_archive",
]

#: Always omitted, on top of whatever the member declares in `excluded` — the
#: worktree's git linkage is host-specific by construction (see the module
#: docstring) and is materialized fresh on the peer by the `history` phase.
_ALWAYS_EXCLUDED = (".git",)


def _excluded_path_parts(excluded: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    return tuple(Path(entry).parts for entry in (*_ALWAYS_EXCLUDED, *excluded))


def _is_excluded(member_name: str, excluded_parts: tuple[tuple[str, ...], ...]) -> bool:
    """True when *member_name* IS one of the excluded paths, or lies beneath one."""
    parts = Path(member_name).parts
    return any(parts[: len(ex)] == ex for ex in excluded_parts)


def write_archive(worktree: Path, excluded: Sequence[str], fileobj: BinaryIO) -> None:
    """Stream a tar of *worktree*'s content into *fileobj*.

    Omits `.git` and every path in *excluded* (or beneath one) — a directory
    the filter excludes is never descended into, so a large excluded tree
    (the common case: a provisioned `.venv` or `node_modules`) is never even
    read, not merely dropped after being read.
    """
    excluded_parts = _excluded_path_parts(excluded)

    def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        if _is_excluded(tarinfo.name, excluded_parts):
            return None
        return tarinfo

    with tarfile.open(fileobj=fileobj, mode="w|", dereference=False) as tf:
        for entry in sorted(worktree.iterdir()):
            tf.add(str(entry), arcname=entry.name, filter=_filter)


def build_archive_argv(worktree: Path, excluded: Sequence[str]) -> list[str]:
    """The exact argv for the sender-side producer: this module, run as a
    standalone script, writing :func:`write_archive`'s stream to stdout.

    A separate process (never a thread in the caller) because `stream_camp`
    requires a real `subprocess.Popen` with `stdout=PIPE` it can drain
    concurrently with feeding the remote invocation — see the module's own
    `if __name__ == "__main__"` block below.
    """
    return [sys.executable, str(Path(__file__).resolve()), str(worktree), *excluded]


def send_worktree(
    host: Host,
    *,
    group: str,
    slug: str,
    member: str,
    worktree: Path,
    excluded: Sequence[str],
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    server_alive_interval: float = DEFAULT_SERVER_ALIVE_INTERVAL_SECONDS,
    server_alive_count_max: int = DEFAULT_SERVER_ALIVE_COUNT_MAX,
    extra_ssh_options: Sequence[str] = (),
    spawn: StreamSpawner = default_stream_spawner,
    producer_spawn: ProducerSpawner = default_producer_spawn,
) -> TransportOutcome:
    """Stream *worktree*'s content, minus `.git` and *excluded*, into
    `camp transfer-receive worktree` on *host*.

    Returns whatever `stream_camp` classifies the invocation as — a failed
    producer surfaces as `ProducerFailed`, never as a successful transfer of
    a truncated archive.
    """
    remote_argv = [
        "transfer-receive",
        "worktree",
        "--group",
        group,
        "--slug",
        slug,
        "--member",
        member,
    ]
    producer = producer_spawn(build_archive_argv(worktree, excluded))
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


# ---------------------------------------------------------------------------
# Peer side — confinement-checked extraction.
# ---------------------------------------------------------------------------


class ArchiveMemberEscaped(Exception):
    """One archive member's path, or its symlink/hardlink target, would land
    outside the extraction root.

    Raised before that member is written to disk. Members already extracted
    from earlier in the stream are not rolled back — this is a defense
    against a hostile or corrupted archive, not a transactional guarantee
    over an archive `write_archive` itself produced, which never emits an
    escaping member.
    """

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"archive member {name!r} refused: {reason}")
        self.name = name
        self.reason = reason


_UNSAFE_TYPES = {tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE}


def _confine(dest_root: Path, path: Path, *, member_name: str, reason: str) -> None:
    try:
        path.resolve().relative_to(dest_root)
    except ValueError:
        raise ArchiveMemberEscaped(member_name, reason) from None


def _check_member(dest_root: Path, member: tarfile.TarInfo) -> None:
    if Path(member.name).is_absolute():
        raise ArchiveMemberEscaped(member.name, "absolute path")

    _confine(
        dest_root,
        dest_root / member.name,
        member_name=member.name,
        reason="path resolves outside the worktree",
    )

    if member.type in _UNSAFE_TYPES:
        raise ArchiveMemberEscaped(member.name, "device or special file")

    if member.issym():
        link_target = (dest_root / member.name).parent / member.linkname
        _confine(
            dest_root,
            link_target,
            member_name=member.name,
            reason="symlink target escapes the worktree",
        )
    elif member.islnk():
        # A hardlink's linkname is itself a path within the archive (rooted
        # at the extraction root), not relative to the current member's
        # parent directory — the one case here whose target isn't resolved
        # against the member's own directory.
        _confine(
            dest_root,
            dest_root / member.linkname,
            member_name=member.name,
            reason="hard link target escapes the worktree",
        )


def extract_archive(fileobj: BinaryIO, dest_root: Path) -> None:
    """Extract a tar stream produced by :func:`write_archive` into
    *dest_root*.

    See the module docstring for the confinement rule and why it is
    implemented unconditionally rather than via `tarfile.data_filter`. Reads
    incrementally — a member is checked and written before the next is
    read off the stream.

    Raises:
        ArchiveMemberEscaped: a member's path or link target would land
            outside *dest_root* — refused before that member is written.
    """
    dest_root = dest_root.resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    data_filter = getattr(tarfile, "data_filter", None)

    with tarfile.open(fileobj=fileobj, mode="r|") as tf:
        for member in tf:
            _check_member(dest_root, member)
            if data_filter is not None:
                try:
                    data_filter(member, str(dest_root))
                except Exception as exc:  # noqa: BLE001 - translate to our own type
                    raise ArchiveMemberEscaped(member.name, str(exc)) from exc
            with warnings.catch_warnings():
                # Deliberate: extract() is called with no `filter=` on
                # purpose (see the module docstring — the 3.11.0-3.11.3
                # floor lacks that parameter entirely), not an oversight
                # the newer default-filter warning should flag.
                warnings.filterwarnings("ignore", category=DeprecationWarning, module="tarfile")
                tf.extract(member, path=dest_root, set_attrs=True)


def _cli_main(argv: Sequence[str]) -> None:
    write_archive(Path(argv[0]), tuple(argv[1:]), sys.stdout.buffer)


if __name__ == "__main__":
    _cli_main(sys.argv[1:])
