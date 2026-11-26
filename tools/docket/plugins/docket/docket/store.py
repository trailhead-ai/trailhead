"""The docket store: the only code that reads or writes docket data.

Layout, all inside the default vault (the `lore vault ls --json` entry whose scope is "default"):

    <vault>/sites/docket/lists/<vault name>.json   one list per vault: {"version": 1, "entries": [...]}
    <vault>/sites/docket/vault-order.json          {"version": 1, "order": [vault name, ...]}
    <vault>/.git/docket/                           lock file and staging files (never synced)

An entry is {"kind", "slug"} plus an optional {"workspace": {"group", "slug"}}; its vault is the
vault the file is named for. Nothing else is stored.

Reads are strict. A file is at most 256 KiB, is opened with O_NOFOLLOW (as is every directory
below the vault root), must be a regular file, and must be UTF-8 JSON with no duplicate keys, no
NaN or Infinity, no unknown keys, version 1, no duplicate entries and only safe values. A file
holding git conflict markers is refused with the file named and the vault to resolve it in. A
file with a higher version is refused as written by a newer docket and is never rewritten. Reads
take no lock: a write replaces the whole file atomically.

Writes go through one path, `Store.update_list` / `Store.update_order` (`write_*` are the
replace-everything forms of them). Each takes the host lock, reads the current file strictly
(so an unreadable file is refused, never overwritten), applies the caller's mutation, and, only
if the result differs, writes a temp file in the staging directory, fsyncs it, `os.replace`s it
over the target and fsyncs the target directory. The staging directory sits in the vault's git
directory: on the same filesystem as the target, so the replace is atomic; outside anything sync
commits; and mode 0700. An interrupted write leaves the old file intact.

The lock is an flock on `.git/docket/lock`, polled with LOCK_NB until a deadline and then
refused with a named error. It is host-local; across hosts, sync settles conflicts.

Confinement: no API takes a path. Write targets are only `lists/<validated vault name>.json` and
`vault-order.json`, and every name and entry value is validated before it reaches a path or a file.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, NamedTuple, Sequence

from docket.errors import DocketError

FORMAT_VERSION = 1
MAX_FILE_BYTES = 256 * 1024
DEFAULT_LOCK_DEADLINE = 30.0
_LOCK_POLL_SECONDS = 0.05

_VAULT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_KIND = re.compile(r"[a-z][a-z0-9-]{0,31}")
_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_CONFLICT_MARKER = re.compile(r"^(<<<<<<<|>>>>>>>)( |$)", re.MULTILINE)

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class StoreError(DocketError):
    """Any refusal by the store."""


class UnsafeNameError(StoreError):
    """A vault name, kind, slug or workspace part that is not safe to store or use in a path."""


class UnreadableListError(StoreError):
    """A docket file that cannot be read strictly. It is never overwritten."""


class ConflictedFileError(UnreadableListError):
    """A docket file holding git conflict markers."""


class NewerVersionError(UnreadableListError):
    """A docket file written by a newer docket; this one must not read or rewrite it."""


class ListTooLargeError(StoreError):
    """A list whose file would exceed the read cap."""


class LockTimeoutError(StoreError):
    """The host lock stayed held past the deadline."""


class StagingDirError(StoreError):
    """The staging and lock directory is missing its parent, not a real directory, or unsafe."""


def _check(pattern: re.Pattern[str], value: Any, what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise UnsafeNameError(f"{what} {value!r} is not safe to store")
    return value


def validate_vault_name(name: Any) -> str:
    return _check(_VAULT_NAME, name, "vault name")


def validate_kind(kind: Any) -> str:
    return _check(_KIND, kind, "kind")


def validate_slug(slug: Any) -> str:
    return _check(_SLUG, slug, "slug")


@dataclass(frozen=True)
class Workspace:
    group: str
    slug: str

    def __post_init__(self) -> None:
        _check(_VAULT_NAME, self.group, "workspace group")
        _check(_SLUG, self.slug, "workspace slug")


@dataclass(frozen=True)
class Entry:
    kind: str
    slug: str
    workspace: Workspace | None = None

    def __post_init__(self) -> None:
        validate_kind(self.kind)
        validate_slug(self.slug)

    @property
    def key(self) -> tuple[str, str]:
        """(kind, slug): what identifies the entry within its vault's list."""
        return (self.kind, self.slug)


@dataclass(frozen=True)
class DocketList:
    entries: tuple[Entry, ...] = ()


