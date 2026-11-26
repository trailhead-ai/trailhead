# plan-orders-tasks-by-phase-and-records-a-reviewability-claim

## Under test

Given `/craft:plan task/manage-notes-from-the-command-line --unattended` on a slice parent whose four criteria are written out of phase order (delete, edit, list, add), plan orders the child tasks by phase: the task that reads (list) comes before the task that creates (add), and that comes before the tasks that change or delete (edit, delete) (AC11). Plan also writes a `**Reviewability:**` line into the parent's body, one line on why the planned change fits one reviewable pull request that names concrete sizes (tasks, files, eval cases, surfaces) (AC12, the unattended half; the attended half is graded in a later case). The un-narrowed unattended ending stays `outcome: plan-ready task/<name> parked=0`. The prose under test is `skills/plan/SKILL.md` (step 7, step 8, the unattended ending), `templates/plan.md` and `agents/planner.md` (the step 7 mirror), as they stand when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `notes` with four criteria on one entity and one command-line surface, in this order: AC1 deletes a note by id, AC2 edits a note's text by id, AC3 lists the notes, AC4 adds a note from the command line. The spec order is the reverse of the phase order, so a plan that follows the spec order and one that follows the phase order build different dependency chains (unknown U3: whether the model orders by phase unprompted). It then seeds a slice parent `Manage notes from the command line` (`in-progress`, label `craft/slice-parent`, related spec `notes`) carrying a value claim, `**Covers:** AC1, AC2, AC3, AC4` and one `## Unattended run` block with outcome `slice-chosen`. The slice is four criteria on one surface, small enough for one pull request, so the case never narrows. The spec has no operator-supplied path, no token and no external account, so no council lens has a Critical a plan edit cannot close. The prompt runs plan with `--unattended` on the slice parent, with `Agent` allowed for the council, `Write` for bodies and `timeout_seconds: 900`.

This case is also the runner canary: a run's `./lore record create` calls must land in the fixture vault, and the graders read those calls.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 900`.
- A run scores the fraction of graders that pass and passes only at 1.0. One rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- Arms: baseline (unedited prose) 3 runs, expected red, at least on the reviewability grader (no such field exists) and on the ordering graders if the model follows the spec order; treatment (edited prose) 3 runs, bar 3 of 3; two mutation probes (phase-ordering carriers removed, reviewability carriers removed), each expected red; regression re-runs of `unattended-slice-then-plan-ends-plan-ready`, `plan-keeps-an-earlier-run-section` and `planning-builds-each-folded-entry-into-a-task-or-reopens-it` on the edited prose, which must stay green.
- The ordering graders can be green on the baseline if the model reads before it creates on its own; the case-level result and the per-grader record in `tools/craft/MANUAL-EVAL.md` say so, and a non-discriminating ordering grader is reported as such.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A child task whose title adds or creates is created `--depends-on` a task named for listing, reading, showing or viewing | `a-create-task-depends-on-a-read-task` | `tool_used` Bash, `min: 1` |
| A child task whose title edits is created `--depends-on` a task named for adding or creating | `an-edit-task-depends-on-a-create-task` | `tool_used` Bash, `min: 1` |
| A child task whose title deletes is created `--depends-on` a task named for adding, creating or editing | `a-delete-task-depends-on-a-create-or-edit-task` | `tool_used` Bash, `min: 1` |
| A file the session writes carries a `**Reviewability:**` line naming a number or number word followed by tasks, files, eval cases or surfaces | `parent-body-carries-a-reviewability-line-naming-a-size` | `tool_used` Write, `min: 1` |
| The last line is `outcome: plan-ready task/<name> parked=0` | `final-line-is-plan-ready` | `regex` over the final message, the same pattern as the existing plan-ready cases |

## Limitations

- The ordering graders read one hop of the `--depends-on` edge from the Bash command and match on the dependency's slug, which is derived from the agent's own task title. A chain through a foundation task, an edit that depends on delete, or a title that avoids the words (`add`/`create`, `list`/`read`/`show`/`view`, `edit`, `delete`) can score wrongly. A transitive chain is not followed.
- The reviewability grader reads file-tool writes only: a body written through a Bash command would be missed. It cannot tell the parent's body from a child's, and it accepts any size word the line carries, not a judgment that the claim is true.
- A Write or Bash tool input reaches a `tool_used` grader JSON-escaped, so a newline is a literal backslash-n; the reviewability grader stops at it with `[^\\]*`.
- One spec, one phrasing, one model tier.
