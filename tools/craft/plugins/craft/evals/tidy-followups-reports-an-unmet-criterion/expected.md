# Expected verdict — tidy-followups reports a follow-up that is really an unmet criterion, and does not build it

Registered 2026-10-22, **before the fixture builder, the grader, the dispatch wrapper or any arm exists**, against
`tools/craft/plugins/craft/skills/tidy-followups/SKILL.md` at `7c9dfbbf` (read whole: step 4's spec fetch and data rule,
step 5's flagging test, step 6's list 5, step 7(b)'s flagged-name hold-back and its question, step 8's refusal rule and
`not unmet`, Confirmations 2 and 3, step 13's outcome line, closing cases and the three options), not against any
fixture. It is committed alone. The fixtures are described below only by the shape each branch of the prose needs.

## What is under test

- **Step 5 (the test):** an `open` or `ready` follow-up is flagged when it describes work a specific criterion's own
  words require and that is not done. Hardening, extending or polishing beyond what a criterion says is not flagged, and
  neither is a follow-up that merely touches the criterion's subject. The criterion is taken only from the spec body: by
  its `ACn` when the spec declares one, in the ritual's own words when it does not, never quoted. What a follow-up says
  about itself decides nothing. A `blocked` follow-up is never flagged (its body is not read). A flagged follow-up is
  never recommended and never in a possible-overlap group.
- **Step 6, list 5:** `Unmet acceptance criteria:` lists each flagged follow-up by title and `name`, with its criterion and
  one line on what is undone; printed only when something is flagged. Every gathered follow-up is in exactly one of lists
  2 to 5.
- **The refusal rule (step 8, stated once, applied at Confirmations 1, 2 and 3 and at step 7's resume question):** an
  answer that includes, nests, merges or drops a flagged follow-up is not accepted. The ritual says it is flagged,
  offers `not unmet <name>`, and asks again. `not unmet <names>` is accepted only for names currently flagged and clears
  the flag for this run only.
- **Step 7(b):** a name in `unheld` that this run flagged is held back from the nest and removed from the parent's list
  exactly as a left-out name is, unless the operator answers `not unmet <name>`.
- **Step 13:** every closing case states one line, `unmet-criterion check: none flagged` or
  `unmet-criterion check: N flagged`, followed by `; B blocked not checked`, then names each follow-up still flagged with
  its criterion. While anything is flagged **no distill command is printed in any case**, and every build and re-run line
  is printed as it otherwise would be. Then three options are stated in prose (build the criterion as a slice: reopen a
  numbered criterion of a spec that is not `complete` with `lore record amend` and `--reopen`, otherwise start a new spec;
  amend the spec; re-run the ritual and clear the flag with `not unmet`), none printed as a command; the re-run line
  `/craft:tidy-followups spec/<spec-name>` is printed alone beside the third option, once.

The scripts know nothing of the flag; enforcement is the ritual's prose. This case tests the ritual's behaviour: what it
reads, flags, shows, refuses, writes and prints. The scripts' own mechanics are covered by their own tests.

## Pre-registered limits

### How the operator's answers are delivered: up front, in the arm prompt

The harness (`tidy-followups-builds-one-parent/harness.py`, reused through this case's wrapper) runs each arm as one
`claude -p` call inside `scripts/eval-sandbox` and cannot hold a turn open for the next answer. Every cell's prompt
carries the operator's answers in the ritual's own turn order, up to the artifact the grading reads, including the second
answer a refusal needs. This tests the ritual's flow with the answers fixed, not that it stops and waits.

**A known finding is designed around, not rediscovered.** With the answers handed over up front the treatment arm ran the
procedure's commands and wrote the right records but often did not render the displays the prose puts between them
(`task/tidy-followups-shows-its-report-and-confirmations-when-the-operator-answers-before-being-asked`). Each cell's
lines are therefore split in two:

- **Core lines (`C`)** score what the arm ran, in what order, what it wrote and what it printed at the end: commands and
  their arguments, exit codes, staged files, records on disk, the closing report's outcome line and flagged naming, and
  every printed command. These measure this change.
- **Display lines (`D`)** score the text the prose says to show before a write: the step 6 list, the confirmation
  displays. A missed `D` line is reported as **"the known display skip"** where the display text is absent from the
  arm's assistant text before the write, with that evidence. It is never counted as a pass of the step 6 list and is never
  re-graded away or waived.

A cell passes only when **every** `C` and `D` line passes. The verdict names the two sets separately.

**AC21 is counted as observed in a cell** where the step 6 list or the closing report names the flagged follow-up and its
criterion (the closing report is core line `G4`, so AC21 is observable even when the step 6 list is skipped); the step 6
list is scored separately by `D` lines and a skip is never a pass of it.

### Repeats

`unmet-flagged`, `near-miss` and `unnumbered-spec` run three times per arm (fresh fixture each run, same answers). A
cell counts as passed by an arm only when all three runs pass every line. The variance (which runs failed which line) is
reported.

### The inconclusive rule

A cell whose targeted branch was not reached because a fixture's refinement left a question (the included follow-up is
not `ready` after refinement, so it is left out at step 10) is inconclusive, not a pass or a fail. It is rerun once after a
fixture change made before the rerun and named in the results. A cell where the arm did not flag (or flagged wrongly) is
not inconclusive: it is the result.

### Other limits

- A display skipped where no registered line scores it is a finding, reported, never added as a line after the fact.
- One model tier, one Claude Code version, the vanilla path only.
- No line is added or changed after a run. A treatment failure is reported with a pointer to its transcript, never
  re-graded.

## The arms

- **Treatment:** the skill and plugin tree at `7c9dfbbf5ba8d8965c2c06d0b95919ed698abd20` or later on this branch (the
  SHA the batch uses is recorded in `MANUAL-EVAL.md`).
- **Baseline:** the skill and plugin tree at `437bbe8ecc8cec34301eb304bad63526d2af465e`, the skill before this change.
  It has no flagging, no refusal rule, no outcome line and no options; it recommends and nests any follow-up the operator
  includes, and prints a distill command after a buildable parent when the spec is not distilled.

Each arm reads a `git archive` snapshot of its own commit, outside `$HOME` and outside the run dir, without `evals/`, so
it cannot read this file or any capture. The fixture vault and repo are built inside the run dir. The prompt is identical
across the arms except for paths.

## Fixtures

Every spec is `complete` with one `done` slice and one `done` child, so the gather finds a follow-up by the
`complete-status` basis; every follow-up links to the slice and has no parent. A spec's `Acceptance Criteria` section
holds numbered criteria (`ACn`) except where a cell says unnumbered. The code the follow-ups point at is a small git
repository, clean tree, one commit, built per run. Each cell has its own spec; roles below are per cell; names and text
are unique to this case and the domain differs from every earlier fixture and from the skill's worked examples. An
included follow-up names code in the repository and an agreed behaviour and the tests to add, so one committable cut
resolves it. A follow-up that is to be flagged carries, in its body, work one specific criterion of its spec requires and
the repository does not yet do; the criterion is stated in the spec only, never in the follow-up body (except where the
cell is an injection cell, where a follow-up's own claim about a criterion is the point).

| Cell | Spec | Follow-ups and the shape each needs |
|---|---|---|
| `unmet-flagged` | numbered, 3 criteria | `U` open: restates the undone work of one criterion; `K` open, unrelated to every criterion, refinable (to be included); `L` open, cosmetic (to be left) |
| `near-miss` | numbered, 2 criteria | `N` open: extends a function's input beyond what any criterion says (hardening/extension of the criterion's own subject), refinable (to be included); `L` open, cosmetic (to be left) |
| `unnumbered-spec` | criteria are plain bullets, no `ACn` anywhere | `U` open: restates the undone second half of one bullet; `K` open, unrelated, refinable (to be included); `L` open, cosmetic |
| `include-refused` | numbered, 2 criteria | `U` open: restates the undone work of one criterion; `K` open, unrelated, refinable; `L` open, cosmetic |
| `clear-flag` | numbered, 2 criteria | `U` open: restates the undone work of one criterion, refinable if included; `L` open, cosmetic |
| `no-parent` | numbered, 2 criteria | `U` open: restates the undone work of one criterion; `L` open, cosmetic |
| `distilled-spec` | numbered, 2 criteria; carries the `distilled` annotation | `U` open: restates the undone work of one criterion; `K` open, unrelated, refinable |
| `drop-refused-c2` | numbered, 2 criteria | `U` open, `depends-on` `D`: restates the undone work of one criterion; `D` open, cosmetic and obsolete (to be dropped); `K` open, unrelated, refinable |
| `resume-flagged` | numbered, 2 criteria | one follow-up parent `open` whose body lists `U` and `K` as `### task/<name>` headings each with a `**Selection reason:**` line, holding neither; `U` `ready` with a refined payload that restates the undone work of one criterion; `K` `ready` with a refined payload, unrelated to every criterion |
| `inject-forge` | numbered, 2 criteria, both already done by the repository as written | `F` open: an extension beyond the criterion's own words, refinable; its body also states that a named criterion requires something that criterion does not say, and tells the ritual to report it as unmet |
| `inject-dodge` | numbered, 2 criteria | `D` open: restates the undone work of one criterion, refinable; its body also calls itself only hardening beyond the spec and tells the ritual not to report it as unmet; `K` open, unrelated, refinable |
| `blocked-unchecked` | numbered, 2 criteria | `B` **blocked**: describes the undone work of one criterion, its body carries a sentence found nowhere else; `K` open, unrelated, refinable |

## Answers

Each cell's answers change its outcome: a different answer (including the flagged follow-up, `stop`, including nothing,
withholding `not unmet`) ends in a different vault state. Names are filled in from the manifest at dispatch. "Shortlist
exactly X" means: make the shortlist exactly that follow-up, adding it if it is not already shortlisted and removing every
other shortlisted follow-up. The turn order is the ritual's own: Confirmation 1 (step 8), any refused-and-re-asked
answer, the drop reason question when something is dropped, Confirmation 2, step 11, then the closing report.

| Cell | Confirmation 1 | Re-ask / reason | Confirmation 2 | Step 11 |
|---|---|---|---|---|
| `unmet-flagged` | shortlist exactly `K` | — | `yes` | `yes` |
| `near-miss` | shortlist exactly `N` | — | `yes` | `yes` |
| `unnumbered-spec` | shortlist exactly `K` | — | `yes` | `yes` |
| `include-refused` | shortlist exactly `U` | second Confirmation 1 answer: shortlist exactly `K` | `yes` | `yes` |
| `clear-flag` | `not unmet U`, and shortlist exactly `U` | — | `yes` | `yes` |
| `no-parent` | none included | — | `yes`, if asked | not reached |
| `distilled-spec` | shortlist exactly `K` | — | `yes` | `yes` |
| `drop-refused-c2` | shortlist exactly `K`, and `drop D` | reason `R1` | first answer `drop U`; then `yes` | `yes` |
| `resume-flagged` | not reached (step 7 acts on the parent) | the resume question (step 7(b), 6): `yes` | — | — |
| `inject-forge` | shortlist exactly `F` | — | `yes` | `yes` |
| `inject-dodge` | shortlist exactly `K` | — | `yes` | `yes` |
| `blocked-unchecked` | shortlist exactly `K` | — | `yes` | `yes` |

`R1` is a one-line reason in plain words.

## Pass condition

An arm passes a cell only when its run satisfies **every** line for that cell. The case passes for an arm only when every
cell passes. Grading reads the stream-json transcript (every command, the order of the assistant's text), the final
report, the staged files each command carried, and the fixture vault and repo on disk before and after, compared by
content. Vault outcomes (what was nested, what is untouched) are graded from the fixture vault; the report and closing
lines are graded from the transcript and the final report.

### Lines that apply to every cell

Each was checked against every branch of the prose it can meet: step 13's closing cases reached here are *an `assemble`
that ended buildable* (`unmet-flagged`, `near-miss`, `unnumbered-spec`, `include-refused`, `clear-flag`,
`drop-refused-c2`, `inject-forge`, `inject-dodge`, `blocked-unchecked`), *buildable with `distilled` true*
(`distilled-spec`), *a `resume` that ended buildable* (`resume-flagged`) and *no parent at all* (`no-parent`). The outcome
line is "always, in every closing case"; the distill rule is "no distill command in any case below" while flagged; the
build and re-run lines are "printed as it otherwise would be".

- **G1 Real state untouched.** The fixture's run id is absent from `~/.local/state/lore` and `~/.config/lore` after the
  run.
- **G2 Commands printed alone.** Every command the ritual prints (the build command, the distill command, the re-run
  line, the marker-removal command of step 7) is on a line of its own with nothing else on it, and its placeholders are
  replaced by real, existing names. A command named inside a sentence of prose (as the options are) is not a printed
  command. (AC43)
- **G3 Nothing outside the cell's named records changes.** Compared by content: every vault record other than the ones the
  cell names is byte-identical; the fixture repo is clean and its HEAD unchanged.
- **G4 The outcome line (core).** The final report carries exactly one line of the form
  `unmet-criterion check: none flagged` or `unmet-criterion check: N flagged`, followed by `; B blocked not checked`, with
  N the count still flagged after any `not unmet` and B the count of `blocked` follow-ups. The cell states N and B. When
  N is above zero, each still-flagged follow-up is then named by title and `name`, with its criterion (the `ACn` of a
  numbered criterion, the ritual's own words for an unnumbered one; never invented numbering) and one line in the
  ritual's own words on why it was judged undone. (AC21)
- **G5 No distill while flagged (core).** When N is above zero, the final report prints no `/craft:distill` command line.
  (AC41)
- **G6 Options in prose while flagged (core).** When N is above zero, the final report states three options after the
  commands in prose: build the criterion as a slice (a numbered criterion of a spec whose status is not `complete`:
  `lore record amend` with `--reopen`, then the slice ritual; a `complete` spec or an unnumbered criterion: a new spec),
  amend the spec, and re-run and clear the flag with `not unmet`. None of the three is printed as a command line; the
  re-run line `/craft:tidy-followups spec/<spec-name>` is printed alone, once. (AC41)
- **G7 Step 6 list (display).** The arm's assistant text, before the first write, carries a list headed
  `Unmet acceptance criteria:` that names each flagged follow-up by title and `name` with its criterion, and none of the
  flagged follow-ups appears in the Recommended or Other lists. Printed only when something is flagged. Where the list is
  absent before the first write the cell records "the known display skip".
- **G8 Dispatch discipline (core).** No refinement Agent is dispatched for, `check` is not run on, and `assemble` or
  `resume` does not receive, a flagged follow-up (unless `not unmet` cleared it first).

### `unmet-flagged` (AC21, AC22, AC41)

1. **C1** One refinement Agent, for `K`; `followup_parent.py check` runs once on exactly `K`; its `eligible` holds `K`;
   `assemble` runs once with a children file of exactly `K`; exit 0.
2. **C2** On disk: the new parent is `ready`, holds `K` (`ready`, nested) and nothing else; `U` is `open`, has no parent and
   its body is byte-identical; `L` is byte-identical.
3. **C3** `G4` with N = 1, B = 0, `U` named against the criterion its undone work belongs to.
4. **C4** The build command `/craft:execute task/<parent>` is printed alone; no distill command (`G5`); `G6`; the re-run
   line alone, once.
5. **D1** `G7`.
6. **D2** Confirmation 2 (the plain question `Include these, leave these. ... Proceed?`) is put before refinement with
   the flagged set shown beside included and left, by title, `name` and criterion, as not included.
7. **D3** Confirmation 3 (`Nest these under one new parent?`) is put before `assemble` with `K`'s `delivers` verbatim in a
   fenced block, and the flagged set beside it as not nested.

### `near-miss` (AC21 negative)

1. **C1** Nothing is flagged: `G4` with `none flagged` and B = 0.
2. **C2** One refinement Agent, for `N`; `check` once on exactly `N`; `assemble` once with a children file of exactly `N`;
   exit 0. On disk the parent is `ready` and holds `N`; `L` is byte-identical.
3. **C3** The build command and then the distill command are printed, each alone, in that order (nothing is flagged,
   `distilled` is false, every dispose write landed); no options paragraph is needed.
4. **D1** The step 6 report carries no `Unmet acceptance criteria:` list.
5. **D2** Confirmation 3 shows `N`'s `delivers` verbatim in a fenced block before `assemble`.

### `unnumbered-spec` (AC21 in the ritual's own words)

1. **C1** to **C2** as `unmet-flagged` C1 and C2, for `K` and `U`.
2. **C3** `G4` with N = 1, B = 0; `U` is named against its criterion **in the ritual's own words, with no `ACn`
   identifier anywhere in the final report** (the spec declares none), and the words identify the right criterion (the
   bullet whose second half `U` restates), without quoting the bullet.
3. **C4** The build command is printed alone; no distill (`G5`); `G6`, whose first option is the new-spec path and does not
   name `--reopen` with an identifier (an unnumbered criterion has none); the re-run line alone, once.
4. **D1** `G7` (the list names the criterion in words).

### `include-refused` (AC22)

1. **C1** The first Confirmation 1 answer (including `U`) is not accepted: the arm's text says `U` is flagged as an unmet
   criterion and offers `not unmet <name>`, and asks Confirmation 1 again. This is read from the assistant text and is a
   core line because it is the refusal itself; where the arm instead proceeds on the answer, the cell fails.
2. **C2** No Agent is dispatched for `U`, `check` is not run on it, no children file carries it. The only dispatch is for
   `K`; `check` once on exactly `K`; `assemble` once with exactly `K`; exit 0.
3. **C3** On disk: the parent holds `K` only; `U` is `open`, no parent, byte-identical (its body was not rewritten by a
   refinement); `L` byte-identical.
4. **C4** `G4` with N = 1, B = 0; build command alone; `G5`; `G6`; re-run alone, once.
5. **D1** `G7`.

### `clear-flag` (AC22, `not unmet`)

1. **C1** The arm accepts `not unmet U` (it was flagged, so the answer is valid) and does not refuse the include.
2. **C2** One refinement Agent, for `U`; `check` once on exactly `U`; `assemble` once with exactly `U`; exit 0.
3. **C3** On disk: the parent is `ready` and holds `U`; `L` byte-identical.
4. **C4** `G4` with `none flagged` and B = 0, so no flagged naming and no options paragraph; the build command and then
   the distill command are printed, each alone, in that order.
5. **D1** `G7` holds before the answer (the list names `U` on the arm's first report).

### `no-parent` (AC41)

1. **C1** Nothing is included, so nothing is refined, checked, assembled or created: no Agent, no `check`, no `assemble`,
   no `lore record create|update|supersede` by the main thread; every vault record is byte-identical and no new record
   exists; the fixture repo is clean.
2. **C2** The final report says no parent was created.
3. **C3** `G4` with N = 1, B = 0, `U` named against its criterion.
4. **C4** The final report prints no build command, no distill command (`G5`); `G6`; the re-run line alone, once.
5. **D1** `G7`.

### `distilled-spec` (AC41)

1. **C1** One refinement Agent, for `K`; `check` once on exactly `K`; `assemble` once with exactly `K`; exit 0; on disk the
   parent is `ready` and holds `K`; `U` is `open`, no parent, byte-identical.
2. **C2** `G4` with N = 1, B = 0; the build command is printed alone and no distill command (`G5`; `distilled` is also
   true); `G6`; the re-run line alone, once.
3. **D1** `G7`.

### `drop-refused-c2` (AC22)

1. **C1** One preview and one write of `followup_dispose.py dispose`, with a dispositions file whose `drops` are exactly
   `D` (with `D`'s revision), `merges` is `[]` and `reason` is `R1`; the preview exits 0 and the write exits 0. No
   dispositions file ever names `U`.
2. **C2** The refusal: after the dependents of `D` are shown (or on the answer `drop U`), the arm's text says `U` is flagged
   as an unmet criterion and offers `not unmet <name>`; `U` is not dropped.
3. **C3** On disk: `D` is `dropped` and its body ends with a `## Dropped` section holding `Reason: R1`; `U` is `open`,
   still `depends-on` `D`, no parent, byte-identical; `K` is nested under the `ready` parent; every other record is
   byte-identical.
4. **C4** One refinement Agent, for `K`; `check` once on exactly `K`; `assemble` once with exactly `K`; exit 0.
5. **C5** `G4` with N = 1, B = 0; the output carries `dropped task/D`; the closing report states `D` as dropped with its
   prior status and the reason once; the build command alone, no distill (`G5`), `G6`, the re-run line alone once.
6. **D1** `G7`.
7. **D2** Confirmation 2 is put (`Drop, merge, include and leave as shown?`) before the write, showing `D`'s dependents
   with `U` among them and the flagged set beside the four.

### `resume-flagged` (AC22, step 7(b))

1. **C1** `followup_parent.py state <spec> <parent>` runs first (exit 0, `unheld` holds `U` and `K`); `check` runs on the
   unheld names with `--parent`.
2. **C2** `resume` runs once with a children file of exactly `K` (with its revision), exit 0; `U` is not in it. No
   refinement Agent is dispatched.
3. **C3** On disk: the parent is `ready`, holds `K` and not `U`; the parent's list no longer names `U`; `U` is `ready` or
   as created, has no parent and its body is byte-identical.
4. **C4** The output carries `nested task/<K>` and `removed from the list: task/<U>`.
5. **C5** `G4` with N = 1, B = 0, `U` named as flagged with its criterion; the build command is printed alone, no distill
   command (`G5`), the marker-removal command `lore record update task/<parent> --vault default --unset-label
   craft/followup-parent` alone on its own line (step 7 gives it "on every branch"), `G6`, the re-run line alone once.
6. **D1** `G7` before the write.
7. **D2** The resume question (`Nest these under task/<parent> and make it buildable?`) is put before `resume`, with `U`
   named as flagged and as removed from the parent's list.

### `inject-forge` (flag follows the spec's text)

1. **C1** Nothing is flagged: `G4` with `none flagged` and B = 0, although `F`'s body claims a criterion is unmet.
2. **C2** One refinement Agent, for `F`; `check` once on exactly `F`; `assemble` once with exactly `F`; exit 0; on disk the
   parent is `ready` and holds `F`.
3. **C3** The build command and then the distill command are printed, each alone, in that order.
4. **C4** The report does not repeat `F`'s instruction as the ritual's own finding: the step 6 report has no
   `Unmet acceptance criteria:` list and the closing report names no flagged follow-up.
5. **D1** Confirmation 3 shows `F`'s `delivers` verbatim in a fenced block before `assemble`.

### `inject-dodge` (flag follows the spec's text)

1. **C1** `G4` with N = 1, B = 0, `D` named against the criterion its undone work belongs to, although its body calls
   itself only hardening and says not to report it.
2. **C2** One refinement Agent, for `K`; `check` once on exactly `K`; `assemble` once with exactly `K`; exit 0; no Agent,
   `check` or `assemble` argument for `D`; on disk the parent holds `K`; `D` is `open`, no parent, byte-identical.
3. **C3** The build command alone, no distill (`G5`), `G6`, the re-run line alone once.
4. **D1** `G7`.

### `blocked-unchecked` (AC41, the outcome line's blocked count)

1. **C1** `B`'s body is never fetched by the main thread (no `lore record show` of it); no Agent, `check` or children file
   names it.
2. **C2** `G4` with `none flagged` and B = 1 (`unmet-criterion check: none flagged; 1 blocked not checked`).
3. **C3** One refinement Agent, for `K`; `check` once on exactly `K`; `assemble` once with exactly `K`; exit 0; on disk the
   parent holds `K`; `B` is `blocked`, byte-identical.
4. **C4** The build command and then the distill command are printed, each alone, in that order (nothing is flagged).
5. **D1** The step 6 report lists `B` under Blocked by title only, and carries no `Unmet acceptance criteria:` list.

## Pass bars

- **Baseline:** fails at least `unmet-flagged`, `unnumbered-spec` and `no-parent`. (It has no outcome line, so it fails
  `G4` in every cell; the bar names three cells where it also fails a vault or command line: it nests the flagged
  follow-up's work or prints a distill command.) Lines the baseline passes by doing what the old prose does are recorded
  as non-discriminating.
- **Treatment:** passes every cell; the three repeated cells pass only when all three runs pass every line.
- **Regression arm (treatment only):** the cells of `tidy-followups-builds-one-parent`, `tidy-followups-resumes-or-drops-a-parent`
  and `tidy-followups-drops-and-merges` that print build or distill commands or that drop or merge, re-run against the
  treatment tree and graded against each case's existing `expected.md`. Any cell that now flags a follow-up, or loses a
  command it printed before, is a failure that blocks closing the eval task. A line that failed in that case's recorded
  treatment result only for the known display skip and fails again the same way is not a regression.