class Change(NamedTuple):
    before: Any
    after: Any

    @property
    def changed(self) -> bool:
        return self.before != self.after


def default_runner(argv: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=60)


def locate_default_vault(runner: Runner = default_runner) -> Path:
    """The path of the vault whose scope is "default"; its name is not guaranteed to be "default"."""
    proc = runner(["lore", "vault", "ls", "--json"])
    if proc.returncode != 0:
        raise StoreError(f"cannot list vaults: {(proc.stderr or '').strip() or 'lore failed'}")
    try:
        vaults = json.loads(proc.stdout)
    except ValueError as exc:
        raise StoreError(f"cannot list vaults: lore printed unparseable output ({exc})") from exc
    if not isinstance(vaults, list):
        raise StoreError("cannot list vaults: lore did not print a list")
    defaults = [v for v in vaults if isinstance(v, dict) and v.get("scope") == "default"]
    if len(defaults) != 1:
        raise StoreError(f"expected exactly one default vault, lore lists {len(defaults)}")
    path = defaults[0].get("path")
    if not isinstance(path, str) or not os.path.isabs(path):
        raise StoreError("the default vault has no absolute path")
    return Path(path)


def resolve_order(stored: Iterable[str], with_list: Iterable[str]) -> list[str]:
    """The effective vault order: the stored order, then the other vaults that have a list, alphabetically."""
    order = list(stored)
    seen = set(order)
    return order + sorted(set(with_list) - seen)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed")


