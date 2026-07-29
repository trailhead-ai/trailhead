"""The durable exit report for a ranger sweep.

A sweep's outcome must survive the session (the coordinator is a transcript, not
a record), so this module owns a timestamped markdown file under
``state_dir("ranger")/reports/<group>/`` plus a companion JSON state file at the
same path with a ``.state.json`` suffix instead of ``.md``. The state file is the
source of truth for rendering: every ``append_*`` call re-renders the whole
report from the accumulated state rather than literally appending bytes, which
is what makes re-appends idempotent (a task id already recorded in
``appended_task_ids`` is a no-op) and lets a crashed-and-resumed process
(different Python object, same files) continue exactly where a prior one left
off. ``start`` creates both files 0600 with the header and all seven bucket
headings rendered up front — a fresh report is a valid, parseable partial
report even before any task is appended, and stays parseable if the sweep never
reaches ``finish`` (no footer is simply absent, not a corrupt file). Because
every write re-renders the whole file, each one lands by writing a complete
temp file alongside and renaming it into place: the report is a partial durable
artifact an operator reads precisely *because* a sweep died, so no write may
leave it truncated. A state file that stops parsing is refused by name rather
than reset — resetting would un-dedupe the sweep and duplicate every line
recorded after it.

Bucket set is fixed by the spec this module implements: ``promoted``,
``escalated-awaiting-operator``, ``routed``, ``blocked-answered``,
``blocked-still-waiting``, ``skipped``, ``failed``. ``blocked-answered`` covers a
previously-``blocked`` task whose recorded answer let it dispatch this sweep —
its ritual outcome already flows into a status write, not a further bucket
split, so no question accompanies that line. (A task that *re-escalates* mid-
sweep is the exception: it is reported as an escalation, question and all,
because a bare id would strand the question the ritual just wrote.)
``escalated-awaiting-operator`` and ``blocked-still-waiting`` are the two
"still needs an operator" buckets, and both carry the task's one-line question
plus a ready-to-paste answer command naming the elected vault — or, when the
record's section carries no parseable question, a fixed placeholder naming the
record instead, since a malformed record must not be able to end a sweep.

**Reading record bodies stays here, not in the coordinator.** Per the package
docstring, the CLI (this module, driven by the future ``ranger sweep record``
verb) is what reads a task record's raw body — an escalated or blocked-still-
waiting line is built from that raw text, so the extracted question, and the
answer command built from it, never transit the dispatched agent's one-line
return or the coordinating session's context.

**Credential-pattern scrub, always — on the untrusted field, not the
rendered line.** Every ``append_*`` function funnels through ``_append``
(ported from craft execute's Phase 5 five-category regex list, upgraded per
the qualifier-text/vendor-prefix lesson so compound names like
``STRIPE_SECRET_KEY=`` are caught), and ``_append`` is the one place any of
them scrubs. A skipped/failed/routed line's `reason`/`target` text is exactly
as untrusted as the escalated question: it originates from the dispatched
agent's free-text return line, authored over the same record bodies. But
``_append`` scrubs only the caller's *untrusted* argument, never the fully
assembled line: the high-entropy base64 pattern matches any 32+ character
alnum run with no separator required, and a task id or the answer command's
own fixed ``lore record update ... --diff`` syntax can be exactly that long
— scrubbing the whole rendered line would occasionally redact the id itself
or the runnable command around it, not just the secret sitting next to them.
Each ``append_*`` function hands ``_append`` a ``render`` callable plus its
one untrusted string; ``_append`` scrubs the string and calls ``render`` with
the scrubbed result, so trusted structural text (the task id, the backticks,
the command syntax) never passes through the scrubber at all, and a future
bucket-writer still cannot land raw untrusted text in a bucket without
routing it through this same funnel. The answer command's diff carries **no
context lines at all** — it is a pure positional insertion (``old_count=0``)
that names only the line number to insert after, never the original line's
text — so the one place a raw, unscrubbed secret could otherwise leak (the
diff needing to quote the surrounding line verbatim to satisfy the applier's
context check) never arises. The tradeoff: a positional insert cannot detect
drift the way a context-checked hunk would, so a record edited between
report generation and the operator's paste could land the ``**Answer:**``
line in a slightly different spot in the section — accepted, since the
alternative is writing the secret into a git-backed report.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from trailhead.paths import ensure_dir, state_dir

from .names import validate_shell_safe_name
from .queue import UNRESOLVED_HEADING, unresolved_section_bounds

_REPORTS_SUBDIR = "reports"

BUCKETS = (
    "promoted",
    "escalated-awaiting-operator",
    "routed",
    "blocked-answered",
    "blocked-still-waiting",
    "skipped",
    "failed",
)

_BUCKET_HEADINGS = {
    "promoted": "Promoted",
    "escalated-awaiting-operator": "Escalated — awaiting operator",
    "routed": "Routed",
    "blocked-answered": "Blocked — answered",
    "blocked-still-waiting": "Blocked — still waiting",
    "skipped": "Skipped",
    "failed": "Failed",
}

_QUESTION_PREFIX = "**Question:**"

_NEAR_MISS_LINE = (
    "answer detected but not recognized — expected `**Answer:**` inside "
    "`## Refine — unresolved`"
)

#: Rendered in place of the question when a record's unresolved section cannot
#: be parsed. The bucket line still names the record, so the operator keeps a
#: handle on it; the answer command is omitted because there is no insertion
#: line to build one around.
_MISSING_QUESTION_LINE = "question could not be extracted — open the record"

#: Rendered in place of the question when the record itself could not be read
#: back from the elected vault — deleted, renamed, or moved between the
#: derivation that queued it and the read that would lift its question. Same
#: shape as the unparseable-body line above, and for the same reason: the
#: operator keeps a handle on the task, and one unreadable record never ends a
#: sweep that still has tasks behind it.
_UNREADABLE_RECORD_LINE = "record could not be read from the elected vault"

_FAILED_RETRY_SENTENCE = (
    "The task record was left untouched and will be retried automatically next sweep."
)

# Ported from craft's execute/SKILL.md Phase 5 credential-pattern scrub list,
# upgraded per lesson/credential-regexes-anchored-keyword-to-separator-miss-
# compound-secret-names-allow-qualifier-text-and-add-vendor-prefixes: the
# key-like pattern allows qualifier text between the keyword and the separator
# (catches SECRET_KEY=, AWS_SECRET_ACCESS_KEY=, API_KEY_ID=), and vendor
# fixed-prefix tokens are matched on their own, with no `key=` preamble needed.
_CREDENTIAL_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?i)(secret|token|passwd|password|api[_-]?key)[A-Za-z0-9_-]*\s*[=:]\s*\S+",
        r"(?i)\b(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}"
        r"|glpat-[A-Za-z0-9_-]{20}|xox[baprs]-[A-Za-z0-9-]+|sk_live_[A-Za-z0-9]+"
        r"|AIza[0-9A-Za-z_-]{35})\b",
        r"(?i)bearer\s+[A-Za-z0-9._\-]+",
        r"(?i)api[_-]?key['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{16,}",
        r"\b[A-Za-z0-9+/]{32,}={0,2}\b",
        r"\b[A-Fa-f0-9]{40,}\b",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    )
)

_REDACTED = "[REDACTED]"


class ReportError(Exception):
    """Raised for report-writer failures: no report started at a given path, a
    state file that no longer parses as JSON, or a group name that fails
    path-segment confinement."""


class QuestionExtractionError(ReportError):
    """Raised when a record body has no parseable ``## Refine — unresolved``
    section, or no ``**Question:**`` line inside it. Signals an upstream
    derivation bug — the sweep only ever calls this on a record it has
    already determined carries an escalated or blocked-still-waiting
    question."""


def scrub_credentials(text: str) -> str:
    """Redact every credential-shaped substring in text.

    Over-matches on purpose — this is a tripwire, not a precision filter; a
    false hit costs a manual look, a missed one ships a secret.
    """
    for pattern in _CREDENTIAL_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def extract_question(record_body: str) -> tuple[str, int]:
    """Return (question_text, line_number) for the record's escalated question.

    line_number is the 1-based line the ``**Question:**`` line occupies in
    record_body, matching unified-diff hunk-header conventions — callers use
    it to place the answer insertion.

    The scan is bounded by the section — it shares
    :func:`queue.unresolved_section_bounds` with the classifier rather than
    reading to the end of the body — so the two never disagree about where the
    section ends. That agreement is load-bearing twice over: a
    ``**Question:**`` in some later section is not this section's question, and
    the insertion line this returns always lands inside the bounds the
    answered predicate checks, so the answer an operator pastes actually
    satisfies it.
    """
    lines = record_body.splitlines(keepends=True)
    bounds = unresolved_section_bounds(lines)
    if bounds is None:
        raise QuestionExtractionError(f"record body has no {UNRESOLVED_HEADING!r} section")

    start, end = bounds
    for i in range(start, end):
        stripped = lines[i].rstrip("\n")
        if stripped.startswith(_QUESTION_PREFIX):
            return stripped[len(_QUESTION_PREFIX):].strip(), i + 1

    raise QuestionExtractionError(
        f"no {_QUESTION_PREFIX!r} line found inside the {UNRESOLVED_HEADING!r} section"
    )


def _build_answer_command(task_id: str, question_line_no: int, vault: str) -> str:
    """Build the exact `lore record update <task_id> --vault <vault> --diff`
    invocation that inserts a `**Answer:**` line immediately after the question
    line.

    ``--vault`` is as mandatory here as on the sweep's own writes, and for the
    same reason one step removed: this command is run by a human, in their own
    shell, from a directory nobody here controls. Without it ``update`` locates
    the record by a cwd-blind first-match scan across the configured vaults in
    declaration order, so a task name that exists in two vaults takes the
    operator's answer into whichever one lore's config lists first — leaving
    the task they meant to unblock still waiting, and someone else's record
    silently edited.

    The hunk is a pure positional insertion (old_count=0, no context/deletion
    lines) — see the module docstring for why it never quotes the original
    line's text.
    """
    insert_at = question_line_no + 1
    diff = (
        "--- a/body\n"
        "+++ b/body\n"
        f"@@ -{insert_at},0 +{insert_at},1 @@\n"
        "+**Answer:** <your answer here>\n"
    )
    return f"lore record update {task_id} --vault {vault} --diff <<'EOF'\n{diff}EOF"


def _validate_group(group: str) -> None:
    try:
        validate_shell_safe_name(group, what="group")
    except ValueError as exc:
        raise ReportError(str(exc)) from exc


def _state_path(report_path: Path) -> Path:
    return Path(report_path).with_suffix(".state.json")


def _load_state(report_path: Path) -> dict:
    state_path = _state_path(report_path)
    try:
        text = state_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ReportError(f"no report started at {report_path}: {e}")
    try:
        return json.loads(text)
    except ValueError as e:
        # Never a silent reset: `appended_task_ids` is the only record of what
        # this sweep has already written, so a fresh state would re-append
        # every task recorded from here on and double-count the report's own
        # buckets. Refusing by name leaves both files exactly as found.
        #
        # `finish` calls this before it ever reaches `lock_mod.release` (see
        # `_cmd_sweep_finish`), so a corrupt state file raised here always
        # means the sweep's vault lock is still held — naming only "start a
        # new sweep" would wedge the operator against a lock nothing tells
        # them still exists.
        raise ReportError(
            f"report state at {state_path} is unreadable JSON ({e}); this sweep cannot be "
            "resumed — start a new sweep, and keep this report for the lines it already holds; "
            "this failure happens before the sweep's vault lock is released, so also clear "
            "that lock (`ranger sweep start` reports it as stale, with the exact removal "
            "command, once its holder is gone) before starting the new one"
        )


def _write_0600(path: Path, text: str) -> None:
    """Write *text* to *path* atomically, owner-only from the creating syscall on.

    Two properties, both load-bearing:

    **Owner-only from creation.** The mode is an argument to ``open(2)``, not a
    ``chmod`` afterwards: a report written at the process umask and tightened a
    moment later is a file whose question text — scrubbed of credential shapes,
    but still the operator's private backlog — was world-readable for that
    moment. ``mkstemp`` creates at 0600 and ``os.replace`` carries that mode
    onto the destination.

    **Atomic replacement.** Every append re-renders the whole file, so an
    in-place truncating write would put the entire report at risk on each one:
    a crash inside that window leaves a truncated file, and the report is a
    *partial durable artifact* — an operator reads it precisely when the sweep
    died. Writing a complete temp file in the same directory and renaming it
    over the destination makes the window unobservable (``rename(2)`` is atomic
    on POSIX), and a failed write leaves the previous contents intact and no
    temp file behind.
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _write_state(report_path: Path, state: dict) -> None:
    _write_0600(_state_path(report_path), json.dumps(state, indent=2, sort_keys=True))


