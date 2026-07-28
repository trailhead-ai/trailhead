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
against it and returns **one line**; you record that line, move on, and finish with a
durable report.

**You are the coordinator.** Three parties, three jobs, and the split is the whole
design:

- **The `ranger` CLI** owns everything mechanical — preconditions, the per-vault lock,
  queue derivation, and the report. Its files, not your transcript, are the sweep's state.
- **The dispatched agent** owns one task's ritual, in its own context. Task details never
  reach you.
- **You** own the loop: dispatch, parse one line per task, record it, and write the one
  status the ritual is forbidden to write.

**Recommended tier:** whatever you are already on. You read no task bodies and make no
design calls — the reasoning is in the dispatched agents.

## Ground rules

- **You never read a task record.** Not its body, not its question, not its payload. The
  CLI extracts question text straight into the report so escalation prose never transits
  your context; opening a record by hand defeats that containment and floods the very
  context the dispatch exists to protect.
- **Shell variables do not survive between commands.** Each command you run may get a
  fresh shell, so nothing you assign persists. Carry the values from `ranger sweep start`
  in your working context and substitute them **literally** into every later command.
- **Snippets are POSIX `sh`.** They must run unchanged under whatever shell your Bash
  tool spawns. No fish syntax, no bash-only builtins.
- **Untrusted text is never interpolated into a command string.** An agent's return line
  goes into `--outcome` as a single quoted argument and nowhere else.

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

**Dispatch is serial — one task at a time**, and you never dispatch a second agent until
the first has returned. Lore has no write mutex and every record write is a vault git
commit, so two agents in flight race each other for the vault.

### 2.1 Dispatch the agent

Dispatch the `ranger:refine` subagent. Its prompt carries exactly four values and nothing
else about the task: the task record id, the procedure path, the templates root, and the elected vault name.

```
Task record id: task/<name>
Refine procedure: <procedure_path>
Templates root: <templates_root>
Elected vault: <vault>
```

Do not add background, do not summarize the task, and do not tell the agent how to refine —
`procedure_path` is the authority on the ritual and the agent reads it in full.

### 2.2 Parse the return

The agent returns exactly one line, drawn from a closed set:
`PROMOTED` / `ESCALATED` / `ROUTED <target>` / `SKIPPED <reason>`

Stream that one line per task into the transcript as it arrives — for an attended run it
is the only sign the sweep is alive.

### 2.3 Record it

```sh
ranger sweep record --report "<report_path>" --task "task/<name>" --queue-bucket dispatchable --outcome "PROMOTED"
```

Use `--queue-bucket blocked-answered` for a task that came out of that bucket; the report
keeps the bucket the task's history earns it, whatever the agent returned. Pass the return
line as one quoted argument, exactly as received — never build a larger command string
around it.

### 2.4 The blocked exit edge — yours, and only yours

For a task that entered the queue as `blocked-answered`, and for no other task, write the
exit status yourself after recording the outcome:

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

### 2.6 When a dispatch goes wrong

A dispatch that errors, returns nothing parseable, or overruns the
**10-minute per-dispatch timeout**
buckets `failed`, leaves the task record untouched, and the sweep continues to the next
task. One confused agent must never end a drain that still has tasks in it.

Feed the CLI what you actually got — it truncates to one line, buckets `failed`, and exits
0 either way:

```sh
ranger sweep record --report "<report_path>" --task "task/<name>" --outcome "FAILED dispatch timed out after 10 minutes"
```

### 2.7 Re-derive, then take the next task

After each return, re-derive the queue:

```sh
ranger sweep derive --json
```

The fresh derivation is authoritative — never the list you started with. A task promoted
this sweep drops out; a task that just gained an unanswered question is churn-guarded out
of dispatch. Record each never-dispatched task exactly once per sweep.

**Exit when a fresh derivation leaves nothing actionable** — no `dispatchable` or
`blocked-answered` entry remains. The never-dispatched buckets persist by design; they are
reported, not drained, and must not keep the loop spinning.

## 3. Finish

```sh
ranger sweep finish --report "<report_path>" --vault "<vault>" --token "<lock_token>"
```

`--token` is the `lock_token` from `start`. The vault name identifies the *lock*; the token
identifies the *run* — without it an out-of-order `finish` would release a sweep that is
still going.

Then hand back **the report path** and the bucket counts, and nothing else. The report is
the durable artifact and the primary surface for headless runs; do not paste its contents
into the transcript, and do not re-narrate the sweep you just streamed one line per task of.

If the sweep dies before `finish`, the partial report is still on disk and the lock still
names its holder — the next `start` reports both. There is no recovery step to run: the
ritual's writes are update-in-place against idempotency keys, so a re-run reaches the same
state.

## Never

- **Never remove a lock file yourself**, stale or not.
- Never dispatch two agents at once, or dispatch while one is still running.
- Never read a task record's body, question, or payload.
- Never write a task status other than the `blocked-answered` exit edge in §2.4.
- Never answer an escalated question on the operator's behalf — the report carries the
  exact command they run to answer it.