def _exact_keys(obj: Any, required: set[str], optional: set[str], what: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError(f"{what} must be an object")
    unknown = set(obj) - required - optional
    if unknown:
        raise ValueError(f"{what} has unknown key {sorted(unknown)[0]!r}")
    missing = required - set(obj)
    if missing:
        raise ValueError(f"{what} is missing {sorted(missing)[0]!r}")
    return obj


def _parse_entry(raw: Any) -> Entry:
    obj = _exact_keys(raw, {"kind", "slug"}, {"workspace"}, "entry")
    workspace = None
    if "workspace" in obj:
        ws = _exact_keys(obj["workspace"], {"group", "slug"}, set(), "workspace")
        workspace = Workspace(ws["group"], ws["slug"])
    return Entry(obj["kind"], obj["slug"], workspace)


def _parse_list(doc: dict[str, Any]) -> DocketList:
    _exact_keys(doc, {"version", "entries"}, set(), "list")
    entries_raw = doc["entries"]
    if not isinstance(entries_raw, list):
        raise ValueError("entries must be an array")
    entries = tuple(_parse_entry(e) for e in entries_raw)
    keys = [e.key for e in entries]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate entries")
    return DocketList(entries)


def _parse_order(doc: dict[str, Any]) -> tuple[str, ...]:
    _exact_keys(doc, {"version", "order"}, set(), "order")
    order = doc["order"]
    if not isinstance(order, list):
        raise ValueError("order must be an array")
    names = tuple(validate_vault_name(n) for n in order)
    if len(set(names)) != len(names):
        raise ValueError("duplicate entries in the order")
    return names


def entry_doc(entry: Entry) -> dict[str, Any]:
    """An entry as it is stored: {"kind", "slug"} plus {"workspace": {"group", "slug"}} when it has one."""
    doc: dict[str, Any] = {"kind": entry.kind, "slug": entry.slug}
    if entry.workspace is not None:
        doc["workspace"] = {"group": entry.workspace.group, "slug": entry.workspace.slug}
    return doc


def _serialize_list(lst: DocketList) -> dict[str, Any]:
    return {"version": FORMAT_VERSION, "entries": [entry_doc(e) for e in lst.entries]}


class Store:
    """Strict reads, atomic locked writes and the vault order for one default vault.

    `vault_root` is the default vault's directory (see `locate_default_vault`).
    `lock_deadline` (seconds), `clock`, `sleep` and `replace` are injectable: a test supplies a
    fake clock to run the lock poll without waiting, and a replace that stalls to interrupt a write.
    """

    def __init__(
        self,
        vault_root: Path,
        *,
        lock_deadline: float = DEFAULT_LOCK_DEADLINE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        replace: Callable[..., None] = os.replace,
    ) -> None:
        self.vault_root = Path(vault_root)
        self.lock_deadline = lock_deadline
        self._clock = clock
        self._sleep = sleep
        self._replace = replace
        self._lock_depth = 0

    # -- directory handling ------------------------------------------------------------------

    @contextmanager
    def _dir(self, parts: Sequence[str], *, create: bool = False, mode: int = 0o755,
             refusal: type[StoreError] = StoreError) -> Iterator[int | None]:
        """Open `parts` below the vault root, each with O_NOFOLLOW; None if one is missing and not created."""
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        try:
            fd = os.open(self.vault_root, flags)
        except OSError as exc:
            raise StoreError(f"cannot open the default vault {self.vault_root}: {exc.strerror}") from exc
        try:
            for part in parts:
                try:
                    child = os.open(part, flags | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        yield None
                        return
                    try:
                        os.mkdir(part, mode, dir_fd=fd)
                    except FileExistsError:
                        pass
                    except OSError as exc:
                        raise self._dir_refusal(refusal, part, OSError(exc.errno, exc.strerror)) from exc
                    child = self._open_child(fd, part, flags, refusal)
                except OSError as exc:
                    raise self._dir_refusal(refusal, part, exc) from exc
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    def _open_child(self, fd: int, part: str, flags: int, refusal: type[StoreError]) -> int:
        try:
            return os.open(part, flags | os.O_NOFOLLOW, dir_fd=fd)
        except OSError as exc:
            raise self._dir_refusal(refusal, part, exc) from exc

    def _dir_refusal(self, refusal: type[StoreError], part: str, exc: OSError) -> StoreError:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            return refusal(f"{self.vault_root}/.../{part} is not a real directory (a symlink or a file); refusing to use it")
        return refusal(f"cannot open or create {part} in {self.vault_root}: {exc.strerror}")

    @contextmanager
    def _stage(self) -> Iterator[int]:
        """The staging and lock directory <vault>/.git/docket, created 0700, never a symlink."""
        git = self.vault_root / ".git"
        if not git.is_dir() or git.is_symlink():
            raise StagingDirError(f"{self.vault_root} has no .git directory to hold docket's lock and staging files")
        with self._dir((".git", "docket"), create=True, mode=0o700, refusal=StagingDirError) as fd:
            assert fd is not None
            st = os.fstat(fd)
            if st.st_uid != os.geteuid():
                raise StagingDirError(f"{git / 'docket'} is owned by another user; refusing to use it")
            if stat.S_IMODE(st.st_mode) != 0o700:
                os.fchmod(fd, 0o700)
            yield fd

    # -- lock --------------------------------------------------------------------------------

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the host lock; re-entrant on this Store so a read-modify-write can span several calls."""
        if self._lock_depth:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return
        with self._stage() as stage_fd:
            try:
                fd = os.open("lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             0o600, dir_fd=stage_fd)
            except OSError as exc:
                raise StagingDirError(f"cannot open the docket lock file in {self.vault_root}/.git/docket: "
                                      f"{exc.strerror}") from exc
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise StagingDirError("the docket lock file is not a regular file")
                deadline = self._clock() + self.lock_deadline
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if self._clock() >= deadline:
                            raise LockTimeoutError(
                                f"timed out after {self.lock_deadline:g}s waiting for the docket lock "
                                f"({self.vault_root}/.git/docket/lock); another docket command on this host holds it"
                            ) from None
                        self._sleep(_LOCK_POLL_SECONDS)
                self._lock_depth = 1
                try:
                    yield
                finally:
                    self._lock_depth = 0
            finally:
                os.close(fd)

    # -- strict read -------------------------------------------------------------------------

    def _read_file(self, parts: Sequence[str], name: str) -> tuple[bytes | None, Path]:
        path = self.vault_root.joinpath(*parts, name)
        with self._dir(parts) as dfd:
            if dfd is None:
                return None, path
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
            except FileNotFoundError:
                return None, path
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise UnreadableListError(f"{path} is a symlink; docket will not read or write through it") from exc
                raise UnreadableListError(f"{path} cannot be opened: {exc.strerror}") from exc
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise UnreadableListError(f"{path} is not a regular file")
            with os.fdopen(fd, "rb") as fh:
                data = fh.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise UnreadableListError(f"{path} is larger than 256 KiB; refusing to read it")
        return data, path

    def _decode(self, data: bytes, path: Path) -> dict[str, Any]:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnreadableListError(f"{path} is not valid UTF-8 ({exc.reason})") from exc
        if _CONFLICT_MARKER.search(text):
            raise ConflictedFileError(
                f"{path} has unresolved git conflict markers; resolve the conflict in the vault "
                f"{self.vault_root} and run docket again"
            )
        try:
            doc = json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
        except ValueError as exc:
            raise UnreadableListError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(doc, dict):
            raise UnreadableListError(f"{path} must hold a JSON object")
        version = doc.get("version")
        if isinstance(version, int) and not isinstance(version, bool) and version > FORMAT_VERSION:
            raise NewerVersionError(
                f"{path} was written by a newer docket (format version {version}); update trailhead. "
                "It will not be rewritten."
            )
        if isinstance(version, bool) or version != FORMAT_VERSION:
            raise UnreadableListError(f"{path} has an unsupported version {version!r}; expected {FORMAT_VERSION}")
        return doc

    def read_list(self, vault: str) -> DocketList:
        """The vault's list; empty when the file does not exist. Refuses anything unreadable."""
        validate_vault_name(vault)
        data, path = self._read_file(("sites", "docket", "lists"), f"{vault}.json")
        if data is None:
            return DocketList(())
        doc = self._decode(data, path)
        try:
            return _parse_list(doc)
        except (ValueError, UnsafeNameError) as exc:
            raise UnreadableListError(f"{path} is invalid: {exc}") from exc

    def read_order(self) -> tuple[str, ...]:
        """The stored vault order; empty when the file does not exist."""
        data, path = self._read_file(("sites", "docket"), "vault-order.json")
        if data is None:
            return ()
        doc = self._decode(data, path)
        try:
            return _parse_order(doc)
        except (ValueError, UnsafeNameError) as exc:
            raise UnreadableListError(f"{path} is invalid: {exc}") from exc

    def list_vaults(self) -> list[str]:
        """Names of the vaults that have a list file, alphabetically."""
        with self._dir(("sites", "docket", "lists")) as dfd:
            if dfd is None:
                return []
            names = os.listdir(dfd)
        out = []
        for name in names:
            stem = name[: -len(".json")] if name.endswith(".json") else None
            if stem is not None and _VAULT_NAME.fullmatch(stem):
                out.append(stem)
        return sorted(out)

    def effective_order(self) -> list[str]:
        return resolve_order(self.read_order(), self.list_vaults())

    # -- atomic write ------------------------------------------------------------------------

    def _write_file(self, parts: Sequence[str], name: str, payload: bytes) -> None:
        if len(payload) > MAX_FILE_BYTES:
            raise ListTooLargeError(f"{name} would be {len(payload)} bytes, over the 256 KiB limit; not written")
        with self._dir(parts, create=True) as dfd, self._stage() as sfd:
            assert dfd is not None
            tmp = f"{name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=sfd)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                try:
                    self._replace(tmp, name, src_dir_fd=sfd, dst_dir_fd=dfd)
                except OSError as exc:
                    if exc.errno == errno.EXDEV:
                        raise StagingDirError("the vault's .git directory and sites folder are on different "
                                              "filesystems; docket cannot replace files atomically") from exc
                    raise
            except BaseException:
                try:
                    os.unlink(tmp, dir_fd=sfd)
                except FileNotFoundError:
                    pass
                raise
            os.fsync(dfd)

    @staticmethod
    def _encode(doc: dict[str, Any]) -> bytes:
        return (json.dumps(doc, indent=2) + "\n").encode("utf-8")

    def update_list(self, vault: str, mutate: Callable[[DocketList], DocketList]) -> Change:
        """Read-modify-write one vault's list under the host lock; writes only when `mutate` changed it."""
        validate_vault_name(vault)
        with self.locked():
            before = self.read_list(vault)
            after = mutate(before)
            exists = (self.vault_root / "sites" / "docket" / "lists" / f"{vault}.json").exists()
            if after != before or not exists:
                self._write_file(("sites", "docket", "lists"), f"{vault}.json",
                                 self._encode(_serialize_list(after)))
            return Change(before, after)

    def write_list(self, vault: str, lst: DocketList) -> Change:
        return self.update_list(vault, lambda _current: lst)

    def update_order(self, mutate: Callable[[tuple[str, ...]], Iterable[str]]) -> Change:
        with self.locked():
            before = self.read_order()
            after = tuple(validate_vault_name(n) for n in mutate(before))
            if len(set(after)) != len(after):
                raise UnsafeNameError("a vault appears twice in the order")
            exists = (self.vault_root / "sites" / "docket" / "vault-order.json").exists()
            if after != before or not exists:
                self._write_file(("sites", "docket"), "vault-order.json",
                                 self._encode({"version": FORMAT_VERSION, "order": list(after)}))
            return Change(before, after)

    def write_order(self, order: Iterable[str]) -> Change:
        names = tuple(order)
        return self.update_order(lambda _current: names)
