# Operator re-entry rituals

The execute drain runs unattended, so every state it can leave a task or itself in has a
named, pinned recovery — no ritual below asks the operator to guess or to reset anything
blind. Six rituals cover the drain's stranded states; a seventh section covers the
degraded-trust mode a portage-absent install runs in. Every recovery command is a `lore`
CLI invocation or a plain shell command — **never a direct edit of a vault file**.

## 1. Failed

A task's outcome line reads `FAILED <reason>`, **or** it is stuck `in-progress` carrying
the `craft/push=failed` label — craft's execute ritual sets that label when a push fails at
close, and a task carrying it is never silently resumed.

The two arrive in different states, and the ritual restores both to the same one:

- **The outcome-line case.** §5 of the loop skill has already written the release edge:
  the task is back at `ready` with `craft/branch` asserted, and its workspace is preserved.
  Work may exist on that branch — the `FAILED` says the run ended, not that it committed
  nothing.
- **The `craft/push=failed` case.** Nothing was released. The task sits `in-progress` and
  the guard label is what holds it out of every queue.

1. Inspect the report line (or the record, for the label case) to see what failed.
2. Inspect the preserved workspace and the task's `craft/branch`; fix the underlying
   problem and get the push through by hand, or decide to abandon the attempt.
3. Clear the guard, if the record carries it:
   `lore record update task/<name> --vault <vault> --unset-label craft/push`
4. Put the task back in a queue — `ready`, with its branch still named:
   `lore record update task/<name> --vault <vault> --status ready --label craft/branch=worktree-<slug>`
   In the outcome-line case the loop already wrote exactly this command, so run it only if
   the record does not read `ready`. **A task left `in-progress` is re-derived by nothing**:
   the drain queue derives from `ready`, the refine sweep from `open`/`blocked`.
5. Or abandon it: `camp remove <slug>` tears the workspace down, and
   `lore record update task/<name> --vault <vault> --status dropped` takes the task out of
   every queue deliberately rather than by omission.

## 2. Blocked

The task's outcome line reads `BLOCKED <reason>`; the drain has already written `status
blocked` and re-asserted `craft/branch`, and the dispatched agent has already written the
literal `## Refine — unresolved` section onto the record. The operator answers by adding an
exact-case `**Answer:**` line inside that section — the same answer-line ritual refine's
queue classifier reads (`ranger/sweep/queue.py`'s `_ANSWER_PREFIX` / answered predicate).
That single line is the whole re-entry: once it lands, a later refine or drain sweep reads
the task as `blocked-answered` and re-attempts it — the ratified answered-blocked edge.

This ritual covers the `blocked` bucket only, and that bucket has exactly one source: an
executor agent's own parked question.
**A monitor's own `BLOCKED` line is a red PR, not a parked question**
— no `## Refine — unresolved` section exists to answer — so the drain
reports it in the `failed` bucket alongside `STOPPED`, and ritual 1 is its re-entry.

## 3. Crashed

Two entry points, one state and one recovery:

- **The coordinator itself died mid-task** — nothing is in the report for that task.
- **The dispatched executor agent left no outcome file at all** — it died, timed out, or
  never ran, and the report carries the task in its `crashed` bucket. (A *monitor* that
  wrote nothing lands in the same bucket; see §6 of the loop skill.)

Either way this leaves the task `in-progress` and its workspace preserved. The `in-progress` is the run claim
the loop wrote at dispatch (§4.4 of the loop skill) — a crashed run writes no status edge
of its own, by design, so nothing was recorded on the way down and the claim plus the
workspace are the only recovery handles.

1. Inspect the preserved workspace to see how far the build got.
2. Recover it with this exact command, which re-asserts `craft/branch`:
   `lore record update task/<name> --vault <vault> --status ready --label craft/branch=worktree-<slug>`
3. Or abandon it: `camp remove <slug>` tears the workspace down without touching the
   task record's status.

## 4. Stale lock

The drain's vault lock lives at `state_dir("ranger")/locks/<vault>.lock`.

A held lock whose holder is no longer alive, paired with an unfinished exit report, is the crash signal — the same pair a dead coordinator leaves behind.

**An absent report is not the signal.** `ranger drain start` writes the report at the same
moment it takes the lock, so every locked vault has one. The marker to check is the
report's footer, which only `ranger drain finish` writes: a `---` rule followed by
``Report written to `<path>`.`` at the end of the file. Present, the run closed out and the
lock is someone else's; absent, the run died holding it.

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

**The signal is pinned to the commit it was given on.** A review approves its `commit_id`,
and a label approves the head commit standing when it was applied — so any push after that
point makes the signal stale, and `portage approvals` reports it as stale rather than
approved. That is deliberate: without it, a fix cycle that pushes new commits inherits an
approval no human gave those commits.
So **after every fix cycle on an approved PR, re-approve it** — leave a fresh review, or
remove and re-apply the `human-approved` label — once you have reviewed the commits that
landed since. Nothing else clears a stale signal, and nothing in the automation may clear
it for you.

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

The banner also names the one substate this mode strands: with no monitor to resolve them,
pushed tasks stay under **In flight** for the life of the report. That is the terminal
state of a degraded run, not a stall — there is no ritual to run against it.
