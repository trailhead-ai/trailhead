"""Resurrects a workspace whose tmux session is gone from its window record.

`plan_resurrection` maps a window record's entries, the workspace root, the
environment, and the harness to one decision per entry, in record order:
:class:`Restore` (a resolved directory and a stub argv, never a live
process) or :class:`Drop` (a reason, no tmux call ever made for that
entry). Nothing in the planner touches tmux, the window record, or the
filesystem beyond `Path.resolve()` and an existence check.

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

`resurrect_workspace_session` is the engine: it drives tmux from the
planner's decisions, in record order. No `Restore` at all folds into the
create arm — `create_workspace_session` brings up the ordinary login-shell
session, and the record is still re-stamped to drop whatever was decided
against. Otherwise the first `Restore` rides
`Tmux.new_session_with_window` (its stub, its directory, its recorded
name), reading the session and its first window's id back on that same
call; a duplicate answer folds to :class:`DuplicateSession` (the caller's
connect arm), any other failure to :class:`CreateFailed`. On success the
session is marked and bound through `_mark_and_bind` — the same helper
`create_workspace_session`'s own CREATED branch uses, so there is one copy
of what makes a session a camp workspace session, and one abandon path
when tmux refuses to mark it. Each later `Restore` is one `Tmux.new_window`;
a `None` or `UNANSWERED` answer records a :class:`Failed` and continues —
one window's failure never stops the rest. The record is then re-stamped
once, under the workspace lock, with every window tmux actually created,
dropping every entry that was decided against or that failed to come back.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, Union

from ..group.window_record import (
    NotRestamped,
    Restamped,
    WindowEntry,
    restamp_window_entries,
    window_record_path_for,
)
from .eligibility import assert_not_a_credential_store
from .naming import workspace_session_name
from .recovery import printable_path
from .session import LaunchError
from .tmux import DUPLICATE, UNANSWERED, NewSessionWindowFailure, Tmux
from .window_reconcile import RECONCILE_LOCK_TIMEOUT_SECONDS
from .workspace_session import (
    WorkspaceSessionOutcome,
    _mark_and_bind,
    create_workspace_session,
)

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


@dataclass(frozen=True)
class Failed:
    """One `Restore` whose tmux call did not come back — `reason` is fixed
    text distinguishing tmux answering with a refusal (`None`) from tmux
    never answering at all (`UNANSWERED`); the entry keeps its OLD
    window_id, since tmux never assigned it a new one."""

    entry: WindowEntry
    reason: str


@dataclass(frozen=True)
class DuplicateSession:
    """The session-creating call found another camp process already holds
    *session_name* — not a failure. The caller folds this into the connect
    arm, the same way `create_workspace_session`'s `ALREADY_EXISTED`
    already does."""

    session_name: str


@dataclass(frozen=True)
class CreateFailed:
    """The session-creating call failed outright, or came up but could not
    be marked as a camp workspace session (the shared `_mark_and_bind`
    abandon path) — *error* carries tmux's own words. The window record is
    touched in neither case."""

    session_name: str
    error: str


@dataclass(frozen=True)
class ResurrectionResult:
    """One resurrection's outcome: every window tmux actually holds now,
    split by what happened to it, plus what the re-stamp did to the
    record.

    `restored` and `failed`/`dropped` entries never overlap — every
    surviving record entry appears in exactly one of the three. `restored`
    entries carry the id and name tmux assigned on the way back; `failed`
    and `dropped` entries keep their old ids, since tmux never (re)assigned
    them one. `record_path` is carried alongside `restamp` so a renderer
    can name the record without recomputing its path from a `ws_dir` this
    result does not otherwise carry.
    """

    session_name: str
    restored: tuple[WindowEntry, ...]
    failed: tuple[Failed, ...]
    dropped: tuple[Drop, ...]
    restamp: "Restamped | NotRestamped"
    record_path: Path


def _restamped_entry(new: "object", entry: WindowEntry) -> WindowEntry:
    """The record entry to write back for a window tmux just (re)created:
    tmux's own id and name, every other field carried from the recorded
    entry unchanged — mirrors `reconcile`'s own "identity is cwd and
    conversation_id/command_line, never id or name" rule."""
    return WindowEntry(
        window_id=new.window_id,
        name=new.window_name,
        cwd=entry.cwd,
        conversation_id=entry.conversation_id,
        command_line=entry.command_line,
    )


