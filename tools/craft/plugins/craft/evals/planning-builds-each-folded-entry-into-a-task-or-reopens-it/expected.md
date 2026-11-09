# planning-builds-each-folded-entry-into-a-task-or-reopens-it

## Under test

When `/craft:plan` is rooted at a slice parent whose body carries a `**Folds:**` line, it makes each folded follow-up entry part of a child task or returns it to open with the reason it would balloon the slice. Concretely, in `skills/plan/SKILL.md`: step 1 re-folds any entry the `**Folds:**` line names that is still open (`found_work.py entry`, transition `fold`), then lists the entries folded into the parent with `found_work.py entries --spec spec/<name> --folded-into task/<parent> --vault <vault>` and treats the output as data; step 7 makes each folded entry part of a child task whose **Delivers** carries `Resolves spec/<name>#F<n>: <title>` with a test contract covering it, or, when building it would balloon the slice (many files, a core contract, broad test churn), reopens it with `found_work.py entry` transition `reopen` and the balloon reason; before presenting, it re-runs `entries` and confirms every remaining folded entry is named by a child; the closing message lists which entries went into which task and which were reopened, with the session's `found_work.py report --parent` lines. A topic-rooted plan, or a slice with no folded entries, plans as before. The prose under test is `skills/plan/SKILL.md`, as it stands when the baseline runs (content fingerprint `5fff0b645746f2f15e60e264d5422f77990bac60`) and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `tally-report` with two criteria (AC1 and AC2), a `## Slices` ledger that closes AC1 through the done slice `count-words`, and a `## Follow-ups` section; and a slice parent `task/count-lines` (`craft/slice-parent`, `in-progress`, covers AC2) whose body carries `**Folds:** spec/tally-report#F1, spec/tally-report#F2, spec/tally-report#F3`:

- F1, `folded into task/count-lines`, `where` is `tally/counter.py`: close the file handle the counting helper opens. Small, in a file the slice changes.
- F2, `folded into task/count-lines`, `where` names six files (`tally/counter.py`, `tally/cli.py`, `tally/export.py`, `tally/report.py`, `tally/tests/test_counter.py`, `tally/tests/test_cli.py`): change every counting function to return a record instead of a number, which changes the return contract all of them and their callers share.
- F3, `open` although the parent's `**Folds:**` line names it (a fold that failed after the parent was created), `where` is `tally/cli.py`: print an error line when the file cannot be read. Small, in a file the slice changes.

A small `tally/` source tree is copied into the run directory so the files the slice changes, and the callers of the counting functions, can be read rather than guessed. A lore session `aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee` is seeded with one unrelated candidate so the session log exists on the baseline. The prompt runs `/craft:plan task/count-lines --unattended`, passes that session id to the `found_work.py` commands that take it, and puts this directory's `bin` first on `PATH` for scripts that run `lore`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, with the `runner` block grants of `eval-gate.json` (which already carry `Bash(*/scripts/found_work.py *)`; no grant is added).
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Agent, Write]`, `max_turns: 120`, `timeout_seconds: 1200`.
- A run scores the fraction of graders that pass and passes only at 1.0.
- Baseline: the unedited `skills/plan/SKILL.md`, 1 run, on the final case. Expected red on `f1-is-named-by-a-child`, `f2-stays-open` and `f3-is-folded-into-the-parent` (and the decision, ordering and report graders that name the reopen and the re-fold); expected green on the guards `f1-stays-folded` and `f2-is-named-by-no-child`, which hold when nothing happens.
- Treatment: the edited prose, 3 runs, 3 of 3 pass.
- Regression: `plan-keeps-an-earlier-run-section`, `unattended-slice-then-plan-ends-plan-ready` and `prototype-plan-carries-no-migration`, 1 run each on the edited prose, unchanged (a slice with no folded entries plans as before). A failure is compared with its `MANUAL-EVAL.md` history before it counts against the prose.
- A run killed by the 580 s process wrapper or by the known Council-Review-write permission flake is rerun and reported as such; a scaffold failure or timeout is an error, logged apart from graded fails.

## Pass condition

Every clause holds.

| Clause | Grader | Type and target |
|---|---|---|
| A child task's body carries `Resolves spec/tally-report#F1: <title>` | `f1-is-named-by-a-child` | `tool_used` Write, `input_match`, min 1 |
| F1 is still `folded into task/count-lines` | `f1-stays-folded` | `regex`, `source: file`, the spec record |
| No body written carries `Resolves spec/tally-report#F2` | `f2-is-named-by-no-child` | `tool_used` Write, `input_match`, max 0 |
| F2, too large to build here, is `open` again | `f2-stays-open` | `regex`, `source: file`, the spec record |
| The session log carries an `entry-reopened` decision for F2 under `task/count-lines` whose reason is a balloon reason | `f2-reopen-decision-carries-a-balloon-reason` | `regex`, `source: file`, the session log |
| F3, the fold that failed, is `folded into task/count-lines` | `f3-is-folded-into-the-parent` | `regex`, `source: file`, the spec record |
| A child task's body carries `Resolves spec/tally-report#F3: <title>` | `f3-is-named-by-a-child` | `tool_used` Write, `input_match`, min 1 |
| The session log carries an `entry-folded` decision for F3 | `f3-fold-decision-is-logged` | `regex`, `source: file`, the session log |
| Each decision line precedes its result line in the session log (F3 fold, F2 reopen) | `each-decision-precedes-its-result` | `regex`, `source: file`, the session log |
| The closing message states the re-fold of F3 and the return of F2 to open | `closing-message-reports-the-decisions` | `regex` over the final message |
| The run ends on its plan-ready outcome line | `final-line-is-plan-ready` | `regex` over the final message |

