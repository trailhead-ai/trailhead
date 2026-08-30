---
name: scout
description: >
  Take one fuzzy idea and return a walking skeleton — a thin slice running end to end — by
  surveying, writing a parent task with child slices directly, and building them. No spec, no
  gauntlet, no plan ritual, no approval loops. Optimized for speed and for what building the
  thing teaches you, not for correctness.
  TRIGGER when: user says "scout this", "spike this", "just build me something end to end",
  "I want to see it working", "give me a first iteration", or invokes /ranger:scout explicitly.
  DO NOT TRIGGER when: the work is understood and needs building properly (use /craft:brainstorm
  then /craft:plan), a spec already exists (use /craft:plan), or the change is a bug fix.
---

# Scout

Build a **walking skeleton** for one idea: a thin slice that runs end to end, fast, so there is
something real to react to. Then report what building it taught you.

You are not producing a correct implementation, and you are not producing a spec. You are
producing an artifact the operator will look at, form an opinion about, and then re-do properly
with a full brainstorm. That is the intended lifecycle, not a failure of it.

**The whole point is speed.** Every instinct toward thoroughness here is a bug. If you find
yourself weighing options, pick one and move.

## Skip gate

Do not run scout when:

- The idea is already understood and just needs building — that is `/craft:brainstorm` →
  `/craft:plan` → `/craft:execute`, and it will produce a better result.
- A `ready` spec already exists on the topic. Plan it properly.
- It is a bug fix, or a change small enough to just make.

Scout earns its keep exactly when the operator cannot yet say what they want, because nothing
exists to point at.

## The prime directive

**Cut depth. Never cut breadth.**

You will run out of room — context, time, or patience. When you do, sacrifice *how well* each
layer works, never *how many* layers exist. Stub it, hardcode it, fake the data, skip the edge
cases, handle only the happy path. But the slice must run from one end to the other.

A stubbed path that runs end to end is the deliverable. A beautiful, complete implementation of
the first component is a failure, because the operator cannot react to it. Left to itself, an
agent under pressure does an excellent job of whatever is in front of it and runs out of room
before reaching the end — which is exactly the failure this directive exists to prevent.

## Blast radius

The skeleton must not break things that already work. Two rules, in order:

1. **Prefer additive.** New code in new files. When you must touch existing code, prefer adding
   a branch to changing a path. A new module nothing imports yet cannot break anything.
2. **The existing test suite is the guard.** Run it before you start (know what was already
   red) and again at the end. Newly-red tests are a stop condition, not a note for the report.

Everything happens on a branch. **Nothing merges, and nothing pushes.** The run ends at
committed work on a local branch plus a report; pushing is outward-facing and stays the
operator's call, as does whether any of it becomes real.

## Process

### 1. Survey — time-boxed, one pass

Find out what already exists and what has already been tried. Dispatch **one** research agent
(`lore:librarian` if the vault is configured, `Explore` otherwise) and read what comes back.
One pass. Do not iterate on it, do not dispatch a second round to chase something interesting.

You are looking for three things and no more: what code already does part of this, what the
vault says was already decided or already failed, and what the thin slice should be.

### 2. Frame — publish, do not wait

Write down, in a few sentences: the goal in your own words, and the specific end-to-end slice
you intend to build. This is the one place where being wrong is expensive — a misread goal makes
the whole run worthless rather than usefully wrong.

So state it plainly and **keep going without waiting for an answer**. If outpost is running,
publish it where the operator can see it; otherwise print it. The operator may kill the run
early if it is wrong. That bounds a misread to one run, and costs nothing when the framing is
fine.

### 3. Write the task graph

Emit a **parent task with child slices** — the same shape `/craft:plan` produces, written
directly, without the planning ritual or the council.

The parent uses `templates/plan.md`; each child uses `templates/task.md`. Wire children with
`--parent <parent-name>` and order them with `--depends-on`. Create every record at
`--status ready`, in the elected vault, naming `--vault` explicitly on every command.

Three deviations from a real plan, all deliberate:

- **No `--related spec=`.** There is no spec, and there will not be one.
- **Known Unknowns are not resolved before building.** List them; do not chase them. Building is
  how a spike resolves an unknown.
- **Aim for three to six slices.** More than that is not a skeleton.

Label the parent `scout/run=<slug>` so everything this run produced is findable later, and say
in the parent's Goal that it is a scout skeleton — a reader six weeks from now must not mistake
it for planned work.

### 4. Build

Hand the graph to craft's execute procedure and let it run. It already walks a parent's children
in dependency order, dispatching one executor per slice.

Two things about this loop matter to scout specifically:

**Tests: ordinary TDD, per slice.** The skeleton is kept and iterated on, not thrown away, so
it needs real tests — written first, watched fail, at whatever granularity each slice naturally
has. There is no spike dispensation here, and no single end-to-end uber-test standing in for the
rest: writing tests first is not what makes a spike slow. Deliberation is, and that is what this
skill cuts.

**Scope may grow mid-run, without asking.** If a slice discovers the skeleton needs something
that is not in the graph, **add a child task to the parent and keep going** — do not stop, do
not ask. The execute loop re-derives the parent's graph each cycle and will pick the new slice
up on its own.

Two bounds on that, so a run cannot grow forever:

- **A doubling cap.** Added slices may not exceed the count the graph started with. On hitting
  the cap, stop adding, finish what exists, and say in the report what you wanted to add.
- **Label every added slice `scout/added=1`**, so the report can show what the idea grew into.
  That growth is a finding in its own right — it is the clearest possible evidence that the
  original framing was too small.

### 5. Report

Findings first. The forks are secondary.

- **Surprises** — what building it taught you that thinking about it could not. An API that
  does not behave as documented, two records that actually contradict, a layer that turned out
  trivial, a layer that needs a schema change. **This is the return on the whole run.** If this
  section is thin, say so plainly rather than padding it.
- **What the scope grew into** — every `scout/added=1` slice, and what prompted it.
- **Forks taken** — one line each, no risk ranking. Nothing shipped, so nothing has a blast
  radius. This is context for reading the skeleton, not a decision log to audit.
- **The branch**, and what actually runs end to end on it.
- **What is fake** — the stubs, the hardcoded values, the skipped edge cases. Be exhaustive
  here. An operator who mistakes a stub for a working feature is the most likely way this whole
  ritual does damage.

Then stop. Do not open a PR, do not merge, do not distill, do not write a spec, and do not offer
to "finish it properly" — the operator decides whether the next pass is a real brainstorm or a
second scout.

## Never

- Never run the gauntlet, the plan ritual, or a council review. If the output needed that level
  of assurance, scout was the wrong tool.
- Never write a `spec` or an `adr` record. Scout's output is a task graph, a branch, and a
  report.
- Never merge, and never open a PR.
- Never block on a question. Answer it, note it as a fork, and keep moving.
- Never let a slice go deep at the cost of the last slice existing at all.
