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

import os
import stat
import subprocess
import sys
import tarfile
import tempfile
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
    "escaping_members",
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


def _mutation_confined(scratch_root: Path, target: Path) -> bool:
    """True when *target*'s parent directory, resolved through whatever
    symlink an earlier mutation (the seed, or an earlier replayed member)
    may have planted along the way, is still inside *scratch_root*.

    Checked immediately before every filesystem mutation `escaping_members`
    performs (`mkdir`, `unlink`, `symlink`), independently of the
    confinement gate above — a defense-in-depth layer for the case a gate
    verdict and the mutation it guards ever drift apart. Resolves only the
    PARENT, deliberately never the target itself: the target may not exist
    yet (the common case, about to be created), and when it does already
    exist as a symlink, resolving it here would silently follow that final
    component instead of treating it as the thing being replaced.
    """
    try:
        real_parent = os.path.realpath(target.parent)
        scratch_real = os.path.realpath(scratch_root)
    except OSError:
        return False
    return real_parent == scratch_real or real_parent.startswith(scratch_real + os.sep)


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


def _refuse_escaping_member(dest_root: Path, member: tarfile.TarInfo) -> None:
    """The one refusal gate: `_check_member` unconditionally, then
    `tarfile.data_filter` as an additional layer when the running
    interpreter provides it, translated to `ArchiveMemberEscaped`. Both `extract_archive` (the peer) and
    `escaping_members` (the sender) call this same function against their
    own resolved root, so what one end refuses is exactly what the other
    end would have refused too — see the module docstring for why
    `data_filter` catches a case `_check_member` alone does not (an
    absolute symlink target, even one that happens to sit inside the
    root).
    """
    _check_member(dest_root, member)
    data_filter = getattr(tarfile, "data_filter", None)
    if data_filter is not None:
        try:
            data_filter(member, str(dest_root))
        except Exception as exc:  # noqa: BLE001 - translate to our own type
            raise ArchiveMemberEscaped(member.name, str(exc)) from exc


_SPECIAL_STAT_TYPES = {
    stat.S_ISFIFO: tarfile.FIFOTYPE,
    stat.S_ISCHR: tarfile.CHRTYPE,
    stat.S_ISBLK: tarfile.BLKTYPE,
}


def _seed_scratch_from_tip(scratch_root: Path, worktree: Path, tip: str) -> bool:
    """Populate *scratch_root* with the tree *tip* resolves to in *worktree*'s
    own git history — the state the peer's `history` phase has ALREADY
    checked out before its `worktree` phase ever extracts anything.
    Directories, symlinks (with their targets read from the blob, never
    followed), and every tracked regular file as an empty placeholder —
    metadata only, matching the real peer's checkout in *shape* without ever
    reading a tracked file's actual content. A gitlink (a tracked submodule
    commit) is skipped; nothing here models nested repositories.

    Returns `False` on any git failure (not a repository, *tip* does not
    resolve, a corrupt object) — the caller treats that exactly like an
    unwalkable worktree, never as an empty tree.
    """
    listing = subprocess.run(
        ["git", "-C", str(worktree), "ls-tree", "-r", "-t", "-z", tip],
        capture_output=True,
        text=True,
    )
    if listing.returncode != 0:
        return False

    for entry in listing.stdout.split("\0"):
        if not entry:
            continue
        meta, _, rel_path = entry.partition("\t")
        try:
            mode, obj_type, sha = meta.split(" ")
        except ValueError:
            return False
        target = scratch_root.joinpath(*rel_path.split("/"))

        if obj_type == "tree":
            if not _mutation_confined(scratch_root, target):
                return False
            target.mkdir(parents=True, exist_ok=True)
        elif obj_type == "blob" and mode == "120000":
            blob = subprocess.run(
                ["git", "-C", str(worktree), "cat-file", "blob", sha],
                capture_output=True,
                text=True,
            )
            if blob.returncode != 0:
                return False
            if not _mutation_confined(scratch_root, target):
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.exists():
                target.unlink()
            os.symlink(blob.stdout, target)
        elif obj_type == "blob":
            if not _mutation_confined(scratch_root, target):
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch(exist_ok=True)
        # A gitlink ("commit" type) or anything else git ever emits here is
        # left unmaterialized — no archive member ever routes through a
        # submodule boundary this walk needs to model.

    return True


