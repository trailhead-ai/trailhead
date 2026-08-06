---
name: execute
description: >
  Drain a camp group's buildable queue unattended — build every runnable standalone task in
  the group's elected lore vault through craft's execute ritual, one dispatched agent per
  task in its own ephemeral camp workspace, push each to a PR behind a human-approval merge
  gate, and hand back a durable exit report.
  TRIGGER when: user says "run the execute drain", "drain the ready queue", "build the
  backlog", "drain the buildable tasks", "build whatever is runnable", or invokes
  /ranger:execute explicitly.
  DO NOT TRIGGER when: the user names one task to build (use /craft:execute), the queue
  needs shaping rather than building (use /ranger:refine or /craft:refine), or the work is
  a multi-slice plan rather than standalone leaves (use /craft:execute).
---

# Execute drain

Drain this camp group's buildable queue without a human in the loop. Every task in the
queue gets its own ephemeral camp workspace and a dispatched `ranger:execute` agent that
runs craft's execute ritual against it and writes **one line to a file**; you tell the CLI
to read that file, hand the pushed branch to portage, and finish with a durable report.

**You are the coordinator.** Four parties, four jobs, and the split is the whole design:

- **The `ranger` CLI** owns everything mechanical — preconditions, the per-vault lock,
  queue derivation, outcome validation, the in-flight cap's durable bookkeeping, and the
  report. Its files, not your transcript, are the drain's state.
- **The dispatched executor agent** owns one task's build, in its own context and its own
  workspace. Task details never reach you.
- **Portage's `updater` and `monitor`** own the PR tail — push to PR, CI, the
  human-approval gate, merge. You dispatch them; they report through an outcome file.
- **You** own the loop: sync, provision, dispatch, record, every task status edge, and
  teardown. You do not interpret anything.

**Recommended tier:** whatever you are already on. You read no task bodies and make no
design calls — the reasoning is in the dispatched agents.

## Ground rules

- **You never read a task record.** Not its body, not its payload, not its diff. The CLI
  extracts everything the report needs; opening a record by hand defeats that containment
  and floods the very context the dispatch exists to protect.
- **You never read an agent's reply as a result.** Outcomes travel in files, and the CLI
  reads them. A reply is text you cannot un-read.
- **Shell variables do not survive between commands.** Each command you run may get a
  fresh shell. Carry the values from `ranger drain start` in your working context and
  substitute them **literally** into every later command.
- **Snippets are POSIX `sh`.** No fish syntax, no bash-only builtins.
- **Untrusted text is never interpolated into a command string.** Outcome text reaches the
  CLI by file, never through your shell.

## 1. Start the drain

Run from **inside the camp group's workspace**. There is no `--group` flag: the group and
the elected vault are both resolved from the current directory.

```sh
ranger drain start --holder-pid "$PPID"
```

`--holder-pid` names the long-lived process whose liveness *is* this drain's liveness.
`$PPID` inside the shell your Bash tool spawns is the harness process driving the drain.
**Never omit it:** the CLI's fallback is `ranger`'s own parent, which dies the instant
`start` returns, and the drain would read as abandoned to its own next verb.

The drain's three bounds are flags on this verb, and their defaults are the ones below:

| Flag | Default | What it bounds |
|---|---|---|
| `ranger drain start --concurrency N` | 2 | Executor agents building at once (§3). |
| `ranger drain start --inflight-cap N` | 3 | Pushed-but-unmerged tasks holding a monitor slot (§6). |
| `ranger drain start --monitor-deadline HOURS` | 2h | How long one monitor slot may stay in flight (§6). |

On success `start` prints one JSON object on stdout and echoes the report path on stderr.
Keep every field; you need them all later:

| Field | What you do with it |
|-------|---------------------|
| `vault` | The elected vault — goes in every dispatch prompt, every status write, and `finish`. |
| `procedure_path` | Absolute path to craft's shared execute procedure — goes in every dispatch prompt. |
| `templates_root` | Absolute path to craft's `templates/` — goes in every dispatch prompt. |
| `report_path` | The report to append to and to hand back at the end. |
| `outcomes_dir` | Where each agent writes its result — you form one path per task from it. |
| `lock_token` | Proves the lock is this run's; `finish` will not release without it. |
| `degraded` | True when portage is absent: no PR tail, no monitor, no cap (§6). |
| `concurrency`, `inflight_cap`, `monitor_deadline_hours` | The three bounds, as resolved. |
| `queue` | The derived queue: one entry per task, oldest first, each with `bucket` and `slug`. |
| `group`, `vault_path` | Context only. |

