# Task-status ownership contract (shared reference)

This is the single source of truth for craft's task-status contract: who writes
each status value, who owns closing out each exit edge, and what a `done` claim
guarantees. `execute/SKILL.md` references this doc rather than restating it (the
`_shared/council.md` precedent); the standalone-leaf path in the refine skill and
the future ranger loops will reference it too. Read standalone — do not assume
you arrived here from `execute/SKILL.md`.

## Contract ownership

The plugin whose ritual acts on a record kind defines what that kind's status
values mean and when they are written — the *primary contract owner*. For `task`
records that plugin is craft. No other plugin writes a task status outside
craft's contract semantics defined below.

## Write execution: the top-level orchestrating session writes status

The **top-level orchestrating session** is the session that invoked the ritual
and owns the operator conversation — vanilla usage: the session running
`/craft:execute`; under a loop: the loop session. That session is the only
writer of task status.

A **dispatched agent never writes status.** It reports its outcome in a fixed
shape and the invoking session performs the write:

- `PUSHED <branch> <sha>` — the agent's work is committed and (where it owns the
  push) pushed; the invoker writes `done`.
- `BLOCKED <reason>` — the run cannot continue; the invoker judges whether to
  write `blocked` (see below) or resolve inline and continue.

## Status vocabulary: writer and exit owner per transition

Every value below names both who writes it and who owns closing out its exit
edge — a status with no named exit owner is how a value becomes a permanent
high-water mark instead of current state.

- **`open → ready`** — writer: the refine ritual (standalone tasks) or the
  planning ritual (child tasks under a plan). Exit owner: the execute dispatch
  step, which picks up `ready` leaves and moves them off this value.
- **`ready → in-progress`** — writer: the orchestrating session, at first
  dispatch. Exit owner: every execute exit path below (done, blocked, or
  answered-and-continuing), plus reconciliation on resume.
- **`in-progress → done`** — writer: the orchestrating session at close,
  **after push succeeds** (see push guarantee below). `done` is terminal for
  craft; there is no further exit edge to own.
- **`* → blocked`** — writer: the orchestrating session, as a judgment call when
  an escalation ends the run unresolved. Never written mechanically by a
  dispatched agent. Exit owner: the operator's recorded answer, acted on by a
  sweep (a future ranger refine loop) or, until that ships, by hand — the
  by-hand path is the sufficient interim exit owner.

## `done` = committed and pushed

Execute's close phase pushes with `git push --set-upstream origin HEAD` for
every repo in the workspace carrying commits on the task branch (a bare
`git push origin` never converges without an upstream — always use the
`--set-upstream` form verbatim). `done` is written only after all such pushes
succeed; push failure keeps the task `in-progress` — the honest state — with a
`craft/push=failed` label (see Label conventions) so resume logic skips and
reports rather than silently re-building.

A **child** task's `done` under a plan means only "committed on the task
branch" — bookkeeping within the run, no durability claim. The push guarantee
attaches to the root/standalone `done`.

Push is idempotent: an already-up-to-date branch counts as success, so a
status-write failure after a successful push is safe to retry. The status write
is the completion signal; the push is its precondition.

## Label conventions

Craft owns the `craft/` label-key namespace for its own lifecycle facts.

- **`craft/branch`** — the bare local branch name (`worktree-foo`, never
  `origin/worktree-foo`). Written when the task branch is cut / at first
  dispatch (not only at close — its primary reader is crash-resume logic, which
  runs on tasks that never reached close) and re-asserted at close. Queried as
  `label.craft.branch:<name>` — dot-for-slash spelling, exact-match; a literal
  `/` in a KQL query is a lexer error.
- **`craft/push=failed`** — written when a push attempt fails at close or at a
  `blocked` transition with unpushed commits. Lets resume logic distinguish
  "un-pushable" from "crashed" and skip-and-report instead of re-running the
  build.
- Both are **single-valued, last-write-wins**: `--label` on `lore record update`
  is a repeatable upsert, and a later write silently replaces the prior value —
  there is no history. This applies to `craft/push` too: a subsequent label
  write clears a `craft/push=failed` skip guard with no trace that it was ever
  set, so don't re-assert `craft/branch` on a task carrying `craft/push=failed`
  without deliberately deciding whether that guard should still hold.

## `in-progress` is a lease stand-in, not final storage

Per the decision to defer a dedicated operational-state store
(`decision/task-dispatch-claiming-optimistic-status-as-lease-defer-the-operational-state-store`),
vault `in-progress` is an **optimistic status-as-lease** — a knowing, temporary
stand-in. When an operational-state store lands, `in-progress` claims migrate to
it; this contract is written so that migration changes the storage, not the
semantics: the writer, exit owner, and reconciliation rules above still hold
after the move.

Reconciliation (independent of ranger): invoking execute against a task already
`in-progress` resumes it — via `craft/branch` or a locally-present branch —
rather than refusing or restarting. An `in-progress` task whose workspace no
longer exists is resumed (branch recoverable) or released back to `ready`.
Tasks carrying `craft/push=failed` are skipped-and-reported, never silently
re-run.

## No PR/merge state in the vault

Per the decision that PR/merge lifecycle is operational machine state, not vault
state (`decision/observability-seam-lore-operational-state-store-not-sidecar-files`),
craft never writes PR or merge state to a task record. `done` marks the boundary
of craft's ownership — push is the last thing craft does. Where completed work
landed is queryable via `craft/branch`; whether it has since merged is a live
VCS/portage query (or, later, the operational-state store), never a task label.

## `blocked` body content and the credential scrub

A `blocked` body states: what happened, why it blocks, the specific question or
condition that would clear it, and the next action. If commits exist when a
task goes `blocked`, they are pushed on the task's branch (same
`--set-upstream` rule as `done`) and `craft/branch` is (re-)written, so parked
work resumes from the remote rather than restarting from zero.

Every status-related body write — blocked reasons, report text captured into
records — runs through the credential-pattern scrub already mandated at the
close phase in `execute/SKILL.md` before it lands in the git-backed vault: raw
git/auth error text is never captured verbatim.

## Operator-facing: by-hand sweep queries

Until a sweep ritual exists, clearing `blocked` and `craft/push=failed` tasks is
a by-hand operation. The CLI's generated command reference does not advertise
`--label` on `record update`, so the query spellings aren't otherwise
discoverable — use these directly:

```sh
# find every task record on a given branch (crash-resume / parked-work lookup)
lore search 'label.craft.branch:<name>'

# find every task carrying a craft push label (failed pushes to triage)
lore search 'has:label.craft.push'
```