def _render(state: dict) -> str:
    parts = [
        "# Ranger sweep report\n\n",
        f"**Group:** {state['group']}\n",
        f"**Vault:** {state['vault']}\n",
        f"**Queue size:** {state['queue_size']} tasks derived\n\n",
    ]
    for bucket in BUCKETS:
        parts.append(f"## {_BUCKET_HEADINGS[bucket]}\n\n")
        entries = state["buckets"][bucket]
        for entry in entries:
            parts.append(entry)
        if entries:
            parts.append("\n")
    if state["finished"]:
        parts.append("---\n\n")
        parts.append(f"Report written to `{state['report_path']}`.\n")
    return "".join(parts)


def _write_report(report_path: Path, state: dict) -> None:
    _write_0600(Path(report_path), _render(state))


def _cleanup_orphaned_temp_files(reports_dir: Path, *, before: float) -> None:
    """Best-effort removal of leftover ``.*.tmp`` files in *reports_dir*.

    ``_write_0600`` writes by ``mkstemp`` then ``os.replace``; a process
    killed in that window leaks the temp file, and nothing else in this
    module ever revisits the reports directory to find it — a report is
    addressed by the path ``start`` returned, never rediscovered by listing.
    Left alone, every crash between those two calls adds one more file that
    sits in the reports directory forever. Only files whose mtime predates
    *before* (the sweep about to start) are removed, so a temp file from a
    write genuinely in flight — from a sweep starting concurrently against a
    different vault in the same group — is never touched. Every failure is
    swallowed: this is housekeeping, not the sweep's own write path, and must
    never turn `start` into a refusal over a file it doesn't otherwise care
    about.
    """
    try:
        candidates = list(reports_dir.glob(".*.tmp"))
    except OSError:
        return
    for candidate in candidates:
        try:
            if candidate.stat().st_mtime < before:
                candidate.unlink()
        except OSError:
            continue