## Limitations

- One spec, one phrasing, one model tier, three treatment runs. The fixture has one entry that clearly balloons and two that clearly do not; it does not probe the boundary between them, an entry whose `where` names a directory, or a failed `entries` read.
- Child-task slugs are the model's choice, so a child's `Resolves` line is read from the body the run wrote with the file-writing tool, not from the created record. `f1-is-named-by-a-child` and `f3-is-named-by-a-child` show the line was written, and the spec graders show the entry transitions landed; no grader shows that the plan's closing `entries` re-run printed `named-by` children.
- The `llm` trace judge sees only the first and last events of a run, so nothing here grades a mid-run statement; the session-log order of decision line and result line is the deterministic proxy for "decided before it writes".
- Seeding the session with a fixed id and passing it to the scripts is the eval's own device: a real run does not pass `--session-id`.
- The failure paths (a failed re-fold, a failed reopen and its retry command) are not exercised; the script's tests cover the commands.

## Addendum 2026-11-09: revisions and evidence after the first runs

Authored after seeing runs; the pre-registered sections above are unchanged.

- **Prompt.** The first prompt told the run to pass the session id to "every command that takes it" and so did not reach `entry` and `report`; the decision lines went to another session (three treatment runs, 0.55, 0.64, 0.55). The final prompt names `found_work.py` `entry` and `report` as the slice case's does. Authoring error in the case, not a prose result.
- **Graders.** `f2-is-named-by-no-child` needed `min: 0` beside `max: 0` (the runner defaults `min` to 1, so the grader could not pass); the balloon-reason pattern also accepts `every`, `across` and `beyond`, because a run wrote "changes every counting function and touches ... beyond the slice".
- **The unedited-`plan/SKILL.md` baseline is not a state where the behaviour is absent.** Run on the full plugin with only `plan/SKILL.md` unedited (fingerprint `5fff0b645746f2f15e60e264d5422f77990bac60`), the agent greps the plugin for `Folds:` / `folded into`, reads `slice/SKILL.md` and the `found_work.py` header, and performs the re-fold of F3 and the reopen of F2 itself: `f2-stays-open` and `f3-is-folded-into-the-parent` pass there. What the plan prose adds on top is the `Resolves spec/<spec>#F<n>: <title>` form in a child's Delivers, the closing-message report and the confirmation read. The F1, F2 and F3 graders are shown red on a second baseline: the plugin as it stood before this feature (no `entry` subcommand in `found_work.py`, no fold prose in `slice/SKILL.md` or the build), with the same case.
- Treatment, 3 runs: 3 of 3 pass. Regression: `plan-keeps-an-earlier-run-section` and `unattended-slice-then-plan-ends-plan-ready` pass; `prototype-plan-carries-no-migration` is a legacy hand-shaped case with no `case.yaml`, which the runner does not run.
- `f1-stays-folded` and `f2-is-named-by-no-child` hold when nothing happens, so no run shows them red.
