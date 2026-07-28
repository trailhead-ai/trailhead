---
name: refine
description: >
  Promote a standalone (childless, parentless) task from `open` to `ready` — draft its
  Delivers / Test contract / Files payload from the code and the vault, cite every derived
  answer, and escalate only an irreducible operator decision.
  TRIGGER when: user says "refine this task", "promote this task", "make this task ready",
  "get this task executor-ready", "fill in this task's payload", "is this task runnable yet",
  or hands over a captured standalone task that has no payload and no child slices.
  DO NOT TRIGGER when: the task already has child tasks (that is a plan — use plan) or a
  parent task (that is a slice — execute its parent instead), the
  work needs two or more independently-committable cuts or its what/why is still open (use
  plan or brainstorm), the user is executing an approved plan (use execute — it runs this
  procedure inline on a standalone task), or the request is to write new code rather than to
  make a task ready.
---

# Refine

Promote a standalone task to `ready`.

**The procedure lives in `_shared/refine.md`.** Read it and follow it end to end —
the status gate, the draft attempt, the self-serve resolution passes, the citation
rules and the resolution gate, the payload shape, the escalation contract, and the
re-refine rules are all defined there. This skill does **not** restate them: the
`execute` skill runs the same procedure inline, and a second copy here is how the
two callers would drift apart.

## Argument

The task record to promote — a record id or a bare task name
(`/craft:refine task/<name>` or `/craft:refine <name>`). If the argument is missing
or resolves to more than one record, ask which one before doing anything else.

## Modes

- **Bare invocation runs unattended.** This is the default posture: refine drafts
  what it can, and a decision that survives the self-serve passes is *recorded* for
  a human rather than asked. It never blocks on an answer, so it is safe to dispatch
  as a subagent with no channel back to a human.
- **`--interactive` opts into the question path.** Refine asks one surviving
  question at a time, each pre-loaded with the evidence and a recommended answer. A
  deferred question falls back to the unattended behavior for that task.

Everything else is identical between the two — see `_shared/refine.md`.

## Reviewing a draft before it reaches a dispatch

`/craft:execute` runs this procedure inline on a standalone `open` task and, if it
promotes, proceeds straight to dispatching the work. There is no preview step in
between. When you want to see the drafted payload first, run
`/craft:refine --interactive` standalone against the task, read what it wrote, then
hand the task to execute.

## Outcome

Report which of the three landed:

- **Promoted** — the payload was appended and the task is now `ready`. Name the
  fields filled and the citations behind any derived answer.
- **Escalated** — a partial payload was written and the surviving question recorded;
  the task is still `open`. Name the question.
- **Routed** — the work is not a leaf. Name the route (`/craft:plan` or
  `/craft:brainstorm`) and why.

A `blocked` task can be drafted but never promoted; a task with children is refused
toward `/craft:plan`, and a task that already has a parent is refused toward that
parent's plan. All three gates are in `_shared/refine.md`.
