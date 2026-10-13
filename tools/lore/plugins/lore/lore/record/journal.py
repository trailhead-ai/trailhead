"""Crash-safe write of a record's body and sidecar as one unit.

A record is two files. Renaming them one after the other leaves a window in
which a hard crash (power cut, ``kill -9``) strands a new body beside an old
sidecar. :func:`write_pair` closes it with an undo journal:

  1. stage both new files as fsync'd temp siblings;
  2. write and fsync a journal holding the prior bytes (or absence) of each file
     and the SHA-256 of each file's before and after image;
  3. rename both into place;
  4. remove the journal.

A crash before step 3 finishes is rolled back by :func:`recover_vault`, which
``locking`` runs on every fresh vault-scope lock acquisition (this module
registers the hook on import). Recovery restores the before image of each file
only when its current bytes match the journal's before or after hash; a file that
matches neither was changed outside lore after the crash and is left alone with a
warning. Rolling back is the "not at all" outcome: a crashed command never
reported success.

**Guarantee and boundary.** After a crash, lore's next operation that is a writer
or one of the ``record show``, ``record history`` and ``session show`` readers
restores the pair before anything is read or written: every writer recovers on
taking the lock, and those readers take the lock to recover when a journal is
pending. The guarantee stops there. Other read paths (``lore vault add``'s index
scan, for one) take no lock and can see a torn pair until the next writer runs. A
concurrent reader with no journal pending takes no lock and can run between the
two renames and see the new body beside the old sidecar, and a reader outside lore
(Outpost reading files) can see the torn pair from a hard crash until the next lore
operation on that vault. ``move_record``'s cross-vault copy is out of scope and
keeps its two renames.

**Where it lives.** ``state_dir("lore")/journal/<sha256 of the resolved vault
root>/<sha256 of the record id>.json``, outside the vault tree so sync can never
commit it; directory 0700, files 0600. A journal directory that is a symlink, is
not owned by the current user, or (for the per-vault directory) has any group or
other mode bits is refused, for writes and for recovery alike, with a named
``VaultRecoveryError``; journal files are opened ``O_NOFOLLOW``, must be owned by
the current user, and are discarded as unreadable above a fixed size. The journal
stores the record id only; paths are re-derived from it, must resolve inside the
vault root (writing and recovering), symlinked record files are refused
(``O_NOFOLLOW``), and a journal recorded for a different root is skipped.

**Rollback path.** Reverting the commits that introduced this module restores
the two-rename write. A leftover journal directory is inert and safe to delete
(``rm -rf "$XDG_STATE_HOME/lore/journal"``).

This module imports nothing from ``lore.record``; it depends only on
``locking`` for the lock and the state directory.
"""

from __future__ import annotations

import base64
import hashlib
import glob
import json
import os
import stat
import sys
from pathlib import Path

from .. import locking
from ..confine import realpath_is_descendant

_FILES = ("body", "sidecar")

_MAX_JOURNAL_BYTES = 64 * 1024 * 1024


def _is_mine(st: os.stat_result) -> bool:
    return st.st_uid == os.geteuid()


def _unsafe(what: Path, why: str) -> locking.VaultRecoveryError:
    return locking.VaultRecoveryError(
        f"lore: refusing to use the write journal {what}: {why}; "
        "this is what blocks the vault: inspect it, then remove it to clear the block "
        "once the layout is fixed, and re-run — nothing was written through it"
    )


