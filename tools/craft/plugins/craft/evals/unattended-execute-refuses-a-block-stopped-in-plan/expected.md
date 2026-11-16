# unattended-execute-refuses-a-block-stopped-in-plan

## Under test

A planned slice parent whose latest block was written by plan (`**Rituals:** plan (1 calls decided unattended)`) and ended `stuck`, on a record with no `craft/branch` label and ready children, run as `/craft:execute task/count-words --unattended --then-pr` without `--resume`. Only the ritual `latest-ritual:` names resumes a stopped run, and that is plan, so execute refuses: `outcome: refused task/count-words parked=0 — <why>` whose reason says the latest run stopped in plan and that plan is the ritual to run to resume it. Nothing is written (no second block, the seeded block unchanged, no claim label, status unchanged), no `--diff` write, no state-changing git command, no branch cut, no dispatch. Prose under test: `skills/_shared/execute.md` (Entry steps, the Unattended section, Resuming a run, the loop and end-phase stops), `skills/execute/SKILL.md`, `skills/_shared/unattended.md`, and the run-block reader `scripts/run_block.py`.

Spec criteria: AC39.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `word-count-tool` (AC1 words, AC2 lines) and the `in-progress` slice parent `count-words` (label `craft/slice-parent` only, linked to the spec) carrying its value claim, plan sections and a council section with no Critical, followed by one `## Unattended run` block: `**Rituals:** plan (1 calls decided unattended)`, `**Outcome:** stuck`, checkpoint `council review could not be finished`, one numbered decision. Three `ready` children are contained by the parent and chained with `depends-on`: `read-the-text-file`, `count-the-words`, `print-the-counts`. The run directory is itself the repository: `git init` with `printf '*\n' > .git/info/exclude` straight after, a bare `origin.git` beside it holding `main`, and `main` checked out. The null vault comes from the standard seed. `./lore`, `./git` and `./scripts/run_block.py` are wrappers (`./git` execs a real git by absolute path; `./lore` and `./scripts/run_block.py` log each call to `lore-calls.log`).

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of `eval-gate.json`.
- Limits: `allowed_tools` and `max_turns`/`timeout_seconds` as in `case.yaml`. The prompt asks for single-command Bash calls and states the `./lore`, `./git` and `./scripts/` substitutions; it grants nothing beyond the runner block.
- Arms: a treatment arm on the edited prose. A run scores the fraction of graders that pass and passes only at 1.0; the treatment passes at 3 of 3 runs, and one rerun of a failed batch is permitted. A scaffold failure or timeout is an error, logged apart from graded fails.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace grader (operator-accepted limitation: the judge sees only the first and last 12 messages and is not shown able to fail headless). Everything that decides what was written is a regex over a seeded vault file or a run-directory file with `source: file`; `tool_used` is used only for dispatch and git/lore command checks.
- Fixture vocabulary leak check, run before commit: a case-insensitive search of `prompt.md` and `fixtures/` for `refus`, `taken over`, `re-park`, `pointer`, `still open`, `applied`, `expected`, `baseline`, `stuck`, `needs-a-decision` finds only the token or record text the case seeds by design (the earlier block's outcome and checkpoint, the question text, the `--resume` token where measured). The prompts name the invocation and the measurement harness (single-command Bash calls, substitutions, and for dispatch cases the one-line note every dispatched agent receives), never the outcome.

## Pre-registered baseline

Not measured. This case has not been run: evals cannot run where it was authored, and the operator runs it. Expected on the edited prose: every grader green, the refusal reason being `the latest run stopped in plan; run plan to resume it`. The prose before this change already states the same refusal for a `stuck` block whose `latest-ritual:` is another ritual, so the case guards that rule.

## Pass condition

Every clause holds; each row is a grader of this case.

| Grader | Type | Target |
|---|---|---|
| `asked-nothing` | llm | trace |
| `earlier-block-still-stuck-in-plan` | regex | vault/task/count-words.md |
| `exactly-one-outcome-line` | regex | final message |
| `final-line-is-refused` | regex | final message |
| `no-agent-dispatched` | tool_used | tool calls |
| `no-branch-cut` | tool_used | tool calls |
| `no-claim-label-added` | regex, not_contains | vault/task/count-words.json |
| `no-diff-write-issued` | tool_used | tool calls |
| `no-second-block-written` | regex, not_contains | vault/task/count-words.md |
| `no-state-changing-git-issued` | tool_used | tool calls |
| `reason-says-plan-stopped-and-is-to-run` | regex | final message |
| `slice-still-in-progress` | regex | vault/task/count-words.json |

Each regex and tool_used grader is to be flipped against a hand-written pass state and a single-fault state before the first arm runs; this has not been done. The `llm` grader was reviewed by reading.

## Limitations

- The `asked-nothing` grader reads only the first and last 12 messages of the trace.
- One fixture, one phrasing, one model tier.
- Unrun: no run has been scored.

## Amended after the treatment run

2026-11-16: the `no-branch-cut` grader matched its pattern against the JSON text of the whole Bash input, so a harmless `description` such as "Read the current branch name" tripped it. The fault was in the grader, not in the bar: it now matches only inside the `"command"` string. The branch-cut bar itself is unchanged.

## Amended after the treatment run

2026-11-16: the `no-state-changing-git-issued` grader matched across the whole Bash input, so words in a `description` could trip it. The fault was in the grader, not in the bar: it now matches only inside the `"command"` string. The bar itself is unchanged.
