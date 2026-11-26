# unattended-execute-starts-fresh-after-a-finished-plan

## Under test

An unattended execute run on a planned slice parent whose latest block is plan's finished block
(`**Rituals:** plan (1 calls decided unattended)`, `**Outcome:** plan-ready`) and which carries no
`craft/branch` label. A finished outcome written by an earlier ritual than execute means a fresh
run: execute is not refused, leaves plan's block as it is, appends its own block
(`**Rituals:** execute`, no `**Outcome:**` line yet) and goes on, so an executor is dispatched for
the first child `read-the-text-file`. Prose under test: `skills/_shared/execute.md` (Entry step 5),
`skills/execute/SKILL.md`, `skills/_shared/unattended.md` (Whether a run resumes), and the run-block
reader `scripts/run_block.py`.

Spec criteria: AC47, AC39.

## Fixture

`fixtures/scaffold.sh` seeds the `ready` spec `word-count-tool` and the `in-progress` slice parent
`count-words` (label `craft/slice-parent` only, linked to the spec) carrying its value claim, plan
sections, a council section with no Critical and plan's finished block. Three children are contained
by the parent and chained with `depends-on`, all `ready`: `read-the-text-file`, `count-the-words`,
`print-the-counts`. The run directory is itself the repository on `main`, with a bare `origin.git`
beside it. `./lore`, `./git` and `./scripts/run_block.py` are wrappers as in the sibling case
`unattended-execute-takes-over-an-interrupted-block-and-reaches-dispatch`.

## Conditions, sample and bar

- Runner: `claude plugin eval`, target `tools/craft/plugins/craft`, `--ablation none`, model
  `claude-opus-5-5`, `--scaffold`, 3 runs, `-j 1`, with the `runner` block grants of
  `eval-gate.json` (this change adds the `run_block.py` grants, see the plugin's `evals/README.md`).
- Limits: `allowed_tools` and `max_turns`/`timeout_seconds` as in `case.yaml`. The prompt asks for
  single-command Bash calls and states the `./lore`, `./git` and `./scripts/` substitutions; it
  grants nothing beyond the runner block.
- Arms: a **baseline** arm on the prose as it stands before any edit, fingerprinted by
  `git hash-object`: `skills/_shared/execute.md` `c0147861797560101c878efefee307791cc29596`,
  `skills/execute/SKILL.md` `585fbc43a6e5bcf256279aba315e3859e23e95d2`,
  `skills/_shared/unattended.md` `cc88b177369e688d84fc03e4217e784415c31a91`, `scripts/run_block.py`
  `3a480e1bf828dcb742321e0a2e7271ecf00f60c8`; and a **treatment** arm on the edited prose. A run
  scores the fraction of graders that pass and passes only at 1.0. The treatment passes at 3 of 3
  runs; one rerun of a failed batch is permitted, stated here before any run, and a rerun-green is
  reported as such. A scaffold failure or timeout is an error, logged apart from graded fails.
- A headless eval session never offers `AskUserQuestion`, so "asked nothing" is an `llm` trace
  grader (operator-accepted limitation: the judge sees only the first and last 12 messages and is
  not shown able to fail headless). Everything that decides what was written is a regex over a
  seeded vault file or a run-directory file with `source: file`; `tool_used` is used only for
  dispatch and git/lore command checks.
- Fixture vocabulary leak check, run before commit: a case-insensitive search of `prompt.md` and
  `fixtures/` for `refus`, `taken over`, `re-park`, `pointer`, `still open`, `applied`, `expected`,
  `baseline`, `stuck`, `needs-a-decision` finds only the token or record text the case seeds by
  design (the earlier block's outcome and checkpoint, the question text, the `--resume` token where
  measured). The prompts name the invocation and the measurement harness (single-command Bash calls,
  substitutions, and for dispatch cases the one-line note every dispatched agent receives), never
  the outcome.

## Pre-registered baseline

No arm has run yet: the evals cannot run in the sandbox that built this case, so the baseline result
is not recorded. The baseline arm is the prose before the edit, fingerprinted with `git hash-object`
when the operator runs it. Expected on the treatment arm: every grader green, at 3 of 3 runs.

## Pass condition

Every clause holds; each row is a grader of this case.

| Grader | Type | Target |
|---|---|---|
| `earlier-block-preserved` | regex | vault/task/count-words.md |
| `exactly-two-blocks` | regex | vault/task/count-words.md |
| `second-block-is-execute` | regex | vault/task/count-words.md |
| `new-block-carries-no-outcome-line` | regex | vault/task/count-words.md |
| `executor-dispatched-for-read-the-text-file` | tool_used | tool calls |
| `no-double-start-stop` | regex, not_contains | final message |
| `not-refused` | regex, not_contains | final message |


## Limitations

- One fixture, one phrasing, one model tier.
- A dispatch case asks every dispatched agent to change nothing and stops after the first agent
  returns, so the run ends without an `**Outcome:**` and without a final outcome line by design. If
  the runner counts the stop as a timeout rather than a pass, the dispatch grader is still read from
  the trace; the operator should check how the first batch reports it. "Dispatched" is graded as an
  `Agent` call whose input names the child task, the earliest observable of the dispatch.
- `new-block-carries-no-outcome-line` needs a second block to exist; its pattern requires two
  headings, so it cannot pass on the seeded record alone.
