#!/usr/bin/env python3
"""Check-list answer writer — saves one operator answer to one waiting pending check.

Usage:
    checklist_answer.py --vault <v> --spec spec/<n> --criterion ACn --slice task/<id>
                        --answer passed|failed --base <revision> --words-file <path>

The values are the ones `checklist_gather.py --json` prints for an entry (`vault`, `spec`,
`criterion`, `building_slice`, `revision`). `--words-file` holds the operator's words; they
never reach an argv. The file is the caller's to create and this script's to remove: it is
deleted on every outcome, refusals included. The path removed is the one the parser read; when
the arguments do not parse, the last `--words-file` in the argv. A failure to remove it changes
no outcome.

Order, which is the invariant:

    1. Every value is shape-checked before it enters an argv (`unsafe-name`; the revision
       must be 64 lowercase hex, because lore reads any `--base` string and reports a
       malformed one as stale). Lore is then probed for `record update --base` and
       `record amend --close` / `--base` (`lore-too-old`), before any read.
    2. The spec is read with `record show --json`. A shared-layer spec is refused
       (`shared-vault`: `show` XML-escapes its bodies); a revision other than `--base` is
       `stale`, and nothing is written. The `--base` is always the revision the operator was
       shown, never a fresh read's.
    3. The entry is found by (criterion, building slice): `entry-not-found`; a set-aside
       entry is `answer-not-pending-check`; a latest status other than `waiting` is
       `entry-not-waiting`.
    4. The words are cleaned: flattened to one line, control and format characters removed,
       refused rather than redacted when over `MAX_WORDS_CHARS` (`words-too-long`) or
       credential-shaped (`credential-shaped-words`; a tripwire, not a guarantee). Empty
       words on `failed` are `words-required`; on `passed` they record
       `passed on the check list (no note)`.
    5. Today is computed once (`waiting_gate._today`, which honours `CRAFT_WAITING_GATE_TODAY`)
       and used for both the appended line and the gate. The line
       `  - <today> <status>: <words>` is appended to the entry and the body is certified
       by `waiting_gate.certify_answer`; a gate refusal is printed with the gate's own code
       and exit code.
    6. `failed` is written with `lore record update --base`; `passed` with one
       `lore record amend --close <the matched entry's own criterion> --initiated-by operator
       --reason 'check passed on the check list' --base`. The reason is fixed text. When lore
       refuses the write (any non-zero exit, a timeout, a missing lore) the spec is read
       again: a revision other than `--base` is `stale`; an unchanged revision and, for
       `passed`, lore text saying the criterion `is already closed` or `cannot be closed` is
       `close-refused`; anything else is `write-failed`. Before that classification the re-read
       body is checked for a write that landed: the entry's last history line is the one just
       proposed. A landed write is a save.

Lore's own output is never printed. On success the first line is `saved: <ACn> <status>` and
the second is `revision: <rev>`, the spec's revision after the write, which the caller passes as
`--base` for its next answer on the same spec; exit 0. When the spec cannot be read again the
save stands and the revision line is left out.

Exit codes: 0 saved; 1 refused (`EXIT_1_CODES`, and the gate's violations); 2 could not
complete (`EXIT_2_CODES`, and the gate's could-not-certify codes). One `reason-code:` line
on stderr names the code. `REASON_CODES` lists every code this script can print.

Known boundaries: this is the only writer of a waiting status change that craft ships; a raw
`lore record update` skips it, as it skips the waiting gate. A lore that is missing or hangs
while probing or reading is `spec-unreadable`.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import followup_gather as gather  # noqa: E402
import markdown_section  # noqa: E402
import spec_write as sw  # noqa: E402
import waiting  # noqa: E402
import waiting_gate  # noqa: E402
from covers_gate import DuplicateHeadingError  # noqa: E402
from criterion_gate import _scrub_credential_shaped  # noqa: E402
from gate_plumbing import Refusal, emit_refusal  # noqa: E402

PROG = "checklist-answer"
MAX_WORDS_CHARS = 500
_MAX_WORDS_FILE_BYTES = 64 * 1024
_DEFAULT_PASSED_WORDS = "passed on the check list (no note)"
_CLOSE_REASON = "check passed on the check list"
_ANSWERS = ("passed", "failed")
_REVISION_RE = re.compile(r"[0-9a-f]{64}")
_CRITERION_RE = re.compile(r"AC[1-9][0-9]{0,8}")
_BARE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
_CLOSE_REFUSED_TEXTS = ("is already closed", "cannot be closed")
_REMOVED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})

EXIT_1_CODES = (
    "stale",
    "close-refused",
    "entry-not-found",
    "words-required",
    "words-too-long",
    "credential-shaped-words",
)
EXIT_2_CODES = (
    "unsafe-name",
    "shared-vault",
    "lore-too-old",
    "spec-unreadable",
    "write-failed",
    "invalid-arguments",
)
_GATE_CODES = (
    "removed-entry",
    "history-rewritten",
    "entry-changed",
    "masked-content",
    "credential-shaped-text",
    "control-characters",
    "field-too-long",
    "change-outside-waiting",
    "section-not-at-end",
    "malformed-prior-section",
    "unterminated-masked-region",
    "invalid-today",
)
REASON_CODES = tuple(
    dict.fromkeys(
        (*EXIT_1_CODES, *EXIT_2_CODES, *waiting_gate.ANSWER_REASON_CODES, *_GATE_CODES, *sorted(waiting.REASON_CODES))
    )
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise Refusal("invalid arguments — see the usage in the script docstring", "invalid-arguments", 2)


def _unsafe() -> Refusal:
    return Refusal("a value has a shape that cannot be used", "unsafe-name", 2)


def _bare(value: str, prefix: str) -> str:
    if not value.startswith(prefix):
        raise _unsafe()
    name = value[len(prefix):]
    if not _BARE_ID_RE.fullmatch(name) or not gather._shape_ok(name):
        raise _unsafe()
    return name


def _check_shapes(args) -> tuple[str, str]:
    """(spec name, bare building slice); refuses any value that is not safe to put in an argv."""
    if not gather._shape_ok(args.vault) or "/" in args.vault:
        raise _unsafe()
    spec = _bare(args.spec, "spec/")
    slice_ = _bare(args.slice, "task/")
    if not _CRITERION_RE.fullmatch(args.criterion) or not _REVISION_RE.fullmatch(args.base):
        raise _unsafe()
    return spec, slice_


def _probe(runner) -> None:
    for argv, tokens in (
        (["record", "update", "--help"], ("--base",)),
        (["record", "amend", "--help"], ("--close", "--base")),
    ):
        try:
            done = runner(argv, stdin="")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            raise Refusal("lore could not be run", "spec-unreadable", 2)
        if done.returncode != 0 or any(token not in done.stdout for token in tokens):
            raise Refusal(
                f"this lore's '{' '.join(argv[:2])}' lacks {' and '.join(tokens)}, so no guarded write can be made",
                "lore-too-old",
                2,
            )


def _read_spec(runner, vault: str, spec_id: str) -> tuple[str, str]:
    try:
        _, body, revision, layer = sw.read_record(runner, vault, spec_id, "spec-unreadable", personal_only=False)
    except Refusal:
        raise Refusal(f"{spec_id} could not be read", "spec-unreadable", 2)
    if layer != "personal":
        raise Refusal(
            f"{spec_id} is in a shared vault: 'show' XML-escapes its body, so a body composed from "
            "it would write escaped text",
            "shared-vault",
            2,
        )
    return body, revision


def _find_entry(body: str, criterion: str, slice_: str) -> waiting.WaitingEntry:
    entries = sw.spec_entries(body)
    entry = entries.get((criterion, slice_))
    if entry is None:
        if (criterion, None) in entries:
            raise Refusal(
                f"{criterion} is a set-aside criterion — only a pending check takes an answer",
                "answer-not-pending-check",
                1,
            )
        raise Refusal(f"no pending check for {criterion} under task/{slice_}", "entry-not-found", 1)
    if entry.status != "waiting":
        raise Refusal(
            f"{criterion} is not waiting (latest status '{entry.status}')", "entry-not-waiting", 1
        )
    return entry


_MKTEMP_NAME = re.compile(r"tmp\.[A-Za-z0-9]{6,}")


def _is_mktemp_file(st: os.stat_result) -> bool:
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()


def _mktemp_made(path: str) -> bool:
    """Whether `path` is a plain file `mktemp` could have made for this user: a regular file (not a
    symlink) owned by the current uid, named `tmp.` plus 6 or more letters or digits, directly in
    `$TMPDIR` (else `/tmp`)."""
    directory = os.environ.get("TMPDIR") or "/tmp"
    parent, name = os.path.split(path)
    if not _MKTEMP_NAME.fullmatch(name) or os.path.realpath(parent) != os.path.realpath(directory):
        return False
    try:
        return _is_mktemp_file(os.lstat(path))
    except OSError:
        return False


def _read_words(path: str) -> str:
    if not _mktemp_made(path):
        raise Refusal("--words-file must be the file mktemp made", "invalid-arguments", 2)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as f:
            if not _is_mktemp_file(os.fstat(f.fileno())):
                raise OSError("not a regular file")
            raw = f.read(_MAX_WORDS_FILE_BYTES + 1)
        if len(raw) > _MAX_WORDS_FILE_BYTES:
            raise Refusal("the words are too long", "words-too-long", 1)
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise Refusal("--words-file could not be read as UTF-8 text", "invalid-arguments", 2)


def _clean_words(raw: str, answer: str) -> str:
    flat = " ".join(raw.split())
    words = "".join(ch for ch in flat if unicodedata.category(ch) not in _REMOVED_CATEGORIES).strip()
    if not words:
        if answer == "failed":
            raise Refusal("a failed answer needs words saying what failed", "words-required", 1)
        return _DEFAULT_PASSED_WORDS
    if len(words) > MAX_WORDS_CHARS:
        raise Refusal(f"the words are longer than {MAX_WORDS_CHARS} characters", "words-too-long", 1)
    if _scrub_credential_shaped(words) != words:
        raise Refusal("the words look like a credential", "credential-shaped-words", 1)
    return words


def _append_history(body: str, entry: waiting.WaitingEntry, line: str) -> str:
    """`body` with `line` after the last non-blank line of `entry`'s block."""
    lines, masked = markdown_section.split_body(body)
    try:
        located = markdown_section.section_range(
            lines, masked, waiting.HEADING, waiting.DUPLICATE_HEADING_REASON_CODE
        )
    except DuplicateHeadingError:
        located = None
    if located is None:
        raise Refusal("the spec's '## Waiting' section could not be located", "malformed-prior-section", 2)
    start, end = located
    opening = f"- **{entry.criterion_id}.** {entry.kind} — task/{entry.building_slice}"
    at = next((i for i in range(start + 1, end) if not masked[i] and lines[i].rstrip() == opening), None)
    if at is None:
        raise Refusal("the entry's block could not be located", "malformed-prior-section", 2)
    last = at
    for i in range(at + 1, end):
        if lines[i].strip() and not lines[i].startswith(" "):
            break
        if lines[i].strip():
            last = i
    lines.insert(last + 1, line)
    return "\n".join(lines)


