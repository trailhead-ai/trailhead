#!/usr/bin/env python3
"""Maturity declaration writer — the inverse of `maturity_resolve.py`. Given
a repository's agent-instruction file and one vocabulary word, appends a
`## Project Maturity` section declaring that word and keeps the write only
when re-running `maturity_resolve.py` against the composed bytes reads back
`level: <that word>` / `reason: declared`. Every edge the writer cannot
enumerate ahead of time — a file ending inside an unclosed code fence, a
mis-detected heading — becomes a loud refusal rather than a silent
mis-declaration.

Usage:
    maturity_declare.py <agent-instruction-file> <level>

`<level>` is one of the closed vocabulary `prototype` / `early` /
`production`. `<agent-instruction-file>` is created (as a fresh file
carrying only the new section) when it does not already exist.

This is the ONLY thing in craft that writes a maturity declaration.
`maturity_resolve.py` is read-only and is never modified by this script;
it is imported here, unmodified, both for its closed vocabulary and its
fenced-block-aware heading extraction (so this writer's own "does a
declaration already exist" guard agrees exactly with what the resolver
itself will read back) and is also invoked as a subprocess for the
self-check, so a bug in the resolver's own sanitization or parsing is
caught by the same path a caller would hit, not bypassed by a shortcut
internal call. The self-check runs before the file is ever touched — it
resolves the composed in-memory bytes, not a re-read of anything written
— so no refusal path ever leaves a partial write behind.

Refuses, writing nothing, whenever an unfenced `## Project Maturity`
heading already exists in the file — this is the guard that keeps a
second heading (which the resolver reads as `ambiguous-value` →
`production`) from ever being created, and what makes the writer safe to
point at a file the operator authored. A heading found only inside a
fenced code block is not an existing declaration and does not trigger this
guard — craft's own skills carry illustrative fenced examples of this very
section.

The write is a compare-and-swap: the file is read once up front (the
"initial read"), and — after every other check passes — re-read a second
time immediately before the atomic replace, under an `flock` (on a
sibling `.maturity-declare.lock` file, so the target file itself is never
touched until the moment of the real write) held across that re-read and
the replace. If the bytes changed since the initial read, the write
refuses with `concurrent-modification` rather than clobbering whatever the other
writer just wrote — the losing session sees the refusal instead of a
silently discarded write.

If the target path is itself a symlink at the moment of that re-read, the
write refuses with `target-is-symlink` rather than following the link:
this script is always invoked against a concrete `<repo-root>/CLAUDE.md`,
so there is no legitimate reason for that path to be a symlink, and a
symlink there can only redirect the write to a file this invocation was
never authorized to touch (e.g. a sibling repository's own
agent-instruction file, silently changing what maturity level that other
repository declares). Because the writer never resolves through a
symlink, the lock is always keyed on the same path it ultimately writes
to — there is no separate "resolved target" for the lock to miss.

Stdout on success (exit 0), one stable line naming what was written:

    declared: <level>

Exit codes:
    0  written — the section was appended and its round-trip through
       `maturity_resolve.py` confirmed `level: <level>` / `reason:
       declared`. NEVER exits 0 without having performed the write.
    2  refused — nothing was written (or, for the already-declared race,
       nothing beyond what the other writer already committed), and
       stderr names a stable `reason-code:`:

       invalid-level          — `<level>` is outside the closed vocabulary.
       path-is-directory      — `<agent-instruction-file>` names a
                                 directory; nothing is created.
       invalid-utf8-file      — the file's existing bytes do not decode as
                                 UTF-8.
       already-declared       — an unfenced `## Project Maturity` heading
                                 already exists.
       target-is-symlink      — `<agent-instruction-file>` is itself a
                                 symlink; refused rather than followed.
       concurrent-modification
                               — the file's bytes changed between the
                                 initial read and the compare-and-swap
                                 re-read (a concurrent writer won the
                                 race) — distinct from `already-declared`:
                                 no `## Project Maturity` heading need be
                                 involved, only some other change to the
                                 file in that window.
       self-check-failed      — the composed bytes, re-resolved through
                                 `maturity_resolve.py`, did not read back
                                 as `level: <level>` / `reason: declared`
                                 (e.g. the file ends inside an unclosed
                                 fence that swallows the appended
                                 heading).
       write-failed           — the atomic replace itself failed (e.g.
                                 the filesystem refused it), the existing
                                 file could not be read (e.g. permission
                                 denied), or the sibling lock file could
                                 not be opened (e.g. a read-only parent
                                 directory) — the original file is left
                                 exactly as it was.
       usage-error            — called with the wrong number of
                                 arguments.

       No refusal path ever writes the file's existing content to stdout
       or stderr: the file is repo content an arbitrary contributor can
       author, and the refusal is read by an agent deciding what to do
       next, so this script's error type is structurally unable to carry
       it — mirroring `maturity_bars.py`'s `RenderError`.
"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from maturity_resolve import LEVELS, _extract_sections  # noqa: E402

_RESOLVER = _SCRIPT_DIR / "maturity_resolve.py"
_LOCK_SUFFIX = ".maturity-declare.lock"

_INVALID_LEVEL_REASON_CODE = "invalid-level"
_PATH_IS_DIRECTORY_REASON_CODE = "path-is-directory"
_INVALID_UTF8_FILE_REASON_CODE = "invalid-utf8-file"
_ALREADY_DECLARED_REASON_CODE = "already-declared"
_TARGET_IS_SYMLINK_REASON_CODE = "target-is-symlink"
_CONCURRENT_MODIFICATION_REASON_CODE = "concurrent-modification"
_SELF_CHECK_FAILED_REASON_CODE = "self-check-failed"
_WRITE_FAILED_REASON_CODE = "write-failed"
_USAGE_REASON_CODE = "usage-error"


def _err(msg: str) -> None:
    print(f"maturity-declare: {msg}", file=sys.stderr)


class DeclareError(Exception):
    """A fail-closed outcome: `reason_code` is always set. Never carries
    the file's own content — structurally unable to, mirroring
    `maturity_bars.py`'s `RenderError`."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def lock_path_for(path: Path) -> Path:
    """The sibling lock-file path used to guard the compare-and-swap for
    `path`, exposed so a test can drive a real interleaving against the
    same lock this script itself takes."""
    return path.parent / (path.name + _LOCK_SUFFIX)


