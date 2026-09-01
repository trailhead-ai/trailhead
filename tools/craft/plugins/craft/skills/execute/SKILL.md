---
name: execute
description: >
  Use when executing an approved implementation plan task-by-task — or a single standalone task
  record as the whole unit of work — dispatching `assumption-prover` and `executor` subagents
  rather than building inline. The controller (you) orchestrates; subagents do the work.
  TRIGGER when: user says "execute", "execute the plan", "start building", "let's build", "build it",
  "implement this", "run the plan", "work the tasks", "start the tasks", "go" (following plan approval),
  "ship it", or resumes a plan with unfinished tasks. Also triggers as the natural
  handoff after `/planning` when the user approves the written plan, and when the user hands over a
  standalone (childless, parentless) task record to run — `ready` to dispatch straight away, `open`
  and execute refines it inline first.
  DO NOT TRIGGER when: there is neither a plan nor a task record to execute yet (use `/planning`
  or `planner` first), the plan has ≤2 tasks with no unknowns and small scope (just build it
  yourself), or the user is debugging rather than executing.
---

# Execute

Execute a plan task-by-task — or a standalone task as its own single slice — in an
attended session with a human operator.

**The procedure lives in `../_shared/execute.md`** (a sibling of this skill's
directory). Read it and follow it end to end — the subagent roles, the mode table,
the task-shape branch, the per-task loop, the end-of-run phase pipeline, and the
status-handling rules are all defined there. This skill does **not** restate them: an
unattended caller (a future ranger drain loop) dispatches the same document with no
human channel, and a second copy here is how the two callers would drift apart.

## Mode

`/craft:execute` always runs **attended** — a human operator is present in this
session, so every escalation point in `../_shared/execute.md`'s mode table asks the
user rather than re-routing through escalate-via-park or proceed-per-contract. There
is no flag here to opt into unattended behavior: that mode is reached only by a
loop session dispatching the shared procedure directly, never by this wrapper.

## When to Use

- You have an approved implementation plan with tasks and known unknowns, **or** a
  standalone task record that is itself the whole unit of work (`ready`, or `open` and
  refinable — the shared procedure's standalone branch)
- You want to execute in the current session, with a human available to answer
  escalations

## Skip Gate

**Don't use subagents when:**
- The plan has ≤2 tasks, no unknowns, and the total scope is small (≤100 lines expected)
- You'd spend more time writing prompts and absorbing reports than just building it

In those cases, build it yourself following TDD and verification. Subagent overhead isn't free.

On a standalone task this gate is an explicit judgment, never an automatic one — see
`../_shared/execute.md`'s Skip Gate section for the full rule.

## Everything else

Read `../_shared/execute.md` from the top: subagent roles and model selection, the
task-shape branch (plan vs. standalone), the per-task Loop, the After All Tasks
phase pipeline, Handling Assumption-Prover Status, Handling Executor Status, and the
Red Flags. All of it applies verbatim to an attended run.
