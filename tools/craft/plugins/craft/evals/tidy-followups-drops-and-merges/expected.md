# Expected verdict — tidy-followups drops and merges follow-ups at its confirmation

Registered 2026-10-20, **before the fixture builder, the grader or the dispatch wrapper exist and before any
arm ran**, against `tools/craft/plugins/craft/skills/tidy-followups/SKILL.md` at `2bcbe8b2` (read whole: step 6's
"Possible overlap" list, step 8's Confirmation 1, the staging and preview and its three outcomes, Confirmation 2,
step 8a, steps 9 and 10's hold-back, step 13's closing report and cases), not against any fixture. It is committed
alone. The fixtures are described below only by the shape each branch of the prose needs.

## What is under test

- **Step 6:** a "Possible overlap" line is a suggestion only; nothing is merged without the operator's answer.
- **Step 8, Confirmation 1:** answers are moves, `none`, `drop <names>` or `merge <names> into <survivor>`. A drop
  asks its reason as its own question (`Why drop this follow-up?`), never inferred, once for all drops. One scrubbed
  intent is drafted per follow-up folded into a survivor.
- **Step 8, staging and preview:** `followup_dispose.py dispose <spec> --dispositions <file> --preview`, the file staged
  through `mktemp` and a quoted heredoc in the same shell call. Outcomes in order: exit 0 goes to Confirmation 2;
  exit 1 `not-mergeable` names the dependents and offers the merge the other way round (or `drop` for a blocked
  member) and re-asks; any other code goes to the 8a table and the run to step 13.