Announce the queue size — **"N tasks derived"** — before the first dispatch, so a long
drain is distinguishable from a stalled one.

**If `start` exits nonzero**, print its one-line message verbatim and stop. **If the lock
is held**, relay the refusal as printed and stop; if it reports the holder as *stale*,
relay the exact `rm` command it prints and let the operator run it. **Never remove a lock
file yourself.**

## 2. The queue

Each entry carries `name`, `status`, `bucket`, and `slug`. The `bucket` decides everything:

| Bucket | Action |
|--------|--------|
| `buildable` | Run §4's per-task ritual. |
| `skipped:not-buildable` | Never dispatch. Record it once with `SKIPPED not buildable — payload names no member-repo file`. |
| `skipped:collision` | Never dispatch. Record it once with `SKIPPED workspace slug collision`, naming the entry's `collision_with`. |

Record both never-dispatched buckets **once, at the first derivation, before the dispatch
loop starts**, and add each id to the attempted-this-drain set so later derivations drop
them.

## 3. The pool

**Dispatch runs a bounded pool: up to 2 executor agents in flight at once**, and the bound
is `ranger drain start --concurrency N`. Each agent is dispatched in the background, so
dispatching does not block on its reply.

Keep an **attempted-this-drain set**: every task id you have dispatched or recorded during
this drain. It starts empty at `start`, gains an id the moment you dispatch or record that
task, and never loses one:
**never re-dispatch a task already in the attempted-this-drain set**.
A `SKIPPED` outcome and a failed build both leave the record byte-identical, so an
unfiltered loop rebuilds the same task forever, and a re-dispatched *successful* task
rebuilds work that is already committed and pushed.

**Per-slot state.** For each in-flight slot, hold four values and nothing else — no record
body, no agent reply, no diff:
its task id, its outcome file path, its dispatch deadline, and the queue bucket
it was derived from. With several tasks interleaving, none of the four is recoverable from
a return; each has to be carried from the derivation that produced the task.

**Initial fill.** Open the pool by dispatching up to `concurrency` tasks before you wait
for any of them. Waiting on the first dispatch before making the second is how a pool
degrades into a serial drain.

**When a slot frees** — its agent returned, or its deadline passed —
record its outcome, re-derive, then dispatch the next task into that slot,
in exactly that order:

1. Record the outcome (§5), write the status edge it earns (§5), tear down or preserve the
   workspace (§7), and add the id to the attempted-this-drain set.
2. Re-derive the queue with `ranger drain derive` — after recording, never before: a
   derivation taken before the outcome lands still classifies the finished task as
   buildable.
3. Then fill the freed slot from that fresh derivation, filtered by the
   attempted-this-drain set — never from the queue snapshot you started with. Nothing else
   in the pool is disturbed.

Hold dispatch entirely while the in-flight cap is full — see §6.

## 4. Per task: sync, provision, resume, dispatch

### 4.1 Sync the group's canonical repos

```sh
camp sync --json
```

Read the **per-member `action`** values, not the top-level status. `camp sync` sets its
top-level status to `ok_with_warnings` only when `errors > 0`, so a member left un-synced
because it was dirty, off main, or absent reports a top-level `"ok"` with the real signal
buried in its own action. Any member whose action is `skip-dirty`, `skip-off-main`, or
`absent` — or anything else that is not a completed fast-forward — means this task would be
built on a stale base: do not provision, record `SKIPPED camp sync left <member> at
<action>`, and move to the next task. The per-member map is keyed `members` or `siblings`
depending on which camp sync implementation answers; read whichever is present.

### 4.2 Provision the ephemeral workspace

```sh
camp new <slug>
```

`<slug>` is the entry's own `slug` from the derivation — never one you compute yourself.
Provisioning is asynchronous; poll it:

```sh
camp status <slug> --json
```

Exit 0 is ready, 2 is still provisioning (poll again), 3 is failed. On exit 3, record
`FAILED camp provisioning failed` and move on.