def escaping_members(
    worktree: Path, excluded: Sequence[str], tip: str | None = None
) -> tuple[str, ...] | None:
    """Every archive-member name the shared confinement gate
    (:func:`_refuse_escaping_member`) would refuse for *worktree*, evaluated
    by REPLAYING the peer's own extraction against a scratch root.

    The peer's `worktree` phase never extracts into an empty root: its
    `history` phase has already run `git worktree add` for *tip* — the
    commit this host's `history` phase ships for this member (the tip of
    the member's own workspace branch, `camp.transfer.history.send_history`'s
    *ref*) — so every file, directory, and symlink that commit's tree holds
    is already standing on disk before a single archive member is checked.
    *tip* (when given) seeds the scratch root with exactly that tree, read
    from git (:func:`_seed_scratch_from_tip`), before the replay below ever
    runs — metadata only, never a tracked file's content. `None` skips
    seeding: the scratch root starts empty, as if the peer's history phase
    had never run — never the case in production, where *tip* always names
    a real commit, but useful for isolating the replay's own ordering logic
    from git.

    A member `write_archive` would emit follows tarfile's own overwrite
    behaviour on top of that seed: a directory member is a no-op if the
    seed already created it there; a symlink member replaces whatever the
    seed left standing at that path (`os.unlink` before `os.symlink`,
    exactly as `tarfile.TarFile.makelink_with_filter` does on extraction).
    This is what surfaces a member the seed alone would have hidden — a
    tracked, unchanged symlink whose OWN path already resolves back to the
    destination root because the peer's checkout already put it there (the
    `data_filter` "resolves to the destination itself" guard,
    `tarfile._get_filtered_attrs`) — and it is also why a file the sender
    deleted only in its working tree (never committed) still stands on the
    peer via the seed, and a later member routing through it must be judged
    against that leftover, not against its absence.

    On top of the seed, each directory and each gate-accepted symlink
    `write_archive` would already have emitted earlier in the stream is
    materialized into the scratch root, in exactly the order `write_archive`
    emits members — top-level `sorted(worktree.iterdir())`, then
    depth-first per entry exactly as `tarfile.add`'s own recursion does
    (`sorted(os.listdir(...))`, `dereference=False`, the same `_is_excluded`
    filter). Each member is checked against the scratch state as it stands
    at that point in the replay — the same state the peer's own extraction
    root holds when it reaches that member, since the peer has already
    extracted everything earlier in the stream (on top of its own checkout)
    and nothing later. Resolving against an empty (or the real worktree's
    own) root instead misses a link whose target only escapes once an
    earlier, already-accepted sibling stands on disk — for example `sub/up
    -> ..` (accepted on its own) followed by `z -> sub/up/..`, which the
    peer refuses once `sub/up` is already extracted but a non-replaying
    evaluation reads as clean.

    The scratch root is named after *worktree*'s own resolved basename
    (never a random name), sitting under a fresh temporary parent — so a
    relative link that climbs out of the root and back in by the member's
    own directory name (`docs/x -> ../../<member>/README`) is judged exactly
    as the real peer judges it, where that same climb-and-reenter lands back
    inside because the peer's own root happens to carry that same name.

    Enumerates exactly the members `write_archive` would emit: the same
    traversal, and the same `_is_excluded` filter — an excluded directory is
    never descended into, so a large excluded tree is never even listed, let
    alone read. Every non-excluded entry `write_archive` would emit is
    checked through the same gate before anything for it is materialized —
    a directory, a regular file, a symlink, and a FIFO or a character or
    block device (the same `_UNSAFE_TYPES` `_check_member` refuses on the
    peer; `write_archive` emits all four as real archive members via
    `tarfile.add`'s own type detection, and `tarfile` itself silently drops
    a socket, so a socket is never a member to check). A refused member is
    recorded as an offender and never materialized in the scratch root, and
    its subtree — for a refused directory — is never walked: the real peer
    aborts its own extraction at the first member it refuses, so nothing
    beneath a refused directory is ever reached on the peer either. Nothing
    here ever opens a worktree file to read its contents — the seed step
    reads git objects instead, and only a symlink's own target, never a
    blob's data. A symlink loop already standing in the replay
    (`RuntimeError` from `Path.resolve()`) is treated as an offender rather
    than left to crash the walk.

    Every offender the replay finds is reported, not just the first — the
    peer aborts its own extraction at the first member it refuses, but the
    sender's job here is to never report a worktree clean that the peer
    would refuse, and reporting every offender at once is more useful to the
    operator than stopping early.

    Confinement is enforced twice, independently, for every mutation this
    replay performs (seeding the scratch root from *tip*, and materializing
    an accepted directory or symlink): first by the shared gate above
    (:func:`_refuse_escaping_member`, resolved against the scratch root as
    it stands at that point in the replay), and again immediately before
    the filesystem call itself (`_mutation_confined`, which re-resolves the
    mutation's parent directory through `os.path.realpath` and refuses the
    member if that parent no longer lands inside the scratch root). The
    second check exists because a directory replacing a symlink the seed
    (or an earlier accepted member) already stood up must never have its
    `mkdir`, `unlink`, or `symlink` call silently follow that symlink and
    mutate the filesystem outside the scratch root — the gate's own
    resolution and the mutation that follows it must never be allowed to
    drift apart.

    Returns `None` when *worktree* itself could not be walked — missing, not
    a directory, an `OSError` or symlink-loop `RuntimeError` partway
    through, or (when *tip* is given) a git failure reading it — never an
    empty tuple for any of these, so an unwalkable worktree can never read
    as clean. A leftover from an earlier, failed transfer attempt that the
    peer's checkout does not (and never did) hold is invisible here by
    construction: this walk only ever sees what *tip*'s tree records, never
    the peer's actual, possibly-stale disk state.
    """
    try:
        dest_root = worktree.resolve()
    except (OSError, RuntimeError):
        return None
    if not dest_root.is_dir():
        return None

    excluded_parts = _excluded_path_parts(excluded)
    offenders: list[str] = []

    def _check(scratch_root: Path, arcname: str, member_info: tarfile.TarInfo) -> bool:
        """Run the shared confinement gate for one replayed member. Returns
        whether the member is accepted; a refused member is appended to
        *offenders* here, once, regardless of its type — every member type
        this walk replays (directory, regular file, symlink, special) goes
        through this same gate before anything for it is materialized."""
        try:
            _refuse_escaping_member(scratch_root, member_info)
        except (ArchiveMemberEscaped, RuntimeError):
            offenders.append(arcname)
            return False
        return True

    def _mkdir_or_refuse(scratch_root: Path, scratch_path: Path, arcname: str) -> bool:
        if not _mutation_confined(scratch_root, scratch_path):
            offenders.append(arcname)
            return False
        try:
            scratch_path.mkdir()
        except FileExistsError:
            # Mirrors `tarfile.TarFile.makedir`'s own overwrite behaviour:
            # a directory member is a no-op if the path is already a
            # directory (following a symlink to one, exactly as the real
            # peer's `makedir` does) — never unlinked and recreated.
            if not scratch_path.is_dir():
                offenders.append(arcname)
                return False
        return True

    def _symlink_or_refuse(scratch_root: Path, scratch_path: Path, linkname: str, arcname: str) -> None:
        if not _mutation_confined(scratch_root, scratch_path):
            offenders.append(arcname)
            return
        if scratch_path.is_symlink() or scratch_path.exists():
            scratch_path.unlink()
        os.symlink(linkname, scratch_path)

    def _walk(
        real_dir: Path, scratch_dir: Path, rel_parts: tuple[str, ...], scratch_root: Path
    ) -> None:
        for name in sorted(os.listdir(real_dir)):
            arcname = "/".join((*rel_parts, name))
            if _is_excluded(arcname, excluded_parts):
                continue
            real_path = real_dir / name
            scratch_path = scratch_dir / name

            if real_path.is_symlink():
                linkname = os.readlink(real_path)
                member_info = tarfile.TarInfo(name=arcname)
                member_info.type = tarfile.SYMTYPE
                member_info.linkname = linkname
                # Materialized only when accepted: a refused symlink is
                # recorded as an offender and left unmaterialized, exactly
                # as the peer never writes a member it refuses. An
                # accepted symlink is still materialized before the walk
                # moves on, since a later sibling's own target may route
                # through it, and the peer's real extraction root holds it
                # too by the time it reaches that later member.
                if _check(scratch_root, arcname, member_info):
                    _symlink_or_refuse(scratch_root, scratch_path, linkname, arcname)
                continue

            entry_mode = real_path.lstat().st_mode
            if stat.S_ISDIR(entry_mode):
                member_info = tarfile.TarInfo(name=arcname)
                member_info.type = tarfile.DIRTYPE
                # A directory member is gated exactly like every other
                # type, before it is ever mkdir'd — a directory replacing
                # a tracked symlink the seed already stood up in the
                # scratch root is refused right here, and its subtree is
                # never walked: the peer aborts extraction at the first
                # member it refuses, so nothing beneath a refused
                # directory is ever reached on the peer either.
                if _check(scratch_root, arcname, member_info) and _mkdir_or_refuse(
                    scratch_root, scratch_path, arcname
                ):
                    _walk(real_path, scratch_path, (*rel_parts, name), scratch_root)
            elif stat.S_ISREG(entry_mode):
                # Never materialized — a regular file's content is never
                # read by this walk (see the module docstring) — but still
                # gated: a directory ancestor that the gate accepted may
                # still resolve differently once its own accepted siblings
                # stand in the scratch root, and a regular file's own path
                # can surface that the same way a symlink's can.
                member_info = tarfile.TarInfo(name=arcname)
                member_info.type = tarfile.REGTYPE
                _check(scratch_root, arcname, member_info)
            else:
                for predicate, tar_type in _SPECIAL_STAT_TYPES.items():
                    if predicate(entry_mode):
                        member_info = tarfile.TarInfo(name=arcname)
                        member_info.type = tar_type
                        _check(scratch_root, arcname, member_info)
                        break

    try:
        with tempfile.TemporaryDirectory() as scratch_parent:
            scratch_root = (Path(scratch_parent) / dest_root.name).resolve()
            scratch_root.mkdir()
            if tip is not None:
                if not _seed_scratch_from_tip(scratch_root, worktree, tip):
                    return None
            _walk(dest_root, scratch_root, (), scratch_root)
    except OSError:
        return None

    return tuple(offenders)


