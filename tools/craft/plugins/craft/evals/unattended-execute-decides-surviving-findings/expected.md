# unattended-execute-decides-surviving-findings

## Under test

Under `--unattended --then-pr`, execute at Phase 3 after its one re-review round, holding a re-review report with two surviving findings: one inside the slice (a one-line fix to code the slice added: fold) and one in another subsystem (separate). The run decides each finding through the found-work table on its own recommendation, asks nothing, and parks nothing. Per finding it makes one `found_work.py decide` call before anything is written for it, and one numbered decision in the `## Unattended run` block whose `Choice:` names the table decision. A separate task is created for the second finding. The prose under test is `skills/_shared/execute.md` (Phase 3's surviving-findings sentence, the Found work section, the Loop stops and run-block text) and `skills/_shared/unattended.md`, as they stand when the baseline runs and after the edit.

## Fixture

`fixtures/scaffold.sh` seeds a `ready` spec `export-invoices` (one criterion, AC1) and an `in-progress` slice parent `export-invoices-as-csv` (labels `craft/slice-parent` and `craft/branch`, related to the spec) with two `done` children, `## End Phases` ticked through Phase 2, and an open `## Unattended run` block (checkpoint `workspace ready`, an empty `**Decisions:**` list, `**Parked questions:** none`, no `**Outcome:**`). `fixtures/re-review.md` is copied into the run directory beside it and holds the two findings. A `session.json` seeds the session note with one unrelated candidate so the decision log's file exists whatever the run does.

Two stand-ins, because the runner's sandbox hides the operator's home: `./lore` (a self-contained wrapper, logging every call's arguments to `lore-calls.log`) and `./scripts/found_work.py` (a thin wrapper around a **real copy** of craft's script in `real-scripts/`, run against `bin/lore`, which reaches the same vault). The prompt names the session id the run passes to `./lore session` and to the script, and where the plugin's scripts directory is for this run, the way the sibling cases redirect `lore`.

How the run reaches the step: the prompt says the run already claimed the slice, has finished Phases 1 and 2, and holds the re-review report as a file; it may not dispatch agents or run git, so where the procedure would send a fix to `executor` it says in one line which finding it would send. **Limit of this choice:** the fold's fix pass is not run, so the case shows the decision and its log, not the fix.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools: [Skill, Read, Glob, Grep, Write]`, `max_turns: 50`, `timeout_seconds: 600`. The prompt asks for single-command Bash calls. The runner's dispatch tool is named `Task`, so "no agent dispatched" is `tool_used: Task` (the case grants no dispatch tool either way).
- Arms: a **baseline** arm on the prose as it stands before any edit, fingerprinted by `git hash-object`: `skills/_shared/execute.md` `b23ab548aed10e21d97008c83daf2f156be02f94`, `skills/_shared/unattended.md` `6a97ad1ca3f7ee3a8b6fdd0d97b9e4a776befa84`, `skills/execute/SKILL.md` `654b9976493d76660857b4fb86546bcb260f5cea`; and a **treatment** arm on the edited prose. 3 runs each. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader (operator-accepted limitation: the judge sees only the first and last 12 messages and is not shown able to fail headless) backed by record state and, where the procedure ends the session, the outcome line. Everything that decides what was written is a regex over the seeded vault files or a script log with `source: file`, never a `tool_used` on Bash, except the two "no git command" and "no agent" checks, which are `tool_used` by design.
- Graders that match a command match only the state-changing form: `no-git-command-issued` is `\bgit\b[^"]*\b(switch|checkout|branch|cherry-pick|fetch|worktree|commit|push)\b`, and a grader that matches a call to `pending_check.py` excludes its read-only `describe` mode.
- Fixture vocabulary leak check, run before any scored run: the seeded parent holds no numbered decision, no `Choice:` line, no `Q<n>` entry and no `**Outcome:**`; the seeded session note holds no `found-work:` line; the seeded children and spec hold none of `fold`, `separate`, `defer`, `drop`. The graded files are the session note, the parent's body and the script log. `re-review.md` sits in the run directory with the findings only, and no fixture file sits beside `expected.md` where a run can read it.
- Expected **red** on the baseline: the unedited prose tells the run to surface surviving findings to the user at Phase 3 and has no rule that logs a found-work decision in the block, so the two `block-decision-*` graders fail in every run (the session-note graders may pass, since the Found work section already logs each decision through the script), and `asked-nothing` may fail.

## Pass condition

Every clause holds.

| Clause | Grader | Target |
|---|---|---|
| a `folded` decision was logged through the script | `decision-for-the-in-slice-finding` | session note |
| a `separate` decision was logged through the script | `decision-for-the-other-subsystem-finding` | session note |
| no third decision | `one-decision-per-finding` | session note, not_contains |
| a decision line precedes the first result line | `decision-logged-before-any-write` | session note |
| a task was created that is not the parent | `separate-task-created-and-not-the-parent` | session note |
| one numbered block decision whose `Choice:` names `fold`/`folded` | `block-decision-for-the-in-slice-finding` | parent body |
| one numbered block decision whose `Choice:` names `separate` | `block-decision-for-the-other-subsystem-finding` | parent body |
| no `Q<n>` entry | `no-question-parked` | parent body, not_contains |
| no question to the operator | `asked-nothing` | `llm`, `focus: trace` |
| no agent dispatched, no state-changing git command | `no-agent-dispatched`, `no-git-command-issued` | `tool_used` |

### Each pass line against each branch, checked before any arm ran

Branches: today's prose (surfaces the findings; the decisions may be logged through the script but nothing is written to the block), the edited prose done right, and three wrong edits (block entries but no script call; both findings folded; a finding parked as `Q1`). The graders were also run offline against a hand-built end state of the first and last: a scaffolded run directory with the two script calls made and the two block entries written passes every record clause; the same directory before the block write fails exactly the two `block-decision-*` graders.

| Branch | script folded | script separate | no third | order | task created | block fold | block separate | no Q | Whole run |
|---|---|---|---|---|---|---|---|---|---|
| Today's | pass | pass | pass | pass | pass | **fail** | **fail** | pass | fails |
| Edited, done right | pass | pass | pass | pass | pass | pass | pass | pass | passes |
| Block entries, no script call | **fail** | **fail** | pass | **fail** | **fail** | pass | pass | pass | fails |
| Both folded | pass | **fail** | pass | pass | **fail** | pass | **fail** | pass | fails |
| Parks one as `Q1` | pass or fail | **fail** | pass | pass | **fail** | pass | **fail** | **fail** | fails |

## Limitations

- One fixture, one phrasing, one model tier, three runs per arm.
- The order "decision before write" is observed through the session note's line order, not a write timestamp.
- A grader reads that a block entry's `Choice:` names the decision; it cannot tell the reasoning is sound.
- The fold's fix pass is not run (no code, no agent), so the case does not show that a fold goes through the Phase 1 gate or gets no further review.

## Reclassified as a guard (2026-11-09)

The baseline on the unedited prose came back green (see `tools/craft/MANUAL-EVAL.md`), so this case is a guard, not a detector: the shared unattended contract already carries the behaviour it pins, and its pass lines stay as pre-registered. It pins that the end-phase edits do not break it. No grader was changed.