**If `camp new` fails naming a worktree that already exists**, suspect a
stale worktree registration
— an abnormally torn-down workspace (an `rm -rf` rather than a `camp remove`) leaves a
registration behind that fails `git worktree add` while nothing is on disk. Record
`FAILED stale worktree registration for <slug>` and name it in the report; do not try to
clear it yourself.

### 4.3 The resume ritual — unconditional

Run this **whenever the task carries a `craft/branch` label**, without first checking
whether the branch looks present locally. Camp provisioning never fetches the task branch:
a branch that exists only on the remote yields a *fresh local branch off base*, which looks
exactly like a clean start and silently discards everything the previous run pushed. The
check that would let you skip the ritual is the check that cannot tell the two apart.

In each member worktree of the new workspace, in order:

1. `git fetch origin`
2. reset the workspace branch to the remote branch (`git reset --hard origin/<branch>`)
3. rebase onto `origin/main`

A rebase conflict is a `FAILED` bucket, never a fresh start
— building fresh on top of a conflict throws away the previous run's committed work.
Record `FAILED resume rebase conflicted on <branch>`, preserve the workspace, and move on.

### 4.4 Dispatch the executor agent

Dispatch the `ranger:execute` subagent **in the background**. Its prompt carries exactly
six values and nothing else about the task:

```
Task record id: task/<name>
Execute procedure: <procedure_path>
Templates root: <templates_root>
Elected vault: <vault>
Workspace path: <workspace_path>
Outcome file: <outcomes_dir>/<name>.outcome
```

The outcome path is `<outcomes_dir>`, the task's bare name, and `.outcome` — form it
exactly that way, because recording recomputes the same path and a mismatch records a
finished build as failed.

Send those six lines and nothing more: do not add context about the task, do not summarize
it, and do not tell the agent how to build — `procedure_path` is the authority on the
ritual and the agent reads it in full.

**Do not read the agent's reply.** Its result is the outcome file.

## 5. Record it, and write the status edge

```sh
ranger drain record --report "<report_path>" --task "task/<name>" --outcome "$(cat <outcomes_dir>/<name>.outcome)"
```

The file's first line is held to the drain grammar — `PUSHED <branch> <sha> <diffstat>` /
`BLOCKED <reason>` / `FAILED <reason>` / `SKIPPED <reason>`. A missing or empty file is an
agent that died, timed out, or never ran: record it as `FAILED no outcome written`.

**The loop session writes every task status edge.** Not the executor agent, not craft's
ritual, not portage. This is the one thing in the drain that has a single writer by
contract, because a status split across a process boundary races:

| Outcome | Status edge you write |
|---|---|
| `PUSHED` | `lore record update task/<name> --vault <vault> --status done` — write `done` immediately after the push succeeds, before the portage tail, so a coordinator that dies mid-monitor does not lose the edge the push already earned. |
| `BLOCKED` | Park it: the agent has already written the literal `## Refine — unresolved` section onto the record; you write `lore record update task/<name> --vault <vault> --status blocked --label craft/branch=<branch>` — the status **and** a re-assert `craft/branch` in one command, because without the label the next drain cannot find the branch the parked work is on. |
| `FAILED` / `SKIPPED` | No status write at all. The report's line is the operator's handle, and a status guessed from a run that produced nothing is worse than a task that stays `ready`. |
| Your own crash | Nothing to write — and that is the design. A task whose coordinator died is left `in-progress`, and its workspace is preserved, so the next drain's resume ritual picks it up from the branch. |

`--vault` is not optional. Without it `lore record update` locates the record by a
cwd-blind first-match scan across configured vaults in declaration order, so a task name
that exists in two vaults gets written to whichever one lore's config happens to list
first — silently, unattended, in someone else's vault.

## 6. The portage tail

For every `PUSHED` task, and only after §5's `done`,
the loop session dispatches portage's `updater` and then `monitor` — never the executor agent.
This is not delegation you may optimize away: a background agent dispatched from inside
another subagent is nested and loses its notification channel, and the drain's completion
signal degrades with it.

Dispatch `updater` first, take the `pr_pairs` it returns, then dispatch `monitor` in the
background from **this** session, handing it an `outcome_file` path.