#: `data_filter`'s own mode mask (strips setuid/setgid/sticky and
#: group/other write bits), applied here unconditionally rather than by
#: depending on `data_filter` itself — see the module docstring: `data_filter`
#: did not exist at all on Python 3.11.0-3.11.3, so a peer on one of those
#: releases must still get this sanitization.
_MODE_MASK = 0o755


def _sanitize_member(member: tarfile.TarInfo) -> None:
    """Strip setuid/setgid/sticky and group/other write bits from *member*'s
    mode, and clear its archive-supplied ownership fields, in place —
    mirroring `tarfile.data_filter`'s sanitization but applied unconditionally
    (see `_MODE_MASK`)."""
    member.mode &= _MODE_MASK
    member.uid = 0
    member.gid = 0
    member.uname = ""
    member.gname = ""


def extract_archive(fileobj: BinaryIO, dest_root: Path) -> None:
    """Extract a tar stream produced by :func:`write_archive` into
    *dest_root*.

    See the module docstring for the confinement rule and why it is
    implemented unconditionally rather than via `tarfile.data_filter`. The
    same posture applies to mode sanitization: every member's mode is masked
    to `_MODE_MASK` and its uid/gid/uname/gname are cleared before
    extraction, unconditionally — never left to depend on `data_filter`
    being present. Reads incrementally — a member is checked and written
    before the next is read off the stream.

    Raises:
        ArchiveMemberEscaped: a member's path or link target would land
            outside *dest_root* — refused before that member is written.
    """
    dest_root = dest_root.resolve()
    dest_root.mkdir(parents=True, exist_ok=True)

    with tarfile.open(fileobj=fileobj, mode="r|") as tf:
        for member in tf:
            _refuse_escaping_member(dest_root, member)
            _sanitize_member(member)
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
