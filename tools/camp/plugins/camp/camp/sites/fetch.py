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
  archive before a byte is extracted. Extraction then uses tarfile's ``data``
  filter as a second layer. A stream that is short, lacks the end-of-archive
  marker, or came from an exporter that exited non-zero is ``transfer-failed``.
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

#: Exit status for every ``ok: false`` outcome.
EXIT_FAILURE = 1

#: The most archive bytes accepted from the remote. Larger streams are cut off
#: and reported as ``archive-too-large``.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024

#: What a remote camp older than ``site-export`` prints for it: the dispatcher's
#: bare-slug refusal.
_UNKNOWN_VERB_TEXT = "bare slug dispatch is no longer supported"

_HOST_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
_GROUP_RE = re.compile(r"[a-z0-9-]+")
_SLUG_RE = re.compile(r"[a-z0-9-]+")
_SITE_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")

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
        ("group", ns.group, _GROUP_RE),
        ("slug", ns.slug, _SLUG_RE),
        ("site", ns.site, _SITE_RE),
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
    from .export import EXIT_NOT_FOUND

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


def _check_member(member: tarfile.TarInfo) -> None:
    def refuse(why: str) -> NoReturn:
        raise _Failure("refused-archive", f"the archive was refused: {why}")

    if member.type not in (tarfile.REGTYPE, tarfile.DIRTYPE):
        refuse("it holds a member that is neither a plain file nor a directory")
    name = member.name
    if not name or name.startswith("/") or "\\" in name or "\0" in name:
        refuse("it holds a member with an unsafe name")
    segments = name[:-1].split("/") if name.endswith("/") else name.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        refuse("it holds a member with an unsafe name")


def _has_end_marker(archive: str) -> bool:
    size = os.path.getsize(archive)
    if size % _TAR_BLOCK or size < _END_MARKER_BYTES:
        return False
    with open(archive, "rb") as fh:
        fh.seek(size - _END_MARKER_BYTES)
        return not any(fh.read(_END_MARKER_BYTES))


def _extract(archive: str, into: str) -> None:
    try:
        with tarfile.open(archive, mode="r:") as tar:
            members = tar.getmembers()
            for member in members:
                _check_member(member)
            if not _has_end_marker(archive):
                raise _Failure("transfer-failed", "the site archive ended early")
            for member in members:
                tar.extract(member, into, filter="data")
    except _Failure:
        raise
    except tarfile.FilterError:
        raise _Failure("refused-archive", "the archive was refused: a member failed the extraction filter") from None
    except (tarfile.TarError, EOFError, OSError):
        raise _Failure("transfer-failed", "the site archive could not be read") from None


def fetch(ns: argparse.Namespace, timeout: float) -> str:
    """Fetch and place the site; return the destination path or raise `_Failure`."""
    host = _resolve_host(getattr(ns, "from"))
    dest = _check_dest(ns.dest)
    parent = os.path.dirname(dest)

    fd, archive = tempfile.mkstemp(prefix=".site-fetch-", suffix=".tar", dir=parent)
    scratch = tempfile.mkdtemp(prefix=".site-fetch-", dir=parent)
    try:
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
        os.chmod(scratch, 0o755)
        if os.path.lexists(dest):
            raise _Failure("dest-exists", "the destination already exists")
        os.rename(scratch, dest)
        return dest
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
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