def start(group: str, vault: str, queue_size: int, *, env: dict[str, str] | None = None) -> Path:
    """Create the report + state files for a fresh sweep, return the report path.

    The report is created 0600 with the header and all seven bucket headings
    rendered before any task is appended. Also sweeps the group's reports
    directory for orphaned temp files a prior crashed write left behind (see
    `_cleanup_orphaned_temp_files`) — cheap, best-effort hygiene that never
    blocks this sweep from starting.
    """
    _validate_group(group)
    reports_dir = ensure_dir(state_dir("ranger", env=env) / _REPORTS_SUBDIR / group, mode=0o700)
    now = datetime.now(timezone.utc)
    _cleanup_orphaned_temp_files(reports_dir, before=now.timestamp())
    timestamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    report_path = reports_dir / f"{timestamp}.md"

    state = {
        "group": group,
        "vault": vault,
        "queue_size": queue_size,
        "report_path": str(report_path),
        "appended_task_ids": [],
        "buckets": {bucket: [] for bucket in BUCKETS},
        "finished": False,
    }
    _write_state(report_path, state)
    _write_report(report_path, state)
    return report_path


def elected_vault(report_path: Path) -> str:
    """Return the vault name the sweep that owns *report_path* elected.

    Pinned at ``start`` and read back per verb, because each verb is its own
    process and the election is cwd-driven: re-resolving it in ``record``
    would let a verb run from a different directory read and quote a different
    vault than the one the sweep is draining and holds the lock on.

    Two consumers: the record read that lifts a question (in the ``record``
    verb), and the ``--vault`` the rendered answer command carries into the
    operator's own shell. Both must name the drained vault, not whichever one
    the reader's cwd or lore's config order would pick.
    """
    return _load_state(report_path)["vault"]


