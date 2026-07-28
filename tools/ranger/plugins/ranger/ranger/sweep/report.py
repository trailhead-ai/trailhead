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
reaches ``finish`` (no footer is simply absent, not a corrupt file).

Bucket set is fixed by the spec this module implements: ``promoted``,
``escalated-awaiting-operator``, ``routed``, ``blocked-answered``,
``blocked-still-waiting``, ``skipped``, ``failed``. ``blocked-answered`` covers a
previously-``blocked`` task whose recorded answer let it dispatch this sweep —
its ritual outcome already flows into a status write, not a further bucket
split, so no question accompanies that line. ``escalated-awaiting-operator``
and ``blocked-still-waiting`` are the two "still needs an operator" buckets, and
both carry the task's one-line question plus a ready-to-paste answer command.

**Reading record bodies stays here, not in the coordinator.** Per the package
docstring, the CLI (this module, driven by the future ``ranger sweep record``
verb) is what reads a task record's raw body — an escalated or blocked-still-
waiting line is built from that raw text, so the extracted question, and the
answer command built from it, never transit the dispatched agent's one-line
return or the coordinating session's context.

**Credential-pattern scrub, always.** The visible question text is scrubbed
before it is written anywhere (ported from craft execute's Phase 5 five-
category regex list, upgraded per the qualifier-text/vendor-prefix lesson so
compound names like ``STRIPE_SECRET_KEY=`` are caught). The answer command's
diff carries **no context lines at all** — it is a pure positional insertion
(``old_count=0``) that names only the line number to insert after, never the
original line's text — so the one place a raw, unscrubbed secret could
otherwise leak (the diff needing to quote the surrounding line verbatim to
satisfy the applier's context check) never arises. The tradeoff: a positional
insert cannot detect drift the way a context-checked hunk would, so a record
edited between report generation and the operator's paste could land the
``**Answer:**`` line in a slightly different spot in the section — accepted,
since the alternative is writing the secret into a git-backed report.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from trailhead.paths import ensure_dir, state_dir

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

_UNRESOLVED_HEADING = "## Refine — unresolved"
_QUESTION_PREFIX = "**Question:**"

_NEAR_MISS_LINE = (
    "answer detected but not recognized — expected `**Answer:**` inside "
    "`## Refine — unresolved`"
)

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
    """Raised for report-writer failures: no report started at a given path,
    or a group name that fails path-segment confinement."""


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
    """
    lines = record_body.splitlines(keepends=True)
    heading_idx = None
    for i, line in enumerate(lines):
        if line.rstrip("\n") == _UNRESOLVED_HEADING:
            heading_idx = i
            break
    if heading_idx is None:
        raise QuestionExtractionError(f"record body has no {_UNRESOLVED_HEADING!r} section")

    for i in range(heading_idx + 1, len(lines)):
        stripped = lines[i].rstrip("\n")
        if stripped.startswith(_QUESTION_PREFIX):
            return stripped[len(_QUESTION_PREFIX):].strip(), i + 1

    raise QuestionExtractionError(
        f"no {_QUESTION_PREFIX!r} line found inside the {_UNRESOLVED_HEADING!r} section"
    )


def _build_answer_command(task_id: str, question_line_no: int) -> str:
    """Build the exact `lore record update <task_id> --diff` invocation that
    inserts a `**Answer:**` line immediately after the question line.

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
    return f"lore record update {task_id} --diff <<'EOF'\n{diff}EOF"


def _validate_group(group: str) -> None:
    if not group:
        raise ReportError("group must not be empty")
    if "/" in group or "\\" in group or os.sep in group or ".." in group:
        raise ReportError(f"group {group!r} must not contain path separators or '..'")


def _state_path(report_path: Path) -> Path:
    return Path(report_path).with_suffix(".state.json")


def _load_state(report_path: Path) -> dict:
    state_path = _state_path(report_path)
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ReportError(f"no report started at {report_path}: {e}")


def _write_state(report_path: Path, state: dict) -> None:
    state_path = _state_path(report_path)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    state_path.chmod(0o600)


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
    Path(report_path).write_text(_render(state), encoding="utf-8")
    Path(report_path).chmod(0o600)


def start(group: str, vault: str, queue_size: int, *, env: dict[str, str] | None = None) -> Path:
    """Create the report + state files for a fresh sweep, return the report path.

    The report is created 0600 with the header and all seven bucket headings
    rendered before any task is appended.
    """
    _validate_group(group)
    reports_dir = ensure_dir(state_dir("ranger", env=env) / _REPORTS_SUBDIR / group, mode=0o700)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
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


def _append(report_path: Path, bucket: str, task_id: str, entry: str) -> None:
    state = _load_state(report_path)
    if task_id in state["appended_task_ids"]:
        return
    state["appended_task_ids"].append(task_id)
    state["buckets"][bucket].append(entry)
    _write_state(report_path, state)
    _write_report(report_path, state)


def append_promoted(report_path: Path, task_id: str) -> None:
    _append(report_path, "promoted", task_id, f"- `{task_id}`\n")


def append_routed(report_path: Path, task_id: str, target: str) -> None:
    _append(report_path, "routed", task_id, f"- `{task_id}` — routed to {target}\n")


def append_blocked_answered(report_path: Path, task_id: str) -> None:
    _append(report_path, "blocked-answered", task_id, f"- `{task_id}`\n")


def append_skipped(report_path: Path, task_id: str, reason: str) -> None:
    _append(report_path, "skipped", task_id, f"- `{task_id}` — {reason}\n")


def append_failed(report_path: Path, task_id: str, reason: str) -> None:
    entry = f"- `{task_id}` — {reason} {_FAILED_RETRY_SENTENCE}\n"
    _append(report_path, "failed", task_id, entry)


def _render_question_entry(task_id: str, record_body: str, *, near_miss: bool) -> str:
    question, line_no = extract_question(record_body)
    scrubbed_question = scrub_credentials(question)
    answer_command = _build_answer_command(task_id, line_no)
    lines = [
        f"- `{task_id}` — {scrubbed_question}\n\n",
        "  Answer with:\n\n",
        "  ```\n",
        *(f"  {line}\n" for line in answer_command.splitlines()),
        "  ```\n",
    ]
    if near_miss:
        lines.append(f"  {_NEAR_MISS_LINE}\n")
    lines.append("\n")
    return "".join(lines)


def append_escalated(
    report_path: Path, task_id: str, record_body: str, *, near_miss: bool = False
) -> None:
    entry = _render_question_entry(task_id, record_body, near_miss=near_miss)
    _append(report_path, "escalated-awaiting-operator", task_id, entry)


def append_blocked_still_waiting(
    report_path: Path, task_id: str, record_body: str, *, near_miss: bool = False
) -> None:
    entry = _render_question_entry(task_id, record_body, near_miss=near_miss)
    _append(report_path, "blocked-still-waiting", task_id, entry)