**And then ignore the notification.** Dispatched from the top level, `monitor` keeps its
channel — but
the monitor outcome file is the contract, never the notification.
Poll the file; never wait on a reply. The drain must survive a lost notification, and
a missing or empty monitor outcome file reads as crashed
— an unwritten file is exactly the signal that the monitor died, timed out, or never ran,
and treating it as "still running" wedges the cap forever.

The file's one line is `MERGED` / `READY <reason>` / `BLOCKED <reason>` / `STOPPED
<reason>`. `READY` is the human-approval gate holding: the PR is green and waiting for the
operator's signal. The report renders that as `awaiting-human-approval` with the exact
`gh pr edit … --add-label human-approved` command inline — and **the drain
never applies the approval signal itself**, in any component. Not you, not the executor,
not portage's agents. A gate that the automation building the change can open is not a
gate.

**The in-flight cap.** Every task handed to a monitor holds a cap slot, tracked durably by
the CLI so it survives a restart.
**pause dispatch while the in-flight count is at the cap**
— `ranger drain start --inflight-cap N`, **default 3** — and resume when a slot frees.
Without the pause an unattended drain opens unbounded unmerged PRs against a gate only a
human can clear.

**The monitor deadline.** Each slot carries one:
`ranger drain start --monitor-deadline HOURS`, **default 2h**.
When it expires the slot is reclaimed into the `monitor-timeout` bucket,
so it is distinguishable from a merged one, and
a reclaimed slot never removes the task's workspace: an expired deadline means the loop
lost track of the PR, not that the work is disposable.

**Degraded mode.** When `start` reported `degraded: true`, portage is not installed: there
is no tail, no monitor, and no cap. A `PUSHED` task is `done` at the push, and the report
carries a banner saying so.

## 7. Teardown

At monitor-terminal, tear the workspace down with `camp remove` — but only when the
monitor reported `MERGED`. `READY`, `BLOCKED`, and `STOPPED` are terminal for the monitor
while still naming something an operator may need the workspace to finish, so they preserve
it and it goes into `finish`'s still-standing list. A crashed monitor, an expired deadline,
a `FAILED` build, and a `BLOCKED` park all preserve the workspace too. When
portage absent (degraded), tear down at push instead
— there is no monitor-terminal to wait for.

## 8. Progress and exit

Print **one summary line per task** as you record it — task id, bucket, and, for a pushed
task, its branch. Nothing else: no diffs, no agent output, no record bodies. A long
unattended drain with no per-task line is indistinguishable from a stalled one.

**Exit when the filtered buildable set is empty, no slot is still in flight, and no monitor is outstanding.**
An empty derivation with monitors outstanding is a drained *queue*, not a
finished drain — their outcomes are still owed to the report. The never-dispatched buckets
persist by design; they are recorded exactly once and the attempted-set filter is what
stops them keeping the loop spinning.

```sh
ranger drain finish --report "<report_path>" --still-standing "<slug>" --vault "<vault>" --token "<lock_token>"
```

Repeat `--still-standing` once per preserved workspace. Then hand back **the report path**
and the bucket counts, and nothing else. Do not paste the report's contents into the
transcript.

If the drain dies before `finish`, the partial report is still on disk and the lock still
names its holder — the next `start` reports both. Every preserved workspace and every task
left `in-progress` is picked up by the next drain's resume ritual.

## Operator re-entry

Every stranded state the drain can leave — a failed push, a parked block, a crashed
coordinator, a stale lock, a stalled approval, a corrupt state file — has a named,
pinned recovery ritual, plus the degraded-trust (portage-absent) mode description, in
[`operator-rituals.md`](./operator-rituals.md). Read it before touching a task or a lock
by hand.

## Never

- **Never remove a lock file yourself**, stale or not.
- Never let more than `concurrency` dispatches run at once, or fill a freed slot from a
  stale derivation instead of a fresh `ranger drain derive`.
- Never read a task record's body, payload, or diff.
- Never treat a dispatched agent's reply as its result — the outcome file is the result.
- Never skip the resume ritual because the branch looks locally present.
- Never write a task status other than the edges in §5.
- Never dispatch portage's `updater` or `monitor` from inside the executor agent.
- Never apply the `human-approved` label, approve a PR, or merge anything.
