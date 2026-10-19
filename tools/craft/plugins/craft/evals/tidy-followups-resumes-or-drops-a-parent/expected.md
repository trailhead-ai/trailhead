# Expected verdict — tidy-followups resumes, refuses or drops an existing follow-up parent

Registered 2026-10-19, **before the fixture builder exists and before any arm ran**, against
`tools/craft/plugins/craft/skills/tidy-followups/SKILL.md` at `c2a30319` (read whole: step 7's three
branches and the drop flow, step 12's roll-forward, step 13's closing cases), not against any
fixture. It is committed alone. The fixtures are described below only by the shape each branch of
the prose needs.

## What is under test

Step 7 acts on a live follow-up parent the gather reports in `followup-parents`, instead of stopping:

- (b) exactly one `open` parent is **resumed**: `followup_parent.py state`, `check` on the listed
  follow-ups the parent does not hold, each eligible one's scope shown verbatim, one question, then
  `followup_parent.py resume` with a children file;
- (c) exactly one `ready`, `in-progress` or `blocked` parent is **refused**: named with its status,
  its build command printed alone, and one question offering the drop;
- the **drop flow**: `state`, the plan (children un-nested, children kept), the reason asked as its
  own question, one confirmation naming every child's fate, then `followup_parent.py drop`;
- step 12/13: a failed nesting leaves the parent `open` (no undo) and the closing report prints the
  re-run alone.

The scripts' own mechanics are covered by `tools/craft/tests/test_followup_parent*.py` and are not
re-tested here; this case tests the ritual's behaviour: what it runs, shows, asks, writes and prints.

## Pre-registered limit: answers are fixed in the arm prompt

As in `tidy-followups-builds-one-parent/expected.md`: an arm cannot be resumed once per answer inside
`scripts/eval-sandbox`, so every cell's prompt carries the operator's answers to every question it
reaches. This tests the ritual's flow with the answers fixed, not that it stops and waits. Each
question's text must still appear in the arm's output before the write it gates; a pass shows that,
never that the ritual waits.

Every cell's answers change the outcome. A different answer (`stop`, a name to leave out, nothing,
`stop` at the confirmation) would end in a different vault state; the cells below name the answer
given and the state it produces.

## The arms

- **Treatment:** the skill and plugin tree at `c2a30319`.
- **Baseline:** the skill and plugin tree at `7a48b25e5ef7cf054fbb595bc99d29bea36d5b07`, the skill
  before this change, whose step 7 names an existing parent and stops, printing a build command for
  `ready` and `in-progress` and printed un-nest and drop commands for `open` and `blocked`.

Each arm reads a `git archive` snapshot of its own commit (so each arm runs its own commit's
`followup_parent.py`), outside `$HOME` and outside the run dir, without `evals/`, as in the existing
case. The fixture vault and repo are built inside the run dir. The prompt is identical across the
arms except for paths.

## Fixtures

Every spec is `complete` with one `done` slice and one `done` child, so the gather finds a
follow-up by the `complete-status` basis. Every spec carries one follow-up parent: a task carrying
the `craft/followup-parent` label, related to the spec, whose body lists follow-ups as
`### task/<name>` headings each followed by a `**Selection reason:**` line. Each follow-up is linked
to the slice. Each spec's names and text are unique to this case.

| Cell | The parent | Its follow-ups |
|---|---|---|
| `resume` | `open` | two follow-ups listed, both `ready` with a refined payload (`Delivers`, `Test contract`, `Files`); one held (nested under the parent), one not held |
| `resume-left-out` | `open` | two listed; one held and `ready`; the other not held and gone back to `open` (it needs an operator decision refinement has not made) |
| `refuse-build` | `ready` | two listed, both held and `ready` |
| `refuse-drop` | `in-progress` | two listed and held: one `done`, one `ready` |

## Pass condition

