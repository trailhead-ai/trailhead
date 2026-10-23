"""Multi-turn driver for the tidy-followups evals.

Starts an arm as a stream-json `claude` process, sends the invoking message, and after
each turn's `result` event sends the next scripted answer, until the answers run out or the
arm ends a turn without asking.

    drive(argv, first_message, answers, *, env=None, cwd=None, turn_timeout=...) -> DriveResult

`answers` is a list of strings. `{code}` in an answer is replaced by the confirmation code
the arm printed in its own text during the turn just ended (see `QUESTION_CODE`), or by the
empty string when it printed none. Only top-level assistant text counts: tool calls and
results, and everything a subagent says, never supply a code.

Each turn is recorded as a dict:

    turn            1-based turn number
    assistant_text  the turn's top-level assistant text, in order, joined by newlines
    tool_calls      [{"name", "input"}] for the turn's top-level tool_use blocks
    asked           the turn ended with a question: `drive`'s `asked(text, last_text, code)`
                    predicate, by default a confirmation question line or final text ending
                    in `?`
    code            the confirmation code taken from the text, or None
    display_shown   a confirmation question line with its code was in the arm's text
    answer_sent     the answer sent after this turn (code filled in), or None
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import followup_confirm  # noqa: E402
import scrub as capture_scrub  # noqa: E402

STREAM_FLAGS = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
DEFAULT_TURN_TIMEOUT = 580

_CODE = rf"[{followup_confirm.ALPHABET}]{{{followup_confirm.CODE_LENGTH}}}"
QUESTION_CODE = re.compile(
    rf"^[^\n]*\?[^\n]*\bthe code[ \t]+[`*_\"']*(?P<code>{_CODE})[`*_\"']*[ \t.!)]*$",
    re.MULTILINE,
)


@dataclass
class DriveResult:
    turns: list = field(default_factory=list)
    unsent: list = field(default_factory=list)
    ended: str = ""
    returncode: int | None = None
    stream: list = field(default_factory=list)


def question_code(text: str) -> str | None:
    found = QUESTION_CODE.findall(text)
    return found[-1] if found else None


def _is_top_level(event: dict) -> bool:
    return not event.get("parent_tool_use_id")


class _Lines:
    def __init__(self, fd: int):
        self.fd = fd
        self.buf = b""
        self.eof = False

    def next(self, deadline: float) -> str | None:
        import select

        while b"\n" not in self.buf:
            if self.eof:
                break
            wait = deadline - time.monotonic()
            if wait <= 0:
                raise TimeoutError
            ready, _, _ = select.select([self.fd], [], [], wait)
            if not ready:
                raise TimeoutError
            chunk = os.read(self.fd, 65536)
            if not chunk:
                self.eof = True
                break
            self.buf += chunk
        if not self.buf:
            return None
        line, _, self.buf = self.buf.partition(b"\n")
        return line.decode("utf-8", errors="replace")


def _send(proc, text: str) -> None:
    msg = {"type": "user", "message": {"role": "user", "content": text}}
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()


def strict_asked(text: str, last_text: str, code: str | None) -> bool:
    """A turn asked when it printed a confirmation question line with its code, or its last text
    block ends in `?`."""
    return code is not None or last_text.rstrip().endswith("?")


def _read_turn(lines: _Lines, deadline: float, stream: list, asked=strict_asked) -> tuple[dict | None, str]:
    texts: list[str] = []
    calls: list[dict] = []
    last_text = ""
    while True:
        try:
            line = lines.next(deadline)
        except TimeoutError:
            return None, "timeout"
        if line is None:
            return None, "eof"
        stream.append(line)
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict) or not _is_top_level(ev):
            continue
        if ev.get("type") == "result":
            break
        if ev.get("type") != "assistant":
            continue
        for block in (ev.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
                last_text = block.get("text", "")
            elif block.get("type") == "tool_use":
                calls.append({"name": block.get("name"), "input": block.get("input")})
    text = "\n".join(texts)
    code = question_code(text)
    return {
        "assistant_text": text,
        "tool_calls": calls,
        "asked": asked(text, last_text, code),
        "code": code,
        "display_shown": code is not None,
    }, ""


def drive(argv, first_message: str, answers, *, env=None, cwd=None,
          turn_timeout: float = DEFAULT_TURN_TIMEOUT, asked=strict_asked) -> DriveResult:
    result = DriveResult()
    pending = list(answers)
    proc = subprocess.Popen(
        [str(a) for a in argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=env, cwd=cwd,
    )
    lines = _Lines(proc.stdout.fileno())
    try:
        _send(proc, first_message)
        while True:
            turn, why = _read_turn(lines, time.monotonic() + turn_timeout, result.stream, asked)
            if turn is None:
                result.ended = why
                break
            turn = {"turn": len(result.turns) + 1, **turn, "answer_sent": None}
            result.turns.append(turn)
            if not turn["asked"]:
                result.ended = "no-question"
                break
            if not pending:
                result.ended = "answers-exhausted"
                break
            answer = pending.pop(0).replace("{code}", turn["code"] or "")
            turn["answer_sent"] = answer
            _send(proc, answer)
    finally:
        result.unsent = pending
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=30 if result.ended != "timeout" else 0.1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        result.returncode = proc.returncode
    return result


def transcript(result: DriveResult, **scrub_roots) -> dict:
    return capture_scrub.scrub_value(
        {"turns": result.turns, "unsent": result.unsent, "ended": result.ended}, **scrub_roots
    )