def finish(report_path: Path) -> None:
    """Append the footer naming the report's own absolute path.

    ``start`` is the only entry point that needs ``env`` — it is what resolves
    the reports directory. Every later call is addressed by the report path
    ``start`` returned, so none of them resolve a state dir at all.
    """
    state = _load_state(report_path)
    state["finished"] = True
    state["report_path"] = str(Path(report_path).resolve())
    _write_state(report_path, state)
    _write_report(report_path, state)


def _append(
    report_path: Path,
    bucket: str,
    task_id: str,
    render: Callable[[str], str],
    untrusted: str = "",
) -> None:
    """Render one bucket line from *render* + *untrusted*, scrubbed, and persist.

    This is the sole place any bucket-writer's free text reaches a bucket —
    every ``append_*`` function funnels through it — so scrubbing here,
    rather than at each caller, is what makes the scrub bypass-proof: a
    future bucket-writer cannot forget to scrub its own free text, because
    ``render`` is only ever called with the already-scrubbed string, never
    the raw one. ``render`` must build the line using only that scrubbed
    string plus fixed/trusted text (the task id, backticks, command syntax)
    — never close over unscrubbed free text of its own, or it defeats the
    funnel this function exists to be.

    Scrubbing is applied to *untrusted* alone, not to ``render``'s output —
    see the module docstring for why: the high-entropy pattern matches any
    32+ character alnum run, and a task id or the answer command's own fixed
    syntax can be exactly that long, so scrubbing the assembled line would
    occasionally redact trusted text along with any real secret next to it.
    """
    state = _load_state(report_path)
    if task_id in state["appended_task_ids"]:
        return
    state["appended_task_ids"].append(task_id)
    state["buckets"][bucket].append(render(scrub_credentials(untrusted)))
    _write_state(report_path, state)
    _write_report(report_path, state)


