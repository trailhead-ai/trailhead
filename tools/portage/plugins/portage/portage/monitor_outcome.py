"""Monitor's machine-readable completion channel — the outcome-file grammar.

`monitor` (see `agents/monitor.md`) may be handed an `outcome_file` path by
its dispatcher. On reaching a terminal state it writes exactly one line to
that path, naming the state: `MERGED` | `READY <reason>` | `BLOCKED
<reason>` | `STOPPED <reason>`, so a caller that cannot wait on monitor's
prose reply can poll for its result instead.

This module owns only the read/parse side. Monitor is a prose-driven
subagent — there is no monitor-side Python that writes the file — so nothing
here writes an outcome file; that contract lives in `agents/monitor.md`
itself, pinned by `tests/test_portage_monitor_outcome_prose.py`.
"""

from __future__ import annotations

from pathlib import Path

#: The four terminal-state tokens monitor's outcome file may carry.
MONITOR_OUTCOME_TOKENS = frozenset({"MERGED", "READY", "BLOCKED", "STOPPED"})

#: `MERGED` needs no argument (there is nothing further to say); the other
#: three are always followed by a reason.
_TOKENS_REQUIRING_ARGUMENT = frozenset({"READY", "BLOCKED", "STOPPED"})

_MAX_ARGUMENT_CHARS = 200


def parse_monitor_outcome(line: str) -> tuple[str | None, str]:
    """Split a monitor outcome line into `(token, argument)`.

    Only the first physical line is parsed — a well-formed outcome file has
    exactly one, but a caller reading a file some other process appended to
    should not have later lines corrupt the result. Returns `(None,
    <line>)` when the first line is not one of `MONITOR_OUTCOME_TOKENS`, or
    when a token that requires an argument (`READY`/`BLOCKED`/`STOPPED`) was
    given none — the caller treats that as a validation failure.
    """
    first_line = line.strip().splitlines()[0].strip() if line.strip() else ""
    token, _, argument = first_line.partition(" ")
    argument = argument.strip()
    if token not in MONITOR_OUTCOME_TOKENS:
        return None, first_line[:_MAX_ARGUMENT_CHARS]
    if token in _TOKENS_REQUIRING_ARGUMENT and not argument:
        return None, first_line[:_MAX_ARGUMENT_CHARS]
    return token, argument


def read_monitor_outcome(path: Path) -> str:
    """Return the outcome line monitor wrote, or a synthesized `BLOCKED` line.

    A missing or empty file means monitor died, timed out, or never ran —
    the crashed signal callers rely on (see the module docstring and
    `agents/monitor.md`). `BLOCKED` is the closest of the four terminal
    tokens to that meaning: an unattended caller cannot tell "crashed" from
    "stuck awaiting an operator" from the file alone, and both need the same
    response — hold, don't treat as merged or ready, and surface the file's
    absence for a human to investigate.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return f"BLOCKED no outcome written to {path.name} — monitor died, timed out, or never ran"
    if not text.strip():
        return f"BLOCKED empty outcome file {path.name} — monitor wrote no return token"
    return text