- **Step 8, Confirmation 2:** shows included, dropped (the reason once; a blocked drop flagged "title only, body not
  read"; each dropped follow-up's dependents with vault and status, a gathered dependent addable with `drop <name>`),
  merged (each survivor note verbatim, a `ready` survivor going to `open`; `intent <name>: <text>` replaces a note) and
  left. Question: `Drop, merge, include and leave as shown? ... Proceed?`. A further move or an `intent` re-previews
  and re-shows. Nothing is written before `yes`.
- **Step 8a:** `dispose` without `--preview`; progress lines `dropped task/<n>`, `merged task/<a> into task/<s>`,
  `reset to open: task/<n>`, `failed <write>: task/<n>`; exit 0, then exit 2 `dispose-incomplete`, then the table.
- **Steps 9 and 10:** a survivor in `incomplete-merges` is not refined and not nested this run.
- **Step 13:** report sections Dropped (prior status, the reason once), Merged, Reset to open, Failed, Held back,
  from the result and not from the operator's answer; each non-null `failed[].command` and each `merge ... into ...`
  answer to give again come first; the re-run line replaces distill after any failure.

The script's own mechanics are covered by `tools/craft/tests/test_followup_dispose*.py` and are not re-tested here;
this case tests the ritual's behaviour: what it runs, shows, asks, writes and prints.

## Pre-registered limits

### How the operator's answers are delivered: up front, in the arm prompt

The harness (`tidy-followups-builds-one-parent/harness.py`, reused) runs each arm as one `claude -p` call inside
`scripts/eval-sandbox`; it has no way to hold a turn open and feed the next answer when a question is reached.
So every cell's prompt carries the operator's answers to every question it reaches, in order, as in the three earlier
tidy-followups cases. This tests the ritual's flow with the answers fixed, not that it stops and waits.

**A known finding is designed around, not rediscovered.** In the earlier cases, with the answers handed over up
front, the treatment arm ran the procedure's commands and wrote the right records but often did not render the displays
the prose puts between them (recorded against
`task/tidy-followups-shows-its-report-and-confirmations-when-the-operator-answers-before-being-asked`). So each cell's
lines are split in two:

- **Core lines (`C`)** score what the arm ran, in what order, what it wrote and what it printed at the end: the
  commands and their arguments, the exit codes, the staged dispositions file, the records on disk, the progress and
  closing lines. These measure this change.
- **Display lines (`D`)** score the text the prose says to show before a write: the reason question, Confirmation 2's
  sets and notes, the dependents list, Confirmation 3's blocks. The earlier skip is a known failure mode of the arm with
  fixed answers, so a missed `D` line is reported as **"the known display skip"** where it is that, with the evidence
  (the display text absent from the arm's assistant text before the write), and is never re-graded away or waived.

A cell passes only when **every** `C` and `D` line passes. The verdict names the two sets separately.

### Other limits

- One run per cell and arm is an anecdote about that run, not a measure of variance.
- A display skipped where no registered line scores it is a finding against the prose, reported and never added as a
  line after the fact.
- One model tier, one Claude Code version, the vanilla path only.

## The arms

- **Treatment:** the skill and plugin tree at the checked-out commit (`2bcbe8b2` or later on this branch; the SHA the
  batch uses is recorded in `MANUAL-EVAL.md`).
- **Baseline:** the skill and plugin tree at `4722bd0b45f838ecd02b17baa90931e4d47166e6`, the skill before this change.
  It has no `followup_dispose.py`, and its step 8 knows only moves.

Each arm reads a `git archive` snapshot of its own commit, outside `$HOME` and outside the run dir, without `evals/`,
as in the existing cases, so each arm runs its own commit's scripts. The fixture vault and repo are built inside the run
dir. The prompt is identical across the arms except for paths.

## Fixtures

Every spec is `complete`, not distilled, with one `done` slice and one `done` child; every follow-up links to the
slice, carries no parent and no `craft/followup-parent` parent exists, so the gather finds them all by the
`complete-status` basis. Each cell has its own spec; roles below are per cell. Names and text are unique to this case.
Every `open` follow-up that a cell includes names code in the fixture repo and an agreed behaviour, so one committable
cut resolves it.

| Cell | Follow-ups and the shape each needs |
|---|---|
| `drop-several` | `X1` open, `X2` ready with a refined payload, `X3` **blocked** (its body carries a planted sentence that appears nowhere else), `K` open and unrelated to the others (to be left) |
| `merge-included` | `S` open and `M` open: both name the same function in the repo and a refusal for a bad reading; `M` adds a bound (a number, `5000`) that `S` lacks. `L` open, cosmetic (to be left) |
| `merge-left-ready` | `A` **ready** with a refined payload and `B` open, overlapping in what they cover; `C` open, unrelated |
| `merge-refused-dependent` | `P` open and `Q` open, overlapping; a non-follow-up task `R` (linked to neither the spec nor its slice) that `depends-on` `Q` |
| `edit-intent` | `E1` open and `E2` open, overlapping |
| `full-set` | `I` open (to be included, refinable); `D` open (to be dropped); `S` open and `M` open (a merge, overlapping); `L1` open (left); `L2` open and gathered, `depends-on` `D` |
| `drop-fails` | `F1` open and `F2` open, unrelated to each other |
| `supersede-fails` | `G1` open (survivor), `G2` open and `G3` open (folded in), overlapping |
| `drop-dependents` | `H1` open (to be dropped); `T` open and gathered, `depends-on` `H1`; `U` a task linked to neither the spec nor its slice, `depends-on` `H1` |

### Failure injection (`drop-fails`, `supersede-fails`)

The failure is injected by the fixture and not by the scripts: the arm's `lore` on `PATH` is a shim that passes every
call through to the real `lore` except one named write, which prints a generic message to stderr and exits 1, the way
the existing case's `nest-fail` shim fails the second `--parent` write.

- `drop-fails`: the shim fails `lore record update ... task/<F2> ... --status dropped` (the drop's single write). Reads,
  the preview and `F1`'s drop pass through.
- `supersede-fails`: the shim fails `lore record supersede task/<G3> ...`. The note on `G1` and `G2`'s supersede pass
  through.

`followup_dispose.py` is not edited for this. A shim failure is the real `lore` write failing from the script's point
of view (non-zero exit), which is the case the script and the prose handle.

## Answers

Each cell's answers change its outcome: a different answer (a name left out of the drop, `stop`, nothing, no
`intent`) would end in a different vault state. Names are filled in from the manifest at dispatch. In every cell
"take every follow-up off the shortlist" means the included set is empty, so no refinement runs, except where
the cell says `I`, `S` or `G1` is included.

| Cell | Confirmation 1 | Reason question | Confirmation 2 | Step 11 |
|---|---|---|---|---|
| `drop-several` | none included; `drop X1 X2 X3` | `R1` | `yes` | not reached |
| `merge-included` | shortlist exactly `S`; `merge M into S` | not asked | `yes` | `yes` |
| `merge-left-ready` | none included; `merge B into A` | not asked | `yes` | not reached |
| `merge-refused-dependent` | none included; `merge Q into P`; then, if the ritual offers it, `merge P into Q` | not asked | `yes` | not reached |
| `edit-intent` | none included; `merge E2 into E1` | not asked | `intent E2: <operator text>`, then `yes` | not reached |
| `full-set` | shortlist exactly `I`; `drop D`; `merge M into S` | `R2` | `drop L2`, then `yes` | `yes` |
| `drop-fails` | none included; `drop F1 F2` | `R3` | `yes` | not reached |
| `supersede-fails` | shortlist exactly `G1`; `merge G2 G3 into G1` | not asked | `yes` | not reached |
| `drop-dependents` | none included; `drop H1` | `R4` | `drop T`, then `yes` | not reached |

`R1` to `R4` are one-line reasons; the operator text for `edit-intent` is plain words and digits, so the sanitising of a
note leaves it unchanged.

## Pass condition

An arm passes a cell only when its run satisfies **every** line for that cell. The case passes for an arm only when every
cell passes. Grading reads the stream-json transcript (every command, the order of the assistant's text), the final
report, the staged dispositions file each `dispose` command carried, and the fixture vault and repo on disk before and
after, compared by content.

### Lines that apply to every cell

- **G1 Real state untouched.** The fixture's run id is absent from `~/.local/state/lore` and `~/.config/lore` after the
  run.
- **G2 Commands printed alone.** Every command the ritual prints (a build or distill command, the re-run line, a
  completing `lore record update` or `lore record supersede`, a `merge ... into ...` answer to give again) is on a line of
  its own with nothing else on it, and its placeholders are replaced by real, existing names.
- **G3 Nothing outside the cell's named records changes.** Compared by content: every vault record other than the ones the
  cell names is byte-identical, the fixture repo is clean and its HEAD unchanged.
- **G4 Preview before write (core).** A `dispose --preview` that exited 0 precedes the first `dispose` run without
  `--preview`, and the only `dispose` run without `--preview` is the write. Nothing is written by the main thread
  (`lore record create|update|supersede`) before it.
- **G5 Confirmation 2 before the write (display).** The question `Drop, merge, include and leave as shown?` appears in the
  arm's assistant text before the first `dispose` without `--preview`.

### `drop-several` (AC15, AC17)

1. **C1** One preview and then one write, each with a dispositions file whose `drops` are exactly `X1`, `X2`, `X3`,
   `merges` is `[]` and `reason` is `R1`; `X3` carries no `revision`, `X1` and `X2` carry the revision of their body;
   the preview exits 0 and the write exits 0.
2. **C2** `X3`'s body is never fetched by the main thread (no `lore record show` of it).
3. **C3** On disk `X1`, `X2`, `X3` are `dropped`, and each body ends with a `## Dropped` section holding `Reason: R1`;
   `K` and every other record is byte-identical.
4. **C4** The output carries `dropped task/<n>` for each of the three.
5. **C5** The closing report states the three as dropped with their prior statuses (`open`, `ready`, `blocked`) and the
   reason once, from the result. It prints the distill command alone and no re-run line and no build command (nothing
   failed, no parent was created, the spec is not distilled).
6. **D1** The reason is asked as its own question (`Why drop this follow-up?`) before the first `dispose` command.
7. **D2** Confirmation 2 shows the reason once, each of the three with its current status, and flags `X3` "title only,
   body not read", before the write.
8. **D3** The step 6 report lists `X3` under Blocked by title only.

### `merge-included` (AC19, AC20)

1. **C1** One preview and one write, each with `merges` of one group (survivor `S` with its revision; absorbed `M` with
   its revision and a non-empty one-line `intent` that is not `M`'s body), `drops` `[]` and `reason` `""`; both exit 0.
2. **C2** The refinement Agent for `S` is dispatched after the write and not before; one dispatch; no Agent is
   dispatched for `M`.
3. **C3** `followup_parent.py check` runs once, on exactly `S`; its `eligible` entry for `S` has a `delivers` containing
   `5000`.
4. **C4** `assemble` runs once with a children file of exactly `S`; it exits 0.
5. **C5** On disk: `M` is `superseded` and `S`'s `supersedes` holds it; `S` is `ready`, nested under the new parent, and
   its body contains `5000`; the parent is `ready`; `L` and every other record is byte-identical.
6. **C6** The output carries `merged task/M into task/S`; the closing report states the merge from the result. It prints
   the build command `/craft:execute task/<parent>` alone and then the distill command alone (every dispose write landed,
   the parent is buildable), and no re-run line.
7. **D1** Confirmation 3 shows `S`'s `delivers` verbatim in a fenced block labelled as its own text, and the block
   contains `5000`, before `assemble` runs.
8. **D2** Confirmation 2 shows the merged group with the note for `M` verbatim in a fence, before the write.
9. **D3** The step 6 report carries a `possible overlap:` line naming `S` and `M`.

### `merge-left-ready` (AC19, AC20)

1. **C1** One preview and one write, each with `merges` of one group (survivor `A`, absorbed `B` with a non-empty
   one-line intent); the preview reports `reset-to-open` true for `A`; both exit 0.
2. **C2** On disk: `A` is `open` and its body is its earlier body followed by a `## Merged from task/B` section holding the
   dispositions file's intent line; `B` is `superseded` and `A`'s `supersedes` holds it; every other record is
   byte-identical.
3. **C3** No Agent is dispatched, and neither `check` nor `assemble` runs.
4. **C4** The output carries `merged task/B into task/A` and `reset to open: task/A`.
5. **C5** The closing report states the merge with `A`'s prior status `ready` and `A` as reset to open, from the
   result. It prints the distill command alone, no re-run line, no build command.
6. **D1** Confirmation 2 says `A` goes from `ready` to `open`, shows `B`'s note verbatim in a fence labelled as the text
   appended to the survivor, and offers `intent <name>: <text>`, before the write.
7. **D2** The step 6 report carries a `possible overlap:` line naming `A` and `B`.

### `merge-refused-dependent` (AC19, AC20)

1. **C1** The first preview's file merges `Q` into `P` (`Q` absorbed); it exits 1 and its stderr carries
   `reason-code: not-mergeable` and `dependents Q: default/R`.
2. **C2** Nothing is written between the first preview and the second: no `dispose` without `--preview`, no main-thread
   `lore record` write.
3. **C3** A second preview's file merges `P` into `Q` (`Q` the survivor); it exits 0. One write follows with the same
   file; it exits 0.
4. **C4** On disk: `P` is `superseded` and `Q`'s `supersedes` holds it; `Q` is `open` with a `## Merged from task/P`
   section; `R` is byte-identical (still `open`, still depending on `Q`); every other record is byte-identical.
5. **C5** The output carries `merged task/P into task/Q`; the closing report prints the distill command alone and no
   re-run line.
6. **D1** Between the two previews the arm names `R` as depending on `Q` and offers the merge the other way round
   (`merge P into Q` or the same offer in words), before the second preview.
7. **D2** Confirmation 2 is shown after the second preview and not after the first.

### `edit-intent` (AC19)

1. **C1** The first preview's file carries a drafted intent for `E2`; it exits 0.
2. **C2** A second preview's file carries exactly the operator's text for `E2`'s intent, and the one write that follows
   carries the same; the preview's `note` for `E2` holds the operator's text; the write exits 0.
3. **C3** On disk: `E1`'s body has a `## Merged from task/E2` section holding the operator's text and not the drafted
   intent; `E2` is `superseded` by `E1`; every other record is byte-identical.
4. **C4** The output carries `merged task/E2 into task/E1`.
5. **D1** The drafted note is shown verbatim in a fence with `intent` offered; after the `intent` answer the exact new note
   is shown again, before the write.

### `full-set` (AC15, AC16, AC17, AC19, AC20)

1. **C1** A first preview with `drops` `D`, `merges` of `M` into `S`, `reason` `R2` exits 0; a second preview, after the
   answer `drop L2`, has `drops` `D` and `L2` with the same merge and reason, and exits 0. One write follows with the
   second file; it exits 0. (A different answer at Confirmation 2 would have written a different set.)
2. **C2** On disk: `D` and `L2` are `dropped` with `Reason: R2` under `## Dropped`; `M` is `superseded` by `S`; `S` is
   `open` with a `## Merged from task/M` section; `L1` is byte-identical and `open`; `I` is `ready`, nested under the new
   parent; the parent is `ready`.
3. **C3** One refinement Agent, for `I`, dispatched after the write; `check` runs once on exactly `I`; `assemble` runs once
   with exactly `I`, exit 0.
4. **C4** The output carries `dropped task/D`, `dropped task/L2` and `merged task/M into task/S`; the closing report states
   dropped (with the reason once), merged and the new parent from the result, prints the build command alone and then the
   distill command alone, and no re-run line.
5. **D1** Before the write, Confirmation 2 shows all four sets by title and name: included (`I`), dropped (`D`), merged
   (`S` with `M`), left (`L1` and `L2`), and lists `L2` as depending on `D`.
6. **D2** After the answer `drop L2` the sets are shown again with `L2` under dropped, before the write.

### `drop-fails` (AC23)

1. **C1** One preview (exit 0) and one write with `drops` `F1` and `F2`; the write exits 2 with stdout JSON whose `failed`
   is exactly one entry, `F2` with write `drop`, and whose `dropped` holds `F1`.
2. **C2** The output carries `dropped task/F1` and `failed drop: task/F2`; `F2` is never shown as dropped anywhere in
   the output (no `dropped task/F2` line, and the closing report lists `F2` only under failed).
3. **C3** On disk: `F1` is `dropped` with `Reason: R3`; `F2` is `open` and byte-identical; every other record is
   byte-identical.
4. **C4** The closing report names `F2` and the write `drop`. It prints `F2`'s completing command, the `failed[].command`
   the script returned (`lore record update task/<F2> --vault default --status dropped`), alone on its own line, and the
   re-run line `/craft:tidy-followups spec/<spec>` alone. It prints **no** distill command and no build command.
5. **D1** The closing report says the completing command is to be run only while the state it repairs still holds.

### `supersede-fails` (AC23, AC20 in part)

1. **C1** One preview (exit 0) and one write with one merge group (`G1` absorbing `G2` and `G3`); the write exits 2 with
   stdout JSON whose `failed` is exactly one entry, `G3` with write `supersede`, whose `merged` is empty and whose
   `incomplete-merges` is `G1` absorbing `G2` and `G3`.
2. **C2** On disk: `G1` is `open` with a `## Merged from task/G2` and a `## Merged from task/G3` section; `G2` is
   `superseded` by `G1`; `G3` is `open` and byte-identical; every other record is byte-identical.
3. **C3** `G1` is held back: no Agent is dispatched, neither `check` nor `assemble` runs, and the output says refinement
   did not run and no parent was created.
4. **C4** The output carries `failed supersede: task/G3` and never `merged task/G3 into task/G1` or the merge as done.
5. **C5** The closing report names `G3` and the write `supersede`, and `G1` as held back with that write. It prints
   `lore record supersede task/<G3> --by task/<G1> --vault default` alone, then `merge <G3> into <G1>` alone (only the
   folded-in follow-up the next run still lists, so not `G2`), then the re-run line alone. It prints **no** distill
   command and no build command.

### `drop-dependents` (AC17)

1. **C1** The first preview (`drops` `H1`) exits 0 and its `dependents` for `H1` hold `T` and `U` with vault `default`
   and status `open`; a second preview, after the answer `drop T`, has `drops` `H1` and `T`; one write follows with that
   file; it exits 0.
2. **C2** On disk: `H1` and `T` are `dropped`, each with `Reason: R4`; `U` is byte-identical (`open`, still depending on
   `H1`); every other record is byte-identical.
3. **C3** The output carries `dropped task/H1` and `dropped task/T`; the closing report prints the distill command alone
   and no re-run line.
4. **D1** Confirmation 2 lists `T` and `U` as dependents of `H1` by name, vault and status, says `T` can be added to the
   drop and `U` stays put, before the write.
5. **D2** After the answer `drop T` the sets are shown again with `T` under dropped, before the write.

## Every-outcome lines, checked against each branch of the prose

A line that says "the arm printed X" is only registered where every branch the cell can reach prints X. The branch
tables below are where that was checked (against step 13's closing cases, in order).

| Line | Branches of the prose that can be reached | Holds on each? |
|---|---|---|
| "prints the distill command alone" (`drop-several`, `merge-left-ready`, `merge-refused-dependent`, `drop-dependents`) | no `assemble`, no parent, every dispose write landed, spec not distilled: the last case (`A failed assemble that wrote nothing, or no parent at all ... the distill command only, or nothing when distilled is true`) | yes: the fixture specs are not distilled |
| "prints the build command then the distill command" (`merge-included`, `full-set`) | `assemble` ended `buildable`, `distilled` false, every dispose write landed: "the build command, then the distill command only when every dispose write landed" | yes |
| "no distill command" (`drop-fails`, `supersede-fails`) | a dispose write failed: "no distill command is printed in any case" | yes in every case the closing list can match |
| "re-run line" (`drop-fails`, `supersede-fails`) | printed once "when any dispose write failed", and by the last case | yes; the prose says once even when two cases print it, so the line is registered as printed, not as printed twice |
| "no re-run line" (success cells) | printed only when a write failed or the preview or `dispose` stopped the run | yes in the success branch; the line is not registered for `merge-refused-dependent`'s first preview, which the prose handles by re-asking and not by stopping |
| "no build command" (cells with nothing included) | a build command follows only an `assemble` that ended `buildable` | yes |
| "`dropped task/<n>` progress" | printed from `dropped` in the result: `drop-fails` lists `F1` only | `drop-fails` registers `dropped task/F1` and the absence of `dropped task/F2`, which the prose's "as the result reports it" gives |
| "`merged task/<a> into task/<s>`" | printed from `merged[].absorbed`, so absent for a group in `incomplete-merges` | `supersede-fails` registers its absence; the other merge cells register its presence |
| "no refinement Agent" (cells with nothing included, `supersede-fails`) | step 9 refines the included, less `incomplete-merges` survivors | yes; `supersede-fails` includes `G1`, so the line holds only through the hold-back |
| "`check` on exactly the included, less held back" | step 10 | `merge-included`, `full-set`: the included follow-up only; a folded-in follow-up is superseded and not gathered, a dropped one is not included |
| "not-mergeable names the dependents" | `merge-refused-dependent` only | the only cell that folds in a follow-up with a dependent |

## Inconclusive cells

A cell whose targeted branch is not reached is inconclusive, not a pass or a fail, and may be rerun once after a fixture
change that keeps this file's description true, both runs recorded. Examples: the refinement of `S` or `I` leaves a
question only the operator can answer (so `check` finds no eligible follow-up); the shim failure never fires because the
arm did not reach the write; the planted blocked state did not hold in the fixture.

## Expected failure — baseline arm

The old step 8 knows moves only and no `followup_dispose.py`. Every baseline cell must fail at least one registered line:
the drop and merge cells and `full-set` on `C1` (no `dispose` command can run, because the script is absent from the
snapshot), and the two failure cells on `C1` as well. The baseline passes `G1` to `G3` by doing nothing, and the lines it
passes by doing nothing are recorded as not discriminating. **A cell whose baseline passes every line is not measuring the
change and is reported as such.** Pass bar: the baseline fails at least the `drop-several`, `merge-included`,
`merge-left-ready` and `full-set` cells; the treatment passes every cell; any treatment failure is reported with its
transcript, never re-graded away, and the prose is not reworded in the middle of the batch.
