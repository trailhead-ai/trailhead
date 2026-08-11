"""Sweep queue derivation and classification for an elected vault.

Derives the candidate queue for a sweep by shelling out to `lore task list
--vault <name> --status open --status blocked --json` (never importing
lore's internals — the queue-read capability lives in lore, matching the
`lore task list` verb's own docstring, and keeping ranger decoupled from
lore's on-disk vault/record layout). Every lore invocation goes through the
injectable `Runner` callable below, exactly like `trailhead.vcs.runner`'s
seam: production code calls `derive_queue` with no `runner`, tests inject a
stub to capture calls without touching a real `lore` install.

**Shape gate.** Only standalone tasks (no `parent`, no `children`) are
candidates for the sweep — the same gate refine's Step 1 applies before
promoting a task, mirrored here because a task with a parent or children is
either a plan slice (owned by `execute`, not the sweep) or a plan itself.

**Classification.** Each candidate lands in exactly one of four buckets:

- `dispatchable` — `open`, and either no `## Refine — unresolved` section at
  all, or the section is present and answered (the answer re-entry path: an
  operator answered a previously-escalated question, so the task is ready
  to be picked up again).
- `escalated-awaiting-operator` — `open` with an unanswered `## Refine —
  unresolved` section. A churn guard: this task is never dispatched again
  until an operator adds a `**Answer:**` line, however many times the sweep
  re-derives the queue.
- `blocked-answered` — `blocked`, and the section is answered. Also
  dispatchable (the sweep re-attempts a previously-blocked task once
  answered), but reported under its own bucket since it carries a different
  history than a plain `dispatchable` task.
- `blocked-still-waiting` — `blocked`, and the section is not answered.

**Answered predicate (strict).** A line beginning with the literal,
exact-case `**Answer:**` inside the `## Refine — unresolved` section, which
itself is located by an exact, single-line, literal match of the heading
(including the U+2014 em dash) — a wrapped heading or one using a different
dash character is not recognized. This predicate is deliberately narrow:
loosening it to fuzzy-match near-misses would let an operator's typo or a
stray line elsewhere in the body silently promote a task that was never
actually answered.

**Near-miss signal (informational, never dispatch-affecting).** Because the
predicate above is strict, `classify` also reports `answer_near_miss` when
it detects a plausible-but-not-recognized answer attempt: a case/format
variant of `**Answer:**` (e.g. `Answer:`, `**answer:**`) inside the section,
or an exact `**Answer:**` line that landed outside the section. Either way
the task is still routed to whichever "waiting" bucket its status implies —
the flag exists purely so a report can say "an answer was attempted but not
recognized" instead of the ambiguous "never answered".

**Read-only.** This module never calls `lore record update` or otherwise
mutates a record — it only lists and reads.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Callable

# Type alias for the injectable lore-CLI runner, matching
# trailhead.vcs.runner's Runner protocol: runner(cmd, **kwargs) ->
# subprocess.CompletedProcess, cmd always a list (shell=False).
Runner = Callable[..., subprocess.CompletedProcess]

BUCKETS = (
    "dispatchable",
    "escalated-awaiting-operator",
    "blocked-answered",
    "blocked-still-waiting",
)

#: The buckets whose tasks the loop actually dispatches. The other two are
#: reported once and never drained, so a coordinator asking "what is left to
#: do?" wants only these — see :func:`actionable`.
ACTIONABLE_BUCKETS = ("dispatchable", "blocked-answered")

_TASK_KIND = "task"

#: The one spelling of the escalation heading, shared with the report writer
#: so the classifier and the question extractor can never disagree about which
#: heading opens the section or where it ends.
UNRESOLVED_HEADING = "## Refine — unresolved"
_ANSWER_PREFIX = "**Answer:**"
_ANSWER_NEAR_MISS_RE = re.compile(r"^\*{0,2}answer:", re.IGNORECASE)


class QueueDeriveError(Exception):
    """Raised when a `lore` CLI call the derivation depends on cannot be run at
    all (no `lore` on PATH), fails, or emits output the derivation can't parse
    as JSON."""


def default_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Production runner: subprocess.run with shell=False, output captured as text.

    Public so `ranger.drain.queue.run_camp` can share this exact runner
    rather than carrying its own duplicate copy — see that module's
    docstring for why the two are meant to be the same seam.
    """
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", False)
    return subprocess.run(cmd, **kwargs)


def run_lore(argv: list[str], *, runner: Runner | None) -> Any:
    """Run `lore <argv>` via the injectable runner and return its parsed JSON stdout.

    Shared with `ranger.sweep.preflight`, which shells to `lore vault resolve`
    through the same seam so a sweep's every lore call is stubbable at one point.
    """
    effective = runner if runner is not None else default_runner
    cmd = ["lore", *argv]
    # An absent (or unexecutable) `lore` is a dependency failure, not a bug:
    # nothing enforces plugin dependencies at install time, so these runtime
    # calls are the sweep's only guard, and they must refuse in the same named,
    # remediable shape every startup check uses rather than surface the
    # subprocess layer's own exception to an unattended operator.
    try:
        result = effective(cmd)
    except FileNotFoundError as exc:
        raise QueueDeriveError(
            "lore CLI not found on PATH — install lore or adjust PATH"
        ) from exc
    except OSError as exc:
        raise QueueDeriveError(
            f"lore CLI could not be run ({exc}) — install lore or adjust PATH"
        ) from exc
    if result.returncode != 0:
        raise QueueDeriveError(
            f"lore {' '.join(argv)} failed: {result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise QueueDeriveError(
            f"lore {' '.join(argv)} emitted unparseable JSON: {exc}"
        ) from exc


