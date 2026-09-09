# Planning — Define Tasks (step 7) — operating instructions (treatment)

You are the main session, running the planning ritual's step 7 ("Define Tasks") for the
feature described in the fixture file you were given. The fixture carries the spec under
review — including its `## Maturity` stamp and its Acceptance Criteria — plus a "Migration
bar (rendered)" section. That section is `scripts/migration_bar.py`'s real output, already
run against the fixture's own spec and target repository, embedded so you do not need to
invoke any script yourself — where the instructions below direct you to pipe the spec
through the renderer, treat that step as already done and read the embedded output instead.

Read the fixture file in full, then decompose the feature into tasks exactly as the
instructions below direct, and present your task list.

The instructions below are `skills/plan/SKILL.md`'s own step 7, reproduced, at the revision
this task commits.

---

### 7. Define Tasks

**Before decomposing, resolve whether migration and backfill work is suppressed for this plan's
target repository.** Pipe the linked spec's body through the renderer, by the same absolute-path
convention the existing gate invocations use:

```sh
lore record show spec/<spec-name> \
  | ${CLAUDE_PLUGIN_ROOT}/scripts/migration_bar.py [--target-repo <name>]
```

`--target-repo` names the plan's target repository — the camp member this plan builds against. Omit
it when the spec's `## Maturity` section names exactly one repository; supply it once the section
names more than one.

State the resolved level and its basis to the operator in session.

When the renderer's block opens with the literal token `— suppressed: migration and backfill`,
define no migration or backfill task for this plan — unless an acceptance criterion requires
preserving existing state, in which case name which criterion and keep the task, decomposed
normally. When the block opens with `— not-suppressed: migration and backfill`, decompose migration
and backfill work normally.

Both safe directions: on a non-zero exit, decompose migration work normally, and state the
renderer's own `reason-code:` to the operator — that vocabulary is authored by this script, not read
from repository content. And at any resolved level other than `prototype`, decompose migration work
normally.

**When migration and backfill tasks are suppressed, write the decision into the plan's `Given
Axioms` in step 8** — the renderer's resolved `target-repo`, `level`, and `basis`, and that it
reported `suppressed`; and, where the acceptance-criteria carve-out reopened them instead, the
criterion that did.

Break the feature into buildable tasks. Each task is the component-shaped unit beneath a slice — see
`_shared/slice.md` for the quality bar a slice must clear and the value floor it's read against.
Order tasks so that:

- Tasks with unproven unknowns come first
- Each task produces something testable
- Later tasks build on validated earlier ones

**Every task follows TDD.** Each task description must include the test contract — the behaviors to
prove with failing tests before writing implementation code. Tasks that skip or defer tests are not
valid tasks.

Tasks don't need step-by-step implementation detail. The subagent figures out how to build it. What
the plan needs is: what the task delivers, what files it touches, what unknown (if any) it depends
on, and what test behaviors prove the task works.

---

Present your output in two parts:

1. **Task list** — one task per bullet, naming what each task delivers. If a task is
   migration- or backfill-shaped, say so explicitly in the bullet.
2. **Given Axioms** — any lines the instructions above direct you to record there. Omit
   this section entirely if the instructions call for none.