def resurrect_workspace_session(
    group_name: str,
    slug: str,
    ws_dir: Path,
    entries: Sequence[WindowEntry],
    *,
    env: Mapping[str, str] | None,
    tmux: Tmux,
    harness,
) -> "ResurrectionResult | DuplicateSession | CreateFailed":
    """Bring the workspace's session back up from *entries*, in record
    order, and re-stamp the record with what tmux actually created.

    See the module docstring for the shape: `plan_resurrection` decides
    per entry; no `Restore` at all folds into `create_workspace_session`;
    otherwise the first `Restore` rides `new_session_with_window` and every
    later one rides `new_window`, each failure isolated to its own entry.
    The record is re-stamped exactly once, after every tmux call has been
    made, under the workspace lock (`RECONCILE_LOCK_TIMEOUT_SECONDS`) —
    never entry by entry, so a caller who reads the record mid-resurrection
    never sees a partially-restamped one.
    """
    ws_dir = Path(ws_dir)
    name = workspace_session_name(group_name, slug)
    record_path = window_record_path_for(ws_dir)

    decisions = plan_resurrection(entries, ws_dir, env=env, harness=harness)
    restores = [d for d in decisions if isinstance(d, Restore)]
    dropped = tuple(d for d in decisions if isinstance(d, Drop))

    if not restores:
        result = create_workspace_session(group_name, slug, ws_dir, env=env, tmux=tmux)
        if result.outcome is WorkspaceSessionOutcome.ALREADY_EXISTED:
            return DuplicateSession(session_name=result.session_name)
        if result.outcome is WorkspaceSessionOutcome.FAILED:
            return CreateFailed(session_name=result.session_name, error=result.error or "")
        restamp = restamp_window_entries(
            ws_dir,
            {},
            remove={d.entry.window_id for d in dropped},
            lock_timeout=RECONCILE_LOCK_TIMEOUT_SECONDS,
        )
        return ResurrectionResult(
            session_name=result.session_name,
            restored=(),
            failed=(),
            dropped=dropped,
            restamp=restamp,
            record_path=record_path,
        )

    first = restores[0]
    first_answer = tmux.new_session_with_window(
        name,
        cwd=first.directory,
        window_name=first.entry.name,
        command=first.argv,
        env=env,
    )
    if first_answer is DUPLICATE:
        return DuplicateSession(session_name=name)
    if isinstance(first_answer, NewSessionWindowFailure):
        return CreateFailed(session_name=name, error=first_answer.stderr)

    mark_failure = _mark_and_bind(tmux, name, group_name, slug)
    if mark_failure is not None:
        return CreateFailed(session_name=name, error=mark_failure.error or "")

    mapping: dict[str, WindowEntry] = {
        first.entry.window_id: _restamped_entry(first_answer, first.entry)
    }
    restored: list[WindowEntry] = [mapping[first.entry.window_id]]
    failed: list[Failed] = []

    for restore in restores[1:]:
        answer = tmux.new_window(
            name,
            cwd=restore.directory,
            window_name=restore.entry.name,
            command=restore.argv,
        )
        if answer is None or answer is UNANSWERED:
            reason = "tmux did not answer" if answer is UNANSWERED else "tmux refused to create it"
            failed.append(Failed(restore.entry, reason))
            continue
        new_entry = _restamped_entry(answer, restore.entry)
        mapping[restore.entry.window_id] = new_entry
        restored.append(new_entry)

    remove_ids = {d.entry.window_id for d in dropped} | {f.entry.window_id for f in failed}
    restamp = restamp_window_entries(
        ws_dir, mapping, remove=remove_ids, lock_timeout=RECONCILE_LOCK_TIMEOUT_SECONDS
    )

    return ResurrectionResult(
        session_name=name,
        restored=tuple(restored),
        failed=tuple(failed),
        dropped=dropped,
        restamp=restamp,
        record_path=record_path,
    )


def render_resurrection_lines(result: ResurrectionResult) -> list[str]:
    """The lines a resurrection prints: the drop lines `render_plan_lines`
    already renders for `result.dropped`, then one failure line per
    `result.failed`, then — only when the re-stamp itself could not write —
    one line naming the record. Every line is escaped whole through
    `printable_path`, like every other line this module composes."""
    lines = render_plan_lines(result.dropped)
    for failure in result.failed:
        entry = failure.entry
        if entry.conversation_id is not None:
            detail = f"conversation {entry.conversation_id}"
        else:
            detail = f"command {entry.command_line}"
        line = (
            f'camp: window {entry.window_id} "{entry.name}" did not come back — '
            f"{failure.reason} ({detail})"
        )
        lines.append(printable_path(line))
    if isinstance(result.restamp, NotRestamped):
        line = (
            f"camp: window record at {result.record_path} could not be "
            f"re-stamped — {result.restamp.reason}"
        )
        lines.append(printable_path(line))
    return lines
