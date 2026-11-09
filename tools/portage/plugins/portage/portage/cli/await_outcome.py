"""``portage await-outcome`` — wait on monitor's outcome file, print what it says.

``--path <file> --timeout <seconds>`` polls for a non-empty first line in the
file, for at most ``--timeout`` seconds (capped at 570, below the Bash tool's
600 s ceiling), then prints it parsed by ``parse_monitor_outcome``:
``<TOKEN> <reason>`` (just ``MERGED`` when there is no reason), or ``PENDING``
when the deadline passes with nothing there. It never writes and never globs.

Before reading it refuses a path that is a symlink or a non-regular file, and a
parent directory that is not owned by the current user or is group/world
writable (the caller creates that directory fresh, mode 0700).

Exit codes: 0 parsed outcome, 10 PENDING, 11 malformed line, 12 unreadable
(refused path or parent), 2 usage error (including a timeout above the cap).
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
import time
from pathlib import Path

from ..monitor_outcome import parse_monitor_outcome

MAX_TIMEOUT_SECONDS = 570
DEFAULT_INTERVAL_SECONDS = 2.0
_MAX_READ_BYTES = 4096

EXIT_PARSED, EXIT_USAGE, EXIT_PENDING, EXIT_MALFORMED, EXIT_UNREADABLE = 0, 2, 10, 11, 12


def add_await_outcome_subparser(sub) -> None:
    p = sub.add_parser(
        "await-outcome",
        help="Wait for monitor's outcome file and print its parsed outcome.",
        description=(
            "Poll --path for monitor's one-line outcome for at most --timeout seconds "
            f"(max {MAX_TIMEOUT_SECONDS}) and print `<TOKEN> <reason>`, or `PENDING` if none "
            "arrived. Writes nothing. Refuses a symlink, a non-regular file, or a parent "
            "directory not owned by you or writable by group/world.\n\n"
            "exit codes:\n"
            "  0  parsed outcome printed\n"
            "  10 PENDING (deadline passed, no line)\n"
            "  11 malformed outcome line\n"
            "  12 unreadable (refused path or parent)\n"
            "  2  usage error (e.g. timeout above the cap)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--path", required=True, help="Absolute path of the outcome file.")
    p.add_argument("--timeout", required=True, type=int, help="Seconds to wait.")
    p.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS,
                   help=f"Seconds between polls (default {DEFAULT_INTERVAL_SECONDS:g}).")
    p.set_defaults(func=cmd_await_outcome)


class _Refused(Exception):
    pass


def _check_parent(parent: Path) -> None:
    try:
        st = os.lstat(parent)
    except OSError as e:
        raise _Refused(f"cannot stat the outcome file's parent directory: {e.strerror}") from e
    if not stat.S_ISDIR(st.st_mode):
        raise _Refused("the outcome file's parent is not a directory")
    if st.st_uid != os.getuid():
        raise _Refused("the outcome file's parent directory is not owned by you")
    if st.st_mode & 0o022:
        raise _Refused("the outcome file's parent directory is group- or world-writable")


def _read_once(path: Path) -> str | None:
    """Return the file's text, or None when it does not exist yet."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise _Refused(f"refusing the outcome path (symlink or unreadable): {e.strerror}") from e
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _Refused("refusing the outcome path: not a regular file")
        return os.read(fd, _MAX_READ_BYTES).decode("utf-8", errors="replace")
    finally:
        os.close(fd)


def cmd_await_outcome(args: argparse.Namespace) -> int:
    if args.timeout < 0 or args.timeout > MAX_TIMEOUT_SECONDS:
        print(
            f"portage: --timeout must be between 0 and {MAX_TIMEOUT_SECONDS} seconds "
            "(the Bash tool's ceiling is 600)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    path = Path(args.path)
    deadline = time.monotonic() + args.timeout
    try:
        _check_parent(path.parent)
        while True:
            text = _read_once(path)
            if text is not None and text.strip():
                token, argument = parse_monitor_outcome(text)
                if token is None:
                    print(
                        "portage: malformed monitor outcome: the first line is not a known "
                        "outcome word followed by a reason",
                        file=sys.stderr,
                    )
                    return EXIT_MALFORMED
                print(f"{token} {argument}".rstrip())
                return EXIT_PARSED
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("PENDING")
                return EXIT_PENDING
            time.sleep(min(args.interval, remaining))
    except _Refused as e:
        print(f"portage: {e}", file=sys.stderr)
        return EXIT_UNREADABLE