An arm passes a cell only when its run satisfies **every** line for that cell. The case passes for an
arm only when every cell, here and in the re-registered `nest-fail` cell below, passes. Grading reads
the stream-json transcript (every command, the order of the assistant's text), the final report, and
the fixture vault and repo on disk before and after, compared by content.

### Lines that apply to every cell

- **Real state untouched.** The fixture's run id is absent from `~/.local/state/lore` and
  `~/.config/lore` after the run.
- **Commands printed (AC43).** Every command the ritual prints is on a line of its own, with
  nothing else on that line, and its placeholders are replaced by real, existing names. (A command
  named inside a sentence is not a printed command.)
- **Step 7 replaces steps 8 to 12.** No refinement Agent is dispatched, no `followup_parent.py
  check`-then-`assemble` path runs, and no `assemble` command runs. The gather's own report (step 6)
  or the empty-set outcome renders first.
- **Nothing outside the cell's named records changes.** Compared by content: every vault record other
  than the ones the cell names is byte-identical, the fixture repo is clean and its HEAD unchanged.

### `resume` (AC5; AC43 in part)

Answer to the one question: `yes`. (A different answer, naming the unheld follow-up to leave out,
would remove it from the list instead of nesting it; `stop` would write nothing.)

1. `followup_parent.py state <spec> <parent>` runs before any write, and the report names the
   unheld follow-up as the one to be nested and the held one with its status.
2. `followup_parent.py check <spec> <name>` runs on exactly the unheld follow-up's name, not on the
   held one.
3. The unheld follow-up's `delivers` and `files` text appears verbatim in the assistant's output,
   each in a fenced block labelled as that follow-up's own text, before the question and before
   `resume` runs.
4. The held child is shown with its current status, and the unheld follow-up's selection reason is
   given in the ritual's own words, before the question.
5. The warning that an `open` parent may still be mid-assembly elsewhere is given before the question.
6. The question `Nest these under task/<parent> and make it buildable?` (in that sense, with the
   parent's name) is put after those displays, and no `lore record create|update` command and no
   `resume` command appears before it.
7. `resume <spec> <parent> --children <file>` runs once, the children file written through `mktemp`
   and a quoted heredoc in the same shell call, holding exactly one `{name, revision}` for the unheld
   follow-up, the revision as `check` emitted it; it exits 0.
8. The output carries a `nested task/<name>` progress line for the unheld follow-up.
9. On disk: the parent is `ready`; both follow-ups have the parent as their parent; the parent's body
   lists both as `### task/<name>` headings, each with a selection reason; no other record changed.
10. The closing output prints the build command `/craft:execute task/<parent>` alone on its line and
    prints no `/craft:distill` command and no `/craft:tidy-followups` re-run line.

### `resume-left-out` (AC6; AC43 in part)

Answer: `yes`. (Naming a name would have been rejected: the left-out follow-up is not eligible.)

1. `state` runs, then `check` runs on exactly the unheld follow-up's name.
2. The report gives the plain sentence for `not-ready` (it is not at `ready` after refinement, so it
   cannot be nested), names the follow-up, and says it **will be removed from the parent's list** if
   the operator goes on.
3. No scope block is shown for it: its `delivers` and `files` text does not appear (it is not
   eligible).
4. The question is put after the report and before any write; no write and no `resume` before it.
5. `resume` runs once with a children file holding the empty list `[]`; it exits 0.
6. The output carries a `removed from the list: task/<name>` progress line for the left-out follow-up.
7. On disk: the parent is `ready` and still holds the held follow-up; the left-out follow-up has no
   parent, is still `open` and its record is byte-identical; the parent's body lists the held
   follow-up and has no heading for the left-out one.
8. The closing output prints the build command alone, no `/craft:distill`, no re-run line.

### `refuse-build` (AC7; AC43 in part)

Answer to the one question: build (the operator takes the build command and drops nothing).
(The answer `drop` would have entered the drop flow, as `refuse-drop` does.)

1. The gather returns no follow-up that is not nested, so the empty-set outcome renders (no follow-ups
   found, the slices checked named) and then step 7.
2. The parent is named with its status `ready`, and its build command `/craft:execute task/<parent>`
   is printed alone on its line.
3. The question offering the build or the drop (`Build it with the command above, or drop it?`) is
   put, so the report offers the drop as the other way forward.
4. The warning that a build already running against the parent must be stopped before a drop is given.
5. No `followup_parent.py resume`, `drop` or `assemble` command runs, no Agent is dispatched, and
   no `lore record create|update` runs: the vault is byte-identical before and after.
6. The way to remove the marker label (`lore record update task/<parent> --vault <vault>
   --unset-label craft/followup-parent`) is printed alone on its line.
7. No `/craft:distill` command and no `/craft:tidy-followups` re-run line is printed anywhere.

### `refuse-drop` (AC7; AC43 in part)

Answers, in order: `drop` to the step 7(c) question; the reason (one line) to the reason question;
`yes` to the confirmation. (`stop` at the confirmation would write nothing.)

1. The parent is named with its status `in-progress` and its build command is printed alone.
2. `followup_parent.py state <spec> <parent>` runs before any write.
3. The plan is shown by name from the state read: the `ready` child as the one to be un-nested and
   the `done` child as the one kept as the record of finished work.
4. The reason is asked as its own question (`Why is task/<parent> being dropped?`), after the plan and
   before the confirmation; the reason used is the one the answers give, never inferred.
5. The confirmation (`Un-nest these, keep those, and drop task/<parent> with this reason?`) names
   **both** children, the `ready` one as un-nested and the `done` one as kept, and shows the reason.
6. No `lore record create|update` and no `drop` command appears before the confirmation text.
7. `drop <spec> <parent> --reason-file <file>` runs once, the reason staged as one JSON string through
   `mktemp` and a quoted heredoc in the same shell call; it exits 0.
8. The output carries `un-nested task/<ready child>` and `kept task/<done child> (done)` progress lines.
9. On disk: the parent is `dropped` and its body carries a `## Dropped` section containing the reason
   the answers gave; the `ready` child has no parent and is still `ready`; the `done` child still has
   the parent and is still `done`; no other record changed.
10. The closing output prints the re-run `/craft:tidy-followups spec/<spec>` alone on its line and no
    `/craft:distill` command.

### `nest-fail` — re-registered (existing case `tidy-followups-builds-one-parent`)

The existing case's `nest-fail` cell (fixture, shim and answers unchanged) is re-registered here, and
registered in that case's `expected.md` as Revision 4, because the prose it measured changed: step
12 no longer undoes a failed nesting. Answers: both follow-ups added if absent; `yes`; `yes`.

Core lines (this change's):
1. Both are refined, both eligible, the scope is shown, and `assemble` runs once.
2. `assemble` reports `nesting-incomplete` (exit 2), and the report names the failure by follow-up
   and write: the second follow-up's nesting. It shows the first follow-up as nested and the parent
   as not buildable, and says the parent is left being assembled, nothing undone.
3. **No build command is printed anywhere.** The re-run `/craft:tidy-followups spec/<spec>` is printed
   alone on its line, and no `/craft:distill` command is printed.
4. **Nothing is dropped or un-nested.** No `drop` command and no `lore record update ... --unset-parent`
   runs after the failure.
5. On disk: the parent is `open` and holds the first follow-up (still `ready`); the second follow-up
   has no parent; the parent is not `dropped`.
6. `followup_gather.py` run on the spec afterwards returns the parent in `followup-parents` and the
   second follow-up in its follow-ups.

The scoped lines of the existing case (nothing written before the disposition set is shown;
Confirmation 1 put) also apply to this cell as registered there. Those lines failed in every earlier
round of that case on the treatment arm, for reasons outside this change (the arm skipping displays
when answers are given up front). This cell's verdict therefore names **core lines and scoped lines
separately**; the cell passes only when both pass, and a scoped-line miss is not read as this
change's failure but is not waived either.

### Inconclusive cells

As in the existing case: a cell whose targeted branch is not reached (for example the `resume-left-out`
follow-up is somehow resolved before `state`, or `nest-fail`'s refinement leaves a question so the
shim's failure never fires) is inconclusive, not a pass or fail; it may be rerun once after a fixture
change inside this file's description, both runs recorded.

## Expected failure — baseline arm

The old step 7 stops at the parent. So the baseline fails `resume` and `resume-left-out` on every
line that needs `state`, `check`, `resume`, a question or a disk change; fails `refuse-drop` the same
way (it never asks or drops); and fails `nest-fail` on lines 3 to 5 (it undoes the nesting, drops the
parent, prints the distill command). `refuse-build` is the cell the baseline can partly pass: it
prints the build command alone and writes nothing. It still fails lines 3 and 4 (no question, no drop
as the other way forward), since the old prose never offers one. Where the baseline passes a line by
doing nothing, that is recorded as such and is not discrimination. **Each baseline cell must fail at
least one registered line; a cell whose baseline passes every line is not measuring the change and
is reported as such.**

## Limitations, stated up front

- One run per cell is an anecdote about that run, not a measure of variance.
- Answers are fixed in the prompt, so a pass shows the flow, not that the ritual waits.
- A skipped display is observable only when it matters to a registered line; an observed miss no
  registered line scores is a finding against the prose, reported and never added as a line after
  the fact.
- One model tier, one Claude Code version, the vanilla path only.

## Label correction (2026-10-19, after the batch)

Label correction (2026-10-19, after the batch): the headings above attach two criteria to the wrong
cells. `resume-left-out` is a resume cell and covers AC5 (the left-out reporting is part of resuming),
not AC6; `refuse-build` is the AC6 cell (a buildable or in-progress parent is refused with its build
command), not AC7. `refuse-drop` stays AC7. No pass line and no verdict changed.
