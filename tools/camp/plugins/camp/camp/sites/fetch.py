"""`camp site-fetch` — pull one remote workspace site into a fresh local directory.

This is the viewing host's half of fetching a site from another host. It runs
`camp site-export` on the named remote through the raw-bytes transport,
streams the archive into a scratch file, validates every member, extracts into
a scratch directory, and renames that directory into place.

Contract:

- stdout carries exactly one JSON object on every path:
  ``{"ok": true, "dest": "<dir>"}`` with exit 0, or
  ``{"ok": false, "reason": "<code>", "message": "<words>"}`` with exit
  `EXIT_FAILURE`. Argparse usage errors are reported the same way, as
  ``invalid-name``. Messages are camp's own words; remote text never reaches them.
- Every name is full-matched against its grammar (``$`` accepts a trailing
  newline) before the hosts file, the destination, or the transport is touched.
- ``dest`` must not exist (a dangling symlink counts) and its parent must.
  Scratch lives under that parent, so the final rename never crosses a
  filesystem. On any failure ``dest`` is absent and all scratch is removed.
- The archive is untrusted. Only members whose type is exactly a regular file
  or a directory are accepted, with relative names whose segments are never
  empty, ``.``, ``..`` or backslash-bearing; any other member refuses the whole
  archive before a byte is extracted, as does any GNU sparse PAX header and a
  declared total of member data above the archive byte cap. The limits are
  enforced inside tarfile's own header parse (a `TarInfo` subclass, members read
  one at a time), so each header is judged by exactly the parse that uses it:
  a global PAX header, an oversized or over-numerous extended header, a run of
  more than `MAX_EXTENSION_CHAIN` extension headers, a PAX key outside
  ``_ALLOWED_PAX_KEYS`` and more than `MAX_ARCHIVE_MEMBERS` members are all
  ``refused-archive``; a header tarfile itself cannot read is
  ``transfer-failed``. Extraction then uses tarfile's ``data`` filter as a
  second layer. The fetched directory is mode 0700. A stream that is short,
  lacks the end-of-archive marker, or came from an exporter that exited
  non-zero is ``transfer-failed``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import tarfile
import tempfile
from typing import NoReturn

from ..host import transport
from .export import EXIT_NOT_FOUND, GROUP_RE, SITE_RE, SLUG_RE

#: Exit status for every ``ok: false`` outcome.
EXIT_FAILURE = 1

#: The most archive bytes accepted from the remote. Larger streams are cut off
#: and reported as ``archive-too-large``.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024

#: What a remote camp older than ``site-export`` prints for it: the dispatcher's
#: bare-slug refusal.
_UNKNOWN_VERB_TEXT = "bare slug dispatch is no longer supported"

#: The most data one extended header (PAX ``x``, GNU long name ``L`` / link
#: ``K``) may carry.
MAX_EXTENDED_HEADER_BYTES = 8 * 1024

#: The most extended-header data an archive may carry in total.
MAX_EXTENDED_TOTAL_BYTES = 4 * 1024 * 1024

#: The most extension headers that may run in a row before a real member.
MAX_EXTENSION_CHAIN = 2

#: The PAX keys an archive may carry. ``camp site-export`` emits ``path`` (for
#: names tarfile cannot fit in the ustar name field) and ``mtime`` (for a file
#: time the ustar field cannot hold); every other key, including ``size`` and
#: ``GNU.sparse.*``, changes how tarfile frames or expands a member and is
#: refused.
_ALLOWED_PAX_KEYS = frozenset({"path", "mtime"})

_EXTENSION_TYPES = (tarfile.XHDTYPE, tarfile.SOLARIS_XHDTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)

#: The most members an archive may hold.
MAX_ARCHIVE_MEMBERS = 20_000

_HOST_RE = re.compile(r"[a-z0-9][a-z0-9-]*")

_TAR_BLOCK = 512
_END_MARKER_BYTES = 2 * _TAR_BLOCK

#: The transport spawner; tests replace it to run a local child instead of ssh.
_spawn = transport.default_fetch_spawner


class _Failure(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _Failure("invalid-name", f"bad arguments: {message}")


def _emit(doc: dict) -> None:
    sys.stdout.write(json.dumps(doc) + "\n")
    sys.stdout.flush()


def _parse(args: list[str]) -> argparse.Namespace:
    parser = _Parser(prog="camp site-fetch", add_help=False, allow_abbrev=False)
    for flag in ("from", "group", "slug", "site", "dest", "timeout"):
        parser.add_argument(f"--{flag}", dest=flag, required=True)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(args)


def _validate(ns: argparse.Namespace) -> float:
    for label, value, rx in (
        ("host", getattr(ns, "from"), _HOST_RE),
        ("group", ns.group, GROUP_RE),
        ("slug", ns.slug, SLUG_RE),
        ("site", ns.site, SITE_RE),
    ):
        if not rx.fullmatch(value):
            raise _Failure("invalid-name", f"{label} {value!r} is not a valid name")
    try:
        timeout = float(ns.timeout)
    except ValueError:
        timeout = math.nan
    if not (math.isfinite(timeout) and timeout > 0):
        raise _Failure("invalid-name", f"timeout {ns.timeout!r} must be a positive number of seconds")
    if not ns.dest or "\0" in ns.dest:
        raise _Failure("invalid-name", "dest is not a usable path")
    return timeout


def _resolve_host(name: str) -> transport.Host:
    from ..host.config import HostConfigError, load_hosts

    try:
        hosts = load_hosts()
    except HostConfigError:
        raise _Failure("host-not-declared", "the camp hosts file could not be read") from None
    host = hosts.get(name)
    if host is None:
        raise _Failure("host-not-declared", f"host {name!r} is not declared in the camp hosts file")
    return host


def _check_dest(dest: str) -> str:
    dest = os.path.abspath(dest)
    parent = os.path.dirname(dest)
    if os.path.lexists(dest):
        raise _Failure("dest-exists", "the destination already exists")
    if not os.path.isdir(parent):
        raise _Failure("invalid-name", "the destination's parent directory does not exist")
    return dest


def _map_outcome(outcome: transport.TransportOutcome) -> None:
    if isinstance(outcome, transport.Delivered):
        return
    if isinstance(outcome, transport.TooLarge):
        raise _Failure("archive-too-large", f"the site archive is larger than {outcome.max_bytes} bytes")
    if isinstance(outcome, transport.StoppedResponding):
        raise _Failure("timed-out", "the remote host did not answer in time")
    if isinstance(outcome, transport.Unreachable):
        raise _Failure("unreachable", "the remote host could not be reached")
    if isinstance(outcome, transport.CampNotResolvable):
        raise _Failure("camp-unavailable-remote", "camp could not be run on the remote host")
    if isinstance(outcome, transport.CredentialsRefused):
        raise _Failure("auth-refused", "the remote host refused this host's credentials")
    if isinstance(outcome, (transport.IdentityUnknown, transport.IdentityChanged)):
        raise _Failure("auth-refused", "the remote host's identity is not trusted by this host")
    if isinstance(outcome, transport.RemoteRefusal):
        if outcome.exit_code == EXIT_NOT_FOUND:
            raise _Failure("not-found", "the remote host has no such site")
        if _UNKNOWN_VERB_TEXT in outcome.stderr:
            raise _Failure("remote-camp-outdated", "the remote camp is too old to export sites")
    raise _Failure("transfer-failed", "the site could not be fetched from the remote host")


def _refuse(why: str) -> NoReturn:
    raise _Failure("refused-archive", f"the archive was refused: {why}")


def _check_member(member: tarfile.TarInfo) -> None:
    if member.type not in (tarfile.REGTYPE, tarfile.DIRTYPE) or member.issparse():
        _refuse("it holds a member that is neither a plain file nor a directory")
    name = member.name
    if not name or name.startswith("/") or "\\" in name or "\0" in name:
        _refuse("it holds a member with an unsafe name")
    segments = name[:-1].split("/") if name.endswith("/") else name.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        _refuse("it holds a member with an unsafe name")


class _Limits:
    """The caps one extraction enforces, and the running totals it checks them against."""

    def __init__(
        self, extended_header: int, extended_total: int, chain: int, members: int, archive_size: int, complete: bool
    ) -> None:
        self.archive_size = archive_size
        self.complete = complete
        self.last_data = -1
        self.extended_header = extended_header
        self.extended_total = extended_total
        self.chain = chain
        self.members = members
        self.extended_bytes = 0
        self.run = 0
        self.seen = 0


def _limited_tarinfo(limits: _Limits) -> type[tarfile.TarInfo]:
    """A `TarInfo` that judges each header inside tarfile's own parse.

    tarfile calls `_proc_member` on every header with the fields it will itself
    act on, before it reads that header's payload, so a refusal happens as the
    bytes are reached and cannot disagree with the parse that follows.
    """

    class LimitedTarInfo(tarfile.TarInfo):
        def _proc_member(self, tar):
            kind = self.type
            if self.size < 0:
                _refuse("it holds a member with a negative size")
            if kind == tarfile.XGLTYPE:
                _refuse("it holds a global extended header")
            if kind == tarfile.GNUTYPE_SPARSE:
                _refuse("it holds a member with GNU sparse headers")
            if kind in _EXTENSION_TYPES:
                limits.run += 1
                if limits.run > limits.chain:
                    _refuse("it holds too long a run of extension headers")
                if self.size > limits.extended_header:
                    _refuse("it holds an oversized extended header")
                limits.extended_bytes += self.size
                if limits.extended_bytes > limits.extended_total:
                    _refuse("it holds too much extended header data")
            else:
                limits.run = 0
                limits.seen += 1
                if limits.seen > limits.members:
                    _refuse("it holds too many members")
                member = super()._proc_member(tar)
                if member.offset_data <= limits.last_data:
                    _refuse("it holds a member whose data does not start after the previous member's")
                limits.last_data = member.offset_data
                if limits.complete and member.isreg() and member.offset_data + member.size > limits.archive_size:
                    _refuse("it holds a member whose data runs past the end of the archive")
                return member
            return super()._proc_member(tar)

        def _apply_pax_info(self, pax_headers, encoding, errors):
            if any(key not in _ALLOWED_PAX_KEYS for key in pax_headers):
                _refuse("it holds an extended header key camp does not accept")
            super()._apply_pax_info(pax_headers, encoding, errors)

        def _proc_gnusparse_00(self, *args):
            _refuse("it holds a member with GNU sparse headers")

        _proc_gnusparse_01 = _proc_gnusparse_10 = _proc_gnusparse_00

    return LimitedTarInfo


def _has_end_marker(archive: str) -> bool:
    size = os.path.getsize(archive)
    if size % _TAR_BLOCK or size < _END_MARKER_BYTES:
        return False
    with open(archive, "rb") as fh:
        fh.seek(size - _END_MARKER_BYTES)
        return not any(fh.read(_END_MARKER_BYTES))


def _extract(
    archive: str,
    into: str,
    max_extracted_bytes: int = MAX_ARCHIVE_BYTES,
    max_extended_header_bytes: int = MAX_EXTENDED_HEADER_BYTES,
    max_extended_total_bytes: int = MAX_EXTENDED_TOTAL_BYTES,
    max_extension_chain: int = MAX_EXTENSION_CHAIN,
    max_members: int = MAX_ARCHIVE_MEMBERS,
) -> None:
    limits = _Limits(
        max_extended_header_bytes,
        max_extended_total_bytes,
        max_extension_chain,
        max_members,
        os.path.getsize(archive),
        _has_end_marker(archive),
    )
    try:
        with tarfile.open(archive, mode="r:", tarinfo=_limited_tarinfo(limits)) as tar:
            members = []
            while (member := tar.next()) is not None:
                _check_member(member)
                members.append(member)
            if not _has_end_marker(archive):
                raise _Failure("transfer-failed", "the site archive ended early")
            if sum(m.size for m in members if m.isreg()) > max_extracted_bytes:
                raise _Failure("refused-archive", "the archive was refused: its members declare more data than it can hold")
            for member in members:
                tar.extract(member, into, filter="data")
    except _Failure:
        raise
    except tarfile.FilterError:
        raise _Failure("refused-archive", "the archive was refused: a member failed the extraction filter") from None
    except (tarfile.TarError, EOFError, OSError, RecursionError):
        raise _Failure("transfer-failed", "the site archive could not be read") from None


def fetch(ns: argparse.Namespace, timeout: float) -> str:
    """Fetch and place the site; return the destination path or raise `_Failure`."""
    host = _resolve_host(getattr(ns, "from"))
    dest = _check_dest(ns.dest)
    parent = os.path.dirname(dest)

    scratch = archive = None
    try:
        scratch = tempfile.mkdtemp(prefix=".site-fetch-", dir=parent)
        fd, archive = tempfile.mkstemp(prefix=".site-fetch-", suffix=".tar", dir=parent)
        remote_argv = ["site-export", f"--group={ns.group}", f"--slug={ns.slug}", f"--site={ns.site}"]
        with os.fdopen(fd, "wb") as out:
            outcome = transport.fetch_camp_bytes(
                host,
                remote_argv,
                out,
                max_bytes=MAX_ARCHIVE_BYTES,
                execution_timeout=timeout,
                spawner=_spawn,
            )
        _map_outcome(outcome)
        _extract(archive, scratch)
        os.chmod(scratch, 0o700)
        if os.path.lexists(dest):
            raise _Failure("dest-exists", "the destination already exists")
        os.rename(scratch, dest)
        return dest
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        if archive is not None:
            try:
                os.unlink(archive)
            except FileNotFoundError:
                pass


def run_cli(args: list[str]) -> None:
    """The `camp site-fetch` verb."""
    try:
        ns = _parse(args)
        timeout = _validate(ns)
        dest = fetch(ns, timeout)
    except _Failure as exc:
        _emit({"ok": False, "reason": exc.reason, "message": exc.message})
        sys.exit(EXIT_FAILURE)
    except Exception:  # noqa: BLE001 - the contract is one JSON object on every path
        _emit({"ok": False, "reason": "transfer-failed", "message": "the site could not be fetched"})
        sys.exit(EXIT_FAILURE)
    _emit({"ok": True, "dest": dest})