def _reread(runner, vault: str, spec_id: str) -> tuple[str, str] | None:
    """(body, revision) of the spec as it now stands, or None when it cannot be read."""
    try:
        _, body, revision, _ = sw.read_record(runner, vault, spec_id, "spec-unreadable", personal_only=False)
    except Refusal:
        return None
    return body, revision


def _landed(body: str, entry, line: tuple) -> bool:
    try:
        now = sw.spec_entries(body).get((entry.criterion_id, entry.building_slice))
    except Refusal:
        return False
    return now is not None and bool(now.history) and now.history[-1] == line


def _write(runner, vault: str, spec_id: str, entry, answer: str, base: str, proposed: str, line: tuple) -> str | None:
    """The spec's revision after the write, or None when it could not be read again."""
    if answer == "failed":
        argv = sw.update_argv(spec_id, vault, base)
    else:
        argv = [
            "record", "amend", spec_id, "--vault", vault, "--close", entry.criterion_id,
            "--initiated-by", "operator", "--reason", _CLOSE_REASON, "--base", base,
        ]
    done = sw.write_record(runner, argv, proposed)
    now = _reread(runner, vault, spec_id)
    ours = now[1] if now and now[0].rstrip("\n") == proposed.rstrip("\n") else None
    if done is not None and done.returncode == 0:
        return ours
    if now is not None:
        if _landed(now[0], entry, line):
            return ours
        if now[1] != base:
            raise Refusal(f"{spec_id} changed after it was read", "stale", 1)
    text = done.stderr if done is not None else ""
    if answer == "passed" and any(t in text for t in _CLOSE_REFUSED_TEXTS):
        raise Refusal(f"{entry.criterion_id} cannot be closed", "close-refused", 1)
    raise Refusal(f"lore could not write {spec_id}", "write-failed", 2)