def append_promoted(report_path: Path, task_id: str) -> None:
    _append(report_path, "promoted", task_id, lambda _safe: f"- `{task_id}`\n")


def append_routed(report_path: Path, task_id: str, target: str) -> None:
    _append(
        report_path, "routed", task_id,
        lambda safe_target: f"- `{task_id}` — routed to {safe_target}\n",
        target,
    )


def append_blocked_answered(report_path: Path, task_id: str) -> None:
    _append(report_path, "blocked-answered", task_id, lambda _safe: f"- `{task_id}`\n")


def append_skipped(report_path: Path, task_id: str, reason: str) -> None:
    _append(
        report_path, "skipped", task_id,
        lambda safe_reason: f"- `{task_id}` — {safe_reason}\n",
        reason,
    )


def append_failed(report_path: Path, task_id: str, reason: str) -> None:
    _append(
        report_path, "failed", task_id,
        lambda safe_reason: f"- `{task_id}` — {safe_reason} {_FAILED_RETRY_SENTENCE}\n",
        reason,
    )


def _question_entry(
    task_id: str, record_body: str, vault: str, *, near_miss: bool
) -> tuple[Callable[[str], str], str]:
    """Return the ``(render, untrusted)`` pair ``_append`` needs for a question line.

    Two shapes, and which one is used is decided by the record, not the
    caller. A parseable question renders the question plus the exact
    invocation that answers it; an unparseable one renders
    ``_MISSING_QUESTION_LINE`` and no command. A malformed record is never
    fatal here — the sweep's rule that an unparseable outcome buckets rather
    than raises applies just as much to an unparseable record body, and a
    sweep that exits on one bad record leaves every task behind it undrained.

    The question text is the only untrusted piece — the task id, the answer
    command's syntax, and the near-miss hint are all fixed/trusted, so
    ``render`` closes over them directly and only ever receives the
    (already-scrubbed) question as its argument.

    **The invocation renders at column 0**, outside the bullet's indentation,
    because the operator copies it out of the raw markdown: a heredoc whose
    ``EOF`` terminator carries the list item's two-space indent is a terminator
    the shell never recognizes, and an indented diff body is a hunk that no
    longer applies.
    """
    try:
        question, line_no = extract_question(record_body)
    except QuestionExtractionError:
        def render(_safe: str) -> str:
            lines = [f"- `{task_id}` — {_MISSING_QUESTION_LINE}\n"]
            if near_miss:
                lines.append(f"\n{_NEAR_MISS_LINE}\n")
            lines.append("\n")
            return "".join(lines)

        return render, ""

    answer_command = _build_answer_command(task_id, line_no, vault)

    def render(safe_question: str) -> str:
        lines = [
            f"- `{task_id}` — {safe_question}\n\n",
            "Answer with:\n\n",
            "```\n",
            *(f"{line}\n" for line in answer_command.splitlines()),
            "```\n",
        ]
        if near_miss:
            lines.append(f"\n{_NEAR_MISS_LINE}\n")
        lines.append("\n")
        return "".join(lines)

    return render, question


def append_escalated(
    report_path: Path, task_id: str, record_body: str, *, near_miss: bool = False
) -> None:
    render, untrusted = _question_entry(
        task_id, record_body, elected_vault(report_path), near_miss=near_miss
    )
    _append(report_path, "escalated-awaiting-operator", task_id, render, untrusted)


def append_blocked_still_waiting(
    report_path: Path, task_id: str, record_body: str, *, near_miss: bool = False
) -> None:
    render, untrusted = _question_entry(
        task_id, record_body, elected_vault(report_path), near_miss=near_miss
    )
    _append(report_path, "blocked-still-waiting", task_id, render, untrusted)


def append_unreadable_record(report_path: Path, bucket: str, task_id: str) -> None:
    """Report a task whose record could not be read back from the vault.

    Carries no question and no answer command — there is no body to lift
    either from — but keeps the task's line in the bucket its derivation put it
    in, so the report still accounts for every task the sweep touched.
    """
    _append(
        report_path, bucket, task_id,
        lambda _safe: f"- `{task_id}` — {_UNREADABLE_RECORD_LINE}\n\n",
    )
