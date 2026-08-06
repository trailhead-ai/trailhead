# Operator re-entry rituals

The execute drain runs unattended, so every state it can leave a task or itself in has a
named, pinned recovery — no ritual below asks the operator to guess or to reset anything
blind. Six rituals cover the drain's stranded states; a seventh section covers the
degraded-trust mode a portage-absent install runs in. Every recovery command is a `lore`
CLI invocation or a plain shell command — **never a direct edit of a vault file**.

## 1. Failed

A task's outcome line reads `FAILED <reason>` (§5 of the loop skill: no status write
accompanies it, so the record is exactly where it was before the drain touched it), **or**
it is stuck `in-progress` carrying the `craft/push=failed` label — craft's execute ritual
sets that label when a push fails at close, and a task carrying it is never silently
resumed. Either way:

1. Inspect the report line (or the record, for the label case) to see what failed.
2. Fix the underlying problem and get the push through by hand, or decide to abandon the
   attempt.
3. Clear the guard: `lore record update task/<name> --unset-label craft/push`.
4. The task returns to `ready` once the guard is clear — nothing else holds it back.

## 2. Blocked

The task's outcome line reads `BLOCKED <reason>`; the drain has already written `status
blocked` and re-asserted `craft/branch`, and the dispatched agent has already written the
literal `## Refine — unresolved` section onto the record. The operator answers by adding an
exact-case `**Answer:**` line inside that section — the same answer-line ritual refine's
queue classifier reads (`ranger/sweep/queue.py`'s `_ANSWER_PREFIX` / answered predicate).
That single line is the whole re-entry: once it lands, a later refine or drain sweep reads
the task as `blocked-answered` and re-attempts it — the ratified answered-blocked edge.

## 3. Crashed

The coordinator itself died mid-task. Per §5's contract this leaves the task `in-progress` and its workspace preserved — a crashed run writes no status edge, by design, so the
record and the workspace are the only recovery handles.

1. Inspect the preserved workspace to see how far the build got.
2. Recover it with this exact command, which re-asserts `craft/branch`:
   `lore record update task/<name> --status ready --label craft/branch=worktree-<slug>`
3. Or abandon it: `camp remove <slug>` tears the workspace down without touching the
   task record's status.

## 4. Stale lock

The drain's vault lock lives at `state_dir("ranger")/locks/<vault>.lock`.

A held lock whose holder is no longer alive, paired with no exit report having been written for that run, is the crash signal — the same pair a dead coordinator leaves behind.

`ranger drain start` detects the staleness itself (the holder pid is checked against the
running process table) and prints the exact `rm <path>` command to clear it. The operator
runs that command by hand.

**Nothing in the drain ever removes a lock file itself, stale or not** — an automated removal race would let two drains hold the same vault at once.

## 5. Awaiting-approval / cap-stall

A `PUSHED` task's monitor outcome is `READY <reason>`: CI is green and the PR is waiting on
the human-approval gate.

The pushed bucket splits into a `merged` / `in-flight` / `awaiting-human-approval` / `monitor-timeout` substate, each flagged when it is the thing holding the in-flight cap.

To grant the signal, the operator applies the `human-approved` label
by hand — `gh pr edit <pr> --add-label human-approved` — or leaves an approving review.
`portage approvals` is what verifies the signal before monitor will merge.

No drain component, executor, or portage agent ever applies that label or approves a PR itself — a gate the automation building the change can open is not a gate.

Applying the label frees the cap slot the stalled task is holding, unblocking the next dispatch.

## 6. Corrupt state file

The drain's `.state.json` sidecar failed to parse.

`ranger.drain.report` refuses this by name rather than resetting it blind — the same refusal `ranger.sweep.report._load_state` uses, verbatim.

The file is the drain's only record of what it has already written and
dispatched, and a silent reset would replay or drop work.

1. Inspect the named `.state.json` file (the error names its path) to see what is
   recoverable before touching anything.
2. This failure surfaces before the vault lock is released, so also clear that lock —
   `ranger drain start` reports it as stale, with the exact removal command, once its
   holder is confirmed gone (ritual 4 above).
3. Start a new drain; keep the old report for the lines it already holds — it is not
   overwritten.
4. There is no `ranger drain finish` for the broken run; the lock-release step above is
   what closes it out.

## Degraded-trust mode

When `ranger drain start` reports `degraded: true`, portage is not installed in this
project: there is no PR tail, no monitor, and no in-flight cap. A `PUSHED` task is `done`
immediately at the push — its workspace tears down there too, since there is no
monitor-terminal to wait for — and the report carries a banner naming the degraded run so
an operator reading it later knows no PR, CI, or approval gate ever ran.
