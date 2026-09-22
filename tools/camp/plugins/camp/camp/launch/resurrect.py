"""Pure planner for resurrecting a workspace whose tmux session is gone.

`plan_resurrection` maps a window record's entries, the workspace root, the
environment, and the harness to one decision per entry, in record order:
:class:`Restore` (a resolved directory and a stub argv, never a live
process) or :class:`Drop` (a reason, no tmux call ever made for that
entry). Nothing here touches tmux, the window record, or the filesystem
beyond `Path.resolve()` and an existence check — the door's engine (a later
task) drives tmux from these decisions and re-stamps the record with the
ids tmux assigns on the way back.

The stub is a small shell wrapper: print the lines that describe what the
window used to hold, then `exec` the operator's own shell under the same
environment scrub a composed conversation runs under. camp starts no Claude
process on the way back — the operator decides which conversations to
resume, and when, from inside that shell. The lines are argv elements,
never interpolated into the script, and every line is escaped whole through
`printable_path` before it becomes one: the window record is a file any
process running as the operator can write, and a control, C1, or bidi byte
in a recorded command line or conversation id must reach the terminal as
its visible spelling, never as the sequence.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, Union

from ..group.window_record import WindowEntry
from .eligibility import assert_not_a_credential_store
from .recovery import printable_path
from .session import LaunchError

#: The stub script with no environment scrub. A harness whose
#: `session_launch_env_unset()` is empty (or absent, `harness=None`) gets
#: this constant, unchanged, as the `sh -c` script; a non-empty scrub is
#: spliced in between `env` and the shell it execs (see `_stub_script`).
STUB_SCRIPT = 'printf \'%s\\n\' "$@"; exec env "${SHELL:-sh}"'

_NO_HARNESS_LINE = (
    "camp: no harness is configured for this group, so camp cannot "
    "compose a resume command"
)


@dataclass(frozen=True)
class Restore:
    """One entry that comes back as a shell window, never a live process.

    `directory` is the resolved absolute path the window is rooted at.
    `argv` is the full `sh -c ...` command tmux is asked to run.
    """

    entry: WindowEntry
    directory: Path
    argv: list[str]


@dataclass(frozen=True)
class Drop:
    """One entry that will not come back, and why. No tmux call is made
    for it, and its window is not written back into the record."""

    entry: WindowEntry
    reason: str


Decision = Union[Restore, Drop]


def plan_resurrection(
    entries: Sequence[WindowEntry],
    ws_dir: Path,
    *,
    env: Mapping[str, str] | None,
    harness,
) -> tuple[Decision, ...]:
    """Decide, per entry and in record order, whether it comes back.

    Order: resolve `ws_dir / entry.cwd` (symlinks followed); outside the
    root → `Drop` naming the recorded path; at/under/above a credential
    store → `Drop` naming no path; not a directory → `Drop` naming the
    recorded path as no longer existing; otherwise `Restore` with the stub.
    """
    resolved_ws_dir = Path(ws_dir).resolve()
    unset_vars = list(harness.session_launch_env_unset()) if harness is not None else []
    script = _stub_script(unset_vars)

    decisions: list[Decision] = []
    for entry in entries:
        resolved = (resolved_ws_dir / entry.cwd).resolve()

        if resolved != resolved_ws_dir and resolved_ws_dir not in resolved.parents:
            decisions.append(
                Drop(entry, f"directory {entry.cwd} resolves outside the workspace")
            )
            continue

        try:
            assert_not_a_credential_store(resolved, env=env)
        except LaunchError:
            decisions.append(Drop(entry, "directory is a credential store"))
            continue

        if not resolved.is_dir():
            decisions.append(Drop(entry, f"directory {entry.cwd} no longer exists"))
            continue

        lines = _stub_lines(entry, resolved, env=env, harness=harness)
        escaped_lines = [printable_path(line) for line in lines]
        argv = ["sh", "-c", script, "camp-resurrect", *escaped_lines]
        decisions.append(Restore(entry, resolved, argv))

    return tuple(decisions)


def _stub_script(unset_vars: Sequence[str]) -> str:
    """Splice the harness's env-unset vars into `STUB_SCRIPT`.

    Empty `unset_vars` returns `STUB_SCRIPT` itself, unchanged — the
    identity a caller with no scrub (or no harness) can rely on.
    """
    if not unset_vars:
        return STUB_SCRIPT
    flags = " ".join(f"-u {var}" for var in unset_vars)
    return STUB_SCRIPT.replace('exec env "', f'exec env {flags} "')


def _stub_lines(
    entry: WindowEntry,
    resolved_dir: Path,
    *,
    env: Mapping[str, str] | None,
    harness,
) -> list[str]:
    """The lines a surviving entry's stub prints, before escaping."""
    if entry.command_line is not None:
        return [f"camp: this window was opened with: {entry.command_line}"]

    conversation_id = entry.conversation_id
    id_line = f"camp: this window held conversation {conversation_id}"

    if harness is None:
        return [id_line, _NO_HARNESS_LINE]

    transcript = harness.session_transcript_path(conversation_id, resolved_dir, env=env)
    if transcript is None:
        return [
            f"camp: this window held conversation {conversation_id}, but no "
            "transcript for it exists on this machine"
        ]

    resume_argv = harness.session_resume(conversation_id)
    if resume_argv is None:
        return [id_line, _NO_HARNESS_LINE]

    return [id_line, f"camp: resume it with: {shlex.join(resume_argv)}"]


def render_plan_lines(decisions: Sequence[Decision]) -> list[str]:
    """One stderr line per `Drop`, in reconciliation's dropped shape,
    escaped whole through `printable_path`. `Restore` decisions render
    nothing — they are not a correction to report."""
    lines: list[str] = []
    for decision in decisions:
        if not isinstance(decision, Drop):
            continue
        detail = decision.reason
        if decision.entry.conversation_id is not None:
            detail += f"; conversation {decision.entry.conversation_id}"
        line = (
            f'camp: window record: dropped {decision.entry.window_id} '
            f'"{decision.entry.name}" ({detail})'
        )
        lines.append(printable_path(line))
    return lines
