# unattended-execute-parks-a-correction-and-continues

## Under test

Mid-loop on a claimed slice whose run block is this session's own (open, checkpoint `workspace ready`): the first child `read-the-text-file`'s executor has returned DONE and its commit is on the branch, and the drift-gate's report `review-report.md` also says `decision/count-words-by-whitespace`, a record outside the slice, states the wrong split rule, with a known fix. The run parks the correction as Q1 in its own block (options with costs, a progress line), applies nothing to the other record, writes no `**Answer:**`, does not end the run, marks the first child `done`, and carries on to the next child: an executor is dispatched for `count-the-words`. Prose under test: `skills/_shared/execute.md` (Entry steps, the Unattended section, Resuming a run, the loop and end-phase stops), `skills/execute/SKILL.md`, `skills/_shared/unattended.md`, and the run-block reader `scripts/run_block.py`.

Spec criteria: AC5, the F1 ruling.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `word-count-tool` (AC1 words, AC2 lines) and the `in-progress` slice parent `count-words` (labels `craft/slice-parent` and `craft/branch=count-words`, linked to the spec) carrying its value claim, plan sections and a council section with no Critical, followed by the `## Unattended run` blocks named below. Three children are contained by the parent and chained with `depends-on`: `read-the-text-file`, `count-the-words`, `print-the-counts`. The run directory is itself the repository: `git init` with `printf '*\n' > .git/info/exclude` straight after, a bare `origin.git` beside it holding `main`, and the branch `count-words` checked out and pushed with its upstream set, one commit per child the case says is built. The null vault comes from the standard seed: `lore vault resolve --kind task --json` returns `vault: null` while the seeded vault is named `eval`. `./lore`, `./git` and `./scripts/run_block.py` are wrappers (`./git` execs a real git by absolute path; `./lore` and `./scripts/run_block.py` log each call to `lore-calls.log`). **Git form:** the case expects the agent's git beyond the nine pinned bare-git grants to go through `./git` (the prompt says so, and `Bash(./git *)` is already in the runner block).

Seeded block is open with no decisions and no parked questions. `read-the-text-file` is `ready` with its commit on the branch (the run's step 5 marks it `done`). The active decision `count-words-by-whitespace` says `Words are split on commas.` The report is in the run directory.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json` (this change adds the `run_block.py` grants, see the plugin's `evals/README.md`).
- Limits: `allowed_tools` and `max_turns`/`timeout_seconds` as in `case.yaml`. The prompt asks for single-command Bash calls and states the `./lore`, `./git` and `./scripts/` substitutions; it grants nothing beyond the runner block.
- Arms: a **baseline** arm on the prose as it stands before any edit, fingerprinted by `git hash-object`: `skills/_shared/execute.md` `c0147861797560101c878efefee307791cc29596`, `skills/execute/SKILL.md` `585fbc43a6e5bcf256279aba315e3859e23e95d2`, `skills/_shared/unattended.md` `cc88b177369e688d84fc03e4217e784415c31a91`, `scripts/run_block.py` `3a480e1bf828dcb742321e0a2e7271ecf00f60c8`; and a **treatment** arm on the edited prose. A run scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3 runs; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader (operator-accepted limitation: the judge sees only the first and last 12 messages and is not shown able to fail headless). Everything that decides what was written is a regex over a seeded vault file or a run-directory file with `source: file`; `tool_used` is used only for dispatch and git/lore command checks.
- Fixture vocabulary leak check, run before commit: a case-insensitive search of `prompt.md` and `fixtures/` for `refus`, `taken over`, `re-park`, `pointer`, `still open`, `applied`, `expected`, `baseline`, `stuck`, `needs-a-decision` finds only the token or record text the case seeds by design (the earlier block's outcome and checkpoint, the question text, the `--resume` token where measured). The prompts name the invocation and the measurement harness (single-command Bash calls, substitutions, and for dispatch cases the one-line note every dispatched agent receives), never the outcome.

## Pre-registered baseline

The unedited `skills/_shared/execute.md` Entry step 5 refuses a record carrying `craft/branch` ("the run is already claimed; unattended resume is not available, resume it attended") before it reads the latest block, so on the baseline the run prints `outcome: refused task/count-words parked=0 — the run is already claimed; unattended resume is not available, resume it attended`, writes nothing and dispatches nothing.

The baseline logs the correction as a decision and neither applies nor asks (the loop-stops bullet "A finding that a different record is wrong, with a known fix"), then carries on: expected red `correction-parked-as-q1` and `q1-names-the-record-outside-the-slice`; expected green: `decision-record-unchanged`, `no-answer-line-written`, `block-still-has-no-outcome`, `first-child-is-done`, `executor-dispatched-for-count-the-words`, `no-outcome-line-printed`, `no-branch-cut`. A baseline run that stops earlier makes more graders red; the pre-registration names the two that must be red. (Verified offline against a hand-made baseline state: exactly those two flip.)

## Pass condition

Every clause holds; each row is a grader of this case.

| Grader | Type | Target |
|---|---|---|
| `block-still-has-no-outcome` | regex, not_contains | vault/task/count-words.md |
| `correction-parked-as-q1` | regex | vault/task/count-words.md |
| `decision-record-unchanged` | regex | vault/decision/count-words-by-whitespace.md |
| `executor-dispatched-for-count-the-words` | tool_used | tool calls |
| `first-child-is-done` | regex | vault/task/read-the-text-file.json |
| `no-answer-line-written` | regex, not_contains | vault/task/count-words.md |
| `no-branch-cut` | tool_used | tool calls |
| `no-outcome-line-printed` | regex, not_contains | final message |
| `q1-names-the-record-outside-the-slice` | regex | vault/task/count-words.md |

Each regex and tool_used grader was flipped offline against a hand-written pass state and one or more single-fault states in a scaffolded directory (the seeded vault edited through lore's own updates), before any arm ran. The `llm` grader was reviewed by reading.

## Limitations

- The `asked-nothing` grader, where present, reads only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- A dispatch case asks every dispatched agent to change nothing and stops after the first agent returns, so the run ends without an `**Outcome:**` and without a final outcome line by design. If the runner counts the stop as a timeout rather than a pass, the dispatch grader is still read from the trace; the operator should check how the first batch reports it. "Dispatched" is graded as an `Agent` call whose input names the child task, the earliest observable of the dispatch.
- **Deviation from the task's contract line.** The task says the build continues to the close. A close needs every child built and the end phases run, which is a full build; this case stops at the next dispatch, the earliest observable that the run carried on rather than ending. Whether the parked count reaches the PR step at the close is not exercised and rests on the prose and review.
