---
name: refine
description: >
  Drain a camp group's shaping queue unattended — sweep every standalone `open` or
  answered-`blocked` task in the group's elected lore vault through craft's refine ritual,
  one dispatched agent per task, and hand back a durable exit report.
  TRIGGER when: user says "run the refine sweep", "drain the shaping queue", "refine the
  backlog", "sweep the open tasks", "promote whatever is promotable", "work the queue", or
  invokes /ranger:refine explicitly.
  DO NOT TRIGGER when: the user names one task to promote (use /craft:refine), the work still
  needs shaping or planning (use /craft:brainstorm or /craft:plan), or the user wants `ready`
  tasks built rather than shaped (use /craft:execute).
---

# Refine sweep

Drain this camp group's shaping queue without a human in the loop. Every task in the
queue is handed to a dispatched `ranger:refine` agent that runs craft's refine ritual
against it and writes **one token to a file**; you tell the CLI to read that file, move on,
and finish with a durable report.

**You are the coordinator.** Three parties, three jobs, and the split is the whole
design:

- **The `ranger` CLI** owns everything mechanical — preconditions, the per-vault lock,
  queue derivation, outcome reading, and the report. Its files, not your transcript, are
  the sweep's state.
- **The dispatched agent** owns one task's ritual, in its own context. Task details never
  reach you.
- **You** own the loop: dispatch, record, and write the one status the ritual is forbidden
  to write. You do not interpret anything.

**Recommended tier:** whatever you are already on. You read no task bodies and make no
design calls — the reasoning is in the dispatched agents.

## Ground rules

- **You never read a task record.** Not its body, not its question, not its payload. The
  CLI extracts question text straight into the report so escalation prose never transits
  your context; opening a record by hand defeats that containment and floods the very
  context the dispatch exists to protect.
- **You never read an agent's reply as a result.** Outcomes travel in files, and the CLI
  reads them (§2.2). A reply is text you cannot un-read — treating it as the result puts a
  task's citations and reasoning in your context, which is the same leak by a different
  door.
- **Shell variables do not survive between commands.** Each command you run may get a
  fresh shell, so nothing you assign persists. Carry the values from `ranger sweep start`
  in your working context and substitute them **literally** into every later command.
- **Snippets are POSIX `sh`.** They must run unchanged under whatever shell your Bash
  tool spawns. No fish syntax, no bash-only builtins.
- **Untrusted text is never interpolated into a command string.** Outcome text reaches the
  CLI by file, never through your shell. On the rare hand-written `--outcome`, it is one
  quoted argument and nowhere else.

## 1. Start the sweep

Run from **inside the camp group's workspace**. There is no `--group` flag: the group and
the elected vault are both resolved from the current directory, so running from the wrong
place is a refusal, not something to override.

```sh
ranger sweep start --holder-pid "$PPID"
```

`--holder-pid` names the long-lived process whose liveness *is* this sweep's liveness.
`$PPID` inside the shell your Bash tool spawns is the harness process driving the sweep —
that is the right value. **Never omit it:** the CLI's fallback is `ranger`'s own parent,
which under a fresh-shell-per-command harness dies the instant `start` returns, and the
sweep would read as abandoned to its own next verb. If your shell leaves `$PPID` unset,
get the pid portably with `ps -o ppid= -p $$` and pass it literally — but pass one.

On success `start` prints one JSON object on stdout and echoes the report path on stderr.
Keep these fields; you need every one of them later:

| Field | What you do with it |
|-------|---------------------|
| `vault` | The elected vault name — goes in every dispatch prompt, every status write, and `finish`. |
| `procedure_path` | Absolute path to craft's refine procedure — goes in every dispatch prompt. |
| `templates_root` | Absolute path to craft's `templates/` — goes in every dispatch prompt. |
| `report_path` | The report to append to and to hand back at the end. |
| `outcomes_dir` | Where each agent writes its result — you form one path per task from it. |
| `lock_token` | Proves the lock is this run's; `finish` will not release without it. |
| `queue` | The derived queue: one entry per task, oldest first. |
| `group`, `vault_path` | Context only. |

Announce the queue size — **"N tasks derived"** — before the first dispatch, so a long
sweep is distinguishable from a stalled one.

**If `start` exits nonzero**, print its one-line message verbatim and stop. Each
precondition failure names its own remediation; do not improvise around it.

**If the lock is held**, relay the refusal as printed and stop. If it reports the holder
as *stale*, relay the exact `rm` command it prints and let the operator run it.
**Never remove a lock file yourself** — a live sweep whose coordinator you cannot see
looks exactly like a dead one from here.

## 2. Work the queue