def _compose(initial_text: str, level: str) -> str:
    rationale = f"This repository is declared at the {level} maturity level."
    text = initial_text
    if text and not text.endswith("\n"):
        text += "\n"
    if text:
        text += "\n"
    text += f"## Project Maturity\n\n{rationale}\n"
    return text


def _self_check(new_text: str, level: str) -> None:
    result = subprocess.run(
        [sys.executable, str(_RESOLVER)],
        input=new_text.encode("utf-8"),
        capture_output=True,
    )
    if result.returncode != 0:
        raise DeclareError(_SELF_CHECK_FAILED_REASON_CODE)
    lines = result.stdout.decode("utf-8", "replace").splitlines()
    got = dict(line.split(": ", 1) for line in lines if ": " in line)
    if got.get("level") != level or got.get("reason") != "declared":
        raise DeclareError(_SELF_CHECK_FAILED_REASON_CODE)


def _default_new_file_mode() -> int:
    """The mode a brand-new file would get from a plain `open()` call,
    honoring the process umask — never `tempfile.mkstemp`'s hardened
    0600, which is right for a throwaway temp file but wrong for the
    agent-instruction file a declare of a nonexistent path creates."""
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def _write_with_compare_and_swap(path: Path, initial_bytes: bytes, new_content: bytes) -> None:
    lock_path = lock_path_for(path)
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as e:
        raise DeclareError(_WRITE_FAILED_REASON_CODE) from e
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            current = path.read_bytes() if path.exists() else b""
            if current != initial_bytes:
                raise DeclareError(_CONCURRENT_MODIFICATION_REASON_CODE)

            # Refuse rather than follow: the caller always passes a concrete
            # `<repo-root>/CLAUDE.md`, so there is no legitimate symlink case
            # to preserve, and a symlink here can only redirect the write to
            # a file this invocation was never authorized to touch.
            if path.is_symlink():
                raise DeclareError(_TARGET_IS_SYMLINK_REASON_CODE)

            mode = (
                stat.S_IMODE(path.stat().st_mode) if path.exists() else _default_new_file_mode()
            )

            tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
            try:
                os.fchmod(tmp_fd, mode)
                with os.fdopen(tmp_fd, "wb") as f:
                    f.write(new_content)
                os.replace(tmp_name, str(path))
            except OSError as e:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise DeclareError(_WRITE_FAILED_REASON_CODE) from e
        except OSError as e:
            raise DeclareError(_WRITE_FAILED_REASON_CODE) from e
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        try:
            os.unlink(str(lock_path))
        except OSError:
            pass


def declare(path: Path, level: str) -> None:
    """Append a `## Project Maturity` declaration of `level` to `path`,
    creating it if absent. Raises `DeclareError` on any refusal; the file
    is guaranteed unchanged on every refusal path."""
    if level not in LEVELS:
        raise DeclareError(_INVALID_LEVEL_REASON_CODE)

    if path.exists() and path.is_dir():
        raise DeclareError(_PATH_IS_DIRECTORY_REASON_CODE)

    try:
        initial_bytes = path.read_bytes() if path.exists() else b""
    except OSError as e:
        raise DeclareError(_WRITE_FAILED_REASON_CODE) from e
    try:
        initial_text = initial_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise DeclareError(_INVALID_UTF8_FILE_REASON_CODE) from e

    if _extract_sections(initial_text):
        raise DeclareError(_ALREADY_DECLARED_REASON_CODE)

    new_text = _compose(initial_text, level)
    _self_check(new_text, level)

    _write_with_compare_and_swap(path, initial_bytes, new_text.encode("utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        _err("usage: maturity_declare.py <agent-instruction-file> <level>")
        _err(f"reason-code: {_USAGE_REASON_CODE}")
        return 2

    path = Path(argv[0])
    level = argv[1]

    try:
        declare(path, level)
    except DeclareError as e:
        _err(f"reason-code: {e.reason_code}")
        return 2

    print(f"declared: {level}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
