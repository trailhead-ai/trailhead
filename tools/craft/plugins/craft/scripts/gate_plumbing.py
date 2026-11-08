#!/usr/bin/env python3
"""Plumbing shared by the section write gates (`followups_gate`, `waiting_gate`).

    Refusal         an exception carrying a stable reason code and an exit code
    safe(text)      control-character neutralizer, then credential scrub
    load_prior(p)   (body, revision) from a `lore record show <spec> --json` file
    read_stdin()    the proposed body, capped, UTF-8, non-empty
    emit_refusal(prog, r) -> int
                    prints the `reason:` / `reason-code:` stderr pair, returns
                    the refusal's exit code

Exit 1 is a violation of the gate's rules; exit 2 means the gate could not
certify (stdin or prior unusable). This module has no CLI.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from criterion_gate import (  # noqa: E402
    _neutralize_control_chars,
    _scrub_credential_shaped,
)

MAX_BYTES = 256 * 1024
_REVISION_RE = re.compile(r"[0-9a-f]{64}")


class Refusal(Exception):
    def __init__(self, message: str, reason_code: str, exit_code: int):
        self.reason_code = reason_code
        self.exit_code = exit_code
        super().__init__(message)


def safe(text: str) -> str:
    return _scrub_credential_shaped(_neutralize_control_chars(text))


def load_prior(path_str: str) -> tuple[str, str]:
    """(body, revision) from the `--prior` document."""
    try:
        with Path(path_str).open("rb") as f:
            raw = f.read(MAX_BYTES + 1)
    except OSError as e:
        raise Refusal(f"--prior file {path_str!r} could not be read: {e}", "unreadable-prior", 2)
    if len(raw) > MAX_BYTES:
        raise Refusal(
            f"--prior file {path_str!r} exceeds the {MAX_BYTES}-byte cap", "unreadable-prior", 2
        )
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
        raise Refusal(
            f"--prior is not valid UTF-8 JSON ({type(e).__name__}) — pass the output of "
            "'lore record show spec/<n> --json'",
            "malformed-prior",
            2,
        )
    fields = ("body", "revision", "layer")
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in fields):
        raise Refusal(
            "--prior must be a JSON object with string 'body', 'revision' and 'layer' — "
            "pass the output of 'lore record show spec/<n> --json'",
            "malformed-prior",
            2,
        )
    if not _REVISION_RE.fullmatch(data["revision"]):
        raise Refusal(
            "--prior 'revision' is not a 64-character lowercase hex digest — "
            "pass the output of 'lore record show spec/<n> --json'",
            "malformed-prior",
            2,
        )
    if data["layer"] != "personal":
        raise Refusal(
            "--prior is not from a personal-layer vault: in a shared vault 'show' "
            "XML-escapes the body, so a body composed from it would write escaped text",
            "shared-layer-prior",
            2,
        )
    return data["body"], data["revision"]


def read_stdin() -> str:
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise Refusal(
            f"proposed body on stdin exceeds the {MAX_BYTES}-byte cap", "stdin-too-large", 2
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise Refusal(f"proposed body on stdin is not valid UTF-8: {e}", "non-utf8-stdin", 2)
    if not text.strip():
        raise Refusal("proposed body on stdin is empty", "empty-stdin", 2)
    return text


def emit_refusal(prog: str, r: Refusal) -> int:
    # Every vault-sourced value in a message is already repr'd, which escapes
    # every character the neutralizer would; safe() is a second layer.
    print(f"{prog}: reason: {safe(str(r))}", file=sys.stderr)
    print(f"{prog}: reason-code: {r.reason_code}", file=sys.stderr)
    return r.exit_code