Each `queue` entry carries `name`, `status`, `bucket`, and `answer_near_miss` (plus the
sidecar's graph fields). No entry carries a record body — the `bucket` decides everything:

| Bucket | Action |
|--------|--------|
| `dispatchable` | Dispatch the agent, then record its return. |
| `blocked-answered` | Dispatch the agent, record its return, **then write the exit status** (§2.4). |
| `escalated-awaiting-operator` | Never dispatch. Record from the bucket alone (§2.5). |
| `blocked-still-waiting` | Never dispatch. Record from the bucket alone (§2.5). |

**Dispatch runs a bounded pool: up to 4 agents in flight at once.** The lore vault write
lock now serializes the writes themselves, so concurrent dispatches no longer race the
vault — the cap exists to bound how much escalation/failure context one sweep can pile
up at once, not to protect writes. Each agent is dispatched in the background, so
dispatching does not block on its reply: the coordinator keeps working while all 4
slots run. The moment any slot's agent returns, record its outcome (§2.3–§2.5) and
re-derive with `--actionable` the moment a slot frees (§2.7). Then fill the freed slot with the next task from that fresh derivation — never from the queue snapshot you started the pool with.

### 2.1 Dispatch the agent

Dispatch the `ranger:refine` subagent. Its prompt carries exactly five values and nothing
else about the task: the task record id, the procedure path, the templates root, the
elected vault name, and the outcome file this task's result goes in.

```
Task record id: task/<name>
Refine procedure: <procedure_path>
Templates root: <templates_root>
Elected vault: <vault>
Outcome file: <outcomes_dir>/<name>.outcome
```

The outcome path is `<outcomes_dir>` from `start`, the task's bare name, and `.outcome` —
form it exactly that way, because §2.3 recomputes the same path and a mismatch records the
task as failed.

Do not add background, do not summarize the task, and do not tell the agent how to refine —
`procedure_path` is the authority on the ritual and the agent reads it in full.

### 2.2 Do not read the agent's reply

**The agent's result is in its outcome file, not in what it says back to you.** Its reply
is not the result of its run and carries no information you need — do not parse it, do not
summarize it, and do not act on it. The whole reason the outcome travels through a file is
that a reply is text you cannot un-read: parse a result out of it and the task's citations,
file paths, and reasoning are in your context, which is exactly what the dispatch exists to
prevent.

If you want a liveness signal for an attended run, print the task name as you dispatch it
and again as you record it. That is the sweep showing progress without you reading a word
of agent output.

### 2.3 Record it

```sh
ranger sweep record --report "<report_path>" --task "task/<name>" --queue-bucket dispatchable --outcome-file
```

`--outcome-file` reads the token the agent wrote, from the path §2.1 handed it. **Prefer it
always.** A missing or empty file — an agent that died, timed out, or never ran — records
as `failed` and names that as the reason, so you never need to synthesize a failure by hand
for a dispatch that produced nothing.

Use `--queue-bucket blocked-answered` for a task that came out of that bucket; the report
keeps the bucket the task's history earns it on `PROMOTED` and `ROUTED`. Every other outcome
outranks that bucket, because each carries something a bare id under "Blocked — answered"
would drop: `ESCALATED` carries the question the ritual just wrote and the command that
answers it, and `SKIPPED`, `FAILED`, and an unparseable token each carry their reason.

`--outcome "<line>"` remains for driving the verb by hand. Never use it to pass along
something you read out of an agent's reply — that launders a broken return into a clean
record and defeats the enforcement in §2.2.

If you need the outcome for the §2.4 status write, read it back from the report or from the
outcome file with `cat` — one token, not the agent's prose.

Add the task id to the attempted-this-sweep set (§2.7) as you record it.

### 2.4 The blocked exit edge — yours, and only yours

For a task that entered the queue as `blocked-answered`, and for no other task, write the
exit status yourself after recording the outcome. Read the token from the outcome file —
`cat "<outcomes_dir>/<name>.outcome"` — not from the agent's reply.

The file holds one token from a closed set, and this is the only step that reads it:
`PROMOTED` / `ESCALATED` / `ROUTED <target>` / `SKIPPED <reason>`

- `PROMOTED` → `lore record update task/<name> --vault <elected-vault> --status ready`
- `ESCALATED` or `ROUTED <target>` → `lore record update task/<name> --vault <elected-vault> --status open`

The loop writes this status, never the agent and never the ritual — craft's refine ritual
never flips `blocked`, and the sweep is the pre-authorized owner of that one exit edge,
acting on the answer the operator recorded.

`--vault` is not optional. Without it `lore record update` locates the record by a
cwd-blind first-match scan across the configured vaults in declaration order, so a task
name that exists in two vaults gets written to whichever one lore's config happens to list
first — silently, unattended, in someone else's vault.

On `SKIPPED <reason>` or a failed dispatch, leave the record untouched: the report's line
is the operator's handle, and a status guessed from a return the ritual never produced is
worse than a task that stays `blocked` one more sweep.

### 2.5 Never-dispatched tasks

A churn-guarded or still-waiting task is recorded from its bucket and takes **no**
`--outcome`:

```sh
ranger sweep record --report "<report_path>" --task "task/<name>" --queue-bucket escalated-awaiting-operator
```

The CLI reads the record itself to lift the question and build the operator's answer
command. That is why you do not open the record.

**Record both never-dispatched buckets once, at the first derivation, before the dispatch loop starts.**
They are reported, never drained, so every later derivation still carries them — and
recording them again each pass re-reads a record body per task for a line the report
already holds. Add each id to the attempted-this-sweep set (§2.7) as you record it, so
the filter drops them from every derivation that follows.

### 2.6 When a dispatch goes wrong

A dispatch that errors, writes nothing parseable, or overruns its own
**10-minute per-slot timeout**
buckets `failed`, leaves the task record untouched, and the sweep continues to the next
task. One confused agent must never end a drain that still has tasks in it — and because
each slot's timeout is independent, one slow agent never holds up the other 3.

If **every** slot times out at once, read that as lock contention before you read it as
4 stuck agents: check the transcript for the lock helper's own stderr notice —
`lore: waiting for the vault write lock` — printed whenever an acquisition waits past
~2 seconds. A mid-drain operator action that takes the vault lock (an operator-run
`lore reindex` is the common case) blocks every slot at once and reads exactly like a
mass hang; the notice is how you tell the two apart.

**Usually you do nothing special.** Record it exactly as §2.3 says: an agent that died or
never ran left no outcome file, and `--outcome-file` turns that into a `failed` line naming
the absence. The same holds for an agent that wrote commentary instead of a token.

Synthesize a failure by hand only when you know something the file cannot say — a timeout
you enforced, or a dispatch that errored before the agent started:

```sh
ranger sweep record --report "<report_path>" --task "task/<name>" --outcome "FAILED dispatch timed out after 10 minutes"
```

### 2.7 Re-derive, filter, then take the next task

Keep an **attempted-this-sweep set**: every task id you have dispatched or recorded during
this sweep. It starts empty at `start`, gains an id the moment you dispatch or record that
task, and never loses one.

After each task, re-derive the queue:

```sh
ranger sweep derive --actionable
```

`--actionable` prints only the two buckets you dispatch, one short line each. Use it, not
`--json`: you re-derive once per task, and the full JSON carries every sidecar graph field
for every entry — including the never-dispatched buckets, which persist for the whole sweep
and grow with every escalation. On a long queue that is tens of thousands of tokens spent
re-reading tasks you will never act on, in the context this whole design exists to keep
clear.

The fresh derivation is authoritative — never the list you started with. Then **drop every
entry whose id is already in the attempted-this-sweep set**; what survives is the actionable
set, and the next task is the first of them.

Filtering is what ends the loop, and derivation alone cannot. A promoted task drops out of
the derivation on its own, but a `SKIPPED` outcome and a failed dispatch both leave the
record byte-identical — so the next derivation classifies that task exactly as it did
before, and an unfiltered loop dispatches it again, and again, forever.

**Exit when the filtered actionable set is empty.** The never-dispatched buckets persist by
design; they are recorded exactly once per sweep, reported rather than drained, and the
filter is what stops them keeping the loop spinning.

## 3. Finish

```sh
ranger sweep finish --report "<report_path>" --vault "<vault>" --token "<lock_token>"
```

`--token` is the `lock_token` from `start`. The vault name identifies the *lock*; the token
identifies the *run* — without it an out-of-order `finish` would release a sweep that is
still going.

Then hand back **the report path** and the bucket counts, and nothing else. The report is
the durable artifact and the primary surface for headless runs; do not paste its contents
into the transcript, and do not re-narrate a sweep whose progress you already streamed.

**Report entries are completion-ordered, not queue order.** With up to 4 dispatches in
flight, whichever agent returns first is recorded first — the report's line order tells
an operator which task finished when, not where it sat in the original derivation.

**Set expectations about what a drain produces.** A sweep's output is mostly triage, not a
`ready` queue: a large share of any real backlog comes back `ESCALATED` or `ROUTED`, because
the ritual refuses to invent answers to questions only the operator can settle. That is the
sweep working, not failing — but say so alongside the counts, so nobody reads "drained" as
"promoted". The first real drain measured this directly: of 12 queued tasks, 5 were
promoted, 3 escalated, 3 routed, and 1 stayed blocked — roughly half the queue came back
needing the operator. **The drain's product is a triage list, not a `ready` queue.**

If the sweep dies before `finish`, the partial report is still on disk and the lock still
names its holder — the next `start` reports both. There is no recovery step to run: the
ritual's writes are update-in-place against idempotency keys, so a re-run reaches the same
state.

## Never

- **Never remove a lock file yourself**, stale or not.
- Never let more than 4 dispatches run at once, or fill a freed slot from a stale
  derivation instead of a fresh `--actionable` re-derive.
- Never read a task record's body, question, or payload.
- Never treat a dispatched agent's reply as its result — the outcome file is the result.
- Never pass text you read out of an agent's reply to `--outcome`.
- Never write a task status other than the `blocked-answered` exit edge in §2.4.
- Never answer an escalated question on the operator's behalf — the report carries the
  exact command they run to answer it.