def _list_standalone_candidates(vault: str, *, runner: Runner | None) -> list[dict]:
    """Return every open/blocked standalone task in *vault*, oldest-first.

    Standalone means no `parent` and no `children` — the containment shape
    gate, applied here rather than trusted from the caller, since a plan
    slice can carry `open`/`blocked` status too and is never sweep-owned.
    """
    entries = run_lore(
        [
            "task", "list",
            "--vault", vault,
            "--status", "open",
            "--status", "blocked",
            "--json",
        ],
        runner=runner,
    )
    standalone = [e for e in entries if e.get("parent") is None and not e.get("children")]
    standalone.sort(key=lambda e: (e.get("created-at") or "", e["name"]))
    return standalone


def read_body(name: str, *, vault: str, runner: Runner | None) -> str:
    """Read a task record's raw body from *vault* via `lore record show`, read-only.

    `--vault` is not optional. Without it `record show` locates the record by
    a cwd-blind first-match scan across the configured vaults in declaration
    order, so a task name that exists in more than one vault is read from
    whichever one lore's config lists first — and this body is what decides
    the task's bucket and supplies the escalated question the report hands the
    operator. Naming the elected vault is what keeps both of those about the
    vault the sweep is actually draining.
    """
    payload = run_lore(
        ["record", "show", f"{_TASK_KIND}/{name}", "--vault", vault, "--json"],
        runner=runner,
    )
    return payload.get("body", "")


def unresolved_section_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Return the (start, end) line-index range of the unresolved section's
    body, exclusive of the heading itself, or None if the heading is absent.

    The heading must match `UNRESOLVED_HEADING` exactly on a single physical
    line — a wrapped heading or a different dash character never matches.
    The section ends at the next `## ` heading, or at the end of the body.

    Shared with the report writer's question extractor: the line the answer
    command inserts at has to fall inside the same bounds the answered
    predicate below checks, or an operator's pasted answer would never be
    recognized as one.
    """
    heading_idx = None
    for i, line in enumerate(lines):
        if line.rstrip("\n") == UNRESOLVED_HEADING:
            heading_idx = i
            break
    if heading_idx is None:
        return None

    end = len(lines)
    for i in range(heading_idx + 1, len(lines)):
        if lines[i].rstrip("\n").startswith("## "):
            end = i
            break
    return heading_idx + 1, end


def classify(status: str, body: str) -> tuple[str, bool]:
    """Classify a single candidate's (status, body) into (bucket, answer_near_miss).

    See the module docstring for the bucket set, the strict answered
    predicate, and the near-miss signal's three trigger cases.
    """
    lines = body.splitlines(keepends=True)
    bounds = unresolved_section_bounds(lines)
    answered = False
    near_miss = False

    if bounds is not None:
        start, end = bounds
        for i in range(start, end):
            stripped = lines[i].rstrip("\n").strip()
            if stripped.startswith(_ANSWER_PREFIX):
                answered = True
            elif _ANSWER_NEAR_MISS_RE.match(stripped):
                near_miss = True

        if not answered:
            for i, line in enumerate(lines):
                if start <= i < end:
                    continue
                if line.rstrip("\n").strip().startswith(_ANSWER_PREFIX):
                    near_miss = True
                    break

    if status == "open":
        bucket = "escalated-awaiting-operator" if (bounds is not None and not answered) else "dispatchable"
    elif status == "blocked":
        bucket = "blocked-answered" if answered else "blocked-still-waiting"
    else:
        raise QueueDeriveError(f"unexpected status {status!r} for a sweep queue candidate")

    return bucket, near_miss


def derive_queue(vault: str, *, runner: Runner | None = None) -> list[dict]:
    """Derive and classify the sweep queue for *vault*.

    Returns a list of dicts — each candidate's `lore task list` entry
    (`name`, `status`, `created-at`, `updated-at`, `parent`, `depends-on`,
    `children`) plus `bucket` and `answer_near_miss` — ordered oldest-first
    by `created-at` with a record-name tiebreak.
    """
    candidates = _list_standalone_candidates(vault, runner=runner)
    queue: list[dict] = []
    for entry in candidates:
        body = read_body(entry["name"], vault=vault, runner=runner)
        bucket, near_miss = classify(entry["status"], body)
        queue.append({**entry, "bucket": bucket, "answer_near_miss": near_miss})
    return queue


def actionable(entries: list[dict]) -> list[dict]:
    """Return only the entries in :data:`ACTIONABLE_BUCKETS`, order preserved.

    The coordinator re-derives after every task and needs one answer from it:
    what is still dispatchable. Serving that question from the full queue
    means the two never-dispatched buckets — which by design persist for the
    whole sweep — are re-read on every pass, and their entries dominate the
    output on any queue that has accumulated escalations. Filtering here keeps
    the loop's own view proportional to the work it has left.
    """
    return [e for e in entries if e.get("bucket") in ACTIONABLE_BUCKETS]