def _answer(args, runner) -> int:
    spec, slice_ = _check_shapes(args)
    spec_id = f"spec/{spec}"
    _probe(runner)
    body, revision = _read_spec(runner, args.vault, spec_id)
    if revision != args.base:
        raise Refusal(f"{spec_id} changed after it was read", "stale", 1)
    entry = _find_entry(body, args.criterion, slice_)
    words = _clean_words(_read_words(args.words_file), args.answer)
    today = waiting_gate._today()
    proposed = _append_history(body, entry, f"  - {today.isoformat()} {args.answer}: {words}")
    waiting_gate.certify_answer(body, proposed, today)
    new_revision = _write(
        runner, args.vault, spec_id, entry, args.answer, args.base, proposed, (today.isoformat(), args.answer, words)
    )
    print(f"saved: {entry.criterion_id} {args.answer}")
    if new_revision is not None:
        print(f"revision: {new_revision}")
    return 0


def _words_file_in(argv: list[str]) -> str | None:
    """The path after the last `--words-file`, for an argv that did not parse."""
    found = None
    for i, token in enumerate(argv):
        if token == "--words-file" and i + 1 < len(argv):
            found = argv[i + 1]
        elif token.startswith("--words-file="):
            found = token.split("=", 1)[1]
    return found


def _remove(path: str | None) -> None:
    if path is None or not _mktemp_made(path):
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def main(argv: list[str], runner=None) -> int:
    runner = runner or gather.lore_runner
    args = None
    try:
        try:
            ap = _Parser(prog="checklist_answer.py", description=__doc__.splitlines()[0], allow_abbrev=False)
            for flag in ("--vault", "--spec", "--criterion", "--slice", "--base", "--words-file"):
                ap.add_argument(flag, required=True)
            ap.add_argument("--answer", required=True, choices=_ANSWERS)
            args = ap.parse_args(argv)
            return _answer(args, runner)
        finally:
            _remove(args.words_file if args is not None else _words_file_in(argv))
    except Refusal as r:
        return emit_refusal(PROG, r)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
