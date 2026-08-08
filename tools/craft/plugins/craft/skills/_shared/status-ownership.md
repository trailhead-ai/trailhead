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

## Pre-authorized carve-outs for an unattended run

`../_shared/execute.md`'s mode table re-routes every attended escalation point
through escalate-via-park or proceed-per-contract when the caller has no human
channel. Two of those re-routes are decisions this contract pre-authorizes up front,
named here so an unattended run never has to invent an answer:

- **(a) Under a loop, the loop session is the sole task-status writer.** This is the
  same rule the next section states generally — "vanilla usage: the session running
  `/craft:execute`; under a loop: the loop session. That session is the only writer of
  task status" — restated here as a carve-out because an unattended dispatch makes it
  load-bearing: the dispatched `executor` **never writes status**, under a loop exactly
  as much as it never does in the attended path. A dispatched agent reports its
  outcome (`PUSHED <branch> <sha>` / `BLOCKED <reason>`) and the loop session performs
  the write; nothing in the unattended mode table licenses a dispatched agent to write
  its own status.
- **(b) The PR decision is pre-authorized only into the portage tail.** Once a push
  succeeds, whether and when to open, merge, or otherwise decide a pull request is
  never the unattended run's call to make directly — it is pre-authorized **only**
  into the portage tail (`updater`/`monitor`), the same pipeline an attended operator
  would eventually hand a pushed branch to. An unattended run never merges and never decides the PR outside that pipeline: no drain, no loop session, and no dispatched
  agent applies a merge decision itself — the decision belongs to `updater`/`monitor`
  once the run's task-status obligations are settled.

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
- **`ready → in-progress`** — writer: the orchestrating session, at the run's
  first dispatch. This value belongs to the **run's task** — a plan's parent, or
  a standalone leaf run on its own; child slices under a plan walk
  `ready → done` and never take it. Exit owner: the two execute exit writes
  below (done and blocked), plus reconciliation on resume. An escalation
  *answered* in-session writes no status — the run continues and the task holds
  `in-progress` until one of those two writes lands.
- **`in-progress → done`** — writer: the orchestrating session at close,
  **after push succeeds** (see push guarantee below). `done` is terminal for
  craft; there is no further exit edge to own.
- **`* → blocked`** — writer: the orchestrating session, as a judgment call when
  an escalation ends the run unresolved. Never written mechanically by a
  dispatched agent. Exit owner: the operator's recorded answer, acted on by a
  sweep (a future ranger refine loop) or, until that ships, by hand — the
  by-hand path is the sufficient interim exit owner.
- **`blocked → open` / `blocked → ready`** — writer: whoever acts on the
  operator's answer (interim: the operator by hand). `ready` when the answer
  makes the task workable as-is; `open` when the answer changes its shape enough
  to need re-refinement first.

## `done` = committed and pushed

Execute's close phase pushes with `git push --set-upstream origin HEAD` for
every repo in the workspace carrying commits on the task branch (a bare
`git push origin` never converges without an upstream — always use the
`--set-upstream` form verbatim). `done` is written only after all such pushes
succeed; push failure keeps the task `in-progress` — the honest state — with a
`craft/push=failed` label (see Label conventions) so resume logic skips and
reports rather than silently re-building.

**Auto-push covers task branches only.** A run sitting on the repo's default
branch — one started on `main`/`master` with explicit user consent — is not
auto-pushed, so its `done` carries no push guarantee. The completion report must
say so, naming the branch and its unpushed commits, so a `done` whose guarantee
never applied is not mistaken for one whose guarantee held.

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
  `label.craft.branch:<name>` — the **key** takes the dot-for-slash spelling,
  since an unquoted `/` is a lexer error; the match is exact on the value, and a
  value containing `/` is fine as long as it is quoted
  (`label.craft.branch:"feat/foo"`).
- **`craft/push=failed`** — written when a push attempt fails at close or at a
  `blocked` transition with unpushed commits. Lets resume logic distinguish
  "un-pushable" from "crashed" and skip-and-report instead of re-running the
  build.
- Both are **single-valued, last-write-wins**, and last-write-wins applies **per
  key**: `--label` on `lore record update` is a repeatable upsert that mutates
  only the keys it names, leaving every other key untouched. Re-asserting
  `craft/branch` therefore cannot disturb `craft/push` — only another
  `craft/push=…` write, or an explicit `--unset-label craft/push`, replaces a
  `craft/push=failed` skip guard, and it does so with no trace that the guard was
  ever set.

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
condition that would clear it, and the next action.

**That text is appended, never substituted.** Write it with
`lore record update task/<name> --status blocked --diff`, piping a unified diff
whose hunks add it — the two flags combine in one invocation. Piping the note
over bare stdin instead is a **full-body replace**: it silently destroys
everything the task record already held.

If commits exist when a task goes `blocked`, they are pushed on the task's
branch (same `--set-upstream` rule as `done`) and `craft/branch` is (re-)written
with its own `lore record update task/<name> --label craft/branch=<bare-branch>`
— never the close command, which also writes `--status done` — so parked work
resumes from the remote rather than restarting from zero. A push that fails here
leaves the task `blocked` and adds `craft/push=failed`; the status is not
reverted to `in-progress`.

That push carries the same precondition as `done`'s: the outgoing commits are
scanned against the credential-pattern scrub list in `execute/SKILL.md`'s
flow-out phase first, and on a hit the repo is not pushed. A task blocked
*because* that scan hit is therefore **never** pushed — the credential is
rotated and the history rewritten before any push is attempted. Going `blocked`
is not a licence to ship flagged commits.

**That scan is fail-closed: a scan command that errors is never a clean scan.**
Empty output clears a repo only when the command also exited successfully — an
errored command prints nothing either, and the two are indistinguishable by
output alone. The case that bites is a task branch before its first
`--set-upstream` push: with no `origin/<branch>` remote-tracking ref to diff
against, the usual `git log origin/<branch>..HEAD -p` fails outright rather than
reporting a clean tree. Scan such a branch with
`git log HEAD --not --remotes=origin -p`, which needs no upstream and covers the
whole outgoing history the push is about to publish.

Every body write made during a run — blocked reasons, task-body notes, report
text captured into records — runs through the credential-pattern scrub already
mandated at the close phase in `execute/SKILL.md` before it lands in the
git-backed vault: raw git/auth error text is never captured verbatim.

## Operator-facing: by-hand sweep queries

Until a sweep ritual exists, clearing `blocked` and `craft/push=failed` tasks is
a by-hand operation. The `label.craft.branch:` / `has:label.` search spellings
aren't discoverable from CLI help, so the queries aren't otherwise obvious — use
these directly:

```sh
# find every task record on a given branch (crash-resume / parked-work lookup)
lore search 'label.craft.branch:<name>'

# find every task carrying a craft push label (failed pushes to triage)
lore search 'has:label.craft.push'

# clear the skip guard once a failed push has been settled by hand
lore record update task/<name> --unset-label craft/push
```