def _check_dir(path: Path, *, owner_only_mode: bool) -> None:
    """Refuse *path* unless it is a real directory this user owns (and, if asked, 0700)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _unsafe(path, str(exc)) from exc
    if stat.S_ISLNK(st.st_mode):
        raise _unsafe(path, "it is a symlink")
    if not stat.S_ISDIR(st.st_mode):
        raise _unsafe(path, "it is not a directory")
    if not _is_mine(st):
        raise _unsafe(path, "it is not owned by the current user")
    if owner_only_mode and st.st_mode & 0o077:
        raise _unsafe(
            path, f"its mode {stat.S_IMODE(st.st_mode):04o} lets other users in (expected 0700)"
        )


def _prepare_journal_dir(jdir: Path) -> None:
    """Create (if needed) and vet the journal directory and its parent for a write."""
    parent = jdir.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _check_dir(parent, owner_only_mode=False)
    os.chmod(parent, 0o700)
    try:
        jdir.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _check_dir(jdir, owner_only_mode=True)


def _vet_existing_journal_dir(jdir: Path) -> bool:
    """False when there is no journal directory; raises when one exists but is unsafe."""
    try:
        _check_dir(jdir.parent, owner_only_mode=False)
        _check_dir(jdir, owner_only_mode=True)
    except FileNotFoundError:
        return False
    return True


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def journal_dir(vault_root: str | Path, *, env: dict[str, str] | None = None) -> Path:
    """The journal directory for *vault_root* (resolved, then hashed)."""
    key = _sha(str(Path(vault_root).resolve()).encode())
    return locking.lore_state_dir(env=env) / "journal" / key


def has_pending(vault_root: str | Path) -> bool:
    """True when a journal file exists for *vault_root* (no lock taken)."""
    d = journal_dir(vault_root)
    try:
        return any(d.iterdir())
    except OSError:
        return False


def _check_id(record_id: str) -> tuple[str, str]:
    if not record_id or "\x00" in record_id or "/" not in record_id:
        raise ValueError(f"malformed record id {record_id!r}")
    kind, name = record_id.split("/", 1)
    segments = [kind, name, *Path(name).parts]
    if os.path.isabs(kind) or os.path.isabs(name) or any(s in ("", ".", "..") for s in segments):
        raise ValueError(f"illegal record id {record_id!r}")
    return kind, name


def _record_paths(root: Path, record_id: str) -> dict[str, Path]:
    kind, name = _check_id(record_id)
    paths = {"body": root / kind / f"{name}.md", "sidecar": root / kind / f"{name}.json"}
    root_real = os.path.realpath(root)
    # The directory chain is confined; a symlinked record file itself is never
    # followed (O_NOFOLLOW on read, os.replace swaps the link rather than its target).
    for p in paths.values():
        if not realpath_is_descendant(p.parent, root_real):
            raise locking.VaultRecoveryError(
                f"lore: {record_id} resolves outside the vault ({root}); "
                "a symlinked kind directory is not followed — nothing was written"
            )
    return paths


def _read_nofollow(path: Path) -> bytes | None | str:
    """Current bytes, ``None`` when absent, or ``"unsafe"`` for a symlink / non-regular file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        return "unsafe"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return "unsafe"
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read()
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _stage(path: Path, data: bytes) -> Path:
    """Write *data* to a fsync'd temp sibling of *path* and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.pair.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o666)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        _unlink(tmp)
        raise
    return tmp


def _unlink(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _journal_file(root: Path, record_id: str) -> Path:
    return journal_dir(root) / f"{_sha(record_id.encode())[:32]}.json"


def _write_journal(jfile: Path, payload: dict) -> None:
    jdir = jfile.parent
    fd = os.open(jfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
        fh.flush()
        os.fsync(fh.fileno())
    _fsync_dir(jdir)


def _remove_journal(jfile: Path) -> None:
    os.unlink(jfile)
    _fsync_dir(jfile.parent)


def write_pair(vault_root: str | Path, record_id: str, body: str, sidecar: str) -> None:
    """Write *body* and *sidecar* text for *record_id* together or not at all.

    The caller holds the vault's write lock. On any failure after the journal
    exists the prior state is restored before the exception propagates.
    """
    root = Path(vault_root).resolve()
    paths = _record_paths(root, record_id)
    _prepare_journal_dir(journal_dir(root))
    new = {"body": body.encode("utf-8"), "sidecar": sidecar.encode("utf-8")}

    staged: dict[str, Path] = {}
    jfile = _journal_file(root, record_id)
    journalled = False
    try:
        for which in _FILES:
            staged[which] = _stage(paths[which], new[which])
        files = {}
        for which in _FILES:
            prior = _read_nofollow(paths[which])
            if prior == "unsafe":
                raise OSError(f"{paths[which]} is not a regular file")
            files[which] = {
                "before": None if prior is None else base64.b64encode(prior).decode("ascii"),
                "before_sha": None if prior is None else _sha(prior),
                "after_sha": _sha(new[which]),
            }
        try:
            _write_journal(jfile, {"root": str(root), "id": record_id, "files": files})
        except BaseException:
            _unlink(jfile)
            raise
        journalled = True
        for which in _FILES:
            os.replace(staged[which], paths[which])
        for d in {p.parent for p in paths.values()}:
            _fsync_dir(d)
        _remove_journal(jfile)
    except BaseException:
        for tmp in staged.values():
            _unlink(tmp)
        if journalled:
            _recover_one(root, jfile, quiet=True)
        raise


def _warn(msg: str) -> None:
    print(msg, file=sys.stderr)


def _read_journal(jfile: Path) -> str:
    """The journal's text; ``OSError`` for a symlink, a stranger's file or one over the cap."""
    fd = os.open(jfile, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not _is_mine(st):
            raise OSError(f"{jfile} is not a regular file owned by this user")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            data = fh.read(_MAX_JOURNAL_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > _MAX_JOURNAL_BYTES:
        raise OSError(f"{jfile} is larger than {_MAX_JOURNAL_BYTES} bytes")
    return data.decode("utf-8")


def _parse(jfile: Path):
    """The parsed journal, or ``None`` when it is unreadable or malformed."""
    try:
        data = json.loads(_read_journal(jfile))
        root, rid, files = data["root"], data["id"], data["files"]
        if not isinstance(root, str) or not isinstance(rid, str):
            return None
        _check_id(rid)
        parsed = {}
        for which in _FILES:
            f = files[which]
            before = f["before"]
            before_sha, after_sha = f["before_sha"], f["after_sha"]
            if before is not None:
                before = base64.b64decode(before, validate=True)
                if _sha(before) != before_sha:
                    return None
            elif before_sha is not None:
                return None
            if not isinstance(after_sha, str):
                return None
            parsed[which] = (before, before_sha, after_sha)
        return root, rid, parsed
    except Exception:
        return None


def _restore_file(path: Path, before: bytes | None) -> None:
    if before is None:
        os.unlink(path)
        return
    tmp = _stage(path, before)
    try:
        os.replace(tmp, path)
    except BaseException:
        _unlink(tmp)
        raise


def _recover_one(root: Path, jfile: Path, *, quiet: bool = False) -> None:
    parsed = _parse(jfile)
    if parsed is None:
        _warn(
            f"lore: discarded an unreadable write journal ({jfile.name}) without "
            "restoring anything; if it named a record, check that record for a "
            "half-finished write"
        )
        _unlink(jfile)
        return
    recorded_root, rid, files = parsed
    if recorded_root != str(root):
        return
    try:
        paths = _record_paths(root, rid)
    except locking.VaultRecoveryError as exc:
        raise locking.VaultRecoveryError(
            f"{exc}; the pending write journal {jfile} is what blocks this vault: "
            "inspect it, then remove that file to clear the block once the layout is fixed"
        ) from exc

    current = {which: _read_nofollow(paths[which]) for which in _FILES}
    for which in _FILES:
        before, before_sha, after_sha = files[which]
        cur = current[which]
        if cur == "unsafe":
            matches = False
        else:
            cur_sha = None if cur is None else _sha(cur)
            matches = cur_sha == before_sha or cur_sha == after_sha
        if not matches:
            _warn(
                f"lore: {rid} changed after an interrupted write; "
                "left as it is — the write did not land and was not rolled back"
            )
            _finish(paths, jfile)
            return

    try:
        for which in _FILES:
            before, before_sha, _after = files[which]
            cur = current[which]
            if (None if cur is None else _sha(cur)) != before_sha:
                _restore_file(paths[which], before)
        for d in {p.parent for p in paths.values()}:
            _fsync_dir(d)
    except OSError as exc:
        raise locking.VaultRecoveryError(
            f"lore: could not roll back the interrupted write to {rid}: {exc}; "
            "fix the cause and re-run — the journal is kept"
        ) from exc
    _finish(paths, jfile)
    if not quiet:
        _warn(f"lore: recovered an interrupted write to {rid}; it did not land — re-run it")


def _finish(paths: dict[str, Path], jfile: Path) -> None:
    for p in paths.values():
        try:
            for tmp in p.parent.glob(f"{glob.escape(p.name)}.pair.*.tmp"):
                if tmp.is_file() and not tmp.is_symlink():
                    _unlink(tmp)
        except OSError:
            pass
    try:
        _remove_journal(jfile)
    except OSError as exc:
        raise locking.VaultRecoveryError(
            f"lore: could not clear the write journal {jfile.name}: {exc}"
        ) from exc


def recover_vault(vault_root: str | Path) -> None:
    """Roll back every interrupted write journalled for *vault_root*."""
    root = Path(vault_root).resolve()
    jdir = journal_dir(root)
    if not _vet_existing_journal_dir(jdir):
        return
    try:
        jfiles = sorted(jdir.iterdir())
    except OSError:
        return
    for jfile in jfiles:
        _recover_one(root, jfile)


def recover_if_pending(vault_root: str | Path) -> None:
    """Take the vault lock (which recovers) only when a journal is pending."""
    if has_pending(vault_root):
        with locking.vault_write_lock(vault_root):
            pass


locking.register_on_acquire(recover_vault)
